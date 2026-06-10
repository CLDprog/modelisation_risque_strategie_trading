"""
Eq. 23 — Variance de panier & diagnostics de dispersion (roadmap, section générique).

    σ_P² = Σ_i w_i² σ_i² + 2 Σ_{i<j} w_i w_j ρ_ij σ_i σ_j

Application à l'univers indice + composantes :
  - variance de panier sous corrélation donnée (Eq. 23 exacte) ;
  - CORRÉLATION IMPLICITE moyenne ρ̄ : on inverse Eq. 23 avec ρ_ij = ρ̄ constant,
    en utilisant l'IV ATM de l'indice comme σ_P et les IV ATM des composantes :

        ρ̄ = (σ_I² − Σ w_i² σ_i²) / ((Σ w_i σ_i)² − Σ w_i² σ_i²)

  - SPREAD DE DISPERSION = Σ w_i σ_i − σ_I  (vol moyenne pondérée − vol de l'indice).

Poids : la pondération free-float officielle STOXX n'est pas disponible via IBKR →
poids ÉGAUX par défaut (diagnostic, limitation documentée), surchargeables par un
champ `weight` dans universe.yaml.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd
from loguru import logger


def basket_variance(weights: Sequence[float], vols: Sequence[float],
                    corr: np.ndarray) -> float:
    """Eq. 23 exacte : variance du panier sous une matrice de corrélation donnée."""
    w = np.asarray(weights, dtype=float)
    s = np.asarray(vols, dtype=float)
    corr = np.asarray(corr, dtype=float)
    cov = corr * np.outer(s, s)
    return float(w @ cov @ w)


def implied_correlation(index_vol: float, weights: Sequence[float],
                        comp_vols: Sequence[float]) -> Optional[float]:
    """Inverse Eq. 23 sous ρ_ij = ρ̄ constant. Retourne None si dénominateur dégénéré."""
    w = np.asarray(weights, dtype=float)
    s = np.asarray(comp_vols, dtype=float)
    sum_w2s2 = float(np.sum((w * s) ** 2))
    avg_vol  = float(np.sum(w * s))
    denom = avg_vol ** 2 - sum_w2s2          # = Σ_{i≠j} w_i w_j σ_i σ_j
    if denom <= 1e-12:
        return None
    return (index_vol ** 2 - sum_w2s2) / denom


def _atm_iv_by_tenor(df: pd.DataFrame, tenors_days: Sequence[int],
                     tol: float = 0.45) -> Dict[int, float]:
    """IV ATM par tenor cible pour UN sous-jacent : échéance listée la plus proche
    (tolérance relative `tol`), puis strike au |log-moneyness| minimal."""
    out: Dict[int, float] = {}
    d = df
    if "is_usable" in d.columns:
        d = d[d["is_usable"].astype(bool)]
    if "converged" in d.columns:
        d = d[d["converged"].astype(bool)]
    d = d.dropna(subset=["implied_vol", "log_moneyness", "days_to_expiry"])
    if d.empty:
        return out
    for tenor in tenors_days:
        days = d["days_to_expiry"].unique()
        nearest = min(days, key=lambda x: abs(x - tenor))
        if abs(nearest - tenor) > tol * tenor:
            continue                      # pas d'échéance proche → indisponible (spec §3)
        sl = d[d["days_to_expiry"] == nearest]
        calls = sl[sl["right"] == "C"]
        sl = calls if not calls.empty else sl
        row = sl.loc[sl["log_moneyness"].abs().idxmin()]
        out[int(tenor)] = float(row["implied_vol"])
    return out


def dispersion_diagnostics(iv_points: pd.DataFrame, index_symbol: str,
                           tenors_days: Sequence[int],
                           weights: Optional[Dict[str, float]] = None,
                           snapshot_ts: str = "") -> pd.DataFrame:
    """
    Table `dispersion_diagnostics` : une ligne par tenor cible —
    IV indice, IV moyenne pondérée des composantes, corrélation implicite (Eq. 23
    inversée), spread de dispersion, nb de composantes utilisées.
    """
    if iv_points is None or iv_points.empty or "underlying_symbol" not in iv_points.columns:
        return pd.DataFrame()

    idx_df = iv_points[iv_points["underlying_symbol"] == index_symbol]
    if idx_df.empty:
        return pd.DataFrame()
    idx_iv = _atm_iv_by_tenor(idx_df, tenors_days)

    comp_ivs: Dict[str, Dict[int, float]] = {}
    for sym, grp in iv_points.groupby("underlying_symbol"):
        if sym == index_symbol:
            continue
        ivs = _atm_iv_by_tenor(grp, tenors_days)
        if ivs:
            comp_ivs[sym] = ivs

    rows: List[dict] = []
    for tenor in tenors_days:
        if tenor not in idx_iv:
            continue
        syms = [s for s, ivs in comp_ivs.items() if tenor in ivs]
        if len(syms) < 5:                 # trop peu de composantes → diagnostic non fiable
            continue
        vols = np.array([comp_ivs[s][tenor] for s in syms])
        if weights:
            w = np.array([weights.get(s, 0.0) for s in syms], dtype=float)
            if w.sum() <= 0:
                w = np.ones(len(syms))
            weighting = "configured"
        else:
            w = np.ones(len(syms))
            weighting = "equal"
        w = w / w.sum()

        sigma_i  = idx_iv[tenor]
        avg_vol  = float(np.sum(w * vols))
        rho      = implied_correlation(sigma_i, w, vols)
        rows.append({
            "snapshot_ts": snapshot_ts,
            "index_symbol": index_symbol,
            "tenor_days": int(tenor),
            "maturity_years": round(tenor / 365.0, 4),
            "index_iv": sigma_i,
            "basket_avg_iv": avg_vol,
            "dispersion_spread": avg_vol - sigma_i,
            "implied_correlation": rho,
            "n_components": len(syms),
            "weighting": weighting,
        })
        if rho is not None and not (-0.5 <= rho <= 1.5):
            logger.warning(f"Dispersion {tenor}j : corrélation implicite hors bornes ({rho:.2f})")
    return pd.DataFrame(rows)
