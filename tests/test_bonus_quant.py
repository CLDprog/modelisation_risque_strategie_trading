"""Tests des bonus quant : variance swap (log-contrat) et simulateur de delta-hedge."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import math
import numpy as np

from src.pricing.varswap import (variance_strike_from_svi, interpolate_variance_index,
                                 VarStrikeResult)
from src.pricing.monte_carlo import delta_hedge_pnl


def _flat_svi(sigma: float, maturity: float) -> dict:
    """Surface PLATE : w(k) = σ²T ⇔ a = σ²T, b = 0."""
    return {"svi_a": sigma ** 2 * maturity, "svi_b": 0.0, "svi_rho": 0.0,
            "svi_m": 0.0, "svi_sigma": 0.1}


# ── Variance swap ──────────────────────────────────────────────────────────

def test_varswap_flat_surface_recovers_sigma():
    # Sans skew, le strike de variance DOIT valoir σ² (le log-contrat n'ajoute rien).
    sigma, T = 0.20, 0.25
    res = variance_strike_from_svi(_flat_svi(sigma, T), forward=6000.0, maturity=T,
                                   rate=0.025)
    assert abs(res.vol_strike - sigma) < 0.002          # ±0.2 pt de vol (discrétisation)
    assert abs(res.convexity_premium) < 0.002


def test_varswap_skew_adds_convexity_premium():
    # Avec un skew négatif (ρ<0, b>0), les puts OTM enrichissent le strip → K_var > σ_ATM.
    sigma, T = 0.18, 0.25
    skewed = {"svi_a": sigma ** 2 * T * 0.7, "svi_b": 0.04, "svi_rho": -0.7,
              "svi_m": 0.0, "svi_sigma": 0.15}
    res = variance_strike_from_svi(skewed, forward=6000.0, maturity=T, rate=0.025)
    assert res.convexity_premium > 0.001                # la prime du skew est positive


def test_variance_index_interpolation():
    a = VarStrikeResult("e1", 20 / 365, 6000, 0.04, 0.20, 0.19, 0.01, 100)
    b = VarStrikeResult("e2", 50 / 365, 6010, 0.0625, 0.25, 0.24, 0.01, 100)
    idx = interpolate_variance_index([a, b], 30)
    # variance totale linéaire entre 20j et 50j → niveau entre 20 et 25 de vol
    assert 20.0 < idx["index"] < 25.0
    assert not idx["clamped"]
    assert interpolate_variance_index([a, b], 10)["clamped"]


# ── Delta-hedge ────────────────────────────────────────────────────────────

S0, K, SIG, T, R, B = 100.0, 100.0, 0.20, 0.5, 0.025, 0.01


def test_hedged_pnl_centered_and_tightens_with_frequency():
    naked = delta_hedge_pnl(S0, K, SIG, T, R, B, "C", n_paths=4000,
                            rebalance_steps=0, seed=11)
    weekly = delta_hedge_pnl(S0, K, SIG, T, R, B, "C", n_paths=4000,
                             rebalance_steps=5, seed=11)
    daily = delta_hedge_pnl(S0, K, SIG, T, R, B, "C", n_paths=4000,
                            rebalance_steps=1, seed=11)
    # Le hedge resserre la distribution : σ(quotidien) < σ(hebdo) < σ(nu)
    assert daily.std() < weekly.std() < naked.std()
    # Vendre AU prix Black et hedger au même σ → P&L centré sur ~0
    assert abs(daily.mean()) < 3 * daily.std() / math.sqrt(len(daily)) + 0.05
    # Ordre de grandeur du resserrement hebdo→quotidien : ~√5
    assert weekly.std() / daily.std() > 1.5


def test_naked_seller_keeps_premium_when_otm():
    # Vente nue d'un call très OTM : la plupart des chemins gardent ~toute la prime > 0
    pnl = delta_hedge_pnl(S0, 140.0, SIG, T, R, B, "C", n_paths=4000,
                          rebalance_steps=0, seed=12)
    assert np.median(pnl) > 0
