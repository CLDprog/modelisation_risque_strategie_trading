"""Validation de src/risk/strategy_risk.py CONTRE l'outil de référence du professeur
(« Analyseur de risques en finance quantitative », T. Hossen) — chiffres exacts relevés
dans l'outil le 11/06/2026 avec μ=10 %, σ=20 % annuels."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import math
import numpy as np

from src.risk.strategy_risk import (portfolio_moments, symmetrize_correlation,
                                    horizon_stats, prob_run_at_least,
                                    sharpe_confidence_interval)

MU_A, SIGMA_A = 0.10, 0.20      # portefeuille de référence de l'outil (Sharpe 0.5)


# ── Onglet « Mensuel » : sorties EXACTES de l'outil ─────────────────────────

def test_monthly_matches_reference_tool():
    m = horizon_stats(MU_A, SIGMA_A, 12)
    assert round(m["mu_h"] * 100, 3) == 0.833            # espérance mensuelle 0.833 %
    assert round(m["sigma_h"] * 100, 3) == 5.774         # volatilité mensuelle 5.774 %
    assert round(m["p_red"] * 100, 2) == 44.26           # P(mois négatif) 44.26 %
    assert round(m["p_at_least_one"] * 100, 2) == 99.91  # ≥1 mois rouge/an 99.91 %
    assert round(m["mean_reds"], 3) == 5.311             # nb moyen de mois rouges
    assert round(m["var_reds"], 3) == 2.960              # variance 2.960
    assert round(m["std_reds"], 3) == 1.721              # écart-type 1.721
    assert round(m["p_zero_red"] * 100, 2) == 0.09       # P(0 mois rouge) 0.09 %


def test_monthly_pmf_matches_reference_table():
    m = horizon_stats(MU_A, SIGMA_A, 12)
    expected = {1: 0.86, 2: 3.74, 3: 9.91, 4: 17.70, 5: 22.49, 6: 20.83,
                7: 14.18, 8: 7.04, 9: 2.48, 10: 0.59, 11: 0.09, 12: 0.01}
    for k, pct in expected.items():
        assert round(m["pmf"][k] * 100, 2) == pct, f"pmf({k})"


def test_consecutive_runs_match_reference_table():
    m = horizon_stats(MU_A, SIGMA_A, 12)
    runs = dict(m["runs"])
    expected = {1: 99.91, 2: 84.28, 3: 46.22, 4: 20.35, 5: 8.29,
                6: 3.27, 7: 1.26, 8: 0.48, 9: 0.17, 10: 0.06}
    for x, pct in expected.items():
        assert round(runs[x] * 100, 2) == pct, f"run({x})"


def test_run_probability_edge_cases():
    assert prob_run_at_least(12, 0.4426, 13) == 0.0      # série plus longue que l'année
    assert prob_run_at_least(12, 1.0, 12) == 1.0         # tout rouge → série certaine
    assert prob_run_at_least(12, 0.0, 1) == 0.0          # jamais rouge


# ── Onglet « Intervalle » : IC du Sharpe (méthode delta, Lo 2002) ──────────

def test_sharpe_ci_matches_reference_tool():
    r = sharpe_confidence_interval(1.5, 252, 0.95)
    assert round(r["lo"], 3) == -0.464                   # [−0.464 ; 3.464] exact
    assert round(r["hi"], 3) == 3.464
    assert not r["significant"]                          # 1 an ne suffit PAS à 95 %


def test_sharpe_significance_horizon():
    r = sharpe_confidence_interval(1.5, 252, 0.95)
    t = r["t_days_for_significance"]
    assert 400 < t < 460                                 # ~1.7 an de track record requis
    assert sharpe_confidence_interval(1.5, t, 0.95)["significant"]
    assert not sharpe_confidence_interval(1.5, t - 30, 0.95)["significant"]


# ── Onglet « Stratégies » : agrégation de portefeuille ─────────────────────

def test_portfolio_moments_two_strategies():
    corr = np.array([[1.0, 0.0], [0.0, 1.0]])
    mu, sig = portfolio_moments([0.10, 0.10], [0.20, 0.20], [0.5, 0.5], corr)
    assert math.isclose(mu, 0.10, rel_tol=1e-12)
    assert math.isclose(sig, 0.20 / math.sqrt(2), rel_tol=1e-12)   # diversification ρ=0
    _, sig1 = portfolio_moments([0.10, 0.10], [0.20, 0.20], [0.5, 0.5],
                                np.array([[1.0, 1.0], [1.0, 1.0]]))
    assert math.isclose(sig1, 0.20, rel_tol=1e-12)                 # ρ=1 → pas de gain


def test_symmetrize_correlation_flags_non_psd():
    good, ok, _ = symmetrize_correlation(np.array([[1.0, 0.5], [0.3, 1.0]]))
    assert ok and math.isclose(good[0, 1], 0.4)          # moyenne (0.5+0.3)/2, symétrique
    # Triangle impossible : ρ12=ρ13=0.9 mais ρ23=−0.9 → non PSD
    bad = np.array([[1, 0.9, 0.9], [0.9, 1, -0.9], [0.9, -0.9, 1]], dtype=float)
    _, ok_bad, eig = symmetrize_correlation(bad)
    assert not ok_bad and eig < 0
