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
from scipy.interpolate import PchipInterpolator
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

    # MULTI-DÉPART : avec peu de points (6-10), SLSQP depuis une seule graine tombe
    # parfois sur le minimum local DÉGÉNÉRÉ b≈0 (smile plat — constaté sur ESTX50
    # JUN27 : b=0 avec RMSE 0.005 alors qu'un fit skewé existe). On essaie plusieurs
    # graines (dont des skews négatifs typiques equity) et on garde le meilleur RMSE.
    atm_w = float(np.median(w))
    starts = [
        [atm_w * 0.5, 0.10, 0.0, 0.0, 0.10],
        [atm_w * 0.7, 0.05, -0.5, 0.05, 0.15],
        [atm_w * 0.3, 0.20, -0.7, 0.10, 0.20],
        [atm_w * 0.9, 0.02, -0.3, -0.05, 0.05],
    ]
    bounds = [(1e-6, None), (0, None), (-0.999, 0.999), (-1, 1), (1e-4, None)]

    try:
        best = None
        for x0 in starts:
            r = minimize(objective, x0, method="SLSQP",
                         bounds=bounds, constraints=svi_constraints(),
                         options={"maxiter": 500, "ftol": 1e-10})
            if r.success and (best is None or r.fun < best.fun):
                best = r
        if best is None:
            raise RuntimeError("aucun départ SVI n'a convergé")
        res = best
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
    """Fallback quand le SVI échoue : interpolation SHAPE-PRESERVING (PCHIP, sans
    overshoot) de la variance totale, évaluée UNIQUEMENT sur la plage observée et
    bornée à l'enveloppe des points. Remplace UnivariateSpline (degré 3, lissage
    minuscule) qui overshootait jusqu'à des variances absurdes (−9e12 constaté sur
    AI 2028, points épars / quasi-dupliqués) → variance négative propagée au check
    calendaire et à la surface. PCHIP est monotone par morceaux : aucune oscillation
    entre les nœuds."""
    try:
        # PCHIP exige des abscisses strictement croissantes : on trie et on fond les
        # strikes au même log-moneyness (sinon pente quasi-verticale → blowup).
        idx = np.argsort(k)
        ks, ws = np.asarray(k, dtype=float)[idx], np.asarray(w, dtype=float)[idx]
        uk, inv = np.unique(np.round(ks, 8), return_inverse=True)
        if len(uk) < len(ks):
            ws = np.array([ws[inv == i].mean() for i in range(len(uk))])
            ks = uk
        if len(ks) < 2:
            raise ValueError("moins de 2 points distincts pour l'interpolation")

        interp = PchipInterpolator(ks, ws, extrapolate=False)
        k_grid = np.linspace(ks.min(), ks.max(), 50)
        # Garde-fou : variance totale positive et dans l'enveloppe observée élargie.
        # PCHIP n'overshoote pas — le clip est une ceinture+bretelles.
        w_lo, w_hi = max(float(ws.min()) * 0.5, 1e-8), float(ws.max()) * 1.5
        w_grid = np.clip(np.nan_to_num(interp(k_grid), nan=float(ws.mean())), w_lo, w_hi)
        w_hat = np.clip(np.nan_to_num(interp(ks), nan=float(ws.mean())), w_lo, w_hi)
        rmse = float(np.sqrt(np.mean((w_hat - ws) ** 2)))
        max_err = float(np.max(np.abs(w_hat - ws)))
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


def interpolate_across_maturities(surface_df: pd.DataFrame,
                                  target_maturities: List[float]) -> pd.DataFrame:
    """
    Eq. 22 — interpolation linéaire en VARIANCE TOTALE entre tranches calibrées :

        w(k,T) = [(T2−T)·w(k,T1) + (T−T1)·w(k,T2)] / (T2−T1)

    `surface_df` = grille calibrée d'UN sous-jacent (colonnes log_moneyness,
    maturity_years, total_variance — schéma de surface_to_dataframe).
    `target_maturities` = maturités cibles en années (ex. les tenors exacts de la grille
    spec : 30/91/…/730 jours, que les échéances listées n'atteignent jamais exactement).

    Hors de la plage calibrée [T_min, T_max], on CLAMPE sur la tranche la plus proche
    (pas d'extrapolation : on n'invente pas de variance). Retourne le même schéma avec
    model='eq22_interp'.
    """
    if surface_df is None or surface_df.empty:
        return pd.DataFrame()
    req = {"log_moneyness", "maturity_years", "total_variance"}
    if not req.issubset(surface_df.columns):
        return pd.DataFrame()

    underlying  = surface_df["underlying"].iloc[0] if "underlying" in surface_df.columns else ""
    snapshot_ts = surface_df["snapshot_ts"].iloc[0] if "snapshot_ts" in surface_df.columns else ""

    # w(k) par tranche, sur la grille k commune (pivot ; les k diffèrent rarement,
    # un interp 1D par tranche aligne le tout proprement).
    slices = {}
    for T, grp in surface_df.groupby("maturity_years"):
        grp = grp.sort_values("log_moneyness")
        slices[float(T)] = (grp["log_moneyness"].to_numpy(dtype=float),
                            grp["total_variance"].to_numpy(dtype=float))
    Ts = sorted(slices)
    if not Ts:
        return pd.DataFrame()
    k_common = slices[Ts[0]][0]

    def w_on_common(T: float) -> np.ndarray:
        k, w = slices[T]
        return np.interp(k_common, k, w)

    rows = []
    for T in target_maturities:
        T = float(T)
        if T <= 0:
            continue
        if T <= Ts[0]:
            w_t, t1, t2 = w_on_common(Ts[0]), Ts[0], Ts[0]          # clamp court
        elif T >= Ts[-1]:
            w_t, t1, t2 = w_on_common(Ts[-1]), Ts[-1], Ts[-1]        # clamp long
        else:
            t2 = min(t for t in Ts if t >= T)
            t1 = max(t for t in Ts if t <= T)
            if t1 == t2:
                w_t = w_on_common(t1)
            else:
                lam = (t2 - T) / (t2 - t1)
                w_t = lam * w_on_common(t1) + (1.0 - lam) * w_on_common(t2)
        w_t = np.maximum(w_t, 1e-12)
        for k, w in zip(k_common, w_t):
            rows.append({
                "underlying": underlying, "snapshot_ts": snapshot_ts,
                "maturity_years": T, "log_moneyness": float(k),
                "total_variance": float(w),
                "implied_vol": float(math.sqrt(w / T)),
                "bracket_t1": t1, "bracket_t2": t2,
                "model": "eq22_interp",
            })
    return pd.DataFrame(rows)


def surface_params_to_dataframe(surface: SurfaceFitResult) -> pd.DataFrame:
    """
    Paramètres SVI bruts (a, b, rho, m, sigma) par tranche → table `surface_parameters`
    (roadmap : table jamais écrite). Auditabilité de la calibration (≠ surface_grid qui
    ne stocke que la grille de vol).
    """
    rows = []
    for s in surface.slices:
        p = s.svi_params or {}
        rows.append({
            "underlying": surface.underlying,
            "snapshot_ts": surface.snapshot_ts,
            "expiry": s.expiry,
            "maturity_years": s.maturity_years,
            "model": s.model,
            "n_points": s.n_points,
            "fit_rmse": s.rmse,
            "max_error": s.max_error,
            "quality_flag": s.quality_flag,
            "svi_a": p.get("a"), "svi_b": p.get("b"), "svi_rho": p.get("rho"),
            "svi_m": p.get("m"), "svi_sigma": p.get("sigma"),
            "model_version": surface.model_version,
        })
    return pd.DataFrame(rows)
