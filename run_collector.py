"""
run_collector.py — Process collecteur autonome (roadmap : "Collector service").

Responsabilité unique : posséder la connexion IBKR (via la Client Portal Web API),
collecter les données de marché à un rythme maîtrisé, calculer les analytics et les
persister dans le store. Le front Dash ne fait que LIRE ce store — il ne parle
jamais à IBKR.

Conforme à la roadmap :
  - "one always-on collector process" (Part IV.I)
  - "Keep the connectivity process separate from the analytics process" (Part I)
  - process isolation → un pic CPU dans l'UI ne peut plus couper la connexion

Prérequis : un Client Portal Gateway (ou IBeam) authentifié sur https://host:port.

Usage :
    python run_collector.py                     # 127.0.0.1:5000
    python run_collector.py --interval 60        # cycle toutes les 60 s
    python run_collector.py --account-id DU123   # force le compte (sinon auto-découvert)
"""
from __future__ import annotations

import sys
import os
import json
import time
import argparse
from pathlib import Path
from datetime import date, datetime, timezone

sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
from loguru import logger

from src.connectivity.ibkr_webapi import IBKRWebAdapter
from src.connectivity.broker import SessionState

ROOT         = Path(__file__).parent
DATA_DIR     = ROOT / "data"
STATUS_FILE  = DATA_DIR / "collector_status.json"

# Version de code propagée dans la lineage de chaque sortie (roadmap : provenance)
CODE_VERSION = "vol-infra-collector-3.0.0"


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

    def __init__(self, host: str, port: int, account_id, use_oauth: bool,
                 interval: int, symbol_pause: float, max_cycles: int = 0):
        import hashlib
        import uuid
        from src.utils.config import load_config
        from src.storage.schemas import ParquetStore, MetadataStore

        self.host          = host
        self.port          = port
        self.account_id    = account_id
        self.use_oauth     = use_oauth
        self.interval      = interval
        self.symbol_pause  = symbol_pause
        self.max_cycles    = max_cycles

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

        # L'adaptateur Web API possède la session (tickle/keep-alive en arrière-plan).
        self.adapter = IBKRWebAdapter(
            host=self.host, port=self.port,
            account_id=self.account_id, use_oauth=self.use_oauth,
        )

        self._running = True
        self._status = {
            "connected":  False,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "last_cycle": None,
            "cycle_count": 0,
            "cycle_secs": [],
            "symbols":    {},
        }

    # ------------------------------------------------------------------
    # Connexion (avec backoff)
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """
        Valide/établit la session Web API via le gateway. Backoff exponentiel.
        Le gateway (Client Portal / IBeam) doit être lancé ET authentifié.
        """
        delay = 2.0
        for attempt in range(5):
            if self.adapter.connect():
                self.account_id = self.adapter.account_id or self.account_id
                self._status["connected"] = True
                self._status["account_id"] = self.account_id
                logger.success(f"Connecté à la Web API IBKR (compte {self.account_id})")
                return True
            logger.warning(f"Session non authentifiée (essai {attempt+1}) — "
                           f"gateway lancé et connecté ? Retry dans {delay:.0f}s")
            time.sleep(delay)
            delay = min(delay * 2, 60)
        logger.error("Connexion Web API impossible — vérifie le gateway "
                     "(https://localhost:5000) et son login navigateur.")
        self._status["connected"] = False
        return False

    # ------------------------------------------------------------------
    # Collecte d'un symbole
    # ------------------------------------------------------------------

    def collect_symbol(self, symbol: str) -> dict:
        from src.data.live import (fetch_spot, fetch_option_chain,
                                    compute_live_analytics)

        result = {
            "spot": None, "n_quotes": 0, "n_usable": 0,
            "iv_df": pd.DataFrame(), "forward_df": pd.DataFrame(),
            "surface_df": pd.DataFrame(), "chain_df": pd.DataFrame(),
            "forward_diag_df": pd.DataFrame(), "pricing_df": pd.DataFrame(),
        }

        u = next((x for x in self.universe_cfg.get("underlyings", [])
                  if x.get("symbol") == symbol), {})
        sec_type = u.get("sec_type", "STK")
        exchange = u.get("exchange", "SMART")
        currency = u.get("currency", "USD")
        ibkr_symbol = u.get("ibkr_symbol", symbol)   # ticker IBKR (≠ id interne si doublon)

        spot = fetch_spot(self.adapter, ibkr_symbol, sec_type, exchange, currency)
        result["spot"] = spot

        chain_df = fetch_option_chain(
            self.adapter, symbol, self.universe_cfg, self.qc_cfg,
            self.pricing_cfg, spot=spot, sec_type=sec_type,
            exchange=exchange, currency=currency, ibkr_symbol=ibkr_symbol,
            option_exchange=u.get("option_exchange"),
        )
        if chain_df is not None and not chain_df.empty:
            analytics = compute_live_analytics(
                chain_df, symbol, self.pricing_cfg, self.qc_cfg,
                american=(sec_type == "STK"),
            )
            result["chain_df"]   = analytics["chain_df"]
            result["iv_df"]      = analytics["iv_df"]
            result["forward_df"] = analytics["forward_df"]
            result["surface_df"] = analytics["surface_df"]
            result["forward_diag_df"] = analytics.get("forward_diag_df", pd.DataFrame())
            result["pricing_df"]      = analytics.get("pricing_df", pd.DataFrame())
            result["n_quotes"]   = len(chain_df)
            cdf = result["chain_df"]
            result["n_usable"]   = (int(cdf["is_usable"].sum())
                                    if "is_usable" in cdf.columns else len(cdf))

        return result

    # ------------------------------------------------------------------
    # Portefeuille + risk + scénarios
    # ------------------------------------------------------------------

    def collect_portfolio_risk(self, session_date: date,
                               spots: dict, run_id: str = "") -> None:
        from src.data.live import fetch_portfolio, enrich_portfolio_greeks
        from src.risk.scenarios import (load_scenarios_from_config,
                                        run_all_scenarios, scenario_reports_to_dataframe)
        from src.risk.aggregation import Position

        positions_df = fetch_portfolio(self.adapter, None)
        if positions_df is None or positions_df.empty:
            return

        # Roadmap : table `positions` (état brut du portefeuille, avant enrichissement).
        self.store.write("positions", self._add_lineage(positions_df.copy(), run_id),
                         session_date, atomic=True, version=run_id)

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
        from src.risk.aggregation import aggregate_risk_frame
        # Détail ligne-à-ligne → position_risk ; vraie agrégation par bucket → risk_aggregates.
        self.store.write("position_risk", self._add_lineage(risk_df, run_id),
                         session_date, atomic=True, version=run_id)
        agg = aggregate_risk_frame(risk_df, "underlying_symbol")
        if not agg.empty:
            self.store.write("risk_aggregates", self._add_lineage(agg, run_id),
                             session_date, atomic=True, version=run_id)

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
                             session_date, atomic=True, version=run_id)

    # ------------------------------------------------------------------
    # QC léger à partir des analytics live
    # ------------------------------------------------------------------

    def compute_qc(self, session_date: date, iv_all: pd.DataFrame,
                   fwd_all: pd.DataFrame, surfaces: dict, run_id: str) -> list:
        from src.qc.checks import (check_iv_convergence, check_surface_fit,
                                   check_quote_health, check_calendar_arbitrage,
                                   check_option_chain_coverage, check_put_call_parity,
                                   check_greeks_reconciliation, check_carry_consistency,
                                   check_broker_greeks_reconciliation,
                                   QcResult, qc_results_to_dataframe)
        from src.risk.greeks_reconciliation import (reconcile_chain_greeks,
                                                    reconciliation_summary)

        max_spread = self.qc_cfg.get("quote_filters", {}).get("max_spread_pct", 0.25)
        # Couverture cible = tenors × (ATM + 2×ailes) × (call+put), depuis la config grille.
        opt_cfg  = self.universe_cfg.get("options", {})
        n_tenors = len(opt_cfg.get("target_tenors_days") or [1, 3, 10, 21, 30, 91, 182, 273, 365, 548, 730, 1095])
        n_ladder = len(opt_cfg.get("delta_ladder") or [0.10, 0.20, 0.30])
        expected_quotes = n_tenors * (1 + 2 * n_ladder) * 2
        min_cov    = self.qc_cfg.get("coverage", {}).get("min_coverage_ratio", 0.5)
        max_parity = self.qc_cfg.get("parity", {}).get("max_residual_pct", 0.02)
        und_by_sym = {u["symbol"]: u for u in self.universe_cfg.get("underlyings", [])}
        recon_cfg = self.qc_cfg.get("reconciliation", {})
        broker_cfg = self.qc_cfg.get("broker_reconciliation", {})
        carry_cfg = self.qc_cfg.get("carry", {})
        results, all_recon = [], []
        for sym in self.symbols:
            if not iv_all.empty:
                # 1. Convergence du solveur IV
                results.append(check_iv_convergence(
                    iv_all, sym,
                    self.qc_cfg.get("iv_solver", {}).get("min_convergence_ratio", 0.97)))
                # 2. Santé des quotes (spread, ratio inutilisable)
                results.append(check_quote_health(iv_all, sym, max_spread))
                # 2b. Couverture de la chaîne vs grille cible (roadmap)
                results.append(check_option_chain_coverage(iv_all, sym, expected_quotes, min_cov))
                # 2c. Résidu de parité put-call (roadmap ; tolérance ↑ pour américaines)
                is_am = und_by_sym.get(sym, {}).get("sec_type", "STK") == "STK"
                results.append(check_put_call_parity(iv_all, sym, self.rate, max_parity, american=is_am))
                # 2d. Réconciliation greeks vs diff-finies sur un échantillon (roadmap Step 11)
                sub = iv_all[iv_all["underlying_symbol"] == sym]
                if "is_usable" in sub.columns:
                    sub = sub[sub["is_usable"]]
                if not sub.empty:
                    recon = reconcile_chain_greeks(sub.head(12), self.rate, american=is_am)
                    results.append(check_greeks_reconciliation(
                        reconciliation_summary(recon), sym,
                        recon_cfg.get("max_delta_diff", 0.02),
                        recon_cfg.get("max_vega_diff", 0.05)))
                    if not recon.empty:
                        all_recon.append(recon)
                # 2e. Réconciliation greeks plateforme vs greeks BROKER (roadmap Step 11)
                results.append(check_broker_greeks_reconciliation(
                    iv_all, sym,
                    broker_cfg.get("max_delta_diff", 0.08),
                    broker_cfg.get("max_vega_diff", 0.20),
                    broker_cfg.get("min_points", 5)))

            # 3. Fit de surface (RMSE) + 4. no-arbitrage (calendaire + papillon)
            surf = surfaces.get(sym)
            if surf is not None:
                results.append(check_surface_fit(
                    surf, sym, self.qc_cfg.get("surface", {}).get("max_rmse", 0.02)))
                results.append(check_calendar_arbitrage(surf, sym))

            # 4. Stabilité du forward (confiance min par maturité)
            if not fwd_all.empty:
                # 4b. Carry implicite dans des bornes plausibles (roadmap Step 6)
                results.append(check_carry_consistency(
                    fwd_all, sym,
                    carry_cfg.get("min_carry", -0.10), carry_cfg.get("max_carry", 0.10)))
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

        if all_recon:
            recon_all = pd.concat(all_recon, ignore_index=True)
            self.store.write("greeks_reconciliation",
                             self._add_lineage(recon_all, run_id), session_date, atomic=True)

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
        Émet des alertes (étape 15) : tout check QC en 'fail'/'warn' est écrit dans
        data/alerts.json avec sa politique d'ESCALADE S1–S4 (étape 14 : niveau, owner,
        SLA, échéance), puis routé vers les canaux externes configurés (webhook/email).
        """
        from datetime import timedelta
        from src.qc.alert_router import route_alerts

        esc_cfg = self.qc_cfg.get("escalation", {})

        def _escalate(kind: str) -> dict:
            pol = esc_cfg.get(kind, {})
            sla = int(pol.get("sla_minutes", 1440))
            due = (datetime.now(timezone.utc) + timedelta(minutes=sla)).isoformat()
            return {"level": pol.get("level", "S4"),
                    "owner": pol.get("owner", "operator"),
                    "sla_minutes": sla, "due_by": due}

        alerts = []
        now = datetime.now(timezone.utc).isoformat()
        for r in qc_results:
            status = getattr(r, "status", "pass")
            if status in ("fail", "warn"):
                alerts.append({
                    "ts": now, "severity": r.severity, "check": r.check_name,
                    "target": r.target_key, "status": status,
                    "measured": r.measured_value, "threshold": r.threshold,
                    "reason": r.reason_code,
                    "collector_session_id": self.collector_session_id,
                    **_escalate(status),
                })
        if not self.adapter.is_healthy():
            alerts.append({"ts": now, "severity": "error", "check": "connectivity",
                           "target": "collector", "status": "fail",
                           "reason": "ibkr_disconnected",
                           **_escalate("disconnect")})
        _atomic_write_json(DATA_DIR / "alerts.json",
                           {"updated": now, "n_alerts": len(alerts), "alerts": alerts})
        # Routage externe (no-op tant que webhook/SMTP ne sont pas configurés)
        try:
            sent = route_alerts(alerts, self.qc_cfg.get("alerting"))
            if any(v is not None for v in sent.values()):
                logger.info(f"  alertes routées : {sent}")
        except Exception as exc:
            logger.warning(f"alert routing : {exc}")

    # ------------------------------------------------------------------
    # Un cycle complet
    # ------------------------------------------------------------------

    def run_cycle(self) -> None:
        from src.surfaces.calibration import fit_surface, surface_params_to_dataframe

        session_date = date.today()
        idx = self._status["cycle_count"]
        run_id = f"{session_date.isoformat()}_collector_{idx}"
        t0 = time.perf_counter()

        all_iv, all_fwd, all_surf = [], [], []
        all_iv_diag, all_fwd_diag, all_pricing = [], [], []
        spots, surfaces = {}, {}

        # Vide le pool de souscriptions market data AVANT CHAQUE SYMBOLE : IBKR limite
        # à ~100 lignes simultanées et un symbole en consomme ~72 (chaîne + spot +
        # iv30). Sans purge systématique, dès le 2e symbole on dépasse et le serveur
        # évince — snapshots sans prix (constaté le 2026-06-11 : sains après purge,
        # morts 4-6 symboles plus loin). Le pool persiste aussi ENTRE les runs.
        for i_sym, symbol in enumerate(self.symbols):
            self.adapter.unsubscribe_all_marketdata()
            try:
                r = self.collect_symbol(symbol)
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
                # Diagnostics roadmap : IV résolues (failure_reason) + candidats forward rejetés
                if not r["iv_df"].empty:
                    all_iv_diag.append(r["iv_df"])
                if not r["forward_diag_df"].empty:
                    all_fwd_diag.append(r["forward_diag_df"])
                if not r["pricing_df"].empty:
                    all_pricing.append(r["pricing_df"])
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
                    "n_usable": r["n_usable"],
                    "updated":  datetime.now(timezone.utc).isoformat(),
                }
                logger.info(f"  {symbol}: spot={r['spot']}, "
                            f"{r['n_usable']}/{r['n_quotes']} usable")
            except Exception as exc:
                logger.error(f"  {symbol}: erreur de collecte — {exc}")
                self._status["symbols"][symbol] = {
                    "spot": None, "n_quotes": 0, "error": str(exc)[:120],
                    "updated": datetime.now(timezone.utc).isoformat(),
                }
            time.sleep(self.symbol_pause)

        # Écritures combinées (atomiques) + lineage (code_version/config_hash/run_id).
        # FutureWarning pandas 2.x bénin (colonnes entièrement NA, ex. greeks absents sur
        # certains tenors) : le dtype inféré de ces colonnes vides ne nous concerne pas.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            iv_all  = pd.concat(all_iv, ignore_index=True)  if all_iv  else pd.DataFrame()
            fwd_all = pd.concat(all_fwd, ignore_index=True) if all_fwd else pd.DataFrame()
        if not iv_all.empty:
            # Étape 2 : instrument master canonique versionné (1×/jour)
            self._persist_instrument_master(iv_all, session_date)
            # Étape 5 : market-state snapshots persistés
            self._persist_snapshots(iv_all, run_id, session_date)
            self.store.write("iv_points", self._add_lineage(iv_all, run_id),
                             session_date, atomic=True, version=run_id)
        if not fwd_all.empty:
            self.store.write("forward_curve", self._add_lineage(fwd_all, run_id),
                             session_date, atomic=True, version=run_id)
        if all_surf:
            surf_all = pd.concat(all_surf, ignore_index=True)
            self.store.write("surface_grid", self._add_lineage(surf_all, run_id),
                             session_date, atomic=True, version=run_id)
        # Tables roadmap (auparavant jamais écrites) : params SVI + diagnostics rejetés.
        if surfaces:
            params = [surface_params_to_dataframe(s) for s in surfaces.values()]
            params = [p for p in params if not p.empty]
            if params:
                self.store.write("surface_parameters",
                                 self._add_lineage(pd.concat(params, ignore_index=True), run_id),
                                 session_date, atomic=True, version=run_id)
        if all_fwd_diag:
            self.store.write("forward_diagnostics",
                             self._add_lineage(pd.concat(all_fwd_diag, ignore_index=True), run_id),
                             session_date, atomic=True, version=run_id)
        if all_iv_diag:
            self.store.write("iv_diagnostics",
                             self._add_lineage(pd.concat(all_iv_diag, ignore_index=True), run_id),
                             session_date, atomic=True, version=run_id)
        # Roadmap : sorties du moteur de pricing persistées (round-trip prix↔IV).
        if all_pricing:
            self.store.write("pricing_results",
                             self._add_lineage(pd.concat(all_pricing, ignore_index=True), run_id),
                             session_date, atomic=True, version=run_id)

        # Eq.22 — surface interpolée aux tenors cibles EXACTS (les échéances listées
        # n'atteignent jamais 30/91/…/730 jours pile) → table surface_interpolated.
        try:
            from src.surfaces.calibration import interpolate_across_maturities
            tenors = (self.universe_cfg.get("options", {}).get("target_tenors_days")
                      or [30, 91, 182, 273, 365, 730])
            targets = [d / 365.0 for d in tenors]
            interp_parts = []
            if all_surf:
                for sym, grp in surf_all.groupby("underlying"):
                    out = interpolate_across_maturities(grp, targets)
                    if not out.empty:
                        interp_parts.append(out)
            if interp_parts:
                self.store.write("surface_interpolated",
                                 self._add_lineage(pd.concat(interp_parts, ignore_index=True), run_id),
                                 session_date, atomic=True, version=run_id)
        except Exception as exc:
            logger.warning(f"surface interpolée (Eq.22) : {exc}")

        # Eq.23 — diagnostics de dispersion indice vs composantes (corrélation implicite).
        try:
            from src.risk.dispersion import dispersion_diagnostics
            weights = {u["symbol"]: u["weight"]
                       for u in self.universe_cfg.get("underlyings", [])
                       if u.get("weight") is not None} or None
            disp = dispersion_diagnostics(
                iv_all, "ESTX50",
                self.universe_cfg.get("options", {}).get("target_tenors_days")
                or [30, 91, 182, 273, 365, 730],
                weights=weights,
                snapshot_ts=datetime.now(timezone.utc).isoformat())
            if not disp.empty:
                self.store.write("dispersion_diagnostics",
                                 self._add_lineage(disp, run_id),
                                 session_date, atomic=True, version=run_id)
                logger.info(f"  dispersion : {len(disp)} tenors "
                            f"(ρ̄ ~ {disp['implied_correlation'].mean():.2f})")
        except Exception as exc:
            logger.warning(f"dispersion (Eq.23) : {exc}")

        # Portefeuille / risk / scénarios
        try:
            self.collect_portfolio_risk(session_date, spots, run_id)
        except Exception as exc:
            logger.warning(f"portfolio/risk : {exc}")

        # QC + alertes (étapes 14/15)
        qc_results = []
        try:
            qc_results = self.compute_qc(session_date, iv_all, fwd_all, surfaces, run_id) or []
            self._emit_alerts(qc_results, session_date)
        except Exception as exc:
            logger.warning(f"qc : {exc}")

        # Statut + catalogue de métriques opérationnelles (roadmap Part XIV)
        self._status["connected"]   = self.adapter.is_healthy()
        self._status["last_cycle"]  = datetime.now(timezone.utc).isoformat()
        self._status["cycle_count"] += 1
        secs = round(time.perf_counter() - t0, 1)
        self._status["last_cycle_secs"] = secs
        self._status.setdefault("cycle_secs", []).append(secs)
        sym_stats = self._status.get("symbols", {})
        n_quotes = sum(int(s.get("n_quotes") or 0) for s in sym_stats.values())
        n_usable = sum(int(s.get("n_usable") or 0) for s in sym_stats.values())
        self._status["metrics"] = {
            "run_id": run_id,
            "symbols_ok": sum(1 for s in sym_stats.values() if s.get("n_usable")),
            "symbols_failed": sum(1 for s in sym_stats.values()
                                  if not s.get("n_usable")),
            "quotes_total": n_quotes,
            "usable_total": n_usable,
            "usable_ratio": round(n_usable / n_quotes, 4) if n_quotes else None,
            "quote_rate_per_sec": round(n_quotes / secs, 2) if secs else None,
            "qc_pass": sum(1 for r in qc_results if getattr(r, "status", "") == "pass"),
            "qc_warn": sum(1 for r in qc_results if getattr(r, "status", "") == "warn"),
            "qc_fail": sum(1 for r in qc_results if getattr(r, "status", "") == "fail"),
        }
        logger.info(f"Cycle {idx} terminé en {secs}s")
        _atomic_write_json(STATUS_FILE, self._status)

    # ------------------------------------------------------------------
    # Boucle principale
    # ------------------------------------------------------------------

    def main_loop(self) -> None:
        if not self.connect():
            self._status["connected"] = False
            _atomic_write_json(STATUS_FILE, self._status)
            return

        logger.info(f"Collecteur démarré — symboles : {self.symbols} | cycle {self.interval}s")
        try:
            while self._running:
                # Heartbeat (tickle + statut d'auth) ; reconnecte si la session a expiré.
                if not self.adapter.heartbeat():
                    logger.warning("Session dégradée — tentative de reconnexion…")
                    self._status["connected"] = False
                    _atomic_write_json(STATUS_FILE, self._status)
                    if not self.adapter.reconnect():
                        logger.error("Reconnexion KO — re-login gateway requis ?")
                        time.sleep(self.interval)
                        continue

                logger.info(f"── Cycle {self._status['cycle_count']} ──")
                self.run_cycle()
                if self.max_cycles and self._status["cycle_count"] >= self.max_cycles:
                    logger.info(f"--max-cycles {self.max_cycles} atteint — arrêt.")
                    break
                time.sleep(self.interval)
        except KeyboardInterrupt:
            logger.info("Arrêt demandé (Ctrl+C)")
        finally:
            self._running = False
            self._status["connected"] = False
            self._status["stopped_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_write_json(STATUS_FILE, self._status)
            try:
                self.adapter.disconnect()
            except Exception:
                pass
            logger.info("Collecteur arrêté.")

    def stop(self) -> None:
        self._running = False


# ---------------------------------------------------------------------------
# Entrée CLI
# ---------------------------------------------------------------------------

def main():
    from src.utils.config import load_config

    broker_cfg = load_config("broker")
    wcfg = broker_cfg.get("webapi", {})

    parser = argparse.ArgumentParser(description="Collecteur IBKR (Web API) autonome")
    parser.add_argument("--host", default=wcfg.get("host", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=wcfg.get("port", 5000))
    parser.add_argument("--account-id", default=os.getenv("IBKR_ACCOUNT_ID"),
                        help="compte paper (DU…) ; auto-découvert si omis")
    parser.add_argument("--interval", type=int, default=120,
                        help="secondes entre deux cycles complets")
    parser.add_argument("--symbol-pause", type=float, default=1.0,
                        help="pause entre symboles (pacing)")
    parser.add_argument("--max-cycles", type=int, default=0,
                        help="0 = boucle infinie ; N = s'arrête après N cycles")
    parser.add_argument("--log-level", default="INFO",
                        help="niveau loguru (INFO par défaut ; DEBUG pour diagnostic)")
    args = parser.parse_args()

    # Logs structurés -> console + fichier logs/vol_infra_<date>.log (rotation 10 Mo).
    from src.utils.logging_helpers import setup_logger
    setup_logger(log_dir=str(ROOT / "logs"), level=args.log_level)

    account_id = args.account_id
    if account_id in (None, "", "DU0000000"):
        account_id = None   # l'adaptateur le découvrira après authentification

    collector = Collector(
        host=args.host, port=args.port, account_id=account_id,
        use_oauth=bool(wcfg.get("use_oauth", False)),
        interval=args.interval, symbol_pause=args.symbol_pause,
        max_cycles=args.max_cycles,
    )

    try:
        collector.main_loop()
    except KeyboardInterrupt:
        logger.info("Arrêt demandé (Ctrl+C)")
        collector.stop()


if __name__ == "__main__":
    main()
