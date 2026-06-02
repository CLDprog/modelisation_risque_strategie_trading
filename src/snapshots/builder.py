"""
Market-state snapshot builder (Step 5).

Converts raw events into deterministic, time-aligned market-state snapshots.
Pure functions only — no external calls, fully replayable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Dict, List

import pandas as pd
from loguru import logger


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class UnderlyingState:
    instrument_key: str
    snapshot_ts: str
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    reference_spot: float
    reference_type: str          # "mid" | "last" | "close" | "fallback"
    spread_pct: Optional[float]
    is_stale: bool = False
    flags: Dict[str, bool] = field(default_factory=dict)


@dataclass
class OptionRow:
    instrument_key: str
    snapshot_ts: str
    underlying_symbol: str
    expiry: str
    strike: float
    right: str
    bid: Optional[float]
    ask: Optional[float]
    last: Optional[float]
    mid_price: Optional[float]
    open_interest: Optional[float]
    volume: Optional[float]
    quote_age_seconds: float
    is_usable: bool
    reject_reason: Optional[str]


@dataclass
class MarketStateSnapshot:
    snapshot_ts: str
    underlying_state: UnderlyingState
    option_rows: List[OptionRow]
    flags: Dict[str, bool] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Pure builder functions
# ---------------------------------------------------------------------------

def latest_by_field(events_df: pd.DataFrame, field_name: str,
                    before_ts: datetime) -> Optional[float]:
    """Return the most recent value of field_name before before_ts."""
    before_str = before_ts.isoformat()
    mask = (events_df["field_name"] == field_name) & (events_df["receipt_ts"] <= before_str)
    subset = events_df[mask]
    if subset.empty:
        return None
    idx = subset["receipt_ts"].idxmax()
    return float(subset.loc[idx, "field_value"])


def choose_reference_spot(bid: Optional[float], ask: Optional[float],
                           last: Optional[float],
                           max_spread_pct: float = 0.05) -> tuple[float, str]:
    """
    Choose the reference spot using: mid → last → fallback.
    Returns (price, reference_type).
    """
    if bid is not None and ask is not None and bid > 0 and ask > bid:
        spread_pct = (ask - bid) / ((bid + ask) / 2)
        if spread_pct <= max_spread_pct:
            return (bid + ask) / 2, "mid"
    if last is not None and last > 0:
        return last, "last"
    if bid is not None and bid > 0:
        return bid, "bid_fallback"
    raise ValueError("Cannot determine reference spot: no valid bid/ask/last available.")


def build_underlying_state(events_df: pd.DataFrame, instrument_key: str,
                            snapshot_ts: datetime, max_spread_pct: float = 0.05) -> UnderlyingState:
    """Build underlying state from raw events."""
    bid = latest_by_field(events_df, "bid", snapshot_ts)
    ask = latest_by_field(events_df, "ask", snapshot_ts)
    last = latest_by_field(events_df, "last", snapshot_ts)

    try:
        ref_spot, ref_type = choose_reference_spot(bid, ask, last, max_spread_pct)
    except ValueError as e:
        logger.warning(f"{instrument_key}: {e}")
        ref_spot = last or 0.0
        ref_type = "fallback"

    spread_pct = None
    if bid and ask and bid > 0:
        spread_pct = (ask - bid) / ((bid + ask) / 2)

    return UnderlyingState(
        instrument_key=instrument_key,
        snapshot_ts=snapshot_ts.isoformat(),
        bid=bid,
        ask=ask,
        last=last,
        reference_spot=ref_spot,
        reference_type=ref_type,
        spread_pct=spread_pct,
        is_stale=(ref_type == "fallback"),
    )


def build_option_row(events_df: pd.DataFrame, opt,
                     snapshot_ts: datetime, max_age_seconds: float = 60.0) -> OptionRow:
    """Build one option row from raw events for a given contract."""
    key = opt.instrument_key
    bid = latest_by_field(events_df, "bid", snapshot_ts)
    ask = latest_by_field(events_df, "ask", snapshot_ts)
    last = latest_by_field(events_df, "last", snapshot_ts)
    oi = latest_by_field(events_df, "open_interest", snapshot_ts)
    vol = latest_by_field(events_df, "volume", snapshot_ts)

    # Compute mid price and age
    mid = (bid + ask) / 2 if (bid and ask and bid > 0 and ask > bid) else None

    # Determine quote age
    mask = events_df["instrument_key"] == key
    if mask.any():
        latest_receipt = events_df.loc[mask, "receipt_ts"].max()
        age = (snapshot_ts - datetime.fromisoformat(latest_receipt)).total_seconds()
    else:
        age = float("inf")

    # Usability check
    is_usable, reason = True, None
    if bid is None or bid <= 0:
        is_usable, reason = False, "non_positive_bid"
    elif ask is None or ask <= bid:
        is_usable, reason = False, "crossed_or_locked_market"
    elif age > max_age_seconds:
        is_usable, reason = False, f"stale_quote_{age:.0f}s"

    return OptionRow(
        instrument_key=key,
        snapshot_ts=snapshot_ts.isoformat(),
        underlying_symbol=opt.underlying_symbol,
        expiry=opt.expiry.isoformat(),
        strike=opt.strike,
        right=opt.right,
        bid=bid,
        ask=ask,
        last=last,
        mid_price=mid,
        open_interest=oi,
        volume=vol,
        quote_age_seconds=age if age != float("inf") else -1.0,
        is_usable=is_usable,
        reject_reason=reason,
    )


# ---------------------------------------------------------------------------
# Main snapshot builder
# ---------------------------------------------------------------------------

def build_snapshot(raw_events_df: pd.DataFrame, underlying, options: list,
                   snapshot_ts: datetime, cfg: dict) -> MarketStateSnapshot:
    """
    Build a complete market-state snapshot from raw events.
    Deterministic: same inputs → same output.
    """
    qc_cfg = cfg.get("quote_filters", {})
    max_spread = qc_cfg.get("max_spread_pct", 0.05)
    max_age = qc_cfg.get("max_quote_age_seconds", 60)

    # Filter events up to snapshot_ts
    raw_events_df = raw_events_df[
        raw_events_df["receipt_ts"] <= snapshot_ts.isoformat()
    ].copy()

    # Build underlying state
    und_events = raw_events_df[raw_events_df["instrument_key"] == underlying.instrument_key]
    und_state = build_underlying_state(und_events, underlying.instrument_key,
                                        snapshot_ts, max_spread)

    # Build option rows
    option_rows = []
    for opt in options:
        opt_events = raw_events_df[raw_events_df["instrument_key"] == opt.instrument_key]
        row = build_option_row(opt_events, opt, snapshot_ts, max_age)
        option_rows.append(row)

    usable = sum(1 for r in option_rows if r.is_usable)
    flags = {
        "stale_underlying": und_state.is_stale,
        "no_usable_options": usable == 0,
        "low_option_coverage": usable < len(option_rows) * 0.5,
    }

    logger.debug(f"Snapshot @ {snapshot_ts.isoformat()}: spot={und_state.reference_spot:.2f} "
                 f"({und_state.reference_type}), {usable}/{len(option_rows)} options usable")

    return MarketStateSnapshot(
        snapshot_ts=snapshot_ts.isoformat(),
        underlying_state=und_state,
        option_rows=option_rows,
        flags=flags,
    )


def snapshot_to_dataframe(snapshot: MarketStateSnapshot) -> pd.DataFrame:
    """Flatten a snapshot into a DataFrame for Parquet persistence."""
    rows = []
    for opt in snapshot.option_rows:
        row = {
            "snapshot_ts": snapshot.snapshot_ts,
            "instrument_key": opt.instrument_key,
            "underlying_symbol": opt.underlying_symbol,
            "expiry": opt.expiry,
            "strike": opt.strike,
            "right": opt.right,
            "bid": opt.bid,
            "ask": opt.ask,
            "last": opt.last,
            "mid_price": opt.mid_price,
            "open_interest": opt.open_interest,
            "volume": opt.volume,
            "quote_age_seconds": opt.quote_age_seconds,
            "is_usable": opt.is_usable,
            "reject_reason": opt.reject_reason,
            "reference_spot": snapshot.underlying_state.reference_spot,
            "reference_type": snapshot.underlying_state.reference_type,
        }
        rows.append(row)
    return pd.DataFrame(rows)
