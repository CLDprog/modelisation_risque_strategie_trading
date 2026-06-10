"""Tests des écarts roadmap fermés : Eq.22, Eq.23, pricing_results, versioning store."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import math
import numpy as np
import pandas as pd
import pytest

from src.surfaces.calibration import interpolate_across_maturities
from src.risk.dispersion import (basket_variance, implied_correlation,
                                 dispersion_diagnostics)
from src.storage.schemas import ParquetStore


# ---------------------------------------------------------------------------
# Eq. 22 — interpolation cross-maturité en variance totale
# ---------------------------------------------------------------------------

def _flat_surface(t_w_pairs):
    rows = []
    for T, w in t_w_pairs:
        for k in (-0.1, 0.0, 0.1):
            rows.append({"underlying": "TEST", "snapshot_ts": "ts",
                         "maturity_years": T, "log_moneyness": k,
                         "total_variance": w, "implied_vol": math.sqrt(w / T)})
    return pd.DataFrame(rows)


def test_eq22_linear_interpolation_midpoint():
    surf = _flat_surface([(0.5, 0.02), (1.0, 0.06)])
    out = interpolate_across_maturities(surf, [0.75])
    assert not out.empty
    w = out["total_variance"].unique()
    assert np.allclose(w, 0.04)                       # (0.02 + 0.06) / 2
    iv = out["implied_vol"].iloc[0]
    assert math.isclose(iv, math.sqrt(0.04 / 0.75), rel_tol=1e-9)
    assert (out["model"] == "eq22_interp").all()


def test_eq22_clamps_outside_range():
    surf = _flat_surface([(0.5, 0.02), (1.0, 0.06)])
    short = interpolate_across_maturities(surf, [0.1])
    long  = interpolate_across_maturities(surf, [3.0])
    assert np.allclose(short["total_variance"], 0.02)  # clamp tranche courte
    assert np.allclose(long["total_variance"], 0.06)   # clamp tranche longue


def test_eq22_exact_on_calibrated_slice():
    surf = _flat_surface([(0.5, 0.02), (1.0, 0.06)])
    out = interpolate_across_maturities(surf, [1.0])
    assert np.allclose(out["total_variance"], 0.06)


# ---------------------------------------------------------------------------
# Eq. 23 — variance panier & corrélation implicite
# ---------------------------------------------------------------------------

def test_eq23_basket_variance_full_correlation():
    # ρ=1 partout → σ_P = Σ w σ (le panier ne diversifie plus rien)
    w, s = [0.5, 0.5], [0.20, 0.30]
    var = basket_variance(w, s, np.ones((2, 2)))
    assert math.isclose(math.sqrt(var), 0.25, rel_tol=1e-12)


def test_eq23_basket_variance_diversification():
    w, s = [0.5, 0.5], [0.20, 0.20]
    var_indep = basket_variance(w, s, np.eye(2))
    assert math.sqrt(var_indep) < 0.20                # diversification ρ=0


def test_eq23_implied_correlation_round_trip():
    rng = np.random.default_rng(7)
    n = 10
    w = np.full(n, 1.0 / n)
    vols = rng.uniform(0.15, 0.45, n)
    rho_true = 0.6
    corr = np.full((n, n), rho_true)
    np.fill_diagonal(corr, 1.0)
    index_vol = math.sqrt(basket_variance(w, vols, corr))
    rho_hat = implied_correlation(index_vol, w, vols)
    assert math.isclose(rho_hat, rho_true, rel_tol=1e-9)


def test_eq23_dispersion_diagnostics_table():
    rows = []
    # indice : IV ATM 20% à ~30j ; 6 composantes : IV 30%
    for sym, iv in [("ESTX50", 0.20)] + [(f"C{i}", 0.30) for i in range(6)]:
        for k in (-0.05, 0.0, 0.05):
            rows.append({"underlying_symbol": sym, "days_to_expiry": 32,
                         "log_moneyness": k, "implied_vol": iv, "right": "C",
                         "is_usable": True, "converged": True})
    df = pd.DataFrame(rows)
    out = dispersion_diagnostics(df, "ESTX50", [30])
    assert len(out) == 1
    r = out.iloc[0]
    assert r["n_components"] == 6
    assert math.isclose(r["index_iv"], 0.20, rel_tol=1e-9)
    assert math.isclose(r["basket_avg_iv"], 0.30, rel_tol=1e-9)
    assert r["dispersion_spread"] > 0                  # vol moyenne > vol indice
    assert 0.0 < r["implied_correlation"] < 1.0       # ρ̄ = (σI²−Σw²σ²)/(…) cohérent


# ---------------------------------------------------------------------------
# pricing_results — round-trip prix↔IV
# ---------------------------------------------------------------------------

def test_pricing_results_round_trip_european():
    from src.data.live import _pricing_results
    from src.pricing.european import bs_price

    F, K, T, iv, rate = 100.0, 100.0, 0.5, 0.25, 0.025
    mid = bs_price(F, K, iv, T, rate, "C")
    chain = pd.DataFrame([{
        "instrument_key": "TEST", "expiry": "2026-12-18", "strike": K, "right": "C",
        "forward": F, "reference_spot": 99.0, "maturity_years": T,
        "implied_vol": iv, "mid_price": mid,
    }])
    out = _pricing_results(chain, "TEST", rate, american=False, snap_ts="ts")
    assert len(out) == 1
    assert out["model"].iloc[0] == "black76"
    assert out["abs_error"].iloc[0] < 1e-10            # re-pricing à l'IV inversée = mid


def test_pricing_results_american_routing():
    from src.data.live import _pricing_results
    chain = pd.DataFrame([{
        "instrument_key": "TEST", "expiry": "2026-12-18", "strike": 100.0, "right": "P",
        "forward": 100.5, "reference_spot": 100.0, "maturity_years": 0.5,
        "implied_vol": 0.25, "mid_price": 7.0,
    }])
    out = _pricing_results(chain, "TEST", 0.025, american=True, snap_ts="ts")
    assert out["model"].iloc[0] == "crr_american"
    assert out["model_price"].iloc[0] > 0


# ---------------------------------------------------------------------------
# Versioning du ParquetStore
# ---------------------------------------------------------------------------

def test_parquet_store_versioning(tmp_path):
    store = ParquetStore(tmp_path)
    dt = date(2026, 6, 10)
    df1 = pd.DataFrame({"x": [1, 2]})
    df2 = pd.DataFrame({"x": [3, 4, 5]})

    store.write("t", df1, dt, atomic=True, version="run_1")
    store.write("t", df2, dt, atomic=True, version="run_2")

    assert len(store.read("t", dt)) == 3               # data.parquet = dernier état
    assert store.list_versions("t", dt) == ["run_1", "run_2"]
    assert len(store.read_version("t", dt, "run_1")) == 2   # l'historique survit
    assert store.read_version("t", dt, "absent").empty
