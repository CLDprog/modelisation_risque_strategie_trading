"""
Schema definitions and read/write adapters (Step 4).

Storage strategy:
  - SQLite (via SQLAlchemy) for metadata: instrument master, jobs, QC results
  - Parquet files (via pandas/pyarrow) for bulk timeseries: raw events, analytics

All derived tables carry: snapshot_ts, code_version, config_hash.
"""
from __future__ import annotations

import json
from pathlib import Path
from datetime import date, datetime
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from loguru import logger
from sqlalchemy import create_engine, text


# ---------------------------------------------------------------------------
# Parquet store (raw events + analytics)
# ---------------------------------------------------------------------------

class ParquetStore:
    """Append-only partitioned Parquet store, partitioned by date."""

    def __init__(self, base_dir: str | Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _partition_path(self, table: str, dt: date) -> Path:
        p = self.base_dir / table / f"dt={dt.isoformat()}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def write(self, table: str, df: pd.DataFrame, dt: date,
              filename: str = "data.parquet", atomic: bool = False) -> Path:
        """
        Write (or overwrite) a partition.

        atomic=True : écrit dans un fichier temporaire puis le renomme (os.replace,
        atomique sur le même FS). Indispensable quand un lecteur (le front Dash) peut
        lire le fichier pendant que le collecteur l'écrit.
        """
        if df.empty:
            logger.warning(f"Attempted to write empty DataFrame to {table}/{dt}")
            return None
        path = self._partition_path(table, dt) / filename
        if atomic:
            import os
            tmp = path.with_suffix(path.suffix + ".tmp")
            df.to_parquet(tmp, index=False, engine="pyarrow")
            os.replace(tmp, path)
        else:
            df.to_parquet(path, index=False, engine="pyarrow")
        logger.debug(f"Wrote {len(df)} rows → {path}")
        return path

    def append(self, table: str, df: pd.DataFrame, dt: date) -> None:
        """Append rows to an existing partition (used by live collector)."""
        path = self._partition_path(table, dt) / "data.parquet"
        if path.exists():
            existing = pd.read_parquet(path)
            df = pd.concat([existing, df], ignore_index=True)
        self.write(table, df, dt)

    def read(self, table: str, dt: date) -> pd.DataFrame:
        path = self._partition_path(table, dt) / "data.parquet"
        if not path.exists():
            logger.warning(f"Partition not found: {table}/{dt}")
            return pd.DataFrame()
        return pd.read_parquet(path)

    def read_range(self, table: str, start: date, end: date) -> pd.DataFrame:
        frames = []
        d = start
        while d <= end:
            df = self.read(table, d)
            if not df.empty:
                frames.append(df)
            d = date.fromordinal(d.toordinal() + 1)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def partition_exists(self, table: str, dt: date) -> bool:
        return (self._partition_path(table, dt) / "data.parquet").exists()


# ---------------------------------------------------------------------------
# SQLite metadata store
# ---------------------------------------------------------------------------

class MetadataStore:
    """SQLite-backed store for instrument master, job manifests, QC records."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        self._init_tables()

    def _init_tables(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS instrument_master (
                    instrument_key TEXT PRIMARY KEY,
                    underlying_symbol TEXT NOT NULL,
                    sec_type TEXT NOT NULL,
                    exchange TEXT,
                    currency TEXT,
                    expiry TEXT,
                    strike REAL,
                    right TEXT,
                    multiplier INTEGER,
                    contract_id_broker INTEGER,
                    as_of_date TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS job_manifests (
                    run_id TEXT PRIMARY KEY,
                    job_name TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    code_version TEXT,
                    status TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    notes TEXT
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS qc_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    check_name TEXT NOT NULL,
                    target_key TEXT,
                    status TEXT NOT NULL,
                    severity TEXT,
                    measured_value REAL,
                    threshold REAL,
                    reason_code TEXT,
                    context_json TEXT,
                    created_at TEXT NOT NULL
                )
            """))
            conn.commit()

    def save_instrument_master(self, master, as_of_date: date) -> None:
        from src.universe.contracts import OptionContract, Underlying
        rows = []
        today_str = as_of_date.isoformat()
        now_str = datetime.utcnow().isoformat()

        for u in master.all_underlyings():
            rows.append({
                "instrument_key": u.instrument_key,
                "underlying_symbol": u.symbol,
                "sec_type": u.sec_type,
                "exchange": u.exchange,
                "currency": u.currency,
                "expiry": None, "strike": None, "right": None, "multiplier": None,
                "contract_id_broker": u.contract_id_broker,
                "as_of_date": today_str, "created_at": now_str,
            })
        for o in master.all_options():
            rows.append({
                "instrument_key": o.instrument_key,
                "underlying_symbol": o.underlying_symbol,
                "sec_type": "OPT",
                "exchange": o.exchange,
                "currency": o.currency,
                "expiry": o.expiry.isoformat(),
                "strike": o.strike,
                "right": o.right,
                "multiplier": o.multiplier,
                "contract_id_broker": o.contract_id_broker,
                "as_of_date": today_str, "created_at": now_str,
            })

        df = pd.DataFrame(rows)
        df.to_sql("instrument_master", self.engine, if_exists="replace", index=False)
        logger.info(f"Saved {len(rows)} instruments to metadata store.")

    def save_qc_result(self, run_id: str, check_name: str, target_key: str,
                       status: str, severity: str, measured_value: float,
                       threshold: float, reason_code: str, context: dict) -> None:
        row = {
            "run_id": run_id, "check_name": check_name, "target_key": target_key,
            "status": status, "severity": severity,
            "measured_value": measured_value, "threshold": threshold,
            "reason_code": reason_code,
            "context_json": json.dumps(context),
            "created_at": datetime.utcnow().isoformat(),
        }
        pd.DataFrame([row]).to_sql("qc_results", self.engine, if_exists="append", index=False)

    def save_manifest(self, manifest: dict) -> None:
        pd.DataFrame([manifest]).to_sql("job_manifests", self.engine, if_exists="append", index=False)
