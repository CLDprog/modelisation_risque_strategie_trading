"""
Job entry points and orchestration (Step 15).

Each job is a thin wrapper that:
  1. Loads config and state
  2. Calls library functions in order
  3. Writes outputs + manifest
  4. Emits metrics
"""
from __future__ import annotations

import uuid
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
from loguru import logger


CODE_VERSION = "vol-infra-1.0.0"


def new_run_id(job_name: str, session_date: date) -> str:
    return f"{session_date.isoformat()}_{job_name}_{str(uuid.uuid4())[:6]}"


# ---------------------------------------------------------------------------
# EOD analytics pipeline
# ---------------------------------------------------------------------------

def run_eod_pipeline(session_date: date, config_dir: str = "./configs",
                      data_dir: str = "./data") -> dict:
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
    from src.utils.dates import maturity_years as calc_t
    import datetime as dt_mod

    run_id = new_run_id("eod", session_date)
    started_at = datetime.now(timezone.utc).isoformat()
    status = "started"

    # Load configs
    env_cfg = load_config("environment", config_dir)
    qc_cfg = load_config("qc", config_dir)
    pricing_cfg = load_config("pricing", config_dir)
    universe_cfg = load_config("universe", config_dir)
    scenario_cfg = load_config("scenarios", config_dir)

    rate = pricing_cfg.get("risk_free_rate", {}).get("value", 0.05)

    raw_store = ParquetStore(Path(data_dir) / "raw")
    analytics_store = ParquetStore(Path(data_dir) / "analytics")
    meta_store = MetadataStore(Path(data_dir) / "metadata.db")

    logger.info(f"[{run_id}] EOD pipeline starting for {session_date}")

    try:
        # 1. Load raw snapshots
        snapshot_df = raw_store.read("market_state_snapshots", session_date)
        raw_events_df = raw_store.read("raw_market_events", session_date)

        if snapshot_df.empty:
            raise RuntimeError("No market-state snapshots found for session date")

        # 2. Forward estimation
        logger.info(f"[{run_id}] Building forwards...")
        forward_results = []
        forward_dict = {}  # expiry → ForwardResult

        for symbol in [u["symbol"] for u in universe_cfg.get("underlyings", [])]:
            symbol_snap = snapshot_df[snapshot_df["underlying_symbol"] == symbol]
            if symbol_snap.empty:
                continue
            spot = symbol_snap["reference_spot"].iloc[-1]

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

        fwd_df = pd.DataFrame([forward_result_to_dict(r) for r in forward_results])
        analytics_store.write("forward_curve", fwd_df, session_date)

        # 3. IV solving
        logger.info(f"[{run_id}] Solving implied volatilities...")
        iv_rows = solve_chain_iv(snapshot_df, forward_dict, rate, qc_cfg)
        iv_df = pd.DataFrame(iv_rows)
        if not iv_df.empty:
            analytics_store.write("iv_points", iv_df, session_date)

        # 4. Surface calibration
        logger.info(f"[{run_id}] Fitting volatility surfaces...")
        surface_results = []
        snap_ts = snapshot_df["snapshot_ts"].iloc[-1] if not snapshot_df.empty else ""
        for symbol in [u["symbol"] for u in universe_cfg.get("underlyings", [])]:
            if not iv_df.empty:
                surface = fit_surface(iv_df, symbol, snap_ts, qc_cfg)
                surface_results.append(surface)
                surf_df = surface_to_dataframe(surface)
                if not surf_df.empty:
                    analytics_store.write("surface_grid", surf_df, session_date,
                                          filename=f"{symbol}_surface.parquet")

        # 5. Scenarios (with synthetic flat positions if none loaded)
        logger.info(f"[{run_id}] Running scenarios...")
        scenarios = load_scenarios_from_config(scenario_cfg)
        iv_rows_by_key = {r["contract_key"]: r for r in iv_rows}
        scenario_reports = run_all_scenarios([], iv_rows_by_key, rate, scenarios)
        scen_df = scenario_reports_to_dataframe(scenario_reports)
        if not scen_df.empty:
            analytics_store.write("scenario_results", scen_df, session_date)

        # 6. QC suite
        logger.info(f"[{run_id}] Running QC checks...")
        from src.universe.contracts import Underlying
        underlyings = [
            Underlying(symbol=u["symbol"], exchange=u["exchange"],
                       currency=u["currency"], sec_type=u["sec_type"])
            for u in universe_cfg.get("underlyings", [])
        ]
        qc_results = run_qc_suite(
            raw_events_df, snapshot_df, iv_df,
            forward_results, surface_results, scenario_reports,
            qc_cfg, underlyings
        )
        qc_df = qc_results_to_dataframe(qc_results, run_id)
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
            "run_id": run_id,
            "job_name": "eod",
            "session_date": session_date.isoformat(),
            "code_version": CODE_VERSION,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "notes": "",
        }
        meta_store.save_manifest(manifest)
        logger.info(f"Manifest saved: {manifest}")

    return manifest
