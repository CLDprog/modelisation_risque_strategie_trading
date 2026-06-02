"""
Unit tests for the implied-volatility inversion engine.
Tests: round-trip accuracy, bounds checking, failure diagnostics.
"""
import math
import pytest
from src.pricing.european import bs_call, bs_put
from src.iv.solver import solve_iv, check_price_bounds, intrinsic_value


# ---------------------------------------------------------------------------
# Round-trip: price → IV → price
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sigma_true,right,forward,strike,maturity", [
    (0.20, "C", 100, 100, 0.5),
    (0.30, "P", 100, 90, 1.0),
    (0.15, "C", 100, 110, 0.25),
    (0.40, "P", 100, 80, 2.0),
    (0.05, "C", 100, 100, 0.1),    # low vol
    (1.50, "P", 100, 100, 0.5),    # high vol
])
def test_round_trip_iv(sigma_true, right, forward, strike, maturity):
    rate = 0.05
    if right == "C":
        price = bs_call(forward, strike, sigma_true, maturity, rate)
    else:
        price = bs_put(forward, strike, sigma_true, maturity, rate)

    result = solve_iv(price, forward, strike, maturity, rate, right,
                      contract_key="test")

    assert result.converged, f"IV solver did not converge: {result.failure_reason}"
    assert result.implied_vol is not None
    assert abs(result.implied_vol - sigma_true) < 1e-4, (
        f"IV={result.implied_vol:.6f} vs true={sigma_true}"
    )


def test_round_trip_diagnostics():
    """Verify diagnostic fields are populated on success."""
    price = bs_call(100, 100, 0.20, 0.5, 0.05)
    result = solve_iv(price, 100, 100, 0.5, 0.05, "C", contract_key="spy_test")
    assert result.iterations > 0
    assert result.residual < 1e-5
    assert result.lower_bound == pytest.approx(1e-4)
    assert result.upper_bound == pytest.approx(5.0)
    assert result.failure_reason is None


# ---------------------------------------------------------------------------
# Failure cases — structured diagnostics (not NaN)
# ---------------------------------------------------------------------------

def test_non_positive_price_fails():
    result = solve_iv(-1.0, 100, 100, 0.5, 0.05, "C")
    assert not result.converged
    assert result.implied_vol is None
    assert result.failure_reason == "non_positive_price"


def test_below_intrinsic_fails():
    """Market price below intrinsic value is not solvable."""
    # Deep ITM call: intrinsic ≈ 50
    result = solve_iv(0.01, 150, 100, 1.0, 0.05, "C")
    assert not result.converged
    assert result.failure_reason is not None and "below_intrinsic" in result.failure_reason


def test_zero_maturity_fails():
    result = solve_iv(5.0, 100, 100, 0.0, 0.05, "C")
    assert not result.converged
    assert result.failure_reason == "zero_or_negative_maturity"


# ---------------------------------------------------------------------------
# Intrinsic value check
# ---------------------------------------------------------------------------

def test_intrinsic_call_itm():
    iv = intrinsic_value(110, 100, 1.0, 0.05, "C")
    assert iv > 0


def test_intrinsic_call_otm():
    iv = intrinsic_value(90, 100, 1.0, 0.05, "C")
    assert iv == 0.0


def test_intrinsic_put_itm():
    iv = intrinsic_value(90, 100, 1.0, 0.05, "P")
    assert iv > 0


# ---------------------------------------------------------------------------
# check_price_bounds
# ---------------------------------------------------------------------------

def test_bounds_valid_returns_none():
    price = bs_call(100, 100, 0.20, 0.5, 0.05)
    result = check_price_bounds(price, 100, 100, 0.5, 0.05, "C")
    assert result is None
