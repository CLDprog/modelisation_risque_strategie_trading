"""
Carnet d'ordres — pont DASHBOARD → COLLECTEUR (respecte l'isolation des process).

Le dashboard NE PARLE JAMAIS à IBKR : il dépose un « ticket d'ordre » (JSON) via
`submit_ticket()`. Le COLLECTEUR, qui possède la session, lit les tickets en attente
(`pending_tickets`), résout le conid, place l'ordre, puis réécrit le statut
(`update_ticket`). Le blotter (ordres vivants + positions) est publié par le
collecteur dans `blotter.json`, lu par le dashboard.

Aucune dépendance broker ici — pur fichiers + résolution de strike sur la chaîne
collectée. Le module est donc importable côté front (écriture) ET côté collecteur
(lecture/maj), sans jamais toucher IBKR.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import pandas as pd

_ORDERS_DIR  = Path(__file__).resolve().parents[2] / "data" / "orders"
_TICKETS_DIR = _ORDERS_DIR / "tickets"
_BLOTTER     = _ORDERS_DIR / "blotter.json"

# Cycle de vie d'un ticket. `cancel_requested` = le dashboard demande l'annulation
# d'un ordre déjà soumis → le collecteur l'exécute et passe à `cancelled`.
STATUSES = ("pending", "submitted", "filled", "cancelled",
            "cancel_requested", "rejected", "error")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class OrderTicket:
    """Intention d'ordre déposée par le dashboard. Le contrat est LOGIQUE
    (sous-jacent / type / échéance / strike) ; le collecteur résout le conid."""
    underlying: str                       # id interne (ex. 'SAP', 'ESTX50')
    instrument: str                       # 'C', 'P' (option) ou 'FUT' (future indice)
    side: str                             # 'BUY' ou 'SELL'
    quantity: float
    order_type: str = "MKT"               # 'MKT' ou 'LMT'
    limit_price: Optional[float] = None
    expiry: Optional[str] = None          # ISO date (options & futures)
    strike: Optional[float] = None        # options
    moneyness: Optional[str] = None       # 'ATM'/'OTM'/'ITM' (info de saisie)
    tif: str = "DAY"
    ticket_id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    created_ts: str = field(default_factory=_now)
    status: str = "pending"
    broker_order_id: Optional[str] = None
    message: Optional[str] = None
    updated_ts: Optional[str] = None


# ---------------------------------------------------------------------------
# Écriture / lecture des tickets (atomique)
# ---------------------------------------------------------------------------

def _write(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    tmp.replace(path)


def submit_ticket(ticket: OrderTicket) -> str:
    """Dépose un ticket (statut 'pending'). Retourne son id."""
    _write(_TICKETS_DIR / f"{ticket.ticket_id}.json", asdict(ticket))
    return ticket.ticket_id


def all_tickets() -> List[dict]:
    """Tous les tickets, du plus récent au plus ancien."""
    if not _TICKETS_DIR.exists():
        return []
    out = []
    for f in _TICKETS_DIR.glob("*.json"):
        try:
            out.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return sorted(out, key=lambda t: t.get("created_ts", ""), reverse=True)


def tickets_with_status(*statuses: str) -> List[dict]:
    s = set(statuses)
    return [t for t in all_tickets() if t.get("status") in s]


def pending_tickets() -> List[dict]:
    """Tickets que le collecteur doit traiter (à placer ou à annuler)."""
    return tickets_with_status("pending", "cancel_requested")


def update_ticket(ticket_id: str, status: Optional[str] = None,
                  broker_order_id: Optional[str] = None,
                  message: Optional[str] = None) -> None:
    p = _TICKETS_DIR / f"{ticket_id}.json"
    if not p.exists():
        return
    t = json.loads(p.read_text(encoding="utf-8"))
    if status is not None:
        t["status"] = status
    if broker_order_id is not None:
        t["broker_order_id"] = broker_order_id
    if message is not None:
        t["message"] = message
    t["updated_ts"] = _now()
    _write(p, t)


def request_cancel(ticket_id: str) -> None:
    """Le dashboard demande l'annulation d'un ordre soumis."""
    t = _TICKETS_DIR / f"{ticket_id}.json"
    if t.exists():
        cur = json.loads(t.read_text(encoding="utf-8"))
        if cur.get("status") == "submitted":
            update_ticket(ticket_id, "cancel_requested")


# ---------------------------------------------------------------------------
# Blotter publié par le collecteur (ordres vivants + positions)
# ---------------------------------------------------------------------------

def write_blotter(orders: list, positions: list) -> None:
    _write(_BLOTTER, {"ts": _now(), "orders": orders, "positions": positions})


def read_blotter() -> dict:
    if _BLOTTER.exists():
        try:
            return json.loads(_BLOTTER.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"ts": None, "orders": [], "positions": []}


# ---------------------------------------------------------------------------
# Résolution du strike par moneyness, sur la chaîne déjà collectée
# ---------------------------------------------------------------------------

def resolve_strike(chain: pd.DataFrame, expiry: str, right: str,
                   moneyness: str) -> Optional[float]:
    """Choisit un strike dans la chaîne collectée pour (échéance, call/put, moneyness).
    ATM = le plus proche du forward ; OTM/ITM = le plus proche du bon côté.
      - call OTM / put ITM → strike au-dessus du forward
      - call ITM / put OTM → strike en-dessous du forward
    """
    if chain is None or chain.empty:
        return None
    df = chain[chain["expiry"].astype(str) == str(expiry)]
    if "is_usable" in df.columns:
        df = df[df["is_usable"]]
    df = df.dropna(subset=["strike", "forward"])
    if df.empty:
        return None
    fwd = float(df["forward"].iloc[0])
    strikes = sorted(float(k) for k in df["strike"].unique())
    if not strikes:
        return None
    m = (moneyness or "ATM").upper()
    if m == "ATM":
        return min(strikes, key=lambda k: abs(k - fwd))
    is_call = str(right).upper().startswith("C")
    above_side = (m == "OTM") == is_call        # True → strike > forward
    if above_side:
        above = [k for k in strikes if k > fwd]
        return above[0] if above else max(strikes)
    below = [k for k in strikes if k < fwd]
    return below[-1] if below else min(strikes)
