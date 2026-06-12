"""
European option pricer — Black-Scholes / Black-76 (Step 10).

Equations from the roadmap:
  Eq 8.  d1 = (ln(F/K) + 0.5*sigma^2*T) / (sigma*sqrt(T))
  Eq 9.  d2 = d1 - sigma*sqrt(T)
  Eq 10. Call = e^(-r*T) * [F*N(d1) - K*N(d2)]
  Eq 11. Put  = e^(-r*T) * [K*N(-d2) - F*N(-d1)]
  Eq 13-18. Greeks: delta, gamma, vega, theta, dollar_gamma, dollar_vega
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional
from scipy.stats import norm


# ---------------------------------------------------------------------------
# Black-Scholes core functions
# ---------------------------------------------------------------------------

def d1(forward: float, strike: float, sigma: float, maturity: float) -> float:
    """Eq 8."""
    if sigma <= 0 or maturity <= 0:
        return float("nan")
    return (math.log(forward / strike) + 0.5 * sigma ** 2 * maturity) / (sigma * math.sqrt(maturity))


def d2(forward: float, strike: float, sigma: float, maturity: float) -> float:
    """Eq 9."""
    return d1(forward, strike, sigma, maturity) - sigma * math.sqrt(maturity)


def bs_call(forward: float, strike: float, sigma: float,
            maturity: float, rate: float) -> float:
    """Eq 10: European call price via Black-76."""
    if maturity <= 0:
        return max(forward - strike, 0.0)
    _d1 = d1(forward, strike, sigma, maturity)
    _d2 = d2(forward, strike, sigma, maturity)
    discount = math.exp(-rate * maturity)
    return discount * (forward * norm.cdf(_d1) - strike * norm.cdf(_d2))


def bs_put(forward: float, strike: float, sigma: float,
           maturity: float, rate: float) -> float:
    """Eq 11: European put price via Black-76."""
    if maturity <= 0:
        return max(strike - forward, 0.0)
    _d1 = d1(forward, strike, sigma, maturity)
    _d2 = d2(forward, strike, sigma, maturity)
    discount = math.exp(-rate * maturity)
    return discount * (strike * norm.cdf(-_d2) - forward * norm.cdf(-_d1))


def bs_price(forward: float, strike: float, sigma: float,
             maturity: float, rate: float, right: str) -> float:
    """Dispatch to call or put pricer."""
    r = right.upper()
    if r in ("C", "CALL"):
        return bs_call(forward, strike, sigma, maturity, rate)
    if r in ("P", "PUT"):
        return bs_put(forward, strike, sigma, maturity, rate)
    raise ValueError(f"Unknown option right: {right}")


# ---------------------------------------------------------------------------
# Analytical Greeks  (Eqs 13–18)
# ---------------------------------------------------------------------------

def bs_delta(forward: float, strike: float, sigma: float,
             maturity: float, rate: float, right: str, spot: float) -> float:
    """Eq 13: delta = dPrice/dSpot (via chain rule through forward)."""
    if maturity <= 0 or sigma <= 0:
        r = right.upper()
        if r in ("C", "CALL"):
            return 1.0 if spot > strike else 0.0
        return -1.0 if spot < strike else 0.0
    _d1 = d1(forward, strike, sigma, maturity)
    discount = math.exp(-rate * maturity)
    r = right.upper()
    if r in ("C", "CALL"):
        return discount * norm.cdf(_d1)
    return -discount * norm.cdf(-_d1)


def bs_gamma(forward: float, strike: float, sigma: float,
             maturity: float, rate: float, spot: float) -> float:
    """Eq 14: gamma = d²Price/dSpot² (same for call and put)."""
    if maturity <= 0 or sigma <= 0:
        return 0.0
    _d1 = d1(forward, strike, sigma, maturity)
    discount = math.exp(-rate * maturity)
    return discount * norm.pdf(_d1) / (forward * sigma * math.sqrt(maturity))


def bs_vega(forward: float, strike: float, sigma: float,
            maturity: float, rate: float) -> float:
    """Eq 15: vega = dPrice/d(sigma), expressed per 1% vol point (0.01)."""
    if maturity <= 0 or sigma <= 0:
        return 0.0
    _d1 = d1(forward, strike, sigma, maturity)
    discount = math.exp(-rate * maturity)
    # multiply by 0.01 so vega is "per 1 vol point"
    return discount * forward * norm.pdf(_d1) * math.sqrt(maturity) * 0.01


def bs_theta(forward: float, strike: float, sigma: float,
             maturity: float, rate: float, right: str) -> float:
    """Eq 16: theta = dPrice/dT, expressed per calendar day."""
    if maturity <= 0 or sigma <= 0:
        return 0.0
    _d1 = d1(forward, strike, sigma, maturity)
    _d2 = d2(forward, strike, sigma, maturity)
    discount = math.exp(-rate * maturity)
    r_char = right.upper()[0]

    term1 = -forward * norm.pdf(_d1) * sigma * discount / (2 * math.sqrt(maturity))
    if r_char == "C":
        term2 = -rate * strike * discount * norm.cdf(_d2)
        term3 = rate * forward * discount * norm.cdf(_d1)
    else:
        term2 = rate * strike * discount * norm.cdf(-_d2)
        term3 = -rate * forward * discount * norm.cdf(-_d1)

    # Convert from per year to per calendar day
    return (term1 + term2 + term3) / 365.0


def bs_rho(forward: float, strike: float, sigma: float,
           maturity: float, rate: float, right: str) -> float:
    """rho = dPrice/d(rate), exprimé PAR POINT de taux (×0.01, même convention que
    le vega par point de vol). Spot tenu FIXE (convention BSM : le forward
    F = S·e^{(r−q)T} se réapprécie avec r) :
        call : ρ = K·T·e^{−rT}·N(d2)·0.01   ·   put : ρ = −K·T·e^{−rT}·N(−d2)·0.01
    Un call s'apprécie quand les taux montent (forward plus haut, strike plus
    actualisé) ; un put se déprécie. Relation de parité : ρ_C − ρ_P = K·T·e^{−rT}·0.01."""
    if maturity <= 0 or sigma <= 0:
        return 0.0
    _d2 = d2(forward, strike, sigma, maturity)
    discount = math.exp(-rate * maturity)
    if right.upper()[0] == "C":
        return strike * maturity * discount * norm.cdf(_d2) * 0.01
    return -strike * maturity * discount * norm.cdf(-_d2) * 0.01


def dollar_gamma(gamma: float, spot: float, multiplier: int = 100) -> float:
    """Eq 17: $/gamma = gamma * spot^2 * multiplier / 100"""
    return gamma * spot ** 2 * multiplier / 100.0


def dollar_vega(vega: float, multiplier: int = 100) -> float:
    """Eq 18: dollar vega = vega * multiplier"""
    return vega * multiplier


# ---------------------------------------------------------------------------
# Typed result object
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EuropeanPricerResult:
    forward: float
    strike: float
    sigma: float
    maturity_years: float
    rate: float
    right: str
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    dollar_gamma: float
    dollar_vega: float
    spot: float
    multiplier: int = 100
    rho: float = 0.0
    pricer_version: str = "bs_v1"


def price_european(forward: float, strike: float, sigma: float,
                   maturity: float, rate: float, right: str,
                   spot: float, multiplier: int = 100) -> EuropeanPricerResult:
    """
    Full European pricing with Greeks in one call.
    Returns a typed, immutable result object.
    """
    price = bs_price(forward, strike, sigma, maturity, rate, right)
    delta = bs_delta(forward, strike, sigma, maturity, rate, right, spot)
    gamma = bs_gamma(forward, strike, sigma, maturity, rate, spot)
    vega = bs_vega(forward, strike, sigma, maturity, rate)
    theta = bs_theta(forward, strike, sigma, maturity, rate, right)
    rho = bs_rho(forward, strike, sigma, maturity, rate, right)
    dgamma = dollar_gamma(gamma, spot, multiplier)
    dvega = dollar_vega(vega, multiplier)

    return EuropeanPricerResult(
        forward=forward, strike=strike, sigma=sigma,
        maturity_years=maturity, rate=rate, right=right,
        price=price, delta=delta, gamma=gamma,
        vega=vega, theta=theta, rho=rho,
        dollar_gamma=dgamma, dollar_vega=dvega,
        spot=spot, multiplier=multiplier,
    )
