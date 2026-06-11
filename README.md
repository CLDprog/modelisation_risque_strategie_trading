# Vol Risk Infrastructure — EURO STOXX 50

Infrastructure de risque de volatilité de niveau institutionnel — **Projet M1 Trading Algorithmique**.

Le projet implémente les **16 étapes** de la roadmap fournie en cours (*Industrial Roadmap for a
Volatility Infrastructure Platform v4*) sur l'univers **EURO STOXX 50** : l'indice (options OESX,
pricing **Black-76 européen**) et ses **50 sociétés composantes** (options EUREX/MEFF/BELFOX,
pricing **arbre CRR américain**). Chaîne complète : collecte de données de marché → forwards par
parité → volatilité implicite → surface SVI → pricing → greeks (bruts **et monétisés en €**) →
scénarios → contrôle qualité — avec persistance Parquet versionnée, lineage, orchestration et
observabilité.

**État de conformité** : audit V6 (11/06/2026) — **14 Conforme · 2 Conforme− · 0 Partiel · 0
Manquant** sur les 16 étapes, cadre mathématique **25/25 équations**, le tout constaté en
conditions réelles (50/51 sous-jacents collectés, 95,9 % de quotes exploitables). Voir
[audit_conformite_roadmap/](audit_conformite_roadmap/).

> ### Connexion broker : IBKR **Client Portal Web API** (REST) — **sans TWS**
> Un **gateway léger** tient la session IBKR authentifiée et le code lit les données en HTTPS sur
> `https://localhost:5000`. Deux options :
> - **Client Portal Gateway (Java)** — le plus simple ▶ **[docs/gateway_setup.md](docs/gateway_setup.md)**
> - **IBeam (Docker)** — automatisé/headless ▶ [gateway/docker-compose.yml](gateway/docker-compose.yml)
>
> Le code parle au gateway via [`ibind`](https://github.com/Voyz/ibind), isolé derrière un
> **adaptateur broker-agnostique** (`src/connectivity/broker.py`). Aucun autre module ne dépend du SDK broker.

---

## Architecture — 2 process découplés (roadmap : *process isolation*)

```
        [ Compte IBKR paper ]
                 ▲   login (1× via le gateway)
        ┌────────┴─────────┐
        │  GATEWAY Web API │   Client Portal Gateway (Java)  OU  IBeam (Docker)
        │  https://:5000   │
        └────────▲─────────┘
                 │ REST (ibind) — purge des souscriptions par symbole, warm-up adaptatif
        ┌────────┴───────────────┐
        │  run_collector.py      │  ← POSSÈDE la session : collecte 51 sous-jacents,
        │  (collecteur autonome) │     analytics, QC, signaux desk ; heartbeat ~30 s
        └────────┬───────────────┘
                 │ écrit (atomique, versionné par run_id)
        ┌────────▼────────────────────────────────────────────────┐
        │  data/  (store)                                          │
        │   raw/        events bruts immuables + historiques de    │
        │               signaux (variance_history, dispersion_…)   │
        │   raw_payloads/  réponses secdef brutes (évidence)       │
        │   analytics/  18 tables Parquet (+ versions/<run_id>)    │
        │   collector_status.json (heartbeat, métriques par cycle) │
        └────────┬────────────────────────────────────────────────┘
                 │ LIT uniquement (jamais IBKR)
        ┌────────▼───────────────┐
        │  app.py  (dashboard)   │  http://localhost:8050 — 12 pages + 3 bonus
        └────────────────────────┘
```

**Pourquoi 2 process ?** Si l'UI plante, la collecte n'est pas affectée ; si la session IBKR tombe,
le dashboard continue d'afficher les dernières données du store. Le front ne se connecte **jamais** à IBKR.

---

## L'univers EURO STOXX 50

- **Figé** dans [configs/universe.yaml](configs/universe.yaml) (composition officielle STOXX) :
  l'indice `ESTX50` + 50 composantes, toutes en EUR.
- **Grille de collecte** : 7 maturités cibles (1m, 3m, 6m, 9m, 12m, 18m, 24m) × échelle de delta
  **ATM ± 10Δ/30Δ**, call **et** put → ~70 options par sous-jacent, ~2 700 par cycle.
- Particularités gérées par config : tickers IBKR ambigus (`ibkr_symbol` : Sanofi=`SAN1`,
  Nordea=`NDA FI`, Santander désambiguïsé de Sanofi), bourses d'options hors EUREX
  (`option_exchange` : BBVA/IBE→MEFF, ARGX→BELFOX). NDA (Nordea) reste dans l'univers mais sans
  données (entitlement Nasdaq Nordic absent du compte paper — flagué par le QC, documenté).

## Tables produites (Parquet, partitionnées par date, lineage complet)

`iv_points` (chaîne enrichie : prix, IV, greeks bruts + € monétisés, greeks broker en diagnostic) ·
`forward_curve` + `forward_diagnostics` (candidats, y compris rejetés) · `surface_grid` +
`surface_parameters` (SVI a,b,ρ,m,σ + RMSE) + `surface_interpolated` (Eq.22, tenors exacts) ·
`pricing_results` (round-trip prix↔IV) · `dispersion_diagnostics` (Eq.23, corrélation implicite) ·
`variance_term` (strikes de variance swap) · `market_state_snapshots` · `iv_diagnostics` ·
`greeks_reconciliation` · `qc_results`/`qc_triage`/`qc_anomalies` · `positions`/`position_risk`/
`risk_aggregates`/`scenario_results` (si positions au compte) — plus les historiques append-only
`variance_history` et `dispersion_history` (le desk suit ses signaux à chaque cycle).

Chaque écriture conserve une copie `versions/<run_id>.parquet` ; `data.parquet` = dernier état lu
par le front. Les analytics sont **recalculables depuis le raw** (replay same-code-path).

---

## Le dashboard (15 pages)

| Page | Contenu |
|------|---------|
| **Market Monitor** (accueil) | Cross-section des 51 : matrice de vol ATM 51×7 **cliquable**, scatter niveau/pente de term structure, moniteur de dispersion (Eq.23), couverture de collecte, table de l'univers |
| Collecteur | État temps réel (heartbeat), couverture par sous-jacent qui se remplit en direct pendant le cycle |
| Instrument Master / Market Data | Référentiel et chaîne brute par produit |
| Forward & Carry | Courbe F(T), score de confiance, carry q(T) en % et en €, diagnostics par strike |
| Volatilité Implicite | Smiles superposés toutes maturités, term structure ATM, **skew RR30/BF30**, smile par maturité, diagnostics du solveur |
| Surface de Vol | 3D + heatmap (grille SVI), term structure des paramètres SVI, vérification visuelle du no-arbitrage calendaire |
| Pricing | Pricer interactif Black-76/CRR, greeks bruts et **monétisés €** (multiplicateur réel du contrat) |
| Greeks & Risk | La sortie §5 de la spec : grille greeks bruts + € par maturité×strike×C/P, **lecture desk +1 %**, **simulateur de choc Eq.19** (sliders spot/vol/horizon, theta borné par option), portefeuille |
| Scénarios / QC | Stress par repricing complet · 10 checks nommés, réconciliations (diff-finies **et** broker), triage, anomalies |
| **Bonus — Monte Carlo** | Asiatique arithmétique sous ℚ : Sobol, variate de contrôle Kemna-Vorst, greeks à aléas communs, strike ladder, drift calé sur NOTRE courbe forward, **simulateur de delta-hedge** (coûts en bps, vol réalisée vs implicite) |
| **Bonus — Variance & VSTOXX** | Réplication model-free du log-contrat : mini-VSTOXX 30j comparable à l'officiel, prime de convexité, strip théorique (SVI) vs **exécutable** (bid/ask réels), historique du signal |
| **Bonus — Dispersion** | Trade short corrélation : niveaux d'entrée ρ̄, panier vega-weighted en contrats réels, P&L vs corrélation réalisée, risque du package (gamma/theta nets €), coût d'entrée au spread |

---

## Démarrage rapide

**Prérequis** : Python 3.11+, compte IBKR paper, et un gateway (Java 8u192+ → Client Portal
Gateway, *recommandé* ; ou Docker → IBeam).

```bash
# 1. Dépendances
cd architecture_risque
pip install -r requirements.txt

# 2. Lancer + authentifier le gateway (pas-à-pas : docs/gateway_setup.md)
#    Java :  cd C:\clientportal.gw && bin\run.bat root\conf.yaml
#            puis ouvrir https://localhost:5000 et se connecter (login paper)

# 3. Smoke test de connectivité
python scripts/bootstrap.py

# 4. Collecte + dashboard (2 terminaux)
python run_collector.py            # options : --max-cycles 1 · --interval 60 · --log-level DEBUG
python app.py                      # → http://localhost:8050
```

> **En séance EUREX (~9h-17h30 Paris)** pour des données complètes — hors séance, les ailes
> peu liquides tombent en fallback last/close (tracé, jamais inventé). Compte paper = données
> **différées 15 min**. Premier cycle ~25-30 min (découverte + caches froids), suivants plus rapides.
> ⚠️ Ne pas ouvrir le portail web IBKR pendant la collecte (une seule session market data par login).

```bash
# Vérifications & exploitation
python -m pytest tests/ -q              # 102 tests
python scripts/handover_check.py        # checklist « nouvel ingénieur » automatisée (6 points)
.\scripts\schedule_collector.ps1        # (option) Planificateur Windows : collecte 09:05, EOD 17:45
python scripts/generate_audit_v6.py     # régénère l'audit de conformité Word
```

---

## Structure du projet

```
architecture_risque/
├── run_collector.py            # Collecteur : session Web API, 51 sous-jacents, analytics, QC, signaux
├── app.py                      # Dashboard Dash (thème clair desk) — lit le store uniquement
├── pages/                      # 15 pages (overview=Market Monitor, … , 3 pages bonus)
├── src/
│   ├── connectivity/           # broker.py (interface) · ibkr_webapi.py (ibind, rate-limits,
│   │                           #   purge des souscriptions, warm-up adaptatif, archivage payloads)
│   ├── universe/contracts.py   # InstrumentMaster, clés canoniques
│   ├── collectors/raw_writer.py# Couche brute append-only
│   ├── snapshots/builder.py    # Snapshots déterministes
│   ├── forwards/engine.py      # Forward par parité + rejet MAD + confiance
│   ├── iv/solver.py            # Brent (européen) · CRR (américain)
│   ├── surfaces/calibration.py # SVI + no-arb + interpolation cross-maturité (Eq.22)
│   ├── pricing/                # european.py (Black-76) · american.py (CRR)
│   │                           # monte_carlo.py (asiatique, Sobol, CV, greeks CRN, delta-hedge)
│   │                           # varswap.py (log-contrat, mini-VSTOXX)
│   ├── risk/                   # aggregation.py · scenarios.py · greeks_reconciliation.py
│   │                           # dispersion.py (Eq.23, corrélation implicite)
│   ├── qc/                     # checks.py (10 checks) · quote_filters.py (usabilité, qf_v2)
│   │                           # anomaly.py · alert_router.py (webhook/SMTP)
│   ├── storage/schemas.py      # ParquetStore (versions/<run_id>) + MetadataStore (SQLite)
│   ├── orchestration/jobs.py   # Pipeline EOD + replay
│   └── data/                   # live.py (fetchers + analytics) · source.py (lecteur store, front)
├── configs/                    # YAML : broker, universe (grille), qc (seuils, escalade, alerting),
│                               #   pricing (taux EUR), scenarios
├── scripts/                    # bootstrap (smoke) · handover_check · schedule_collector.ps1
│                               #   generate_audit_v5/v6 (Word)
├── audit_conformite_roadmap/   # Audits de conformité V2→V6 (.docx)
├── tests/                      # 102 tests
└── docs/                       # voir tableau ci-dessous
```

## Savoir opérationnel important (appris en conditions réelles)

- **Souscriptions market data** : chaque snapshot souscrit ses conids côté serveur **pour toute la
  session** (persiste entre les runs, le re-login ne purge pas) ; limite ~100 lignes simultanées →
  le collecteur purge (`unsubscribeall`) **avant chaque symbole**. Sans ça : lots entiers sans prix.
- **Warm-up du flux différé** : ~2 s/lot l'après-midi, >6 s à l'ouverture européenne → warm-up
  **adaptatif** (arrêt sur complétude/plateau), lots de 30 conids.
- **Rate-limits secdef** : throttle + retry 429 + caches de session ; snapshot ≤ 100 conids/appel.
- **Lecture du store** : lire `data.parquet` (ou via `DataSource`) — lire le **dossier** de
  partition avec pandas inclut `versions/` et double les comptes.

## Conventions mathématiques

- Moneyness : `k = ln(K/F)` (vs **forward**) · Surface en **variance totale** `w = σ²·T`
- Vega par **1 point de vol** · Theta par **jour calendaire** · Greeks € : `Δ€ = Δ·mult·S`,
  `Γ€ = Γ·mult·S²`, `ν€ = ν·mult`, `Θ€ = Θ·mult` (mult : 10 indice, 100 actions)
- Day-count **ACT/365** · Timestamps **UTC** · Taux EUR continu (configs/pricing.yaml)

## Documentation

| Doc | Contenu |
|-----|---------|
| [docs/specification_eurostoxx.md](docs/specification_eurostoxx.md) | **La spec du professeur (fait foi)** : univers, grille, sorties exigées |
| [methodology.md](methodology.md) | Cadre mathématique Eq.1–25 + méthodes bonus (MC, variance swap, dispersion, hedge) |
| [docs/gateway_setup.md](docs/gateway_setup.md) | Installer et lancer le gateway IBKR Web (Java ou Docker) |
| [docs/environment.md](docs/environment.md) | Provisioning machine neuve, secrets, artefacts |
| [docs/runbooks.md](docs/runbooks.md) | Procédures : start-of-day, intraday, EOD, replay, incidents (dont market data) |
| [docs/known_limitations.md](docs/known_limitations.md) | Limitations connues, couverture de l'univers, compromis assumés |
| [docs/interface_contracts.md](docs/interface_contracts.md) | Signatures gelées + schémas des tables |
| [docs/release_checklist.md](docs/release_checklist.md) | Checklist de release |
| [audit_conformite_roadmap/](audit_conformite_roadmap/) | Audits de conformité V2→V6 vs la roadmap (Word, régénérables) |

## Historique des choix structurants

- **TWS → Web API** : TWS (socket, `ib_insync`) abandonné pour l'IBKR Client Portal Web API
  (instabilité, popups) ; gateway léger + `tickle` keep-alive.
- **Pivot d'univers** : SPY/QQQ/AAPL → EURO STOXX 50 (demande du professeur, 08/06).
- **Grille réduite assumée** : 7 tenors × ATM±10/30Δ au lieu de 12 tenors × ±10/20/30Δ —
  déviation documentée, réversible par configuration seule.
