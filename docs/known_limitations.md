# Limitations connues

Document exigé par la roadmap (Step 16). État au **2026-06-08** (après migration TWS → IBKR Web API).

## Prérequis d'exploitation (à connaître)

| Point | Détail |
|-------|--------|
| **Gateway local requis** | Le compte retail/paper accède à la Web API via un gateway local (Client Portal Gateway Java, ou IBeam Docker) sur `https://localhost:5000`. Le « zéro process local » (OAuth headless) est réservé à l'institutionnel. |
| **Re-login périodique (voie Java)** | La session gateway expire après inactivité prolongée. Le collecteur la maintient via `tickle`, mais un re-login navigateur peut être requis après une longue coupure. IBeam (Docker) automatise ce point. |
| **Données différées** | Compte paper → données **différées** (≈15 min). Suffisant pour l'analytique ; abonnement temps réel sinon. |

## Limitations de conception (assumées)

| Limitation | Détail | Évolution prévue |
|------------|--------|------------------|
| **Plage de strikes** | Fenêtre ATM budgétée (`max_strikes_per_side`) pour limiter le nombre de lignes | Élargir si besoin (la Web API supporte plus de volume) |
| **Maturités** | 4 échéances les plus proches par défaut | Étendre la fenêtre dans `universe.yaml` |
| **Carry constant** | Carry implicite reconstruit par parité, pas de courbe de dividendes externe | Brancher une source de dividendes si nécessaire |
| **IV américaine** | Inversion via CRR — plus lente que l'européenne | Acceptable pour usage diagnostic |
| **Greeks broker** | Captés via snapshot mais utilisés en diagnostic seulement | Réconciliation broker vs plateforme (backlog audit) |

## Limitations d'exploitation

- **Pas de scheduler système** (cron/systemd/Airflow) : le collecteur tourne en boucle continue, à superviser. IBeam couvre le redémarrage du gateway, pas du collecteur.
- **Alertes légères** : `data/alerts.json` (QC fail + déconnexion) ; pas de routage email/Slack.
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
