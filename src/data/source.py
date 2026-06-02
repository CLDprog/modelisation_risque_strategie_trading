"""
Gestionnaire de source de données — singleton partagé entre toutes les pages Dash.

Architecture threading :
  - Dash (Flask) tourne dans des threads sans event loop asyncio
  - ib_insync a besoin d'un event loop asyncio pour fonctionner
  - Solution : un thread dédié "IB thread" possède son propre event loop
    et tourne en permanence. Les callbacks Dash lui envoient des coroutines
    via asyncio.run_coroutine_threadsafe().
"""
from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from loguru import logger

# Import au niveau module (thread principal) pour éviter l'erreur
# "no current event loop" quand eventkit tente get_event_loop() à l'import
try:
    from ib_insync import IB
    _IB_AVAILABLE = True
except Exception:
    # ImportError ou RuntimeError asyncio selon le contexte
    _IB_AVAILABLE = False
    IB = None  # type: ignore


class DataSource:
    """Singleton — une seule instance dans tout le process Dash."""

    _instance: Optional["DataSource"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized    = True
        self.mode            = "mock"
        self._ib             = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ib_thread      = None
        self._connected_at: Optional[datetime] = None
        self._last_error: Optional[str] = None
        self.is_connecting   = False   # flag visible par les callbacks

    # ------------------------------------------------------------------
    # Connexion
    # ------------------------------------------------------------------

    def connect(self, host: str = "127.0.0.1",
                port: int = 7497, client_id: int = 1) -> None:
        """
        Démarre la connexion IBKR en arrière-plan et retourne immédiatement.
        L'état est mis à jour dans self.mode / self.is_connected.
        Les callbacks Dash utilisent l'interval pour lire le statut.
        """
        if self.is_connecting or self.is_connected:
            return
        if not _IB_AVAILABLE:
            self._last_error = "ib_insync non installé."
            return

        self.is_connecting = True
        self._last_error   = None

        def _ib_thread_main():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ib = IB()

            async def _connect_and_run():
                try:
                    await ib.connectAsync(
                        host, port, clientId=client_id, timeout=10, readonly=False
                    )
                    self._ib           = ib
                    self._connected_at = datetime.now(timezone.utc)
                    self._last_error   = None
                    self.mode          = "live"
                    self.is_connecting = False
                    logger.info(f"IBKR connecté sur {host}:{port}")

                    while ib.isConnected():
                        await asyncio.sleep(1)

                except Exception as exc:
                    self._last_error   = str(exc)
                    self.is_connecting = False
                    logger.error(f"Connexion IBKR échouée : {exc}")
                finally:
                    self._loop = None
                    self._ib   = None
                    self.mode  = "mock"
                    self.is_connecting = False

            loop.run_until_complete(_connect_and_run())
            loop.close()

        self._ib_thread = threading.Thread(
            target=_ib_thread_main, daemon=True, name="IBThread"
        )
        self._ib_thread.start()

    def disconnect(self) -> None:
        if self._ib:
            try:
                self._ib.disconnect()   # méthode synchrone d'ib_insync
            except Exception:
                pass
        self._ib           = None
        self._connected_at = None
        self.mode          = "mock"
        self.is_connecting = False
        logger.info("Déconnecté — retour mode mock")

    # ------------------------------------------------------------------
    # Propriétés de statut
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._ib is not None and self._ib.isConnected()

    @property
    def state_label(self) -> str:
        if self.is_connecting:
            return "CONNEXION..."
        return "CONNECTÉ" if self.is_connected else "DÉCONNECTÉ"

    @property
    def heartbeat_age(self) -> Optional[float]:
        if self._connected_at is None or not self.is_connected:
            return None
        return (datetime.now(timezone.utc) - self._connected_at).total_seconds()

    @property
    def connected_at_str(self) -> str:
        return self._connected_at.strftime("%H:%M:%S UTC") if self._connected_at else "—"

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    # ------------------------------------------------------------------
    # Appels IB depuis les threads Dash (via run_coroutine_threadsafe)
    # ------------------------------------------------------------------

    def _run_ib(self, coro, timeout: float = 5):
        """Exécute une coroutine ib_insync depuis un thread Dash."""
        if self._loop is None or not self.is_connected:
            return None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=timeout)
        except Exception as exc:
            logger.warning(f"_run_ib erreur : {exc}")
            return None

    # ------------------------------------------------------------------
    # Accesseurs de données (dispatch live / mock)
    # ------------------------------------------------------------------

    def get_spot(self, symbol: str = "SPY") -> float:
        if self.mode == "live" and self.is_connected:
            from src.data.live import fetch_spot_async
            spot = self._run_ib(fetch_spot_async(self._ib, symbol))
            if spot and spot > 0:
                return spot
        from src.data.mock import SPOT
        return SPOT

    def get_option_chain(self, symbol: str = "SPY") -> pd.DataFrame:
        if self.mode == "live" and self.is_connected:
            from src.data.live import build_chain_on_live_spot
            spot = self.get_spot(symbol)
            return build_chain_on_live_spot(spot)
        from src.data.mock import generate_option_chain
        return generate_option_chain()

    def get_forward_curve(self, symbol: str = "SPY") -> pd.DataFrame:
        if self.mode == "live" and self.is_connected:
            from src.data.live import build_forward_curve_on_live_spot
            spot = self.get_spot(symbol)
            return build_forward_curve_on_live_spot(spot)
        from src.data.mock import generate_forward_curve
        return generate_forward_curve()

    def get_portfolio(self) -> pd.DataFrame:
        from src.data.mock import generate_portfolio
        return generate_portfolio()

    def get_scenarios(self) -> pd.DataFrame:
        from src.data.mock import generate_scenarios
        return generate_scenarios()

    def get_qc(self) -> pd.DataFrame:
        from src.data.mock import generate_qc
        return generate_qc()


# Instance globale unique
datasource = DataSource()
