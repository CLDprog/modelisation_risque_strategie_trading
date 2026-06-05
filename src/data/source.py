"""
Source de données du front Dash — LECTEUR DE STORE PUR.

Conforme à la roadmap (process isolation) : le front ne parle JAMAIS à IBKR.
Toutes les données viennent du store alimenté par le collecteur (run_collector.py) :
  - statut + spots          → data/collector_status.json
  - analytics (iv, forward, surface, risk, scénarios, qc) → data/analytics/*.parquet

Si le collecteur ne tourne pas, les pages affichent "données indisponibles".
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger

_DATA_DIR    = Path(__file__).parent.parent.parent / "data"
_STATUS_FILE = _DATA_DIR / "collector_status.json"

# Le collecteur est considéré "frais" si son dernier cycle date de moins de N secondes
_FRESH_WINDOW = 600   # 10 min


class DataSource:
    """Singleton — lecteur de store partagé par toutes les pages."""

    _instance: Optional["DataSource"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized     = True
        self._selected_symbol = "SPY"
        self._universe_cfg    = None
        self._status_cache    = None
        self._status_cache_ts = 0.0

    # ------------------------------------------------------------------
    # Config / symboles
    # ------------------------------------------------------------------

    def _get_universe_cfg(self) -> dict:
        if self._universe_cfg is None:
            from src.utils.config import load_config
            self._universe_cfg = load_config("universe")
        return self._universe_cfg

    @property
    def available_symbols(self) -> list[str]:
        return [u["symbol"] for u in self._get_universe_cfg().get("underlyings", [])]

    @property
    def selected_symbol(self) -> str:
        return self._selected_symbol

    @selected_symbol.setter
    def selected_symbol(self, symbol: str) -> None:
        self._selected_symbol = (symbol or "SPY").upper()

    # ------------------------------------------------------------------
    # Statut du collecteur (lu depuis collector_status.json)
    # ------------------------------------------------------------------

    def collector_status(self) -> dict:
        """Lit le statut du collecteur (cache 2s pour limiter les I/O disque)."""
        now = time.time()
        if self._status_cache is not None and (now - self._status_cache_ts) < 2:
            return self._status_cache
        status = {}
        try:
            if _STATUS_FILE.exists():
                with open(_STATUS_FILE, encoding="utf-8") as f:
                    status = json.load(f)
        except Exception as exc:
            logger.debug(f"lecture collector_status: {exc}")
        self._status_cache    = status
        self._status_cache_ts = now
        return status

    def _collector_fresh(self, status: Optional[dict] = None) -> bool:
        status = status or self.collector_status()
        last = status.get("last_cycle")
        if not last:
            return False
        try:
            dt = datetime.fromisoformat(last)
            age = (datetime.now(timezone.utc) - dt).total_seconds()
            return age < _FRESH_WINDOW
        except Exception:
            return False

    @property
    def is_collector_connected(self) -> bool:
        st = self.collector_status()
        return bool(st.get("connected")) and self._collector_fresh(st)

    # Alias de compatibilité pour le code existant du front
    @property
    def is_connected(self) -> bool:
        return self.is_collector_connected

    def get_data_source_label(self) -> str:
        st = self.collector_status()
        if self.is_collector_connected:
            return "Live (collecteur)"
        if self._analytics_available("iv_points", self._selected_symbol):
            return "Analytics (store)"
        if st:
            return "Collecteur arrêté"
        return "Collecteur non démarré"

    # ------------------------------------------------------------------
    # Lecture du store analytics (Parquet)
    # ------------------------------------------------------------------

    def _analytics_store(self):
        from src.storage.schemas import ParquetStore
        return ParquetStore(_DATA_DIR / "analytics")

    def _read_analytics(self, table: str, dt: Optional[date] = None) -> pd.DataFrame:
        try:
            return self._analytics_store().read(table, dt or date.today())
        except Exception as exc:
            logger.debug(f"lecture store ({table}): {exc}")
            return pd.DataFrame()

    def _analytics_available(self, table: str, symbol: Optional[str] = None,
                               dt: Optional[date] = None) -> bool:
        df = self._read_analytics(table, dt)
        if df.empty:
            return False
        if symbol:
            for col in ("underlying_symbol", "underlying"):
                if col in df.columns:
                    return not df[df[col] == symbol].empty
        return True

    # ------------------------------------------------------------------
    # Accesseurs publics — store uniquement
    # ------------------------------------------------------------------

    def get_spot(self, symbol: Optional[str] = None) -> Optional[float]:
        sym = (symbol or self._selected_symbol).upper()

        # 1. Statut du collecteur (spot par symbole)
        sd = self.collector_status().get("symbols", {}).get(sym)
        if sd and sd.get("spot"):
            try:
                return float(sd["spot"])
            except (TypeError, ValueError):
                pass

        # 2. Fallback : reference_spot dans iv_points
        iv_df = self._read_analytics("iv_points")
        if not iv_df.empty and "reference_spot" in iv_df.columns and "underlying_symbol" in iv_df.columns:
            sub = iv_df[iv_df["underlying_symbol"] == sym]
            if not sub.empty:
                return float(sub["reference_spot"].iloc[-1])

        return None

    def get_option_chain(self, symbol: Optional[str] = None) -> pd.DataFrame:
        sym = (symbol or self._selected_symbol).upper()
        df = self._read_analytics("iv_points")
        if df.empty or "underlying_symbol" not in df.columns:
            return pd.DataFrame()
        return df[df["underlying_symbol"] == sym].copy()

    def get_forward_curve(self, symbol: Optional[str] = None) -> pd.DataFrame:
        sym = (symbol or self._selected_symbol).upper()
        df = self._read_analytics("forward_curve")
        if df.empty:
            return pd.DataFrame()
        col = "underlying" if "underlying" in df.columns else "underlying_symbol"
        if col in df.columns:
            df = df[df[col] == sym].copy()
        if df.empty:
            return pd.DataFrame()
        if "underlying" in df.columns and "underlying_symbol" not in df.columns:
            df = df.rename(columns={"underlying": "underlying_symbol"})
        if "days_to_expiry" not in df.columns and "maturity_years" in df.columns:
            df["days_to_expiry"] = (df["maturity_years"] * 365).round().astype(int)
        return df

    def get_surface_grid(self, symbol: Optional[str] = None) -> pd.DataFrame:
        sym = (symbol or self._selected_symbol).upper()
        df = self._read_analytics("surface_grid")
        if df.empty:
            return pd.DataFrame()
        if "underlying" in df.columns:
            df = df[df["underlying"] == sym].copy()
        elif "underlying_symbol" in df.columns:
            df = df[df["underlying_symbol"] == sym].copy()
        return df

    def get_portfolio(self, symbol: Optional[str] = None) -> pd.DataFrame:
        sym = (symbol or self._selected_symbol).upper()
        df = self._read_analytics("risk_aggregates")
        if df.empty:
            return pd.DataFrame()
        if "underlying_symbol" in df.columns:
            df = df[df["underlying_symbol"] == sym].copy()
        return df

    def get_scenarios(self, symbol: Optional[str] = None) -> pd.DataFrame:
        sym = (symbol or self._selected_symbol).upper()
        df = self._read_analytics("scenario_results")
        if df.empty:
            return pd.DataFrame()
        if "underlying_symbol" in df.columns:
            df = df[df["underlying_symbol"] == sym].copy()
        return df

    def get_qc(self, symbol: Optional[str] = None) -> pd.DataFrame:
        sym = (symbol or self._selected_symbol).upper()
        df = self._read_analytics("qc_results")
        if df.empty:
            return pd.DataFrame()
        cols = ["check_name", "target_key", "status", "severity",
                "measured_value", "threshold", "reason_code"]
        mask = df["target_key"].str.contains(sym, case=False, na=False)
        df_sym = df[mask | (df["target_key"] == "portfolio")]
        if df_sym.empty:
            return pd.DataFrame()
        return df_sym[[c for c in cols if c in df_sym.columns]].copy()

    def get_qc_triage(self, symbol: Optional[str] = None) -> pd.DataFrame:
        """Table de triage : checks en warn/fail (étape 14)."""
        sym = (symbol or self._selected_symbol).upper()
        df = self._read_analytics("qc_triage")
        if df.empty:
            return pd.DataFrame()
        if "target_key" in df.columns:
            mask = df["target_key"].str.contains(sym, case=False, na=False)
            df = df[mask | (df["target_key"] == "portfolio")]
        return df.copy()

    def get_qc_anomalies(self, symbol: Optional[str] = None) -> pd.DataFrame:
        """Anomalies QC détectées vs baseline glissante (étape 14)."""
        sym = (symbol or self._selected_symbol).upper()
        df = self._read_analytics("qc_anomalies")
        if df.empty:
            return pd.DataFrame()
        if "target_key" in df.columns:
            df = df[df["target_key"].str.contains(sym, case=False, na=False)]
        return df.copy()


# Instance globale unique
datasource = DataSource()
