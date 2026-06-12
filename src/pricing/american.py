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


def greeks_american(spot: float, strike: float, sigma: float, maturity: float,
                    rate: float, carry: float, right: str,
                    steps: int = 200) -> tuple:
    """
    Greeks d'une option AMÉRICAINE depuis l'arbre binomial CRR.

    delta / gamma / theta sont lus directement sur les nœuds internes de l'arbre
    (méthode standard de Hull) — bien plus stable que des différences finies par
    bump, polluées par le "sawtooth" de discrétisation de l'arbre. vega et rho sont
    obtenus par re-pricing (bump de vol / bump du taux seul, dividende q fixe → le
    forward se réapprécie avec r, convention identique au bs_rho européen).
    Conventions alignées sur Black-76 : vega PAR POINT de vol (×0.01),
    rho PAR POINT de taux (×0.01), theta PAR JOUR calendaire.
    Renvoie (delta, gamma, vega, theta, rho).
    """
    if maturity <= 0 or sigma <= 0 or spot <= 0 or steps < 2:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    N = steps
    dt = maturity / N
    disc = math.exp(-rate * dt)
    u = math.exp(sigma * math.sqrt(dt))
    d = 1.0 / u
    p_up = (math.exp((rate - carry) * dt) - d) / (u - d)
    p_up = max(0.0, min(1.0, p_up))
    p_dn = 1.0 - p_up
    r_char = right.upper()[0]

    j = np.arange(N + 1)
    stock = spot * (u ** j) * (d ** (N - j))
    values = (np.maximum(stock - strike, 0.0) if r_char == "C"
              else np.maximum(strike - stock, 0.0))

    nodes = {}  # step ∈ {0,1,2} -> (values, stock_prices)
    for step in range(N - 1, -1, -1):
        stock_now = spot * (u ** np.arange(step + 1)) * (d ** (step - np.arange(step + 1)))
        cont = disc * (p_up * values[1:step + 2] + p_dn * values[0:step + 1])
        intr = (np.maximum(stock_now - strike, 0.0) if r_char == "C"
                else np.maximum(strike - stock_now, 0.0))
        values = np.maximum(cont, intr)
        if step <= 2:
            nodes[step] = (values.copy(), stock_now.copy())

    (v0, _), (v1, s1), (v2, s2) = nodes[0], nodes[1], nodes[2]
    delta = (v1[1] - v1[0]) / (s1[1] - s1[0])
    h_up = (v2[2] - v2[1]) / (s2[2] - s2[1])
    h_dn = (v2[1] - v2[0]) / (s2[1] - s2[0])
    gamma = (h_up - h_dn) / (0.5 * (s2[2] - s2[0]))
    theta = ((v2[1] - v0[0]) / (2.0 * dt)) / 365.0   # par jour calendaire

    dv = 0.01  # vega par 1 point de vol
    p_vu = price_american_binomial(spot, strike, sigma + dv, maturity, rate, carry, right, N).price
    p_vd = price_american_binomial(spot, strike, sigma - dv, maturity, rate, carry, right, N).price
    vega = (p_vu - p_vd) / 2.0

    # rho par 1 point de taux. ⚠️ Dans ce codebase le paramètre `carry` est le
    # RENDEMENT DE DIVIDENDE q (rate − carry = b dans p_up) : on bumpe donc le
    # taux SEUL, q fixe → b = r − q bouge → le forward se réapprécie avec r,
    # convention identique au bs_rho européen (spot tenu fixe).
    dr = 0.0005
    p_ru = price_american_binomial(spot, strike, sigma, maturity, rate + dr, carry, right, N).price
    p_rd = price_american_binomial(spot, strike, sigma, maturity, rate - dr, carry, right, N).price
    rho = (p_ru - p_rd) / (2.0 * dr) * 0.01

    return float(delta), float(gamma), float(vega), float(theta), float(rho)
