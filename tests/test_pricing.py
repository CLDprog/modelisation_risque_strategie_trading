"""
Unit tests for European and American pricers.
Tests: Black-Scholes identities, Greeks sign conventions, put-call parity.
"""
import math
import pytest
from src.pricing.european import (
    bs_call, bs_put, bs_delta, bs_gamma, bs_vega, bs_theta,
    dollar_gamma, dollar_vega, price_european,
)
from src.pricing.american import price_american_binomial


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ATMS = dict(forward=100.0, strike=100.0, sigma=0.20, maturity=0.5, rate=0.05, spot=98.0)


# ---------------------------------------------------------------------------
# Black-Scholes call/put prices
# ---------------------------------------------------------------------------

def test_call_positive():
    c = bs_call(**{k: v for k, v in ATMS.items() if k != 'spot'})
    assert c > 0, "ATM call must be positive"


def test_put_positive():
    p = bs_put(**{k: v for k, v in ATMS.items() if k != 'spot'})
    assert p > 0, "ATM put must be positive"


def test_put_call_parity():
    """C - P = (F - K) * e^(-rT)  — Black-76 form."""
    F, K, s, T, r = ATMS["forward"], ATMS["strike"], ATMS["sigma"], ATMS["maturity"], ATMS["rate"]
    c = bs_call(F, K, s, T, r)
    p = bs_put(F, K, s, T, r)
    parity_rhs = (F - K) * math.exp(-r * T)
    assert abs((c - p) - parity_rhs) < 1e-8, "Put-call parity violated"


def test_call_intrinsic_at_expiry():
    """Call price at T=0 = max(F-K, 0)."""
    assert abs(bs_call(110, 100, 0.2, 0, 0.05) - 10.0) < 1e-6


def test_put_intrinsic_at_expiry():
    """Put price at T=0 = max(K-F, 0)."""
    assert abs(bs_put(100, 110, 0.2, 0, 0.05) - 10.0) < 1e-6


def test_call_deep_itm():
    """Deep ITM call ≈ discount * (F - K)."""
    c = bs_call(200, 100, 0.2, 1.0, 0.05)
    expected = math.exp(-0.05) * (200 - 100)
    assert abs(c - expected) < 1.0  # within $1


def test_put_deep_itm():
    """Deep ITM put ≈ discount * (K - F)."""
    p = bs_put(50, 100, 0.2, 1.0, 0.05)
    expected = math.exp(-0.05) * (100 - 50)
    assert abs(p - expected) < 1.0


# ---------------------------------------------------------------------------
# Greeks sign conventions
# ---------------------------------------------------------------------------

def test_call_delta_positive():
    delta = bs_delta(ATMS["forward"], ATMS["strike"], ATMS["sigma"],
                     ATMS["maturity"], ATMS["rate"], "C", ATMS["spot"])
    assert 0 < delta < 1, f"Call delta should be in (0,1), got {delta}"


def test_put_delta_negative():
    delta = bs_delta(ATMS["forward"], ATMS["strike"], ATMS["sigma"],
                     ATMS["maturity"], ATMS["rate"], "P", ATMS["spot"])
    assert -1 < delta < 0, f"Put delta should be in (-1,0), got {delta}"


def test_gamma_positive():
    gamma = bs_gamma(ATMS["forward"], ATMS["strike"], ATMS["sigma"],
                     ATMS["maturity"], ATMS["rate"], ATMS["spot"])
    assert gamma > 0, "Gamma must always be positive"


def test_vega_positive():
    vega = bs_vega(ATMS["forward"], ATMS["strike"], ATMS["sigma"],
                   ATMS["maturity"], ATMS["rate"])
    assert vega > 0, "Vega must always be positive for long options"


def test_call_theta_negative():
    theta = bs_theta(ATMS["forward"], ATMS["strike"], ATMS["sigma"],
                     ATMS["maturity"], ATMS["rate"], "C")
    assert theta < 0, "Call theta must be negative (time decay)"


def test_delta_call_minus_put_equals_discount():
    """delta_call - delta_put = e^(-rT)  [put-call delta identity in Black-76]."""
    F, K, s, T, r = ATMS["forward"], ATMS["strike"], ATMS["sigma"], ATMS["maturity"], ATMS["rate"]
    d_call = bs_delta(F, K, s, T, r, "C", ATMS["spot"])
    d_put = bs_delta(F, K, s, T, r, "P", ATMS["spot"])
    disc = math.exp(-r * T)
    # d_call = disc*N(d1), d_put = -disc*N(-d1) = disc*(N(d1)-1)
    # → d_call - d_put = disc
    assert abs((d_call - d_put) - disc) < 1e-8


# ---------------------------------------------------------------------------
# Monetized Greeks
# ---------------------------------------------------------------------------

def test_dollar_gamma_positive():
    g = bs_gamma(100, 100, 0.2, 0.5, 0.05, 100)
    dg = dollar_gamma(g, 100, 100)
    assert dg > 0


def test_dollar_vega_positive():
    v = bs_vega(100, 100, 0.2, 0.5, 0.05)
    dv = dollar_vega(v, 100)
    assert dv > 0


# ---------------------------------------------------------------------------
# American pricer — degenerate cases
# ---------------------------------------------------------------------------

def test_american_call_no_dividends_equals_european():
    """For a call with no dividends (carry=0), American = European price."""
    from src.pricing.european import bs_call
    amer = price_american_binomial(100, 100, 0.20, 0.5, 0.05, 0.0, "C", steps=300)
    euro = bs_call(100 * math.exp(0.05 * 0.5), 100, 0.20, 0.5, 0.05)
    # American call without dividends = European call
    assert abs(amer.price - euro) < 0.05, f"American call={amer.price:.4f}, European={euro:.4f}"


def test_american_put_ge_european():
    """American put >= European put (early exercise premium)."""
    from src.pricing.european import bs_put
    amer = price_american_binomial(100, 100, 0.20, 1.0, 0.05, 0.03, "P", steps=200)
    euro = bs_put(100 * math.exp((0.05 - 0.03) * 1.0), 100, 0.20, 1.0, 0.05)
    assert amer.price >= euro - 0.01, f"American put {amer.price:.4f} < European {euro:.4f}"


def test_american_intrinsic_at_expiry():
    """At T=0, American put = intrinsic."""
    result = price_american_binomial(80, 100, 0.2, 0, 0.05, 0.0, "P", steps=50)
    assert abs(result.price - 20.0) < 0.01


# ---------------------------------------------------------------------------
# price_european convenience function
# ---------------------------------------------------------------------------

def test_price_european_result_fields():
    result = price_european(100, 100, 0.2, 0.5, 0.05, "C", 98, 100)
    assert result.price > 0
    assert 0 < result.delta < 1
    assert result.gamma > 0
    assert result.vega > 0
    assert result.theta < 0
    assert result.dollar_gamma > 0
