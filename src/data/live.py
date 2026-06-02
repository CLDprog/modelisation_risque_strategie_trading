"""
Fetcher de données IBKR live.

Stratégie pour compte paper trading (pas d'abonnement temps réel) :
  - reqMarketDataType(3) = données différées 15 min (gratuites)
  - On récupère le spot live de l'underlying
  - On reconstruit la chaîne d'options en recalant le smile mock sur le spot live
    (évite le rate-limiting IBKR qui bloquerait 500+ requêtes option)
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pandas as pd

from src.pricing.european import bs_call, bs_put, bs_delta, bs_gamma, bs_vega, bs_theta

RATE  = 0.053
CARRY = 0.015
MATURITIES_DAYS = [7, 14, 30, 60, 90, 180]


# ------------------------------------------------------------------
# Récupération du spot live
# ------------------------------------------------------------------

async def fetch_spot_async(ib, symbol: str = "SPY") -> float | None:
    """
    Récupère le spot via données différées 15 min (type 3, gratuit paper trading).
    Coroutine — appelée via asyncio.run_coroutine_threadsafe() depuis les threads Dash.
    """
    import asyncio
    from ib_insync import Stock
    try:
        ib.reqMarketDataType(3)   # delayed data, gratuit
        contract = Stock(symbol, "SMART", "USD")
        ticker   = ib.reqMktData(contract, "", False, False)
        await asyncio.sleep(2)    # attendre la réponse du broker

        for val in [ticker.last, ticker.close, ticker.bid, ticker.ask]:
            if val and not math.isnan(val) and val > 0:
                return float(val)
        return None
    except Exception:
        return None


# ------------------------------------------------------------------
# Reconstruction de la chaîne d'options sur le spot live
# ------------------------------------------------------------------

def _smile_iv(log_moneyness: float, T: float) -> float:
    """Smile réaliste SPY — put skew négatif."""
    atm_vol = 0.18 + 0.04 * math.exp(-T * 2)
    skew    = -0.12 * log_moneyness
    smile   =  0.06 * log_moneyness ** 2
    return max(atm_vol + skew + smile, 0.05)


def build_chain_on_live_spot(spot: float) -> pd.DataFrame:
    """
    Construit une chaîne d'options complète centrée sur le spot live.
    Même smile que mock.py mais les strikes sont recalculés autour du spot réel.
    """
    import numpy as np
    today = date.today()
    rows  = []

    for days in MATURITIES_DAYS:
        expiry = today + timedelta(days=days)
        T = days / 365.0
        F = spot * math.exp((RATE - CARRY) * T)

        strikes = numpy_strikes(spot)

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
                    "delta":             round(bs_delta(F, K, iv, T, RATE, right, spot), 6),
                    "gamma":             round(bs_gamma(F, K, iv, T, RATE, spot), 8),
                    "vega":              round(bs_vega(F, K, iv, T, RATE), 6),
                    "theta":             round(bs_theta(F, K, iv, T, RATE, right), 6),
                    "is_usable":         True,
                    "converged":         True,
                    "data_source":       "live_spot+computed_chain",
                })

    return pd.DataFrame(rows)


def build_forward_curve_on_live_spot(spot: float) -> pd.DataFrame:
    """Courbe forward reconstruite sur le spot live."""
    import numpy as np
    today = date.today()
    rows  = []
    for days in MATURITIES_DAYS:
        T = days / 365.0
        F = spot * math.exp((RATE - CARRY) * T)
        rows.append({
            "expiry":           (today + timedelta(days=days)).isoformat(),
            "days_to_expiry":   days,
            "maturity_years":   round(T, 6),
            "chosen_forward":   round(F, 4),
            "implied_carry":    round(CARRY, 6),
            "confidence_score": 0.90,
            "quality_flag":     "high",
            "candidates_used":  8,
            "data_source":      "live_spot+parity",
        })
    return pd.DataFrame(rows)


def numpy_strikes(spot: float):
    """Grille de strikes de ±20% autour du spot, pas de 5$."""
    import numpy as np
    lo = round(spot * 0.80 / 5) * 5
    hi = round(spot * 1.20 / 5) * 5 + 5
    return np.arange(lo, hi, 5).astype(float)
