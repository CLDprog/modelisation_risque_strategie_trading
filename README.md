# Vol Risk Infrastructure

Infrastructure de risque de volatilité de niveau institutionnel — **Projet M1 Trading Algorithmique**.

Le projet implémente les **16 étapes** du roadmap fourni en cours : collecte de données de marché → forward / carry → volatilité implicite → surface de volatilité (SVI) → pricing → Greeks → scénarios de stress → contrôle qualité (QC), avec persistance, orchestration et observabilité.

> ### Connexion broker : IBKR **Client Portal Web API** (REST + WebSocket) — **sans TWS**
> Le projet n'utilise **plus TWS** (l'application de bureau, instable : popups, redémarrage quotidien, déconnexions). À la place, un **gateway léger** tient la session IBKR authentifiée et le code lit les données en HTTP sur `https://localhost:5000`. Deux options de gateway :
> - **Client Portal Gateway (Java)** — le plus simple à démarrer ▶ voir **[docs/gateway_setup.md](docs/gateway_setup.md)**
> - **IBeam (Docker)** — automatisé / headless, pour un usage « production » ▶ voir [gateway/docker-compose.yml](gateway/docker-compose.yml)
>
> Le code Python parle au gateway via la librairie [`ibind`](https://github.com/Voyz/ibind), isolée derrière un **adaptateur broker-agnostique** (`src/connectivity/broker.py`). Aucun autre module ne dépend du SDK broker.

---

## Architecture — 2 process découplés (roadmap : *process isolation*)

```
        [ Compte IBKR paper ]
                 ▲   login (1× via le gateway)
        ┌────────┴─────────┐
        │  GATEWAY Web API │   Client Portal Gateway (Java)  OU  IBeam (Docker)
        │  https://:5000   │
        └────────▲─────────┘
                 │ REST / WebSocket (ibind)
        ┌────────┴───────────────┐
        │  run_collector.py      │  ← POSSÈDE la session, collecte + analytics
        │  (collecteur autonome) │     tickle keep-alive, reconnexion
        └────────┬───────────────┘
                 │ écrit
        ┌────────▼───────────────────────────────────┐
        │  data/  (store)                             │
        │   raw/        events bruts immuables        │
        │   analytics/  iv_points, forward_curve,     │
        │               surface_grid, risk, scénarios │
        │   collector_status.json · metadata.db       │
        └────────┬───────────────────────────────────┘
                 │ LIT uniquement (jamais IBKR)
        ┌────────▼───────────────┐
        │  app.py  (dashboard)   │  http://localhost:8050
        └────────────────────────┘
```

**Pourquoi 2 process ?** Si l'UI plante ou rame, la collecte n'est pas affectée ; si la session IBKR tombe, le dashboard continue d'afficher les dernières données du store. Le front ne se connecte **jamais** à IBKR.

---

## Prérequis

- **Python 3.11+**
- Un compte **Interactive Brokers paper** (avec ses identifiants paper)
- **Un gateway** (au choix) :
  - **Java 8u192+** (testé avec Temurin 25) → Client Portal Gateway — *recommandé pour démarrer*
  - **ou Docker** → IBeam (auto-authentification headless)

---

## Démarrage rapide

```bash
# 1. Dépendances
cd architecture_risque
pip install -r requirements.txt

# 2. (optionnel) Configurer l'environnement
cp .env.example .env          # IBKR_ACCOUNT_ID est auto-découvert si laissé vide
```

**3. Lancer + authentifier le gateway** (détails pas-à-pas dans [docs/gateway_setup.md](docs/gateway_setup.md)) :

```bash
# Option A — Client Portal Gateway (Java) :
#   cd C:\clientportal.gw  &&  bin\run.bat root\conf.yaml
#   puis ouvrir https://localhost:5000 et se connecter (login paper)
#
# Option B — IBeam (Docker) :
#   docker compose -f gateway/docker-compose.yml up -d
```

**4. Vérifier que tout est branché (smoke test)** :

```bash
python scripts/bootstrap.py
# → Session CONNECTED, conid SPY, snapshot spot, chaîne d'options, greeks, positions, event écrit
```

**5. Lancer la collecte + le dashboard (2 terminaux)** :

```bash
# Terminal 1 — collecteur (possède la session, alimente le store)
python run_collector.py            # options : --interval 60 · --account-id DU…

# Terminal 2 — dashboard (lit le store)
python app.py                      # → http://localhost:8050
```

---

## Tests

```bash
python -m pytest tests/ -q         # 56 tests : pricing, IV solver, forward, no-arb, replay…
```

---

## Structure du projet

```
architecture_risque/
├── run_collector.py            # Process collecteur (possède la session Web API)
├── app.py                      # Point d'entrée du dashboard Dash
├── pages/                      # Pages de l'interface (1 par couche)
│   ├── connexion.py            #  Monitoring du collecteur
│   ├── universe.py · market_data.py · forward.py · implied_vol.py
│   ├── surface.py · pricing.py · greeks.py · scenarios.py · qc.py
├── src/
│   ├── connectivity/
│   │   ├── broker.py           # Interface BrokerAdapter (broker-agnostique) + types
│   │   └── ibkr_webapi.py      # IBKRWebAdapter — wrappe ibind (REST/WebSocket)
│   ├── universe/contracts.py   # InstrumentMaster, Underlying, OptionContract
│   ├── collectors/raw_writer.py# RawMarketEvent + RawEventWriter (couche brute)
│   ├── snapshots/builder.py    # Snapshots de marché déterministes
│   ├── forwards/engine.py      # Forward par parité put-call + rejet MAD
│   ├── iv/solver.py            # Inversion prix→vol (Brent ; CRR pour l'américain)
│   ├── surfaces/calibration.py # SVI par tranche + spline fallback + no-arbitrage
│   ├── pricing/european.py     # Black-76 + Greeks analytiques
│   ├── pricing/american.py     # Arbre binomial CRR
│   ├── risk/aggregation.py     # Greeks par position et agrégats
│   ├── risk/scenarios.py       # Repricing complet sous chocs (spot/vol/temps)
│   ├── qc/checks.py            # Suite de checks QC nommés
│   ├── qc/anomaly.py           # Baselines glissantes + détection d'anomalies (MAD)
│   ├── storage/schemas.py      # ParquetStore (par date) + MetadataStore (SQLite)
│   ├── orchestration/jobs.py   # Pipeline EOD + replay
│   ├── data/live.py            # Fetchers Web API (spot, chaîne, portefeuille) + analytics
│   └── data/source.py          # DataSource — lecteur de store pur (côté front)
├── configs/                    # YAML : broker, universe, qc, pricing, scenarios, environment
├── gateway/docker-compose.yml  # IBeam (option Docker du gateway)
├── scripts/bootstrap.py        # Smoke test Web API (Step 1)
├── tests/                      # 56 tests unitaires
└── docs/                       # environment · gateway_setup · runbooks · known_limitations · …
```

## Couches backend (`src/`)

| Module | Rôle clé |
|--------|----------|
| `connectivity/broker.py` | `BrokerAdapter` (ABC) : interface unique consommée par tout le code. Types normalisés. |
| `connectivity/ibkr_webapi.py` | `IBKRWebAdapter` : connexion/auth/tickle, résolution conids, snapshots (greeks), positions — via `ibind` |
| `universe/contracts.py` | `InstrumentMaster` + `Underlying`/`OptionContract` — clés canoniques, round-trip |
| `collectors/raw_writer.py` | `RawMarketEvent` + `RawEventWriter` append-only (couche brute immuable) |
| `snapshots/builder.py` | `build_snapshot()` pur et déterministe |
| `forwards/engine.py` | `estimate_forward()` : parité put-call + pondération liquidité + rejet MAD + score de confiance |
| `iv/solver.py` | `solve_iv()` (Brent bracketé) / inversion américaine (CRR) → `IvSolveResult` |
| `surfaces/calibration.py` | SVI par tranche + spline fallback + monotonie calendaire + no-arbitrage papillon |
| `pricing/european.py` · `american.py` | Black-76 + Greeks analytiques · arbre CRR |
| `risk/aggregation.py` · `scenarios.py` | Greeks par position/agrégats · repricing complet + approximation par Greeks |
| `qc/checks.py` · `anomaly.py` | Checks QC nommés · baselines + anomalies |
| `storage/schemas.py` | `ParquetStore` (partitionné par date) + `MetadataStore` (SQLite) |
| `orchestration/jobs.py` | `build_snapshots_job()` + `run_eod_pipeline()` + `replay_pipeline()` |

## Configuration (`configs/`)

Tous les seuils économiques sont dans les YAML (jamais en dur dans le code), chargés via `src/utils/config.load_config(name)` :
`broker` (gateway Web API), `universe` (sous-jacents surveillés), `qc` (filtres/seuils), `pricing` (taux, bumps), `scenarios` (grille de stress), `environment` (chemins/logs).

## Conventions mathématiques

- Moneyness : log-moneyness `k = ln(K/F)` (par rapport au **forward**, pas au spot)
- Surface : interpolée en **variance totale** `w = σ²·T`
- Vega : par **1 point de vol** (×0.01) · Theta : par **jour calendaire** (÷365)
- Day-count : **ACT/365** · Timestamps : **UTC** partout

## Documentation

| Doc | Contenu |
|-----|---------|
| [docs/gateway_setup.md](docs/gateway_setup.md) | **Installer et lancer le gateway IBKR Web** (Java ou Docker), pas-à-pas |
| [docs/environment.md](docs/environment.md) | Provisioning d'une machine neuve, secrets, artefacts |
| [docs/runbooks.md](docs/runbooks.md) | Procédures : start-of-day, intraday, end-of-day, replay, incident |
| [docs/known_limitations.md](docs/known_limitations.md) | Limitations connues et compromis assumés |
| [docs/interface_contracts.md](docs/interface_contracts.md) | Signatures gelées + schémas des tables |
| [docs/release_checklist.md](docs/release_checklist.md) | Catégories de changement + checklist de release |
| [methodology.md](methodology.md) | Cadre mathématique (équations 1–25) |

## Note de migration (TWS → Web API)

Auparavant, le projet se connectait via **TWS** et `ib_insync` (API socket). Il a été migré vers l'**IBKR Client Portal Web API** (REST/WebSocket via `ibind`) car TWS est instable pour un usage automatisé. Effet de bord positif : **les options AAPL/QQQ sont désormais accessibles** (l'ancienne limite OPRA `10089` côté TWS a disparu en données différées). Détails dans [docs/known_limitations.md](docs/known_limitations.md).
