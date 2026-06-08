"""
Bootstrap smoke test for the IBKR Web API path (Step 1 acceptance criteria).

Proves the full data path WITHOUT TWS:
  1. Client Portal Gateway session reachable AND authenticated
  2. Underlying conid resolved (SPY)
  3. Live snapshot retrieved (spot) + historical-close fallback
  4. Option chain discovered; one ATM option resolved + snapshotted (greeks / IV)
  5. Positions endpoint reachable
  6. One normalised RawMarketEvent written to the immutable raw store

Prerequisites:
  - A running, authenticated Client Portal Gateway or IBeam at host:port
    (default 127.0.0.1:5000)
  - .env with IBKR_ACCOUNT_ID set (paper account, e.g. DU1234567)

Run: python scripts/bootstrap_webapi.py
"""
import sys
from pathlib import Path
from datetime import date

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

import os

from src.utils.config import load_config
from src.utils.logging_helpers import setup_logger
from src.connectivity.ibkr_webapi import IBKRWebAdapter
from src.storage.schemas import ParquetStore
from src.collectors.raw_writer import RawMarketEvent, RawEventWriter


def main():
    setup_logger("./logs")
    logger.info("=== BOOTSTRAP SMOKE TEST (IBKR Web API) ===")

    broker_cfg = load_config("broker")
    wcfg = broker_cfg.get("webapi", {})
    host = wcfg.get("host", "127.0.0.1")
    port = wcfg.get("port", 5000)
    use_oauth = bool(wcfg.get("use_oauth", False))
    # Optional: if not set (or left as the placeholder), the adapter auto-discovers
    # the account id from the authenticated gateway.
    account_id = os.getenv("IBKR_ACCOUNT_ID")
    if account_id in (None, "", "DU0000000"):
        account_id = None

    universe_cfg = load_config("universe")
    opt_cfg = universe_cfg.get("options", {})
    min_dte = opt_cfg.get("min_days_to_expiry", 7)
    max_dte = opt_cfg.get("maturity_window_days", 180)

    logger.info(f"Gateway {host}:{port}  account={account_id}  oauth={use_oauth}")

    adapter = IBKRWebAdapter(host=host, port=port,
                             account_id=account_id, use_oauth=use_oauth)
    with adapter:
        # 1. Session health
        if not adapter.is_healthy():
            logger.error("Session not healthy/authenticated. Start and log in to the "
                         "Client Portal Gateway (or IBeam) and retry.")
            sys.exit(1)
        logger.info(f"Session state: {adapter.health.state.name}")
        logger.info(f"Account id: {adapter.account_id}")

        # 2. Resolve SPY
        conid = adapter.resolve_underlying("SPY")
        if not conid:
            logger.error("Could not resolve SPY conid")
            sys.exit(1)
        logger.info(f"SPY conid = {conid}")

        # 3. Spot snapshot + historical close
        snap = adapter.snapshot([conid], ["bid", "ask", "last", "close"])
        logger.info(f"SPY snapshot: {snap.get(conid)}")
        hclose = adapter.historical_close(conid)
        logger.info(f"SPY historical close: {hclose}")

        # 4. Option chain + one ATM option (with broker greeks / IV)
        chain = adapter.option_chain_params("SPY", conid, min_dte, max_dte)
        if not chain:
            logger.warning("No option chain discovered (market-data entitlement?)")
        else:
            logger.info(f"Chain: {len(chain.expiries)} expiries, "
                        f"{len(chain.strikes)} strikes, mult={chain.multiplier}")
            nearest = chain.expiries[0]
            spot = (snap.get(conid, {}).get("last") or hclose
                    or chain.strikes[len(chain.strikes) // 2])
            atm = min(chain.strikes, key=lambda s: abs(s - spot))
            opt_conid = adapter.resolve_option(conid, nearest, atm, "C")
            logger.info(f"ATM call {nearest} K={atm} → conid {opt_conid}")
            if opt_conid:
                osnap = adapter.snapshot(
                    [opt_conid],
                    ["bid", "ask", "last", "iv", "delta", "gamma", "vega", "theta"])
                logger.info(f"Option snapshot (broker greeks, IV as fraction): "
                            f"{osnap.get(opt_conid)}")

        # 5. Positions endpoint
        positions = adapter.positions()
        logger.info(f"Positions returned: {len(positions)}")

        # 6. Write one normalised raw event
        store = ParquetStore(Path("./data/raw"))
        writer = RawEventWriter(store, date.today())
        bid = (snap.get(conid, {}).get("bid")
               or snap.get(conid, {}).get("last") or hclose or 0.0)
        writer.push(RawMarketEvent.create(
            "bootstrap_webapi", "SPY|STK|SMART|USD", "bid", float(bid)))
        writer.flush()
        logger.info(f"Raw event written: {writer.session_summary()}")

        # 7. Heartbeat
        alive = adapter.heartbeat()
        logger.info(f"Heartbeat: {'OK' if alive else 'FAILED'}")
        logger.info("=== BOOTSTRAP COMPLETE — Web API path verified ===")


if __name__ == "__main__":
    main()
