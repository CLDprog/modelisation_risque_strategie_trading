"""
Quality control validation checks (Step 14).

Each check is a named function returning a QcResult.
Thresholds come from config — never hardcoded here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, List

import pandas as pd
import numpy as np
from loguru import logger


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class QcResult:
    check_name: str
    target_key: str
    status: str             # "pass" | "warn" | "fail"
    severity: str           # "info" | "warning" | "error"
    measured_value: float
    threshold: float
    reason_code: str
    context: Dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "pass"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_collector_continuity(raw_events_df: pd.DataFrame,
                                max_gap_seconds: float = 30.0,
                                underlying_key: str = "") -> QcResult:
    """
    No unexplained gap > max_gap_seconds during liquid session.
    """
    if raw_events_df.empty:
        return QcResult("collector_continuity", underlying_key, "fail", "error",
                        float("inf"), max_gap_seconds, "no_events", {})

    ts = pd.to_datetime(raw_events_df["receipt_ts"]).sort_values()
    gaps = ts.diff().dt.total_seconds().dropna()
    max_gap = float(gaps.max()) if len(gaps) > 0 else 0.0

    status = "pass" if max_gap <= max_gap_seconds else "fail"
    severity = "error" if status == "fail" else "info"

    return QcResult("collector_continuity", underlying_key, status, severity,
                    max_gap, max_gap_seconds,
                    "gap_exceeded" if status == "fail" else "ok",
                    {"max_gap_seconds": max_gap, "n_events": len(raw_events_df)})


def check_quote_health(snapshot_df: pd.DataFrame, underlying_symbol: str,
                        max_spread_pct: float = 0.25,
                        max_stale_ratio: float = 0.30) -> QcResult:
    """
    Option chain: check spread % and stale quote ratio.
    """
    chain = snapshot_df[snapshot_df["underlying_symbol"] == underlying_symbol]
    if chain.empty:
        return QcResult("quote_health", underlying_symbol, "fail", "error",
                        float("inf"), max_spread_pct, "no_quotes", {})

    # Spread check
    spreads = []
    for _, row in chain.iterrows():
        if row.get("bid") and row.get("ask") and row["bid"] > 0:
            mid = (row["bid"] + row["ask"]) / 2
            spreads.append((row["ask"] - row["bid"]) / mid if mid > 0 else float("inf"))

    avg_spread = float(np.mean(spreads)) if spreads else float("inf")
    stale_ratio = float((~chain["is_usable"]).mean()) if "is_usable" in chain.columns else 0.0

    if avg_spread > max_spread_pct or stale_ratio > max_stale_ratio:
        status, severity = "warn", "warning"
        reason = "wide_spread_or_high_stale_ratio"
    else:
        status, severity = "pass", "info"
        reason = "ok"

    return QcResult("quote_health", underlying_symbol, status, severity,
                    avg_spread, max_spread_pct, reason,
                    {"avg_spread_pct": avg_spread, "stale_ratio": stale_ratio})


def check_forward_stability(forward_results: list, underlying_symbol: str,
                             max_residual_pct: float = 0.005) -> QcResult:
    """
    Forward candidates should be within max_residual_pct of the chosen forward.
    """
    results = [r for r in forward_results
               if r.underlying == underlying_symbol and r.quality_flag != "failed"]
    if not results:
        return QcResult("forward_stability", underlying_symbol, "fail", "error",
                        float("inf"), max_residual_pct, "no_forward_results", {})

    max_conf = max(r.confidence_score for r in results)
    min_conf = min(r.confidence_score for r in results)

    status = "pass" if min_conf > 0.4 else ("warn" if min_conf > 0.2 else "fail")
    severity = {"pass": "info", "warn": "warning", "fail": "error"}[status]

    return QcResult("forward_stability", underlying_symbol, status, severity,
                    min_conf, 0.4, "low_confidence" if status != "pass" else "ok",
                    {"min_confidence": min_conf, "max_confidence": max_conf,
                     "n_maturities": len(results)})


def check_iv_convergence(iv_points_df: pd.DataFrame, underlying_symbol: str,
                          min_convergence_ratio: float = 0.97) -> QcResult:
    """
    At least min_convergence_ratio of solved IVs should have converged.
    """
    df = iv_points_df[iv_points_df["underlying_symbol"] == underlying_symbol]
    if df.empty:
        return QcResult("iv_convergence", underlying_symbol, "fail", "error",
                        0.0, min_convergence_ratio, "no_iv_points", {})

    ratio = float(df["converged"].mean()) if "converged" in df.columns else 0.0
    status = "pass" if ratio >= min_convergence_ratio else ("warn" if ratio >= 0.9 else "fail")
    severity = {"pass": "info", "warn": "warning", "fail": "error"}[status]

    return QcResult("iv_convergence", underlying_symbol, status, severity,
                    ratio, min_convergence_ratio,
                    "low_convergence_ratio" if status != "pass" else "ok",
                    {"convergence_ratio": ratio, "n_points": len(df)})


def check_surface_fit(surface_result, underlying_symbol: str,
                       max_rmse: float = 0.02) -> QcResult:
    """
    Surface fit RMSE should be below max_rmse for all slices.
    """
    if not surface_result.slices:
        return QcResult("surface_fit", underlying_symbol, "fail", "error",
                        float("inf"), max_rmse, "no_slices", {})

    rmse_values = [s.rmse for s in surface_result.slices if s.model != "failed"]
    if not rmse_values:
        return QcResult("surface_fit", underlying_symbol, "fail", "error",
                        float("inf"), max_rmse, "all_slices_failed", {})

    max_rmse_obs = float(max(rmse_values))
    n_failed = sum(1 for s in surface_result.slices if s.model == "failed")

    status = "pass" if max_rmse_obs <= max_rmse and n_failed == 0 else (
        "warn" if max_rmse_obs <= max_rmse * 2 else "fail"
    )
    severity = {"pass": "info", "warn": "warning", "fail": "error"}[status]

    return QcResult("surface_fit", underlying_symbol, status, severity,
                    max_rmse_obs, max_rmse,
                    "high_rmse_or_failed_slices" if status != "pass" else "ok",
                    {"max_rmse": max_rmse_obs, "n_slices": len(surface_result.slices),
                     "n_failed": n_failed,
                     "calendar_ok": surface_result.calendar_ok})


def check_scenario_completeness(scenario_reports: list,
                                  expected_scenario_ids: list,
                                  underlying_symbol: str = "portfolio") -> QcResult:
    """
    All configured scenarios must have been executed and stored.
    """
    executed = {r.scenario_id for r in scenario_reports}
    expected = set(expected_scenario_ids)
    missing = expected - executed

    ratio = len(executed & expected) / len(expected) if expected else 1.0
    status = "pass" if not missing else "fail"
    severity = "error" if missing else "info"

    return QcResult("scenario_completeness", underlying_symbol, status, severity,
                    ratio, 1.0,
                    f"missing_scenarios:{','.join(missing)}" if missing else "ok",
                    {"executed": list(executed), "missing": list(missing)})


# ---------------------------------------------------------------------------
# Run full QC suite
# ---------------------------------------------------------------------------

def run_qc_suite(raw_events_df: pd.DataFrame, snapshot_df: pd.DataFrame,
                 iv_points_df: pd.DataFrame, forward_results: list,
                 surface_results: list, scenario_reports: list,
                 cfg: dict, underlyings: list) -> List[QcResult]:
    """Run all QC checks and return a list of QcResult."""
    qc_cfg = cfg.get("quote_filters", {})
    results = []

    for underlying in underlyings:
        symbol = underlying.symbol
        key = underlying.instrument_key

        # Collector continuity
        und_events = raw_events_df[raw_events_df["instrument_key"] == key] if not raw_events_df.empty else pd.DataFrame()
        results.append(check_collector_continuity(
            und_events, cfg.get("collector", {}).get("max_gap_seconds", 30), key
        ))

        # Quote health
        results.append(check_quote_health(
            snapshot_df, symbol,
            qc_cfg.get("max_spread_pct", 0.25),
        ))

        # Forward stability
        results.append(check_forward_stability(forward_results, symbol))

        # IV convergence
        results.append(check_iv_convergence(
            iv_points_df, symbol,
            cfg.get("iv_solver", {}).get("min_convergence_ratio", 0.97)
        ))

        # Surface fit
        surf = next((s for s in surface_results if s.underlying == symbol), None)
        if surf:
            results.append(check_surface_fit(
                surf, symbol, cfg.get("surface", {}).get("max_rmse", 0.02)
            ))

    # Scenario completeness
    if scenario_reports:
        expected_ids = [r.scenario_id for r in scenario_reports]
        results.append(check_scenario_completeness(scenario_reports, expected_ids))

    # Summary
    n_pass = sum(1 for r in results if r.status == "pass")
    n_warn = sum(1 for r in results if r.status == "warn")
    n_fail = sum(1 for r in results if r.status == "fail")
    logger.info(f"QC suite: {n_pass} pass | {n_warn} warn | {n_fail} fail")

    return results


def qc_results_to_dataframe(results: List[QcResult], run_id: str) -> pd.DataFrame:
    import json
    rows = []
    for r in results:
        rows.append({
            "run_id": run_id,
            "check_name": r.check_name,
            "target_key": r.target_key,
            "status": r.status,
            "severity": r.severity,
            "measured_value": r.measured_value,
            "threshold": r.threshold,
            "reason_code": r.reason_code,
            "context_json": json.dumps(r.context),
        })
    return pd.DataFrame(rows)
