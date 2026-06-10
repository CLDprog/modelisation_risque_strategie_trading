# Limitations connues

Document exigé par la roadmap (Step 16). État au **2026-06-10** (univers EURO STOXX 50, 51 sous-jacents).

## Univers EURO STOXX 50 — couverture données (diagnostic live 2026-06-10)

| Sous-jacent | Situation | Traitement |
|-------------|-----------|------------|
| **NDA (Nordea)** | Conid HEX correct (ticker IBKR `NDA FI`) mais **aucune donnée** (spot/close vides) : le compte paper n'a pas l'entitlement **Nasdaq Nordic**. Options uniquement sur OMS Stockholm en **SEK** (rien sur EUREX). | Gardé dans l'univers (les 50 composantes y sont) ; collecte flaguée indisponible par le QC. Solution éventuelle : souscrire les données Nasdaq Nordic dans la gestion de compte IBKR. |
| **BBVA, IBE** | Pas d'options EUREX via IBKR ; options sur **MEFF** (`MEFFRV`). BBVA : 2 expiries seulement → couverture partielle de la grille (flaguée par le QC coverage). | `option_exchange: MEFFRV` dans `universe.yaml` (déviation « options EUREX » de la spec, documentée). |
| **ARGX (Argenx)** | Pas d'options EUREX ; options sur **BELFOX** (dérivés Euronext Bruxelles, 6 expiries). | `option_exchange: BELFOX` dans `universe.yaml` (déviation documentée). |
| **SAN (Sanofi)** | Le ticker IBKR de Sanofi sur SBF est **`SAN1`** (`SAN` seul résout l'ADR Santander NYSE). | `ibkr_symbol: SAN1` — résolu, 14 expiries EUREX. |

## Prérequis d'exploitation (à connaître)

| Point | Détail |
|-------|--------|
| **Gateway local requis** | Le compte retail/paper accède à la Web API via un gateway local (Client Portal Gateway Java, ou IBeam Docker) sur `https://localhost:5000`. Le « zéro process local » (OAuth headless) est réservé à l'institutionnel. |
| **Re-login périodique (voie Java)** | La session gateway expire après inactivité prolongée. Le collecteur la maintient via `tickle`, mais un re-login navigateur peut être requis après une longue coupure. IBeam (Docker) automatise ce point. |
| **Données différées** | Compte paper → données **différées** (≈15 min). Suffisant pour l'analytique ; abonnement temps réel sinon. |

## Limitations de conception (assumées)

| Limitation | Détail | Évolution prévue |
|------------|--------|------------------|
| **Poids de dispersion (Eq.23)** | La pondération free-float officielle STOXX n'est pas disponible via IBKR → **poids égaux** par défaut dans `dispersion_diagnostics` (biais : les grosses capitalisations peu volatiles sont sous-pondérées). | Champ `weight` par sous-jacent dans `universe.yaml` (source STOXX/manuelle) — le code le prend déjà en compte. |
| **Plage de strikes** | Fenêtre ATM budgétée (`max_strikes_per_side`) pour limiter le nombre de lignes | Élargir si besoin (la Web API supporte plus de volume) |
| **Maturités** | 4 échéances les plus proches par défaut | Étendre la fenêtre dans `universe.yaml` |
| **Carry constant** | Carry implicite reconstruit par parité, pas de courbe de dividendes externe | Brancher une source de dividendes si nécessaire |
| **IV américaine** | Inversion via CRR — plus lente que l'européenne | Acceptable pour usage diagnostic |
| **Greeks broker** | Captés via snapshot mais utilisés en diagnostic seulement | Réconciliation broker vs plateforme (backlog audit) |

## Limitations d'exploitation

- **Scheduler système** : `scripts/schedule_collector.ps1` enregistre le collecteur (09:05) et le pipeline EOD (17:45) dans le **Planificateur de tâches Windows** (équivalent cron sur ce poste). Limite restante : le login navigateur du gateway reste manuel (1×/session) — un échec d'authentification arrête proprement le collecteur et reste visible dans `collector_status.json`.
- **Alertes** : `data/alerts.json` enrichi de la politique d'**escalade S1–S4** (niveau, owner, SLA, échéance — section `escalation` de qc.yaml) ; **routage externe codé** (`src/qc/alert_router.py` : webhook Slack-compatible + email SMTP), inactif tant que `alerting.webhook_url`/SMTP ne sont pas renseignés (mot de passe via `VOL_SMTP_PASSWORD`).
- **Quote age** : `max_quote_age_seconds` n'est pas mesurable via le snapshot REST (pas d'horodatage par quote) — documenté dans `quote_filters.py`, volontairement non simulé.
- **Poste local unique** : pas d'environnements dev/staging/prod séparés.

## Résolu par la migration Web API

- ✅ **Stabilité de connexion** : plus de TWS instable ; gateway léger + `tickle` keep-alive + reconnexion.
- ✅ **Options AAPL / QQQ** : désormais **accessibles** en données différées via la Web API.
  L'ancienne erreur OPRA `10089` (côté TWS) a disparu. (QQQ peut rester *thin* hors séance.)

## Ce qui N'EST PAS une limitation (validé)

- Cohérence quantitative : parité put-call au centime, identités de Greeks exactes, round-trip IV→prix précis.
- Isolation 2-process (collecteur/dashboard) : un incident UI n'affecte pas la collecte.
- Traçabilité : couche brute immuable + lineage (`code_version`, `config_hash`, `run_id`) sur les sorties.
- Adaptateur broker-agnostique : le code ne dépend pas du SDK ; broker swappable / mockable pour les tests.
