"""
Canonical instrument master and contract resolution (Step 2).

Every other service uses these dataclasses as its single source of truth
for instrument identity. Never pass raw broker identifiers between modules.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, Dict, List
import json

from loguru import logger


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Underlying:
    symbol: str
    exchange: str
    currency: str
    sec_type: str              # STK, IND, FUT, …
    description: str = ""
    contract_id_broker: Optional[int] = None

    @property
    def instrument_key(self) -> str:
        return f"{self.symbol}|{self.sec_type}|{self.exchange}|{self.currency}"

    def __str__(self) -> str:
        return self.instrument_key


@dataclass(frozen=True)
class OptionContract:
    underlying_symbol: str
    expiry: date
    strike: float
    right: str                 # "C" ou "P"
    exchange: str
    currency: str
    multiplier: int = 100
    contract_id_broker: Optional[int] = None
    trading_class: str = ""

    @property
    def instrument_key(self) -> str:
        exp = self.expiry.strftime("%Y%m%d")
        r = self.right.upper()[0]
        return f"{self.underlying_symbol}|OPT|{exp}|{self.strike:.4f}|{r}|{self.exchange}|{self.currency}"

    @property
    def is_call(self) -> bool:
        return self.right.upper().startswith("C")

    @property
    def is_put(self) -> bool:
        return self.right.upper().startswith("P")

    def __str__(self) -> str:
        return self.instrument_key


# ---------------------------------------------------------------------------
# Universe store — in-memory with SQLite persistence
# ---------------------------------------------------------------------------

class InstrumentMaster:
    """
    Holds the canonical universe for a session date.
    Provides look-up helpers used by all downstream modules.
    """

    def __init__(self):
        self._underlyings: Dict[str, Underlying] = {}
        self._options: Dict[str, OptionContract] = {}
        self._as_of_date: Optional[date] = None

    # ------------------------------------------------------------------
    # Population
    # ------------------------------------------------------------------

    def add_underlying(self, u: Underlying) -> None:
        self._underlyings[u.instrument_key] = u

    def add_option(self, opt: OptionContract) -> None:
        key = opt.instrument_key
        if key in self._options:
            logger.debug(f"Duplicate option ignored: {key}")
            return
        self._options[key] = opt

    def set_as_of_date(self, d: date) -> None:
        self._as_of_date = d

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def get_underlying(self, symbol: str) -> Underlying:
        for u in self._underlyings.values():
            if u.symbol == symbol:
                return u
        raise KeyError(f"Underlying not found: {symbol}")

    def get_option_chain(self, symbol: str,
                         expiry: Optional[date] = None,
                         right: Optional[str] = None) -> List[OptionContract]:
        results = [
            opt for opt in self._options.values()
            if opt.underlying_symbol == symbol
        ]
        if expiry is not None:
            results = [o for o in results if o.expiry == expiry]
        if right is not None:
            results = [o for o in results if o.right.upper() == right.upper()]
        return sorted(results, key=lambda o: (o.expiry, o.strike))

    def resolve_contract(self, key: str) -> Underlying | OptionContract:
        if key in self._underlyings:
            return self._underlyings[key]
        if key in self._options:
            return self._options[key]
        raise KeyError(f"Contract not found: {key}")

    def all_underlyings(self) -> List[Underlying]:
        return list(self._underlyings.values())

    def all_options(self) -> List[OptionContract]:
        return list(self._options.values())

    def expiries(self, symbol: str) -> List[date]:
        return sorted({o.expiry for o in self.get_option_chain(symbol)})

    def strikes(self, symbol: str, expiry: date) -> List[float]:
        return sorted({o.strike for o in self.get_option_chain(symbol, expiry=expiry)})

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> List[str]:
        """Return a list of data-quality issues."""
        issues = []
        for opt in self._options.values():
            if opt.multiplier <= 0:
                issues.append(f"Invalid multiplier on {opt.instrument_key}")
            if opt.strike <= 0:
                issues.append(f"Non-positive strike on {opt.instrument_key}")
        return issues

    def summary(self) -> Dict:
        underlyings = list(self._underlyings.keys())
        return {
            "as_of_date": str(self._as_of_date),
            "underlyings": underlyings,
            "option_count": len(self._options),
            "issues": self.validate(),
        }

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_json(self) -> str:
        data = {
            "as_of_date": str(self._as_of_date),
            "underlyings": [
                {
                    "symbol": u.symbol, "exchange": u.exchange,
                    "currency": u.currency, "sec_type": u.sec_type,
                    "description": u.description,
                    "contract_id_broker": u.contract_id_broker,
                }
                for u in self._underlyings.values()
            ],
            "options": [
                {
                    "underlying_symbol": o.underlying_symbol,
                    "expiry": str(o.expiry),
                    "strike": o.strike,
                    "right": o.right,
                    "exchange": o.exchange,
                    "currency": o.currency,
                    "multiplier": o.multiplier,
                    "contract_id_broker": o.contract_id_broker,
                }
                for o in self._options.values()
            ],
        }
        return json.dumps(data, indent=2)


# ---------------------------------------------------------------------------
# IBKR discovery helper
# ---------------------------------------------------------------------------

def discover_universe_from_ibkr(session, universe_cfg: dict) -> InstrumentMaster:
    """
    Use ib_insync to discover option chains and populate an InstrumentMaster.
    Requires an active IBKRSession.
    """
    from ib_insync import Stock, Option
    from src.utils.dates import parse_ibkr_expiry
    import datetime

    ib = session.ib()
    master = InstrumentMaster()
    master.set_as_of_date(datetime.date.today())

    for u_cfg in universe_cfg.get("underlyings", []):
        symbol = u_cfg["symbol"]
        stock = Stock(symbol, u_cfg["exchange"], u_cfg["currency"])

        # Qualify the contract to get the broker conId
        qualified = ib.qualifyContracts(stock)
        if not qualified:
            logger.warning(f"Could not qualify underlying: {symbol}")
            continue

        q = qualified[0]
        underlying = Underlying(
            symbol=symbol,
            exchange=u_cfg["exchange"],
            currency=u_cfg["currency"],
            sec_type="STK",
            description=u_cfg.get("description", ""),
            contract_id_broker=q.conId,
        )
        master.add_underlying(underlying)
        logger.info(f"Underlying qualified: {underlying.instrument_key} (conId={q.conId})")

        # Fetch option chain metadata
        chains = ib.reqSecDefOptParams(
            symbol, "", "STK", q.conId
        )
        if not chains:
            logger.warning(f"No option chain found for {symbol}")
            continue

        chain = chains[0]
        opt_cfg = universe_cfg.get("options", {})
        max_dte = opt_cfg.get("maturity_window_days", 180)
        min_dte = opt_cfg.get("min_days_to_expiry", 7)
        today = datetime.date.today()

        valid_expiries = [
            parse_ibkr_expiry(e)
            for e in chain.expirations
            if min_dte <= (parse_ibkr_expiry(e) - today).days <= max_dte
        ]
        logger.info(f"{symbol}: {len(valid_expiries)} expiries within window")

        for expiry in valid_expiries:
            for strike in chain.strikes:
                for right in ("C", "P"):
                    opt = OptionContract(
                        underlying_symbol=symbol,
                        expiry=expiry,
                        strike=float(strike),
                        right=right,
                        exchange=opt_cfg.get("exchange", "SMART"),
                        currency=opt_cfg.get("currency", "USD"),
                        multiplier=int(chain.multiplier or 100),
                        trading_class=chain.tradingClass,
                    )
                    master.add_option(opt)

    issues = master.validate()
    if issues:
        for issue in issues:
            logger.warning(f"QC issue: {issue}")

    logger.info(f"Universe discovery complete: {master.summary()}")
    return master
