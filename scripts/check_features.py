"""
Contrôle de présence de TOUTES les fonctionnalités implémentées (conformité roadmap).
Vérifie : tables du store, colonnes de lineage, fonctions clés, docs.
Usage : python scripts/check_features.py
"""
import sys, importlib, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from loguru import logger; logger.remove()

import pandas as pd
from datetime import date

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
today = date.today()
OK, KO = "  [OK] ", "  [.. ] "

def has_parquet(rel):
    p = DATA / rel / f"dt={today}" / "data.parquet"
    if p.exists():
        try:
            return len(pd.read_parquet(p))
        except Exception:
            return 0
    return None

print("=" * 66)
print("CONTRÔLE DES FONCTIONNALITÉS — conformité roadmap")
print("=" * 66)

# ---- 1. Couches de données (store) ----
print("\n[1] Tables du store (data du jour)")
tables = {
    "raw/raw_market_events":          "Couche brute immuable (étape 3)",
    "analytics/market_state_snapshots": "Snapshots déterministes (étape 5)",
    "analytics/iv_points":            "Chaîne + IV (étape 8)",
    "analytics/forward_curve":        "Forwards (étape 6)",
    "analytics/surface_grid":         "Surface SVI (étape 9)",
    "analytics/qc_results":           "QC checks (étape 14)",
    "analytics/qc_triage":            "Table de triage (étape 14)",
    "analytics/qc_anomalies":         "Anomalies vs baseline (étape 14)",
    "raw/qc_history":                 "Historique QC pour baselines (étape 14)",
    "analytics/risk_aggregates":      "Risk positions (étape 11) — si positions",
    "analytics/scenario_results":     "Scénarios (étape 12) — si positions",
}
for rel, desc in tables.items():
    n = has_parquet(rel)
    tag = OK if n is not None else KO
    cnt = f"{n} lignes" if n is not None else "absent"
    print(f"{tag}{rel:38s} {cnt:12s} {desc}")

# ---- 2. Fichiers de statut / alertes ----
print("\n[2] Statut & alertes (étape 15)")
for f, desc in [("collector_status.json", "Statut collecteur"),
                ("alerts.json", "Alertes routées"),
                ("metadata.db", "Métadonnées SQLite (instrument_master)")]:
    p = DATA / f
    print(f"{OK if p.exists() else KO}{f:38s} {'présent' if p.exists() else 'absent'}      {desc}")

# instrument_master rows
try:
    from src.storage.schemas import MetadataStore
    ms = MetadataStore(DATA / "metadata.db")
    n = pd.read_sql("SELECT count(*) n FROM instrument_master", ms.engine)["n"].iloc[0]
    print(f"{OK}{'instrument_master (table)':38s} {int(n)} lignes   Master canonique (étape 2)")
except Exception as e:
    print(f"{KO}instrument_master : {e}")

# ---- 3. Lineage (provenance) ----
print("\n[3] Lineage / provenance (étape 4)")
lp = DATA / "analytics/iv_points" / f"dt={today}" / "data.parquet"
if lp.exists():
    cols = set(pd.read_parquet(lp).columns)
    for c in ("code_version", "config_hash", "run_id"):
        print(f"{OK if c in cols else KO}{c:38s} {'présent' if c in cols else 'absent'}")
else:
    print(f"{KO}iv_points absent — lance le collecteur d'abord")

# ---- 4. Fonctions clés (code) ----
print("\n[4] Fonctions clés implémentées")
checks = [
    ("src.iv.solver", "solve_iv_american", "Inversion IV américaine (étape 8)"),
    ("src.surfaces.calibration", "check_butterfly_arbitrage", "No-arb papillon (étape 9)"),
    ("src.qc.anomaly", "compute_baselines", "Baselines glissantes (étape 14)"),
    ("src.qc.anomaly", "detect_anomalies", "Détection anomalies (étape 14)"),
    ("src.qc.anomaly", "build_triage_table", "Table de triage (étape 14)"),
    ("src.orchestration.jobs", "build_snapshots_job", "Reconstruction snapshots (étape 13)"),
    ("src.orchestration.jobs", "replay_pipeline", "Replay (étape 13)"),
    ("src.orchestration.jobs", "compare_replay_vs_live", "Comparaison replay/live (étape 13)"),
    ("src.data.live", "enrich_portfolio_greeks", "Enrichissement Greeks (étape 11)"),
]
for mod, fn, desc in checks:
    try:
        m = importlib.import_module(mod)
        ok = hasattr(m, fn)
    except Exception:
        ok = False
    print(f"{OK if ok else KO}{(mod.split('.')[-1] + '.' + fn):42s} {desc}")

# OptionContract.from_key
try:
    from src.universe.contracts import OptionContract
    ok = OptionContract.from_key("SPY|OPT|20260612|750.0000|C|SMART|USD") is not None
    print(f"{OK if ok else KO}{'OptionContract.from_key':42s} Parse clé (étape 13)")
except Exception:
    print(f"{KO}OptionContract.from_key")

# ---- 5. Documentation d'exploitation ----
print("\n[5] Documentation (étape 16)")
for d in ["runbooks.md", "release_checklist.md", "known_limitations.md",
          "environment.md", "interface_contracts.md"]:
    p = ROOT / "docs" / d
    print(f"{OK if p.exists() else KO}docs/{d:34s} {'présent' if p.exists() else 'absent'}")

print("\n" + "=" * 66)
print("Note : risk_aggregates / scenario_results n'apparaissent que si tu as")
print("des positions options dans ton portefeuille paper IBKR.")
