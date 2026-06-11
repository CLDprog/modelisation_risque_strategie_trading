# Contrats d'interface (gelés)

Document exigé par la roadmap (Step 16 : « Freeze interface contracts »).
Ces signatures publiques sont stables ; toute modification est un changement de
catégorie A (voir release_checklist.md). État au 11/06/2026 (post-migration Web API,
univers EURO STOXX 50, bonus quant inclus).

## Connectivité broker (`src/connectivity/`)

```python
# broker.py — INTERFACE unique consommée par tout le code (jamais ibind directement)
class BrokerAdapter(ABC):
    connect() -> bool ; disconnect() ; is_healthy() -> bool
    resolve_underlying(symbol, exchange, currency, sec_type) -> Optional[int]
    option_chain_params(symbol, underlying_conid, min_dte, max_dte, sec_type, exchange) -> OptionChainParams
    strikes_for_expiry(conid, expiry, exchange) -> list[float]
    resolve_option_grid(conid, expiry_to_strikes, rights, exchange) -> dict[(expiry,strike,right), conid]
    snapshot(conids, field_names) -> dict[conid, dict[field, float]]   # warm-up adaptatif
    historical_close(conid) -> Optional[float]
    unsubscribe_all_marketdata() -> bool      # purge du pool (limite ~100 lignes IBKR)
    positions() -> list[BrokerPosition]
```

## Couche données (`src/data/`)

```python
# live.py — accès IBKR (collecteur uniquement, SYNCHRONE depuis la migration Web API)
fetch_spot(adapter, symbol, sec_type, exchange, currency) -> Optional[float]
fetch_option_chain(adapter, symbol, universe_cfg, qc_cfg, pricing_cfg,
                   spot=None, sec_type, exchange, currency,
                   ibkr_symbol=None, option_exchange=None) -> DataFrame
compute_live_analytics(chain_df, symbol, pricing_cfg, qc_cfg, american=False) -> dict
    # → chain_df (greeks bruts + eur_*) / forward_df / forward_diag_df / iv_df /
    #   surface_df / pricing_df
fetch_portfolio(adapter, symbol) -> DataFrame
enrich_portfolio_greeks(positions_df, spot, rate) -> DataFrame

# source.py — DataSource (lecteur de store PUR, singleton, jamais IBKR)
datasource.get_spot / get_option_chain / get_forward_curve / get_surface_grid
datasource.get_surface_parameters / get_surface_interpolated / get_forward_diagnostics
datasource.get_iv_diagnostics / get_greeks_reconciliation / get_market_snapshots
datasource.get_pricing_results / get_positions / get_portfolio / get_position_risk
datasource.get_scenarios / get_qc / get_qc_triage / get_qc_anomalies
datasource.get_dispersion() / get_variance_term(symbol)
datasource.get_variance_history(symbol, days) / get_dispersion_history(days)
datasource.get_universe_overview() -> DataFrame      # 1 ligne par sous-jacent (51)
datasource.collector_status() -> dict                # + heartbeat + metrics
```

## Moteurs quantitatifs (`src/`)

```python
# forwards/engine.py
estimate_forward(snapshot_df, symbol, expiry, T, spot, rate, cfg) -> ForwardResult
forward_candidates_to_dataframe(results) -> DataFrame          # table forward_diagnostics

# iv/solver.py
solve_iv(...) -> IvSolveResult ; solve_iv_american(...) -> IvSolveResult
solve_chain_iv(snapshot_df, forward_results, rate, cfg, american=False) -> list[dict]

# surfaces/calibration.py
fit_surface(iv_points_df, underlying, snapshot_ts, cfg) -> SurfaceFitResult
surface_to_dataframe / surface_params_to_dataframe(surface) -> DataFrame
interpolate_across_maturities(surface_df, target_maturities) -> DataFrame   # Eq.22
check_butterfly_arbitrage(k_grid, iv_grid, maturity) -> (bool, int)

# pricing/european.py & american.py
price_european(F, K, sigma, T, rate, right, spot, multiplier) -> EuropeanPricerResult
price_american_binomial(spot, K, sigma, T, rate, carry, right, steps) -> AmericanPricerResult
greeks_american(spot, K, sigma, T, rate, carry, right) -> (delta, gamma, vega, theta)

# pricing/monte_carlo.py (bonus)
simulate_gbm_paths(s0, sigma, T, carry, n_paths, n_steps, seed, method, antithetic,
                   forward_curve=None) -> ndarray           # method ∈ {pseudo, sobol}
price_asian_mc(s0, K, sigma, T, rate, carry, right, n_paths, n_steps, seed,
               method, forward_curve=None, compute_greeks=False) -> AsianMcResult
strike_ladder(res, strikes) -> list[dict]                   # mêmes chemins
delta_hedge_pnl(s0, K, sigma, T, rate, carry, right, n_paths, rebalance_steps,
                seed, method, tc_bps=0, sigma_realized=None) -> ndarray
geometric_asian_closed_form(...) -> float                   # Kemna-Vorst discret

# pricing/varswap.py (bonus)
variance_strike_from_svi(svi_params, forward, T, rate, ...) -> VarStrikeResult
variance_term_structure(params_df, forward_df, rate) -> list[VarStrikeResult]
interpolate_variance_index(term, target_days=30) -> dict    # mini-VSTOXX

# risk/aggregation.py & scenarios.py
compute_position_risk(position, iv_row, rate, valuation_ts) -> PositionRisk
aggregate_risk_frame(risk_df, group_by) -> DataFrame        # source UNIQUE d'agrégation
run_all_scenarios(positions, iv_rows_by_key, rate, scenarios) -> list[ScenarioReport]

# risk/dispersion.py (Eq.23)
basket_variance(weights, vols, corr) -> float
implied_correlation(index_vol, weights, comp_vols) -> Optional[float]
dispersion_diagnostics(iv_points, index_symbol, tenors_days, weights, ts) -> DataFrame

# risk/greeks_reconciliation.py
reconcile_chain_greeks(chain_df, rate, american) -> DataFrame
reconciliation_summary(recon_df) -> dict
```

## QC (`src/qc/`)

```python
# quote_filters.py — usabilité des quotes (version qf_v2 ; seuils UNIQUEMENT dans qc.yaml)
classify_quote(bid, ask, last, close, open_interest, maturity_years, qc_cfg)
    -> (mid, is_usable, reject_reason)

# checks.py — chaque check renvoie QcResult(status, severity, measured, threshold, reason, ctx)
check_iv_convergence / check_quote_health / check_surface_fit / check_calendar_arbitrage /
check_forward_stability / check_option_chain_coverage / check_put_call_parity /
check_greeks_reconciliation / check_carry_consistency / check_broker_greeks_reconciliation

# anomaly.py
compute_baselines(qc_history) -> DataFrame
detect_anomalies(current, baselines, z_threshold) -> DataFrame
build_triage_table(qc_results, run_id) -> DataFrame

# alert_router.py
route_alerts(alerts, alerting_cfg) -> dict     # webhook/SMTP ; no-op sans config
```

## Stockage & orchestration

```python
# storage/schemas.py
ParquetStore.write(table, df, dt, filename, atomic, version=None) -> Path
    # version=run_id → copie conservée dans versions/<run_id>.parquet
ParquetStore.read / read_range / append / list_versions / read_version
MetadataStore.save_instrument_master / save_qc_result / save_manifest

# orchestration/jobs.py
build_snapshots_job(session_date, ...) -> manifest
run_eod_pipeline(session_date, ..., positions) -> manifest
replay_pipeline(start_date, end_date, ...) -> list[manifest]
compare_replay_vs_live(live_df, replay_df, key_cols, value_cols) -> dict
```

## Tables du store (schémas)

| Table | Couche | Clés |
|-------|--------|------|
| `raw_market_events` | raw (immuable) | event_id, collector_session_id |
| `variance_history` / `dispersion_history` | raw (append-only) | ts, run_id |
| `market_state_snapshots` | analytics | snapshot_ts, instrument_key |
| `iv_points` | analytics | instrument_key (greeks bruts + eur_* + broker_*) |
| `forward_curve` / `forward_diagnostics` | analytics | underlying, expiry (, strike) |
| `surface_grid` / `surface_parameters` / `surface_interpolated` | analytics | underlying, expiry/T, k |
| `pricing_results` | analytics | instrument_key (model, market_mid, model_price, erreurs) |
| `iv_diagnostics` / `greeks_reconciliation` | analytics | instrument_key |
| `dispersion_diagnostics` / `variance_term` | analytics | tenor_days / underlying+expiry |
| `positions` / `position_risk` / `risk_aggregates` | analytics | portfolio_id (+contract_key / +underlying) |
| `scenario_results` | analytics | scenario_id, contract_key |
| `qc_results` / `qc_triage` / `qc_anomalies` | analytics | run_id, check_name, target_key |
| `instrument_master` | metadata (SQLite) | instrument_key, as_of_date |

Toutes les tables analytics portent `code_version`, `config_hash`, `run_id` — et chaque
écriture est conservée dans `versions/<run_id>.parquet` (data.parquet = dernier état).
