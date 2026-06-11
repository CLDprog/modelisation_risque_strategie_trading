# AGENTS.md

This file provides guidance to coding agents working in this repository.

## Connexion broker — IBKR Web API (plus de TWS)

Connexion via l'**IBKR Client Portal Web API** (REST) avec `ibind`, isolée derrière un
**adaptateur broker-agnostique**. **Aucun TWS ni `ib_insync`.** Un **gateway** local
(Client Portal Gateway Java, ou IBeam Docker) doit être authentifié sur
`https://localhost:5000` — voir `docs/gateway_setup.md`.

## Commands

```bash
# Gateway d'abord (docs/gateway_setup.md), puis :
python run_collector.py            # collecteur (possède la session) · --max-cycles 1 · --log-level DEBUG
python app.py                      # dashboard (lit le store) → http://localhost:8050
python scripts/bootstrap.py        # smoke test Web API
python -m pytest tests/ -q         # 102 tests
python scripts/handover_check.py   # checklist nouvel ingénieur (6 points)
python -c "from src.orchestration.jobs import run_eod_pipeline; from datetime import date; run_eod_pipeline(date.today())"
```

## Univers & pricing

**EURO STOXX 50** : indice `ESTX50` (options OESX, **Black-76 européen**, mult 10) + **50
composantes** (options EUREX/MEFF/BELFOX, **CRR américain**, mult 100). Figé dans
`configs/universe.yaml` : `ibkr_symbol` pour les tickers ambigus (Sanofi=`SAN1`,
Nordea=`NDA FI`), `option_exchange` pour les options hors EUREX. Grille : 7 tenors
(1m→24m) × ATM±10/30Δ, call & put — pilotée par config (`target_tenors_days`,
`delta_ladder`). NDA = sans données (entitlement Nasdaq Nordic, documenté).

## Architecture

```
IBKR Web API → connectivity(adapter) → collectors → snapshots → forwards → iv → surfaces → pricing → risk → qc
```

**Règles fondamentales :** `raw_market_events` immuable/append-only, analytics recalculables
depuis le raw ; le front (`DataSource`) ne parle JAMAIS à IBKR ; le code n'importe jamais
`ibind` directement — uniquement `BrokerAdapter`.

### Couches backend (`src/`)

| Module | Rôle clé |
|--------|----------|
| `connectivity/broker.py` | `BrokerAdapter` (ABC) + types normalisés (jamais de SDK broker ici) |
| `connectivity/ibkr_webapi.py` | `IBKRWebAdapter` (ibind) : auth/tickle, conids, snapshots (warm-up ADAPTATIF, lots de 30), **purge des souscriptions**, archivage des payloads secdef, throttle/429 |
| `universe/contracts.py` | `InstrumentMaster`, clés canoniques, `discover_universe` |
| `collectors/raw_writer.py` | Couche brute append-only |
| `snapshots/builder.py` | `build_snapshot()` pur et déterministe |
| `forwards/engine.py` | Forward par parité + rejet MAD + score de confiance |
| `iv/solver.py` | Brent (européen) · `solve_iv_american` (CRR, carry du forward) |
| `surfaces/calibration.py` | SVI + no-arb + `interpolate_across_maturities` (Eq.22) |
| `pricing/european.py` · `american.py` | Black-76 + greeks · arbre CRR (greeks par nœuds) |
| `pricing/monte_carlo.py` | **Bonus** : asiatique MC (Sobol, CV Kemna-Vorst, greeks CRN), `delta_hedge_pnl` (coûts, σ réalisée) |
| `pricing/varswap.py` | **Bonus** : variance swap log-contrat, mini-VSTOXX 30j |
| `risk/aggregation.py` · `scenarios.py` | Agrégation par bucket (`position_risk`/`risk_aggregates`) · repricing complet |
| `risk/dispersion.py` | Eq.23 : corrélation implicite, table `dispersion_diagnostics` |
| `risk/greeks_reconciliation.py` | Greeks publiés vs différences finies |
| `qc/checks.py` | 10 checks nommés (dont coverage, parité, carry, broker recon) |
| `qc/quote_filters.py` | Usabilité des quotes (qf_v2) — seuils UNIQUEMENT dans `qc.yaml` |
| `qc/anomaly.py` · `alert_router.py` | Baselines MAD/anomalies · routage webhook/SMTP |
| `storage/schemas.py` | `ParquetStore` : partitions datées + **versions/<run_id>** ; `MetadataStore` SQLite |
| `orchestration/jobs.py` | Pipeline EOD + replay (same-code-path) |
| `data/live.py` · `data/source.py` | Fetchers + analytics (greeks bruts **et € monétisés**) · lecteur de store pur |

### Frontend Dash (`app.py` + `pages/`)

15 pages, **thème CLAIR professionnel** (`assets/style.css` — palette GitHub-light, densité
desk). Sidebar : sélecteur de produit (`dcc.Store("selected-symbol")` partagé) + section
**BONUS** (Monte Carlo, Variance/VSTOXX, Dispersion). Page d'accueil = Market Monitor
(matrice de vol cliquable). Toutes les pages lisent via `DataSource` (jamais IBKR).
⚠️ `app.run(use_reloader=False)` : redémarrer `app.py` après toute modif de page.

### Configuration (`configs/`)

Tous les seuils économiques dans les YAML (`load_config`) : `broker` (webapi), `universe`
(univers + grille), `qc` (quote_filters qf_v2, seuils des checks, escalade S1-S4, alerting),
`pricing` (taux EUR), `scenarios`.

### Conventions mathématiques

- Moneyness `k = ln(K/F)` (vs forward) · Surface en **variance totale** `w = σ²T`
- Vega par **1 pt de vol** · Theta par **jour calendaire** · Day-count ACT/365 · UTC partout
- Greeks € : `Δ€=Δ·mult·S`, `Γ€=Γ·mult·S²`, `ν€=ν·mult`, `Θ€=Θ·mult`
- Équations Eq.1–25 + méthodes bonus : `methodology.md`

## Pièges connus (appris en conditions réelles)

- **Souscriptions market data** : chaque snapshot souscrit côté serveur pour TOUTE la session
  (persiste entre les runs, le re-login ne purge pas) ; limite ~100 lignes → purge
  `unsubscribe_all_marketdata()` avant chaque symbole. Sans ça : lots entiers sans prix.
- **Warm-up différé** : >6 s/lot à l'ouverture européenne — warm-up adaptatif (complétude/plateau).
- **Snapshot** : ≤100 conids/appel (le dépassement renvoie un lot vide) ; lots de 30 en pratique.
- **secdef** : 429 en rafale → throttle thread-safe + retry/backoff + caches de session.
- **Lecture du store** : `pd.read_parquet(<dossier partition>)` inclut `versions/` → comptes
  doublés. Toujours lire `data.parquet` ou passer par `DataSource`/`ParquetStore.read`.
- **Une seule session market data IBKR par login** (ne pas ouvrir le portail web en collecte).
- Greeks/IV du broker = **diagnostic uniquement** (la plateforme recalcule tout).
