"""
Volatility surface calibration (Step 9).

Implements:
  - SVI (Stochastic Volatility Inspired) per maturity slice   [Eq 20]
  - Spline fallback for sparse slices
  - Calendar monotonicity check                                [Eq 21]
  - Cross-maturity variance interpolation                      [Eq 22]
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Dict

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.interpolate import UnivariateSpline
from loguru import logger

from src.pricing.european import bs_call


# ---------------------------------------------------------------------------
# SVI model  (Eq 20)
# ---------------------------------------------------------------------------
# w(k) = a + b * (rho*(k - m) + sqrt((k-m)^2 + sigma^2))
# where k = log-moneyness, w = total variance

def svi_total_variance(k: np.ndarray, a: float, b: float,
                       rho: float, m: float, sigma: float) -> np.ndarray:
    """Eq 20: SVI total variance slice."""
    inner = rho * (k - m) + np.sqrt((k - m) ** 2 + sigma ** 2)
    return a + b * inner


def svi_constraints() -> list:
    """Basic no-arbitrage parameter constraints for SVI."""
    # a > 0, b >= 0, |rho| < 1, sigma > 0
    return [
        {"type": "ineq", "fun": lambda p: p[0]},            # a > 0
        {"type": "ineq", "fun": lambda p: p[1]},            # b >= 0
        {"type": "ineq", "fun": lambda p: 1 - abs(p[2])},  # |rho| < 1
        {"type": "ineq", "fun": lambda p: p[4]},            # sigma > 0
    ]


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SliceFitResult:
    expiry: str
    maturity_years: float
    model: str                      # "svi" | "spline" | "failed"
    n_points: int
    rmse: float
    max_error: float
    quality_flag: str               # "ok" | "sparse" | "failed"
    svi_params: Optional[Dict] = None    # {a, b, rho, m, sigma}
    k_grid: Optional[np.ndarray] = None
    w_grid: Optional[np.ndarray] = None  # total variance on grid
    sigma_grid: Optional[np.ndarray] = None  # IV on grid


@dataclass
class SurfaceFitResult:
    underlying: str
    snapshot_ts: str
    slices: List[SliceFitResult] = field(default_factory=list)
    calendar_ok: bool = True
    butterfly_ok: bool = True            # no-arbitrage papillon (convexité strike)
    n_butterfly_violations: int = 0
    model_version: str = "svi_v1"


# ---------------------------------------------------------------------------
# Per-slice fitting
# ---------------------------------------------------------------------------

def fit_svi_slice(k: np.ndarray, w: np.ndarray,
                  expiry: str, maturity_years: float,
                  min_points: int = 5, max_rmse: float = 0.02) -> SliceFitResult:
    """
    Fit SVI to a single maturity slice.
    Falls back to spline if SVI fails or insufficient points.
    """
    n = len(k)

    if n < min_points:
        return SliceFitResult(
            expiry=expiry, maturity_years=maturity_years,
            model="failed", n_points=n, rmse=float("inf"),
            max_error=float("inf"), quality_flag="failed",
        )

    # --- SVI fit ---
    def objective(params):
        w_hat = svi_total_variance(k, *params)
        return np.mean((w_hat - w) ** 2)

    # Initial guess: a=atm_vol, b=0.1, rho=0, m=0, sigma=0.1
    atm_w = float(np.median(w))
    x0 = [atm_w * 0.5, 0.1, 0.0, 0.0, 0.1]
    bounds = [(1e-6, None), (0, None), (-0.999, 0.999), (-1, 1), (1e-4, None)]

    try:
        res = minimize(objective, x0, method="SLSQP",
                       bounds=bounds, constraints=svi_constraints(),
                       options={"maxiter": 500, "ftol": 1e-10})
        a, b, rho, m, sigma = res.x
        w_hat = svi_total_variance(k, a, b, rho, m, sigma)
        residuals = w_hat - w
        rmse = float(np.sqrt(np.mean(residuals ** 2)))
        max_err = float(np.max(np.abs(residuals)))

        if rmse <= max_rmse and res.success:
            k_grid = np.linspace(k.min(), k.max(), 50)
            w_grid = svi_total_variance(k_grid, a, b, rho, m, sigma)
            sigma_grid = np.sqrt(np.maximum(w_grid / maturity_years, 0))
            return SliceFitResult(
                expiry=expiry, maturity_years=maturity_years,
                model="svi", n_points=n, rmse=rmse, max_error=max_err,
                quality_flag="ok" if n >= min_points * 2 else "sparse",
                svi_params={"a": a, "b": b, "rho": rho, "m": m, "sigma": sigma},
                k_grid=k_grid, w_grid=w_grid, sigma_grid=sigma_grid,
            )
    except Exception as exc:
        logger.debug(f"SVI fit failed for {expiry}: {exc}")

    # --- Spline fallback ---
    return _spline_fallback(k, w, expiry, maturity_years, n, max_rmse)


def _spline_fallback(k: np.ndarray, w: np.ndarray,
                     expiry: str, maturity_years: float,
                     n: int, max_rmse: float) -> SliceFitResult:
    try:
        order = min(3, n - 1)
        spline = UnivariateSpline(k, w, k=order, s=len(k) * 1e-4)
        w_hat = spline(k)
        rmse = float(np.sqrt(np.mean((w_hat - w) ** 2)))
        max_err = float(np.max(np.abs(w_hat - w)))
        k_grid = np.linspace(k.min(), k.max(), 50)
        w_grid = spline(k_grid)
        sigma_grid = np.sqrt(np.maximum(w_grid / maturity_years, 0))
        return SliceFitResult(
            expiry=expiry, maturity_years=maturity_years,
            model="spline", n_points=n, rmse=rmse, max_error=max_err,
            quality_flag="sparse",
            k_grid=k_grid, w_grid=np.array(w_grid), sigma_grid=np.array(sigma_grid),
        )
    except Exception as exc:
        logger.warning(f"Spline fallback also failed for {expiry}: {exc}")
        return SliceFitResult(
            expiry=expiry, maturity_years=maturity_years,
            model="failed", n_points=n, rmse=float("inf"),
            max_error=float("inf"), quality_flag="failed",
        )


# ---------------------------------------------------------------------------
# Butterfly no-arbitrage : convexité des prix de calls en strike
# ---------------------------------------------------------------------------

def check_butterfly_arbitrage(k_grid: np.ndarray, iv_grid: np.ndarray,
                              maturity: float, tol: float = 1e-6) -> tuple[bool, int]:
    """
    Vérifie l'absence d'arbitrage papillon sur une tranche : les prix de calls
    (mesure forward, non actualisés) doivent être CONVEXES en strike.
    Condition : C(K_{i-1}) - 2·C(K_i) + C(K_{i+1}) >= 0.
    Retourne (ok, nombre_de_violations).
    """
    if k_grid is None or iv_grid is None or len(k_grid) < 3:
        return True, 0
    F = 1.0
    K = F * np.exp(k_grid)
    C = np.array([bs_call(F, float(K[i]), float(iv_grid[i]), maturity, 0.0)
                  for i in range(len(k_grid))])
    second_diff = C[2:] - 2.0 * C[1:-1] + C[:-2]
    n_viol = int(np.sum(second_diff < -tol))
    return n_viol == 0, n_viol


# ---------------------------------------------------------------------------
# Calendar monotonicity  (Eq 21)
# ---------------------------------------------------------------------------

def check_calendar_monotonicity(slices: List[SliceFitResult],
                                 atm_k: float = 0.0) -> bool:
    """
    Eq 21: total variance must be non-decreasing across maturities at ATM.
    Returns True if condition holds for all adjacent slice pairs.
    """
    ok_slices = [s for s in slices if s.model != "failed" and s.w_grid is not None]
    ok_slices.sort(key=lambda s: s.maturity_years)

    prev_w_atm = None
    for s in ok_slices:
        # Interpolate w at ATM from the slice grid
        if s.k_grid is None or len(s.k_grid) == 0:
            continue
        idx = int(np.argmin(np.abs(s.k_grid - atm_k)))
        w_atm = float(s.w_grid[idx])
        if prev_w_atm is not None and w_atm < prev_w_atm - 1e-6:
            logger.warning(f"Calendar violation at {s.expiry}: w_atm={w_atm:.4f} < prev={prev_w_atm:.4f}")
            return False
        prev_w_atm = w_atm
    return True


# ---------------------------------------------------------------------------
# Full surface calibration
# ---------------------------------------------------------------------------

def fit_surface(iv_points_df: pd.DataFrame, underlying: str,
                snapshot_ts: str, cfg: dict) -> SurfaceFitResult:
    """
    Fit a volatility surface from solved IV points.

    iv_points_df columns: expiry, maturity_years, log_moneyness, total_variance, converged
    """
    surface_cfg = cfg.get("surface", {})
    min_pts = surface_cfg.get("min_points_per_slice", 5)
    max_rmse = surface_cfg.get("max_rmse", 0.02)

    result = SurfaceFitResult(underlying=underlying, snapshot_ts=snapshot_ts)

    # Garde : un DataFrame vide (aucune IV résolue) n'a pas de colonnes → on sort
    # proprement avec une surface vide au lieu de lever KeyError.
    required = {"converged", "underlying_symbol", "expiry",
                "maturity_years", "log_moneyness", "total_variance"}
    if iv_points_df is None or iv_points_df.empty or not required.issubset(iv_points_df.columns):
        logger.warning(f"{underlying}: pas de points IV exploitables pour la surface")
        return result

    # Only use converged IVs
    df = iv_points_df[
        (iv_points_df["converged"] == True) &
        (iv_points_df["underlying_symbol"] == underlying)
    ].copy()

    if df.empty:
        logger.warning(f"{underlying}: no converged IV points for surface fit")
        return result

    for expiry, group in df.groupby("expiry"):
        T = group["maturity_years"].iloc[0]
        k = group["log_moneyness"].values.astype(float)
        w = group["total_variance"].values.astype(float)

        # Remove nans
        valid = np.isfinite(k) & np.isfinite(w) & (w > 0)
        k, w = k[valid], w[valid]

        if len(k) < 2:
            continue

        # Sort by moneyness
        order = np.argsort(k)
        k, w = k[order], w[order]

        slice_result = fit_svi_slice(k, w, expiry, T, min_pts, max_rmse)
        result.slices.append(slice_result)
        logger.debug(f"{underlying} {expiry}: {slice_result.model} fit, "
                     f"RMSE={slice_result.rmse:.4f}, n={slice_result.n_points}")

    result.calendar_ok = check_calendar_monotonicity(result.slices)
    if not result.calendar_ok:
        logger.warning(f"{underlying}: calendar monotonicity FAILED")

    # No-arbitrage papillon : convexité par tranche
    total_viol = 0
    for s in result.slices:
        if s.model != "failed" and s.k_grid is not None and s.sigma_grid is not None:
            ok, n = check_butterfly_arbitrage(s.k_grid, s.sigma_grid, s.maturity_years)
            total_viol += n
    result.n_butterfly_violations = total_viol
    result.butterfly_ok = (total_viol == 0)
    if not result.butterfly_ok:
        logger.warning(f"{underlying}: butterfly arbitrage — {total_viol} violations")

    return result


def surface_to_dataframe(surface: SurfaceFitResult) -> pd.DataFrame:
    """Flatten surface grid to a DataFrame for Parquet persistence."""
    rows = []
    for s in surface.slices:
        if s.k_grid is None:
            continue
        for i, k in enumerate(s.k_grid):
            rows.append({
                "underlying": surface.underlying,
                "snapshot_ts": surface.snapshot_ts,
                "expiry": s.expiry,
                "maturity_years": s.maturity_years,
                "log_moneyness": float(k),
                "total_variance": float(s.w_grid[i]),
                "implied_vol": float(s.sigma_grid[i]),
                "model": s.model,
                "fit_rmse": s.rmse,
                "quality_flag": s.quality_flag,
                "model_version": surface.model_version,
            })
    return pd.DataFrame(rows)
