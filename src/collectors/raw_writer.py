"""
Raw event model and writer (Step 3).

Normalises market observations into a canonical event structure and persists them
to the immutable, append-only raw layer. The collector (`run_collector.py`) builds
these events from Web API snapshots and pushes them here BEFORE any analytics.

Rule: NEVER compute analytics here. Only normalise, stamp, persist.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, asdict
from datetime import date, datetime
from typing import Optional, List

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
