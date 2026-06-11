# Runbooks Opérationnels

Infrastructure de volatilité EURO STOXX 50 — Procédures d'exploitation.

> Prérequis commun : le **gateway IBKR Web API** doit être lancé et authentifié sur
> `https://localhost:5000`. Voir [gateway_setup.md](gateway_setup.md).
> Données EUREX complètes **en séance** (~9h-17h30 Paris) ; l'indice cote jusqu'à 22h.
> ⚠️ Ne PAS ouvrir le portail web IBKR (ou TWS/mobile) pendant la collecte : une seule
> session de market data par identifiant.

---

## Runbook 1 — Start of day

1. Vérifier que le **gateway** est authentifié (`https://localhost:5000` → « Client login succeeds »).
2. **Terminal 1** — collecteur : `python run_collector.py`
   - Logs attendus : `Connecté à la Web API IBKR (compte DU…)` → `── Cycle 0 ──` →
     `Market data: pool de souscriptions purgé` avant chaque symbole.
3. **Terminal 2** — dashboard : `python app.py` → http://localhost:8050
4. Page **Collecteur** : « ACTIF · CONNECTÉ » apparaît **dès la connexion** (heartbeat écrit
   à chaque symbole, ~30-40 s) ; la couverture par sous-jacent se remplit en direct.
5. Premier cycle à froid : ~25-30 min (découverte secdef + caches). Cible : **50/51**
   sous-jacents (NDA = échec attendu, entitlement Nasdaq Nordic) et usable ≥ 90 %.
6. `collector_status.json → metrics` : `symbols_ok`, `usable_ratio`, `qc_fail`.

---

## Runbook 2 — Intraday monitoring

1. **Market Monitor** (page d'accueil) : bandeau 50/51, matrice de vol ATM (trous = problème
   de collecte), moniteur de dispersion (ρ̄ ≈ 0,2-0,4 plausible).
2. Page **Forward & Carry** : confiance > 0,7 sur les maturités cœur ; carry borné (check QC).
3. Page **Volatilité Implicite** : convergence > 95 % (les échecs résiduels = ailes longues
   en différé, tracés avec `failure_reason`).
4. Page **QC** : jauge > 85 % ; un FAIL → noter le `reason_code`, identifier le
   sous-jacent/maturité via le triage.
5. Signaux desk : mini-VSTOXX 30 j (page Variance) et ρ̄ (page Dispersion) — leurs
   historiques s'enrichissent à chaque cycle (`variance_history`, `dispersion_history`).

---

## Runbook 3 — End of day

```bash
# 1. Vérifier les raw events du jour
ls data/raw/raw_market_events/dt=$(date +%Y-%m-%d)/

# 2. Builder les snapshots depuis les raw events
python -c "from src.orchestration.jobs import build_snapshots_job; from datetime import date; build_snapshots_job(date.today())"

# 3. Pipeline EOD complet
python -c "from src.orchestration.jobs import run_eod_pipeline; from datetime import date; run_eod_pipeline(date.today())"

# 4. Vérifier le manifest
cat data/manifests/$(date +%Y-%m-%d)_eod_*.json
```

> Chaque écriture (live ET EOD/replay) conserve `versions/<run_id>.parquet` — rien n'est
> écrasé sans trace.

---

## Runbook 4 — Replay / backfill

```bash
python -c "from src.orchestration.jobs import replay_pipeline; from datetime import date; replay_pipeline(date(2026, 6, 1), date.today())"
```

- Outputs dans `data/analytics/` avec le `run_id` du replay (+ copie versionnée).
- Comparer replay vs live : `run_id` + `code_version` dans chaque table.

---

## Runbook 5 — Incident response (général)

1. Identifier la couche : gateway / collecteur / analytics / orchestration.
2. Logs : terminal du collecteur + `logs/vol_infra_<date>.log` (`--log-level DEBUG` si besoin).
3. **Gateway** : `not authenticated` → rafraîchir `https://localhost:5000`, se reconnecter.
4. **Collecteur mort** : relancer — il reconnecte et reprend (le statut porte `stopped_at`).
5. **Pipeline EOD échoué** : vérifier `build_snapshots_job()` d'abord.
6. **Données suspectes** : le raw est immuable → rejouer le pipeline dessus.
7. Consigner : impact, cause, remédiation. Voir `data/alerts.json` (avec escalade
   S1-S4 : niveau, owner, SLA, échéance).

---

## Runbook 5-bis — Incident MARKET DATA (snapshots sans prix)

**Symptômes** : symboles entiers à `0/N usable` (tout-ou-rien), `forward failed` en cascade,
spots en « fallback close historique » en pleine séance, logs `snapshot: plateau à 0/X prix`.

**Diagnostic dans l'ordre** (vécu le 11/06/2026, résolu en ~1h45) :
1. **Session concurrente ?** `https://localhost:5000/v1/api/iserver/auth/status` →
   champ `competing`. `true` = un autre login utilise le flux (portail web, TWS, coéquipier).
2. **Saturation des souscriptions ?** IBKR limite à ~100 lignes simultanées ; chaque snapshot
   souscrit côté serveur POUR TOUTE LA SESSION (le re-login ne purge PAS). Test décisif :
   `GET /v1/api/iserver/marketdata/unsubscribeall` puis re-fetch d'un symbole → si ça
   revit, c'était ça. *Le collecteur purge désormais avant chaque symbole — si l'incident
   revient, vérifier que les logs montrent bien les purges.*
3. **Warm-up lent ?** Le flux différé met >6 s à livrer à l'ouverture européenne (~2 s
   l'après-midi). Le warm-up adaptatif absorbe ça ; les plateaux loggés en INFO le montrent.
4. **Heure** : après 17h30, les options ACTIONS n'ont plus de cotation (fallback last/close
   tracé — comportement normal, pas un incident).

---

## Runbook 6 — Ajouter / corriger un sous-jacent

1. Entrée dans `configs/universe.yaml` (exemple réel) :
   ```yaml
   - symbol: SAN              # id INTERNE unique (étiquetage des tables)
     ibkr_symbol: SAN1        # ticker IBKR si différent (doublons, conventions IBKR)
     sec_type: STK
     exchange: SBF            # bourse du SOUS-JACENT (IBIS/SBF/AEB/BM/BVME/ENEXT.BE/HEX)
     currency: EUR
     option_exchange: MEFFRV  # (optionnel) bourse des OPTIONS si pas EUREX
     description: "Sanofi"
   ```
2. Redémarrer le collecteur — résolution du conid et découverte de la chaîne automatiques.
3. Si le spot est faux → mauvais conid : tester la résolution avec un probe secdef/search
   (chercher par NOM si le ticker est ambigu). Si 0 option EUREX → tester MEFFRV (Espagne),
   BELFOX (Belgique) via `option_exchange`.

---

## Runbook 7 — Exploitation programmée & alertes

```powershell
# Scheduler Windows : collecteur 09:05 + pipeline EOD 17:45 (jours ouvrés)
.\scripts\schedule_collector.ps1            # -Remove pour désinscrire
```
> Limite : le login navigateur du gateway reste manuel (contrainte IBKR retail).

**Alertes externes** (optionnel) : renseigner `qc.yaml → alerting` (webhook Slack-compatible
et/ou SMTP ; mot de passe via la variable d'environnement `VOL_SMTP_PASSWORD`). Sans
configuration, les alertes restent dans `data/alerts.json` (toujours écrites).

**Checklist de handover** (parcours « nouvel ingénieur » automatisé) :
```bash
python scripts/handover_check.py     # 6 points : env, store, replay, QC, docs, gateway
```

---

## Niveaux de sévérité QC (codés dans qc.yaml → escalation)

| Niveau | Statut | SLA | Action |
|--------|--------|-----|--------|
| S1 | fail | 60 min | Intervention immédiate |
| S2 | déconnexion | 120 min | Reconnexion / re-login gateway |
| S3 | warn | 24 h | Investigation same-day |
| S4 | info | — | Log, revue ultérieure |
