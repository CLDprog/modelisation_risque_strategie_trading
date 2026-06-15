"""Fallback spline (PCHIP) — garde-fou contre l'overshoot de variance totale.
Régression du bug du 12/06 : UnivariateSpline produisait w_atm = −9e12 sur AI 2028
(points épars + strikes quasi-confondus), variance négative propagée au check
calendaire et à la surface."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from src.surfaces.calibration import _spline_fallback, check_calendar_monotonicity, SliceFitResult


def test_spline_fallback_stays_in_envelope():
    # Points épars et bruités, avec un quasi-doublon en k (le déclencheur du blowup)
    k = np.array([-0.20, -0.05, -0.0499, 0.10, 0.30])
    w = np.array([0.060, 0.045, 0.046, 0.050, 0.075])
    res = _spline_fallback(k, w, "2028-06-16", 2.0, len(k), 0.02)
    assert res.model == "spline"
    # AUCUNE valeur hors de l'enveloppe observée élargie — jamais de −9e12
    assert res.w_grid.min() >= w.min() * 0.5 - 1e-9
    assert res.w_grid.max() <= w.max() * 1.5 + 1e-9
    assert (res.w_grid > 0).all()                    # variance totale positive
    assert np.isfinite(res.w_grid).all()
    # sigma reconstruite finie et raisonnable (< 100% de vol)
    assert np.isfinite(res.sigma_grid).all() and res.sigma_grid.max() < 1.0


def test_spline_fallback_handles_duplicate_k():
    # Deux strikes EXACTEMENT au même log-moneyness → ne doit pas exploser
    k = np.array([-0.1, 0.0, 0.0, 0.1])
    w = np.array([0.05, 0.04, 0.041, 0.05])
    res = _spline_fallback(k, w, "2027-12-17", 1.5, len(k), 0.02)
    assert res.model in ("spline", "failed")
    if res.model == "spline":
        assert np.isfinite(res.w_grid).all() and (res.w_grid > 0).all()


def test_calendar_check_not_broken_by_fallback():
    # Un slice court SVI propre suivi d'un slice long en fallback : le fallback
    # borné ne doit pas déclencher de fausse violation calendaire absurde.
    short = SliceFitResult(expiry="2026-09-18", maturity_years=0.25, model="svi",
                           n_points=8, rmse=0.0, max_error=0.0, quality_flag="ok",
                           k_grid=np.linspace(-0.2, 0.2, 50),
                           w_grid=np.full(50, 0.010),
                           sigma_grid=np.full(50, 0.20))
    k = np.array([-0.20, -0.05, 0.10, 0.30])
    w = np.array([0.060, 0.045, 0.050, 0.075])      # w long > w court partout
    long_ = _spline_fallback(k, w, "2028-06-16", 2.0, len(k), 0.02)
    # le w du fallback à ATM doit rester proche de l'enveloppe (≈ 0.045-0.05), pas −9e12
    idx = int(np.argmin(np.abs(long_.k_grid)))
    assert 0.02 < float(long_.w_grid[idx]) < 0.12
    assert check_calendar_monotonicity([short, long_]) is True
