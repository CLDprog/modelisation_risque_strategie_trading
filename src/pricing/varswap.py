"""
Variance swap & mini-VSTOXX — réplication MODEL-FREE par le log-contrat (bonus).

Théorie : la variance réalisée se réplique par un strip statique d'options OTM
(Carr-Madan / Demeterfi et al. 1999). Méthodologie VIX/VSTOXX (version discrète) :

    σ²_var = (2·e^{rT}/T) · Σ_i (ΔK_i / K_i²) · Q(K_i)  −  (1/T)·(F/K₀ − 1)²

où Q(K) = prix de l'option OTM au strike K (put si K<F, call si K>F), K₀ = premier
strike ≤ F. AUCUN modèle de dynamique n'est supposé — seulement l'absence d'arbitrage.

Notre apport : la grille collectée n'a que ~5 strikes/maturité → on DENSIFIE le strip
avec la surface SVI calibrée (table surface_parameters), puis on intègre. L'indice
30 jours s'obtient par interpolation linéaire en VARIANCE TOTALE entre les deux
maturités encadrantes (standard VIX), et VSTOXX_maison = 100·σ_30j.

Le spread K_var − σ_ATM est la PRIME DE CONVEXITÉ : le varswap « achète » tout le
skew (les ailes pèsent en 1/K²), il vaut donc toujours plus que la vol ATM.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from src.pricing.european import bs_price


def svi_total_variance(k: np.ndarray, a: float, b: float, rho: float,
                       m: float, sigma: float) -> np.ndarray:
    """w(k) = a + b·[ρ(k−m) + √((k−m)² + σ²)] — Eq.20 (mêmes params que calibration.py)."""
    return a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + sigma ** 2))


@dataclass
class VarStrikeResult:
    expiry: str
    maturity_years: float
    forward: float
    var_strike: float            # σ²_var (variance annualisée)
    vol_strike: float            # √σ²_var (en fraction, ex. 0.19)
    atm_vol: float               # σ_ATM de la tranche (repère)
    convexity_premium: float     # vol_strike − atm_vol (pts de vol, ≥ 0 si skew)
    n_strikes: int
    strikes: np.ndarray = field(default=None, repr=False)
    contributions: np.ndarray = field(default=None, repr=False)   # ΔK/K²·Q(K)·2e^rT/T


def variance_strike_from_svi(svi_params: dict, forward: float, maturity: float,
                             rate: float, expiry: str = "",
                             k_lo: float = -1.2, k_hi: float = 0.7,
                             n_strikes: int = 400) -> Optional[VarStrikeResult]:
    """
    Strike de variance par strip dense d'options OTM pricées sur la tranche SVI.

    k_lo/k_hi : bornes du strip en log-moneyness (~±5σ√T en pratique ; les ailes
    au-delà contribuent en e^{-k²/2}/K² → négligeables). n_strikes : pas du strip.
    """
    if maturity <= 0 or forward <= 0:
        return None
    a, b = svi_params.get("svi_a"), svi_params.get("svi_b")
    rho, m, sg = svi_params.get("svi_rho"), svi_params.get("svi_m"), svi_params.get("svi_sigma")
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in (a, b, rho, m, sg)):
        return None

    # Strip dense en log-moneyness, borné à ±~5 écarts-types autour du forward.
    w_atm = float(svi_total_variance(np.array([0.0]), a, b, rho, m, sg)[0])
    atm_vol = math.sqrt(max(w_atm, 1e-10) / maturity)
    span = 5.0 * atm_vol * math.sqrt(maturity)
    lo, hi = max(k_lo, -span), min(k_hi, span)
    k = np.linspace(lo, hi, n_strikes)
    strikes = forward * np.exp(k)

    w = np.maximum(svi_total_variance(k, a, b, rho, m, sg), 1e-10)
    iv = np.sqrt(w / maturity)

    # Prix OTM : put si K < F, call sinon (continuité en K = F par parité).
    otm = np.array([
        bs_price(forward, float(K), float(s), maturity, rate, "P" if K < forward else "C")
        for K, s in zip(strikes, iv)
    ])

    # Formule VIX discrète : ΔK centré, K0 = premier strike ≤ F.
    dk = np.empty_like(strikes)
    dk[1:-1] = (strikes[2:] - strikes[:-2]) / 2.0
    dk[0] = strikes[1] - strikes[0]
    dk[-1] = strikes[-1] - strikes[-2]
    contrib = (2.0 * math.exp(rate * maturity) / maturity) * dk / strikes ** 2 * otm

    k0_idx = int(np.searchsorted(strikes, forward) - 1)
    k0 = strikes[max(k0_idx, 0)]
    var_strike = float(contrib.sum() - (forward / k0 - 1.0) ** 2 / maturity)
    var_strike = max(var_strike, 1e-10)

    return VarStrikeResult(
        expiry=expiry, maturity_years=maturity, forward=forward,
        var_strike=var_strike, vol_strike=math.sqrt(var_strike),
        atm_vol=atm_vol, convexity_premium=math.sqrt(var_strike) - atm_vol,
        n_strikes=len(strikes), strikes=strikes, contributions=contrib,
    )


def variance_term_structure(params_df, forward_df, rate: float) -> List[VarStrikeResult]:
    """Strike de variance pour CHAQUE tranche calibrée d'un sous-jacent.
    `params_df` = surface_parameters du symbole ; `forward_df` = forward_curve."""
    out = []
    if params_df is None or params_df.empty:
        return out
    fwd_map = {}
    if forward_df is not None and not forward_df.empty:
        for _, r in forward_df.iterrows():
            if r.get("chosen_forward") and not math.isnan(r.get("chosen_forward", float("nan"))):
                fwd_map[str(r.get("expiry"))] = float(r["chosen_forward"])
    for _, p in params_df.iterrows():
        T = float(p.get("maturity_years", 0) or 0)
        expiry = str(p.get("expiry", ""))
        fwd = fwd_map.get(expiry)
        if fwd is None or T <= 0:
            continue
        res = variance_strike_from_svi(p.to_dict(), fwd, T, rate, expiry)
        if res is not None:
            out.append(res)
    return sorted(out, key=lambda r: r.maturity_years)


def interpolate_variance_index(term: List[VarStrikeResult],
                               target_days: int = 30) -> Optional[dict]:
    """
    Indice de variance à horizon EXACT (30 j) — interpolation linéaire en VARIANCE
    TOTALE σ²·T entre les deux maturités encadrantes (méthodologie VIX/VSTOXX).
    Retourne {'index': 100·σ_30j, 'var': σ², 't1', 't2', 'clamped'}.
    """
    if not term:
        return None
    t_target = target_days / 365.0
    pts = [(r.maturity_years, r.var_strike * r.maturity_years, r.expiry) for r in term]
    if t_target <= pts[0][0]:
        w = pts[0][1] * t_target / pts[0][0]           # clamp court (var rate constante)
        t1 = t2 = pts[0][2]
        clamped = True
    elif t_target >= pts[-1][0]:
        w = pts[-1][1] * t_target / pts[-1][0]
        t1 = t2 = pts[-1][2]
        clamped = True
    else:
        i = next(i for i in range(len(pts) - 1) if pts[i][0] <= t_target <= pts[i + 1][0])
        (ta, wa, e1), (tb, wb, e2) = pts[i], pts[i + 1]
        lam = (tb - t_target) / (tb - ta)
        w = lam * wa + (1 - lam) * wb
        t1, t2, clamped = e1, e2, False
    var_30 = w / t_target
    return {"index": 100.0 * math.sqrt(max(var_30, 1e-10)), "var": var_30,
            "t1": t1, "t2": t2, "clamped": clamped}
