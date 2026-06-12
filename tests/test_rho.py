"""Rho (sensibilité au taux) — ajout demandé par le professeur.
Convention : par POINT de taux (×0.01), spot tenu fixe (q fixe, F se réapprécie)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import math

from src.pricing.american import greeks_american
from src.pricing.european import bs_price, bs_rho, price_european

S, K, SIG, T, R = 100.0, 100.0, 0.20, 1.0, 0.025
F = S * math.exp(R * T)                       # q = 0


def test_rho_matches_finite_difference():
    h = 1e-4
    fd = (bs_price(S * math.exp((R + h) * T), K, SIG, T, R + h, "C")
          - bs_price(S * math.exp((R - h) * T), K, SIG, T, R - h, "C")) / (2 * h) * 0.01
    assert abs(bs_rho(F, K, SIG, T, R, "C") - fd) < 1e-7


def test_rho_parity_and_signs():
    rc = bs_rho(F, K, SIG, T, R, "C")
    rp = bs_rho(F, K, SIG, T, R, "P")
    assert rc > 0 and rp < 0                  # taux ↑ : call ↑, put ↓
    assert abs((rc - rp) - K * T * math.exp(-R * T) * 0.01) < 1e-12  # parité


def test_rho_grows_with_maturity():
    short = bs_rho(F, K, SIG, 0.25, R, "C")
    long_ = bs_rho(S * math.exp(R * 2.0), K, SIG, 2.0, R, "C")
    assert long_ > short > 0                  # ρ ∝ K·T·e^{-rT}·N(d2)


def test_american_rho_matches_european_for_non_dividend_call():
    # Sans dividende, un call américain ne s'exerce jamais tôt → mêmes greeks
    *_, rho_am = greeks_american(S, K, SIG, T, R, 0.0, "C")
    assert abs(rho_am - bs_rho(F, K, SIG, T, R, "C")) < 5e-4


def test_price_european_carries_rho():
    res = price_european(F, K, SIG, T, R, "P", spot=S, multiplier=10)
    assert res.rho < 0
    assert abs(res.rho - bs_rho(F, K, SIG, T, R, "P")) < 1e-12
