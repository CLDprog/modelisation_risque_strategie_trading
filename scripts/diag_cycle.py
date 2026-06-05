"""Diagnostic d'un cycle de collecte : pourquoi forward/surface sont absents ?"""
import sys, warnings
warnings.filterwarnings("ignore")
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from loguru import logger; logger.remove()

import pandas as pd
from datetime import date

from src.data.live import compute_live_analytics
from src.utils.config import load_config

p = Path("data/analytics/iv_points") / f"dt={date.today()}" / "data.parquet"
df = pd.read_parquet(p)

print("=" * 60)
print(f"Chaîne iv_points : {len(df)} lignes")
print(f"is_usable dtype  : {df['is_usable'].dtype}")
print(f"is_usable == True: {int((df['is_usable'] == True).sum())}")
print(f"is_usable truthy : {int(df['is_usable'].astype(bool).sum())}")
print(f"reject_reason    : {df['reject_reason'].value_counts(dropna=False).to_dict()}")
print(f"mid_price non-null : {int(df['mid_price'].notna().sum())}")
print(f"bid non-null       : {int(df['bid'].notna().sum())}")
print(f"ask non-null       : {int(df['ask'].notna().sum())}")
print(f"expiries           : {sorted(df['expiry'].unique())}")

print("=" * 60)
print("Reproduction de compute_live_analytics :")
res = compute_live_analytics(df, "SPY", load_config("pricing"), load_config("qc"))
print(f"  forward_df rows : {len(res['forward_df'])}")
print(f"  iv_df rows      : {len(res['iv_df'])}")
print(f"  surface_df rows : {len(res['surface_df'])}")
if not res["forward_df"].empty:
    cols = [c for c in ["expiry", "chosen_forward", "quality_flag", "candidates_used", "candidates_total"]
            if c in res["forward_df"].columns]
    print(res["forward_df"][cols].to_string(index=False))
