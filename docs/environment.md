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
  - `VOL_SMTP_PASSWORD` : **optionnel** — mot de passe SMTP pour le routage d'alertes
    (la config non sensible est dans `qc.yaml → alerting`).

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
python -m pytest tests/ -q         # 102 tests
python scripts/handover_check.py   # checklist « nouvel ingénieur » (6 points automatisés)
```

## 7. Emplacement des artefacts

```
data/
  raw/raw_market_events/dt=YYYY-MM-DD/   # couche brute immuable (append-only)
  raw/variance_history/  raw/dispersion_history/   # historiques de signaux (append-only)
  raw_payloads/dt=YYYY-MM-DD/            # réponses secdef brutes (évidence, JSONL)
  analytics/<table>/dt=YYYY-MM-DD/       # 18 tables (iv_points, forward_curve, surface_*,
                                         #   pricing_results, dispersion_diagnostics, qc_*, …)
       └── versions/<run_id>.parquet     # chaque run conservé (data.parquet = dernier état)
  collector_status.json                  # statut + heartbeat + métriques par cycle
  alerts.json                            # alertes QC / connectivité (escalade S1-S4)
  manifests/<run_id>.json                # manifeste par run (lineage)
  metadata.db                            # SQLite (manifests, instrument_master, qc_results)
logs/vol_infra_<date>.log                # journal du collecteur
```

> Tout `data/` et `logs/` est **hors git** (.gitignore) — les artefacts se régénèrent
> par la collecte ou le replay.
