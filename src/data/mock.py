"""
Générateur de données simulées réalistes pour le front Dash.
Remplacé par les données IBKR live une fois TWS connecté.
"""
from __future__ import annotations
import math
import numpy as np
import pandas as pd
from datetime import date, timedelta
from src.pricing.european import bs_call, bs_put, bs_delta, bs_gamma, bs_vega, bs_theta


SPOT     = 520.0
RATE     = 0.053
CARRY    = 0.015
MATURITIES_DAYS = [7, 14, 30, 60, 90, 180]


def _smile_iv(log_moneyness: float, T: float) -> float:
    """IV réaliste avec skew négatif (put skew typique du S&P)."""
    atm_vol = 0.18 + 0.04 * math.exp(-T * 2)   # vol ATM décroît avec la maturité
    skew    = -0.12 * log_moneyness              # skew négatif
    smile   = 0.06 * log_moneyness ** 2          # sourire symétrique
    return max(atm_vol + skew + smile, 0.05)


def generate_option_chain() -> pd.DataFrame:
    """Génère une chaîne d'options SPY complète avec IVs réalistes."""
    today = date.today()
    rows = []

    for days in MATURITIES_DAYS:
        expiry = today + timedelta(days=days)
        T = days / 365.0
        F = SPOT * math.exp((RATE - CARRY) * T)

        strikes = np.arange(
            round(SPOT * 0.80 / 5) * 5,
            round(SPOT * 1.20 / 5) * 5 + 5,
            5
        ).astype(float)

        for K in strikes:
            lm = math.log(K / F)
            iv = _smile_iv(lm, T)
            total_var = iv ** 2 * T

            call_price = bs_call(F, K, iv, T, RATE)
            put_price  = bs_put(F, K, iv, T, RATE)

            for right, price in [("C", call_price), ("P", put_price)]:
                spread = price * 0.02 + 0.05
                rows.append({
                    "underlying_symbol": "SPY",
                    "expiry":            expiry.isoformat(),
                    "maturity_years":    round(T, 6),
                    "days_to_expiry":    days,
                    "strike":            K,
                    "right":             right,
                    "forward":           round(F, 4),
                    "log_moneyness":     round(lm, 6),
                    "implied_vol":       round(iv, 6),
                    "total_variance":    round(total_var, 6),
                    "mid_price":         round(price, 4),
                    "bid":               round(price - spread / 2, 4),
                    "ask":               round(price + spread / 2, 4),
                    "delta":             round(bs_delta(F, K, iv, T, RATE, right, SPOT), 6),
                    "gamma":             round(bs_gamma(F, K, iv, T, RATE, SPOT), 8),
                    "vega":              round(bs_vega(F, K, iv, T, RATE), 6),
                    "theta":             round(bs_theta(F, K, iv, T, RATE, right), 6),
                    "is_usable":         True,
                    "converged":         True,
                })

    return pd.DataFrame(rows)


def generate_forward_curve() -> pd.DataFrame:
    today = date.today()
    rows = []
    for days in MATURITIES_DAYS:
        T = days / 365.0
        F = SPOT * math.exp((RATE - CARRY) * T)
        rows.append({
            "expiry":           (today + timedelta(days=days)).isoformat(),
            "days_to_expiry":   days,
            "maturity_years":   round(T, 6),
            "chosen_forward":   round(F, 4),
            "implied_carry":    round(CARRY, 6),
            "confidence_score": round(0.85 + np.random.uniform(-0.05, 0.05), 3),
            "quality_flag":     "high",
            "candidates_used":  np.random.randint(6, 12),
        })
    return pd.DataFrame(rows)


def generate_portfolio() -> pd.DataFrame:
    """Portefeuille fictif : short strangle sur SPY."""
    today = date.today()
    expiry_30 = (today + timedelta(days=30)).isoformat()
    expiry_60 = (today + timedelta(days=60)).isoformat()

    positions = [
        {"contract_key": "SPY|OPT|30d|530|C", "underlying_symbol": "SPY",
         "expiry": expiry_30, "strike": 530.0, "right": "C",
         "quantity": -10, "multiplier": 100},
        {"contract_key": "SPY|OPT|30d|510|P", "underlying_symbol": "SPY",
         "expiry": expiry_30, "strike": 510.0, "right": "P",
         "quantity": -10, "multiplier": 100},
        {"contract_key": "SPY|OPT|60d|540|C", "underlying_symbol": "SPY",
         "expiry": expiry_60, "strike": 540.0, "right": "C",
         "quantity": -5, "multiplier": 100},
        {"contract_key": "SPY|OPT|60d|500|P", "underlying_symbol": "SPY",
         "expiry": expiry_60, "strike": 500.0, "right": "P",
         "quantity": -5, "multiplier": 100},
    ]

    chain = generate_option_chain()
    rows = []
    for pos in positions:
        T  = (date.fromisoformat(pos["expiry"]) - today).days / 365.0
        F  = SPOT * math.exp((RATE - CARRY) * T)
        K  = pos["strike"]
        r  = pos["right"]
        lm = math.log(K / F)
        iv = _smile_iv(lm, T)
        price = bs_call(F, K, iv, T, RATE) if r == "C" else bs_put(F, K, iv, T, RATE)
        delta = bs_delta(F, K, iv, T, RATE, r, SPOT)
        gamma = bs_gamma(F, K, iv, T, RATE, SPOT)
        vega  = bs_vega(F, K, iv, T, RATE)
        theta = bs_theta(F, K, iv, T, RATE, r)
        q, m  = pos["quantity"], pos["multiplier"]
        rows.append({
            **pos,
            "spot": SPOT, "forward": F, "implied_vol": iv,
            "maturity_years": T, "price": price,
            "delta": delta, "gamma": gamma, "vega": vega, "theta": theta,
            "dollar_gamma": gamma * SPOT**2 * m / 100,
            "dollar_vega":  vega * m,
            "portfolio_delta": delta * q * m,
            "portfolio_gamma": gamma * q * m,
            "portfolio_vega":  vega  * q * m,
            "portfolio_theta": theta * q * m,
            "pnl_approx":      price * q * m,
        })
    return pd.DataFrame(rows)


def generate_scenarios() -> pd.DataFrame:
    chain = generate_option_chain()
    portfolio = generate_portfolio()
    scenarios = [
        ("spot_dn_20pct", -0.20,  0.10, 0, "Crash: spot -20%, vol +10pts"),
        ("spot_dn_10pct", -0.10,  0.05, 0, "Correction: spot -10%, vol +5pts"),
        ("spot_up_10pct",  0.10, -0.03, 0, "Rally: spot +10%, vol -3pts"),
        ("vol_spike",      0.00,  0.15, 0, "Vol spike +15pts"),
        ("vol_crush",      0.00, -0.10, 0, "Vol crush -10pts"),
        ("theta_1day",     0.00,  0.00, 1, "Theta roll +1j"),
        ("theta_5days",    0.00,  0.00, 5, "Theta roll +5j"),
    ]
    rows = []
    for sid, ds, dv, dt_days, desc in scenarios:
        total_pnl = 0
        for _, pos in portfolio.iterrows():
            T  = pos["maturity_years"]
            F0 = pos["forward"]
            iv0 = pos["implied_vol"]
            K  = pos["strike"]
            r  = pos["right"]
            q  = pos["quantity"]
            m  = pos["multiplier"]

            F1  = F0 * (1 + ds)
            iv1 = max(iv0 + dv, 0.01)
            T1  = max(T - dt_days / 365, 0)

            p0 = bs_call(F0, K, iv0, T, RATE) if r == "C" else bs_put(F0, K, iv0, T, RATE)
            p1 = bs_call(F1, K, iv1, T1, RATE) if r == "C" else bs_put(F1, K, iv1, T1, RATE)
            pnl = (p1 - p0) * q * m
            total_pnl += pnl
            rows.append({
                "scenario_id": sid,
                "description": desc,
                "contract_key": pos["contract_key"],
                "underlying_symbol": "SPY",
                "spot_shift_pct": ds,
                "vol_shift_abs": dv,
                "time_roll_days": dt_days,
                "base_price": round(p0, 4),
                "scenario_price": round(p1, 4),
                "scenario_pnl": round(pnl, 2),
                "quantity": q,
                "multiplier": m,
            })
    return pd.DataFrame(rows)


def generate_qc() -> pd.DataFrame:
    checks = [
        ("collector_continuity", "SPY|STK",      "pass", "info",    12.3,  30.0, "ok"),
        ("quote_health",         "SPY",           "pass", "info",    0.018, 0.25, "ok"),
        ("forward_stability",    "SPY",           "pass", "info",    0.87,  0.40, "ok"),
        ("iv_convergence",       "SPY",           "pass", "info",    0.982, 0.97, "ok"),
        ("surface_fit",          "SPY",           "warn", "warning", 0.019, 0.02, "high_rmse"),
        ("scenario_completeness","portfolio",     "pass", "info",    1.0,   1.0,  "ok"),
        ("calendar_monotonicity","SPY",           "pass", "info",    1.0,   1.0,  "ok"),
        ("greek_sanity",         "SPY",           "pass", "info",    0.0003,0.01, "ok"),
    ]
    rows = [
        {"check_name": c, "target_key": t, "status": s, "severity": sev,
         "measured_value": mv, "threshold": th, "reason_code": r}
        for c, t, s, sev, mv, th, r in checks
    ]
    return pd.DataFrame(rows)
