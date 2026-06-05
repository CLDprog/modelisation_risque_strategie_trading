# Vol Risk Infrastructure

Infrastructure de risque de volatilité de niveau institutionnel — Projet M1 Trading Algorithmique.

## Vue d'ensemble

Ce projet implémente les 16 étapes du roadmap fourni en cours, en construisant une pile complète :
collecte de données → surface de volatilité → pricing → Greeks → scénarios de stress → QC.

## Stack technique

| Composant | Technologie |
|-----------|-------------|
| Connexion broker | `ib_insync` (IBKR TWS/Gateway) |
| Calcul numérique | `numpy`, `scipy` |
| Stockage | `pandas`, `pyarrow` (Parquet), `SQLite` |
| Frontend | `dash`, `dash-bootstrap-components`, `plotly` |
| Tests | `pytest` |
| Configuration | `YAML` + `.env` |

## Installation

```bash
# 1. Cloner / ouvrir le dossier
cd architecture_risque

# 2. Installer les dépendances
pip install -r requirements.txt

# 3. Configurer l'environnement
cp .env.example .env
# Éditer .env avec tes paramètres IBKR

# 4. Lancer l'app
python app.py
# Ouvrir http://localhost:8050
```

## Structure du projet

```
architecture_risque/
├── app.py                      # Point d'entrée Dash
├── pages/                      # Pages de l'interface
│   ├── connexion.py            # Page 1  — Connexion IBKR
│   ├── universe.py             # Page 2  — Instrument Master
│   ├── market_data.py          # Page 3  — Market Data
│   ├── forward.py              # Page 4  — Forward & Carry
│   ├── implied_vol.py          # Page 5  — Volatilité Implicite
│   ├── surface.py              # Page 6  — Surface de Vol (3D)
 │   ├── pricing.py              # Page 7  — Pricer interactif
│   ├── greeks.py               # Page 8  — Greeks & Risk
│   ├── scenarios.py            # Page 9  — Scénarios de stress
│   └── qc.py                   # Page 10 — QC & Validation
├── src/
│   ├── connectivity/session.py # IBKRSession — state machine reconnect
│   ├── universe/contracts.py   # InstrumentMaster — source de vérité
│   ├── collectors/raw_writer.py# Collecteur de ticks append-only
│   ├── snapshots/builder.py    # Snapshots déterministes
│   ├── forwards/engine.py      # Forward par parité put-call
│   ├── iv/solver.py            # Solveur IV (méthode de Brent)
│   ├── surfaces/calibration.py # SVI + spline fallback
│   ├── pricing/european.py     # Black-Scholes + Greeks analytiques
│   ├── pricing/american.py     # CRR binomial tree
│   ├── risk/aggregation.py     # Greeks par position et portefeuille
│   ├── risk/scenarios.py       # Moteur de scénarios (repricing complet)
│   ├── qc/checks.py            # Suite de validations nommées
│   ├── storage/schemas.py      # ParquetStore + MetadataStore SQLite
│   ├── orchestration/jobs.py   # Pipeline EOD complet
│   └── data/mock.py            # Données simulées (avant IBKR live)
├── configs/                    # Fichiers YAML de configuration
│   ├── broker.yaml             # Paramètres IBKR
│   ├── universe.yaml           # Underlyings surveillés
│   ├── qc.yaml                 # Seuils de qualité
│   ├── pricing.yaml            # Pricer et taux
│   ├── scenarios.yaml          # Grille de scénarios
│   └── environment.yaml        # Chemins et logs
├── tests/                      # 44 tests unitaires
│   ├── test_pricing.py
│   ├── test_iv_solver.py
│   └── test_forward.py
└── scripts/bootstrap.py        # Smoke test connexion IBKR
```

## Architecture en couches

```
[IBKR TWS / Gateway]
         ↓
[Connectivity]   session.py         — State machine, reconnect exponentiel
         ↓
[Universe]       contracts.py       — Instrument Master canonique
         ↓
[Collectors]     raw_writer.py      — Events bruts, append-only, immuables
         ↓
[Snapshots]      builder.py         — Snapshots déterministes (mid→last→fallback)
         ↓
[Forwards]       engine.py          — F(T) par parité put-call + MAD outlier rejection
         ↓
[IV Solver]      solver.py          — Prix → Vol Implicite (Brent bracketé)
         ↓
[Surfaces]       calibration.py     — SVI par tranche + interpolation cross-maturité
         ↓
[Pricing]        european.py        — Black-Scholes + Greeks analytiques
                 american.py        — CRR binomial tree
         ↓
[Risk]           aggregation.py     — Greeks par position et portefeuille
                 scenarios.py       — Repricing complet sous chocs
         ↓
[QC]             checks.py          — 8 checks nommés (pass/warn/fail)
[Storage]        schemas.py         — Parquet (timeseries) + SQLite (metadata)
[Orchestration]  jobs.py            — Pipeline EOD schedulé
```

## Connexion IBKR

1. Installer **TWS** depuis interactivebrokers.com → Trading → Trader Workstation
2. Se connecter au compte **paper trading**
3. Dans TWS : `Edit → Global Configuration → API → Settings`
   - ✅ Enable ActiveX and Socket Clients
   - Port : **7497**
   - ✅ Allow connections from localhost only
4. Lancer le bootstrap : `python scripts/bootstrap.py`

## Tests

```bash
python -m pytest tests/ -v
# 44 tests — pricing, IV solver, forward engine
```

## Données simulées

Tant que TWS n'est pas connecté, `src/data/mock.py` génère des données réalistes :
- SPY à 520$ avec smile de volatilité (put skew négatif typique du S&P)
- 6 maturités : 7, 14, 30, 60, 90, 180 jours
- Portefeuille short strangle pour démontrer les Greeks et scénarios
=======
# modelisation_risque_strategie_trading
création d'une architecture de modélisation de risque d'une stratégie de trading
>>>>>>> origin/main
