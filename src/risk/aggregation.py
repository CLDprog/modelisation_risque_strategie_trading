"""
Greeks aggregation and per-position risk analytics (Step 11).

Merges positions with pricing analytics snapshots and computes:
  - Line-level: price, delta, gamma, vega, theta, dollar_gamma, dollar_vega
  - Aggregate: by underlying, maturity, portfolio
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime

import pandas as pd
import numpy as np
from loguru import logger

from src.pricing.european import price_european, EuropeanPricerResult


# ---------------------------------------------------------------------------
# Position dataclass
# ---------------------------------------------------------------------------

@dataclass
class Position:
    portfolio_id: str
    contract_key: str
    underlying_symbol: str
    expiry: str
    strike: float
    right: str
    quantity: float             # signed: positive = long, negative = short
    multiplier: int = 100


# ---------------------------------------------------------------------------
# Line-level risk
# ---------------------------------------------------------------------------

@dataclass
class PositionRisk:
    portfolio_id: str
    contract_key: str
    underlying_symbol: str
    expiry: str
    strike: float
    right: str
    quantity: float
    multiplier: int
    spot: float
    forward: float
    implied_vol: float
    maturity_years: float
    rate: float
    # Unitized Greeks (per contract)
    price: float
    delta: float
    gamma: float
    vega: float
    theta: float
    dollar_gamma: float
    dollar_vega: float
    # Monetized (× quantity × multiplier)
    pnl_approx: float
    portfolio_delta: float
    portfolio_gamma: float
    portfolio_vega: float
    portfolio_theta: float
    portfolio_dollar_gamma: float
    portfolio_dollar_vega: float
    valuation_ts: str


def compute_position_risk(position: Position, iv_row: dict,
                          rate: float, valuation_ts: str) -> Optional[PositionRisk]:
    """Compute risk for one position given a solved IV row."""
    try:
        pricer_result = price_european(
            forward=iv_row["forward"],
            strike=position.strike,
            sigma=iv_row["implied_vol"],
            maturity=iv_row["maturity_years"],
            rate=rate,
            right=position.right,
            spot=iv_row.get("reference_spot", iv_row["forward"]),
            multiplier=position.multiplier,
        )
    except Exception as exc:
        logger.warning(f"Pricing failed for {position.contract_key}: {exc}")
        return None

    q = position.quantity
    mult = position.multiplier

    return PositionRisk(
        portfolio_id=position.portfolio_id,
        contract_key=position.contract_key,
        underlying_symbol=position.underlying_symbol,
        expiry=position.expiry,
        strike=position.strike,
        right=position.right,
        quantity=q,
        multiplier=mult,
        spot=pricer_result.spot,
        forward=pricer_result.forward,
        implied_vol=pricer_result.sigma,
        maturity_years=pricer_result.maturity_years,
        rate=rate,
        price=pricer_result.price,
        delta=pricer_result.delta,
        gamma=pricer_result.gamma,
        vega=pricer_result.vega,
        theta=pricer_result.theta,
        dollar_gamma=pricer_result.dollar_gamma,
        dollar_vega=pricer_result.dollar_vega,
        # Monetize by quantity and multiplier
        pnl_approx=pricer_result.price * q * mult,
        portfolio_delta=pricer_result.delta * q * mult,
        portfolio_gamma=pricer_result.gamma * q * mult,
        portfolio_vega=pricer_result.vega * q * mult,
        portfolio_theta=pricer_result.theta * q * mult,
        portfolio_dollar_gamma=pricer_result.dollar_gamma * q,
        portfolio_dollar_vega=pricer_result.dollar_vega * q,
        valuation_ts=valuation_ts,
    )


# ---------------------------------------------------------------------------
# Portfolio aggregation
# ---------------------------------------------------------------------------

_AGG_COLS = [
    "portfolio_delta", "portfolio_gamma", "portfolio_vega", "portfolio_theta",
    "portfolio_dollar_gamma", "portfolio_dollar_vega", "pnl_approx",
]


def aggregate_risk_frame(risk_df: pd.DataFrame,
                         group_by: str = "underlying_symbol") -> pd.DataFrame:
    """
    Vraie agrégation des Greeks monétisés (somme par bucket [portfolio_id, group_by])
    à partir d'un DataFrame de risque ligne-à-ligne. Source UNIQUE d'agrégation, partagée
    par le collecteur live et le pipeline EOD (≠ persister le ligne-à-ligne tel quel).
    """
    if risk_df is None or risk_df.empty:
        return pd.DataFrame()
    keys = [k for k in ("portfolio_id", group_by) if k in risk_df.columns]
    cols = [c for c in _AGG_COLS if c in risk_df.columns]
    if not keys or not cols:
        return pd.DataFrame()
    return risk_df.groupby(keys, as_index=False)[cols].sum()


def aggregate_risk(position_risks: List[PositionRisk],
                   group_by: str = "underlying_symbol") -> pd.DataFrame:
    """Aggregate monetized Greeks by the grouping key (objets → DataFrame agrégé)."""
    return aggregate_risk_frame(position_risk_to_dataframe(position_risks), group_by)


def position_risk_to_dataframe(risks: List[PositionRisk]) -> pd.DataFrame:
    """Flatten position risks to a DataFrame for Parquet persistence."""
    if not risks:
        return pd.DataFrame()
    rows = []
    for r in risks:
        rows.append({
            "portfolio_id": r.portfolio_id,
            "contract_key": r.contract_key,
            "underlying_symbol": r.underlying_symbol,
            "expiry": r.expiry,
            "strike": r.strike,
            "right": r.right,
            "quantity": r.quantity,
            "multiplier": r.multiplier,
            "spot": r.spot,
            "forward": r.forward,
            "implied_vol": r.implied_vol,
            "maturity_years": r.maturity_years,
            "price": r.price,
            "delta": r.delta,
            "gamma": r.gamma,
            "vega": r.vega,
            "theta": r.theta,
            "dollar_gamma": r.dollar_gamma,
            "dollar_vega": r.dollar_vega,
            "portfolio_delta": r.portfolio_delta,
            "portfolio_gamma": r.portfolio_gamma,
            "portfolio_vega": r.portfolio_vega,
            "portfolio_theta": r.portfolio_theta,
            "portfolio_dollar_gamma": r.portfolio_dollar_gamma,
            "portfolio_dollar_vega": r.portfolio_dollar_vega,
            "pnl_approx": r.pnl_approx,
            "valuation_ts": r.valuation_ts,
        })
    return pd.DataFrame(rows)
