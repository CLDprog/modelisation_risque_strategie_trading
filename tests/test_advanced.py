"""
Tests des briques avancées ajoutées pour la conformité roadmap :
  - inversion d'IV américaine (Step 8)
  - no-arbitrage papillon (Step 9)
"""
import numpy as np

from src.pricing.american import price_american_binomial
from src.iv.solver import solve_iv_american
from src.surfaces.calibration import check_butterfly_arbitrage


# ---------------------------------------------------------------------------
# Inversion d'IV américaine
# ---------------------------------------------------------------------------

def test_american_iv_round_trip_call():
    """On price avec une vol connue puis on l'inverse → on doit la retrouver."""
    spot, K, sigma, T, r, q = 753.0, 731.0, 0.117, 30 / 365, 0.053, 0.017
    price = price_american_binomial(spot, K, sigma, T, r, q, "C", 200).price
    res = solve_iv_american(price, spot, K, T, r, q, "C")
    assert res.converged
    assert abs(res.implied_vol - sigma) < 5e-3


def test_american_iv_round_trip_put():
    spot, K, sigma, T, r, q = 753.0, 770.0, 0.20, 60 / 365, 0.053, 0.01
    price = price_american_binomial(spot, K, sigma, T, r, q, "P", 200).price
    res = solve_iv_american(price, spot, K, T, r, q, "P")
    assert res.converged
    assert abs(res.implied_vol - sigma) < 5e-3


def test_american_iv_below_intrinsic_fails_cleanly():
    """Un prix sous l'intrinsèque doit échouer proprement, sans NaN silencieux."""
    spot, K, T, r, q = 753.0, 700.0, 30 / 365, 0.053, 0.0
    intrinsic = spot - K  # 53
    res = solve_iv_american(intrinsic - 5.0, spot, K, T, r, q, "C")
    assert not res.converged
    assert res.implied_vol is None
    assert res.failure_reason is not None


# ---------------------------------------------------------------------------
# No-arbitrage papillon
# ---------------------------------------------------------------------------

def test_butterfly_healthy_smile_no_violation():
    k = np.linspace(-0.05, 0.05, 11)
    iv = 0.12 - 0.1 * k                      # smile linéaire doux, convexe en prix
    ok, n = check_butterfly_arbitrage(k, iv, 0.08)
    assert ok
    assert n == 0


def test_butterfly_pathological_smile_detected():
    k = np.linspace(-0.05, 0.05, 11)
    iv = 0.12 + 0.5 * np.abs(np.sin(k * 80))  # oscillant → arbitrage
    ok, n = check_butterfly_arbitrage(k, iv, 0.08)
    assert not ok
    assert n > 0


def test_butterfly_too_few_points_is_ok():
    ok, n = check_butterfly_arbitrage(np.array([0.0, 0.01]), np.array([0.12, 0.12]), 0.08)
    assert ok and n == 0


# ---------------------------------------------------------------------------
# Régression : fit_surface ne doit pas planter sur un DataFrame vide
# (cas marché fermé / aucune IV résolue) — Step 9 robustesse
# ---------------------------------------------------------------------------

def test_fit_surface_empty_dataframe_no_crash():
    import pandas as pd
    from src.surfaces.calibration import fit_surface
    res = fit_surface(pd.DataFrame(), "SPY", "2026-01-01", {"surface": {}})
    assert res.slices == []
    assert res.calendar_ok is True


def test_fit_surface_missing_columns_no_crash():
    import pandas as pd
    from src.surfaces.calibration import fit_surface
    df = pd.DataFrame({"strike": [750.0], "right": ["C"]})   # colonnes manquantes
    res = fit_surface(df, "SPY", "2026-01-01", {"surface": {}})
    assert res.slices == []


# ---------------------------------------------------------------------------
# Anomaly detection / baselines / triage (Step 14)
# ---------------------------------------------------------------------------

def test_anomaly_detection_flags_deviation():
    import pandas as pd
    from src.qc.anomaly import compute_baselines, detect_anomalies
    hist = pd.DataFrame([{"check_name": "surface_fit", "target_key": "SPY",
                          "measured_value": v} for v in [0.001, 0.0011, 0.0009, 0.001, 0.0012]])
    baselines = compute_baselines(hist)
    assert not baselines.empty
    current = pd.DataFrame([{"check_name": "surface_fit", "target_key": "SPY",
                             "measured_value": 0.05}])   # très au-dessus
    anomalies = detect_anomalies(current, baselines)
    assert len(anomalies) == 1


def test_anomaly_no_false_positive_on_normal_value():
    import pandas as pd
    from src.qc.anomaly import compute_baselines, detect_anomalies
    hist = pd.DataFrame([{"check_name": "iv_convergence", "target_key": "SPY",
                          "measured_value": v} for v in [0.99, 0.98, 1.0, 0.99, 0.98]])
    baselines = compute_baselines(hist)
    current = pd.DataFrame([{"check_name": "iv_convergence", "target_key": "SPY",
                             "measured_value": 0.99}])
    assert len(detect_anomalies(current, baselines)) == 0


def test_triage_table_keeps_only_warn_and_fail():
    from src.qc.checks import QcResult
    from src.qc.anomaly import build_triage_table
    results = [
        QcResult("a", "SPY", "pass", "info", 1.0, 0.9, "ok"),
        QcResult("b", "SPY", "warn", "warning", 0.5, 0.9, "low"),
        QcResult("c", "SPY", "fail", "error", 0.1, 0.9, "bad"),
    ]
    triage = build_triage_table(results, "run1")
    assert len(triage) == 2
    assert set(triage["status"]) == {"warn", "fail"}


# ---------------------------------------------------------------------------
# Replay vs live (Step 13)
# ---------------------------------------------------------------------------

def test_replay_vs_live_identical_zero_diff():
    import pandas as pd
    from src.orchestration.jobs import compare_replay_vs_live
    df = pd.DataFrame([{"instrument_key": "K1", "forward": 754.0, "implied_vol": 0.12},
                       {"instrument_key": "K2", "forward": 754.1, "implied_vol": 0.13}])
    out = compare_replay_vs_live(df, df.copy())
    assert out["status"] == "ok"
    assert out["metrics"]["forward"]["max_abs_diff"] == 0.0
    assert out["metrics"]["implied_vol"]["max_abs_diff"] == 0.0
