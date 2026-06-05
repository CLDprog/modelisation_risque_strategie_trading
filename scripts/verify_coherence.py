"""
Vérification rigoureuse de cohérence sur les données RÉELLES du store.
Usage : python scripts/verify_coherence.py [SYMBOL]   (défaut: AAPL)
"""
import sys, math, warnings
from pathlib import Path
warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")   # console Windows cp1252 → utf-8
except Exception:
    pass
from loguru import logger; logger.remove()

import numpy as np
import pandas as pd
from datetime import date

from src.pricing.european import bs_call, bs_put
from src.utils.config import load_config

SYM = (sys.argv[1] if len(sys.argv) > 1 else "AAPL").upper()
rate = load_config("pricing").get("risk_free_rate", {}).get("value", 0.053)
today = date.today()

def load(table):
    p = Path("data/analytics") / table / f"dt={today}" / "data.parquet"
    return pd.read_parquet(p) if p.exists() else pd.DataFrame()

iv = load("iv_points")
iv = iv[iv["underlying_symbol"] == SYM].copy()
fwd = load("forward_curve")
if "underlying" in fwd.columns:
    fwd = fwd[fwd["underlying"] == SYM]

if iv.empty:
    print(f"Aucune donnée pour {SYM}"); sys.exit()

spot = float(iv["reference_spot"].iloc[0])
print("=" * 70)
print(f"VÉRIFICATION DE COHÉRENCE — {SYM}  (spot={spot:.2f}, r={rate:.1%})")
print("=" * 70)

# Une maturité représentative (la médiane)
expiries = sorted(iv["expiry"].unique())
exp = expiries[len(expiries) // 2]
sub = iv[(iv["expiry"] == exp) & (iv["is_usable"] == True)].copy()
T = float(sub["maturity_years"].iloc[0])
disc = math.exp(-rate * T)
print(f"\nMaturité testée : {exp}  (T={T:.4f} an, {int(round(T*365))} j)\n")

calls = sub[sub["right"] == "C"].set_index("strike")
puts  = sub[sub["right"] == "P"].set_index("strike")
common = sorted(set(calls.index) & set(puts.index))

# ---- TEST 1 : parité put-call → forward par strike ----
print("TEST 1 — Parité put-call : F = K + e^(rT)(C - P)")
fwds = []
for K in common:
    c = float(calls.loc[K, "mid_price"]); p = float(puts.loc[K, "mid_price"])
    F = K + math.exp(rate * T) * (c - p)
    fwds.append(F)
    print(f"   K={K:7.1f}  C-P={c-p:+7.3f}  ->  F={F:.3f}")
print(f"   Forward moyen = {np.mean(fwds):.3f}   (écart max = {max(fwds)-min(fwds):.3f})")

# ---- TEST 2 : carry implicite ----
F_mean = float(np.mean(fwds))
q = -(math.log(F_mean / spot) - rate * T) / T
print(f"\nTEST 2 — Carry implicite : q = {q*100:.2f}%   "
      f"(F={F_mean:.2f} vs spot={spot:.2f})")
if not fwd.empty and "implied_carry" in fwd.columns:
    print(f"   carry moyen stocké (forward_curve) = {fwd['implied_carry'].mean()*100:.2f}%")

# ---- TEST 3 : round-trip IV -> prix ----
print("\nTEST 3 — Round-trip IV→prix (reprice avec l'IV stockée)")
maxerr = 0.0
for K in common:
    cr = calls.loc[K]; pr = puts.loc[K]
    ivc = float(cr["implied_vol"]); ivp = float(pr["implied_vol"])
    if not (ivc == ivc and ivp == ivp):
        continue
    c_hat = bs_call(F_mean, K, ivc, T, rate); p_hat = bs_put(F_mean, K, ivp, T, rate)
    ec = abs(c_hat - float(cr["mid_price"])); ep = abs(p_hat - float(pr["mid_price"]))
    maxerr = max(maxerr, ec, ep)
print(f"   Erreur max de repricing = {maxerr:.4f}  (≈ taille d'un tick → IV cohérente)")

# ---- TEST 4 : relation Greeks ----
print(f"\nTEST 4 — Greeks : Δcall − Δput = e^(-rT) = {disc:.4f}")
ok4 = True
for K in common:
    if "delta" not in calls.columns:
        print("   (pas de colonne delta)"); break
    dc = calls.loc[K, "delta"]; dp = puts.loc[K, "delta"]
    if dc != dc or dp != dp:
        continue
    diff = float(dc) - float(dp)
    flag = "" if abs(diff - disc) < 0.01 else "  <-- écart"
    if abs(diff - disc) >= 0.01:
        ok4 = False
    print(f"   K={K:7.1f}  {dc:+.4f} − ({dp:+.4f}) = {diff:.4f}{flag}")

# ---- TEST 5 : skew / smile ----
print("\nTEST 5 — Smile IV (call) par strike croissant")
for K in common:
    ivc = calls.loc[K, "implied_vol"]
    if ivc == ivc:
        print(f"   K={K:7.1f}  IV={float(ivc)*100:.2f}%")

print("\n" + "=" * 70)
print("VERDICT")
print("=" * 70)
print(f"  Parité put-call : écart {max(fwds)-min(fwds):.3f}$ entre strikes "
      f"({'cohérent' if max(fwds)-min(fwds) < spot*0.01 else 'À VÉRIFIER'})")
print(f"  Round-trip IV→prix : erreur max {maxerr:.4f} "
      f"({'cohérent' if maxerr < 0.05 else 'À VÉRIFIER'})")
print(f"  Identité Greaks Δc−Δp=e^(-rT) : {'cohérent' if ok4 else 'À VÉRIFIER'}")
