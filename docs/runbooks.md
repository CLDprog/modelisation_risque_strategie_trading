# Runbooks Opérationnels

Infrastructure de volatilité — Procédures d'exploitation.

---

## Runbook 1 — Start of day

1. Vérifier que TWS/IB Gateway est ouvert et connecté (port 7497 paper trading).
2. **Terminal 1** — démarrer le collecteur : `python run_collector.py`
   - Vérifier les logs : "Connecté à IBKR" puis "── Cycle 0 ──".
3. **Terminal 2** — démarrer le dashboard : `python app.py` → http://localhost:8050
4. Sur la page **Collecteur**, vérifier l'état "ACTIF · CONNECTÉ" et la couverture par symbole.
5. Vérifier le badge sidebar : "Live (collecteur)".

> Le collecteur et le dashboard sont 2 process séparés (roadmap : process isolation).
> Si la connexion IBKR tombe, le collecteur reconnecte seul ; le dashboard reste affiché.

---

## Runbook 2 — Intraday monitoring

1. Observer les métriques **Market Data** : spot, nb quotes, reference_type.
2. Vérifier la page **Forward & Carry** : scores de confiance > 0.7 pour toutes les maturités.
3. Contrôler la page **Volatilité Implicite** : taux de convergence > 97%.
4. Surveiller la page **QC & Validation** : jauge > 85%, aucun FAIL rouge.
5. Si un FAIL apparaît, noter le `reason_code` et investiguer la maturité/symbole concerné.

---

## Runbook 3 — End of day

```bash
# 1. Vérifier que les raw events ont été collectés
ls data/raw/raw_market_events/dt=$(date +%Y-%m-%d)/

# 2. Builder les snapshots depuis les raw events
python -c "
from src.orchestration.jobs import build_snapshots_job
from datetime import date
build_snapshots_job(date.today())
"

# 3. Lancer le pipeline EOD complet
python -c "
from src.orchestration.jobs import run_eod_pipeline
from datetime import date
run_eod_pipeline(date.today())
"

# 4. Vérifier le manifest
ls data/manifests/
cat data/manifests/$(date +%Y-%m-%d)_eod_*.json
```

5. Recharger la page **QC** dans le front — elle doit afficher les résultats réels (badge "Analytics (EOD)").

---

## Runbook 4 — Replay / backfill

```bash
# Replay d'une plage de dates (même code path que le live)
python -c "
from src.orchestration.jobs import replay_pipeline
from datetime import date
replay_pipeline(date(2026, 5, 1), date.today())
"
```

- Les outputs sont écrits dans `data/analytics/` avec le `run_id` du replay.
- Ne pas écraser les partitions existantes : chaque run_id est unique.
- Comparer les outputs de replay avec les outputs live via les colonnes `run_id` + `code_version`.

---

## Runbook 5 — Incident response

1. Identifier la couche touchée : connectivity / data / analytics / orchestration.
2. Vérifier les logs loguru dans le terminal de l'app.
3. Vérifier la dernière entrée dans `data/manifests/`.
4. Si collector mort : redémarrer l'app, reconnecter IBKR, observer le heartbeat.
5. Si pipeline EOD échoué : vérifier les snapshots avec `build_snapshots_job()` d'abord.
6. Si données corrompues : les partitions raw sont immuables — relancer le pipeline sur les raw events.
7. Consigner : impact, cause, remédiation, follow-up.

---

## Runbook 6 — Ajouter un nouveau symbole

1. Ajouter une entrée dans `src/data/mock.py::SYMBOL_PARAMS` :
   ```python
   "NVDA": {
       "spot": 900.0, "rate": 0.053, "carry": 0.002,
       "atm_vol": 0.45, "skew": -0.07, "smile": 0.09,
       "description": "NVIDIA Corporation",
   },
   ```
2. Ajouter dans `configs/universe.yaml` :
   ```yaml
   - symbol: NVDA
     exchange: SMART
     currency: USD
     sec_type: STK
     description: "NVIDIA Corporation"
   ```
3. Redémarrer l'app → le symbole apparaît dans le sélecteur de la sidebar.

---

## Niveaux de sévérité QC

| Sévérité | Description | Action |
|----------|-------------|--------|
| Severity 1 (error, FAIL) | Collecte arrêtée, storage indisponible | Intervention immédiate |
| Severity 2 (warning, WARN) | QC dégradé sur un underlying | Intervention same-session |
| Severity 3 (info) | Tendance dégradée | Observer, tracer |
| Severity 4 | Événements informationnels | Log, revue ultérieure |
