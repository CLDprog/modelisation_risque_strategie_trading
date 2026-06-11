"""
Broker-agnostic adapter interface (roadmap Part IV.B — "thin adapter pattern").

The ENTIRE stack consumes this interface, never a concrete broker SDK. The only
production implementation today is `IBKRWebAdapter` (Client Portal Web API via
`ibind`), defined in `ibkr_webapi.py`. A `MockBrokerAdapter` (recorded / synthetic
streams) can implement the same interface to power the integration- and
regression-test layers.

Design rules (kept deliberately strict):
  - No broker SDK type ever appears in a signature here.
  - Outputs are normalised primitives only: plain dicts / dataclasses we own.
  - The adapter owns the connection lifecycle and a small, inspectable state machine.

This module is intentionally free of any IBKR/ib_insync import so it stays a pure
contract that downstream modules can depend on without pulling a broker SDK.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum, auto
from typing import Dict, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Session state machine (broker-agnostic)
# ---------------------------------------------------------------------------

class SessionState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    DEGRADED = auto()
    RECONNECTING = auto()


@dataclass
class SessionHealth:
    state: SessionState = SessionState.DISCONNECTED
    last_heartbeat: Optional[datetime] = None
    reconnect_count: int = 0
    connected_at: Optional[datetime] = None

    @property
    def heartbeat_age_seconds(self) -> Optional[float]:
        if self.last_heartbeat is None:
            return None
        return (datetime.now(timezone.utc) - self.last_heartbeat).total_seconds()

    @property
    def is_healthy(self) -> bool:
        return self.state == SessionState.CONNECTED and (
            self.heartbeat_age_seconds is not None
            and self.heartbeat_age_seconds < 90
        )


# ---------------------------------------------------------------------------
# Normalised data objects (broker-agnostic)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class OptionChainParams:
    """Discovered option-chain metadata for one underlying, normalised."""
    underlying_symbol: str
    underlying_conid: int
    expiries: List[date]
    strikes: List[float]
    multiplier: int
    trading_class: str = ""


@dataclass(frozen=True)
class BrokerPosition:
    """One normalised portfolio position (source-of-record)."""
    underlying_symbol: str
    sec_type: str
    expiry: Optional[str]        # ISO date for options, else None
    strike: Optional[float]
    right: Optional[str]
    quantity: float
    multiplier: int
    avg_cost: Optional[float] = None
    market_price: Optional[float] = None
    market_value: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    contract_id_broker: Optional[int] = None


# ---------------------------------------------------------------------------
# Numeric parsing helper (broker quotes arrive as strings, often prefixed)
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def to_float(raw) -> Optional[float]:
    """
    Robustly coerce a broker-returned quote value to float, or None.

    Web API snapshot values arrive as strings and may carry non-numeric prefixes
    (e.g. last price "C520.10" = previous close, "H..." = halted) or formatting
    (commas, '%'). We extract the first numeric token. NaN becomes None.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        return v if v == v else None   # drop NaN
    m = _NUM_RE.search(str(raw).replace(",", ""))
    return float(m.group()) if m else None


# ---------------------------------------------------------------------------
# The adapter contract
# ---------------------------------------------------------------------------

class BrokerAdapter(ABC):
    """
    Broker-agnostic data + session interface consumed by the whole stack.

    Field vocabulary used by `snapshot()` (our internal names, unit conventions
    documented per the roadmap's insistence on explicit units):
        bid, ask, last, close, high, low  — prices (underlying currency)
        volume                            — numeric share/contract volume
        open_interest                     — option open interest
        iv                                — implied volatility as a FRACTION (0.20 = 20%)
        delta, gamma, vega, theta         — BROKER-computed greeks (diagnostics only,
                                            never a source of truth — see roadmap Step 11)
    """

    #: populated by every concrete adapter
    health: SessionHealth

    # -- lifecycle --------------------------------------------------------
    @abstractmethod
    def connect(self) -> bool:
        """Establish/validate the session. Returns True only on an authenticated session."""

    @abstractmethod
    def disconnect(self) -> None:
        ...

    @abstractmethod
    def is_healthy(self) -> bool:
        ...

    @abstractmethod
    def heartbeat(self) -> bool:
        """One lightweight round-trip confirming the session is alive."""

    def reconnect(self) -> bool:
        """Default reconnect: bump the counter and reconnect. Override if needed."""
        self.health.reconnect_count += 1
        return self.connect()

    # -- discovery --------------------------------------------------------
    @abstractmethod
    def resolve_underlying(self, symbol: str, exchange: str = "SMART",
                           currency: str = "USD", sec_type: str = "STK") -> Optional[int]:
        """Return the broker contract id (conid) of an underlying (STK, IND, …), or None."""

    @abstractmethod
    def option_chain_params(self, symbol: str, underlying_conid: int,
                            min_dte: int, max_dte: int, sec_type: str = "STK",
                            exchange: Optional[str] = None) -> Optional[OptionChainParams]:
        """Discover expiries/strikes/multiplier for the option chain in a DTE window."""

    @abstractmethod
    def resolve_option(self, underlying_conid: int, expiry: date, strike: float,
                       right: str, exchange: Optional[str] = None) -> Optional[int]:
        """Return the conid of one specific option contract, or None."""

    def resolve_options(self, underlying_conid: int, expiries: Sequence[date],
                        strikes: Sequence[float], rights: Sequence[str] = ("C", "P"),
                        exchange: Optional[str] = None) -> Dict[tuple, int]:
        """
        Resolve a grid of (expiry, strike, right) → conid. The default loops
        resolve_option; concrete adapters may override with a batched version.
        """
        out: Dict[tuple, int] = {}
        for strike in strikes:
            for right in rights:
                for expiry in expiries:
                    cid = self.resolve_option(underlying_conid, expiry, strike, right, exchange)
                    if cid:
                        out[(expiry, strike, right)] = cid
        return out

    def strikes_for_expiry(self, underlying_conid: int, expiry: date,
                           exchange: Optional[str] = None) -> List[float]:
        """Listed strikes for one expiry. Default empty; concrete adapters override."""
        return []

    def resolve_option_grid(self, underlying_conid: int, expiry_to_strikes: dict,
                            rights: Sequence[str] = ("C", "P"),
                            exchange: Optional[str] = None) -> Dict[tuple, int]:
        """
        Resolve a full grid {expiry: [strikes]} -> {(expiry, strike, right): conid}.
        Default loops resolve_option; concrete adapters may parallelise.
        """
        out: Dict[tuple, int] = {}
        for expiry, strikes in expiry_to_strikes.items():
            for strike in strikes:
                for right in rights:
                    cid = self.resolve_option(underlying_conid, expiry, strike, right, exchange)
                    if cid:
                        out[(expiry, strike, right)] = cid
        return out

    # -- market data ------------------------------------------------------
    @abstractmethod
    def snapshot(self, conids: Sequence[int],
                 field_names: Sequence[str]) -> Dict[int, Dict[str, float]]:
        """
        Return {conid: {field_name: value}} for the requested internal field names.
        Values are already unit-normalised (iv as a fraction). Missing fields are absent.
        """

    @abstractmethod
    def historical_close(self, conid: int) -> Optional[float]:
        """Most recent daily close — robust reference that works when the market is shut."""

    def unsubscribe_all_marketdata(self) -> bool:
        """Release server-side market data subscriptions (no-op by default).

        Brokers that keep per-session subscription pools (IBKR Web API) MUST override:
        the pool saturates after a few thousand snapshot conids and new batches come
        back without prices."""
        return False

    # -- positions --------------------------------------------------------
    @abstractmethod
    def positions(self) -> List[BrokerPosition]:
        """Return the account's positions (source-of-record), normalised."""
