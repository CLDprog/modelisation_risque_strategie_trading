"""
Implied-volatility inversion engine (Step 8).

Uses Brent's bracketed root-finding method (scipy.optimize.brentq) for
reliability. Newton-Raphson is used as an optional accelerator only inside
a safe bracket.

Every solve returns a structured IvSolveResult, even on failure.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.optimize import brentq

from src.pricing.european import bs_price


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IvSolveResult:
    contract_key: str
    snapshot_ts: str
    market_price: float
    implied_vol: Optional[float]
    converged: bool
    iterations: int
    residual: float
    lower_bound: float
    upper_bound: float
    failure_reason: Optional[str]
    model_name: str = "black_scholes"
    model_version: str = "v1"


# ---------------------------------------------------------------------------
# Intrinsic value and no-arbitrage bounds
# ---------------------------------------------------------------------------

def intrinsic_value(forward: float, strike: float, maturity: float,
                    rate: float, right: str) -> float:
    discount = math.exp(-rate * maturity)
    r = right.upper()[0]
    if r == "C":
        return max(discount * (forward - strike), 0.0)
    return max(discount * (strike - forward), 0.0)


def check_price_bounds(market_price: float, forward: float, strike: float,
                       maturity: float, rate: float, right: str) -> Optional[str]:
    """Return a failure reason if the price is outside no-arbitrage bounds, else None."""
    if market_price <= 0:
        return "non_positive_price"
    intrinsic = intrinsic_value(forward, strike, maturity, rate, right)
    if market_price < intrinsic * 0.999:
        return f"below_intrinsic_{intrinsic:.4f}"
    discount = math.exp(-rate * maturity)
    r = right.upper()[0]
    upper = discount * forward if r == "C" else discount * strike
    if market_price > upper * 1.001:
        return f"above_upper_bound_{upper:.4f}"
    return None


# ---------------------------------------------------------------------------
# Scalar solver (the readable, testable core)
# ---------------------------------------------------------------------------

def solve_iv(market_price: float, forward: float, strike: float,
             maturity: float, rate: float, right: str,
             contract_key: str = "", snapshot_ts: str = "",
             lower_vol: float = 1e-4, upper_vol: float = 5.0,
             price_tol: float = 1e-6, max_iter: int = 100) -> IvSolveResult:
    """
    Invert the BS price → implied vol using Brent's method.

    Returns IvSolveResult with full diagnostics, even on failure.
    """
    # Pre-solve bounds check
    bounds_issue = check_price_bounds(market_price, forward, strike, maturity, rate, right)
    if bounds_issue:
        return IvSolveResult(
            contract_key=contract_key, snapshot_ts=snapshot_ts,
            market_price=market_price, implied_vol=None,
            converged=False, iterations=0, residual=float("inf"),
            lower_bound=lower_vol, upper_bound=upper_vol,
            failure_reason=bounds_issue,
        )

    if maturity <= 0:
        return IvSolveResult(
            contract_key=contract_key, snapshot_ts=snapshot_ts,
            market_price=market_price, implied_vol=None,
            converged=False, iterations=0, residual=float("inf"),
            lower_bound=lower_vol, upper_bound=upper_vol,
            failure_reason="zero_or_negative_maturity",
        )

    def objective(sigma: float) -> float:
        return bs_price(forward, strike, sigma, maturity, rate, right) - market_price

    # Verify the bracket [lower_vol, upper_vol] straddles zero
    f_lo = objective(lower_vol)
    f_hi = objective(upper_vol)

    if f_lo * f_hi > 0:
        return IvSolveResult(
            contract_key=contract_key, snapshot_ts=snapshot_ts,
            market_price=market_price, implied_vol=None,
            converged=False, iterations=0, residual=min(abs(f_lo), abs(f_hi)),
            lower_bound=lower_vol, upper_bound=upper_vol,
            failure_reason="bracket_does_not_straddle_zero",
        )

    # Brent solve
    try:
        result_obj = brentq(
            objective, lower_vol, upper_vol,
            xtol=price_tol, maxiter=max_iter,
            full_output=True,
        )
        solved_vol = result_obj[0]
        info = result_obj[1]
        iterations = info.iterations
        residual = abs(objective(solved_vol))
        # Convergence jugée RELATIVEMENT au prix : un résidu de 4e-4 sur une option
        # d'indice valant des dizaines d'euros = erreur relative ~1e-6 (vol correcte),
        # mais l'ancien seuil ABSOLU price_tol*100 (=1e-4 en européen) la rejetait à
        # tort — asymétrie avec l'américain (1e-3), d'où ESTX50 à 0.75. Seuil =
        # max(plancher absolu, 0.1% du prix).
        converged = residual < max(price_tol * 100, 1e-3 * abs(market_price))

        return IvSolveResult(
            contract_key=contract_key, snapshot_ts=snapshot_ts,
            market_price=market_price, implied_vol=solved_vol,
            converged=converged, iterations=iterations, residual=residual,
            lower_bound=lower_vol, upper_bound=upper_vol,
            failure_reason=None if converged else "residual_above_tolerance",
        )

    except Exception as exc:
        return IvSolveResult(
            contract_key=contract_key, snapshot_ts=snapshot_ts,
            market_price=market_price, implied_vol=None,
            converged=False, iterations=0, residual=float("inf"),
            lower_bound=lower_vol, upper_bound=upper_vol,
            failure_reason=f"solver_exception:{str(exc)[:80]}",
        )


# ---------------------------------------------------------------------------
# American IV inversion (Step 8 — options à exercice anticipé)
# ---------------------------------------------------------------------------

def solve_iv_american(market_price: float, spot: float, strike: float,
                      maturity: float, rate: float, carry: float, right: str,
                      contract_key: str = "", snapshot_ts: str = "",
                      lower_vol: float = 1e-4, upper_vol: float = 5.0,
                      price_tol: float = 1e-5, max_iter: int = 60,
                      steps: int = 80) -> IvSolveResult:
    """
    Inverse le prix d'une option AMÉRICAINE en volatilité implicite.

    Même squelette bracketé (Brent) que le cas européen, mais la fonction de prix
    est l'arbre binomial CRR (price_american_binomial). On utilise un arbre plus
    léger (steps=80) car le solveur appelle le pricer de nombreuses fois.

    Convention documentée : l'IV américaine est la vol qui égalise le prix CRR au
    prix de marché, sous l'hypothèse de carry continu fourni.
    """
    from src.pricing.american import price_american_binomial

    if maturity <= 0:
        return IvSolveResult(
            contract_key=contract_key, snapshot_ts=snapshot_ts,
            market_price=market_price, implied_vol=None, converged=False,
            iterations=0, residual=float("inf"), lower_bound=lower_vol,
            upper_bound=upper_vol, failure_reason="zero_or_negative_maturity",
            model_name="american_crr", model_version="v1")

    # Borne basse : valeur intrinsèque actualisée. Si le prix est sous l'intrinsèque
    # immédiat, pas de solution.
    r_char = right.upper()[0]
    intrinsic = max(spot - strike, 0.0) if r_char == "C" else max(strike - spot, 0.0)
    if market_price < intrinsic - 1e-6 or market_price <= 0:
        return IvSolveResult(
            contract_key=contract_key, snapshot_ts=snapshot_ts,
            market_price=market_price, implied_vol=None, converged=False,
            iterations=0, residual=float("inf"), lower_bound=lower_vol,
            upper_bound=upper_vol, failure_reason="below_intrinsic_or_non_positive",
            model_name="american_crr", model_version="v1")

    def objective(sigma: float) -> float:
        return price_american_binomial(spot, strike, sigma, maturity, rate,
                                       carry, right, steps).price - market_price

    try:
        f_lo, f_hi = objective(lower_vol), objective(upper_vol)
        if f_lo * f_hi > 0:
            return IvSolveResult(
                contract_key=contract_key, snapshot_ts=snapshot_ts,
                market_price=market_price, implied_vol=None, converged=False,
                iterations=0, residual=min(abs(f_lo), abs(f_hi)),
                lower_bound=lower_vol, upper_bound=upper_vol,
                failure_reason="bracket_does_not_straddle_zero",
                model_name="american_crr", model_version="v1")

        result_obj = brentq(objective, lower_vol, upper_vol,
                            xtol=price_tol, maxiter=max_iter, full_output=True)
        solved = result_obj[0]
        residual = abs(objective(solved))
        # Convergence relative au prix (cf. note dans le solveur européen) : seuil =
        # max(plancher absolu price_tol*100, 0.1% du prix de marché).
        converged = residual < max(price_tol * 100, 1e-3 * abs(market_price))
        return IvSolveResult(
            contract_key=contract_key, snapshot_ts=snapshot_ts,
            market_price=market_price, implied_vol=solved, converged=converged,
            iterations=result_obj[1].iterations, residual=residual,
            lower_bound=lower_vol, upper_bound=upper_vol,
            failure_reason=None if converged else "residual_above_tolerance",
            model_name="american_crr", model_version="v1")
    except Exception as exc:
        return IvSolveResult(
            contract_key=contract_key, snapshot_ts=snapshot_ts,
            market_price=market_price, implied_vol=None, converged=False,
            iterations=0, residual=float("inf"), lower_bound=lower_vol,
            upper_bound=upper_vol, failure_reason=f"solver_exception:{str(exc)[:80]}",
            model_name="american_crr", model_version="v1")


# ---------------------------------------------------------------------------
# Batch solver
# ---------------------------------------------------------------------------

def solve_chain_iv(snapshot_df, forward_results: dict, rate: float,
                   cfg: dict, american: bool = False) -> list[dict]:
    """
    Solve IV for all usable options in a snapshot_df.

    forward_results: dict mapping expiry_str → ForwardResult
    Returns list of dicts (one per solved contract) for Parquet persistence.
    """
    from src.utils.dates import maturity_years
    import datetime

    solver_cfg = cfg.get("iv_solver", {})
    lower_vol = solver_cfg.get("lower_vol", 1e-4)
    upper_vol = solver_cfg.get("upper_vol", 5.0)
    price_tol = solver_cfg.get("price_tolerance", 1e-6)
    max_iter = solver_cfg.get("max_iterations", 100)

    usable = snapshot_df[snapshot_df["is_usable"] == True]
    results = []

    for _, row in usable.iterrows():
        expiry = row["expiry"]
        fwd_result = forward_results.get(expiry)
        if fwd_result is None or math.isnan(fwd_result.chosen_forward):
            continue

        forward = fwd_result.chosen_forward
        T = maturity_years(
            datetime.date.fromisoformat(expiry),
            datetime.date.fromisoformat(row["snapshot_ts"][:10])
        )
        if T <= 0:
            continue

        mid = row.get("mid_price")
        if mid is None or mid <= 0:
            continue

        if american:
            spot = row.get("reference_spot")
            # carry (dividende implicite) dérivé du forward déjà estimé par parité :
            # F = S·exp((r−q)T) ⇒ carry q tel que (r−q) = ln(F/S)/T.
            carry = (rate - math.log(forward / spot) / T
                     if spot and spot > 0 and forward > 0 and T > 0 else rate)
            result = solve_iv_american(
                market_price=mid, spot=spot, strike=row["strike"],
                maturity=T, rate=rate, carry=carry, right=row["right"],
                contract_key=row["instrument_key"], snapshot_ts=row["snapshot_ts"],
                lower_vol=lower_vol, upper_vol=upper_vol,
                price_tol=price_tol, max_iter=max_iter,
            )
        else:
            result = solve_iv(
                market_price=mid, forward=forward,
                strike=row["strike"], maturity=T, rate=rate,
                right=row["right"], contract_key=row["instrument_key"],
                snapshot_ts=row["snapshot_ts"],
                lower_vol=lower_vol, upper_vol=upper_vol,
                price_tol=price_tol, max_iter=max_iter,
            )

        # Eq 6 & 7: log-moneyness and total variance
        log_moneyness = math.log(row["strike"] / forward) if forward > 0 else float("nan")
        total_variance = (result.implied_vol ** 2 * T) if result.implied_vol else float("nan")

        results.append({
            "contract_key": result.contract_key,
            "instrument_key": result.contract_key,   # alias : schéma harmonisé live/replay
            "snapshot_ts": result.snapshot_ts,
            "underlying_symbol": row["underlying_symbol"],
            "expiry": expiry,
            "maturity_years": T,
            "strike": row["strike"],
            "right": row["right"],
            "forward": forward,
            "log_moneyness": log_moneyness,
            "mid_price": mid,
            "implied_vol": result.implied_vol,
            "total_variance": total_variance,
            "converged": result.converged,
            "iterations": result.iterations,
            "residual": result.residual,
            "failure_reason": result.failure_reason,
        })

    return results
