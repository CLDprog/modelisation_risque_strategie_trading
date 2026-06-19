"""Formatage des nombres EN FRANÇAIS pour l'affichage (dashboard).

Convention française : décimale = virgule, milliers = espace insécable.
- `fr_num(s)` : convertit une chaîne DÉJÀ formatée à l'anglaise (Python `,.2f` →
  virgule milliers, point décimal) vers le français. Sûr sur les valeurs numériques
  (ne touche pas une chaîne sans chiffre).
- `fr(x, dec, sign, suffix)` : formate directement un nombre en français.

NB : les tableaux Dash utilisent la `locale` fr par colonne, et les graphes Plotly
le réglage global `separators=", "` (cf. app.py). Ce helper couvre le 3e canal :
les chaînes Python pré-formatées (encadrés de métriques, libellés).
"""
from __future__ import annotations

_NBSP = " "   # espace insécable = séparateur de milliers français


def fr_num(value) -> str:
    """Bascule une chaîne numérique anglaise (« 48,088.5 ») en français
    (« 48 088,5 »). Inerte si la chaîne ne contient aucun chiffre."""
    s = str(value)
    if not any(c.isdigit() for c in s):
        return s
    # Python produit ',' pour les milliers et '.' pour la décimale → on inverse :
    # ',' → espace insécable, puis '.' → ','.
    return s.replace(",", _NBSP).replace(".", ",")


def fr(x: float, dec: int = 0, sign: bool = False, suffix: str = "") -> str:
    """Formate un nombre en français : milliers = espace insécable, décimale = ','.
    Ex. fr(48088.5, 1, sign=True, suffix=' €') → '+48 088,5 €'."""
    try:
        spec = f"{'+' if sign else ''},.{dec}f"
        s = format(float(x), spec).replace(",", _NBSP).replace(".", ",")
        return s + suffix
    except (TypeError, ValueError):
        return str(x)
