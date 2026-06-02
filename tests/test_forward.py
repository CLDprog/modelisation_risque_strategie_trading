"""
Unit tests for the forward and carry engine.
Tests: parity formula, weighted mean, robust z-score, implied carry.
"""
import math
import pytest
from src.forwards.engine import (
    parity_forward, weighted_mean, robust_zscore_mad,
    implied_carry, estimate_forward,
)


# ---------------------------------------------------------------------------
# Parity forward
# ---------------------------------------------------------------------------

def test_parity_forward_atm():
    """At-the-money: call_mid ≈ put_mid → forward ≈ strike * e^(rT)."""
    F = 100.0
    K = 100.0
    r = 0.05
    T = 0.5
    # Perfect parity: C - P = (F - K)*e^(-rT) = 0 at ATM forward
    call_mid = 5.0
    put_mid = call_mid  # C = P when F = K (with same numerics)
    result = parity_forward(K, call_mid, put_mid, r, T)
    # F_i = K + e^(rT) * (C - P) = K + 0 = 100
    assert result == pytest.approx(100.0, abs=1e-8)


def test_parity_forward_call_premium():
    """When C > P, forward > strike."""
    result = parity_forward(100, 10, 5, 0.05, 1.0)
    assert result > 100


def test_parity_forward_put_premium():
    """When P > C, forward < strike."""
    result = parity_forward(100, 5, 10, 0.05, 1.0)
    assert result < 100


# ---------------------------------------------------------------------------
# Weighted mean
# ---------------------------------------------------------------------------

def test_weighted_mean_uniform():
    values = [1.0, 2.0, 3.0]
    weights = [1.0, 1.0, 1.0]
    result = weighted_mean(values, weights)
    assert result == pytest.approx(2.0)


def test_weighted_mean_concentrated():
    values = [1.0, 10.0]
    weights = [0.0, 1.0]
    result = weighted_mean(values, weights)
    assert result == pytest.approx(10.0)


def test_weighted_mean_zero_weights_returns_median():
    values = [1.0, 2.0, 3.0]
    weights = [0.0, 0.0, 0.0]
    result = weighted_mean(values, weights)
    assert result == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# Robust z-score (MAD)
# ---------------------------------------------------------------------------

def test_robust_zscore_outlier():
    values = [100.0, 100.1, 100.0, 99.9, 200.0]  # 200 is an outlier
    z_scores = robust_zscore_mad(values)
    assert abs(z_scores[-1]) > 5.0, "Outlier should have large z-score"


def test_robust_zscore_uniform():
    values = [1.0, 1.0, 1.0, 1.0]
    z_scores = robust_zscore_mad(values)
    for z in z_scores:
        assert z == pytest.approx(0.0, abs=1e-8)


# ---------------------------------------------------------------------------
# Implied carry
# ---------------------------------------------------------------------------

def test_implied_carry_no_dividends():
    """With carry=0, forward = spot * e^(rT)."""
    spot = 100.0
    r = 0.05
    T = 1.0
    forward = spot * math.exp(r * T)
    q = implied_carry(spot, forward, r, T)
    assert q == pytest.approx(0.0, abs=1e-6)


def test_implied_carry_positive_dividend():
    """With positive dividends, carry > 0."""
    spot = 100.0
    r = 0.05
    q_true = 0.02
    T = 1.0
    forward = spot * math.exp((r - q_true) * T)
    q = implied_carry(spot, forward, r, T)
    assert q == pytest.approx(q_true, abs=1e-6)


def test_implied_carry_nan_on_zero_spot():
    result = implied_carry(0, 100, 0.05, 1.0)
    assert math.isnan(result)
