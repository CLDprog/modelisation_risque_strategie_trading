# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Architecture à 2 process (découplée — roadmap "process isolation")

```
Terminal 1 : COLLECTEUR              Terminal 2 : DASHBOARD
python run_collector.py              python app.py
  ├─ possède la session IBKR Web       └─ LIT le store uniquement
  │   API (via le gateway local)          (ne touche jamais IBKR)
  ├─ collecte + analytics
  └─ écrit data/ (Parquet + status)

Prérequis : un GATEWAY IBKR Web API authentifié sur https://localhost:5000
  (Client Portal Gateway Java, ou IBeam Docker — voir docs/gateway_setup.md)
```

Le front Dash ne se connecte **jamais** à IBKR. Toutes les données viennent du store
alimenté par `run_collector.py`. Si la session IBKR tombe, seul le collecteur est
affecté — le dashboard continue d'afficher les dernières données.

**Plus de TWS ni de `ib_insync`.** La connexion passe par l'IBKR Client Portal Web API
(REST + WebSocket) via la librairie `ibind`, isolée derrière un adaptateur broker-agnostique.

## Commands

```bash
# 0. Lancer + authentifier le gateway (une fois) — voir docs/gateway_setup.md
#    Java  : cd C:\clientportal.gw && bin\run.bat root\conf.yaml  → login https://localhost:5000
#    Docker: docker compose -f gateway/docker-compose.yml up -d

# 1. Démarrer le collecteur (possède la session, alimente le store)
python run_collector.py
# Options : --interval 60 · --account-id DU… (sinon auto-découvert) · --host/--port

# 2. Lancer le dashboard (lit le store)
python app.py            # http://localhost:8050

# Smoke test Web API (résout SPY, snapshot, chaîne, greeks, positions, écrit 1 event)
python scripts/bootstrap.py

# Tests unitaires
python -m pytest tests/ -v
python -m pytest tests/test_pricing.py::test_put_call_parity -v

# Pipeline EOD complet (snapshots → forwards → IV → surface → risk → scénarios → QC)
python -c "from src.orchestration.jobs import run_eod_pipeline; from datetime import date; run_eod_pipeline(date.today())"

# Replay historique sur une plage de dates
python -c "from src.orchestration.jobs import replay_pipeline; from datetime import date; replay_pipeline(date(2026,5,1), date.today())"
```

## Architecture

Infrastructure de risque de volatilité en 16 étapes. Couches strictement séparées (ne remontent jamais vers l'amont).

```
IBKR Web API → connectivity(adapter) → collectors → snapshots → forwards → iv → surfaces → pricing → risk → qc
```

**Règle fondamentale :** les données brutes (`raw_market_events`) sont immuables et append-only ; toutes les analytics sont recalculables depuis le raw.

### Connexion broker (`src/connectivity/`)

- `broker.py` — `BrokerAdapter` (ABC) : **interface broker-agnostique** consommée par tout le code, + types normalisés (`SessionHealth`, `OptionChainParams`, `BrokerPosition`) + helper `to_float`. Aucun import de SDK broker ici.
- `ibkr_webapi.py` — `IBKRWebAdapter` : seule implémentation concrète, wrappe **`ibind`** (REST/WebSocket vers le Client Portal Gateway). Gère auth/`tickle`, résolution de conids (cache + `resolve_options` batché), snapshots (avec greeks broker), `historical_close`, positions.

Le reste du code n'importe JAMAIS `ibind` — uniquement `BrokerAdapter`. Ça permet de
swapper le broker ou d'injecter un mock pour les tests.

### Source de données du front (`src/data/source.py`)

`DataSource` est un **lecteur de store pur** — aucun appel IBKR. Il lit
`data/collector_status.json` + `data/analytics/*.parquet`.

### Couches backend (`src/`)

| Module | Rôle clé |
|--------|----------|
| `connectivity/broker.py` | `BrokerAdapter` (ABC) + types normalisés |
| `connectivity/ibkr_webapi.py` | `IBKRWebAdapter` — wrappe `ibind` (Web API) |
| `universe/contracts.py` | `InstrumentMaster` + `Underlying`/`OptionContract` ; `discover_universe(adapter,…)` |
| `collectors/raw_writer.py` | `RawMarketEvent` + `RawEventWriter` append-only |
| `snapshots/builder.py` | `build_snapshot()` pur et déterministe |
| `forwards/engine.py` | `estimate_forward()` : parité put-call + pondération + rejet MAD |
| `iv/solver.py` | `solve_iv()` Brent ; inversion américaine CRR → `IvSolveResult` |
| `surfaces/calibration.py` | SVI par tranche + spline fallback + monotonie calendaire + no-arb papillon |
| `pricing/european.py` · `american.py` | Black-76 + Greeks analytiques · arbre CRR |
| `risk/aggregation.py` · `scenarios.py` | Greeks par position/agrégats · repricing complet |
| `qc/checks.py` · `anomaly.py` | Checks QC nommés · baselines + anomalies |
| `storage/schemas.py` | `ParquetStore` (par date) + `MetadataStore` (SQLite) |
| `orchestration/jobs.py` | `build_snapshots_job()` + `run_eod_pipeline()` + `replay_pipeline()` |
| `data/live.py` | Fetchers Web API (`fetch_spot`/`fetch_option_chain`/`fetch_portfolio`) + `compute_live_analytics`/`enrich_portfolio_greeks` (purs) |
| `data/source.py` | `DataSource` — lecteur de store pur (front) |

### Pipeline EOD en deux jobs

```
1. build_snapshots_job()  — raw_market_events → market_state_snapshots
2. run_eod_pipeline()     — snapshots → forwards → IV → surface → risk → scénarios → QC
```

### Provenance / lineage

Toutes les tables dérivées portent `code_version`, `session_date`/`config_hash`, `run_id`.
Chaque job écrit un manifest JSON dans `data/manifests/`.

### Configuration (`configs/`)

Tous les seuils économiques dans les YAML. La connexion Web API est dans `broker.yaml` (section `webapi`).

### Conventions mathématiques

- Moneyness : log-moneyness `k = ln(K/F)` (vs forward) · Surface : variance totale `w = σ²T`
- Vega : par **1 point de vol** (×0.01) · Theta : par **jour calendaire** (÷365)
- Day-count : ACT/365 · Timestamps : UTC partout

## Points d'attention

- Le **gateway IBKR Web API** doit être lancé et authentifié (`https://localhost:5000`) avant le collecteur. Voir `docs/gateway_setup.md`.
- L'adaptateur utilise `cacert=false` (certif local auto-signé) → warnings TLS silencés volontairement.
- Les schemas Parquet/SQLite sont initialisés automatiquement.
- Pour ajouter un symbole : une entrée dans `configs/universe.yaml` suffit (la chaîne est découverte automatiquement via l'API).
- Greeks/IV du broker = **diagnostic uniquement** ; la plateforme recalcule ses propres greeks depuis l'IV résolue.
