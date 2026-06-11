"""Tests du moteur Monte Carlo asiatique (bonus) — cohérence avec les formes fermées."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import math
import numpy as np

from src.pricing.monte_carlo import (simulate_gbm_paths, price_asian_mc,
                                     geometric_asian_closed_form)
from src.pricing.european import bs_price

S0, K, SIGMA, T, R, B = 100.0, 100.0, 0.25, 1.0, 0.025, 0.015


def test_gbm_terminal_mean_matches_forward():
    paths = simulate_gbm_paths(S0, SIGMA, T, B, 50_000, 52, seed=1)
    fwd_mc = paths[:, -1].mean()
    fwd_th = S0 * math.exp(B * T)
    se = paths[:, -1].std(ddof=1) / math.sqrt(len(paths))
    assert abs(fwd_mc - fwd_th) < 4 * se          # martingale sous Q (drift b)


def test_geometric_mc_matches_kemna_vorst():
    res = price_asian_mc(S0, K, SIGMA, T, R, B, "C", n_paths=60_000, n_steps=52, seed=2)
    se_geo = res.std_error_raw                     # ordre de grandeur comparable
    assert abs(res.geo_mc - res.geo_closed_form) < 4 * se_geo


def test_asian_below_european_and_positive():
    res = price_asian_mc(S0, K, SIGMA, T, R, B, "C", n_paths=30_000, n_steps=52, seed=3)
    # La moyenne a une vol effective ~sigma/sqrt(3) → l'asiatique vaut MOINS que la vanille
    assert 0 < res.price < res.european_bs
    # et PLUS que la géométrique (moyenne arithmétique ≥ géométrique)
    assert res.price > res.geo_closed_form


def test_control_variate_reduces_variance():
    res = price_asian_mc(S0, K, SIGMA, T, R, B, "C", n_paths=30_000, n_steps=52, seed=4)
    assert res.std_error < res.std_error_raw / 3   # réduction massive attendue (>10x typ.)
    assert res.variance_reduction > 10


def test_put_call_consistency():
    call = price_asian_mc(S0, K, SIGMA, T, R, B, "C", n_paths=40_000, n_steps=52, seed=5)
    put = price_asian_mc(S0, K, SIGMA, T, R, B, "P", n_paths=40_000, n_steps=52, seed=5)
    # Parité asiatique : C − P = df·(E[A] − K), E[A] = S0·(1/n)Σe^{b·t_i}
    n = 52
    ts = np.linspace(T / n, T, n)
    ea = S0 * np.mean(np.exp(B * ts))
    rhs = math.exp(-R * T) * (ea - K)
    assert abs((call.price - put.price) - rhs) < 4 * (call.std_error + put.std_error) + 0.02


def test_degenerate_maturity_returns_intrinsic():
    assert geometric_asian_closed_form(110, 100, 0.2, 0.0, 0.02, 0.0, "C", 12) == 10.0
