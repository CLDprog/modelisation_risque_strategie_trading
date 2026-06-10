"""
Classification des quotes — librairie de checks NOMMÉS et versionnés (roadmap Step 7 :
« do not implement QC as a monolithic if-statement »).

Seuls les seuils de configs/qc.yaml (section quote_filters) font foi : une même quote est
toujours acceptée/rejetée de la même façon sous une version de seuils donnée.

Reason codes stables :
  spread_too_wide · low_open_interest · price_from_last · price_from_close · no_price · expired

Note de couverture : `max_quote_age_seconds` (config) n'est PAS applicable au chemin live —
le snapshot REST /iserver/marketdata/snapshot ne porte pas d'horodatage par quote. Documenté
ici plutôt que silencieusement ignoré.
"""
from __future__ import annotations

import math
from typing import Optional, Tuple

QUOTE_FILTERS_VERSION = "qf_v2"


def _valid(x) -> bool:
    return x is not None and isinstance(x, (int, float)) and not math.isnan(x)


def classify_quote(bid, ask, last, close, open_interest,
                   maturity_years: float, qc_cfg: dict
                   ) -> Tuple[Optional[float], bool, Optional[str]]:
    """
    Classifie une quote → (mid, is_usable, reject_reason).

    Ordre des règles (versionné qf_v2) :
      1. expired                — T <= 0
      2. bid/ask exploitables   — mid = (bid+ask)/2 ; bid>0 exigé si require_positive_bid
      3. spread_too_wide        — (ask-bid)/mid > max_spread_pct
      4. low_open_interest      — OI présent ET < min_open_interest (OI absent ≠ rejet :
                                  EUREX ne publie pas toujours l'OI en séance)
      5. fallback last / close  — usable mais tracé price_from_last / price_from_close
      6. no_price               — rien d'exploitable
    """
    f = (qc_cfg or {}).get("quote_filters", {})
    max_spread = float(f.get("max_spread_pct", 1.0))
    min_oi = int(f.get("min_open_interest", 0) or 0)
    require_pos_bid = bool(f.get("require_positive_bid", True))

    if maturity_years <= 0:
        return None, False, "expired"

    bid_ok = _valid(bid) and (bid > 0 or not require_pos_bid)
    if bid_ok and _valid(ask) and ask > bid:
        mid = (bid + ask) / 2
        spread_pct = (ask - bid) / mid if mid > 0 else float("inf")
        if spread_pct > max_spread:
            return mid, False, "spread_too_wide"
        if min_oi > 0 and _valid(open_interest) and open_interest < min_oi:
            return mid, False, "low_open_interest"
        return mid, True, None

    if _valid(last):
        return float(last), True, "price_from_last"
    if _valid(close):
        return float(close), True, "price_from_close"
    return None, False, "no_price"
