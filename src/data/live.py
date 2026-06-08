"""
IBKR live data fetchers — Client Portal Web API via the broker adapter.

Only REAL data; no TWS, no sockets. Each fetcher takes a connected `BrokerAdapter`
and returns the SAME normalised shapes the rest of the stack already expects, so the
analytics (`compute_live_analytics`) and risk (`enrich_portfolio_greeks`) layers are
unchanged.

Strategy:
  - reference spot via snapshot (last → mid → close) with a historical-close fallback
  - option chain via adapter.option_chain_params + resolve_options + a batched snapshot
  - positions via adapter.positions()

These functions are synchronous: the Web API is request/response (REST), so there is
no async event loop to manage as there was with the old socket API.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
from loguru import logger

from src.utils.dates import maturity_years as calc_t
from src.connectivity.broker import BrokerAdapter


def _valid_num(val) -> bool:
    """True if val is a finite, strictly-positive number."""
    try:
        return val is not None and not math.isnan(float(val)) and float(val) > 0
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Reference spot
# ---------------------------------------------------------------------------

def fetch_spot(adapter: BrokerAdapter, symbol: str) -> Optional[float]:
    """
    Reference price for an underlying: last → mid(bid,ask) → close, then a
    historical daily close as a robust fallback (works when the market is shut).
    """
    conid = adapter.resolve_underlying(symbol)
    if conid is None:
        logger.warning(f"Impossible de résoudre le conid de {symbol}")
        return None

    snap = adapter.snapshot([conid], ["last", "close", "bid", "ask"]).get(conid, {})
    if _valid_num(snap.get("last")):
        return float(snap["last"])
    if _valid_num(snap.get("bid")) and _valid_num(snap.get("ask")):
        return (float(snap["bid"]) + float(snap["ask"])) / 2
    if _valid_num(snap.get("close")):
        return float(snap["close"])

    hclose = adapter.historical_close(conid)
    if _valid_num(hclose):
        logger.info(f"Spot {symbol}: pas de tick live, fallback close historique")
        return float(hclose)

    logger.warning(f"Spot {symbol}: aucune donnée (ni live ni historique)")
    return None


# ---------------------------------------------------------------------------
# Option chain
# ---------------------------------------------------------------------------

def fetch_option_chain(adapter: BrokerAdapter, symbol: str,
                       universe_cfg: dict, qc_cfg: dict, pricing_cfg: dict,
                       spot: Optional[float] = None) -> pd.DataFrame:
    """
    Fetch real option quotes from the IBKR Web API for `symbol`.

    Steps:
      1. resolve underlying conid + reference spot
      2. discover chain params (expiries/strikes/multiplier)
      3. keep nearby maturities + ATM strikes (budgeted line count)
      4. resolve option conids (batched) and snapshot them
      5. normalise quotes (full chain kept with is_usable + reject_reason)

    Returns the SAME DataFrame schema as before, or empty on failure.
    """
    conid = adapter.resolve_underlying(symbol)
    if conid is None:
        return pd.DataFrame()

    if spot is None:
        spot = fetch_spot(adapter, symbol)
    if not _valid_num(spot):
        logger.warning(f"No spot available for {symbol}")
        return pd.DataFrame()

    rate = pricing_cfg.get("risk_free_rate", {}).get("value", 0.053)
    today = date.today()
    opt_cfg = universe_cfg.get("options", {})
    max_dte = opt_cfg.get("maturity_window_days", 180)
    min_dte = opt_cfg.get("min_days_to_expiry", 7)
    max_strikes_per_side = opt_cfg.get("max_strikes_per_side", 8)

    chain = adapter.option_chain_params(symbol, conid, min_dte, max_dte)
    if not chain or not chain.expiries or not chain.strikes:
        logger.warning(f"No option chain params for {symbol}")
        return pd.DataFrame()

    expiries = sorted(chain.expiries)[:4]            # cap maturities (load)
    all_strikes = sorted(s for s in chain.strikes if s > 0)
    multiplier = int(chain.multiplier or 100)

    # Budget the number of option lines (keep well under broker pacing limits).
    MAX_LINES = 60
    n_exp = max(1, len(expiries))
    affordable_per_side = max(2, MAX_LINES // (n_exp * 2 * 2))
    strikes_per_side = min(max_strikes_per_side, affordable_per_side)

    atm_idx = min(range(len(all_strikes)), key=lambda i: abs(all_strikes[i] - spot))
    lo = max(0, atm_idx - strikes_per_side)
    hi = min(len(all_strikes), atm_idx + strikes_per_side + 1)
    sel_strikes = all_strikes[lo:hi]

    logger.info(f"Fetching {symbol}: spot={spot:.2f}, {len(expiries)} expiries × "
                f"{len(sel_strikes)} strikes × 2 rights")

    # Resolve the (expiry, strike, right) grid → conids (batched secdef calls).
    conid_map = adapter.resolve_options(conid, expiries, sel_strikes, ("C", "P"))
    if not conid_map:
        logger.warning(f"Could not resolve any option conid for {symbol}")
        return pd.DataFrame()

    # One batched snapshot for the whole grid.
    fields = ["bid", "ask", "last", "close", "volume", "open_interest"]
    snaps = adapter.snapshot(list(conid_map.values()), fields)

    rows = []
    snap_ts = datetime.now(timezone.utc).isoformat()
    for (expiry, strike, right), opt_conid in conid_map.items():
        s = snaps.get(opt_conid, {})
        bid, ask = s.get("bid"), s.get("ask")
        last, close = s.get("last"), s.get("close")
        vol, oi = s.get("volume"), s.get("open_interest")

        last_ok = _valid_num(last)
        T = calc_t(expiry, today)

        # Quote normalisation (Step 7): keep EVERY quote (full chain) with an
        # explicit reason_code + is_usable flag. The usable subset feeds the solver.
        mid = None
        is_usable = True
        reason = None
        if _valid_num(bid) and _valid_num(ask) and ask > bid:
            mid = (bid + ask) / 2
            spread_pct = (ask - bid) / mid if mid > 0 else float("inf")
            if spread_pct > 1.0:
                is_usable, reason = False, "spread_too_wide"
        elif last_ok:
            mid, is_usable, reason = float(last), True, "price_from_last"
        elif _valid_num(close):
            mid, is_usable, reason = float(close), True, "price_from_close"
        else:
            is_usable, reason = False, "no_price"

        if T <= 0:
            is_usable, reason = False, "expired"

        F = spot * math.exp(rate * T) if T > 0 else spot
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
            "open_interest":     oi if _valid_num(oi) else None,
            "volume":            vol if _valid_num(vol) else None,
            "forward":           round(F, 4),
            "log_moneyness":     round(lm, 6) if not math.isnan(lm) else None,
            "reference_spot":    round(spot, 4),
            "is_usable":         is_usable,
            "reject_reason":     reason,
            "converged":         False,
            "data_source":       "live_ibkr",
            "multiplier":        multiplier,
        })

    n_usable = sum(1 for r in rows if r.get("is_usable"))
    logger.info(f"{symbol}: {n_usable} usable / {len(rows)} quotes")
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ---------------------------------------------------------------------------
# Derived analytics on the live chain (PURE — no broker calls)
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
# Real positions (paper trading portfolio)
# ---------------------------------------------------------------------------

def fetch_portfolio(adapter: BrokerAdapter, symbol: Optional[str] = None) -> pd.DataFrame:
    """
    Fetch real paper-trading positions via the Web API.
    Filters to options (and optionally to a specific underlying).
    Returns a DataFrame with the same columns the risk layer expects.
    """
    rows = []
    for p in adapter.positions():
        if p.sec_type not in ("OPT", "FOP"):
            continue
        if symbol and p.underlying_symbol != symbol:
            continue
        if not p.expiry or p.strike is None or not p.right:
            continue
        try:
            exp_compact = datetime.fromisoformat(p.expiry).strftime("%Y%m%d")
        except ValueError:
            continue
        r = p.right.upper()[0]
        rows.append({
            "portfolio_id":      "ibkr_paper",
            "contract_key":      f"{p.underlying_symbol}|OPT|{exp_compact}|{float(p.strike):.4f}|{r}|SMART|USD",
            "underlying_symbol": p.underlying_symbol,
            "expiry":            p.expiry,
            "strike":            float(p.strike),
            "right":             r,
            "quantity":          float(p.quantity),
            "multiplier":        int(p.multiplier or 100),
            "market_price":      p.market_price,
            "market_value":      p.market_value,
            "avg_cost":          p.avg_cost,
            "unrealized_pnl":    p.unrealized_pnl,
        })
    return pd.DataFrame(rows)


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
