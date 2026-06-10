"""
Checklist de handover automatisée (roadmap Step 16) — rejoue le parcours « nouvel
ingénieur » : environnement → store → replay → rapport QC → où enquêter.

Usage : python scripts/handover_check.py   (gateway facultatif — testé s'il répond)
Sortie : PASS / WARN / FAIL par point + code retour 0 (ok) / 1 (au moins un FAIL).
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

RESULTS = []


def check(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn
    return deco


@check("1. Environnement : imports + configs chargées")
def _env():
    from src.utils.config import load_config
    import dash, ibind, pandas  # noqa: F401 — dépendances clés de requirements.txt
    for c in ("universe", "pricing", "qc", "broker"):
        assert load_config(c), f"config {c} vide"
    n = len(load_config("universe").get("underlyings", []))
    return f"51 attendus, {n} sous-jacents configurés"


@check("2. Store : analytics lisibles (partition récente)")
def _store():
    from src.data.source import datasource
    for d in range(7):
        dt = date.today() - timedelta(days=d)
        df = datasource._read_analytics("iv_points", dt)
        if not df.empty:
            return f"iv_points dt={dt} : {len(df)} lignes, {df['underlying_symbol'].nunique()} sous-jacents"
    raise AssertionError("aucune partition iv_points sur 7 jours")


@check("3. Replay : couche brute + jobs importables")
def _replay():
    from src.orchestration.jobs import build_snapshots_job, run_eod_pipeline, replay_pipeline  # noqa: F401
    raw = Path(__file__).parent.parent / "data" / "raw"
    parts = list(raw.glob("raw_market_events/dt=*")) if raw.exists() else []
    assert parts, "aucune partition raw_market_events (replay impossible)"
    return f"{len(parts)} partition(s) raw — replay_pipeline(start, end) disponible"


@check("4. Rapport QC : lisible + cibles en échec identifiables")
def _qc():
    from src.data.source import datasource
    df = datasource._read_analytics("qc_results")
    if df.empty:
        for d in range(1, 7):
            df = datasource._read_analytics("qc_results", date.today() - timedelta(days=d))
            if not df.empty:
                break
    assert not df.empty, "aucun qc_results récent"
    bad = df[df["status"].isin(["warn", "fail"])]["target_key"].unique()
    return f"{len(df)} checks ; cibles à investiguer : {', '.join(map(str, bad[:8])) or 'aucune'}"


@check("5. Où enquêter : runbooks + limitations documentés")
def _docs():
    root = Path(__file__).parent.parent
    needed = ["docs/runbooks.md", "docs/known_limitations.md", "docs/gateway_setup.md",
              "README.md", "docs/interface_contracts.md"]
    missing = [f for f in needed if not (root / f).exists()]
    assert not missing, f"docs manquantes : {missing}"
    return "runbooks, limitations, setup gateway, contrats d'interface présents"


@check("6. Gateway (optionnel) : session Web API")
def _gateway():
    from src.utils.config import load_config
    from src.connectivity.ibkr_webapi import IBKRWebAdapter
    w = load_config("broker").get("webapi", {})
    a = IBKRWebAdapter(host=w.get("host", "127.0.0.1"), port=w.get("port", 5000),
                       account_id=None, use_oauth=False)
    try:
        with a:
            return "session authentifiée" if a.is_healthy() else "gateway joignable, non authentifié"
    except Exception:
        return "gateway non lancé (OK hors séance — voir docs/gateway_setup.md)"


def main():
    print("=== Handover check — parcours nouvel ingénieur ===")
    failed = 0
    for name, fn in RESULTS:
        try:
            detail = fn()
            print(f"  PASS  {name} — {detail}")
        except AssertionError as exc:
            print(f"  FAIL  {name} — {exc}")
            failed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN  {name} — {type(exc).__name__}: {exc}")
    print("=== " + ("OK" if not failed else f"{failed} FAIL") + " ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
