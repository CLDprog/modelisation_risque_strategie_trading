"""Date and time utilities for the vol infrastructure."""
from datetime import date, datetime, timezone
import pandas as pd


DAYS_PER_YEAR = 365.0
TRADING_DAYS_PER_YEAR = 252.0


def utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


def to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def maturity_years(expiry: date, valuation: date | None = None, convention: str = "actual_365") -> float:
    """Convert expiry date to year fraction (ACT/365 by default)."""
    if valuation is None:
        valuation = date.today()
    delta = (expiry - valuation).days
    if convention == "actual_365":
        return max(delta / DAYS_PER_YEAR, 0.0)
    if convention == "actual_252":
        return max(delta / TRADING_DAYS_PER_YEAR, 0.0)
    raise ValueError(f"Unknown day-count convention: {convention}")


def parse_ibkr_expiry(expiry_str: str) -> date:
    """Parse IBKR expiry string (YYYYMMDD) to date."""
    return datetime.strptime(expiry_str, "%Y%m%d").date()


def business_days_between(start: date, end: date) -> int:
    return len(pd.bdate_range(start, end)) - 1
