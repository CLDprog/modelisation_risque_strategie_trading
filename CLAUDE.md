# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture à 2 process (découplée — roadmap "process isolation")

```
Terminal 1 : COLLECTEUR              Terminal 2 : DASHBOARD
python run_collector.py              python app.py
  ├─ possède la connexion IBKR         └─ LIT le store uniquement
  ├─ collecte + analytics                 (ne touche jamais IBKR)
  └─ écrit data/ (Parquet + status)
```

Le front Dash ne se connecte JAMAIS à IBKR. Toutes les données viennent du store
alimenté par `run_collector.py`. Si la connexion IBKR tombe, seul le collecteur est
affecté — le dashboard continue d'afficher les dernières données.

## Commands

```bash
# 1. Démarrer le collecteur (possède IBKR, alimente le store) — TWS doit être ouvert
python run_collector.py
# Options : --port 4002 (Gateway) · --interval 60 · --client-id 2

# 2. Lancer le dashboard (lit le store)
python app.py
# Ouvrir http://localhost:8050

# Diagnostic spot par symbole (TWS ouvert)
python scripts/diagnose_spot.py

# Tests unitaires
python -m pytest tests/ -v

# Un seul test
python -m pytest tests/test_pricing.py::test_put_call_parity -v

# Test de connexion IBKR (TWS doit être ouvert)
python scripts/bootstrap.py

# Pipeline EOD complet
python -c "
from src.orchestration.jobs import run_eod_pipeline
from datetime import date
run_eod_pipeline(date.today())
"

# Builder des snapshots depuis raw events (avant EOD)
python -c "
from src.orchestration.jobs import build_snapshots_job
from datetime import date
build_snapshots_job(date.today())
"

# Replay historique sur une plage de dates
python -c "
from src.orchestration.jobs import replay_pipeline
from datetime import date
replay_pipeline(date(2026,5,1), date.today())
"
```

## Architecture

Infrastructure de risque de volatilité en 16 étapes. Les couches sont strictement séparées et ne remontent jamais vers l'amont.

```
IBKR → connectivity → collectors → snapshots → forwards → iv → surfaces → pricing → risk → qc
```

**Règle fondamentale :** les données brutes (`raw_market_events`) sont immuables et append-only.

### Source de données du front (`src/data/source.py`)

`DataSource` est désormais un **lecteur de store pur** — aucun appel IBKR. Il lit :
- `data/collector_status.json` → statut collecteur + spot par symbole
- `data/analytics/*.parquet` → iv_points, forward_curve, surface_grid, risk_aggregates, scenario_results, qc_results

Le collecteur (`run_collector.py`) est le SEUL à parler à IBKR (via `src/data/live.py`).

### Sélection du produit actif

L'app supporte SPY, QQQ, AAPL (ou tout symbole dans `src/data/mock.py::SYMBOL_PARAMS`).

- Sélecteur dropdown dans la sidebar → `dcc.Store(id="selected-symbol")`
- Toutes les pages lisent ce store via `Input("selected-symbol", "data")`
- `datasource.selected_symbol` est mis à jour en sync
- Pour ajouter un symbole : ajouter une entrée dans `SYMBOL_PARAMS`

### Couches backend (`src/`)

| Module | Rôle clé |
|--------|----------|
| `connectivity/session.py` | `IBKRSession` — machine d'état 5 états, reconnect exponentiel avec jitter |
| `universe/contracts.py` | `InstrumentMaster` + `Underlying` + `OptionContract` — source de vérité |
| `collectors/raw_writer.py` | `LiveCollector` — souscrit underlyings ET options. `RawEventWriter` append-only. |
| `snapshots/builder.py` | Fonctions pures : `build_snapshot()` déterministe |
| `forwards/engine.py` | `estimate_forward()` : parité put-call + pondération liquidité + rejet MAD |
| `iv/solver.py` | `solve_iv()` : Brent bracketé → `IvSolveResult` toujours retourné |
| `surfaces/calibration.py` | SVI par tranche, spline fallback, monotonicity calendaire |
| `pricing/european.py` | Black-76 + Greeks analytiques |
| `pricing/american.py` | CRR binomial tree |
| `risk/aggregation.py` | `compute_position_risk()` → `aggregate_risk()` |
| `risk/scenarios.py` | `run_all_scenarios()` : repricing complet + approximation Eq.19 |
| `qc/checks.py` | 8 checks nommés → `QcResult(status, severity, measured_value, threshold, reason_code)` |
| `storage/schemas.py` | `ParquetStore` (partitionné par date) + `MetadataStore` (SQLite) |
| `orchestration/jobs.py` | `build_snapshots_job()` + `run_eod_pipeline()` + `replay_pipeline()` |
| `data/mock.py` | Données simulées multi-symboles : SPY, QQQ, AAPL |
| `data/source.py` | `DataSource` singleton — hiérarchie analytics > live > mock |

### Pipeline EOD en deux jobs

```
1. build_snapshots_job()     — raw_market_events → market_state_snapshots
2. run_eod_pipeline()        — snapshots → forwards → IV → surface → risk → scenarios → QC
```

`run_eod_pipeline()` appelle automatiquement `build_snapshots_job()` si les snapshots sont absents.

### Provenance / lineage

Toutes les tables dérivées portent : `code_version`, `session_date`, `run_id`.
Chaque job écrit un manifest JSON dans `data/manifests/`.

### Frontend Dash (`app.py` + `pages/`)

App multi-pages avec sidebar fixe et sélecteur de produit. Chaque page est entièrement callback-driven (plus de calcul au module load). Thème dark custom `assets/style.css`.

- `dcc.Store(id="selected-symbol")` — persisté en session
- Toutes les pages : `Input("selected-symbol", "data")` + `Input("*-interval", "n_intervals")`

### Configuration (`configs/`)

Tous les seuils économiques dans les YAML — jamais dans le code.

### Conventions mathématiques

- Moneyness : log-moneyness `k = ln(K/F)` (vs forward, pas spot)
- Surface : variance totale `w = σ²T`
- Vega : par **1 point de vol** (×0.01)
- Theta : par **jour calendaire** (÷365)
- Day-count : ACT/365
- Timestamps : UTC partout

## Points d'attention

- `IBKRSession` requiert TWS ou IB Gateway ouvert (port 7497 paper TWS)
- `LiveCollector.subscribe_all()` souscrit underlyings ET options
- Les schemas Parquet/SQLite sont initialisés automatiquement
- Pour ajouter un symbole : `SYMBOL_PARAMS` dans `mock.py` + entrée dans `universe.yaml`
- `src/data/mock.py` doit rester synchronisé avec les colonnes attendues par les pages Dash
