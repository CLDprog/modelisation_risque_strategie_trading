"""
Réconciliation des Greeks — greeks publiés vs différences finies (roadmap Step 11).

But : valider la fiabilité des greeks publiés en les recalculant par une méthode
INDÉPENDANTE (re-pricing par bumps) et en mesurant l'écart. Calcul pur, aucun appel
broker. Conventions alignées sur la plateforme : vega par point de vol (×0.01),
theta par jour calendaire.

NB : pour les options AMÉRICAINES, les greeks publiés viennent déjà de l'arbre CRR
(méthode des nœuds). Le gamma par bump y est bruité (sawtooth de discrétisation) →
l'écart gamma américain est attendu et NON significatif. delta et vega sont les
indicateurs de réconciliation fiables ; gamma/theta sont informatifs.
"""
from __future__ import annotations

import math

import pandas as pd

from src.pricing.european import bs_price
from src.pricing.american import price_american_binomial


def _price(american: bool, S: float, K: float, sigma: float, T: float,
           rate: float, carry: float, right: str) -> float:
    """Prix au modèle approprié (européen Black-76 / américain CRR)."""
    if american:
        return price_american_binomial(S, K, sigma, T, rate, carry, right, steps=200).price
    forward = S * math.exp((rate - carry) * T)
    return bs_price(forward, K, sigma, T, rate, right)


def reconcile_chain_greeks(chain_df: pd.DataFrame, rate: float,
                           american: bool = False) -> pd.DataFrame:
    """
    Pour chaque option USABLE (IV + greeks publiés), recalcule delta/gamma/vega/theta
    par différences finies (re-pricing) et renvoie un DataFrame d'écarts absolus
    (colonnes *_pub, *_fd, *_diff par greek).
    """
    rows = []
    for _, r in chain_df.iterrows():
        iv = r.get("implied_vol")
        if iv is None or pd.isna(iv) or iv <= 0:
            continue
        if "is_usable" in r and not bool(r["is_usable"]):
            continue
        S, K = r.get("reference_spot"), r.get("strike")
        T, F = r.get("maturity_years"), r.get("forward")
        right = r.get("right")
        if not all(pd.notna(v) for v in (S, K, T, F)) or S <= 0 or T <= 0:
            continue
        carry = rate - math.log(F / S) / T if F > 0 else rate
        h = S * 0.01

        def P(s=S, sig=iv, t=T):
            return _price(american, s, K, sig, t, rate, carry, right)

        p0 = P()
        delta_fd = (P(s=S + h) - P(s=S - h)) / (2 * h)
        gamma_fd = (P(s=S + h) - 2 * p0 + P(s=S - h)) / (h * h)
        vega_fd = (P(sig=iv + 0.01) - P(sig=iv - 0.01)) / 2.0
        theta_fd = (P(t=T - 1.0 / 365) - p0) if T > 1.0 / 365 else float("nan")

        def diff(stored, fd):
            return abs(float(stored) - fd) if stored is not None and pd.notna(stored) else float("nan")

        rows.append({
            "instrument_key": r.get("instrument_key"),
            "underlying_symbol": r.get("underlying_symbol"),
            "expiry": r.get("expiry"), "strike": K, "right": right,
            "model": "american_crr" if american else "black76",
            "delta_pub": r.get("delta"), "delta_fd": round(delta_fd, 6), "delta_diff": round(diff(r.get("delta"), delta_fd), 6),
            "gamma_pub": r.get("gamma"), "gamma_fd": round(gamma_fd, 6), "gamma_diff": round(diff(r.get("gamma"), gamma_fd), 6),
            "vega_pub": r.get("vega"), "vega_fd": round(vega_fd, 6), "vega_diff": round(diff(r.get("vega"), vega_fd), 6),
            "theta_pub": r.get("theta"), "theta_fd": round(theta_fd, 6), "theta_diff": round(diff(r.get("theta"), theta_fd), 6),
        })
    return pd.DataFrame(rows)


def reconciliation_summary(recon_df: pd.DataFrame) -> dict:
    """Écart médian/max par greek. delta & vega = fiables ; gamma/theta = informatifs."""
    if recon_df.empty:
        return {}
    out = {}
    for g in ("delta", "gamma", "vega", "theta"):
        col = f"{g}_diff"
        if col in recon_df.columns:
            vals = recon_df[col].dropna()
            if len(vals):
                out[g] = {"median": round(float(vals.median()), 6),
                          "max": round(float(vals.max()), 6), "n": int(len(vals))}
    return out
