"""
Monte Carlo sous mesure risque-neutre — option ASIATIQUE arithmétique (bonus, version desk).

Pourquoi MC : le payoff asiatique dépend de la MOYENNE du chemin → pas de forme fermée,
l'arbre explose. On simule sous ℚ et on actualise l'espérance.

Équipement « desk » :
  • Variate de contrôle géométrique (Kemna-Vorst discret, généralisé à une courbe
    forward quelconque) — variance ÷ 20-100.
  • Quasi-Monte Carlo (suites de Sobol brouillées) ou pseudo-aléatoire + antithétiques.
  • GREEKS : delta pathwise (exact), gamma par re-scaling des chemins (CRN parfait,
    zéro re-simulation), vega/theta par bump à NOMBRES ALÉATOIRES COMMUNS.
  • Drift calé sur la VRAIE courbe forward de parité (E[S_t] = F(t) point par point),
    pas un carry constant inventé.
  • Strike ladder : tous les strikes repricés sur les MÊMES chemins.

Conventions identiques au reste de l'infra : taux continu annualisé, ACT/365,
vega par 1 pt de vol, theta par jour calendaire.
"""
from __future__ import annotations

import math
import warnings
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np
from scipy.stats import norm, qmc


# ---------------------------------------------------------------------------
# Forme fermée : asiatique GÉOMÉTRIQUE discrète (Kemna-Vorst généralisé)
# ---------------------------------------------------------------------------

def _geo_moments(s0: float, sigma: float, maturity: float, carry: float,
                 n_fixings: int,
                 fixing_forwards: Optional[np.ndarray] = None) -> tuple:
    """(E[ln G], Var[ln G]) pour la moyenne géométrique à n observations équiréparties.

    ln S_{t_i} ~ N(ln F_i − σ²t_i/2, σ²t_i)  où F_i = forward de marché à t_i
    (F_i = s0·e^{b·t_i} si carry constant). La variance de ln G ne dépend que du
    brownien : σ²·T·(n+1)(2n+1)/(6n²)."""
    n = int(n_fixings)
    t = np.linspace(maturity / n, maturity, n)
    if fixing_forwards is not None:
        ln_f = np.log(np.asarray(fixing_forwards, dtype=float))
    else:
        ln_f = math.log(s0) + carry * t
    mu_g = float(np.mean(ln_f - 0.5 * sigma ** 2 * t))
    var_g = sigma ** 2 * maturity * (n + 1) * (2 * n + 1) / (6 * n ** 2)
    return mu_g, var_g


def _geo_black(mu_g: float, var_g: float, strike: float, df: float,
               is_call: bool) -> float:
    """Prix Black d'un payoff sur la lognormale G de moments (mu_g, var_g)."""
    if var_g <= 0:
        g = math.exp(mu_g)
        return df * max(g - strike, 0.0) if is_call else df * max(strike - g, 0.0)
    f_g = math.exp(mu_g + 0.5 * var_g)
    sd = math.sqrt(var_g)
    d1 = (math.log(f_g / strike) + 0.5 * var_g) / sd
    d2 = d1 - sd
    if is_call:
        return df * (f_g * norm.cdf(d1) - strike * norm.cdf(d2))
    return df * (strike * norm.cdf(-d2) - f_g * norm.cdf(-d1))


def geometric_asian_closed_form(s0: float, strike: float, sigma: float,
                                maturity: float, rate: float, carry: float,
                                right: str, n_fixings: int,
                                fixing_forwards: Optional[np.ndarray] = None) -> float:
    """Prix exact de l'asiatique géométrique discrète (carry constant OU courbe forward)."""
    if maturity <= 0 or sigma <= 0 or n_fixings < 1:
        intrinsic = s0 - strike if right.upper().startswith("C") else strike - s0
        return max(intrinsic, 0.0)
    mu_g, var_g = _geo_moments(s0, sigma, maturity, carry, n_fixings, fixing_forwards)
    return _geo_black(mu_g, var_g, strike, math.exp(-rate * maturity),
                      right.upper().startswith("C"))


# ---------------------------------------------------------------------------
# Génération des chemins : pseudo (+antithétiques) ou Sobol brouillé
# ---------------------------------------------------------------------------

def _gaussians(n_paths: int, n_steps: int, seed: Optional[int],
               method: str = "pseudo", antithetic: bool = True) -> np.ndarray:
    if method == "sobol":
        # QMC : les points de Sobol remplissent l'hypercube uniformément → erreur ~1/N
        # (vs 1/√N). Brouillage (Owen) pour garder un estimateur sans biais + IC.
        sampler = qmc.Sobol(d=n_steps, scramble=True, seed=seed)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")          # avertissement n ≠ 2^m
            u = sampler.random(n_paths)
        u = np.clip(u, 1e-12, 1 - 1e-12)
        return norm.ppf(u)
    rng = np.random.default_rng(seed)
    if antithetic:
        half = (n_paths + 1) // 2
        z = rng.standard_normal((half, n_steps))
        return np.vstack([z, -z])[:n_paths]
    return rng.standard_normal((n_paths, n_steps))


def simulate_gbm_paths(s0: float, sigma: float, maturity: float, carry: float,
                       n_paths: int, n_steps: int, seed: Optional[int] = 42,
                       method: str = "pseudo", antithetic: bool = True,
                       forward_curve: Optional[Callable[[np.ndarray], np.ndarray]] = None,
                       ) -> np.ndarray:
    """Chemins GBM sous ℚ. Si `forward_curve(t)→F(t)` est fournie, le drift est calé
    point par point sur la courbe de marché : E[S_{t_i}] = F(t_i) exactement."""
    dt = maturity / n_steps
    times = np.linspace(0.0, maturity, n_steps + 1)
    if forward_curve is not None:
        ln_f = np.log(np.maximum(forward_curve(times), 1e-12))
        drift = np.diff(ln_f) - 0.5 * sigma ** 2 * dt
    else:
        drift = np.full(n_steps, (carry - 0.5 * sigma ** 2) * dt)
    z = _gaussians(n_paths, n_steps, seed, method, antithetic)
    log_paths = np.cumsum(drift[None, :] + sigma * math.sqrt(dt) * z, axis=1)
    paths = np.empty((n_paths, n_steps + 1))
    paths[:, 0] = s0
    paths[:, 1:] = s0 * np.exp(log_paths)
    return paths


# ---------------------------------------------------------------------------
# Pricing MC + greeks + ladder
# ---------------------------------------------------------------------------

@dataclass
class AsianMcResult:
    price: float                 # estimateur AVEC variate de contrôle
    std_error: float
    price_raw: float             # estimateur brut
    std_error_raw: float
    variance_reduction: float
    geo_closed_form: float       # référence exacte
    geo_mc: float
    european_bs: float
    # Greeks (None si compute_greeks=False)
    delta: Optional[float] = None      # pathwise (exact)
    gamma: Optional[float] = None      # re-scaling des chemins (CRN parfait)
    vega: Optional[float] = None       # bump ±1pt, CRN, par 1 pt de vol
    theta: Optional[float] = None      # bump −1 jour, CRN, par jour
    inputs: dict = field(default_factory=dict)
    # matière pour les graphiques :
    sample_paths: np.ndarray = None
    averages: np.ndarray = None
    geos: np.ndarray = None
    terminals: np.ndarray = None
    convergence_n: np.ndarray = None
    convergence_cv: np.ndarray = None
    convergence_raw: np.ndarray = None
    convergence_se: np.ndarray = None
    fixing_times: np.ndarray = None
    fixing_forwards: np.ndarray = None


def _cv_estimate(pay_a: np.ndarray, pay_g: np.ndarray, geo_cf: float) -> tuple:
    """Estimateur à variate de contrôle (beta optimal) → (prix, SE, série ajustée)."""
    var_g = float(pay_g.var(ddof=1))
    beta = float(np.cov(pay_a, pay_g, ddof=1)[0, 1] / var_g) if var_g > 1e-18 else 0.0
    adj = pay_a - beta * (pay_g - geo_cf)
    n = len(adj)
    return float(adj.mean()), float(adj.std(ddof=1) / math.sqrt(n)), adj, beta


def _payoffs(values: np.ndarray, strike: float, df: float, is_call: bool) -> np.ndarray:
    return df * np.maximum(values - strike, 0.0) if is_call \
        else df * np.maximum(strike - values, 0.0)


def price_asian_mc(s0: float, strike: float, sigma: float, maturity: float,
                   rate: float, carry: float, right: str = "C",
                   n_paths: int = 20_000, n_steps: int = 52,
                   seed: Optional[int] = 42, method: str = "pseudo",
                   antithetic: bool = True,
                   forward_curve: Optional[Callable] = None,
                   compute_greeks: bool = False) -> AsianMcResult:
    """Asiatique ARITHMÉTIQUE : MC (+CV géométrique) avec drift constant OU courbe forward."""
    is_call = right.upper().startswith("C")
    df = math.exp(-rate * maturity)
    times = np.linspace(maturity / n_steps, maturity, n_steps)
    fix_fwd = forward_curve(times) if forward_curve is not None else None

    def run(sig: float, T: float, fcurve) -> tuple:
        """Simule avec les MÊMES aléas (seed/method identiques) → CRN pour les bumps."""
        p = simulate_gbm_paths(s0, sig, T, carry, n_paths, n_steps, seed,
                               method, antithetic, fcurve)
        fx = p[:, 1:]
        a = fx.mean(axis=1)
        g = np.exp(np.log(fx).mean(axis=1))
        ts = np.linspace(T / n_steps, T, n_steps)
        ff = fcurve(ts) if fcurve is not None else None
        cf = geometric_asian_closed_form(s0, strike, sig, T, rate, carry,
                                         right, n_steps, ff)
        d = math.exp(-rate * T)
        pa, pg = _payoffs(a, strike, d, is_call), _payoffs(g, strike, d, is_call)
        price, se, adj, _ = _cv_estimate(pa, pg, cf)
        return price, se, adj, p, a, g, pa, pg, cf

    price_cv, se_cv, adjusted, paths, arith, geo, pay_a, pay_g, geo_cf = \
        run(sigma, maturity, forward_curve)

    price_raw = float(pay_a.mean())
    se_raw = float(pay_a.std(ddof=1) / math.sqrt(n_paths))

    from src.pricing.european import bs_price
    fwd_T = float(fix_fwd[-1]) if fix_fwd is not None else s0 * math.exp(carry * maturity)
    euro = bs_price(fwd_T, strike, sigma, maturity, rate, "C" if is_call else "P")

    # ── Greeks desk ────────────────────────────────────────────────────
    delta = gamma = vega = theta = None
    if compute_greeks:
        # Delta PATHWISE : A est proportionnelle à S0 (bump de spot = translation de
        # toute la courbe forward) → ∂payoff/∂S0 = 1{exercice}·(±A/S0), exact et gratuit.
        ind = (arith > strike) if is_call else (arith < strike)
        sign = 1.0 if is_call else -1.0
        delta = float(df * np.mean(np.where(ind, sign * arith / s0, 0.0)))
        # Gamma par RE-SCALING : A(S0·(1±h)) = A·(1±h) sur les mêmes chemins —
        # différence centrale à aléas STRICTEMENT identiques, zéro re-simulation.
        h = 0.01
        up = _payoffs(arith * (1 + h), strike, df, is_call).mean()
        dn = _payoffs(arith * (1 - h), strike, df, is_call).mean()
        mid = _payoffs(arith, strike, df, is_call).mean()
        gamma = float((up - 2 * mid + dn) / (s0 * h) ** 2)
        # Vega : bump ±1 pt de vol, mêmes aléas (CRN), CV des deux côtés.
        dv = 0.01
        p_up = run(sigma + dv, maturity, forward_curve)[0]
        p_dn = run(max(sigma - dv, 1e-4), maturity, forward_curve)[0]
        vega = float((p_up - p_dn) / 2.0)            # déjà « par 1 pt de vol »
        # Theta : bump −1 jour calendaire, mêmes aléas.
        dt_day = 1.0 / 365.0
        if maturity > 2 * dt_day:
            p_short = run(sigma, maturity - dt_day, forward_curve)[0]
            theta = float(p_short - price_cv)         # par jour qui passe
    # ── Convergence ────────────────────────────────────────────────────
    grid = np.unique(np.geomspace(200, n_paths, 200).astype(int))
    cum_cv, cum_raw = np.cumsum(adjusted), np.cumsum(pay_a)
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
        delta=delta, gamma=gamma, vega=vega, theta=theta,
        inputs={"s0": s0, "strike": strike, "sigma": sigma, "maturity": maturity,
                "rate": rate, "carry": carry, "right": right, "method": method,
                "n_paths": n_paths, "n_steps": n_steps},
        sample_paths=paths[: min(120, n_paths)],
        averages=arith, geos=geo, terminals=paths[:, -1],
        convergence_n=grid, convergence_cv=conv_cv,
        convergence_raw=conv_raw, convergence_se=conv_se,
        fixing_times=times, fixing_forwards=fix_fwd,
    )


def strike_ladder(res: AsianMcResult, strikes: List[float]) -> List[dict]:
    """Reprice TOUS les strikes sur les chemins déjà simulés (gratuit) — le réflexe
    desk : on pense en nappe, pas en point. CV recalibrée par strike."""
    out = []
    inp = res.inputs
    is_call = inp["right"].upper().startswith("C")
    df = math.exp(-inp["rate"] * inp["maturity"])
    mu_g, var_g = _geo_moments(inp["s0"], inp["sigma"], inp["maturity"],
                               inp["carry"], inp["n_steps"], res.fixing_forwards)
    from src.pricing.european import bs_price
    fwd_T = (float(res.fixing_forwards[-1]) if res.fixing_forwards is not None
             else inp["s0"] * math.exp(inp["carry"] * inp["maturity"]))
    for k in strikes:
        cf = _geo_black(mu_g, var_g, k, df, is_call)
        pa = _payoffs(res.averages, k, df, is_call)
        pg = _payoffs(res.geos, k, df, is_call)
        price, se, _, _ = _cv_estimate(pa, pg, cf)
        euro = bs_price(fwd_T, k, inp["sigma"], inp["maturity"], inp["rate"],
                        "C" if is_call else "P")
        out.append({"strike": k, "asian_mc": price, "se": se,
                    "geo_cf": cf, "european_bs": float(euro)})
    return out
