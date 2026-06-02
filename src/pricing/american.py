"""
American option pricer — Binomial Tree (Cox-Ross-Rubinstein) (Step 10).

Eq 12: backward induction at each node.
Used for single-name equity options where early exercise may have value.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class AmericanPricerResult:
    forward: float
    strike: float
    sigma: float
    maturity_years: float
    rate: float
    right: str
    price: float
    steps: int
    pricer_version: str = "binomial_crr_v1"


def price_american_binomial(spot: float, strike: float, sigma: float,
                             maturity: float, rate: float, carry: float,
                             right: str, steps: int = 200) -> AmericanPricerResult:
    """
    CRR binomial tree for American options.

    Eq 12: at each node, price = max(intrinsic, continuation_value)

    Parameters
    ----------
    spot     : current underlying price
    strike   : option strike
    sigma    : annualised volatility (e.g. 0.20 for 20%)
    maturity : time to expiry in years
    rate     : risk-free rate (continuous, annualised)
    carry    : continuous carry / dividend yield
    right    : "C" or "P"
    steps    : number of binomial tree steps
    """
    if maturity <= 0:
        r = right.upper()
        intrinsic = max(spot - strike, 0.0) if r in ("C", "CALL") else max(strike - spot, 0.0)
        fwd = spot  # placeholder, not used at expiry
        return AmericanPricerResult(
            forward=fwd, strike=strike, sigma=sigma, maturity_years=maturity,
            rate=rate, right=right, price=intrinsic, steps=steps,
        )

    dt = maturity / steps
    discount = math.exp(-rate * dt)

    # CRR up/down factors
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u

    # Risk-neutral probability
    p_up = (math.exp((rate - carry) * dt) - d) / (u - d)
    p_up = max(0.0, min(1.0, p_up))   # clamp for numerical safety
    p_dn = 1.0 - p_up

    # Terminal stock prices
    # node [j] at step N corresponds to price = spot * u^j * d^(N-j)
    stock_at_expiry = spot * (u ** np.arange(steps + 1)) * (d ** (steps - np.arange(steps + 1)))

    # Terminal option values (intrinsic)
    r_char = right.upper()[0]
    if r_char == "C":
        values = np.maximum(stock_at_expiry - strike, 0.0)
    else:
        values = np.maximum(strike - stock_at_expiry, 0.0)

    # Backward induction (Eq 12)
    for step in range(steps - 1, -1, -1):
        # Stock prices at this step
        stock_now = spot * (u ** np.arange(step + 1)) * (d ** (step - np.arange(step + 1)))

        # Continuation value
        continuation = discount * (p_up * values[1:step + 2] + p_dn * values[0:step + 1])

        # Intrinsic value at this node
        if r_char == "C":
            intrinsic_now = np.maximum(stock_now - strike, 0.0)
        else:
            intrinsic_now = np.maximum(strike - stock_now, 0.0)

        # American option: take the max of continuation and immediate exercise
        values = np.maximum(continuation, intrinsic_now)

    fwd = spot * math.exp((rate - carry) * maturity)

    return AmericanPricerResult(
        forward=fwd, strike=strike, sigma=sigma, maturity_years=maturity,
        rate=rate, right=right, price=float(values[0]), steps=steps,
    )
