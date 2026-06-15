"""Convergence IV — régression du 12/06 : ESTX50 bloqué à 0.75.
Deux causes corrigées : (1) critère de convergence ABSOLU (price_tol*100=1e-4)
mal calibré pour des options d'indice valant des dizaines d'euros → vols correctes
rejetées ; (2) quotes sous l'intrinsèque (no IV) comptées comme échecs du solveur."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import math
import pandas as pd

from src.iv.solver import solve_iv
from src.qc.checks import check_iv_convergence
from src.pricing.european import bs_price


def test_relative_tolerance_accepts_correct_vol_on_pricey_option():
    # Option d'indice chère : prix exact à sigma=0.18, le solveur doit CONVERGER
    # (l'ancien seuil absolu 1e-4 rejetait un résidu de ~1e-4 pourtant négligeable).
    F, K, T, r, sigma = 6000.0, 6000.0, 0.5, 0.025, 0.18
    price = bs_price(F, K, sigma, T, r, "C")          # ~ plusieurs centaines
    res = solve_iv(price, F, K, T, r, "C")
    assert res.converged
    assert abs(res.implied_vol - sigma) < 1e-3


def test_below_intrinsic_excluded_from_denominator():
    # 8 quotes solvables convergées + 2 quotes sous l'intrinsèque (insolubles) :
    # le ratio doit être 8/8 = 1.0, pas 8/10 = 0.8.
    rows = []
    for i in range(8):
        rows.append({"underlying_symbol": "X", "converged": True,
                     "is_usable": True, "iv_solvable": True})
    for i in range(2):
        rows.append({"underlying_symbol": "X", "converged": False,
                     "is_usable": True, "iv_solvable": False})
    df = pd.DataFrame(rows)
    res = check_iv_convergence(df, "X", 0.97)
    assert res.status == "pass"
    assert abs(res.measured_value - 1.0) < 1e-9
    assert res.context["n_solvable"] == 8 and res.context["n_total"] == 10


def test_genuine_solver_miss_still_counts():
    # Un échec du solveur sur une quote SOLVABLE (residual_above_tolerance) doit, lui,
    # rester compté contre le ratio — on ne masque que l'insoluble.
    rows = [{"underlying_symbol": "Y", "converged": True, "is_usable": True,
             "iv_solvable": True} for _ in range(8)]
    rows += [{"underlying_symbol": "Y", "converged": False, "is_usable": True,
              "iv_solvable": True} for _ in range(2)]
    df = pd.DataFrame(rows)
    res = check_iv_convergence(df, "Y", 0.97)
    assert abs(res.measured_value - 0.8) < 1e-9     # 8/10, pas masqué
    assert res.status == "fail"


def test_backward_compat_without_new_columns():
    # Anciennes données sans is_usable/iv_solvable → comportement d'origine.
    df = pd.DataFrame([{"underlying_symbol": "Z", "converged": c}
                       for c in [True] * 97 + [False] * 3])
    res = check_iv_convergence(df, "Z", 0.97)
    assert res.status == "pass" and abs(res.measured_value - 0.97) < 1e-9
