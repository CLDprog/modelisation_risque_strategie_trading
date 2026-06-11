"""
Analyse de risque de stratégies — réplique de l'« Analyseur de risques en finance
quantitative » (T. Hossen) , validée au centième près contre l'outil de référence.

Quatre blocs :
  1. Agrégation de portefeuille : μ_p = Σ wᵢμᵢ, σ_p² = ΣΣ wᵢwⱼρᵢⱼσᵢσⱼ (réutilise
     basket_variance d'Eq.23 — la structure de covariance est CONSERVÉE au changement
     d'horizon, on ne mensualise pas chaque stratégie isolément).
  2. Changement d'horizon (mensuel n=12, journalier n=252) sous hypothèse i.i.d.
     gaussienne : μ_h = μ_a/n, σ_h = σ_a/√n, p_rouge = Φ(−μ_h/σ_h).
  3. Loi du nombre de périodes rouges : binomiale B(n, p) (+ pmf complète) et
     probabilité EXACTE d'au moins une série de X rouges consécutifs (récurrence
     dynamique — pas l'approximation p^X).
  4. Intervalle de confiance asymptotique du Sharpe annualisé (méthode delta, Lo 2002) :
     Var(ŝ_d) ≈ (1 + ŝ_d²/2)/T  →  SE(Ŝ) = √[(1 + Ŝ²/(2A))·A/T].

Validation (tests) : μ=10 %, σ=20 % annuels → p_rouge mensuel 44.26 %, E[rouges]=5.311,
Var=2.960, P(0)=0.09 %, P(X=5)=22.49 %, P(série≥2)=84.28 % ; IC Sharpe (1.5, 252, 95 %)
= [−0.464 ; 3.464] — les sorties exactes de l'outil de référence.
"""
from __future__ import annotations

import math
from typing import List, Optional, Sequence

import numpy as np
from scipy.stats import binom, norm

from src.risk.dispersion import basket_variance


# ---------------------------------------------------------------------------
# 1. Portefeuille de stratégies
# ---------------------------------------------------------------------------

def portfolio_moments(mus: Sequence[float], sigmas: Sequence[float],
                      weights: Sequence[float],
                      corr: np.ndarray) -> tuple:
    """(μ_p, σ_p) annuels du portefeuille pondéré. corr = matrice de corrélation."""
    w = np.asarray(weights, dtype=float)
    mu_p = float(np.sum(w * np.asarray(mus, dtype=float)))
    var_p = basket_variance(w, sigmas, corr)
    return mu_p, math.sqrt(max(var_p, 0.0))


def symmetrize_correlation(corr: np.ndarray) -> tuple:
    """Symétrise (moyenne C, Cᵀ), force la diagonale à 1, et vérifie la
    semi-définie-positivité. Retourne (corr_corrigée, psd_ok, λ_min)."""
    c = np.asarray(corr, dtype=float)
    c = (c + c.T) / 2.0
    np.fill_diagonal(c, 1.0)
    c = np.clip(c, -1.0, 1.0)
    eig_min = float(np.linalg.eigvalsh(c).min())
    return c, eig_min >= -1e-10, eig_min


# ---------------------------------------------------------------------------
# 2 & 3. Changement d'horizon + lois des périodes rouges
# ---------------------------------------------------------------------------

def prob_run_at_least(n_periods: int, p: float, run_len: int) -> float:
    """P(au moins une série de `run_len` périodes rouges CONSÉCUTIVES sur n) —
    calcul EXACT par récurrence dynamique (états = longueur de la série en cours),
    sous indépendance des périodes."""
    if run_len <= 0:
        return 1.0
    if run_len > n_periods:
        return 0.0
    # f[j] = P(pas encore de série ≥ run_len, série courante de longueur j)
    f = np.zeros(run_len)
    f[0] = 1.0
    for _ in range(n_periods):
        g = np.zeros(run_len)
        g[0] = f.sum() * (1.0 - p)          # période verte → série remise à zéro
        g[1:] = f[:-1] * p                  # période rouge → la série s'allonge
        f = g
    return float(1.0 - f.sum())


def horizon_stats(mu_annual: float, sigma_annual: float, n_periods: int,
                  max_run: Optional[int] = None) -> dict:
    """Statistiques de l'outil de référence à l'horizon 1/n (12 = mensuel, 252 = journalier)."""
    mu_h = mu_annual / n_periods
    sigma_h = sigma_annual / math.sqrt(n_periods)
    p_red = float(norm.cdf(-mu_h / sigma_h)) if sigma_h > 0 else float(mu_h < 0)

    pmf = [float(binom.pmf(k, n_periods, p_red)) for k in range(n_periods + 1)]
    max_run = max_run or min(n_periods, 12)
    runs = [(x, prob_run_at_least(n_periods, p_red, x)) for x in range(1, max_run + 1)]

    return {
        "n_periods": n_periods,
        "mu_h": mu_h, "sigma_h": sigma_h,
        "p_red": p_red,
        "p_at_least_one": 1.0 - (1.0 - p_red) ** n_periods,
        "mean_reds": n_periods * p_red,
        "var_reds": n_periods * p_red * (1.0 - p_red),
        "std_reds": math.sqrt(n_periods * p_red * (1.0 - p_red)),
        "p_zero_red": (1.0 - p_red) ** n_periods,
        "pmf": pmf,                      # P(X = k), k = 0..n
        "runs": runs,                    # [(X, P(série ≥ X consécutives))]
    }


# ---------------------------------------------------------------------------
# 4. Intervalle de confiance du Sharpe annualisé (méthode delta, Lo 2002)
# ---------------------------------------------------------------------------

def sharpe_confidence_interval(sharpe_annual: float, t_days: int,
                               confidence: float = 0.95,
                               periods_per_year: int = 252) -> dict:
    """
    IC asymptotique du Sharpe ANNUALISÉ estimé sur T jours de rendements i.i.d. :
        ŝ_d = Ŝ/√A ;  Var(ŝ_d) ≈ (1 + ŝ_d²/2)/T  (méthode delta sur (μ̂, σ̂))
        SE(Ŝ) = √A·SE(ŝ_d) = √[(1 + Ŝ²/(2A))·A/T]
    Référence : (1.5, 252 j, 95 %) → [−0.464 ; 3.464].
    """
    a = float(periods_per_year)
    s_d = sharpe_annual / math.sqrt(a)
    se_annual = math.sqrt((1.0 + 0.5 * s_d ** 2) * a / t_days)
    z = float(norm.ppf(0.5 + confidence / 2.0))
    half = z * se_annual
    # Bonus desk : nb de jours nécessaires pour que l'IC exclue 0 (significativité)
    t_signif = math.ceil((1.0 + 0.5 * s_d ** 2) * a * (z / sharpe_annual) ** 2) \
        if sharpe_annual > 0 else None
    return {"sharpe": sharpe_annual, "se": se_annual, "z": z,
            "lo": sharpe_annual - half, "hi": sharpe_annual + half,
            "confidence": confidence, "t_days": t_days,
            "t_days_for_significance": t_signif,
            "significant": sharpe_annual - half > 0}
