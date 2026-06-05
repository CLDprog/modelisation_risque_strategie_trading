"""
Détection d'anomalies, baselines glissantes et table de triage (Step 14).

La roadmap exige plus que des checks ponctuels : il faut suivre les métriques QC
dans le temps (baselines), flagger les déviations (anomalies) et matérialiser une
table de triage exploitable par un opérateur.
"""
from __future__ import annotations

from typing import List, Dict, Optional

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Baselines glissantes
# ---------------------------------------------------------------------------

def compute_baselines(qc_history: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule, par (check_name, target_key), la médiane et le MAD de la valeur mesurée
    sur l'historique. Sert de référence robuste pour la détection d'anomalies.
    """
    if qc_history is None or qc_history.empty:
        return pd.DataFrame()
    cols = ["check_name", "target_key", "measured_value"]
    if not all(c in qc_history.columns for c in cols):
        return pd.DataFrame()

    rows = []
    for (check, target), grp in qc_history.groupby(["check_name", "target_key"]):
        vals = pd.to_numeric(grp["measured_value"], errors="coerce").values
        # Écarter les valeurs non finies (inf/nan) — p.ex. un check "no_data" qui
        # renvoie inf, qui sinon polluerait la médiane/MAD (RuntimeWarning).
        vals = vals[np.isfinite(vals)]
        if len(vals) < 3:
            continue
        med = float(np.median(vals))
        mad = float(np.median(np.abs(vals - med)))
        if not np.isfinite(mad) or mad == 0.0:
            mad = 1e-9
        rows.append({"check_name": check, "target_key": target,
                     "median": med, "mad": mad, "n_obs": len(vals)})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Détection d'anomalies (z-score robuste vs baseline)
# ---------------------------------------------------------------------------

def detect_anomalies(current: pd.DataFrame, baselines: pd.DataFrame,
                     z_threshold: float = 3.5) -> pd.DataFrame:
    """
    Compare les mesures QC du cycle courant aux baselines. Retourne les points dont
    le z-score robuste dépasse le seuil (déviation anormale vs comportement habituel).
    """
    if current is None or current.empty or baselines is None or baselines.empty:
        return pd.DataFrame()

    merged = current.merge(baselines, on=["check_name", "target_key"], how="inner")
    if merged.empty:
        return pd.DataFrame()

    merged["measured_value"] = pd.to_numeric(merged["measured_value"], errors="coerce")
    merged = merged[np.isfinite(merged["measured_value"])]
    if merged.empty:
        return pd.DataFrame()
    merged["robust_z"] = (merged["measured_value"] - merged["median"]) / (1.4826 * merged["mad"])
    anomalies = merged[merged["robust_z"].abs() > z_threshold].copy()
    return anomalies[["check_name", "target_key", "measured_value",
                      "median", "robust_z", "n_obs"]]


# ---------------------------------------------------------------------------
# Table de triage
# ---------------------------------------------------------------------------

def build_triage_table(qc_results: List, run_id: str = "") -> pd.DataFrame:
    """
    Construit la table de triage : tout check en warn/fail, avec assez de contexte
    pour qu'un opérateur sache où regarder (cible, valeur, seuil, raison, sévérité).
    """
    import json
    rows = []
    for r in qc_results:
        status = getattr(r, "status", "pass")
        if status in ("warn", "fail"):
            rows.append({
                "run_id": run_id,
                "check_name": r.check_name,
                "target_key": r.target_key,
                "status": status,
                "severity": r.severity,
                "measured_value": r.measured_value,
                "threshold": r.threshold,
                "reason_code": r.reason_code,
                "context_json": json.dumps(getattr(r, "context", {})),
            })
    return pd.DataFrame(rows)
