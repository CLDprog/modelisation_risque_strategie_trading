"""
IBKR live data fetchers — Client Portal Web API via the broker adapter.

Only REAL data; no TWS, no sockets. Each fetcher takes a connected `BrokerAdapter`
and returns the SAME normalised shapes the rest of the stack expects.

Univers EURO STOXX 50 (voir docs/specification_eurostoxx.md) :
  - grille de 12 maturites cibles -> echeance listee la plus proche
  - echelle de strikes par delta : ATM + 10/20/30 delta sur chaque aile (call & put)
  - greeks bruts ET monetises en devise (EUR)

Synchronous: the Web API is request/response (REST).
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
from loguru import logger
from scipy.stats import norm

from src.utils.dates import maturity_years as calc_t
from src.connectivity.broker import BrokerAdapter


def _valid_num(val) -> bool:
    """True if val is a finite, strictly-positive number."""
    try:
        return val is not None and not math.isnan(float(val)) and float(val) > 0
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Grille de maturites cible (jours) — voir docs/specification_eurostoxx.md.
# [1j, 3j, 10j, 3sem, 1m, 3m, 6m, 9m, 12m, 18m, 24m, 3ans]
# ---------------------------------------------------------------------------
TARGET_TENORS_DAYS = [1, 3, 10, 21, 30, 91, 182, 273, 365, 548, 730, 1095]

# Echelle de delta par aile : ATM (implicite) + 10, 20, 30 delta (call & put).
DELTA_LADDER = (0.10, 0.20, 0.30)


def _select_grid_expiries(available, today, tenors=TARGET_TENORS_DAYS):
    """Pour chaque tenor cible, prend l'echeance listee la plus proche (dedupliquee)."""
    if not available:
        return []
    chosen = []
    for tenor in tenors:
        nearest = min(available, key=lambda e: abs((e - today).days - tenor))
        if (nearest - today).days >= 0 and nearest not in chosen:
            chosen.append(nearest)
    return sorted(chosen)


def _strike_for_delta(target_abs_delta: float, right: str, F: float,
                      sigma: float, T: float) -> float:
    """
    Strike (Black-76) correspondant a un delta-forward cible, sur l'aile `right`.
    Call : Phi(d1) = delta ; Put : Phi(d1) = 1 - |delta|. Inversion ->
    K = F*exp(0.5*sigma^2*T - d1*sigma*sqrt(T)). Sert a choisir les strikes 10/20/30
    delta ; le delta reel reporte vient ensuite du pricer.
    """
    if sigma <= 0 or T <= 0 or F <= 0:
        return F
    if right.upper().startswith("C"):
        d1 = norm.ppf(target_abs_delta)
    else:
        d1 = norm.ppf(1.0 - target_abs_delta)
    return F * math.exp(0.5 * sigma * sigma * T - d1 * sigma * math.sqrt(T))


# ---------------------------------------------------------------------------
# Reference spot
# ---------------------------------------------------------------------------

def fetch_spot(adapter: BrokerAdapter, symbol: str, sec_type: str = "STK",
               exchange: str = "SMART", currency: str = "USD") -> Optional[float]:
    """
    Reference price for an underlying: last -> mid(bid,ask) -> close, then a
    historical daily close as a robust fallback (works when the market is shut).
    """
    conid = adapter.resolve_underlying(symbol, exchange, currency, sec_type)
    if conid is None:
        logger.warning(f"Impossible de resoudre le conid de {symbol}")
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

    logger.warning(f"Spot {symbol}: aucune donnee (ni live ni historique)")
    return None


def _build_quote_row(symbol, expiry, strike, right, s, spot, F, T,
                     multiplier, snap_ts, today, qc_cfg=None) -> dict:
    """Construit une ligne de quote normalisee (schema stable, full chain conservee).
    La decision d'usabilite est deleguee a la librairie de checks nommes
    (src/qc/quote_filters.py) — seuils versionnes dans configs/qc.yaml."""
    from src.qc.quote_filters import classify_quote

    bid, ask = s.get("bid"), s.get("ask")
    last, close = s.get("last"), s.get("close")
    vol, oi = s.get("volume"), s.get("open_interest")
    last_ok = _valid_num(last)

    mid, is_usable, reason = classify_quote(bid, ask, last, close, oi, T, qc_cfg or {})

    # Greeks/IV publies par le broker — DIAGNOSTIC uniquement (la plateforme recalcule
    # ses propres greeks depuis l'IV resolue) ; conserves pour la reconciliation QC.
    b_delta, b_gamma = s.get("delta"), s.get("gamma")
    b_vega, b_theta = s.get("vega"), s.get("theta")
    b_iv = s.get("implied_vol_percent")

    lm = math.log(strike / F) if F > 0 else float("nan")
    return {
        "snapshot_ts":       snap_ts,
        "instrument_key":    f"{symbol}|OPT|{expiry.strftime('%Y%m%d')}|{strike:.4f}|{right[0]}|EUREX|EUR",
        "underlying_symbol": symbol,
        "expiry":            expiry.isoformat(),
        "maturity_years":    round(T, 6),
        "days_to_expiry":    (expiry - today).days,
        "strike":            strike,
        "right":             right,
        "bid":               round(bid, 4) if _valid_num(bid) else None,
        "ask":               round(ask, 4) if _valid_num(ask) else None,
        # Volumétrie au premier niveau du carnet : nb de contrats AU bid / À l'ask
        # (la liquidité exécutable maintenant — ≠ volume déjà traité dans la journée)
        "bid_size":          s.get("bid_size") if _valid_num(s.get("bid_size")) else None,
        "ask_size":          s.get("ask_size") if _valid_num(s.get("ask_size")) else None,
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
        "broker_delta":      b_delta if _valid_num(b_delta) else None,
        "broker_gamma":      b_gamma if _valid_num(b_gamma) else None,
        "broker_vega":       b_vega if _valid_num(b_vega) else None,
        "broker_theta":      b_theta if _valid_num(b_theta) else None,
        "broker_iv":         b_iv if _valid_num(b_iv) else None,
    }


# ---------------------------------------------------------------------------
# Option chain — grille 12 maturites x echelle de delta (ATM + 10/20/30)
# ---------------------------------------------------------------------------

def fetch_option_chain(adapter: BrokerAdapter, symbol: str,
                       universe_cfg: dict, qc_cfg: dict, pricing_cfg: dict,
                       spot: Optional[float] = None, sec_type: str = "STK",
                       exchange: str = "SMART", currency: str = "USD",
                       ibkr_symbol: Optional[str] = None,
                       option_exchange: Optional[str] = None) -> pd.DataFrame:
    """
    Fetch real option quotes for `symbol` sur la grille de 12 maturites cibles et,
    pour chaque maturite, l'ATM + les strikes 10/20/30 delta de chaque aile (call & put).

    Strikes choisis PAR DELTA : strike cible analytique (sigma de reference) -> strike
    liste le plus proche (donc uniquement des contrats existants). Performance : une
    seule requete sigma + UN seul snapshot groupe par symbole.
    """
    # `ibkr_symbol` = ticker IBKR pour la RÉSOLUTION (peut différer de l'id interne
    # `symbol` en cas de doublon de ticker, ex. SAN = Sanofi ET Santander). L'étiquetage
    # (underlying_symbol, instrument_key) garde `symbol`, l'id interne unique.
    ibkr_symbol = ibkr_symbol or symbol
    conid = adapter.resolve_underlying(ibkr_symbol, exchange, currency, sec_type)
    if conid is None:
        return pd.DataFrame()

    # `option_exchange` = bourse d'options PAR SOUS-JACENT (certaines composantes ne
    # sont pas listées sur EUREX : BBVA/IBE → MEFFRV, ARGX → BELFOX).
    opt_exchange = option_exchange or universe_cfg.get("options", {}).get("exchange")

    if spot is None:
        spot = fetch_spot(adapter, ibkr_symbol, sec_type, exchange, currency)
    if not _valid_num(spot):
        logger.warning(f"No spot available for {symbol}")
        return pd.DataFrame()

    rate = pricing_cfg.get("risk_free_rate", {}).get("value", 0.025)
    today = date.today()
    opt_cfg = universe_cfg.get("options", {})
    max_dte = opt_cfg.get("maturity_window_days", 1095)
    min_dte = opt_cfg.get("min_days_to_expiry", 1)
    tenors  = opt_cfg.get("target_tenors_days") or TARGET_TENORS_DAYS
    ladder  = tuple(opt_cfg.get("delta_ladder") or DELTA_LADDER)

    chain = adapter.option_chain_params(ibkr_symbol, conid, min_dte, max_dte,
                                        sec_type, opt_exchange)
    if not chain or not chain.expiries:
        logger.warning(f"No option chain params for {symbol}")
        return pd.DataFrame()

    grid_expiries = _select_grid_expiries(chain.expiries, today, tenors)
    multiplier = int(chain.multiplier or 100)

    # sigma de reference (IV 30j du sous-jacent) — UNE requete par symbole pour la
    # selection des strikes ; le delta reel reporte vient ensuite du pricer.
    sigma_ref = adapter.snapshot([conid], ["iv30"]).get(conid, {}).get("iv30")
    if not _valid_num(sigma_ref):
        sigma_ref = 0.20

    logger.info(f"Fetching {symbol}: spot={spot:.2f}, {len(grid_expiries)} maturites, "
                f"ATM + {[int(d * 100) for d in ladder]} delta (call & put), "
                f"sigma_ref={sigma_ref:.2f}")

    # 1) Selection des strikes par delta (par echeance).
    month_strike_cache: dict = {}
    expiry_to_strikes: dict = {}  # expiry -> [strikes]
    fwd_by_expiry: dict = {}      # expiry -> (F, T)
    for expiry in grid_expiries:
        T = calc_t(expiry, today)
        if T <= 0:
            continue
        F = spot * math.exp(rate * T)
        fwd_by_expiry[expiry] = (F, T)

        mkey = expiry.strftime("%b%y").upper()
        if mkey not in month_strike_cache:
            month_strike_cache[mkey] = adapter.strikes_for_expiry(conid, expiry, opt_exchange)
        listed = sorted(s for s in month_strike_cache.get(mkey, []) if s > 0)
        if not listed:
            continue

        atm = min(listed, key=lambda s: abs(s - F))
        selected = {atm}
        for d in ladder:
            for r in ("C", "P"):
                k = _strike_for_delta(d, r, F, sigma_ref, T)
                selected.add(min(listed, key=lambda s: abs(s - k)))
        expiry_to_strikes[expiry] = sorted(selected)

    # 2) Resolution des conids EN PARALLELE (gros gain de vitesse ; cache secdef ensuite).
    conid_map = adapter.resolve_option_grid(conid, expiry_to_strikes, ("C", "P"), opt_exchange)
    if not conid_map:
        logger.warning(f"Could not resolve any option conid for {symbol}")
        return pd.DataFrame()

    # 2) UN SEUL snapshot groupe pour toutes les options du symbole (rapide).
    # Greeks broker inclus (diagnostic + réconciliation QC — roadmap Step 11).
    snaps = adapter.snapshot(list(conid_map.values()),
                             ["bid", "ask", "bid_size", "ask_size", "last", "close",
                              "volume", "open_interest",
                              "delta", "gamma", "vega", "theta", "iv"])

    # 3) Construction des lignes (schema stable, call ET put par strike).
    snap_ts = datetime.now(timezone.utc).isoformat()
    rows = []
    for (e, strike, right), oc in conid_map.items():
        F, T = fwd_by_expiry.get(e, (spot, calc_t(e, today)))
        rows.append(_build_quote_row(
            symbol, e, strike, right, snaps.get(oc, {}),
            spot, F, T, multiplier, snap_ts, today, qc_cfg=qc_cfg))

    n_usable = sum(1 for r in rows if r.get("is_usable"))
    logger.info(f"{symbol}: {n_usable} usable / {len(rows)} quotes ({len(grid_expiries)} maturites)")
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ---------------------------------------------------------------------------
# Derived analytics on the live chain (PURE — no broker calls)
# ---------------------------------------------------------------------------

def compute_live_analytics(chain_df: pd.DataFrame, symbol: str,
                            pricing_cfg: dict, qc_cfg: dict,
                            american: bool = False) -> dict:
    """
    Runs forward estimation + IV solving + surface calibration on live quotes,
    puis ajoute les greeks bruts ET monetises (EUR).
    Returns dict with keys: chain_df, forward_df, iv_df, surface_df.
    """
    from src.forwards.engine import (estimate_forward, forward_result_to_dict,
                                      forward_candidates_to_dataframe)
    from src.iv.solver import solve_chain_iv
    from src.surfaces.calibration import fit_surface, surface_to_dataframe

    rate     = pricing_cfg.get("risk_free_rate", {}).get("value", 0.025)
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
    iv_rows = solve_chain_iv(chain_df, forward_dict, rate, qc_cfg, american=american)
    iv_df   = pd.DataFrame(iv_rows) if iv_rows else pd.DataFrame()

    # Update chain_df avec les IVs resolues (indexe par contract_key = instrument_key).
    if not iv_df.empty and "contract_key" in iv_df.columns:
        iv_unique = iv_df.drop_duplicates(subset="contract_key", keep="last")
        iv_cols = [c for c in ("implied_vol", "total_variance", "converged", "forward")
                   if c in iv_unique.columns]
        iv_map = iv_unique.set_index("contract_key")[iv_cols].to_dict("index")
        chain_df = chain_df.copy()
        chain_df["implied_vol"]    = chain_df["instrument_key"].map(lambda k: iv_map.get(k, {}).get("implied_vol"))
        chain_df["total_variance"] = chain_df["instrument_key"].map(lambda k: iv_map.get(k, {}).get("total_variance"))
        chain_df["converged"]      = chain_df["instrument_key"].map(lambda k: iv_map.get(k, {}).get("converged", False))
        # Forward de PARITÉ (celui utilisé par le solveur IV) — remplace l'estimation
        # préliminaire spot·e^{rT} pour que greeks et pricing_results soient cohérents
        # avec l'IV résolue (sinon erreur de round-trip systématique).
        chain_df["forward"] = chain_df.apply(
            lambda r: iv_map.get(r["instrument_key"], {}).get("forward") or r["forward"],
            axis=1)

        from src.pricing.european import bs_delta, bs_gamma, bs_vega, bs_theta, bs_rho
        from src.pricing.american import greeks_american

        def add_greeks(row):
            iv = row.get("implied_vol")
            if iv and not math.isnan(iv) and iv > 0:
                T = row["maturity_years"]; F = row["forward"]; K = row["strike"]
                r = row["right"]; S = row["reference_spot"]
                mult = row.get("multiplier") or 1
                # Greeks bruts (en %) : delta proportion, vega /1pt vol, theta /jour.
                if american and S and S > 0 and F > 0 and T > 0:
                    # Actions = exercice américain → greeks via arbre CRR (cohérent avec
                    # le pricing). Carry dérivé du forward estimé (parité put-call).
                    carry = rate - math.log(F / S) / T
                    (row["delta"], row["gamma"], row["vega"], row["theta"],
                     row["rho"]) = greeks_american(S, K, iv, T, rate, carry, r)
                else:
                    row["delta"] = bs_delta(F, K, iv, T, rate, r, S)
                    row["gamma"] = bs_gamma(F, K, iv, T, rate, S)
                    row["vega"]  = bs_vega(F, K, iv, T, rate)
                    row["theta"] = bs_theta(F, K, iv, T, rate, r)
                    row["rho"]   = bs_rho(F, K, iv, T, rate, r)
                # Greeks monetises (en devise EUR) via le multiplicateur (10 indice / 100 actions).
                row["eur_delta"] = row["delta"] * mult * S       # cash delta (notionnel EUR)
                row["eur_gamma"] = row["gamma"] * mult * S * S    # gamma monetise
                row["eur_vega"]  = row["vega"] * mult              # EUR par point de vol
                row["eur_theta"] = row["theta"] * mult            # EUR par jour
                row["eur_rho"]   = row["rho"] * mult              # EUR par point de taux
            return row
        chain_df = chain_df.apply(add_greeks, axis=1)

    # --- Surface ---
    surface_df = pd.DataFrame()
    if not iv_df.empty:
        surface = fit_surface(iv_df, symbol, snap_ts, qc_cfg)
        surface_df = surface_to_dataframe(surface)

    # --- Pricing round-trip → table `pricing_results` (roadmap : sorties du moteur
    # de pricing persistées). Re-price chaque option à l'IV résolue avec le modèle
    # routé (Black-76 / CRR) et confronte au mid de marché : preuve d'auditabilité
    # prix↔IV (l'écart doit être ~0 puisque l'IV est inversée depuis ce même mid).
    pricing_df = _pricing_results(chain_df, symbol, rate, american, snap_ts)

    return {
        "chain_df":   chain_df,
        "forward_df": fwd_df,
        "forward_diag_df": forward_candidates_to_dataframe(forward_results),
        "iv_df":      iv_df,
        "surface_df": surface_df,
        "pricing_df": pricing_df,
    }


def _pricing_results(chain_df: pd.DataFrame, symbol: str, rate: float,
                     american: bool, snap_ts: str) -> pd.DataFrame:
    """Re-pricing de la chaîne à l'IV résolue (modèle routé) vs mid de marché."""
    from src.pricing.european import bs_price
    from src.pricing.american import price_american_binomial

    if chain_df.empty or "implied_vol" not in chain_df.columns:
        return pd.DataFrame()
    rows = []
    for _, r in chain_df.iterrows():
        iv, mid = r.get("implied_vol"), r.get("mid_price")
        F, K = r.get("forward"), r.get("strike")
        T, S = r.get("maturity_years"), r.get("reference_spot")
        if (not _valid_num(iv) or iv <= 0 or not _valid_num(mid)
                or not _valid_num(F) or not _valid_num(T) or T <= 0):
            continue
        try:
            if american and _valid_num(S) and S > 0 and F > 0:
                carry = rate - math.log(F / S) / T
                model_price = price_american_binomial(S, K, iv, T, rate, carry,
                                                      r["right"]).price
                model = "crr_american"
            else:
                model_price = bs_price(F, K, iv, T, rate, r["right"])
                model = "black76"
        except Exception:
            continue
        rows.append({
            "snapshot_ts": snap_ts,
            "instrument_key": r.get("instrument_key"),
            "underlying_symbol": symbol,
            "expiry": r.get("expiry"), "strike": K, "right": r.get("right"),
            "model": model, "forward": F, "spot": S,
            "implied_vol": iv, "maturity_years": T, "rate": rate,
            "market_mid": mid, "model_price": model_price,
            "abs_error": abs(model_price - mid),
            "rel_error": abs(model_price - mid) / mid if mid else None,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Real positions (paper trading portfolio)
# ---------------------------------------------------------------------------

def fetch_portfolio(adapter: BrokerAdapter, symbol: Optional[str] = None) -> pd.DataFrame:
    """
    Fetch real paper-trading positions via the Web API.
    Filters to options (and optionally to a specific underlying).
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
            "contract_key":      f"{p.underlying_symbol}|OPT|{exp_compact}|{float(p.strike):.4f}|{r}|EUREX|EUR",
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
    Enrichit des positions IBKR brutes avec les Greeks (IV resolue depuis market_price).
    Fonction pure reutilisable. Retourne un DataFrame de risk ligne a ligne.
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
