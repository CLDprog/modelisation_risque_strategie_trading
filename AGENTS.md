# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Commands

```bash
# Lancer l'app Dash (frontend)
python app.py
# Ouvrir http://localhost:8050

# Tests unitaires
python -m pytest tests/ -v

# Un seul test
python -m pytest tests/test_pricing.py::test_put_call_parity -v

# Test de connexion IBKR (TWS doit être ouvert)
python scripts/bootstrap.py

# Pipeline EOD complet (données historiques)
python -c "from src.orchestration.jobs import run_eod_pipeline; from datetime import date; run_eod_pipeline(date.today())"
```

## Architecture

Le projet implémente une infrastructure de risque de volatilité en 16 étapes. Les couches sont strictement séparées et ne remontent jamais vers l'amont.

```
IBKR → connectivity → collectors → snapshots → forwards → iv → surfaces → pricing → risk → qc
```

**Règle fondamentale :** les données brutes (`raw_market_events`) sont immuables et append-only. Toutes les analytics sont dérivées et recalculables depuis les raw events.

### Couches backend (`src/`)

| Module | Rôle clé |
|--------|----------|
| `connectivity/session.py` | `IBKRSession` — machine d'état 5 états, reconnect exponentiel avec jitter |
| `universe/contracts.py` | `InstrumentMaster` + `Underlying` + `OptionContract` — source de vérité unique pour les instruments |
| `collectors/raw_writer.py` | `RawMarketEvent` (dataclass) → `RawEventWriter` (buffer + flush Parquet). Aucune analytics dans les callbacks. |
| `snapshots/builder.py` | Fonctions pures : `build_snapshot()` déterministe. Même inputs → même output. |
| `forwards/engine.py` | `estimate_forward()` : parity put-call + pondération par liquidité + rejet outliers MAD |
| `iv/solver.py` | `solve_iv()` : Brent bracketé → `IvSolveResult` toujours retourné, même en échec |
| `surfaces/calibration.py` | SVI par tranche, spline fallback, check monotonicit calendaire |
| `pricing/european.py` | Black-76 + Greeks analytiques (Delta, Gamma, Vega, Theta, $Gamma, $Vega) |
| `pricing/american.py` | CRR binomial tree — `price_american_binomial()` |
| `risk/aggregation.py` | `compute_position_risk()` → agrégation par `aggregate_risk()` |
| `risk/scenarios.py` | `run_all_scenarios()` : repricing complet + approximation Eq.19 |
| `qc/checks.py` | 8 checks nommés retournant `QcResult(status, severity, measured_value, threshold, reason_code)` |
| `storage/schemas.py` | `ParquetStore` (partitionné par date) + `MetadataStore` (SQLite via SQLAlchemy) |
| `orchestration/jobs.py` | `run_eod_pipeline()` : enchaîne toutes les étapes, écrit un manifest JSON |

### Données simulées

`src/data/mock.py` génère des données SPY réalistes (spot 520$, smile put skew, 6 maturités) pour le développement sans IBKR. Le front Dash consomme ces données. Remplacé par les données live une fois TWS connecté.

### Frontend Dash (`app.py` + `pages/`)

App multi-pages Dash avec sidebar fixe. Chaque page dans `pages/` est autonome : elle importe depuis `src/data/mock` et appelle directement les fonctions de calcul `src/`. Les callbacks sont définis en bas de chaque fichier page avec `@callback`.

Thème : dark custom via `assets/style.css`. Classes CSS clés : `.metric-box`, `.formula-box`, `.card`, `.page-header`.

### Configuration (`configs/`)

Tous les seuils économiques sont dans les YAML — jamais dans le code. Chargés via `src/utils/config.load_config(name)`. Fichiers : `broker`, `universe`, `qc`, `pricing`, `scenarios`, `environment`.

### Conventions mathématiques

- Moneyness : log-moneyness `k = ln(K/F)` (par rapport au forward, pas au spot)
- Surface : interpolée en **variance totale** `w = σ²T`, pas en volatilité brute
- Vega : par **1 point de vol** (×0.01)
- Theta : par **jour calendaire** (÷365)
- Day-count : ACT/365
- Timestamps : UTC partout

## Points d'attention

- `IBKRSession` requiert TWS ou IB Gateway ouvert sur le port configuré (défaut 7497 paper TWS). Le bootstrap vérifie la connectivité avant tout.
- Les schemas Parquet et SQLite sont initialisés automatiquement au premier `ParquetStore` / `MetadataStore`.
- Les équations de référence (Eq.1–25) sont documentées dans `docs/methodology.md`.
- `src/data/mock.py` doit rester synchronisé avec les colonnes attendues par les pages Dash quand les schémas évoluent.
