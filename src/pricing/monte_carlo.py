"""
Monte Carlo sous mesure risque-neutre — option ASIATIQUE arithmétique (bonus).

Pourquoi MC ici : le payoff asiatique dépend de la MOYENNE du chemin → pas de forme
fermée en Black-Scholes, l'arbre explose. On simule un GBM sous ℚ (drift = b = r − q,
le carry IMPLICITE extrait de notre forward de parité) et on actualise l'espérance.

Précision : VARIATE DE CONTRÔLE géométrique — la moyenne GÉOMÉTRIQUE d'un GBM est
lognormale ⇒ prix fermé exact (Kemna-Vorst, version discrète). On simule les deux
payoffs sur les MÊMES chemins et on corrige le prix arithmétique par l'erreur connue
du géométrique : variance typiquement réduite d'un facteur 20-100×.

Conventions identiques au reste de l'infra : taux continu annualisé, ACT/365.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
from scipy.stats import norm


# ---------------------------------------------------------------------------
# Forme fermée : asiatique GÉOMÉTRIQUE discrète (Kemna-Vorst généralisé)
# ---------------------------------------------------------------------------

def geometric_asian_closed_form(s0: float, strike: float, sigma: float,
                                maturity: float, rate: float, carry: float,
                                right: str, n_fixings: int) -> float:
    """
    Prix exact de l'asiatique géométrique à n observations équiréparties sur (0, T].

    ln G ~ Normale :  E[ln G] = ln S0 + (b − σ²/2)·T·(n+1)/(2n)
                      Var[ln G] = σ²·T·(n+1)(2n+1)/(6n²)
    puis formule de Black sur la lognormale G.
    """
    n = int(n_fixings)
    if maturity <= 0 or sigma <= 0 or n < 1:
        intrinsic = s0 - strike if right.upper().startswith("C") else strike - s0
        return max(intrinsic, 0.0)
    mu_g = math.log(s0) + (carry - 0.5 * sigma ** 2) * maturity * (n + 1) / (2 * n)
    var_g = sigma ** 2 * maturity * (n + 1) * (2 * n + 1) / (6 * n ** 2)
    f_g = math.exp(mu_g + 0.5 * var_g)          # forward de la moyenne géométrique
    sd = math.sqrt(var_g)
    d1 = (math.log(f_g / strike) + 0.5 * var_g) / sd
    d2 = d1 - sd
    df = math.exp(-rate * maturity)
    if right.upper().startswith("C"):
        return df * (f_g * norm.cdf(d1) - strike * norm.cdf(d2))
    return df * (strike * norm.cdf(-d2) - f_g * norm.cdf(-d1))


# ---------------------------------------------------------------------------
# Simulation GBM + pricing MC
# ---------------------------------------------------------------------------

def simulate_gbm_paths(s0: float, sigma: float, maturity: float, carry: float,
                       n_paths: int, n_steps: int,
                       seed: Optional[int] = 42) -> np.ndarray:
    """Chemins GBM sous ℚ : S_t = S0·exp((b − σ²/2)t + σW_t).
    Retourne un tableau (n_paths, n_steps+1) incluant t=0."""
    rng = np.random.default_rng(seed)
    dt = maturity / n_steps
    z = rng.standard_normal((n_paths, n_steps))
    increments = (carry - 0.5 * sigma ** 2) * dt + sigma * math.sqrt(dt) * z
    log_paths = np.cumsum(increments, axis=1)
    paths = np.empty((n_paths, n_steps + 1))
    paths[:, 0] = s0
    paths[:, 1:] = s0 * np.exp(log_paths)
    return paths


@dataclass
class AsianMcResult:
    price: float                 # estimateur AVEC variate de contrôle
    std_error: float             # erreur-type de l'estimateur CV
    price_raw: float             # estimateur brut (sans CV)
    std_error_raw: float
    variance_reduction: float    # Var(brut)/Var(CV)
    geo_closed_form: float       # référence exacte (Kemna-Vorst discret)
    geo_mc: float                # géométrique estimé par MC (doit ≈ closed form)
    european_bs: float           # vanille Black équivalente (contrôle d'intuition)
    inputs: dict = field(default_factory=dict)
    # matière pour les graphiques :
    sample_paths: np.ndarray = None      # (≤120, n_steps+1)
    averages: np.ndarray = None          # moyenne arithmétique par chemin
    terminals: np.ndarray = None         # S_T par chemin
    convergence_n: np.ndarray = None     # nb de chemins cumulé
    convergence_cv: np.ndarray = None    # estimateur CV cumulé
    convergence_raw: np.ndarray = None   # estimateur brut cumulé
    convergence_se: np.ndarray = None    # erreur-type cumulée (CV)


def price_asian_mc(s0: float, strike: float, sigma: float, maturity: float,
                   rate: float, carry: float, right: str = "C",
                   n_paths: int = 20_000, n_steps: int = 52,
                   seed: Optional[int] = 42) -> AsianMcResult:
    """Asiatique ARITHMÉTIQUE par MC + variate de contrôle géométrique (beta optimal)."""
    paths = simulate_gbm_paths(s0, sigma, maturity, carry, n_paths, n_steps, seed)
    fixings = paths[:, 1:]                          # moyenne sur (0, T], pas t=0
    arith = fixings.mean(axis=1)
    geo = np.exp(np.log(fixings).mean(axis=1))

    df = math.exp(-rate * maturity)
    is_call = right.upper().startswith("C")
    pay_a = df * np.maximum(arith - strike, 0.0) if is_call \
        else df * np.maximum(strike - arith, 0.0)
    pay_g = df * np.maximum(geo - strike, 0.0) if is_call \
        else df * np.maximum(strike - geo, 0.0)

    geo_cf = geometric_asian_closed_form(s0, strike, sigma, maturity, rate, carry,
                                         right, n_steps)

    # Variate de contrôle à beta OPTIMAL : Cov(A,G)/Var(G) — corrige l'estimateur
    # arithmétique par l'erreur OBSERVÉE du géométrique (dont on connaît la vérité).
    var_g = float(pay_g.var(ddof=1))
    beta = float(np.cov(pay_a, pay_g, ddof=1)[0, 1] / var_g) if var_g > 1e-18 else 0.0
    adjusted = pay_a - beta * (pay_g - geo_cf)

    price_raw = float(pay_a.mean())
    se_raw = float(pay_a.std(ddof=1) / math.sqrt(n_paths))
    price_cv = float(adjusted.mean())
    se_cv = float(adjusted.std(ddof=1) / math.sqrt(n_paths))

    # Vanille européenne équivalente (Black sur le forward) — repère d'intuition :
    # l'asiatique vaut MOINS (la moyenne a une vol effective ~σ/√3).
    from src.pricing.european import bs_price
    fwd = s0 * math.exp(carry * maturity)
    euro = bs_price(fwd, strike, sigma, maturity, rate, "C" if is_call else "P")

    # Séries de convergence (200 points log-espacés)
    grid = np.unique(np.geomspace(200, n_paths, 200).astype(int))
    cum_cv = np.cumsum(adjusted)
    cum_raw = np.cumsum(pay_a)
    cum_sq = np.cumsum((adjusted - price_cv) ** 2)
    conv_cv = cum_cv[grid - 1] / grid
    conv_raw = cum_raw[grid - 1] / grid
    conv_se = np.sqrt(np.maximum(cum_sq[grid - 1] / np.maximum(grid - 1, 1), 0)) / np.sqrt(grid)

    return AsianMcResult(
        price=price_cv, std_error=se_cv,
        price_raw=price_raw, std_error_raw=se_raw,
        variance_reduction=(se_raw / se_cv) ** 2 if se_cv > 0 else float("inf"),
        geo_closed_form=geo_cf, geo_mc=float(pay_g.mean()),
        european_bs=float(euro),
        inputs={"s0": s0, "strike": strike, "sigma": sigma, "maturity": maturity,
                "rate": rate, "carry": carry, "right": right,
                "n_paths": n_paths, "n_steps": n_steps, "beta": beta},
        sample_paths=paths[: min(120, n_paths)],
        averages=arith, terminals=paths[:, -1],
        convergence_n=grid, convergence_cv=conv_cv,
        convergence_raw=conv_raw, convergence_se=conv_se,
    )
