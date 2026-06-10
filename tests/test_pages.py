"""Tests du front (callbacks des pages Dash) sur datasource monkeypatché —
couverture demandée par l'audit (modules front récents)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest

import app  # noqa: F401 — enregistre l'app Dash + les pages
from src.data.source import datasource
from pages import overview, greeks, implied_vol, qc as qc_page


@pytest.fixture
def fake_overview(monkeypatch):
    ov = pd.DataFrame([
        {"symbol": "ESTX50", "description": "Indice", "sec_type": "IND",
         "exchange": "EUREX", "option_exchange": "EUREX", "spot": 6018.44,
         "n_quotes": 58, "n_usable": 58, "updated": "2026-06-10T09:41:00+00:00",
         "n_options": 58, "n_expiries": 6, "iv_mean": 0.18, "n_forwards": 6,
         "qc_warn": 1, "qc_fail": 0},
        {"symbol": "NDA", "description": "Nordea", "sec_type": "STK",
         "exchange": "HEX", "option_exchange": "EUREX", "spot": None,
         "n_quotes": 0, "n_usable": 0, "updated": None, "n_options": 0,
         "n_expiries": 0, "iv_mean": None, "n_forwards": 0,
         "qc_warn": 0, "qc_fail": 1},
    ])
    monkeypatch.setattr(datasource, "get_universe_overview", lambda: ov)
    monkeypatch.setattr(datasource, "collector_status",
                        lambda: {"last_cycle": "2026-06-10T09:50:51+00:00",
                                 "last_cycle_secs": 1222.8, "symbols": {}})
    monkeypatch.setattr(datasource, "get_data_source_label", lambda: "Analytics (store)")
    monkeypatch.setattr(datasource, "get_dispersion", lambda: pd.DataFrame())
    monkeypatch.setattr(datasource, "_read_analytics",
                        lambda table, dt=None: pd.DataFrame())
    return ov


def test_overview_formats_table_and_metrics(fake_overview):
    metrics, f_mat, f_sc, f_disp, f_bars, data = overview.refresh_overview(0)
    assert len(data) == 2 and len(metrics) == 6
    row = data[0]
    assert row["spot"] == "6,018.44" and row["iv_mean"] == "18.0%"
    assert data[1]["spot"] == "—"            # NDA sans données
    assert len(f_bars.data) == 1             # bar chart de couverture


def test_overview_click_selects_symbol():
    out = overview.select_symbol({"row": 1}, None, [{"symbol": "A"}, {"symbol": "SAP"}])
    assert out == "SAP"
    out = overview.select_symbol(None, {"points": [{"x": "BMW"}]}, None)
    assert out == "BMW"


def _grid_chain():
    return pd.DataFrame([
        {"underlying_symbol": "SAP", "expiry": "2026-09-18", "strike": k,
         "right": r, "mid_price": 5.0, "implied_vol": 0.25, "is_usable": True,
         "multiplier": 100, "delta": 0.5, "gamma": 0.01, "vega": 0.2,
         "theta": -0.02, "eur_delta": 8000.0, "eur_gamma": 250.0,
         "eur_vega": 20.0, "eur_theta": -2.0}
        for k in (150.0, 160.0) for r in ("C", "P")
    ])


def test_greeks_grid_meta_and_table(monkeypatch):
    monkeypatch.setattr(datasource, "get_option_chain", lambda s=None: _grid_chain())
    metrics, opts, value = greeks.refresh_grid_meta(0, "SAP", None)
    assert len(opts) == 1 and value == "2026-09-18"
    f1, f2, data = greeks.refresh_grid("2026-09-18", "SAP", 0)
    assert len(data) == 4
    assert {"eur_delta", "eur_vega", "delta"} <= set(data[0].keys())
    assert len(f1.data) == 2                 # calls + puts


def test_greeks_portfolio_empty_state(monkeypatch):
    monkeypatch.setattr(datasource, "get_portfolio", lambda s=None: pd.DataFrame())
    monkeypatch.setattr(datasource, "get_position_risk", lambda s=None: pd.DataFrame())
    status, metrics, data = greeks.refresh_portfolio(0, "SAP")
    assert status is not None and metrics == [] and data == []


def test_iv_diagnostics_summary(monkeypatch):
    diag = pd.DataFrame([
        {"underlying_symbol": "SAP", "expiry": "2026-09-18", "strike": 150.0,
         "right": "C", "mid_price": 5.0, "forward": 155.0, "implied_vol": 0.25,
         "converged": True, "failure_reason": None},
        {"underlying_symbol": "SAP", "expiry": "2026-09-18", "strike": 90.0,
         "right": "P", "mid_price": 0.01, "forward": 155.0, "implied_vol": None,
         "converged": False, "failure_reason": "below_intrinsic"},
    ])
    monkeypatch.setattr(datasource, "get_iv_diagnostics", lambda s=None: diag)
    summary, data = implied_vol.refresh_iv_diagnostics("SAP", 0)
    assert len(data) == 1                     # seuls les échecs listés
    assert data[0]["failure_reason"] == "below_intrinsic"


def test_qc_extras_recon_and_triage(monkeypatch):
    recon = pd.DataFrame([{"underlying_symbol": "SAP", "expiry": "2026-09-18",
                           "strike": 150.0, "right": "C", "model": "crr",
                           "delta_pub": 0.5, "delta_fd": 0.5, "delta_diff": 0.001,
                           "vega_pub": 0.2, "vega_fd": 0.2, "vega_diff": 0.0,
                           "gamma_diff": 0.01, "theta_diff": 0.02}])
    triage = pd.DataFrame([{"check_name": "coverage", "target_key": "SAP",
                            "status": "warn", "severity": "warning",
                            "measured_value": 0.4, "threshold": 0.5,
                            "reason_code": "low_coverage", "run_id": "r1"}])
    monkeypatch.setattr(datasource, "get_greeks_reconciliation", lambda s=None: recon)
    monkeypatch.setattr(datasource, "get_qc_triage", lambda s=None: triage)
    summary, recon_data, triage_data = qc_page.refresh_qc_extras(0, "SAP")
    assert len(recon_data) == 1 and len(triage_data) == 1
    assert isinstance(summary, list)          # badges (données présentes)
