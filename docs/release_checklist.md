# Release checklist

Document exigé par la roadmap (Step 16, Part V — Release management).
Toute modification touchant l'économie du système doit suivre cette checklist.

## Catégories de changement (change-management)

La roadmap impose de classer chaque changement (Part XV) :

| Catégorie | Nature | Exigence |
|-----------|--------|----------|
| **A — Économique** | Pricer, bornes du solveur, paramétrisation de surface, grille de scénarios | Régression complète + validation explicite |
| **B — Opérationnel** | Seuils QC, politiques d'alerte, cadence des jobs | Tests + revue |
| **C — Non-économique** | Logs, documentation, refactor sans impact calcul | Tests unitaires |

## Checklist avant release

### 1. Code & versions
- [ ] `CODE_VERSION` incrémenté (`run_collector.py` / `orchestration/jobs.py`)
- [ ] `config_hash` recalculé automatiquement (hash des YAML)
- [ ] Configuration versionnée séparément du code

### 2. Tests
- [ ] `python -m pytest tests/ -v` — 100 % vert
- [ ] Tests de pricing (parité, intrinsèque, limites) OK
- [ ] Tests d'inversion IV (européenne + américaine) OK
- [ ] Tests no-arbitrage (calendaire + papillon) OK

### 3. Backtest de cohérence (données réelles)
- [ ] Parité put-call vérifiée (forward consistant entre strikes)
- [ ] Identité Greeks : Δcall − Δput = e^(-rT)
- [ ] Round-trip IV→prix sous tolérance (~1 tick)
- [ ] Variance totale w = σ²·T cohérente

### 4. QC
- [ ] Tous les checks `pass` ou justifiés (iv_convergence, quote_health, surface_fit, no_arbitrage, forward_stability)
- [ ] Aucun `fail` non documenté

### 5. Lineage & store
- [ ] Sorties portent `code_version`, `config_hash`, `run_id`
- [ ] Couche brute `raw_market_events` alimentée (immuabilité)
- [ ] Écritures atomiques actives

### 6. Documentation
- [ ] `known_limitations.md` à jour
- [ ] `runbooks.md` à jour
- [ ] `methodology.md` cohérent avec le code (équations)

## Rollback

En cas de problème post-release : revenir au `CODE_VERSION` précédent (les partitions du store sont identifiées par `run_id` + `code_version`, donc traçables et non écrasées rétroactivement par convention).
