"""
Raw event collector and writer (Step 3).

Captures underlying AND option quotes from IBKR, normalizes them
into a common event structure, and persists them to the immutable raw layer.

Rule: NEVER compute analytics here. Only normalize, stamp, persist.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from typing import Optional, Dict, List

import pandas as pd
from loguru import logger

from src.storage.schemas import ParquetStore
from src.utils.dates import utc_now


# ---------------------------------------------------------------------------
# Event dataclass — canonical raw event format
# ---------------------------------------------------------------------------

VALID_FIELDS = {
    "bid", "ask", "last", "volume", "open_interest",
    "close", "high", "low", "gamma", "delta", "vega", "theta", "iv",
}


@dataclass
class RawMarketEvent:
    event_id: str
    collector_session_id: str
    instrument_key: str
    field_name: str
    field_value: float
    exchange_ts: Optional[str]
    receipt_ts: str
    session_date: str

    @classmethod
    def create(cls, session_id: str, instrument_key: str,
               field_name: str, field_value: float,
               exchange_ts: Optional[datetime] = None) -> "RawMarketEvent":
        now = utc_now()
        return cls(
            event_id=str(uuid.uuid4()),
            collector_session_id=session_id,
            instrument_key=instrument_key,
            field_name=field_name,
            field_value=float(field_value),
            exchange_ts=exchange_ts.isoformat() if exchange_ts else None,
            receipt_ts=now.isoformat(),
            session_date=now.date().isoformat(),
        )


# ---------------------------------------------------------------------------
# Raw writer — append-only
# ---------------------------------------------------------------------------

class RawEventWriter:
    """Batches RawMarketEvents and flushes them to the Parquet raw store."""

    BATCH_SIZE = 500

    def __init__(self, store: ParquetStore, session_date: date):
        self.store = store
        self.session_date = session_date
        self._buffer: List[dict] = []
        self._write_count = 0
        self._malformed_count = 0

    def push(self, event: RawMarketEvent) -> None:
        if not self._validate(event):
            self._malformed_count += 1
            return
        self._buffer.append(asdict(event))
        if len(self._buffer) >= self.BATCH_SIZE:
            self.flush()

    def flush(self) -> None:
        if not self._buffer:
            return
        df = pd.DataFrame(self._buffer)
        self.store.append("raw_market_events", df, self.session_date)
        self._write_count += len(self._buffer)
        logger.debug(f"Flushed {len(self._buffer)} events (total: {self._write_count})")
        self._buffer.clear()

    def session_summary(self) -> dict:
        return {
            "session_date": self.session_date.isoformat(),
            "events_written": self._write_count,
            "malformed_events": self._malformed_count,
            "buffer_pending": len(self._buffer),
        }

    @staticmethod
    def _validate(event: RawMarketEvent) -> bool:
        if not event.instrument_key:
            logger.warning("Malformed event: missing instrument_key")
            return False
        if event.field_name not in VALID_FIELDS:
            return False
        return True


# ---------------------------------------------------------------------------
# Live collector — wraps IBKR callbacks
# ---------------------------------------------------------------------------

class LiveCollector:
    """
    Subscribes to IBKR underlying AND option market data.
    Routes events to RawEventWriter.
    Callbacks are kept lightweight — no analytics inside.
    """

    def __init__(self, session, master, writer: RawEventWriter):
        self.session = session
        self.master  = master
        self.writer  = writer
        self._session_id   = str(uuid.uuid4())[:8]
        self._subscriptions: Dict[int, str] = {}   # reqId → instrument_key
        self._option_sub_count = 0
        self._entitlement_failures: List[str] = []

    def subscribe_all(self) -> None:
        """Subscribe to all underlyings and their option chains."""
        ib = self.session.ib()

        for underlying in self.master.all_underlyings():
            self._subscribe_underlying(ib, underlying)

        option_count = 0
        for opt in self.master.all_options():
            self._subscribe_option(ib, opt)
            option_count += 1

        logger.info(
            f"Subscribed: {len(self.master.all_underlyings())} underlyings, "
            f"{option_count} options (session={self._session_id})"
        )

    def _subscribe_underlying(self, ib, underlying) -> None:
        from ib_insync import Stock
        try:
            stock  = Stock(underlying.symbol, underlying.exchange, underlying.currency)
            ticker = ib.reqMktData(stock, "", False, False)
            ticker.updateEvent += lambda t: self._on_underlying_tick(t, underlying.instrument_key)
            logger.debug(f"Subscribed underlying: {underlying.symbol}")
        except Exception as exc:
            logger.warning(f"Failed to subscribe underlying {underlying.symbol}: {exc}")
            self._entitlement_failures.append(underlying.instrument_key)

    def _subscribe_option(self, ib, opt) -> None:
        from ib_insync import Option as IBOption
        try:
            contract = IBOption(
                opt.underlying_symbol,
                opt.expiry.strftime("%Y%m%d"),
                opt.strike,
                opt.right,
                opt.exchange,
            )
            ticker = ib.reqMktData(contract, "", False, False)
            ticker.updateEvent += lambda t: self._on_option_tick(t, opt.instrument_key)
            self._option_sub_count += 1
        except Exception as exc:
            logger.debug(f"Option subscription failed {opt.instrument_key}: {exc}")
            self._entitlement_failures.append(opt.instrument_key)

    def _on_underlying_tick(self, ticker, instrument_key: str) -> None:
        """Callback : underlying market data update."""
        for field_name, value in [
            ("bid",    ticker.bid),
            ("ask",    ticker.ask),
            ("last",   ticker.last),
            ("volume", ticker.volume),
            ("close",  ticker.close),
        ]:
            if value is not None and not (isinstance(value, float) and value != value):
                if value > 0:
                    event = RawMarketEvent.create(
                        self._session_id, instrument_key, field_name, value
                    )
                    self.writer.push(event)

    def _on_option_tick(self, ticker, instrument_key: str) -> None:
        """Callback : option market data update."""
        for field_name, value in [
            ("bid",           ticker.bid),
            ("ask",           ticker.ask),
            ("last",          ticker.last),
            ("volume",        ticker.volume),
            ("open_interest", ticker.openInterest),
        ]:
            if value is not None and not (isinstance(value, float) and value != value):
                if value >= 0:
                    event = RawMarketEvent.create(
                        self._session_id, instrument_key, field_name, float(value)
                    )
                    self.writer.push(event)

        # Broker-computed greeks (diagnostics only, never used as source of truth)
        model_greeks = ticker.modelGreeks
        if model_greeks:
            for field_name, value in [
                ("iv",    model_greeks.impliedVol),
                ("delta", model_greeks.delta),
                ("gamma", model_greeks.gamma),
                ("vega",  model_greeks.vega),
                ("theta", model_greeks.theta),
            ]:
                if value is not None and not (isinstance(value, float) and value != value):
                    event = RawMarketEvent.create(
                        self._session_id, instrument_key, field_name, float(value)
                    )
                    self.writer.push(event)

    def coverage_summary(self) -> dict:
        """Daily coverage metrics per underlying and maturity."""
        store_path = self.writer.store.base_dir / "raw_market_events"
        today_str  = self.writer.session_date.isoformat()
        part_path  = store_path / f"dt={today_str}" / "data.parquet"

        if not part_path.exists():
            return {"coverage": {}, "total_events": 0}

        try:
            df = pd.read_parquet(part_path)
            option_keys = {o.instrument_key for o in self.master.all_options()}
            opt_df = df[df["instrument_key"].isin(option_keys)]

            coverage: dict = {}
            for opt in self.master.all_options():
                key = opt.instrument_key
                expiry = opt.expiry.isoformat()
                sym = opt.underlying_symbol
                n_events = (opt_df["instrument_key"] == key).sum()
                if sym not in coverage:
                    coverage[sym] = {}
                if expiry not in coverage[sym]:
                    coverage[sym][expiry] = 0
                coverage[sym][expiry] += n_events

            return {
                "coverage": coverage,
                "total_events": len(df),
                "entitlement_failures": len(self._entitlement_failures),
            }
        except Exception as exc:
            logger.warning(f"coverage_summary error: {exc}")
            return {"coverage": {}, "total_events": 0}

    def run_until_close(self, heartbeat_interval: int = 30) -> None:
        """Block until market closes, with periodic heartbeats and coverage logs."""
        import time
        ib = self.session.ib()
        logger.info("Collector running — press Ctrl+C to stop")
        try:
            while True:
                ib.sleep(heartbeat_interval)
                if not self.session.heartbeat():
                    logger.warning("Heartbeat failed — attempting reconnect")
                    if not self.session.reconnect():
                        break
                    self.subscribe_all()

                summary = self.writer.session_summary()
                coverage = self.coverage_summary()
                logger.info(f"Collector status: {summary} | coverage: {coverage}")
        except KeyboardInterrupt:
            logger.info("Collector stopped by operator")
        finally:
            self.writer.flush()
            logger.info(f"Final summary: {self.writer.session_summary()}")
            logger.info(f"Coverage: {self.coverage_summary()}")
