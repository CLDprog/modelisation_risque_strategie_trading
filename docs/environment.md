# Environnement & provisioning

Document exigé par la roadmap (Step 1). Mise en route sur une machine neuve.

## Prérequis

- **Python 3.11+**
- Un compte **Interactive Brokers paper** (identifiants paper)
- **Un gateway IBKR Web API** : Java 8u192+ (Client Portal Gateway) *ou* Docker (IBeam)
  → procédure complète dans **[gateway_setup.md](gateway_setup.md)**

## 1. Installation Python

```bash
cd architecture_risque
pip install -r requirements.txt
```

Dépendances clés : `ibind` + `websocket-client` (broker Web API), `numpy`, `scipy`,
`pandas`, `pyarrow`, `sqlalchemy` (SQLite), `dash` + `dash-bootstrap-components` + `plotly`
(front), `loguru`, `pyyaml`, `python-dotenv`, `pytest`.

## 2. Gateway IBKR Web API

Le code parle à IBKR via un **gateway local** sur `https://localhost:5000`, jamais via TWS.
Voir **[gateway_setup.md](gateway_setup.md)** :
- **Voie A** — Client Portal Gateway (Java) : lancer `bin\run.bat root\conf.yaml`, puis login sur `https://localhost:5000`.
- **Voie B** — IBeam (Docker) : `docker compose -f gateway/docker-compose.yml up -d`.

## 3. Données de marché

- Le compte paper fournit des **données différées** gratuites (suffisant pour l'analytique).
- Les greeks/IV renvoyés par l'API sont **calculés par le broker** (diagnostic uniquement ;
  la plateforme recalcule ses propres greeks à partir de l'IV résolue).

## 4. Configuration & secrets

- Paramètres non sensibles : `configs/*.yaml` (versionnés). Le gateway Web API est décrit dans `configs/broker.yaml` (section `webapi`).
- Secrets / spécifiques machine : `.env` (non commité, voir `.env.example`).
  - `IBKR_ACCOUNT_ID` : **optionnel** — auto-découvert depuis le gateway si laissé vide.
  - `IBEAM_ACCOUNT` / `IBEAM_PASSWORD` : **uniquement** pour l'option Docker/IBeam.

## 5. Lancement (2 process)

```bash
# Terminal 1 — collecteur (possède la session Web API, alimente le store)
python run_collector.py            # options : --interval 60 · --account-id DU…

# Terminal 2 — dashboard (lit le store)
python app.py                      # http://localhost:8050
```

## 6. Vérification (smoke test)

```bash
python scripts/bootstrap.py        # connectivité Web API de bout en bout
python -m pytest tests/ -q         # 56 tests
```

## 7. Emplacement des artefacts

```
data/
  raw/raw_market_events/dt=YYYY-MM-DD/   # couche brute immuable (append-only)
  analytics/<table>/dt=YYYY-MM-DD/       # iv_points, forward_curve, surface_grid,
                                         #   risk_aggregates, scenario_results, qc_results
  collector_status.json                  # statut du collecteur (lu par le front)
  alerts.json                            # alertes QC / connectivité
  manifests/<run_id>.json                # manifeste par run (lineage)
  metadata.db                            # SQLite (manifests, instrument_master, qc_results)
```
