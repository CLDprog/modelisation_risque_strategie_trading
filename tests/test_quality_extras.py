"""Tests des fermetures d'écarts résiduels : quote_filters (lib nommée), carry QC,
réconciliation greeks broker, routeur d'alertes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from src.qc.quote_filters import classify_quote
from src.qc.checks import check_carry_consistency, check_broker_greeks_reconciliation
from src.qc.alert_router import route_alerts, _format_text


CFG = {"quote_filters": {"max_spread_pct": 1.0, "min_open_interest": 10,
                         "require_positive_bid": True}}


# ── quote_filters (étape 7) ────────────────────────────────────────────────

def test_quote_bid_ask_usable():
    mid, ok, reason = classify_quote(10.0, 11.0, None, None, 100, 0.5, CFG)
    assert ok and reason is None and mid == 10.5


def test_quote_spread_too_wide():
    cfg = {"quote_filters": {"max_spread_pct": 0.25}}
    _, ok, reason = classify_quote(1.0, 2.0, None, None, None, 0.5, cfg)
    assert not ok and reason == "spread_too_wide"


def test_quote_low_open_interest_applied():
    _, ok, reason = classify_quote(10.0, 10.5, None, None, 3, 0.5, CFG)
    assert not ok and reason == "low_open_interest"


def test_quote_missing_oi_not_rejected():
    # OI absent ≠ rejet (EUREX ne publie pas toujours l'OI)
    _, ok, reason = classify_quote(10.0, 10.5, None, None, None, 0.5, CFG)
    assert ok and reason is None


def test_quote_fallbacks_and_expiry():
    mid, ok, reason = classify_quote(None, None, 7.5, None, None, 0.5, CFG)
    assert ok and reason == "price_from_last" and mid == 7.5
    mid, ok, reason = classify_quote(None, None, None, 7.0, None, 0.5, CFG)
    assert ok and reason == "price_from_close"
    _, ok, reason = classify_quote(None, None, None, None, None, 0.5, CFG)
    assert not ok and reason == "no_price"
    _, ok, reason = classify_quote(10.0, 10.5, None, None, None, 0.0, CFG)
    assert not ok and reason == "expired"


def test_quote_non_positive_bid_falls_back():
    mid, ok, reason = classify_quote(0.0, 10.5, 8.0, None, None, 0.5, CFG)
    assert ok and reason == "price_from_last" and mid == 8.0


# ── carry consistency (étape 6) ────────────────────────────────────────────

def test_carry_in_bounds_pass():
    fwd = pd.DataFrame({"underlying": ["X"] * 3, "implied_carry": [0.01, -0.02, 0.03]})
    r = check_carry_consistency(fwd, "X")
    assert r.status == "pass"


def test_carry_out_of_bounds_warn():
    fwd = pd.DataFrame({"underlying": ["X"] * 2, "implied_carry": [0.01, 0.5]})
    r = check_carry_consistency(fwd, "X", -0.10, 0.10)
    assert r.status == "warn" and r.reason_code == "carry_out_of_bounds"
    assert r.context["n_out_of_bounds"] == 1


# ── réconciliation greeks broker (étape 11) ────────────────────────────────

def _chain(broker_delta):
    n = 8
    return pd.DataFrame({
        "underlying_symbol": ["X"] * n, "is_usable": [True] * n,
        "delta": [0.5] * n, "broker_delta": [broker_delta] * n,
        "vega": [0.1] * n, "broker_vega": [0.11] * n,
    })


def test_broker_recon_pass():
    r = check_broker_greeks_reconciliation(_chain(0.52), "X", max_delta_diff=0.08)
    assert r.status == "pass" and r.context["n_points"] == 8


def test_broker_recon_mismatch_warn():
    r = check_broker_greeks_reconciliation(_chain(0.70), "X", max_delta_diff=0.08)
    assert r.status == "warn" and r.reason_code == "broker_delta_mismatch"


def test_broker_recon_skip_when_absent():
    df = pd.DataFrame({"underlying_symbol": ["X"], "is_usable": [True], "delta": [0.5]})
    r = check_broker_greeks_reconciliation(df, "X")
    assert r.status == "skip" and r.reason_code == "broker_greeks_not_captured"


# ── routeur d'alertes (étape 15) ───────────────────────────────────────────

def test_router_inactive_without_config():
    alerts = [{"level": "S1", "status": "fail", "check": "c", "target": "t",
               "reason": "r", "owner": "operator", "sla_minutes": 60}]
    assert route_alerts(alerts, {}) == {"webhook": None, "email": None}
    assert route_alerts([], {"webhook_url": "http://x"}) == {"webhook": None, "email": None}


def test_router_message_contains_escalation():
    txt = _format_text([{"level": "S1", "status": "fail", "check": "parity",
                         "target": "SAP", "reason": "residual_high",
                         "owner": "operator", "sla_minutes": 60}])
    assert "S1" in txt and "SAP" in txt and "SLA 60 min" in txt
