# Limitations connues

Document exigé par la roadmap (Step 16). État au 2026-06-04.

## Contraintes externes (hors code)

| Limitation | Cause | Impact | Contournement |
|------------|-------|--------|---------------|
| **Options NASDAQ (QQQ, AAPL) sans quotes** | Pas d'abonnement market data OPRA sur le compte paper (erreur IBKR 10089) | Chaînes options vides pour ces sous-jacents | Activer l'abonnement OPRA (différé gratuit) dans le Client Portal IBKR |
| **Données différées 15 min** | Compte paper sans temps réel | Le spot/les quotes accusent 15 min de retard | Suffisant pour un usage analytique ; abonnement temps réel sinon |
| **Spot QQQ/AAPL via close historique** | Pas de tick différé temps réel pour ces actions | Spot = dernier close (pas intraday) | Repli automatique sur `reqHistoricalData` |

## Limitations de conception (assumées)

| Limitation | Détail | Évolution prévue |
|------------|--------|------------------|
| **Plage de strikes étroite** | ~±0,3 % autour de l'ATM (budget limité pour le pacing) | Élargir `max_strikes_per_side` une fois l'abonnement options actif |
| **Maturités courtes** | 4 échéances les plus proches (7–12 j) | Étendre la fenêtre de maturités dans `universe.yaml` |
| **Replay** | Démontrable depuis la couche brute désormais écrite, mais non rejoué sur de longues périodes | Backfill limité par le pacing/entitlement IBKR |
| **Carry constant** | Carry implicite reconstruit par parité, pas de courbe de dividendes externe | Brancher une source de dividendes si nécessaire |
| **IV américaine** | Inversion via CRR (80 pas) — plus lente que l'européenne | Acceptable pour usage diagnostic |

## Limitations d'exploitation

- Pas de scheduler système (cron/systemd) : le collecteur tourne en boucle continue, à superviser manuellement.
- Alertes légères (log/statut JSON) ; pas de routage email/Slack.
- Anomaly detection / baselines glissantes : mécanisme à enrichir au fil de l'historique accumulé.
- Pas d'environnements dev/staging/prod séparés (poste local unique).

## Ce qui N'EST PAS une limitation (validé)

- Cohérence quantitative : parité put-call au centime, identités de Greeks exactes, pricer exact au décimal, round-trip IV→prix au ¼ de cent (voir backtests).
- Stabilité de connexion : isolation collecteur/dashboard, reconnexion, throttle anti-pacing.
- Traçabilité : couche brute immuable + lineage (code_version, config_hash, run_id) sur les sorties.
