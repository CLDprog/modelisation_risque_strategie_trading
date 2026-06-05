"""
Fetcher de données IBKR live — uniquement des données réelles.

Stratégie :
  - reqMarketDataType(3) = données différées 15 min (gratuites sur compte paper)
  - Spot underlying via reqMktData
  - Chaîne d'options via reqSecDefOptParams + reqMktData par contrat
  - Positions réelles via ib.portfolio()
  - Analytics dérivées sur les quotes réelles (forwards, IV, surface)

Timeout par défaut : 4 secondes pour les quotes (paper trading data delayed)
"""
from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
from loguru import logger

from src.utils.dates import parse_ibkr_expiry, maturity_years as calc_t


# ---------------------------------------------------------------------------
# Spot underlying
# ---------------------------------------------------------------------------

def _valid_num(val) -> bool:
    """True si val est un nombre fini strictement positif."""
    try:
        return val is not None and not math.isnan(float(val)) and float(val) > 0
    except (TypeError, ValueError):
        return False


async def _qualify_stock(ib, symbol: str):
    """Qualifie un Stock via l'API async (indispensable pour les actions)."""
    from ib_insync import Stock
    contract = Stock(symbol, "SMART", "USD")
    qualified = await ib.qualifyContractsAsync(contract)
    if not qualified:
        logger.warning(f"Impossible de qualifier {symbol}")
        return None
    return qualified[0]


async def _historical_close(ib, contract, symbol: str) -> Optional[float]:
    """
    Dernier close via données historiques journalières.
    Fonctionne marché fermé et ne consomme pas de ligne de market data temps réel.
    """
    try:
        bars = await ib.reqHistoricalDataAsync(
            contract, endDateTime="", durationStr="3 D",
            barSizeSetting="1 day", whatToShow="TRADES",
            useRTH=True, formatDate=1, timeout=8,
        )
        if bars:
            close = bars[-1].close
            if _valid_num(close):
                return float(close)
    except Exception as exc:
        logger.debug(f"historical close {symbol}: {exc}")
    return None


# Codes IBKR DURS : aucune donnée ne viendra → on abandonne / on bascule en historique
_HARD_NO_DATA = {10089, 10168, 354, 162}
# Codes IBKR DOUX : "données différées/indépendantes encore actives" → on CONTINUE d'attendre
#   10090/10091/10092 = "Part of requested market data is not subscribed,
#                        subscription-independent ticks are still active"
_SOFT_DELAYED = {10090, 10091, 10092}


async def fetch_spot_async(ib, symbol: str, max_wait: float = 4.0) -> Optional[float]:
    """
    Récupère un prix de référence pour le sous-jacent.

    Stratégie :
      1. Qualifie le contrat (crucial pour les actions comme AAPL).
      2. Données différées temps réel (type 3) avec polling court.
         → Si IBKR signale "pas d'abonnement" (err 10089…), bascule aussitôt.
      3. Fallback sur le dernier close historique (marché fermé / pas d'entitlement).
    """
    try:
        contract = await _qualify_stock(ib, symbol)
        if contract is None:
            return None

        # Détecteur d'erreur DURE uniquement (10089…) → bascule historique immédiate.
        # Les codes doux (10091) sont ignorés : la donnée différée arrive quand même.
        no_sub = {"flag": False}

        def _on_error(reqId, errorCode, errorString, contract_):
            if errorCode in _HARD_NO_DATA:
                try:
                    if contract_ is not None and getattr(contract_, "conId", None) == contract.conId:
                        no_sub["flag"] = True
                except Exception:
                    no_sub["flag"] = True

        ib.errorEvent += _on_error
        ticker = None
        try:
            # --- 1. Données différées temps réel ---
            ib.reqMarketDataType(3)   # delayed (15 min)
            ticker = ib.reqMktData(contract, "", False, False)

            n_iter = max(1, int(max_wait / 0.4))
            for _ in range(n_iter):
                await asyncio.sleep(0.4)
                if no_sub["flag"]:
                    break   # pas d'abonnement → inutile d'attendre, on va à l'historique
                candidates = []
                try:
                    candidates.append(ticker.marketPrice())
                except Exception:
                    pass
                candidates += [ticker.last, ticker.close, ticker.bid, ticker.ask]
                for val in candidates:
                    if _valid_num(val):
                        return float(val)
        finally:
            ib.errorEvent -= _on_error
            if ticker is not None:
                try:
                    ib.cancelMktData(contract)
                except Exception:
                    pass

        # --- 2. Fallback : dernier close historique ---
        reason = "pas d'abonnement market data" if no_sub["flag"] else "pas de tick différé"
        logger.info(f"Spot {symbol}: {reason}, fallback historique…")
        close = await _historical_close(ib, contract, symbol)
        if close:
            return close

        logger.warning(f"Spot {symbol}: aucune donnée (ni différé ni historique)")
        return None

    except Exception as exc:
        logger.warning(f"fetch_spot_async({symbol}): {exc}")
        return None


# ---------------------------------------------------------------------------
# Option chain — requêtes réelles IBKR
# ---------------------------------------------------------------------------

async def fetch_option_chain_async(ib, symbol: str,
                                    universe_cfg: dict,
                                    qc_cfg: dict,
                                    pricing_cfg: dict) -> pd.DataFrame:
    """
    Fetches real option quotes from IBKR for the given symbol.

    Steps:
      1. Qualify underlying → get conId
      2. reqSecDefOptParams → get expiries + strikes
      3. Filter to nearby maturities (7–180 days) and ATM strikes (±6)
      4. Subscribe to market data for each option simultaneously
      5. Wait for quotes (4s)
      6. Cancel subscriptions + return DataFrame

    Returns an empty DataFrame if TWS is not connected or no quotes available.
    """
    from ib_insync import Option

    ib.reqMarketDataType(3)

    rate  = pricing_cfg.get("risk_free_rate", {}).get("value", 0.053)
    today = date.today()
    opt_cfg = universe_cfg.get("options", {})
    max_dte = opt_cfg.get("maturity_window_days", 180)
    min_dte = opt_cfg.get("min_days_to_expiry", 7)
    max_strikes_per_side = opt_cfg.get("max_strikes_per_side", 8)

    # --- 1. Qualify underlying (API async — pas de version sync depuis le loop) ---
    underlying = await _qualify_stock(ib, symbol)
    if underlying is None:
        return pd.DataFrame()
    con_id = underlying.conId

    # --- 2. Fetch spot ---
    spot = await fetch_spot_async(ib, symbol)
    if not spot or spot <= 0:
        logger.warning(f"No spot available for {symbol}")
        return pd.DataFrame()

    # --- 3. Get chain parameters (API async) ---
    try:
        chains = await ib.reqSecDefOptParamsAsync(symbol, "", "STK", con_id)
        if not chains:
            logger.warning(f"No option chain params for {symbol}")
            return pd.DataFrame()
        # Plusieurs chaînes existent (monthly + weeklies). On préfère la classe
        # standard (tradingClass == symbol) sur SMART — ses strikes encadrent le spot.
        smart_chains = [c for c in chains if c.exchange == "SMART"] or chains
        chain_params = next((c for c in smart_chains if c.tradingClass == symbol), None)
        if chain_params is None:
            # sinon, la chaîne qui couvre le mieux le spot (plus de strikes utiles)
            chain_params = max(smart_chains, key=lambda c: len(c.strikes))
    except Exception as exc:
        logger.warning(f"reqSecDefOptParams {symbol}: {exc}")
        return pd.DataFrame()

    # --- 4. Filter expiries ---
    valid_expiries = []
    for exp_str in chain_params.expirations:
        try:
            exp = parse_ibkr_expiry(exp_str)
            dte = (exp - today).days
            if min_dte <= dte <= max_dte:
                valid_expiries.append(exp)
        except Exception:
            continue
    valid_expiries = sorted(valid_expiries)[:4]   # max 4 maturités (allège la charge)

    if not valid_expiries:
        logger.warning(f"No valid expiries for {symbol}")
        return pd.DataFrame()

    # --- 5. Filter strikes around spot ---
    all_strikes = sorted([float(s) for s in chain_params.strikes if float(s) > 0])
    if not all_strikes:
        return pd.DataFrame()

    # IBKR limite ~100 lignes de market data simultanées sur compte paper.
    # On reste bien en dessous pour éviter saturation et pacing (→ déconnexion).
    MAX_MARKET_DATA_LINES = 60
    n_exp = len(valid_expiries)
    # strikes affordables par côté pour ne pas dépasser le budget
    affordable_per_side = max(2, (MAX_MARKET_DATA_LINES // (n_exp * 2 * 2)))
    strikes_per_side = min(max_strikes_per_side, affordable_per_side)

    atm_idx = min(range(len(all_strikes)), key=lambda i: abs(all_strikes[i] - spot))
    lo  = max(0, atm_idx - strikes_per_side)
    hi  = min(len(all_strikes), atm_idx + strikes_per_side + 1)
    sel_strikes = all_strikes[lo:hi]
    multiplier  = int(chain_params.multiplier or 100)

    total_lines = len(valid_expiries) * len(sel_strikes) * 2
    logger.info(
        f"Fetching {symbol}: spot={spot:.2f}, "
        f"{len(valid_expiries)} expiries × {len(sel_strikes)} strikes × 2 rights "
        f"= {total_lines} option lines (budget {MAX_MARKET_DATA_LINES})"
    )

    # --- 6. Build + qualify option contracts (batch async) ---
    trading_class = getattr(chain_params, "tradingClass", "") or ""
    raw_options = []
    for expiry in valid_expiries:
        exp_str = expiry.strftime("%Y%m%d")
        for strike in sel_strikes:
            for right in ("C", "P"):
                opt = Option(symbol, exp_str, strike, right, "SMART",
                             tradingClass=trading_class or None)
                raw_options.append((opt, expiry, strike, right))

    try:
        qualified_opts = await ib.qualifyContractsAsync(*[o[0] for o in raw_options])
    except Exception as exc:
        logger.warning(f"qualifyContracts options {symbol}: {exc}")
        qualified_opts = []

    qualified_set = {(o.conId) for o in qualified_opts if o.conId}

    # Bail-out précoce UNIQUEMENT sur erreurs DURES (10089) — pas sur les douces (10091),
    # car le différé peut quand même arriver et peupler bid/ask.
    no_sub = {"count": 0}

    def _on_error(reqId, errorCode, errorString, contract_):
        if errorCode in _HARD_NO_DATA:
            no_sub["count"] += 1

    ib.errorEvent += _on_error

    try:
        # --- 7. Subscribe to qualified options (THROTTLÉ pour éviter le pacing IBKR) ---
        # IBKR limite ~50 messages/s. Petits lots + micro-pause pour ne pas déclencher
        # de violation de pacing (qui couperait la connexion).
        subscriptions: list[tuple] = []
        for i, (opt, expiry, strike, right) in enumerate(raw_options):
            if opt.conId and opt.conId in qualified_set:
                try:
                    ticker = ib.reqMktData(opt, "", False, False)
                    subscriptions.append((ticker, opt, expiry, strike, right))
                except Exception as exc:
                    logger.debug(f"reqMktData {symbol} {strike} {right}: {exc}")
            if (i + 1) % 20 == 0:
                await asyncio.sleep(0.5)   # ~40 req/s max → sous la limite IBKR
                # Bail-out précoce : pas d'entitlement market data sur ce sous-jacent
                if no_sub["count"] >= 5:
                    logger.warning(
                        f"{symbol}: pas d'abonnement market data options "
                        f"(err 10089) — abandon de la chaîne"
                    )
                    for t, o, *_ in subscriptions:
                        try:
                            ib.cancelMktData(o)
                        except Exception:
                            pass
                    return pd.DataFrame()

        if not subscriptions:
            logger.warning(f"No option subscriptions established for {symbol}")
            return pd.DataFrame()

        # --- 8. Wait for quotes (le différé peut mettre jusqu'à ~10 s) ---
        for _ in range(28):   # jusqu'à ~11 s
            await asyncio.sleep(0.4)
            if no_sub["count"] >= 5:
                break   # blocage DUR confirmé (10089) → inutile d'attendre
            ready = sum(1 for t, *_ in subscriptions if _valid_num(t.bid) and _valid_num(t.ask))
            if ready >= len(subscriptions) * 0.6:
                break
    finally:
        ib.errorEvent -= _on_error

    # --- 9. Collect data (lecture pure des tickers) puis cancel GARANTI ---
    rows = []
    snap_ts = datetime.now(timezone.utc).isoformat()

    try:
        for ticker, opt, expiry, strike, right in subscriptions:
            # Lectures défensives : l'objet Ticker d'ib_insync n'a pas toujours
            # tous les attributs (openInterest n'existe pas → AttributeError).
            bid   = getattr(ticker, "bid", None)
            ask   = getattr(ticker, "ask", None)
            last  = getattr(ticker, "last", None)
            close = getattr(ticker, "close", None)
            vol   = getattr(ticker, "volume", None)
            oi    = (getattr(ticker, "callOpenInterest", None)
                     or getattr(ticker, "putOpenInterest", None))

            last_ok = _valid_num(last)
            T = calc_t(expiry, today)

            # Normalisation des quotes (Step 7) : on conserve TOUTE quote (full chain)
            # avec un reason_code explicite et un flag is_usable. La filtered chain
            # (is_usable=True) sert au solveur ; les rejets restent auditables.
            mid = None
            is_usable = True
            reason = None
            if _valid_num(bid) and _valid_num(ask) and ask > bid:
                mid = (bid + ask) / 2
                # spread % anormal ?
                spread_pct = (ask - bid) / mid if mid > 0 else float("inf")
                if spread_pct > 1.0:
                    is_usable, reason = False, "spread_too_wide"
            elif last_ok:
                # prix de dernière transaction (utilisable)
                mid, is_usable, reason = float(last), True, "price_from_last"
            elif _valid_num(close):
                # prix de close : référence valide hors séance (roadmap autorise le
                # fallback close). On le garde UTILISABLE pour que les analytics
                # tournent marché fermé ; le reason_code trace l'origine.
                mid, is_usable, reason = float(close), True, "price_from_close"
            else:
                is_usable, reason = False, "no_price"

            if T <= 0:
                is_usable, reason = False, "expired"

            F  = spot * math.exp(rate * T) if T > 0 else spot
            lm = math.log(strike / F) if F > 0 else float("nan")

            rows.append({
                "snapshot_ts":       snap_ts,
                "instrument_key":    f"{symbol}|OPT|{expiry.strftime('%Y%m%d')}|{strike:.4f}|{right[0]}|SMART|USD",
                "underlying_symbol": symbol,
                "expiry":            expiry.isoformat(),
                "maturity_years":    round(T, 6),
                "days_to_expiry":    (expiry - today).days,
                "strike":            strike,
                "right":             right,
                "bid":               round(bid, 4) if _valid_num(bid) else None,
                "ask":               round(ask, 4) if _valid_num(ask) else None,
                "last":              round(last, 4) if last_ok else None,
                "mid_price":         round(mid, 4) if mid is not None else None,
                "open_interest":     oi if oi and not math.isnan(oi) else None,
                "volume":            vol if vol and not math.isnan(vol) else None,
                "forward":           round(F, 4),
                "log_moneyness":     round(lm, 6) if not math.isnan(lm) else None,
                "reference_spot":    round(spot, 4),
                "is_usable":         is_usable,
                "reject_reason":     reason,
                "converged":         False,
                "data_source":       "live_ibkr",
                "multiplier":        multiplier,
            })
    finally:
        # Annulation GARANTIE de toutes les souscriptions (évite les fuites de lignes
        # market data qui finiraient par saturer la connexion).
        for idx, (ticker, opt, *_rest) in enumerate(subscriptions):
            try:
                ib.cancelMktData(opt)
            except Exception:
                pass
            if (idx + 1) % 20 == 0:
                await asyncio.sleep(0.5)

    n_usable = sum(1 for r in rows if r.get("is_usable"))
    logger.info(f"{symbol}: {n_usable} usable / {len(rows)} quotes / {len(subscriptions)} subscribed")
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ---------------------------------------------------------------------------
# Analytics dérivées sur la chaîne live
# ---------------------------------------------------------------------------

def compute_live_analytics(chain_df: pd.DataFrame, symbol: str,
                            pricing_cfg: dict, qc_cfg: dict) -> dict:
    """
    Runs forward estimation + IV solving + surface calibration on live quotes.
    Returns dict with keys: chain_df, forward_df, iv_df, surface_df.
    """
    from src.forwards.engine import estimate_forward, forward_result_to_dict
    from src.iv.solver import solve_chain_iv
    from src.surfaces.calibration import fit_surface, surface_to_dataframe

    rate     = pricing_cfg.get("risk_free_rate", {}).get("value", 0.053)
    snap_ts  = chain_df["snapshot_ts"].iloc[0] if not chain_df.empty else ""

    # --- Forwards ---
    forward_results = []
    forward_dict    = {}
    for expiry in chain_df["expiry"].unique():
        exp_rows = chain_df[chain_df["expiry"] == expiry]
        T  = exp_rows["maturity_years"].iloc[0]
        sp = exp_rows["reference_spot"].iloc[0]
        fwd_result = estimate_forward(chain_df, symbol, expiry, T, sp, rate, qc_cfg)
        forward_results.append(fwd_result)
        forward_dict[expiry] = fwd_result

    fwd_df = pd.DataFrame([forward_result_to_dict(r) for r in forward_results])

    # --- IV solving ---
    iv_rows = solve_chain_iv(chain_df, forward_dict, rate, qc_cfg)
    iv_df   = pd.DataFrame(iv_rows) if iv_rows else pd.DataFrame()

    # Update chain_df avec les IVs résolues.
    # NB : iv_df est indexé par "contract_key" (= valeur de instrument_key de la chaîne).
    if not iv_df.empty and "contract_key" in iv_df.columns:
        iv_unique = iv_df.drop_duplicates(subset="contract_key", keep="last")
        iv_map = iv_unique.set_index("contract_key")[["implied_vol", "total_variance", "converged"]].to_dict("index")
        chain_df = chain_df.copy()
        chain_df["implied_vol"]    = chain_df["instrument_key"].map(lambda k: iv_map.get(k, {}).get("implied_vol"))
        chain_df["total_variance"] = chain_df["instrument_key"].map(lambda k: iv_map.get(k, {}).get("total_variance"))
        chain_df["converged"]      = chain_df["instrument_key"].map(lambda k: iv_map.get(k, {}).get("converged", False))
        # Greeks BS
        from src.pricing.european import bs_delta, bs_gamma, bs_vega, bs_theta
        def add_greeks(row):
            iv = row.get("implied_vol")
            if iv and not math.isnan(iv) and iv > 0:
                T  = row["maturity_years"]
                F  = row["forward"]
                K  = row["strike"]
                r  = row["right"]
                S  = row["reference_spot"]
                row["delta"] = bs_delta(F, K, iv, T, rate, r, S)
                row["gamma"] = bs_gamma(F, K, iv, T, rate, S)
                row["vega"]  = bs_vega(F, K, iv, T, rate)
                row["theta"] = bs_theta(F, K, iv, T, rate, r)
            return row
        chain_df = chain_df.apply(add_greeks, axis=1)

    # --- Surface ---
    surface_df = pd.DataFrame()
    if not iv_df.empty:
        surface = fit_surface(iv_df, symbol, snap_ts, qc_cfg)
        surface_df = surface_to_dataframe(surface)

    return {
        "chain_df":   chain_df,
        "forward_df": fwd_df,
        "iv_df":      iv_df,
        "surface_df": surface_df,
    }


# ---------------------------------------------------------------------------
# Positions réelles (portefeuille paper trading)
# ---------------------------------------------------------------------------

async def fetch_portfolio_async(ib, symbol: Optional[str] = None) -> pd.DataFrame:
    """
    Fetches real paper trading positions from IBKR.
    Filters to options (and optionally to a specific underlying).
    Returns a DataFrame with position details.
    """
    try:
        portfolio_items = ib.portfolio()
        rows = []
        for item in portfolio_items:
            contract = item.contract
            if contract.secType != "OPT":
                continue
            if symbol and contract.symbol != symbol:
                continue

            rows.append({
                "portfolio_id":      "ibkr_paper",
                "contract_key":      f"{contract.symbol}|OPT|{contract.lastTradeDateOrContractMonth}|{contract.strike:.4f}|{contract.right}|{contract.exchange}|{contract.currency}",
                "underlying_symbol": contract.symbol,
                "expiry":            _parse_expiry_str(contract.lastTradeDateOrContractMonth),
                "strike":            float(contract.strike),
                "right":             contract.right,
                "quantity":          float(item.position),
                "multiplier":        int(contract.multiplier or 100),
                "market_price":      item.marketPrice,
                "market_value":      item.marketValue,
                "avg_cost":          item.averageCost,
                "unrealized_pnl":    item.unrealizedPNL,
            })
        return pd.DataFrame(rows)
    except Exception as exc:
        logger.warning(f"fetch_portfolio_async: {exc}")
        return pd.DataFrame()


def _parse_expiry_str(s: str) -> str:
    """Converts YYYYMMDD or YYYY-MM-DD to YYYY-MM-DD ISO."""
    s = str(s).replace("-", "")
    try:
        return parse_ibkr_expiry(s[:8]).isoformat()
    except Exception:
        return s


def enrich_portfolio_greeks(positions_df: pd.DataFrame, spot: float,
                            rate: float) -> pd.DataFrame:
    """
    Enrichit des positions IBKR brutes avec les Greeks.
    L'IV de chaque position est résolue depuis son prix de marché IBKR (market_price),
    puis les Greeks sont calculés via le pricer. Fonction pure réutilisable
    (collecteur + tests). Retourne un DataFrame de risk lignes par ligne.
    """
    from src.iv.solver import solve_iv
    from src.risk.aggregation import Position, compute_position_risk, position_risk_to_dataframe
    from src.utils.dates import maturity_years as calc_t
    import datetime as dt_mod

    if positions_df is None or positions_df.empty or not spot or spot <= 0:
        return pd.DataFrame()

    valuation_ts = datetime.now(timezone.utc).isoformat()
    risks = []

    for _, row in positions_df.iterrows():
        try:
            expiry = str(row["expiry"])[:10]
            T = calc_t(dt_mod.date.fromisoformat(expiry), dt_mod.date.today())
            if T <= 0:
                continue
            F      = spot * math.exp(rate * T)
            strike = float(row["strike"])
            right  = row["right"]

            mkt = row.get("market_price")
            iv  = None
            if mkt and mkt > 0:
                res = solve_iv(float(mkt), F, strike, T, rate, right)
                iv  = res.implied_vol
            if iv is None or iv <= 0:
                iv = 0.20

            iv_row = {"forward": F, "implied_vol": iv,
                      "maturity_years": T, "reference_spot": spot}
            pos = Position(
                portfolio_id      = row.get("portfolio_id", "ibkr_paper"),
                contract_key      = row["contract_key"],
                underlying_symbol = row["underlying_symbol"],
                expiry            = expiry,
                strike            = strike,
                right             = right,
                quantity          = float(row["quantity"]),
                multiplier        = int(row.get("multiplier", 100)),
            )
            risk = compute_position_risk(pos, iv_row, rate, valuation_ts)
            if risk:
                risks.append(risk)
        except Exception as exc:
            logger.debug(f"enrich position {row.get('contract_key')}: {exc}")

    return position_risk_to_dataframe(risks)
