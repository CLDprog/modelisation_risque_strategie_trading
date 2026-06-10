"""
Forward and carry engine (Step 6).

Reconstructs the forward price F(T) for each maturity using put-call parity.
Equations from the roadmap:
  Eq 2. F_i = K + e^(r*T) * (C - P)   [parity forward per strike]
  Eq 4. F = sum(w_i * F_i) / sum(w_i)  [weighted aggregation]
  Eq 5. q = (ln(F/S) - r*T) / (-T)     [implied carry/dividend]
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ForwardCandidate:
    strike: float
    maturity_years: float
    call_mid: float
    put_mid: float
    forward_estimate: float
    weight: float
    quality_flag: str       # "ok" | "outlier" | "illiquid"


@dataclass(frozen=True)
class ForwardResult:
    underlying: str
    snapshot_ts: str
    expiry: str
    maturity_years: float
    chosen_forward: float
    confidence_score: float    # 0 (poor) → 1 (high)
    candidates_used: int
    candidates_total: int
    candidates: List[ForwardCandidate]
    implied_carry: Optional[float]
    quality_flag: str          # "high" | "medium" | "low" | "failed"
    diagnostics_version: str = "v1.0"


def forward_candidates_to_dataframe(forward_results) -> "pd.DataFrame":
    """
    Candidats forward individuels (dont rejetés 'outlier'/'illiquid') → table
    `forward_diagnostics` (roadmap : diagnostics forward rejetés non persistés).
    """
    import pandas as pd
    rows = []
    for r in forward_results:
        for c in r.candidates:
            rows.append({
                "underlying": r.underlying,
                "snapshot_ts": r.snapshot_ts,
                "expiry": r.expiry,
                "maturity_years": r.maturity_years,
                "strike": c.strike,
                "call_mid": c.call_mid,
                "put_mid": c.put_mid,
                "forward_estimate": c.forward_estimate,
                "weight": c.weight,
                "quality_flag": c.quality_flag,           # ok | outlier | illiquid
                "used": c.quality_flag == "ok",
                "chosen_forward": r.chosen_forward,
                "diagnostics_version": r.diagnostics_version,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Pure calculation functions  (Equations 2, 4, 5 du roadmap)
# ---------------------------------------------------------------------------

def parity_forward(strike: float, call_mid: float, put_mid: float,
                   rate: float, maturity_years: float) -> float:
    """Eq 2: F_i = K + e^(r*T) * (C - P)"""
    return strike + math.exp(rate * maturity_years) * (call_mid - put_mid)


def liquidity_weight(call_mid: float, put_mid: float,
                     call_bid: float, put_bid: float,
                     call_ask: float, put_ask: float) -> float:
    """
    Weight inversely proportional to combined spread.
    Robuste aux bid/ask manquants (None) : une quote sans bid/ask (prix en
    last/close uniquement) reçoit un spread inconnu → poids 0 (mais le candidat
    reste compté ; la moyenne retombera sur la médiane si tous les poids sont nuls).
    """
    def _spread(ask, bid):
        if ask is None or bid is None:
            return float("inf")
        return (ask - bid) if ask > bid else float("inf")

    call_spread = _spread(call_ask, call_bid)
    put_spread  = _spread(put_ask, put_bid)
    combined_spread = call_spread + put_spread
    if combined_spread == 0 or combined_spread == float("inf"):
        return 0.0
    return 1.0 / combined_spread


def robust_zscore_mad(values: list[float]) -> list[float]:
    """Eq 24: robust z-score using MAD."""
    if len(values) < 2:
        return [0.0] * len(values)
    med = statistics.median(values)
    deviations = [abs(v - med) for v in values]
    mad = statistics.median(deviations) or 1e-8
    return [(v - med) / (1.4826 * mad) for v in values]


def weighted_mean(values: list[float], weights: list[float]) -> float:
    """Eq 4: F = sum(w_i * F_i) / sum(w_i)"""
    total_w = sum(weights)
    if total_w == 0:
        return statistics.median(values) if values else float("nan")
    return sum(v * w for v, w in zip(values, weights)) / total_w


def implied_carry(spot: float, forward: float,
                  rate: float, maturity_years: float) -> float:
    """Eq 5: q = (ln(F/S) - r*T) / (-T)"""
    if spot <= 0 or forward <= 0 or maturity_years <= 0:
        return float("nan")
    return -(math.log(forward / spot) - rate * maturity_years) / maturity_years


# ---------------------------------------------------------------------------
# Main forward estimator
# ---------------------------------------------------------------------------

def estimate_forward(snapshot_df: pd.DataFrame, underlying_symbol: str,
                     expiry: str, maturity_years: float, spot: float,
                     rate: float, cfg: dict) -> ForwardResult:
    """
    Estimate the forward price for a given maturity from option quotes.

    snapshot_df: option rows with columns bid, ask, strike, right, expiry, is_usable
    """
    from src.utils.dates import utc_now

    snap_ts = snapshot_df["snapshot_ts"].iloc[0] if not snapshot_df.empty else ""
    fwd_cfg = cfg.get("forward_engine", {})
    max_robust_z = fwd_cfg.get("max_robust_zscore", 3.5)
    max_candidates = fwd_cfg.get("max_candidate_count", 12)
    min_candidates = fwd_cfg.get("min_candidate_count", 3)

    # Filter to this expiry, usable quotes
    exp_df = snapshot_df[
        (snapshot_df["expiry"] == expiry) &
        (snapshot_df["is_usable"] == True) &
        (snapshot_df["underlying_symbol"] == underlying_symbol)
    ].copy()

    calls = exp_df[exp_df["right"] == "C"].set_index("strike")
    puts = exp_df[exp_df["right"] == "P"].set_index("strike")

    # Build call-put pairs near the money
    common_strikes = set(calls.index) & set(puts.index)
    atm_strikes = sorted(common_strikes, key=lambda k: abs(k - spot))[:max_candidates]

    candidates: List[ForwardCandidate] = []
    for strike in atm_strikes:
        call_row = calls.loc[strike]
        put_row = puts.loc[strike]

        call_mid = call_row.get("mid_price")
        put_mid = put_row.get("mid_price")
        if call_mid is None or put_mid is None:
            continue
        if call_mid <= 0 or put_mid <= 0:
            continue

        fwd_est = parity_forward(strike, call_mid, put_mid, rate, maturity_years)

        # Liquidity weight
        w = liquidity_weight(
            call_mid, put_mid,
            call_row.get("bid", 0), put_row.get("bid", 0),
            call_row.get("ask", call_mid * 2), put_row.get("ask", put_mid * 2),
        )
        candidates.append(ForwardCandidate(
            strike=strike,
            maturity_years=maturity_years,
            call_mid=call_mid,
            put_mid=put_mid,
            forward_estimate=fwd_est,
            weight=w,
            quality_flag="ok",
        ))

    if len(candidates) < min_candidates:
        logger.warning(f"{underlying_symbol} {expiry}: only {len(candidates)} candidates "
                       f"(min={min_candidates}) → forward failed")
        return ForwardResult(
            underlying=underlying_symbol, snapshot_ts=snap_ts, expiry=expiry,
            maturity_years=maturity_years, chosen_forward=float("nan"),
            confidence_score=0.0, candidates_used=0,
            candidates_total=len(candidates), candidates=candidates,
            implied_carry=float("nan"), quality_flag="failed",
        )

    # Outlier rejection via robust z-score (MAD)
    fwd_values = [c.forward_estimate for c in candidates]
    z_scores = robust_zscore_mad(fwd_values)

    clean_candidates = []
    rejected_as_outlier = []
    for c, z in zip(candidates, z_scores):
        if abs(z) > max_robust_z:
            rejected_as_outlier.append(
                ForwardCandidate(c.strike, c.maturity_years, c.call_mid, c.put_mid,
                                 c.forward_estimate, c.weight, "outlier")
            )
        else:
            clean_candidates.append(c)

    if len(clean_candidates) < min_candidates:
        clean_candidates = candidates  # revert if too many rejected

    all_candidates = clean_candidates + rejected_as_outlier

    # Weighted average of clean candidates
    fwds = [c.forward_estimate for c in clean_candidates]
    weights = [c.weight for c in clean_candidates]
    chosen_fwd = weighted_mean(fwds, weights)

    # Confidence score: based on candidate density and consistency
    mad_fwds = statistics.median([abs(f - chosen_fwd) for f in fwds]) if fwds else float("inf")
    consistency = 1.0 / (1.0 + mad_fwds / (spot * 0.001 + 1e-8))
    density = min(len(clean_candidates) / 6.0, 1.0)
    confidence = 0.5 * consistency + 0.5 * density

    q = implied_carry(spot, chosen_fwd, rate, maturity_years)

    quality = "high" if confidence > 0.7 else "medium" if confidence > 0.4 else "low"

    logger.debug(f"{underlying_symbol} {expiry}: F={chosen_fwd:.4f}, "
                 f"confidence={confidence:.2f} ({quality}), "
                 f"{len(clean_candidates)}/{len(candidates)} candidates used")

    return ForwardResult(
        underlying=underlying_symbol,
        snapshot_ts=snap_ts,
        expiry=expiry,
        maturity_years=maturity_years,
        chosen_forward=chosen_fwd,
        confidence_score=confidence,
        candidates_used=len(clean_candidates),
        candidates_total=len(candidates),
        candidates=all_candidates,
        implied_carry=q,
        quality_flag=quality,
    )


def forward_result_to_dict(r: ForwardResult) -> dict:
    return {
        "underlying": r.underlying,
        "snapshot_ts": r.snapshot_ts,
        "expiry": r.expiry,
        "maturity_years": r.maturity_years,
        "chosen_forward": r.chosen_forward,
        "confidence_score": r.confidence_score,
        "candidates_used": r.candidates_used,
        "candidates_total": r.candidates_total,
        "implied_carry": r.implied_carry,
        "quality_flag": r.quality_flag,
        "diagnostics_version": r.diagnostics_version,
    }
