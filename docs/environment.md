# Environnement & provisioning

Document exigé par la roadmap (Step 1). Procédure de mise en route sur une machine neuve.

## Prérequis

- Python 3.11+
- Compte Interactive Brokers (paper trading suffit)
- TWS ou IB Gateway installé

## 1. Installation Python

```bash
cd architecture_risque
pip install -r requirements.txt
```

Dépendances clés : `ib_insync`, `numpy`, `scipy`, `pandas`, `pyarrow`, `dash`,
`dash-bootstrap-components`, `plotly`, `sqlalchemy`, `loguru`, `pytest`, `pyyaml`.

## 2. Configuration TWS / IB Gateway

1. Ouvrir TWS, se connecter au compte **paper trading**.
2. `Edit → Global Configuration → API → Settings` :
   - ✅ Enable ActiveX and Socket Clients
   - ❌ Read-Only API (décoché — pour lire le portefeuille)
   - Socket port : **7497** (paper TWS) ou **4002** (paper Gateway)
   - ✅ Allow connections from localhost only

## 3. Données de marché (gratuit)

- Le code demande les **données différées** (`reqMarketDataType(3)`), gratuites.
- Pour les options (OPRA), accepter l'agreement de données différées dans le
  **Client Portal IBKR** → Settings → Market Data Subscriptions.
- Compte paper : activer « Share market data with paper trading account ».

## 4. Convention de clientId (multi-services)

| Service | clientId | Note |
|---------|----------|------|
| Collecteur (`run_collector.py`) | 1 (auto-incrémenté si pris) | Possède la connexion |
| Diagnostic (`scripts/diagnose_spot.py`) | 99 | Ponctuel |

Le collecteur essaie automatiquement clientId+1, +2… si le sien est occupé (err 326).

## 5. Secrets

- Paramètres non sensibles : fichiers `configs/*.yaml` (versionnés).
- Connexion locale (127.0.0.1) sans secret en dur. Pour un déploiement distant,
  charger host/port via variables d'environnement (`.env`, non commité).

## 6. Lancement (2 process)

```bash
# Terminal 1 — collecteur (possède IBKR)
python run_collector.py            # options : --port 4002 --interval 60

# Terminal 2 — dashboard (lit le store)
python app.py                      # http://localhost:8050
```

## 7. Vérification (smoke test)

```bash
python scripts/bootstrap.py        # connectivité IBKR
python scripts/diagnose_spot.py    # spot par symbole
python -m pytest tests/ -q         # 50 tests
```

## 8. Emplacement des artefacts

```
data/
  raw/raw_market_events/dt=YYYY-MM-DD/   # couche brute immuable
  analytics/<table>/dt=YYYY-MM-DD/       # iv_points, forward_curve, surface_grid, risk_aggregates, scenario_results, qc_results
  collector_status.json                  # statut du collecteur (lu par le front)
  metadata.db                            # SQLite (manifests, master, qc)
```
