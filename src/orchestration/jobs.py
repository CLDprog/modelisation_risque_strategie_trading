"""
Job entry points and orchestration (Step 15).

Jobs:
  build_snapshots_job  — raw events → market-state snapshots
  run_eod_pipeline     — snapshots → forwards → IV → surface → risk → scenarios → QC
"""
from __future__ import annotations

import uuid
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, List

import pandas as pd
from loguru import logger


CODE_VERSION = "vol-infra-2.0.0"


def new_run_id(job_name: str, session_date: date) -> str:
    return f"{session_date.isoformat()}_{job_name}_{str(uuid.uuid4())[:6]}"


# ---------------------------------------------------------------------------
# Job 1 — Build market-state snapshots from raw events
# ---------------------------------------------------------------------------

def build_snapshots_job(session_date: date,
                        config_dir: str = "./configs",
                        data_dir: str = "./data") -> dict:
    """
    Converts raw market events into market-state snapshots.
    Must run before run_eod_pipeline().
    """
    from src.utils.config import load_config
    from src.storage.schemas import ParquetStore, MetadataStore
    from src.universe.contracts import InstrumentMaster, Underlying, OptionContract
    from src.snapshots.builder import build_snapshot, snapshot_to_dataframe
    from src.utils.dates import utc_now
    import datetime as dt_mod

    run_id    = new_run_id("snapshots", session_date)
    started   = datetime.now(timezone.utc).isoformat()
    status    = "started"

    qc_cfg       = load_config("qc", config_dir)
    universe_cfg = load_config("universe", config_dir)

    raw_store      = ParquetStore(Path(data_dir) / "raw")
    analytics_store = ParquetStore(Path(data_dir) / "analytics")
    meta_store     = MetadataStore(Path(data_dir) / "metadata.db")

    logger.info(f"[{run_id}] Snapshot builder starting for {session_date}")

    try:
        raw_df = raw_store.read("raw_market_events", session_date)
        if raw_df.empty:
            raise RuntimeError("No raw events found for session date")

        # Reconstruit l'univers : d'abord depuis les clés brutes (autonome, replay),
        # sinon depuis le metadata store / la config.
        master = _master_from_raw_events(raw_df, session_date)
        if not master.all_options():
            master = _load_master_from_store(meta_store, session_date, universe_cfg)

        snapshot_rows = []
        # Build one snapshot per underlying at each 5-minute mark
        snap_timestamps = _generate_snapshot_times(raw_df, interval_minutes=5)

        for underlying in master.all_underlyings():
            options = master.get_option_chain(underlying.symbol)
            und_events = raw_df[raw_df["instrument_key"] == underlying.instrument_key]

            for snap_ts in snap_timestamps:
                snap = build_snapshot(raw_df, underlying, options, snap_ts, qc_cfg)
                df_snap = snapshot_to_dataframe(snap)
                if not df_snap.empty:
                    snapshot_rows.append(df_snap)

        if snapshot_rows:
            all_snaps = pd.concat(snapshot_rows, ignore_index=True)
            analytics_store.write("market_state_snapshots", all_snaps, session_date)
            logger.info(f"[{run_id}] Wrote {len(all_snaps)} snapshot rows")
        else:
            logger.warning(f"[{run_id}] No snapshots generated")

        status = "success"

    except Exception as exc:
        status = "failed"
        logger.error(f"[{run_id}] Snapshot job FAILED: {exc}")
        raise

    finally:
        finished = datetime.now(timezone.utc).isoformat()
        manifest = {
            "run_id": run_id, "job_name": "snapshots",
            "session_date": session_date.isoformat(),
            "code_version": CODE_VERSION, "status": status,
            "started_at": started, "finished_at": finished, "notes": "",
        }
        meta_store.save_manifest(manifest)

    return manifest


def _generate_snapshot_times(raw_df: pd.DataFrame,
                               interval_minutes: int = 5) -> list:
    """
    Génère les horodatages de snapshot. Chaque marque doit tomber À OU APRÈS les
    événements qu'elle agrège (sinon le snapshot ne voit aucune quote). On ajoute
    donc systématiquement le dernier timestamp observé comme marque finale.
    """
    import datetime as dt_mod
    if raw_df.empty or "receipt_ts" not in raw_df.columns:
        return []
    ts_col = pd.to_datetime(raw_df["receipt_ts"], utc=True)
    start  = ts_col.min().replace(second=0, microsecond=0) + dt_mod.timedelta(minutes=interval_minutes)
    end    = ts_col.max()
    times  = []
    current = start
    while current <= end:
        times.append(current.to_pydatetime())
        current = current + dt_mod.timedelta(minutes=interval_minutes)
    # Marque finale = dernier événement observé (capture toute la session)
    last = end.to_pydatetime()
    if not times or times[-1] < last:
        times.append(last)
    return times


def _master_from_raw_events(raw_df, session_date: date) -> "InstrumentMaster":
    """
    Reconstruit l'univers (underlyings + options) directement depuis les clés
    d'instrument présentes dans raw_market_events. Rend le replay AUTONOME depuis
    la seule couche brute (roadmap : analytics recalculables depuis le raw).
    """
    from src.universe.contracts import InstrumentMaster, Underlying, OptionContract

    master = InstrumentMaster()
    master.set_as_of_date(session_date)
    if raw_df is None or raw_df.empty or "instrument_key" not in raw_df.columns:
        return master

    seen_underlyings = set()
    for key in raw_df["instrument_key"].unique():
        parts = key.split("|")
        if len(parts) >= 2 and parts[1] == "OPT":
            opt = OptionContract.from_key(key)
            if opt:
                master.add_option(opt)
                if opt.underlying_symbol not in seen_underlyings:
                    master.add_underlying(Underlying(
                        symbol=opt.underlying_symbol, exchange="SMART",
                        currency="USD", sec_type="STK"))
                    seen_underlyings.add(opt.underlying_symbol)
        elif len(parts) >= 2 and parts[1] == "STK":
            if parts[0] not in seen_underlyings:
                master.add_underlying(Underlying(
                    symbol=parts[0], exchange=parts[2] if len(parts) > 2 else "SMART",
                    currency=parts[3] if len(parts) > 3 else "USD", sec_type="STK"))
                seen_underlyings.add(parts[0])
    return master


def _load_master_from_store(meta_store, session_date: date,
                              universe_cfg: dict) -> "InstrumentMaster":
    """Load instrument master from SQLite metadata store, or build a minimal one."""
    from src.universe.contracts import InstrumentMaster, Underlying, OptionContract
    from src.utils.dates import parse_ibkr_expiry
    import datetime as dt_mod

    master = InstrumentMaster()
    master.set_as_of_date(session_date)

    try:
        import pandas as pd
        df = pd.read_sql("SELECT * FROM instrument_master WHERE as_of_date = ?",
                         meta_store.engine, params=(session_date.isoformat(),))
        for _, row in df.iterrows():
            if row["sec_type"] != "OPT":
                u = Underlying(
                    symbol=row["underlying_symbol"],
                    exchange=row["exchange"] or "SMART",
                    currency=row["currency"] or "USD",
                    sec_type=row["sec_type"],
                    contract_id_broker=row.get("contract_id_broker"),
                )
                master.add_underlying(u)
            else:
                opt = OptionContract(
                    underlying_symbol=row["underlying_symbol"],
                    expiry=dt_mod.date.fromisoformat(row["expiry"]),
                    strike=float(row["strike"]),
                    right=row["right"],
                    exchange=row["exchange"] or "SMART",
                    currency=row["currency"] or "USD",
                    multiplier=int(row["multiplier"] or 100),
                    contract_id_broker=row.get("contract_id_broker"),
                )
                master.add_option(opt)

        if master.all_underlyings():
            return master
    except Exception as exc:
        logger.debug(f"Master store read failed: {exc}")

    # Fallback: build from universe config
    for u_cfg in universe_cfg.get("underlyings", []):
        u = Underlying(
            symbol=u_cfg["symbol"],
            exchange=u_cfg.get("exchange", "SMART"),
            currency=u_cfg.get("currency", "USD"),
            sec_type=u_cfg.get("sec_type", "STK"),
        )
        master.add_underlying(u)

    return master


# ---------------------------------------------------------------------------
# Job 2 — Full EOD analytics pipeline
# ---------------------------------------------------------------------------

def run_eod_pipeline(session_date: date, config_dir: str = "./configs",
                      data_dir: str = "./data",
                      positions: Optional[List] = None) -> dict:
    """
    End-of-day full analytics pipeline.
    Steps: snapshots → forwards → IV → surface → risk → scenarios → QC → manifest
    """
    from src.utils.config import load_config
    from src.storage.schemas import ParquetStore, MetadataStore
    from src.forwards.engine import estimate_forward, forward_result_to_dict
    from src.iv.solver import solve_chain_iv
    from src.surfaces.calibration import fit_surface, surface_to_dataframe
    from src.qc.checks import run_qc_suite, qc_results_to_dataframe
    from src.risk.scenarios import load_scenarios_from_config, run_all_scenarios, scenario_reports_to_dataframe
    from src.risk.aggregation import compute_position_risk, position_risk_to_dataframe
    from src.utils.dates import maturity_years as calc_t
    import datetime as dt_mod

    run_id     = new_run_id("eod", session_date)
    started_at = datetime.now(timezone.utc).isoformat()
    status     = "started"

    env_cfg      = load_config("environment", config_dir)
    qc_cfg       = load_config("qc", config_dir)
    pricing_cfg  = load_config("pricing", config_dir)
    universe_cfg = load_config("universe", config_dir)
    scenario_cfg = load_config("scenarios", config_dir)

    rate = pricing_cfg.get("risk_free_rate", {}).get("value", 0.053)

    raw_store       = ParquetStore(Path(data_dir) / "raw")
    analytics_store = ParquetStore(Path(data_dir) / "analytics")
    meta_store      = MetadataStore(Path(data_dir) / "metadata.db")

    logger.info(f"[{run_id}] EOD pipeline starting for {session_date}")

    try:
        # 1. Load market-state snapshots (built by build_snapshots_job)
        snapshot_df = analytics_store.read("market_state_snapshots", session_date)
        raw_events_df = raw_store.read("raw_market_events", session_date)

        if snapshot_df.empty:
            logger.warning(f"[{run_id}] No snapshots found — attempting to build them now")
            build_snapshots_job(session_date, config_dir, data_dir)
            snapshot_df = analytics_store.read("market_state_snapshots", session_date)

        if snapshot_df.empty:
            raise RuntimeError("No market-state snapshots available")

        # Add code_version + config lineage to all derived outputs
        _lineage = {
            "code_version":  CODE_VERSION,
            "session_date":  session_date.isoformat(),
            "run_id":        run_id,
        }

        # 2. Forward estimation
        logger.info(f"[{run_id}] Building forwards...")
        forward_results = []
        forward_dict: dict = {}

        for u_cfg in universe_cfg.get("underlyings", []):
            symbol      = u_cfg["symbol"]
            symbol_snap = snapshot_df[snapshot_df["underlying_symbol"] == symbol]
            if symbol_snap.empty:
                continue

            spot = float(symbol_snap["reference_spot"].iloc[-1])

            for expiry in symbol_snap["expiry"].unique():
                T = calc_t(
                    dt_mod.date.fromisoformat(expiry),
                    session_date
                )
                if T <= 0:
                    continue
                fwd_result = estimate_forward(
                    symbol_snap, symbol, expiry, T, spot, rate, qc_cfg
                )
                forward_results.append(fwd_result)
                forward_dict[expiry] = fwd_result

        fwd_rows = [forward_result_to_dict(r) for r in forward_results]
        fwd_df   = pd.DataFrame(fwd_rows)
        if not fwd_df.empty:
            for k, v in _lineage.items():
                fwd_df[k] = v
            analytics_store.write("forward_curve", fwd_df, session_date)

        # 3. IV solving
        logger.info(f"[{run_id}] Solving implied volatilities...")
        iv_rows = solve_chain_iv(snapshot_df, forward_dict, rate, qc_cfg)
        iv_df   = pd.DataFrame(iv_rows)
        if not iv_df.empty:
            for k, v in _lineage.items():
                iv_df[k] = v
            analytics_store.write("iv_points", iv_df, session_date)

        # 4. Surface calibration
        logger.info(f"[{run_id}] Fitting volatility surfaces...")
        surface_results = []
        snap_ts = snapshot_df["snapshot_ts"].iloc[-1] if not snapshot_df.empty else ""

        for u_cfg in universe_cfg.get("underlyings", []):
            symbol = u_cfg["symbol"]
            if not iv_df.empty:
                surface = fit_surface(iv_df, symbol, snap_ts, qc_cfg)
                surface_results.append(surface)
                surf_df = surface_to_dataframe(surface)
                if not surf_df.empty:
                    for k, v in _lineage.items():
                        surf_df[k] = v
                    analytics_store.write(
                        "surface_grid", surf_df, session_date,
                        filename=f"{symbol}_surface.parquet"
                    )

        # 5. Risk analytics (with provided positions)
        logger.info(f"[{run_id}] Computing risk analytics...")
        if positions:
            iv_rows_by_key = {r["contract_key"]: r for r in iv_rows}
            risk_list = []
            valuation_ts = datetime.now(timezone.utc).isoformat()
            for pos in positions:
                iv_row = iv_rows_by_key.get(pos.contract_key)
                if iv_row:
                    risk = compute_position_risk(pos, iv_row, rate, valuation_ts)
                    if risk:
                        risk_list.append(risk)
            if risk_list:
                risk_df = position_risk_to_dataframe(risk_list)
                for k, v in _lineage.items():
                    risk_df[k] = v
                analytics_store.write("risk_aggregates", risk_df, session_date)
        else:
            logger.info(f"[{run_id}] No positions provided — skipping risk step")
            iv_rows_by_key = {r["contract_key"]: r for r in iv_rows}

        # 6. Scenarios
        logger.info(f"[{run_id}] Running scenarios...")
        scenarios    = load_scenarios_from_config(scenario_cfg)
        active_positions = positions or []
        iv_rows_by_key   = {r["contract_key"]: r for r in iv_rows}
        scenario_reports = run_all_scenarios(active_positions, iv_rows_by_key, rate, scenarios)
        scen_df          = scenario_reports_to_dataframe(scenario_reports)
        if not scen_df.empty:
            for k, v in _lineage.items():
                scen_df[k] = v
            analytics_store.write("scenario_results", scen_df, session_date)

        # 7. QC suite
        logger.info(f"[{run_id}] Running QC checks...")
        from src.universe.contracts import Underlying
        underlyings = [
            Underlying(symbol=u["symbol"], exchange=u.get("exchange", "SMART"),
                       currency=u.get("currency", "USD"), sec_type=u.get("sec_type", "STK"))
            for u in universe_cfg.get("underlyings", [])
        ]
        qc_results = run_qc_suite(
            raw_events_df, snapshot_df, iv_df,
            forward_results, surface_results, scenario_reports,
            qc_cfg, underlyings
        )
        qc_df = qc_results_to_dataframe(qc_results, run_id)
        if not qc_df.empty:
            for k, v in _lineage.items():
                qc_df[k] = v
            analytics_store.write("qc_results", qc_df, session_date)

        status = "success"
        logger.info(f"[{run_id}] EOD pipeline COMPLETE")

    except Exception as exc:
        status = "failed"
        logger.error(f"[{run_id}] EOD pipeline FAILED: {exc}")
        raise

    finally:
        finished_at = datetime.now(timezone.utc).isoformat()
        manifest = {
            "run_id":       run_id,
            "job_name":     "eod",
            "session_date": session_date.isoformat(),
            "code_version": CODE_VERSION,
            "status":       status,
            "started_at":   started_at,
            "finished_at":  finished_at,
            "notes":        "",
        }
        meta_store.save_manifest(manifest)
        # Write manifest to file system too
        manifest_path = Path(data_dir) / "manifests"
        manifest_path.mkdir(parents=True, exist_ok=True)
        with open(manifest_path / f"{run_id}.json", "w") as f:
            json.dump(manifest, f, indent=2)
        logger.info(f"Manifest saved: {manifest}")

    return manifest


# ---------------------------------------------------------------------------
# Job 3 — Historical replay
# ---------------------------------------------------------------------------

def replay_pipeline(start_date: date, end_date: date,
                    config_dir: str = "./configs",
                    data_dir: str = "./data") -> List[dict]:
    """
    Replay the full pipeline over a date range using the same code path as live.
    Writes outputs to versioned partitions so prior analytics are preserved.
    """
    manifests = []
    d = start_date
    while d <= end_date:
        raw_store = __import__("src.storage.schemas", fromlist=["ParquetStore"]).ParquetStore(
            Path(data_dir) / "raw"
        )
        if raw_store.partition_exists("raw_market_events", d):
            try:
                logger.info(f"Replaying {d}")
                m = run_eod_pipeline(d, config_dir, data_dir)
                manifests.append(m)
            except Exception as exc:
                logger.error(f"Replay failed for {d}: {exc}")
                manifests.append({"date": d.isoformat(), "status": "failed", "error": str(exc)})
        else:
            logger.warning(f"No raw events for {d} — skipping")

        from datetime import timedelta
        d = d + timedelta(days=1)

    return manifests


# ---------------------------------------------------------------------------
# Replay QA — comparaison replay vs live (Step 13)
# ---------------------------------------------------------------------------

def compare_replay_vs_live(live_df, replay_df,
                            key_cols=("instrument_key",),
                            value_cols=("forward", "implied_vol")) -> dict:
    """
    Compare deux jeux d'analytics (live vs replay recalculé depuis le raw) sur les
    mêmes clés et retourne les écarts par métrique. La roadmap exige que replay et
    live s'alignent sur les périodes chevauchantes ; ceci en est la mesure.
    """
    if live_df is None or replay_df is None or live_df.empty or replay_df.empty:
        return {"status": "no_data", "metrics": {}}

    # Auto-détection de la clé de jointure (instrument_key / contract_key)
    keys = [k for k in key_cols if k in live_df.columns and k in replay_df.columns]
    if not keys:
        for cand in ("instrument_key", "contract_key"):
            if cand in live_df.columns and cand in replay_df.columns:
                keys = [cand]
                break
    if not keys:
        return {"status": "no_common_key", "metrics": {}}

    merged = live_df.merge(replay_df, on=keys, suffixes=("_live", "_replay"))
    if merged.empty:
        return {"status": "no_overlap", "metrics": {}}

    out = {"status": "ok", "n_overlap": int(len(merged)), "metrics": {}}
    for col in value_cols:
        a, b = f"{col}_live", f"{col}_replay"
        if a in merged.columns and b in merged.columns:
            diff = (pd.to_numeric(merged[a], errors="coerce")
                    - pd.to_numeric(merged[b], errors="coerce")).abs().dropna()
            if len(diff) > 0:
                out["metrics"][col] = {
                    "max_abs_diff": float(diff.max()),
                    "mean_abs_diff": float(diff.mean()),
                    "n": int(len(diff)),
                }
    return out
