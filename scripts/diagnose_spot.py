"""
Diagnostic : teste la récupération du spot pour chaque symbole de l'univers.

Usage (TWS doit être ouvert, port 7497) :
    python scripts/diagnose_spot.py

Affiche, pour chaque symbole, si la qualification du contrat réussit et
quel spot est récupéré via les données différées.
"""
import sys
import asyncio
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ib_insync import IB
from loguru import logger

from src.utils.config import load_config
from src.data.live import fetch_spot_async, _qualify_stock


async def main():
    cfg     = load_config("universe")
    symbols = [u["symbol"] for u in cfg.get("underlyings", [])]

    ib = IB()
    print("Connexion à TWS (127.0.0.1:7497)…")
    await ib.connectAsync("127.0.0.1", 7497, clientId=99, timeout=10)
    print(f"Connecté : {ib.isConnected()}\n")

    ib.reqMarketDataType(3)   # delayed

    for sym in symbols:
        print(f"── {sym} ─────────────────────────────")

        # 1. Qualification
        contract = await _qualify_stock(ib, sym)
        if contract is None:
            print(f"  ❌ Qualification échouée\n")
            continue
        print(f"  ✓ Qualifié : conId={contract.conId}, "
              f"primaryExchange={contract.primaryExchange}, exchange={contract.exchange}")

        # 2. Fetch spot
        spot = await fetch_spot_async(ib, sym)
        if spot:
            print(f"  ✓ Spot = ${spot:.2f}\n")
        else:
            print(f"  ❌ Aucun spot reçu\n")

    ib.disconnect()
    print("Déconnecté.")


if __name__ == "__main__":
    asyncio.run(main())
