# AGENTS.md

This file provides guidance to coding agents working in this repository.

## Connexion broker — IBKR Web API (plus de TWS)

La connexion passe par l'**IBKR Client Portal Web API** (REST + WebSocket) via `ibind`,
isolée derrière un **adaptateur broker-agnostique**. **Plus aucun TWS ni `ib_insync`.**
Un **gateway** local (Client Portal Gateway Java, ou IBeam Docker) doit être authentifié
sur `https://localhost:5000` — voir `docs/gateway_setup.md`.

## Commands

```bash
# Gateway d'abord (voir docs/gateway_setup.md), puis :
python run_collector.py          # collecteur (possède la session, alimente le store)
python app.py                    # dashboard (lit le store) → http://localhost:8050
python scripts/bootstrap.py      # smoke test Web API
python -m pytest tests/ -v       # tests
python -c "from src.orchestration.jobs import run_eod_pipeline; from datetime import date; run_eod_pipeline(date.today())"
```

## Architecture

Infrastructure de risque de volatilité en 16 étapes. Couches strictement séparées.

```
IBKR Web API → connectivity(adapter) → collectors → snapshots → forwards → iv → surfaces → pricing → risk → qc
```

**Règle fondamentale :** `raw_market_events` est immuable et append-only ; toutes les analytics sont recalculables depuis le raw.

### Couches backend (`src/`)

| Module | Rôle clé |
|--------|----------|
| `connectivity/broker.py` | `BrokerAdapter` (ABC) broker-agnostique + types normalisés (jamais de SDK broker ici) |
| `connectivity/ibkr_webapi.py` | `IBKRWebAdapter` — wrappe `ibind` : auth/tickle, conids, snapshots (greeks), positions |
| `universe/contracts.py` | `InstrumentMaster` + `Underlying`/`OptionContract` ; `discover_universe(adapter,…)` |
| `collectors/raw_writer.py` | `RawMarketEvent` + `RawEventWriter` (buffer + flush Parquet) |
| `snapshots/builder.py` | `build_snapshot()` pur et déterministe |
| `forwards/engine.py` | `estimate_forward()` : parité put-call + pondération liquidité + rejet MAD |
| `iv/solver.py` | `solve_iv()` Brent ; inversion américaine CRR → `IvSolveResult` |
| `surfaces/calibration.py` | SVI par tranche + spline fallback + monotonie calendaire + no-arb papillon |
| `pricing/european.py` · `american.py` | Black-76 + Greeks analytiques · arbre CRR |
| `risk/aggregation.py` · `scenarios.py` | Greeks ligne/agrégats · repricing complet + approximation Eq.19 |
| `qc/checks.py` · `anomaly.py` | Checks QC nommés · baselines/anomalies (MAD) |
| `storage/schemas.py` | `ParquetStore` (par date) + `MetadataStore` (SQLite via SQLAlchemy) |
| `orchestration/jobs.py` | `build_snapshots_job()` + `run_eod_pipeline()` + `replay_pipeline()` |
| `data/live.py` | Fetchers Web API + analytics dérivées (purs) |
| `data/source.py` | `DataSource` — lecteur de store pur (front) |

### Frontend Dash (`app.py` + `pages/`)

App multi-pages, sidebar fixe + sélecteur de produit. Chaque page est callback-driven et
lit le store via `DataSource` (jamais IBKR). `dcc.Store(id="selected-symbol")` partagé.
Thème dark via `assets/style.css`.

### Configuration (`configs/`)

Tous les seuils économiques dans les YAML (chargés via `src/utils/config.load_config`).
Fichiers : `broker` (section `webapi`), `universe`, `qc`, `pricing`, `scenarios`, `environment`.

### Conventions mathématiques

- Moneyness `k = ln(K/F)` (vs forward) · Surface en **variance totale** `w = σ²T`
- Vega par **1 point de vol** (×0.01) · Theta par **jour calendaire** (÷365)
- Day-count ACT/365 · Timestamps UTC

## Points d'attention

- Le **gateway Web API** doit être authentifié (`https://localhost:5000`) avant le collecteur.
- Le code n'importe jamais `ibind` directement — uniquement `BrokerAdapter`.
- Équations de référence (Eq.1–25) dans `methodology.md`.
- Ajouter un symbole = une entrée dans `configs/universe.yaml` (chaîne découverte automatiquement).
