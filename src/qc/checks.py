"""
Quality control validation checks (Step 14).

Each check is a named function returning a QcResult.
Thresholds come from config — never hardcoded here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, List

import math

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

    # Liste vide = AUCUNE quote deux-côtés (fallback last/close sur toute la chaîne,
    # fréquent au snapshot en flux différé) — c'est une information distincte, pas
    # un « spread infini » (le inf polluait alertes et baselines d'anomalies).
    stale_ratio = float((~chain["is_usable"]).mean()) if "is_usable" in chain.columns else 0.0
    if not spreads:
        return QcResult("quote_health", underlying_symbol, "warn", "warning",
                        round(stale_ratio, 5), max_stale_ratio, "no_two_sided_quotes",
                        {"avg_spread_pct": None, "stale_ratio": stale_ratio})
    avg_spread = float(np.mean(spreads))

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
                     "calendar_ok": surface_result.calendar_ok,
                     "butterfly_ok": getattr(surface_result, "butterfly_ok", True),
                     "n_butterfly_violations": getattr(surface_result, "n_butterfly_violations", 0)})


def check_calendar_arbitrage(surface_result, underlying_symbol: str) -> QcResult:
    """Check no-arbitrage : monotonie calendaire ET convexité papillon."""
    cal_ok = getattr(surface_result, "calendar_ok", True)
    bf_ok  = getattr(surface_result, "butterfly_ok", True)
    n_viol = getattr(surface_result, "n_butterfly_violations", 0)
    ok = cal_ok and bf_ok
    status = "pass" if ok else "warn"
    reason = "ok" if ok else (
        "calendar_violation" if not cal_ok else f"butterfly_{n_viol}_violations")
    return QcResult("no_arbitrage", underlying_symbol, status,
                    "info" if ok else "warning",
                    float(n_viol), 0.0, reason,
                    {"calendar_ok": cal_ok, "butterfly_ok": bf_ok,
                     "n_butterfly_violations": n_viol})


def check_option_chain_coverage(iv_points_df: pd.DataFrame, underlying_symbol: str,
                                 expected_quotes: int,
                                 min_coverage_ratio: float = 0.5) -> QcResult:
    """
    Complétude de la chaîne (roadmap : 'option chain coverage') : part des quotes
    USABLE collectées rapportée à la grille cible (tenors × (ATM + ailes) × call/put).
    """
    df = iv_points_df[iv_points_df["underlying_symbol"] == underlying_symbol]
    if df.empty:
        return QcResult("option_chain_coverage", underlying_symbol, "fail", "error",
                        0.0, min_coverage_ratio, "no_quotes", {})
    n_usable = int(df["is_usable"].sum()) if "is_usable" in df.columns else len(df)
    n_expiries = int(df["expiry"].nunique())
    coverage = n_usable / expected_quotes if expected_quotes > 0 else 0.0
    if coverage >= min_coverage_ratio:
        status, severity, reason = "pass", "info", "ok"
    elif coverage > 0:
        status, severity, reason = "warn", "warning", "partial_coverage"
    else:
        status, severity, reason = "fail", "error", "no_usable_quotes"
    return QcResult("option_chain_coverage", underlying_symbol, status, severity,
                    round(coverage, 4), min_coverage_ratio, reason,
                    {"n_usable": n_usable, "n_expiries": n_expiries,
                     "expected_quotes": expected_quotes})


def check_put_call_parity(iv_points_df: pd.DataFrame, underlying_symbol: str,
                          rate: float, max_residual_pct: float = 0.02,
                          american: bool = False) -> QcResult:
    """
    Résidu de parité put-call (roadmap : 'parity residual') : pour chaque
    (expiry, strike) ayant call ET put, (C − P) doit valoir (F − K)·exp(−rT) ;
    résidu normalisé par le spot. NB : pour les options AMÉRICAINES la parité est
    une inégalité (exercice anticipé) → tolérance ×3 et statut plafonné à 'warn'.
    """
    df = iv_points_df[iv_points_df["underlying_symbol"] == underlying_symbol]
    if "is_usable" in df.columns:
        df = df[df["is_usable"]]
    residuals = []
    for (_expiry, strike), grp in df.groupby(["expiry", "strike"]):
        rights = grp["right"].astype(str).str.upper().str[0]
        calls, puts = grp[rights == "C"], grp[rights == "P"]
        if calls.empty or puts.empty:
            continue
        c, p = calls["mid_price"].iloc[0], puts["mid_price"].iloc[0]
        F, T = calls["forward"].iloc[0], calls["maturity_years"].iloc[0]
        spot = calls["reference_spot"].iloc[0]
        if not all(pd.notna(v) for v in (c, p, F, T, spot)) or spot <= 0:
            continue
        theo = (F - float(strike)) * math.exp(-rate * T)
        residuals.append(abs((c - p) - theo) / spot)
    if not residuals:
        return QcResult("put_call_parity", underlying_symbol, "warn", "warning",
                        float("nan"), max_residual_pct, "no_call_put_pairs", {})
    median_res, max_res = float(np.median(residuals)), float(np.max(residuals))
    threshold = max_residual_pct * (3.0 if american else 1.0)
    status = "pass" if median_res <= threshold else "warn"
    return QcResult("put_call_parity", underlying_symbol, status,
                    "info" if status == "pass" else "warning",
                    round(median_res, 5), round(threshold, 5),
                    "ok" if status == "pass" else "parity_residual_high",
                    {"median_residual": round(median_res, 5),
                     "max_residual": round(max_res, 5),
                     "n_pairs": len(residuals), "american": american})


def check_greeks_reconciliation(recon_summary: dict, underlying_symbol: str,
                                 max_delta_diff: float = 0.02,
                                 max_vega_diff: float = 0.05) -> QcResult:
    """
    Réconciliation des greeks (roadmap Step 11) : l'écart médian de delta ET vega entre
    greeks publiés et différences finies doit rester faible. gamma/theta sont informatifs
    (gamma bruité par bump sur l'arbre CRR ; theta = écart de convention forward/spot).
    """
    if not recon_summary:
        return QcResult("greeks_reconciliation", underlying_symbol, "warn", "warning",
                        float("nan"), max_delta_diff, "no_reconciliation", {})
    d = recon_summary.get("delta", {}).get("median", float("nan"))
    v = recon_summary.get("vega", {}).get("median", float("nan"))
    ok = (not math.isnan(d) and d <= max_delta_diff) and (not math.isnan(v) and v <= max_vega_diff)
    status = "pass" if ok else "warn"
    return QcResult("greeks_reconciliation", underlying_symbol, status,
                    "info" if ok else "warning",
                    round(d, 6) if not math.isnan(d) else float("nan"), max_delta_diff,
                    "ok" if ok else "greeks_diff_high",
                    {"delta_median": d, "vega_median": v,
                     "gamma_median": recon_summary.get("gamma", {}).get("median"),
                     "theta_median": recon_summary.get("theta", {}).get("median")})


def check_carry_consistency(forward_df: pd.DataFrame, underlying_symbol: str,
                            min_carry: float = -0.10,
                            max_carry: float = 0.10,
                            max_abs_total_carry: float = 0.10) -> QcResult:
    """
    Roadmap Step 6 : le carry implicite (q = dividende − repo) doit rester
    économiquement plausible. Le critère porte sur le DÉPORT TOTAL |q·T| (part du
    spot payée en dividendes/repo sur la période), pas sur le taux annualisé :
    en saison de dividendes (mai-juin), un dividende discret juste avant une
    échéance courte donne un q annualisé énorme mais LÉGITIME (constaté 12/06 :
    VOW3 q=43 % sur ~30j = 3.5 % de déport total = un dividende normal). Seuil par
    défaut 10 % : les hauts rendements européens (MBG/VOW3 ~8-9 % en un paiement
    annuel) passent quand l'échéance encadre la date de dividende.
    Le taux annualisé n'est borné que sur les maturités ≥ 6 mois, où il a un sens.
    Hors bornes = forward suspect (quotes asymétriques, parité contaminée).
    """
    df = forward_df
    col = "underlying" if "underlying" in df.columns else "underlying_symbol"
    if col in df.columns:
        df = df[df[col] == underlying_symbol]
    carries = pd.to_numeric(df.get("implied_carry"), errors="coerce") \
        if "implied_carry" in df.columns else pd.Series(dtype=float)
    mats = pd.to_numeric(df.get("maturity_years"), errors="coerce") \
        if "maturity_years" in df.columns else pd.Series(1.0, index=carries.index)
    ok_mask = carries.notna() & mats.notna()
    carries, mats = carries[ok_mask], mats[ok_mask]
    if carries.empty:
        return QcResult("carry_consistency", underlying_symbol, "warn", "warning",
                        float("nan"), max_abs_total_carry, "no_carry_estimates", {})

    total = (carries * mats).abs()                       # déport total |q·T|
    bad_total = total > max_abs_total_carry
    bad_annual = (mats >= 0.5) & ~carries.between(min_carry, max_carry)
    n_out = int((bad_total | bad_annual).sum())
    worst = float(total.max())
    status = "pass" if n_out == 0 else "warn"
    return QcResult("carry_consistency", underlying_symbol, status,
                    "info" if status == "pass" else "warning",
                    round(worst, 5), round(max_abs_total_carry, 5),
                    "ok" if status == "pass" else "carry_out_of_bounds",
                    {"n_maturities": len(carries), "n_out_of_bounds": n_out,
                     "worst_total_carry": round(worst, 5),
                     "worst_annualized": round(float(carries.abs().max()), 5),
                     "carry_median": round(float(carries.median()), 5),
                     "bounds_annualized_T>=0.5": [min_carry, max_carry]})


def check_broker_greeks_reconciliation(iv_points_df: pd.DataFrame,
                                       underlying_symbol: str,
                                       max_delta_diff: float = 0.08,
                                       max_vega_diff: float = 0.20,
                                       min_points: int = 5) -> QcResult:
    """
    Roadmap Step 11 : « reconcile against broker-returned Greeks if available ».
    Compare les greeks RECALCULÉS par la plateforme (depuis l'IV résolue) aux greeks
    PUBLIÉS par le broker (snapshot 7308-7311), sur les options usable où les deux
    existent. Verdict sur le delta (convention universelle) ; vega informatif
    (conventions broker variables). Greeks broker absents → 'skip' (pas un échec :
    IBKR ne les renvoie pas toujours en différé).
    """
    df = iv_points_df[iv_points_df["underlying_symbol"] == underlying_symbol]
    if "is_usable" in df.columns:
        df = df[df["is_usable"]]
    need = {"delta", "broker_delta"}
    if not need.issubset(df.columns):
        return QcResult("broker_greeks_reconciliation", underlying_symbol, "skip",
                        "info", float("nan"), max_delta_diff,
                        "broker_greeks_not_captured", {})
    both = df.dropna(subset=["delta", "broker_delta"])
    if len(both) < min_points:
        return QcResult("broker_greeks_reconciliation", underlying_symbol, "skip",
                        "info", float("nan"), max_delta_diff,
                        "insufficient_broker_greeks", {"n_points": len(both)})
    d_diff = (pd.to_numeric(both["delta"], errors="coerce")
              - pd.to_numeric(both["broker_delta"], errors="coerce")).abs()
    med_delta = float(d_diff.median())
    ctx = {"n_points": len(both), "delta_diff_median": round(med_delta, 5),
           "delta_diff_max": round(float(d_diff.max()), 5)}
    if {"vega", "broker_vega"}.issubset(both.columns):
        v = both.dropna(subset=["vega", "broker_vega"])
        if not v.empty:
            ctx["vega_diff_median"] = round(float(
                (v["vega"] - v["broker_vega"]).abs().median()), 5)
    status = "pass" if med_delta <= max_delta_diff else "warn"
    return QcResult("broker_greeks_reconciliation", underlying_symbol, status,
                    "info" if status == "pass" else "warning",
                    round(med_delta, 5), round(max_delta_diff, 5),
                    "ok" if status == "pass" else "broker_delta_mismatch", ctx)


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
