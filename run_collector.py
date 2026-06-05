"""
run_collector.py — Process collecteur autonome (roadmap : "Collector service").

Responsabilité unique : posséder la connexion IBKR, collecter les données de marché
à un rythme maîtrisé, calculer les analytics et les persister dans le store.
Le front Dash ne fait que LIRE ce store — il ne parle jamais à IBKR.

Conforme à la roadmap :
  - "one always-on collector process" (Part IV.I)
  - "Keep the connectivity process separate from the analytics process" (Part I)
  - process isolation → un pic CPU dans l'UI ne peut plus couper la connexion

Usage :
    python run_collector.py                 # 127.0.0.1:7497, clientId 1
    python run_collector.py --port 4002      # IB Gateway paper
    python run_collector.py --interval 60    # cycle toutes les 60 s
"""
from __future__ import annotations

import sys
import os
import json
import asyncio
import argparse
import tempfile
from pathlib import Path
from datetime import date, datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
from loguru import logger

from ib_insync import IB

ROOT         = Path(__file__).parent
DATA_DIR     = ROOT / "data"
STATUS_FILE  = DATA_DIR / "collector_status.json"

# Version de code propagée dans la lineage de chaque sortie (roadmap : provenance)
CODE_VERSION = "vol-infra-collector-2.1.0"


# ---------------------------------------------------------------------------
# Écriture atomique du statut (lu par le front)
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Collecteur
# ---------------------------------------------------------------------------

class Collector:

    def __init__(self, host: str, port: int, client_id: int,
                 interval: int, symbol_pause: float):
        import hashlib
        import uuid
        from src.utils.config import load_config
        from src.storage.schemas import ParquetStore, MetadataStore

        self.host          = host
        self.port          = port
        self.client_id     = client_id
        self.interval      = interval
        self.symbol_pause  = symbol_pause

        self.universe_cfg  = load_config("universe")
        self.qc_cfg        = load_config("qc")
        self.pricing_cfg   = load_config("pricing")
        self.scenario_cfg  = load_config("scenarios")

        self.symbols = [u["symbol"] for u in self.universe_cfg.get("underlyings", [])]
        self.rate    = self.pricing_cfg.get("risk_free_rate", {}).get("value", 0.053)

        # Lineage (roadmap : provenance) — hash des configs économiques
        cfg_blob = json.dumps([self.universe_cfg, self.qc_cfg, self.pricing_cfg,
                               self.scenario_cfg], sort_keys=True, default=str)
        self.config_hash      = hashlib.sha256(cfg_blob.encode()).hexdigest()[:12]
        self.collector_session_id = str(uuid.uuid4())[:8]

        self.store      = ParquetStore(DATA_DIR / "analytics")
        self.raw_store  = ParquetStore(DATA_DIR / "raw")   # couche brute immuable
        self.meta_store = MetadataStore(DATA_DIR / "metadata.db")
        self._master_saved_date = None    # pour ne persister le master qu'une fois/jour
        self.ib         = IB()
        self._running = True
        self._status = {
            "connected":  False,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "last_cycle": None,
            "cycle_count": 0,
            "symbols":    {},
        }

    # ------------------------------------------------------------------
    # Connexion (avec backoff)
    # ------------------------------------------------------------------

    async def connect(self) -> bool:
        """
        Connexion avec essai automatique de clientId successifs.
        Si le clientId configuré est déjà pris (err 326, connexion zombie),
        on tente clientId+1, +2, … (roadmap : "confirm client ID not duplicated").
        """
        base  = self.client_id
        delay = 2.0
        for offset in range(8):
            cid = base + offset
            try:
                if self.ib.isConnected():
                    self.ib.disconnect()
                logger.info(f"Connexion IBKR {self.host}:{self.port} (clientId {cid})")
                await self.ib.connectAsync(self.host, self.port,
                                           clientId=cid, timeout=10)
                self.ib.reqMarketDataType(3)   # delayed
                self.client_id = cid
                self._status["connected"]  = True
                self._status["client_id"]  = cid
                logger.success(f"Connecté à IBKR (clientId {cid})")
                return True
            except Exception as exc:
                msg = str(exc)
                if "326" in msg or "already in use" in msg.lower():
                    logger.warning(f"clientId {cid} déjà utilisé — essai du suivant ({cid+1})")
                    await asyncio.sleep(0.5)
                    continue
                logger.warning(f"Échec connexion (clientId {cid}) : {exc} — retry dans {delay:.0f}s")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
        logger.error("Connexion IBKR impossible (tous les clientId essayés occupés ?)")
        self._status["connected"] = False
        return False

    # ------------------------------------------------------------------
    # Collecte d'un symbole
    # ------------------------------------------------------------------

    async def collect_symbol(self, symbol: str) -> dict:
        from src.data.live import (fetch_spot_async, fetch_option_chain_async,
                                    compute_live_analytics)

        result = {
            "spot": None, "n_quotes": 0,
            "iv_df": pd.DataFrame(), "forward_df": pd.DataFrame(),
            "surface_df": pd.DataFrame(), "chain_df": pd.DataFrame(),
        }

        spot = await fetch_spot_async(self.ib, symbol)
        result["spot"] = spot

        chain_df = await fetch_option_chain_async(
            self.ib, symbol, self.universe_cfg, self.qc_cfg, self.pricing_cfg
        )
        if chain_df is not None and not chain_df.empty:
            analytics = compute_live_analytics(
                chain_df, symbol, self.pricing_cfg, self.qc_cfg
            )
            result["chain_df"]   = analytics["chain_df"]
            result["iv_df"]      = analytics["iv_df"]
            result["forward_df"] = analytics["forward_df"]
            result["surface_df"] = analytics["surface_df"]
            result["n_quotes"]   = len(chain_df)

        return result

    # ------------------------------------------------------------------
    # Portefeuille + risk + scénarios
    # ------------------------------------------------------------------

    async def collect_portfolio_risk(self, session_date: date,
                                     spots: dict, run_id: str = "") -> None:
        from src.data.live import fetch_portfolio_async, enrich_portfolio_greeks
        from src.risk.scenarios import (load_scenarios_from_config,
                                        run_all_scenarios, scenario_reports_to_dataframe)
        from src.risk.aggregation import Position

        positions_df = await fetch_portfolio_async(self.ib, None)
        if positions_df is None or positions_df.empty:
            return

        # Enrichir par symbole (le spot diffère selon le sous-jacent)
        enriched_parts = []
        for sym, grp in positions_df.groupby("underlying_symbol"):
            spot = spots.get(sym)
            if spot:
                enriched_parts.append(enrich_portfolio_greeks(grp, spot, self.rate))
        if not enriched_parts:
            return
        risk_df = pd.concat(enriched_parts, ignore_index=True)
        if risk_df.empty:
            return
        self.store.write("risk_aggregates", self._add_lineage(risk_df, run_id),
                         session_date, atomic=True)

        # Scénarios sur les positions enrichies
        scenarios = load_scenarios_from_config(self.scenario_cfg)
        positions, iv_rows = [], {}
        for _, row in risk_df.iterrows():
            key = row["contract_key"]
            positions.append(Position(
                portfolio_id=row.get("portfolio_id", "ibkr_paper"),
                contract_key=key, underlying_symbol=row["underlying_symbol"],
                expiry=row["expiry"], strike=float(row["strike"]),
                right=row["right"], quantity=float(row["quantity"]),
                multiplier=int(row.get("multiplier", 100)),
            ))
            iv_rows[key] = {
                "forward": float(row["forward"]), "implied_vol": float(row["implied_vol"]),
                "maturity_years": float(row["maturity_years"]),
                "reference_spot": float(row.get("spot", row["forward"])),
            }
        reports = run_all_scenarios(positions, iv_rows, self.rate, scenarios)
        scen_df = scenario_reports_to_dataframe(reports)
        if not scen_df.empty:
            self.store.write("scenario_results", self._add_lineage(scen_df, run_id),
                             session_date, atomic=True)

    # ------------------------------------------------------------------
    # QC léger à partir des analytics live
    # ------------------------------------------------------------------

    def compute_qc(self, session_date: date, iv_all: pd.DataFrame,
                   fwd_all: pd.DataFrame, surfaces: dict, run_id: str) -> None:
        from src.qc.checks import (check_iv_convergence, check_surface_fit,
                                   check_quote_health, check_calendar_arbitrage,
                                   QcResult, qc_results_to_dataframe)

        max_spread = self.qc_cfg.get("quote_filters", {}).get("max_spread_pct", 0.25)
        results = []
        for sym in self.symbols:
            if not iv_all.empty:
                # 1. Convergence du solveur IV
                results.append(check_iv_convergence(
                    iv_all, sym,
                    self.qc_cfg.get("iv_solver", {}).get("min_convergence_ratio", 0.97)))
                # 2. Santé des quotes (spread, ratio inutilisable)
                results.append(check_quote_health(iv_all, sym, max_spread))

            # 3. Fit de surface (RMSE) + 4. no-arbitrage (calendaire + papillon)
            surf = surfaces.get(sym)
            if surf is not None:
                results.append(check_surface_fit(
                    surf, sym, self.qc_cfg.get("surface", {}).get("max_rmse", 0.02)))
                results.append(check_calendar_arbitrage(surf, sym))

            # 4. Stabilité du forward (confiance min par maturité)
            if not fwd_all.empty:
                col = "underlying" if "underlying" in fwd_all.columns else "underlying_symbol"
                sub = fwd_all[fwd_all[col] == sym] if col in fwd_all.columns else fwd_all
                if not sub.empty and "confidence_score" in sub.columns:
                    min_conf = float(sub["confidence_score"].min())
                    status = "pass" if min_conf > 0.4 else ("warn" if min_conf > 0.2 else "fail")
                    results.append(QcResult(
                        "forward_stability", sym, status,
                        {"pass": "info", "warn": "warning", "fail": "error"}[status],
                        min_conf, 0.4,
                        "ok" if status == "pass" else "low_confidence",
                        {"n_maturities": len(sub)}))

        if results:
            qc_df = qc_results_to_dataframe(results, run_id)
            self._add_lineage(qc_df, run_id)
            self.store.write("qc_results", qc_df, session_date, atomic=True)

            from src.qc.anomaly import (compute_baselines, detect_anomalies,
                                       build_triage_table)
            # Historique QC (append-only) pour baselines/anomaly detection (étape 14)
            try:
                hist = qc_df.copy()
                hist["ts"] = datetime.now(timezone.utc).isoformat()
                self.raw_store.append("qc_history", hist, session_date)
            except Exception as exc:
                logger.debug(f"qc_history append: {exc}")

            # Table de triage : checks warn/fail avec contexte (étape 14)
            triage = build_triage_table(results, run_id)
            if not triage.empty:
                self.store.write("qc_triage", self._add_lineage(triage, run_id),
                                 session_date, atomic=True)
                logger.warning(f"  QC triage : {len(triage)} check(s) en warn/fail")

            # Anomaly detection vs baseline glissante (sur l'historique accumulé)
            try:
                history = self.raw_store.read("qc_history", session_date)
                baselines = compute_baselines(history)
                anomalies = detect_anomalies(qc_df, baselines)
                if not anomalies.empty:
                    self.store.write("qc_anomalies", self._add_lineage(anomalies, run_id),
                                     session_date, atomic=True)
                    logger.warning(f"  QC anomalies détectées : {len(anomalies)}")
            except Exception as exc:
                logger.debug(f"anomaly detection: {exc}")
        return results

    # ------------------------------------------------------------------
    # Couche brute immuable + lineage (roadmap : raw layer, provenance)
    # ------------------------------------------------------------------

    def _add_lineage(self, df: "pd.DataFrame", run_id: str) -> "pd.DataFrame":
        """Attache la provenance à toute sortie dérivée (roadmap : transparency)."""
        if df is None or df.empty:
            return df
        df["code_version"] = CODE_VERSION
        df["config_hash"]  = self.config_hash
        df["run_id"]       = run_id
        return df

    def _write_raw_events(self, chain_df: "pd.DataFrame", spot, symbol: str,
                          session_date: date) -> None:
        """
        Persiste les observations brutes (bid/ask/last par contrat + spot) dans la
        couche immuable raw_market_events AVANT tout calcul. Append-only.
        C'est le socle exigé par la roadmap : analytics recalculables depuis le raw.
        """
        from src.collectors.raw_writer import RawMarketEvent

        events = []
        # Spot du sous-jacent
        if spot and spot > 0:
            events.append(RawMarketEvent.create(
                self.collector_session_id, f"{symbol}|STK|SMART|USD", "last", float(spot)))

        # Quotes options
        if chain_df is not None and not chain_df.empty:
            for _, row in chain_df.iterrows():
                key = row.get("instrument_key")
                if not key:
                    continue
                for field in ("bid", "ask", "last"):
                    val = row.get(field)
                    if val is not None and not (isinstance(val, float) and val != val):
                        events.append(RawMarketEvent.create(
                            self.collector_session_id, key, field, float(val)))

        if not events:
            return
        from dataclasses import asdict
        raw_df = pd.DataFrame([asdict(e) for e in events])
        self.raw_store.append("raw_market_events", raw_df, session_date)

    def _persist_instrument_master(self, iv_all: "pd.DataFrame",
                                   session_date: date) -> None:
        """
        Persiste l'instrument master canonique (étape 2) une fois par jour, reconstruit
        depuis les clés d'instrument observées. Source de vérité versionnée par date.
        """
        if self._master_saved_date == session_date or iv_all is None or iv_all.empty:
            return
        from src.universe.contracts import InstrumentMaster, Underlying, OptionContract

        master = InstrumentMaster()
        master.set_as_of_date(session_date)
        seen = set()
        for key in iv_all.get("instrument_key", pd.Series(dtype=str)).unique():
            opt = OptionContract.from_key(key)
            if opt:
                master.add_option(opt)
                if opt.underlying_symbol not in seen:
                    master.add_underlying(Underlying(
                        symbol=opt.underlying_symbol, exchange="SMART",
                        currency="USD", sec_type="STK"))
                    seen.add(opt.underlying_symbol)
        if master.all_options():
            self.meta_store.save_instrument_master(master, session_date)
            self._master_saved_date = session_date
            logger.info(f"  instrument_master persisté ({len(master.all_options())} options)")

    def _persist_snapshots(self, iv_all: "pd.DataFrame", run_id: str,
                           session_date: date) -> None:
        """
        Persiste les market_state_snapshots (étape 5) : vue time-aligned des quotes
        avec reference_spot/reference_type. La chaîne enrichie EST un snapshot.
        """
        if iv_all is None or iv_all.empty:
            return
        cols = ["snapshot_ts", "instrument_key", "underlying_symbol", "expiry", "strike",
                "right", "bid", "ask", "last", "mid_price", "is_usable",
                "reference_spot"]
        snap = iv_all[[c for c in cols if c in iv_all.columns]].copy()
        snap["reference_type"] = "live_delayed"
        snap["reject_reason"]  = None
        snap = self._add_lineage(snap, run_id)
        self.store.write("market_state_snapshots", snap, session_date, atomic=True)

    def _emit_alerts(self, qc_results: list, session_date: date) -> None:
        """
        Émet des alertes (étape 15) : tout check QC en 'fail'/'warn' est routé vers
        data/alerts.json, lisible par un opérateur ou un dashboard.
        """
        alerts = []
        now = datetime.now(timezone.utc).isoformat()
        for r in qc_results:
            if getattr(r, "status", "pass") in ("fail", "warn"):
                alerts.append({
                    "ts": now, "severity": r.severity, "check": r.check_name,
                    "target": r.target_key, "status": r.status,
                    "measured": r.measured_value, "threshold": r.threshold,
                    "reason": r.reason_code,
                    "collector_session_id": self.collector_session_id,
                })
        if not self.ib.isConnected():
            alerts.append({"ts": now, "severity": "error", "check": "connectivity",
                           "target": "collector", "status": "fail",
                           "reason": "ibkr_disconnected"})
        _atomic_write_json(DATA_DIR / "alerts.json",
                           {"updated": now, "n_alerts": len(alerts), "alerts": alerts})

    # ------------------------------------------------------------------
    # Un cycle complet
    # ------------------------------------------------------------------

    async def run_cycle(self) -> None:
        from src.surfaces.calibration import fit_surface

        session_date = date.today()
        run_id = f"{session_date.isoformat()}_collector_{self._status['cycle_count']}"

        all_iv, all_fwd, all_surf = [], [], []
        spots, surfaces = {}, {}

        for symbol in self.symbols:
            try:
                r = await self.collect_symbol(symbol)
                spots[symbol] = r["spot"]
                # COUCHE BRUTE IMMUABLE : on persiste les observations brutes AVANT
                # tout calcul (roadmap : raw layer append-only, base du replay).
                try:
                    self._write_raw_events(r["chain_df"], r["spot"], symbol, session_date)
                except Exception as exc:
                    logger.warning(f"  {symbol}: écriture raw échouée — {exc}")
                # iv_points = chaîne ENRICHIE (bid/ask, greeks, days_to_expiry,
                # is_usable, multiplier…) — c'est le schéma attendu par les pages.
                # Chaque sortie est protégée : une erreur sur l'une ne doit pas
                # bloquer les autres (robustesse).
                if not r["chain_df"].empty:
                    all_iv.append(r["chain_df"])
                if not r["forward_df"].empty:
                    all_fwd.append(r["forward_df"])
                if not r["surface_df"].empty:
                    all_surf.append(r["surface_df"])
                try:
                    if not r["iv_df"].empty:
                        surfaces[symbol] = fit_surface(
                            r["iv_df"], symbol,
                            datetime.now(timezone.utc).isoformat(), self.qc_cfg)
                except Exception as exc:
                    logger.warning(f"  {symbol}: fit surface QC — {exc}")

                self._status["symbols"][symbol] = {
                    "spot":     r["spot"],
                    "n_quotes": r["n_quotes"],
                    "updated":  datetime.now(timezone.utc).isoformat(),
                }
                logger.info(f"  {symbol}: spot={r['spot']}, {r['n_quotes']} quotes")
            except Exception as exc:
                logger.error(f"  {symbol}: erreur de collecte — {exc}")
                self._status["symbols"][symbol] = {
                    "spot": None, "n_quotes": 0, "error": str(exc)[:120],
                    "updated": datetime.now(timezone.utc).isoformat(),
                }
            await asyncio.sleep(self.symbol_pause)

        # Écritures combinées (atomiques) + lineage (code_version/config_hash/run_id)
        iv_all  = pd.concat(all_iv, ignore_index=True)  if all_iv  else pd.DataFrame()
        fwd_all = pd.concat(all_fwd, ignore_index=True) if all_fwd else pd.DataFrame()
        if not iv_all.empty:
            # Étape 2 : instrument master canonique versionné (1×/jour)
            self._persist_instrument_master(iv_all, session_date)
            # Étape 5 : market-state snapshots persistés
            self._persist_snapshots(iv_all, run_id, session_date)
            self.store.write("iv_points", self._add_lineage(iv_all, run_id),
                             session_date, atomic=True)
        if not fwd_all.empty:
            self.store.write("forward_curve", self._add_lineage(fwd_all, run_id),
                             session_date, atomic=True)
        if all_surf:
            surf_all = pd.concat(all_surf, ignore_index=True)
            self.store.write("surface_grid", self._add_lineage(surf_all, run_id),
                             session_date, atomic=True)

        # Portefeuille / risk / scénarios
        try:
            await self.collect_portfolio_risk(session_date, spots, run_id)
        except Exception as exc:
            logger.warning(f"portfolio/risk : {exc}")

        # QC + alertes (étapes 14/15)
        try:
            qc_results = self.compute_qc(session_date, iv_all, fwd_all, surfaces, run_id)
            self._emit_alerts(qc_results or [], session_date)
        except Exception as exc:
            logger.warning(f"qc : {exc}")

        # Statut
        self._status["connected"]   = self.ib.isConnected()
        self._status["last_cycle"]  = datetime.now(timezone.utc).isoformat()
        self._status["cycle_count"] += 1
        _atomic_write_json(STATUS_FILE, self._status)

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------

    async def main_loop(self) -> None:
        if not await self.connect():
            self._status["connected"] = False
            _atomic_write_json(STATUS_FILE, self._status)
            return

        logger.info(f"Collecteur démarré — symboles : {self.symbols} | cycle {self.interval}s")
        try:
            while self._running:
                if not self.ib.isConnected():
                    logger.warning("Connexion perdue — reconnexion…")
                    self._status["connected"] = False
                    _atomic_write_json(STATUS_FILE, self._status)
                    if not await self.connect():
                        await asyncio.sleep(self.interval)
                        continue

                logger.info(f"── Cycle {self._status['cycle_count']} ──")
                await self.run_cycle()
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            pass
        finally:
            self._status["connected"] = False
            self._status["stopped_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_write_json(STATUS_FILE, self._status)
            if self.ib.isConnected():
                self.ib.disconnect()
            logger.info("Collecteur arrêté.")

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# Entrée CLI
# ---------------------------------------------------------------------------

def _quiet_ib_noise():
    """
    Réduit le bruit console : ib_insync logue chaque contrat sans entitlement
    (erreurs 200 / 10089 / 10091…). On les filtre — elles sont attendues et déjà
    gérées (bail-out). Les vraies erreurs restent visibles via nos logs loguru.
    """
    import logging
    noisy = ("Error 200", "Error 300", "Error 354",
             "Error 10089", "Error 10090", "Error 10091", "Error 10092",
             "Unknown contract")

    class _Filter(logging.Filter):
        def filter(self, record):
            msg = record.getMessage()
            return not any(n in msg for n in noisy)

    for name in ("ib_insync.wrapper", "ib_insync.client", "ib_insync.ib"):
        logging.getLogger(name).addFilter(_Filter())


def main():
    _quiet_ib_noise()
    parser = argparse.ArgumentParser(description="Collecteur IBKR autonome")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7497)
    parser.add_argument("--client-id", type=int, default=1)
    parser.add_argument("--interval", type=int, default=120,
                        help="secondes entre deux cycles complets")
    parser.add_argument("--symbol-pause", type=float, default=3.0,
                        help="pause entre symboles (pacing)")
    args = parser.parse_args()

    collector = Collector(args.host, args.port, args.client_id,
                          args.interval, args.symbol_pause)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(collector.main_loop())
    except KeyboardInterrupt:
        logger.info("Arrêt demandé (Ctrl+C)")
        collector.stop()
    finally:
        loop.close()


if __name__ == "__main__":
    main()
