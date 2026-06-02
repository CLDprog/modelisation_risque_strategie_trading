"""
Bootstrap connectivity smoke test (Step 1 acceptance criteria).

Verifies:
  1. IBKR session reachable
  2. One contract can be resolved
  3. One market-data quote can be retrieved
  4. One event can be written to disk

Run: python scripts/bootstrap.py
"""
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config import load_config, get_env
from src.utils.logging_helpers import setup_logger
from src.connectivity.session import IBKRSession
from src.storage.schemas import ParquetStore
from src.collectors.raw_writer import RawMarketEvent, RawEventWriter

from loguru import logger
from datetime import date


def main():
    setup_logger("./logs")
    logger.info("=== BOOTSTRAP SMOKE TEST ===")

    # Load config
    broker_cfg = load_config("broker")
    env_cfg = load_config("environment")

    host = broker_cfg["ibkr"]["host"]
    port = broker_cfg["ibkr"]["port"]
    client_id = broker_cfg["ibkr"]["client_id"]

    logger.info(f"Connecting to IBKR at {host}:{port} (clientId={client_id})")

    session = IBKRSession(
        host=host, port=port, client_id=client_id,
        max_attempts=3, base_delay=2.0,
    )

    with session:
        if not session.is_healthy():
            logger.error("Session is not healthy after connect. Check TWS/Gateway.")
            sys.exit(1)

        ib = session.ib()

        # 1. Session state
        logger.info(f"Session state: {session.health.state.name}")
        logger.info(f"Connected at: {session.health.connected_at}")

        # 2. Resolve one contract (SPY)
        from ib_insync import Stock
        spy = Stock("SPY", "SMART", "USD")
        qualified = ib.qualifyContracts(spy)
        if not qualified:
            logger.error("Could not qualify SPY contract")
            sys.exit(1)
        q = qualified[0]
        logger.info(f"SPY resolved: conId={q.conId}, exchange={q.primaryExchange}")

        # 3. Request one market-data snapshot
        ticker = ib.reqMktData(spy, "", True, False)
        ib.sleep(2)  # wait for data
        logger.info(f"SPY quote: bid={ticker.bid}, ask={ticker.ask}, last={ticker.last}")

        # 4. Write one event to raw store
        store = ParquetStore(env_cfg["paths"]["raw_dir"])
        writer = RawEventWriter(store, date.today())
        event = RawMarketEvent.create(
            session_id="bootstrap",
            instrument_key=f"SPY|STK|SMART|USD",
            field_name="bid",
            field_value=float(ticker.bid or 0.0),
        )
        writer.push(event)
        writer.flush()
        logger.info(f"Written 1 event to raw store: {writer.session_summary()}")

        # 5. Heartbeat
        alive = session.heartbeat()
        logger.info(f"Heartbeat: {'OK' if alive else 'FAILED'}")

        logger.info("=== BOOTSTRAP COMPLETE — all checks passed ===")


if __name__ == "__main__":
    main()
