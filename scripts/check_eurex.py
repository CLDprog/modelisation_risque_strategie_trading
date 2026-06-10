"""
Probe EUREX — vérifie l'accès aux données IBKR pour EURO STOXX 50 (indice) + une
composante, et AFFICHE LES RÉPONSES BRUTES pour câbler correctement la résolution.

But : valider l'entitlement EUREX (données européennes) et voir la forme exacte des
réponses (indice IND, action € multi-listée) avant de basculer tout le collecteur.

Prérequis : gateway IBKR Web authentifié sur https://localhost:5000.
Run: python scripts/check_eurex.py
"""
import sys
import json
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config import load_config
from src.utils.logging_helpers import setup_logger
from src.connectivity.ibkr_webapi import IBKRWebAdapter, _extract_rows, _month_str


def dump(label: str, obj, n: int = 1500) -> None:
    s = json.dumps(obj, indent=2, default=str)
    print(f"\n----- {label} -----")
    print(s[:n] + (" …(tronqué)" if len(s) > n else ""))


def probe(adapter, symbol: str, sec_type: str, exchange: str,
          currency: str, opt_exchange: str = "EUREX") -> None:
    print("\n" + "=" * 72)
    print(f"#### {symbol}  (sec_type={sec_type}, exchange={exchange}, currency={currency})")
    print("=" * 72)
    client = adapter._client

    # 1. Recherche brute du contrat (forme de la réponse)
    try:
        raw = _extract_rows(client.search_contract_by_symbol(symbol, sec_type=sec_type))
        dump(f"search_contract_by_symbol({symbol}, {sec_type}) [BRUT]", raw)
    except Exception as exc:  # noqa: BLE001
        print(f"search_contract_by_symbol ERROR: {exc}")

    # 2. Résolution conid via l'adaptateur (désambiguïsée par bourse)
    conid = adapter.resolve_underlying(symbol, exchange, currency, sec_type)
    print(f"\nresolve_underlying({symbol}, {exchange}, {currency}, {sec_type}) → conid = {conid}")
    if not conid:
        print("→ pas de conid : on s'arrête pour ce symbole.")
        return

    # 3. Spot
    snap = adapter.snapshot([conid], ["last", "close", "bid", "ask"]).get(conid, {})
    print(f"spot snapshot = {snap}")

    # 4. Strikes bruts (mois courant, bourse des options) — diagnostic
    front_month = _month_str(date.today())
    try:
        raw_strikes = client.search_strikes_by_conid(
            str(conid), "OPT", front_month, exchange=opt_exchange).data
        dump(f"search_strikes_by_conid({conid}, OPT, {front_month}, {opt_exchange}) [BRUT]",
             raw_strikes, n=600)
    except Exception as exc:  # noqa: BLE001
        print(f"search_strikes_by_conid ERROR: {exc}")

    # 5. Chaîne d'options (jusqu'à 3 ans), avec la bourse des options
    chain = adapter.option_chain_params(symbol, conid, 1, 1095,
                                        sec_type=sec_type, exchange=opt_exchange)
    if not chain:
        print("→ pas de chaîne d'options récupérée.")
        return
    print(f"chain: {len(chain.expiries)} expiries, {len(chain.strikes)} strikes, mult={chain.multiplier}")
    print(f"  premières expiries : {[e.isoformat() for e in chain.expiries[:12]]}")
    if chain.strikes:
        print(f"  strikes (extrémités) : {chain.strikes[:4]} … {chain.strikes[-4:]}")

    # 6. Une option ATM : résolution + snapshot greeks
    spot = snap.get("last") or snap.get("close") or chain.strikes[len(chain.strikes) // 2]
    atm = min(chain.strikes, key=lambda s: abs(s - spot))
    expiry = chain.expiries[0]
    ocid = adapter.resolve_option(conid, expiry, atm, "C", exchange=opt_exchange)
    print(f"\noption ATM call {expiry} K={atm} → conid {ocid}")
    if ocid:
        osnap = adapter.snapshot(
            [ocid], ["bid", "ask", "last", "iv", "delta", "gamma", "vega", "theta"])
        print(f"option snapshot (greeks broker) = {osnap.get(ocid)}")


def main():
    setup_logger("./logs")
    broker_cfg = load_config("broker")
    w = broker_cfg.get("webapi", {})
    adapter = IBKRWebAdapter(
        host=w.get("host", "127.0.0.1"), port=w.get("port", 5000),
        account_id=None, use_oauth=bool(w.get("use_oauth", False)))
    with adapter:
        if not adapter.is_healthy():
            print("Session non authentifiée — lance et logue le gateway (https://localhost:5000).")
            sys.exit(1)
        print(f"Compte: {adapter.account_id}")
        probe(adapter, "ESTX50", "IND", "EUREX", "EUR", opt_exchange="EUREX")  # l'indice
        probe(adapter, "SAP", "STK", "IBIS", "EUR", opt_exchange="EUREX")       # une composante €
    print("\n=== FIN DU PROBE EUREX ===")


if __name__ == "__main__":
    main()
