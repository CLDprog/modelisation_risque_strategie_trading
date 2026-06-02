"""
Raw event collector and writer (Step 3).

Captures underlying and option quotes from IBKR, normalizes them
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

@dataclass
class RawMarketEvent:
    event_id: str
    collector_session_id: str
    instrument_key: str
    field_name: str         # "bid", "ask", "last", "volume", "open_interest", …
    field_value: float
    exchange_ts: Optional[str]   # horodatage de la bourse si disponible
    receipt_ts: str              # quand le collector a reçu l'event (UTC ISO)
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
        """Validate and add event to buffer. Flush automatically when full."""
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
        if event.field_name not in {
            "bid", "ask", "last", "volume", "open_interest",
            "close", "high", "low", "gamma", "delta", "vega", "theta", "iv",
        }:
            return False
        return True


# ---------------------------------------------------------------------------
# Live collector — wraps IBKR callbacks
# ---------------------------------------------------------------------------

class LiveCollector:
    """
    Subscribes to IBKR market data and routes events to RawEventWriter.
    Keeps callbacks lightweight — no analytics inside.
    """

    def __init__(self, session, master, writer: RawEventWriter):
        self.session = session
        self.master = master
        self.writer = writer
        self._session_id = str(uuid.uuid4())[:8]
        self._subscriptions: Dict[int, str] = {}  # reqId → instrument_key

    def subscribe_all(self) -> None:
        ib = self.session.ib()

        for underlying in self.master.all_underlyings():
            self._subscribe_underlying(ib, underlying)

        logger.info(f"Subscribed to {len(self._subscriptions)} instruments")

    def _subscribe_underlying(self, ib, underlying) -> None:
        from ib_insync import Stock
        stock = Stock(underlying.symbol, underlying.exchange, underlying.currency)
        ticker = ib.reqMktData(stock, "", False, False)
        ticker.updateEvent += lambda t: self._on_tick(t, underlying.instrument_key)
        logger.debug(f"Subscribed to underlying: {underlying.symbol}")

    def _on_tick(self, ticker, instrument_key: str) -> None:
        """Called by ib_insync on every market data update."""
        for field_name, value in [
            ("bid", ticker.bid),
            ("ask", ticker.ask),
            ("last", ticker.last),
            ("volume", ticker.volume),
        ]:
            if value is not None and not (isinstance(value, float) and value != value):
                event = RawMarketEvent.create(
                    self._session_id, instrument_key, field_name, value
                )
                self.writer.push(event)

    def run_until_close(self, heartbeat_interval: int = 30) -> None:
        """Block until market closes, with periodic heartbeats."""
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
                logger.info(f"Collector status: {summary}")
        except KeyboardInterrupt:
            logger.info("Collector stopped by operator")
        finally:
            self.writer.flush()
            logger.info(f"Final summary: {self.writer.session_summary()}")
