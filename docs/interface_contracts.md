# Contrats d'interface (gelés)

Document exigé par la roadmap (Step 16 : « Freeze interface contracts »).
Ces signatures publiques sont stables ; toute modification est un changement de
catégorie A (voir release_checklist.md).

## Couche données (`src/data/`)

```python
# source.py — DataSource (lecteur de store, singleton)
datasource.get_spot(symbol) -> Optional[float]
datasource.get_option_chain(symbol) -> DataFrame        # iv_points (chaîne enrichie)
datasource.get_forward_curve(symbol) -> DataFrame
datasource.get_surface_grid(symbol) -> DataFrame
datasource.get_portfolio(symbol) -> DataFrame           # risk_aggregates
datasource.get_scenarios(symbol) -> DataFrame
datasource.get_qc(symbol) -> DataFrame
datasource.collector_status() -> dict

# live.py — accès IBKR (utilisé par le collecteur uniquement)
fetch_spot_async(ib, symbol, max_wait) -> Optional[float]
fetch_option_chain_async(ib, symbol, universe_cfg, qc_cfg, pricing_cfg) -> DataFrame
compute_live_analytics(chain_df, symbol, pricing_cfg, qc_cfg) -> dict  # chain_df/forward_df/iv_df/surface_df
fetch_portfolio_async(ib, symbol) -> DataFrame
enrich_portfolio_greeks(positions_df, spot, rate) -> DataFrame
```

## Moteurs quantitatifs (`src/`)

```python
# forwards/engine.py
estimate_forward(snapshot_df, symbol, expiry, T, spot, rate, cfg) -> ForwardResult

# iv/solver.py
solve_iv(market_price, forward, strike, maturity, rate, right, ...) -> IvSolveResult
solve_iv_american(market_price, spot, strike, maturity, rate, carry, right, ...) -> IvSolveResult
solve_chain_iv(snapshot_df, forward_results, rate, cfg) -> list[dict]

# surfaces/calibration.py
fit_surface(iv_points_df, underlying, snapshot_ts, cfg) -> SurfaceFitResult
check_butterfly_arbitrage(k_grid, iv_grid, maturity) -> (bool, int)

# pricing/european.py & american.py
price_european(F, K, sigma, T, rate, right, spot, multiplier) -> EuropeanPricerResult
price_american_binomial(spot, K, sigma, T, rate, carry, right, steps) -> AmericanPricerResult

# risk/aggregation.py & scenarios.py
compute_position_risk(position, iv_row, rate, valuation_ts) -> PositionRisk
run_all_scenarios(positions, iv_rows_by_key, rate, scenarios) -> list[ScenarioReport]
```

## QC (`src/qc/`)

```python
# checks.py — chaque check renvoie QcResult(status, severity, measured_value, threshold, reason_code, context)
check_iv_convergence / check_quote_health / check_surface_fit /
check_calendar_arbitrage / check_forward_stability

# anomaly.py
compute_baselines(qc_history) -> DataFrame
detect_anomalies(current, baselines, z_threshold) -> DataFrame
build_triage_table(qc_results, run_id) -> DataFrame
```

## Orchestration (`src/orchestration/jobs.py`)

```python
build_snapshots_job(session_date, config_dir, data_dir) -> manifest
run_eod_pipeline(session_date, config_dir, data_dir, positions) -> manifest
replay_pipeline(start_date, end_date, config_dir, data_dir) -> list[manifest]
compare_replay_vs_live(live_df, replay_df, key_cols, value_cols) -> dict
```

## Tables du store (schémas)

| Table | Couche | Clés |
|-------|--------|------|
| `raw_market_events` | raw (immuable) | event_id, collector_session_id |
| `market_state_snapshots` | analytics | snapshot_ts, instrument_key |
| `iv_points` | analytics | contract_key / instrument_key |
| `forward_curve` | analytics | underlying, expiry |
| `surface_grid` | analytics | underlying, expiry, moneyness |
| `risk_aggregates` | analytics | portfolio_id, contract_key |
| `scenario_results` | analytics | scenario_id, contract_key |
| `qc_results` / `qc_triage` / `qc_anomalies` | analytics | run_id, check_name |
| `instrument_master` | metadata (SQLite) | instrument_key, as_of_date |

Toutes les tables analytics dérivées portent : `code_version`, `config_hash`, `run_id`.
