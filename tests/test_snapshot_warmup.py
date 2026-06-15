"""Warm-up adaptatif du snapshot — un plateau à ZÉRO prix ne doit pas court-circuiter
(régression du 12/06 : lots 0/30 abandonnés à 8 essais → ALV/BMW/ABI à demi-collectés).
Un plateau PARTIEL stable (ailes réellement mortes) doit, lui, s'arrêter tôt."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.connectivity.ibkr_webapi as webapi
from src.connectivity.ibkr_webapi import IBKRWebAdapter


class _FakeClient:
    """Renvoie des rows vides jusqu'à `ready_after` appels, puis les prix demandés."""
    def __init__(self, conids, ready_after, partial=None):
        self.conids = conids
        self.ready_after = ready_after
        self.partial = partial          # nb de conids servis quand "partiel stable"
        self.calls = 0

    def live_marketdata_snapshot(self, conid_strs, codes):
        self.calls += 1
        if self.partial is not None:                     # plateau partiel permanent
            served = self.conids[:self.partial]
        elif self.calls >= self.ready_after:             # lot froid puis complet
            served = self.conids
        else:
            served = []
        return [{"conid": c, "84": 5.0 + i} for i, c in enumerate(served)]


def _adapter():
    return IBKRWebAdapter(host="127.0.0.1", port=5000, account_id=None, use_oauth=False)


def _run_chunk(monkeypatch, fake):
    monkeypatch.setattr(webapi.time, "sleep", lambda *_: None)   # pas d'attente réelle
    a = _adapter()
    a._client = fake
    out = {}
    a._snapshot_chunk(fake.conids, ["84"], {"84": "bid"}, out)
    return out


def test_cold_chunk_is_not_abandoned_at_zero(monkeypatch):
    # Lot froid qui ne livre qu'au 12e essai (> ancienne limite de 8) → doit attendre
    conids = [1, 2, 3]
    out = _run_chunk(monkeypatch, _FakeClient(conids, ready_after=12))
    assert all(out.get(c, {}).get("bid") is not None for c in conids)


def test_partial_stable_plateau_stops_early(monkeypatch):
    # 2 conids servis en permanence, 1 mort → plateau PARTIEL : s'arrête sans
    # épuiser les 30 essais (et sans jamais servir le 3e).
    conids = [1, 2, 3]
    fake = _FakeClient(conids, ready_after=0, partial=2)
    out = _run_chunk(monkeypatch, fake)
    assert out.get(1, {}).get("bid") is not None
    assert out.get(2, {}).get("bid") is not None
    assert 3 not in out or out.get(3, {}).get("bid") is None
    assert fake.calls < 30          # arrêt anticipé sur plateau partiel


def test_full_completion_stops_immediately(monkeypatch):
    conids = [1, 2]
    fake = _FakeClient(conids, ready_after=1)
    out = _run_chunk(monkeypatch, fake)
    assert all(out.get(c, {}).get("bid") is not None for c in conids)
    assert fake.calls <= 2          # complétude au 1er essai → pas de boucle inutile
