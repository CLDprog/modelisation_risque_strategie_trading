# Runbooks Opérationnels

Infrastructure de volatilité — Procédures d'exploitation.

> Prérequis commun : le **gateway IBKR Web API** doit être lancé et authentifié sur
> `https://localhost:5000`. Voir [gateway_setup.md](gateway_setup.md).

---

## Runbook 1 — Start of day

1. Vérifier que le **gateway Web API** est lancé et authentifié (`https://localhost:5000` → « Client login succeeds »).
2. **Terminal 1** — démarrer le collecteur : `python run_collector.py`
   - Vérifier les logs : `Connecté à la Web API IBKR (compte DU…)` puis `── Cycle 0 ──`.
3. **Terminal 2** — démarrer le dashboard : `python app.py` → http://localhost:8050
4. Sur la page **Collecteur**, vérifier l'état « ACTIF · CONNECTÉ » et la couverture par symbole.
5. Vérifier le badge sidebar : « Live (collecteur) ».

> Collecteur et dashboard sont 2 process séparés (roadmap : *process isolation*).
> Si la session IBKR tombe, le collecteur reconnecte seul ; le dashboard reste affiché.

---

## Runbook 2 — Intraday monitoring

1. Observer les métriques **Market Data** : spot, nb quotes, reference_type.
2. Page **Forward & Carry** : scores de confiance > 0.7 pour toutes les maturités.
3. Page **Volatilité Implicite** : taux de convergence > 97 %.
4. Page **QC & Validation** : jauge > 85 %, aucun FAIL rouge.
5. Si un FAIL apparaît, noter le `reason_code` et investiguer la maturité/symbole concerné.

---

## Runbook 3 — End of day

```bash
# 1. Vérifier que les raw events ont été collectés
ls data/raw/raw_market_events/dt=$(date +%Y-%m-%d)/

# 2. Builder les snapshots depuis les raw events
python -c "from src.orchestration.jobs import build_snapshots_job; from datetime import date; build_snapshots_job(date.today())"

# 3. Lancer le pipeline EOD complet
python -c "from src.orchestration.jobs import run_eod_pipeline; from datetime import date; run_eod_pipeline(date.today())"

# 4. Vérifier le manifest
cat data/manifests/$(date +%Y-%m-%d)_eod_*.json
```

5. Recharger la page **QC** dans le front — elle doit afficher les résultats réels.

---

## Runbook 4 — Replay / backfill

```bash
python -c "from src.orchestration.jobs import replay_pipeline; from datetime import date; replay_pipeline(date(2026, 5, 1), date.today())"
```

- Outputs écrits dans `data/analytics/` avec le `run_id` du replay.
- Les partitions raw sont immuables ; comparer replay vs live via `run_id` + `code_version`.

---

## Runbook 5 — Incident response

1. Identifier la couche touchée : gateway / collecteur / analytics / orchestration.
2. Vérifier les logs loguru dans le terminal du collecteur.
3. **Gateway** : si `not authenticated` → rafraîchir `https://localhost:5000` et se reconnecter.
4. **Collecteur mort** : le relancer (`python run_collector.py`) ; il reconnecte et reprend.
5. **Pipeline EOD échoué** : vérifier les snapshots avec `build_snapshots_job()` d'abord.
6. **Données suspectes** : les partitions raw sont immuables → relancer le pipeline sur les raw events.
7. Consigner : impact, cause, remédiation, follow-up. Voir aussi `data/alerts.json`.

---

## Runbook 6 — Ajouter un nouveau symbole

1. Ajouter une entrée dans `configs/universe.yaml` :
   ```yaml
   - symbol: NVDA
     exchange: SMART
     currency: USD
     sec_type: STK
     description: "NVIDIA Corporation"
   ```
2. Redémarrer le collecteur. Il **résout le contrat et découvre la chaîne d'options
   automatiquement** via l'API Web (plus aucune donnée à coder en dur). Le symbole
   apparaît ensuite dans le sélecteur de la sidebar du dashboard.

---

## Niveaux de sévérité QC

| Sévérité | Description | Action |
|----------|-------------|--------|
| Severity 1 (error, FAIL) | Collecte arrêtée, storage indisponible | Intervention immédiate |
| Severity 2 (warning, WARN) | QC dégradé sur un underlying | Intervention same-session |
| Severity 3 (info) | Tendance dégradée | Observer, tracer |
| Severity 4 | Événements informationnels | Log, revue ultérieure |
