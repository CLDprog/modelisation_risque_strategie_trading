"""
IBKR session management (Step 1).

State machine: DISCONNECTED → CONNECTING → CONNECTED → DEGRADED → RECONNECTING
"""
from __future__ import annotations

import time
import random
from enum import Enum, auto
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger

try:
    from ib_insync import IB, util
    IB_AVAILABLE = True
except ImportError:
    IB_AVAILABLE = False
    logger.warning("ib_insync not installed — connectivity module in stub mode.")


class SessionState(Enum):
    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    DEGRADED = auto()
    RECONNECTING = auto()


@dataclass
class SessionHealth:
    state: SessionState = SessionState.DISCONNECTED
    last_heartbeat: datetime | None = None
    reconnect_count: int = 0
    connected_at: datetime | None = None

    @property
    def heartbeat_age_seconds(self) -> float | None:
        if self.last_heartbeat is None:
            return None
        return (datetime.now(timezone.utc) - self.last_heartbeat).total_seconds()

    @property
    def is_healthy(self) -> bool:
        return self.state == SessionState.CONNECTED and (
            self.heartbeat_age_seconds is not None
            and self.heartbeat_age_seconds < 90
        )


class IBKRSession:
    """Thin wrapper around ib_insync.IB with reconnect logic and health tracking."""

    def __init__(self, host: str, port: int, client_id: int,
                 max_attempts: int = 5, base_delay: float = 2.0,
                 max_delay: float = 60.0, jitter: bool = True):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter

        self.health = SessionHealth()
        self._ib: IB | None = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Attempt connection with exponential backoff. Returns True on success."""
        if not IB_AVAILABLE:
            logger.error("ib_insync is not installed.")
            return False

        self._set_state(SessionState.CONNECTING)
        attempt = 0
        while attempt < self.max_attempts:
            try:
                logger.info(f"Connecting to IBKR {self.host}:{self.port} (attempt {attempt+1})")
                self._ib = IB()
                self._ib.connect(self.host, self.port, clientId=self.client_id, timeout=10)
                self._on_connected()
                return True
            except Exception as exc:
                attempt += 1
                delay = self._backoff_delay(attempt)
                logger.warning(f"Connection failed: {exc}. Retrying in {delay:.1f}s...")
                time.sleep(delay)

        self._set_state(SessionState.DISCONNECTED)
        logger.error("Max reconnect attempts reached. Session is DISCONNECTED.")
        return False

    def disconnect(self) -> None:
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
        self._set_state(SessionState.DISCONNECTED)
        logger.info("IBKR session disconnected.")

    def is_healthy(self) -> bool:
        return self.health.is_healthy

    def heartbeat(self) -> bool:
        """Check that broker is still responding. Returns True if alive."""
        if self._ib is None or not self._ib.isConnected():
            self._set_state(SessionState.DEGRADED)
            return False
        try:
            self._ib.reqCurrentTime()
            self.health.last_heartbeat = datetime.now(timezone.utc)
            return True
        except Exception as exc:
            logger.warning(f"Heartbeat failed: {exc}")
            self._set_state(SessionState.DEGRADED)
            return False

    def reconnect(self) -> bool:
        self._set_state(SessionState.RECONNECTING)
        self.health.reconnect_count += 1
        logger.info(f"Attempting reconnect #{self.health.reconnect_count}")
        return self.connect()

    def ib(self) -> "IB":
        """Return the underlying ib_insync.IB instance."""
        if self._ib is None:
            raise RuntimeError("Session is not connected.")
        return self._ib

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _on_connected(self) -> None:
        self.health.connected_at = datetime.now(timezone.utc)
        self.health.last_heartbeat = self.health.connected_at
        self._set_state(SessionState.CONNECTED)
        logger.info(f"IBKR session CONNECTED (client_id={self.client_id})")

    def _set_state(self, state: SessionState) -> None:
        prev = self.health.state
        self.health.state = state
        if prev != state:
            logger.info(f"Session state: {prev.name} → {state.name}")

    def _backoff_delay(self, attempt: int) -> float:
        delay = min(self.base_delay * (2 ** (attempt - 1)), self.max_delay)
        if self.jitter:
            delay *= (0.5 + random.random() * 0.5)
        return delay

    # ------------------------------------------------------------------
    # Context manager support
    # ------------------------------------------------------------------

    def __enter__(self) -> "IBKRSession":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.disconnect()
