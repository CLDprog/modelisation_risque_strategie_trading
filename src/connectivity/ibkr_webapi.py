"""
IBKR Client Portal Web API adapter (replaces the legacy TWS/ib_insync path).

This is the single concrete `BrokerAdapter` for production. It wraps the `ibind`
REST client, which talks to the IBKR Web API over HTTP at the Client Portal
Gateway (default https://127.0.0.1:5000). No TWS, no socket protocol.

The rest of the codebase imports ONLY `BrokerAdapter` (broker.py) — never `ibind`
and never this module's internals — so the broker can be swapped or mocked.

Auth model (retail / paper): a lightweight gateway (IBeam in Docker, or the Java
Client Portal Gateway) holds the authenticated session; this adapter validates it,
keeps it alive with a background tickler, and consumes the REST endpoints. Fully
headless OAuth (no gateway) is institutional-only and supported via `use_oauth`.

Greeks/IV returned by `snapshot()` are BROKER-computed (diagnostics only, never a
source of truth — roadmap Step 11). IV is normalised to a fraction (0.20 = 20%).
"""
from __future__ import annotations

import json
import threading
import time
import urllib3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from loguru import logger

from src.connectivity.broker import (
    BrokerAdapter, BrokerPosition, OptionChainParams,
    SessionHealth, SessionState, to_float,
)

# ibind is the ONLY place we touch the broker SDK.
from ibind import IbkrClient, StockQuery
from ibind.client.ibkr_definitions import snapshot_by_key

# The local gateway uses a self-signed cert (we pass cacert=False), so urllib3's
# InsecureRequestWarning fires on every call and floods the logs. It is expected
# for a localhost gateway — silence it.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Évidence brute des réponses /iserver/secdef/* (roadmap Step 2 : « store raw contract
# payloads as evidence »). JSONL partitionné par date, append-only, best-effort —
# un échec d'archivage ne doit JAMAIS gêner la collecte.
_PAYLOAD_DIR = Path(__file__).parent.parent.parent / "data" / "raw_payloads"
_payload_lock = threading.Lock()


def _archive_payload(kind: str, key: str, payload) -> None:
    try:
        d = _PAYLOAD_DIR / f"dt={date.today().isoformat()}"
        d.mkdir(parents=True, exist_ok=True)
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind,
               "key": key, "payload": payload}
        line = json.dumps(rec, default=str, ensure_ascii=False)
        with _payload_lock, open(d / "secdef_payloads.jsonl", "a",
                                 encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as exc:  # noqa: BLE001 — l'évidence ne bloque jamais le flux
        logger.debug(f"payload archive ({kind}/{key}): {exc}")


# Our internal field vocabulary → ibind human-readable snapshot keys.
# (ibind maps these keys to IBKR numeric field codes; codes shown for reference.)
_FIELD_TO_KEY = {
    "bid":           "bid_price",            # 84
    "ask":           "ask_price",            # 86
    "last":          "last_price",           # 31
    "close":         "close",                # 7296
    "high":          "high",                 # 70
    "low":           "low",                  # 71
    "volume":        "volume_long",          # 7762 (numeric; 87 is K/M-formatted)
    "open_interest": "option_open_interest", # 7638
    "iv":            "implied_vol_percent",  # 7633 (PERCENT → divided by 100)
    "iv30":          "option_implied_vol_percent",  # 7283 — IV 30j du sous-jacent (PERCENT)
    "delta":         "delta",                # 7308
    "gamma":         "gamma",                # 7309
    "vega":          "vega",                 # 7311
    "theta":         "theta",                # 7310
}
# Broker returns these in percent; we store fractions to match the whole stack.
_PERCENT_FIELDS = {"iv", "iv30"}

# Champs de prix : critère de readiness du snapshot (les greeks broker sont un bonus,
# jamais une condition d'attente).
_PRICE_READY_FIELDS = ("bid", "ask", "last", "close")


def _price_ready(d: Optional[dict]) -> bool:
    return bool(d) and any(f in d for f in _PRICE_READY_FIELDS)

_MONTHS = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
     "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


def _month_str(d: date) -> str:
    """date → IBKR month token, e.g. 2026-01-15 → 'JAN26'."""
    return d.strftime("%b%y").upper()


def _strike_str(strike: float) -> str:
    """Format a strike the way IBKR expects (no trailing .0 for integers)."""
    return f"{strike:g}"


def _extract_rows(result) -> list:
    """ibind Result → list of dicts (snapshot/secdef endpoints return list payloads)."""
    data = getattr(result, "data", result)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    return []


class IBKRWebAdapter(BrokerAdapter):
    """Concrete BrokerAdapter backed by the IBKR Client Portal Web API via ibind."""

    def __init__(self, host: str = "127.0.0.1", port: int = 5000,
                 account_id: Optional[str] = None, cacert: bool = False,
                 timeout: float = 10.0, use_oauth: bool = False,
                 tickle_interval: int = 60):
        self.host = host
        self.port = int(port)
        self.account_id = account_id
        self.cacert = cacert
        self.timeout = timeout
        self.use_oauth = use_oauth
        self.tickle_interval = tickle_interval

        self.health = SessionHealth()
        self._client: Optional[IbkrClient] = None
        self._tickling = False
        self._conid_cache: Dict[str, int] = {}    # symbol → underlying conid (stable)
        self._secdef_cache: Dict[tuple, list] = {}  # (conid,month,strike,right,exch) → contracts
        self._strikes_cache: Dict[tuple, List[float]] = {}  # (conid,month,exch) → strikes listés (statiques/session)
        # Rate-limit des endpoints /iserver/secdef/* : la Web API renvoie 429 au-delà d'un
        # certain débit (observé le 2026-06-09 : ESTX50 sature, les sous-jacents suivants
        # se prennent des 429). On espace les appels (throttle global thread-safe, car
        # resolve_option_grid tire plusieurs threads) + retry/backoff en filet (_call_secdef).
        self._secdef_lock = threading.Lock()
        self._secdef_min_interval = 0.15    # s entre 2 appels secdef (~6-7 req/s)
        self._secdef_last = 0.0
        self._secdef_retry_tries = 6        # tentatives sur 429 (Too Many Requests)
        self._secdef_retry_base = 0.5       # backoff exponentiel : 0.5,1,2,4,8 s

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        self._set_state(SessionState.CONNECTING)
        try:
            self._client = IbkrClient(
                account_id=self.account_id,
                host=self.host, port=str(self.port),
                cacert=self.cacert, timeout=self.timeout,
                use_oauth=self.use_oauth,
            )

            if not self._ensure_authenticated():
                logger.error(
                    "Gateway reachable but session is NOT authenticated. "
                    "Start/authenticate the Client Portal Gateway (or IBeam) and retry."
                )
                self._set_state(SessionState.DEGRADED)
                return False

            # Pre-flights (required before /iserver/marketdata/* and /portfolio/*)
            # and account-id auto-discovery when not provided in config/.env.
            brokerage = self._safe_data(self._client.receive_brokerage_accounts)  # /iserver/accounts
            portfolio = self._safe_data(self._client.portfolio_accounts)          # /portfolio/accounts

            if not self.account_id:
                self.account_id = self._pick_account_id(brokerage, portfolio)
                if self.account_id:
                    try:
                        self._client.account_id = self.account_id
                    except Exception:  # noqa: BLE001
                        pass
                    logger.info(f"Auto-discovered account id: {self.account_id}")
                else:
                    logger.warning("Could not auto-discover account id — set "
                                   "IBKR_ACCOUNT_ID in .env if positions fail.")

            # Keep the brokerage session alive in the background.
            try:
                self._client.start_tickler(interval=self.tickle_interval)
                self._tickling = True
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"start_tickler: {exc}")

            self._on_connected()
            return True

        except Exception as exc:  # noqa: BLE001
            logger.error(f"Web API connect failed: {exc}")
            self._set_state(SessionState.DISCONNECTED)
            return False

    def _ensure_authenticated(self) -> bool:
        """True if the brokerage session is authenticated, attempting a re-init once."""
        status = self._auth_status()
        if status.get("authenticated"):
            return True
        # Try to (re)establish the brokerage session against the gateway.
        try:
            self._client.initialize_brokerage_session(compete=True)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"initialize_brokerage_session: {exc}")
        status = self._auth_status()
        return bool(status.get("authenticated"))

    def _auth_status(self) -> dict:
        try:
            data = self._client.authentication_status(log=False).data or {}
            return data if isinstance(data, dict) else {}
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"authentication_status: {exc}")
            return {}

    @staticmethod
    def _safe_data(fn):
        """Call an ibind endpoint and return its .data, or None on error."""
        try:
            return fn().data
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"{getattr(fn, '__name__', fn)}: {exc}")
            return None

    @staticmethod
    def _pick_account_id(brokerage, portfolio) -> Optional[str]:
        """Extract the account id from /portfolio/accounts or /iserver/accounts."""
        # /portfolio/accounts → list of dicts
        if isinstance(portfolio, list) and portfolio and isinstance(portfolio[0], dict):
            acc = (portfolio[0].get("accountId") or portfolio[0].get("id")
                   or portfolio[0].get("account"))
            if acc:
                return acc
        # /iserver/accounts → dict with selectedAccount / accounts
        if isinstance(brokerage, dict):
            acc = brokerage.get("selectedAccount")
            if acc:
                return acc
            accs = brokerage.get("accounts")
            if isinstance(accs, list) and accs:
                return accs[0]
        return None

    def disconnect(self) -> None:
        if self._client is not None:
            try:
                if self._tickling:
                    self._client.stop_tickler()
            except Exception:  # noqa: BLE001
                pass
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                pass
        self._tickling = False
        self._set_state(SessionState.DISCONNECTED)
        logger.info("IBKR Web API session disconnected.")

    def is_healthy(self) -> bool:
        return self.health.is_healthy

    def heartbeat(self) -> bool:
        if self._client is None:
            self._set_state(SessionState.DEGRADED)
            return False
        try:
            self._client.tickle(log=False)
            if self._auth_status().get("authenticated"):
                self.health.last_heartbeat = datetime.now(timezone.utc)
                return True
            self._set_state(SessionState.DEGRADED)
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Heartbeat failed: {exc}")
            self._set_state(SessionState.DEGRADED)
            return False

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def resolve_underlying(self, symbol: str, exchange: str = "SMART",
                           currency: str = "USD", sec_type: str = "STK") -> Optional[int]:
        cache_key = f"{symbol}|{sec_type}|{exchange}|{currency}"
        if cache_key in self._conid_cache:
            return self._conid_cache[cache_key]

        # Recherche secdef d'abord, désambiguïsée par bourse : gère les multi-cotations
        # (ex. SAP = ADR NYSE en USD ET ligne IBIS en EUR — on veut la bonne).
        conid = self._search_conid(symbol, sec_type, exchange, currency)

        # Dernier recours pour une action US non ambiguë.
        if conid is None and sec_type == "STK":
            try:
                data = self._client.stock_conid_by_symbol(
                    StockQuery(symbol), return_type="dict").data or {}
                if isinstance(data, dict) and data.get(symbol):
                    conid = int(data[symbol])
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"stock_conid_by_symbol({symbol}): {exc}")

        if conid is None:
            logger.warning(f"Could not resolve conid for {symbol} ({sec_type}/{exchange}/{currency})")
            return None
        self._conid_cache[cache_key] = conid
        return conid

    def _search_conid(self, symbol: str, sec_type: str, exchange: Optional[str] = None,
                      currency: Optional[str] = None) -> Optional[int]:
        """Résout un conid via /iserver/secdef/search, désambiguïsé par bourse de cotation."""
        try:
            rows = _extract_rows(self._client.search_contract_by_symbol(symbol, sec_type=sec_type))
            _archive_payload("secdef_search", f"{symbol}|{sec_type}|{exchange}", rows)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"search_contract_by_symbol({symbol},{sec_type}): {exc}")
            return None

        def has_type(r):
            return any(s.get("secType") == sec_type for s in (r.get("sections") or []))

        same_sym = [r for r in rows if r.get("conid")
                    and str(r.get("symbol", "")).upper() == symbol.upper()]
        typed = [r for r in same_sym if has_type(r)] or same_sym or [r for r in rows if r.get("conid")]
        if not typed:
            return None

        # Désambiguïsation par bourse : le champ 'description' porte la bourse de cotation
        # (ex. 'IBIS', 'NYSE', 'EUREX') ; sinon on cherche dans les sections.
        if exchange and exchange.upper() != "SMART":
            ex = exchange.upper()
            for r in typed:
                if str(r.get("description", "")).upper() == ex:
                    return int(r["conid"])
            for r in typed:
                if any(ex in str(s.get("exchange", "")).upper() for s in (r.get("sections") or [])):
                    return int(r["conid"])
        return int(typed[0]["conid"])

    def _option_months(self, symbol: str, sec_type: str = "STK") -> List[str]:
        """Available option months for an underlying, e.g. ['JAN26','FEB26',...]."""
        try:
            res = self._client.search_contract_by_symbol(symbol, sec_type=sec_type)
            for row in _extract_rows(res):
                if str(row.get("symbol", "")).upper() != symbol.upper():
                    continue
                for sec in row.get("sections", []) or []:
                    if sec.get("secType") == "OPT" and sec.get("months"):
                        return [m for m in str(sec["months"]).split(";") if m]
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"_option_months({symbol}): {exc}")
        return []

    def _throttle_secdef(self) -> None:
        """Espace les appels /iserver/secdef/* pour rester sous le rate-limit (429)."""
        with self._secdef_lock:
            wait = self._secdef_min_interval - (time.monotonic() - self._secdef_last)
            if wait > 0:
                time.sleep(wait)
            self._secdef_last = time.monotonic()

    def _call_secdef(self, fn, *args, **kwargs):
        """Appel /iserver/secdef/* throttlé + retry/backoff sur 429 (Too Many Requests).
        Chaque réponse est archivée en évidence brute (roadmap Step 2)."""
        for attempt in range(self._secdef_retry_tries):
            self._throttle_secdef()
            try:
                res = fn(*args, **kwargs)
                _archive_payload(getattr(fn, "__name__", "secdef"),
                                 "|".join(str(a) for a in args),
                                 getattr(res, "data", res))
                return res
            except Exception as exc:  # noqa: BLE001
                if ("429" in str(exc) or "Too Many" in str(exc)) and \
                        attempt < self._secdef_retry_tries - 1:
                    time.sleep(self._secdef_retry_base * (2 ** attempt))
                    continue
                raise

    def _strikes_for_month(self, conid: int, month: str,
                           exchange: Optional[str] = None) -> List[float]:
        # Strikes listés d'un (conid, mois) = statiques sur la session → cache (comme
        # secdef). Évite de re-appeler /secdef/strikes à chaque cycle (perf + 429).
        ck = (int(conid), month, exchange)
        if ck in self._strikes_cache:
            return self._strikes_cache[ck]
        try:
            res = self._call_secdef(self._client.search_strikes_by_conid,
                                    str(conid), "OPT", month, exchange=exchange)
            data = res.data or {}
            calls = data.get("call") or data.get("CALL") or []
            puts = data.get("put") or data.get("PUT") or []
            strikes = sorted({float(s) for s in (calls or puts) if s})
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"_strikes_for_month({conid},{month}): {exc}")
            return []          # échec (ex. 429 épuisé) NON caché → re-tenté au prochain cycle
        if strikes:
            self._strikes_cache[ck] = strikes   # ne cache que les succès
        return strikes

    def _secdef_info(self, conid: int, month: str, strike: float,
                     right: str, exchange: Optional[str] = None) -> list:
        # Définitions de contrats = statiques → cache de session (évite des secdef répétés
        # entre échéances d'un même mois et entre cycles).
        ck = (int(conid), month, _strike_str(strike), right.upper()[0], exchange)
        if ck in self._secdef_cache:
            return self._secdef_cache[ck]
        try:
            res = self._call_secdef(self._client.search_secdef_info_by_conid,
                str(conid), "OPT", month, exchange=exchange,
                strike=_strike_str(strike), right=right.upper()[0])
            rows = _extract_rows(res)
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"_secdef_info({conid},{month},{strike},{right}): {exc}")
            rows = []
        self._secdef_cache[ck] = rows
        return rows

    def option_chain_params(self, symbol: str, underlying_conid: int,
                            min_dte: int, max_dte: int,
                            sec_type: str = "STK",
                            exchange: Optional[str] = None) -> Optional[OptionChainParams]:
        today = date.today()
        months = self._option_months(symbol, sec_type)
        if not months:
            logger.warning(f"No option months discovered for {symbol}")
            return None

        # Keep only months that can contain an expiry within the DTE window.
        keep = []
        for m in months:
            try:
                yy, mm = 2000 + int(m[3:5]), _MONTHS[m[:3].upper()]
            except (KeyError, ValueError):
                continue
            month_start = date(yy, mm, 1)
            horizon = date.fromordinal(today.toordinal() + max_dte)
            if month_start <= horizon and (yy, mm) >= (today.year, today.month):
                keep.append(m)
        if not keep:
            keep = months[:3]

        # A reference spot lets us pick an ATM strike to probe exact expiries.
        spot = self._spot_for(underlying_conid)

        all_expiries: set = set()
        all_strikes: set = set()
        multiplier = 100
        trading_class = symbol

        for m in keep:
            strikes = self._strikes_for_month(underlying_conid, m, exchange)
            if not strikes:
                continue
            all_strikes.update(strikes)
            atm = min(strikes, key=lambda s: abs(s - spot)) if spot else strikes[len(strikes) // 2]
            for item in self._secdef_info(underlying_conid, m, atm, "C", exchange):
                mat = str(item.get("maturityDate") or item.get("expiry") or "")
                if len(mat) != 8:
                    continue
                try:
                    exp = datetime.strptime(mat, "%Y%m%d").date()
                except ValueError:
                    continue
                dte = (exp - today).days
                if min_dte <= dte <= max_dte:
                    all_expiries.add(exp)
                    multiplier = int(to_float(item.get("multiplier")) or multiplier)
                    trading_class = item.get("tradingClass") or trading_class

        if not all_expiries or not all_strikes:
            logger.warning(f"Option chain discovery yielded nothing usable for {symbol}")
            return None

        return OptionChainParams(
            underlying_symbol=symbol,
            underlying_conid=int(underlying_conid),
            expiries=sorted(all_expiries),
            strikes=sorted(all_strikes),
            multiplier=multiplier,
            trading_class=trading_class,
        )

    def option_contracts(self, underlying_conid: int, expiry_month: str,
                         strike: float, right: str,
                         exchange: Optional[str] = None) -> List[dict]:
        """All contracts for one (month, strike, right): [{expiry, conid, multiplier}]."""
        out = []
        for item in self._secdef_info(underlying_conid, expiry_month, strike, right, exchange):
            mat = str(item.get("maturityDate") or item.get("expiry") or "")
            if len(mat) != 8 or not item.get("conid"):
                continue
            try:
                exp = datetime.strptime(mat, "%Y%m%d").date()
            except ValueError:
                continue
            out.append({"expiry": exp, "conid": int(item["conid"]),
                        "multiplier": int(to_float(item.get("multiplier")) or 100)})
        return out

    def resolve_option(self, underlying_conid: int, expiry: date, strike: float,
                       right: str, exchange: Optional[str] = None) -> Optional[int]:
        for c in self.option_contracts(underlying_conid, _month_str(expiry), strike, right, exchange):
            if c["expiry"] == expiry:
                return c["conid"]
        return None

    def resolve_options(self, underlying_conid: int, expiries, strikes,
                        rights=("C", "P"), exchange: Optional[str] = None) -> Dict[tuple, int]:
        """Batched grid resolution: one secdef call per (month, strike, right)."""
        out: Dict[tuple, int] = {}
        cache: Dict[tuple, dict] = {}
        months = {_month_str(e) for e in expiries}
        for strike in strikes:
            for right in rights:
                for month in months:
                    ck = (month, strike, right)
                    if ck not in cache:
                        cache[ck] = {c["expiry"]: c["conid"] for c in
                                     self.option_contracts(underlying_conid, month, strike, right, exchange)}
                for e in expiries:
                    cid = cache.get((_month_str(e), strike, right), {}).get(e)
                    if cid:
                        out[(e, strike, right)] = cid
        return out

    def strikes_for_expiry(self, underlying_conid: int, expiry: date,
                           exchange: Optional[str] = None) -> List[float]:
        """Listed strikes for one expiry (queried via the expiry's contract month)."""
        return self._strikes_for_month(underlying_conid, _month_str(expiry), exchange)

    def resolve_option_grid(self, underlying_conid: int, expiry_to_strikes: dict,
                            rights=("C", "P"), exchange: Optional[str] = None) -> Dict[tuple, int]:
        """
        Resout tout le grid {expiry: [strikes]} -> {(expiry,strike,right): conid}, en
        lancant les appels secdef EN PARALLELE (gros gain de vitesse au 1er cycle ;
        les cycles suivants sont quasi instantanes via le cache secdef).
        """
        from concurrent.futures import ThreadPoolExecutor

        needed = set()
        for e, strikes in expiry_to_strikes.items():
            m = _month_str(e)
            for s in strikes:
                for r in rights:
                    needed.add((m, s, r))
        if not needed:
            return {}

        resolved: Dict[tuple, dict] = {}

        def _fetch(task):
            m, s, r = task
            contracts = self.option_contracts(underlying_conid, m, s, r, exchange)
            return task, {c["expiry"]: c["conid"] for c in contracts}

        try:
            with ThreadPoolExecutor(max_workers=3) as ex:
                for task, mapping in ex.map(_fetch, list(needed)):
                    resolved[task] = mapping
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"resolve_option_grid parallel: {exc}")

        out: Dict[tuple, int] = {}
        for e, strikes in expiry_to_strikes.items():
            m = _month_str(e)
            for s in strikes:
                for r in rights:
                    cid = resolved.get((m, s, r), {}).get(e)
                    if cid:
                        out[(e, s, r)] = cid
        return out

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    def _spot_for(self, conid: int) -> Optional[float]:
        snap = self.snapshot([conid], ["last", "close", "bid", "ask"])
        row = snap.get(int(conid), {})
        for f in ("last", "close"):
            if row.get(f):
                return row[f]
        if row.get("bid") and row.get("ask"):
            return (row["bid"] + row["ask"]) / 2
        return self.historical_close(conid)

    def snapshot(self, conids: Sequence[int],
                 field_names: Sequence[str]) -> Dict[int, Dict[str, float]]:
        if self._client is None or not conids:
            return {}
        # Map internal field names → field codes (skip unknown names).
        codes, code_to_field = [], {}
        for fn in field_names:
            key = _FIELD_TO_KEY.get(fn)
            if not key:
                continue
            code = snapshot_by_key.get(key)
            if code:
                codes.append(code)
                code_to_field[code] = fn
        if not codes:
            return {}

        # Snapshot en lots (l'endpoint limite le nombre de conids par appel ; un seul
        # gros snapshot par symbole au lieu d'un par échéance → bien plus rapide).
        out: Dict[int, Dict[str, float]] = {}
        conid_list = list(conids)
        # Taille de lot : 100 = plafond dur de l'endpoint (lot vide au-delà, observé
        # 2026-06-09). MAIS le farm différé EUREX dégrade les gros lots quand il est
        # chargé (2026-06-11 matin : lots de ~70 → 0 prix pendant que des lots de 30
        # passaient sans problème) → 30 conids/lot = taille robuste constatée.
        chunk_size = 30
        for i in range(0, len(conid_list), chunk_size):
            self._snapshot_chunk(conid_list[i:i + chunk_size], codes, code_to_field, out)
        return out

    def _snapshot_chunk(self, conids, codes, code_to_field, out) -> None:
        # L'endpoint a besoin d'un warm-up : les 1ères réponses omettent souvent des
        # champs jusqu'à l'établissement de la souscription. On retente en fusionnant.
        #
        # Warm-up ADAPTATIF (2026-06-11) : la latence du flux différé EUREX varie
        # énormément (~2s en après-midi, >8s à l'ouverture européenne — constaté en
        # séance : des lots entiers revenaient sans prix avec 8 essais fixes). On
        # poursuit TANT QUE des prix continuent d'arriver ; on ne s'arrête que sur
        # complétude, sur plateau (plus aucun nouveau prix sur 5 essais après un
        # minimum de 8 — ailes réellement mortes), ou au plafond de sécurité.
        conid_strs = [str(c) for c in conids]
        max_attempts, min_attempts, plateau_limit, sleep_s = 30, 8, 5, 1.0
        ready_prev, plateau = -1, 0
        # Le critère « prêt = prix reçu » n'a de sens que si des champs de PRIX sont
        # demandés. Pour un snapshot iv30/greeks seul (sigma_ref), prêt = ≥1 champ —
        # sinon la boucle plafonne 13 essais pour rien (~15s perdues par symbole).
        wants_price = any(fn in _PRICE_READY_FIELDS for fn in code_to_field.values())
        is_ready = _price_ready if wants_price else bool
        for attempt in range(max_attempts):
            try:
                res = self._client.live_marketdata_snapshot(conid_strs, codes)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"snapshot chunk: {exc}")
                time.sleep(sleep_s)
                continue
            for row in _extract_rows(res):
                conid = row.get("conid")
                if conid is None:
                    continue
                conid = int(conid)
                dest = out.setdefault(conid, {})
                for code, fn in code_to_field.items():
                    if code in row:
                        val = to_float(row[code])
                        if val is not None:
                            if fn in _PERCENT_FIELDS:
                                val = val / 100.0
                            dest[fn] = val
            # Prêt = au moins UN CHAMP DE PRIX reçu (bid/ask/last/close) — les greeks
            # broker ne comptent pas (ils arrivent parfois AVANT les prix).
            n_ready = sum(1 for c in conids if is_ready(out.get(int(c))))
            if n_ready == len(conids):
                break
            if n_ready == ready_prev:
                plateau += 1
                if attempt + 1 >= min_attempts and plateau >= plateau_limit:
                    # INFO (pas debug) : un lot qui plafonne bas = signal opérateur
                    # (farm lent / saturation) à voir sans relancer en DEBUG.
                    logger.info(f"snapshot: plateau à {n_ready}/{len(conids)} prix "
                                f"après {attempt + 1} essais")
                    break
            else:
                ready_prev, plateau = n_ready, 0
            time.sleep(sleep_s)

    def unsubscribe_all_marketdata(self) -> bool:
        """Vide le pool de souscriptions market data de la session gateway.

        Chaque appel snapshot SOUSCRIT ses conids côté serveur pour TOUTE la session
        (même entre deux process). À ~50 symboles × ~70 options par cycle, le pool
        sature et les nouveaux lots reviennent SANS PRIX (constaté 2026-06-11 matin :
        0/68 sur ESTX50 → 68/68 immédiatement après unsubscribeall). À appeler en
        début de cycle + périodiquement pendant la collecte."""
        if self._client is None:
            return False
        try:
            self._client.marketdata_unsubscribe_all()
            logger.info("Market data: pool de souscriptions purgé (unsubscribeall)")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"unsubscribeall: {exc}")
            return False

    def historical_close(self, conid: int) -> Optional[float]:
        if self._client is None:
            return None
        try:
            res = self._client.marketdata_history_by_conid(
                str(conid), bar="1d", period="5d", outside_rth=False)
            data = res.data or {}
            bars = data.get("data") if isinstance(data, dict) else data
            if bars:
                close = to_float(bars[-1].get("c"))
                if close and close > 0:
                    return close
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"historical_close({conid}): {exc}")
        return None

    # ------------------------------------------------------------------
    # Positions
    # ------------------------------------------------------------------

    def positions(self) -> List[BrokerPosition]:
        if self._client is None:
            return []
        out: List[BrokerPosition] = []
        page = 0
        try:
            while True:
                res = self._client.positions(account_id=self.account_id, page=page)
                rows = _extract_rows(res)
                if not rows:
                    break
                for row in rows:
                    out.append(self._normalise_position(row))
                if len(rows) < 100:
                    break
                page += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"positions(): {exc}")
        return out

    @staticmethod
    def _normalise_position(row: dict) -> BrokerPosition:
        expiry_raw = str(row.get("expiry") or row.get("lastTradingDay") or "")
        expiry_iso = None
        if len(expiry_raw) == 8 and expiry_raw.isdigit():
            try:
                expiry_iso = datetime.strptime(expiry_raw, "%Y%m%d").date().isoformat()
            except ValueError:
                expiry_iso = None
        return BrokerPosition(
            underlying_symbol=row.get("undSym") or row.get("ticker") or "",
            sec_type=row.get("assetClass") or row.get("secType") or "",
            expiry=expiry_iso,
            strike=to_float(row.get("strike")),
            right=row.get("putOrCall"),
            quantity=to_float(row.get("position")) or 0.0,
            multiplier=int(to_float(row.get("mult")) or 100),
            avg_cost=to_float(row.get("avgCost")),
            market_price=to_float(row.get("mktPrice")),
            market_value=to_float(row.get("mktValue")),
            unrealized_pnl=to_float(row.get("unrealizedPnl")),
            contract_id_broker=int(row["conid"]) if row.get("conid") else None,
        )

    # ------------------------------------------------------------------
    # Internal state helpers
    # ------------------------------------------------------------------

    def _on_connected(self) -> None:
        self.health.connected_at = datetime.now(timezone.utc)
        self.health.last_heartbeat = self.health.connected_at
        self._set_state(SessionState.CONNECTED)
        logger.info(f"IBKR Web API session CONNECTED (account={self.account_id}, "
                    f"gateway={self.host}:{self.port})")

    def _set_state(self, state: SessionState) -> None:
        prev = self.health.state
        self.health.state = state
        if prev != state:
            logger.info(f"Session state: {prev.name} → {state.name}")

    # Context manager support
    def __enter__(self) -> "IBKRWebAdapter":
        self.connect()
        return self

    def __exit__(self, *_) -> None:
        self.disconnect()
