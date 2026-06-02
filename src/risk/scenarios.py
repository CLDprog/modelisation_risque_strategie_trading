"""
Scenario engine and margin-style diagnostics (Step 12).

Eq 19: PnL approx = delta*dS + 0.5*gamma*dS^2 + vega*d_sigma + theta*dt

Supports:
  - Full repricing under shifted spot/vol/time (source of truth)
  - Greeks-based local approximation (fast, for monitoring)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd
import numpy as np
from loguru import logger

from src.pricing.european import bs_price, bs_delta, bs_gamma, bs_vega, bs_theta
from src.risk.aggregation import Position, PositionRisk


# ---------------------------------------------------------------------------
# Scenario definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    spot_shift_pct: float        # e.g. -0.10 for -10%
    vol_shift_abs: float         # e.g. +0.05 for +5 vol pts
    time_roll_days: int          # e.g. 1 for theta roll
    description: str
    version: str = "v1.0"


def load_scenarios_from_config(cfg: dict) -> List[Scenario]:
    version = cfg.get("version", "v1.0")
    return [
        Scenario(
            scenario_id=s["scenario_id"],
            spot_shift_pct=s["spot_shift_pct"],
            vol_shift_abs=s["vol_shift_abs"],
            time_roll_days=s["time_roll_days"],
            description=s["description"],
            version=version,
        )
        for s in cfg.get("scenarios", [])
    ]


# ---------------------------------------------------------------------------
# Scenario result
# ---------------------------------------------------------------------------

@dataclass
class ScenarioLineResult:
    scenario_id: str
    portfolio_id: str
    contract_key: str
    underlying_symbol: str
    scenario_pnl_full: float        # full repricing PnL
    scenario_pnl_approx: float      # Greek-based approximation (Eq 19)
    base_price: float
    scenario_price: float
    quantity: float
    multiplier: int


@dataclass
class ScenarioReport:
    scenario_id: str
    description: str
    total_pnl_full: float
    total_pnl_approx: float
    worst_contributors: List[dict]
    line_results: List[ScenarioLineResult] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Repricing under scenario
# ---------------------------------------------------------------------------

def reprice_under_scenario(position: Position, iv_row: dict, rate: float,
                            scenario: Scenario) -> Optional[ScenarioLineResult]:
    """Full repricing of one position under scenario parameters."""
    base_forward = iv_row.get("forward", 0.0)
    base_spot = iv_row.get("reference_spot", base_forward)
    base_iv = iv_row.get("implied_vol")
    base_T = iv_row.get("maturity_years", 0.0)

    if base_iv is None or math.isnan(base_iv):
        return None

    # Apply scenario shifts
    shifted_spot = base_spot * (1 + scenario.spot_shift_pct)
    # Forward shifts proportionally with spot (simplified: no rate/carry adjustment)
    shifted_forward = base_forward * (1 + scenario.spot_shift_pct)
    shifted_iv = max(base_iv + scenario.vol_shift_abs, 1e-4)
    shifted_T = max(base_T - scenario.time_roll_days / 365.0, 0.0)

    try:
        base_price = bs_price(base_forward, position.strike, base_iv,
                               base_T, rate, position.right)
        scenario_price = bs_price(shifted_forward, position.strike, shifted_iv,
                                   shifted_T, rate, position.right)
    except Exception as exc:
        logger.debug(f"Repricing failed for {position.contract_key}: {exc}")
        return None

    q = position.quantity
    mult = position.multiplier

    pnl_full = (scenario_price - base_price) * q * mult

    # Eq 19: local approximation
    # PnL ≈ delta*dS + 0.5*gamma*dS^2 + vega*d_sigma + theta*dt
    dS = shifted_spot - base_spot
    d_sigma = scenario.vol_shift_abs
    dt = scenario.time_roll_days / 365.0

    delta = bs_delta(base_forward, position.strike, base_iv, base_T, rate, position.right, base_spot)
    gamma = bs_gamma(base_forward, position.strike, base_iv, base_T, rate, base_spot)
    vega = bs_vega(base_forward, position.strike, base_iv, base_T, rate)
    theta = bs_theta(base_forward, position.strike, base_iv, base_T, rate, position.right)

    # vega is per 1% so scale d_sigma accordingly
    pnl_approx = (delta * dS + 0.5 * gamma * dS ** 2 + vega * (d_sigma / 0.01) + theta * 365 * dt) * q * mult

    return ScenarioLineResult(
        scenario_id=scenario.scenario_id,
        portfolio_id=position.portfolio_id,
        contract_key=position.contract_key,
        underlying_symbol=position.underlying_symbol,
        scenario_pnl_full=pnl_full,
        scenario_pnl_approx=pnl_approx,
        base_price=base_price,
        scenario_price=scenario_price,
        quantity=q,
        multiplier=mult,
    )


# ---------------------------------------------------------------------------
# Full portfolio scenario engine
# ---------------------------------------------------------------------------

def run_scenario(positions: List[Position], iv_rows_by_key: dict,
                 rate: float, scenario: Scenario) -> ScenarioReport:
    """Run a single scenario across all positions."""
    line_results = []
    for pos in positions:
        iv_row = iv_rows_by_key.get(pos.contract_key)
        if iv_row is None:
            continue
        result = reprice_under_scenario(pos, iv_row, rate, scenario)
        if result is not None:
            line_results.append(result)

    total_full = sum(r.scenario_pnl_full for r in line_results)
    total_approx = sum(r.scenario_pnl_approx for r in line_results)

    # Top contributors (worst losses)
    sorted_results = sorted(line_results, key=lambda r: r.scenario_pnl_full)
    worst = [
        {
            "contract_key": r.contract_key,
            "underlying_symbol": r.underlying_symbol,
            "scenario_pnl_full": r.scenario_pnl_full,
        }
        for r in sorted_results[:5]
    ]

    return ScenarioReport(
        scenario_id=scenario.scenario_id,
        description=scenario.description,
        total_pnl_full=total_full,
        total_pnl_approx=total_approx,
        worst_contributors=worst,
        line_results=line_results,
    )


def run_all_scenarios(positions: List[Position], iv_rows_by_key: dict,
                      rate: float, scenarios: List[Scenario]) -> List[ScenarioReport]:
    reports = []
    for scenario in scenarios:
        report = run_scenario(positions, iv_rows_by_key, rate, scenario)
        logger.info(f"Scenario {scenario.scenario_id}: PnL={report.total_pnl_full:,.0f} "
                    f"(approx={report.total_pnl_approx:,.0f})")
        reports.append(report)
    return reports


def scenario_reports_to_dataframe(reports: List[ScenarioReport]) -> pd.DataFrame:
    rows = []
    for report in reports:
        for line in report.line_results:
            rows.append({
                "scenario_id": line.scenario_id,
                "portfolio_id": line.portfolio_id,
                "contract_key": line.contract_key,
                "underlying_symbol": line.underlying_symbol,
                "scenario_pnl_full": line.scenario_pnl_full,
                "scenario_pnl_approx": line.scenario_pnl_approx,
                "base_price": line.base_price,
                "scenario_price": line.scenario_price,
                "total_pnl_full": report.total_pnl_full,
                "description": report.description,
            })
    return pd.DataFrame(rows)
