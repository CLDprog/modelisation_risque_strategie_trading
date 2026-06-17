"""Carnet d'ordres (pont dashboard→collecteur) — schéma de ticket, cycle de vie,
et résolution du strike par moneyness. Aucune dépendance IBKR."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest

import src.trading.order_book as ob
from src.trading.order_book import (OrderTicket, submit_ticket, all_tickets,
                                    pending_tickets, update_ticket, request_cancel,
                                    resolve_strike, write_blotter, read_blotter)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Redirige le stockage des tickets vers un dossier temporaire."""
    monkeypatch.setattr(ob, "_TICKETS_DIR", tmp_path / "tickets")
    monkeypatch.setattr(ob, "_BLOTTER", tmp_path / "blotter.json")


def test_ticket_roundtrip_and_lifecycle():
    tid = submit_ticket(OrderTicket(underlying="SAP", instrument="C", side="BUY",
                                    quantity=2, expiry="2027-06-18", strike=145.0,
                                    moneyness="ATM"))
    assert len(all_tickets()) == 1
    assert len(pending_tickets()) == 1
    update_ticket(tid, "submitted", broker_order_id="OID123", message="soumis")
    t = all_tickets()[0]
    assert t["status"] == "submitted" and t["broker_order_id"] == "OID123"
    assert pending_tickets() == []                       # n'est plus en attente


def test_cancel_request_only_for_submitted():
    tid = submit_ticket(OrderTicket("SAP", "P", "SELL", 1, expiry="2027-06-18",
                                    strike=145.0))
    request_cancel(tid)                                  # encore 'pending' → ignoré
    assert all_tickets()[0]["status"] == "pending"
    update_ticket(tid, "submitted", broker_order_id="X")
    request_cancel(tid)                                  # maintenant accepté
    t = all_tickets()[0]
    assert t["status"] == "cancel_requested"
    assert t in [x for x in [t]] and t["status"] in ("cancel_requested",)
    assert any(x["status"] == "cancel_requested" for x in pending_tickets())


def test_blotter_roundtrip():
    write_blotter([{"orderId": 1}], [{"underlying_symbol": "SAP", "quantity": 2}])
    b = read_blotter()
    assert b["orders"][0]["orderId"] == 1
    assert b["positions"][0]["underlying_symbol"] == "SAP"
    assert b["ts"] is not None


# ── résolution du strike par moneyness ──────────────────────────────────────

def _chain(fwd=100.0):
    rows = []
    for k in (80, 90, 100, 110, 120):
        for r in ("C", "P"):
            rows.append({"expiry": "2027-06-18", "right": r, "strike": float(k),
                         "forward": fwd, "is_usable": True})
    return pd.DataFrame(rows)


def test_resolve_strike_atm():
    assert resolve_strike(_chain(103), "2027-06-18", "C", "ATM") == 100.0  # plus proche de 103


def test_resolve_strike_otm_itm_sides():
    ch = _chain(100.0)
    # call OTM = strike au-dessus du forward ; call ITM = en-dessous
    assert resolve_strike(ch, "2027-06-18", "C", "OTM") == 110.0
    assert resolve_strike(ch, "2027-06-18", "C", "ITM") == 90.0
    # put OTM = strike en-dessous ; put ITM = au-dessus (miroir)
    assert resolve_strike(ch, "2027-06-18", "P", "OTM") == 90.0
    assert resolve_strike(ch, "2027-06-18", "P", "ITM") == 110.0


def test_resolve_strike_empty_chain():
    assert resolve_strike(pd.DataFrame(), "2027-06-18", "C", "ATM") is None
