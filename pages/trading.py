"""Page Trading — passage d'ordres sur le compte PAPER (via le collecteur).

Le dashboard NE TOUCHE PAS IBKR : « Envoyer » dépose un ticket d'ordre (fichier),
que le collecteur exécute avec sa session (sous ~40 s). Le blotter (ordres vivants
+ positions) est publié par le collecteur et affiché ici. Confirmation explicite
avant tout envoi."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dash
from dash import html, dcc, dash_table, callback, Input, Output, State, no_update, ctx
import dash_bootstrap_components as dbc

from src.data.source import datasource
from src.trading.order_book import OrderTicket, submit_ticket, resolve_strike
from src.utils.fmt import fr_num

dash.register_page(__name__, path="/trading", name="Trading (paper)")

_STATUS_COLOR = {"pending": "#9a6700", "submitted": "#0969da", "filled": "#1a7f37",
                 "cancelled": "#57606a", "cancel_requested": "#9a6700",
                 "rejected": "#cf222e", "error": "#cf222e"}

layout = dbc.Container([
    html.Div([
        html.H2("Trading — compte paper"),
        html.P("Passe des ordres sur ton compte paper IBKR. Le dashboard ne parle pas "
               "directement au broker : « Envoyer » dépose un ticket que le collecteur "
               "exécute avec sa session (sous ~40 s). Compte PAPER uniquement."),
    ], className="page-header"),

    dcc.Interval(id="trd-interval", interval=8000, n_intervals=0),
    dcc.Store(id="trd-pending"),     # ticket en attente de confirmation

    dbc.Row([
        # ── Ticket d'ordre ───────────────────────────────────────────────
        dbc.Col(dbc.Card([
            dbc.CardHeader("Nouvel ordre"),
            dbc.CardBody([
                dbc.Row([
                    dbc.Col([
                        html.Label("Sous-jacent", className="text-muted small"),
                        dcc.Dropdown(id="trd-underlying", clearable=False,
                                     style={"fontSize": "12px"}),
                    ], width=6),
                    dbc.Col([
                        html.Label("Instrument", className="text-muted small"),
                        dcc.Dropdown(id="trd-instrument", clearable=False,
                                     options=[{"label": "Call", "value": "C"},
                                              {"label": "Put", "value": "P"},
                                              {"label": "Future indice", "value": "FUT"}],
                                     value="C", style={"fontSize": "12px"}),
                    ], width=6),
                ], className="g-2 mb-2"),
                dbc.Row([
                    dbc.Col([
                        html.Label("Échéance", className="text-muted small"),
                        dcc.Dropdown(id="trd-expiry", clearable=False,
                                     style={"fontSize": "12px"}),
                    ], width=6),
                    dbc.Col([
                        html.Label("Moneyness", className="text-muted small"),
                        dcc.Dropdown(id="trd-moneyness", clearable=False,
                                     options=[{"label": "ATM", "value": "ATM"},
                                              {"label": "OTM", "value": "OTM"},
                                              {"label": "ITM", "value": "ITM"}],
                                     value="ATM", style={"fontSize": "12px"}),
                    ], width=6),
                ], className="g-2 mb-2"),
                dbc.Row([
                    dbc.Col([
                        html.Label("Sens", className="text-muted small"),
                        dcc.Dropdown(id="trd-side", clearable=False,
                                     options=[{"label": "Achat (BUY)", "value": "BUY"},
                                              {"label": "Vente (SELL)", "value": "SELL"}],
                                     value="BUY", style={"fontSize": "12px"}),
                    ], width=4),
                    dbc.Col([
                        html.Label("Quantité", className="text-muted small"),
                        dbc.Input(id="trd-qty", type="number", value=1, min=1, step=1,
                                  className="dash-input"),
                    ], width=4),
                    dbc.Col([
                        html.Label("Type", className="text-muted small"),
                        dcc.Dropdown(id="trd-ordertype", clearable=False,
                                     options=[{"label": "Marché", "value": "MKT"},
                                              {"label": "Limite", "value": "LMT"}],
                                     value="MKT", style={"fontSize": "12px"}),
                    ], width=4),
                ], className="g-2 mb-2"),
                dbc.Row([
                    dbc.Col([
                        html.Label("Prix limite (si LMT)", className="text-muted small"),
                        dbc.Input(id="trd-limit", type="number", step=0.01,
                                  className="dash-input"),
                    ], width=6),
                    dbc.Col(html.Div(id="trd-resolved", className="small text-muted mt-4"),
                            width=6),
                ], className="g-2 mb-2"),
                dbc.Button("Préparer l'ordre", id="trd-prepare", color="primary",
                           size="sm", className="mt-2"),
                html.Div(id="trd-confirm", className="mt-3"),
            ]),
        ], className="card h-100"), width=5),

        # ── Blotter ──────────────────────────────────────────────────────
        dbc.Col(dbc.Card([
            dbc.CardHeader("Mes ordres (tickets)"),
            dbc.CardBody([
                html.Div(id="trd-blotter-status", className="small text-muted mb-2"),
                dash_table.DataTable(
                    id="trd-tickets",
                    columns=[
                        {"name": "✕", "id": "cancel"},
                        {"name": "Exécuté le", "id": "updated_ts"},
                        {"name": "Sous-jacent", "id": "underlying"},
                        {"name": "Instr.", "id": "instrument"},
                        {"name": "Échéance", "id": "expiry"},
                        {"name": "Strike", "id": "strike"},
                        {"name": "Sens", "id": "side"},
                        {"name": "Qté", "id": "quantity"},
                        {"name": "Statut", "id": "status"},
                        {"name": "Exécution", "id": "exec_state"},
                        {"name": "Message", "id": "message"},
                    ],
                    style_header={"textTransform": "none"},
                    style_table={"overflowX": "auto"},
                    style_cell={"textAlign": "center", "padding": "4px", "fontSize": "11px"},
                    style_cell_conditional=[
                        {"if": {"column_id": "cancel"}, "width": "26px", "minWidth": "26px"},
                    ],
                    style_data_conditional=[
                        {"if": {"filter_query": f'{{status}} = "{s}"', "column_id": "status"},
                         "color": c, "fontWeight": "bold"}
                        for s, c in _STATUS_COLOR.items()
                    ] + [
                        # Croix d'annulation : rouge, curseur main (cliquable).
                        {"if": {"column_id": "cancel"},
                         "color": "#cf222e", "fontWeight": "bold", "cursor": "pointer"},
                        # Couleurs du statut d'exécution réel (croisé IBKR).
                        {"if": {"filter_query": '{exec_state} = "en attente"',
                                "column_id": "exec_state"},
                         "color": "#9a6700", "fontWeight": "bold"},
                        {"if": {"filter_query": '{exec_state} = "exécuté"',
                                "column_id": "exec_state"},
                         "color": "#1a7f37", "fontWeight": "bold"},
                        {"if": {"filter_query": '{exec_state} = "rejeté"',
                                "column_id": "exec_state"},
                         "color": "#cf222e", "fontWeight": "bold"},
                    ],
                    page_size=10, sort_action="native",
                ),
                html.Small("Clique la ✕ pour annuler un ordre encore en attente. "
                           "« Exécution » = statut réel IBKR : en attente (au carnet) vs "
                           "exécuté. « Statut » = étape du ticket (envoyé au collecteur, etc.).",
                           className="text-muted d-block mt-2"),
            ]),
        ], className="card h-100"), width=7),
    ], className="g-2 mb-3"),

    # ── Clôturer / roller un straddle (juste sous le passage d'ordre) ─────
    dcc.Store(id="trd-close-pending"),
    dbc.Card([
        dbc.CardHeader("Clôturer / roller un straddle (vendre les 2 jambes détenues)"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.Label("Straddle à clôturer", className="text-muted small"),
                    dcc.Dropdown(id="trd-close-sym", clearable=False,
                                 style={"fontSize": "12px"}),
                ], width=4),
                dbc.Col(dbc.Button("Préparer la clôture", id="trd-close-prep",
                                   color="danger", size="sm", className="mt-4"), width=4),
            ], className="g-2"),
            html.Div(id="trd-close-msg", className="mt-2"),
            html.Small("Clôturer = vendre le call ET le put que tu détiens (au strike et à "
                       "l'échéance exacts). Pour ROLLER : clôture ici, puis rouvre un "
                       "straddle ATM via le ticket en haut. Compte paper, exécution par le "
                       "collecteur (~40 s).", className="text-muted d-block mt-1"),
        ]),
    ], className="card mb-3"),

    # ── Décision de fin de journée (hedge + rolls) ───────────────────────
    dbc.Card([
        dbc.CardHeader("Décision de fin de journée — hedge & rolls"),
        dbc.CardBody(html.Div(id="trd-eod")),
    ], className="card mb-3"),

    # ── Synthèse portefeuille (agrégats nets) ────────────────────────────
    dbc.Card([
        dbc.CardHeader("Synthèse du portefeuille (greeks € nets, tous sous-jacents)"),
        dbc.CardBody(dbc.Row(id="trd-pf-totals", className="g-2")),
    ], className="card mb-3"),

    # ── Positions détaillées, regroupées par sous-jacent ─────────────────
    dbc.Card([
        dbc.CardHeader("Positions ouvertes — détail greeks, regroupé par sous-jacent "
                       "(lignes Σ = net du straddle)"),
        dbc.CardBody([
            dash_table.DataTable(
                id="trd-positions",
                columns=[
                    {"name": "Sous-jacent", "id": "underlying_symbol"},
                    {"name": "Exécuté le", "id": "exec_date"},
                    {"name": "C/P", "id": "right"},
                    {"name": "Strike", "id": "strike", "type": "numeric",
                     "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ",.0f"}},
                    {"name": "Qté", "id": "quantity", "type": "numeric"},
                    {"name": "IV σ", "id": "implied_vol", "type": "numeric",
                     "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ".2%"}},
                    {"name": "Δ", "id": "delta", "type": "numeric", "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ".3f"}},
                    {"name": "Γ", "id": "gamma", "type": "numeric", "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ".4f"}},
                    {"name": "ν", "id": "vega", "type": "numeric", "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ".3f"}},
                    {"name": "Θ", "id": "theta", "type": "numeric", "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ".3f"}},
                    {"name": "ρ", "id": "rho", "type": "numeric", "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ".3f"}},
                    {"name": "Prix moyen", "id": "avg_cost", "type": "numeric",
                     "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ",.2f"}},
                    {"name": "Prix marché", "id": "market_price", "type": "numeric",
                     "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ",.2f"}},
                    {"name": "P&L €", "id": "unrealized_pnl", "type": "numeric",
                     "format": {"locale": {"group": " ", "decimal": ","}, "specifier": "+,.0f"}},
                    {"name": "Δ €", "id": "pos_eur_delta", "type": "numeric",
                     "format": {"locale": {"group": " ", "decimal": ","}, "specifier": "+,.0f"}},
                    {"name": "Γ €", "id": "pos_eur_gamma", "type": "numeric",
                     "format": {"locale": {"group": " ", "decimal": ","}, "specifier": "+,.0f"}},
                    {"name": "ν €", "id": "pos_eur_vega", "type": "numeric",
                     "format": {"locale": {"group": " ", "decimal": ","}, "specifier": "+,.0f"}},
                    {"name": "Θ €", "id": "pos_eur_theta", "type": "numeric",
                     "format": {"locale": {"group": " ", "decimal": ","}, "specifier": "+,.1f"}},
                    {"name": "ρ €", "id": "pos_eur_rho", "type": "numeric",
                     "format": {"locale": {"group": " ", "decimal": ","}, "specifier": "+,.1f"}},
                ],
                style_header={"textTransform": "none"},
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "center", "padding": "4px", "fontSize": "11px"},
                style_data_conditional=[
                    {"if": {"filter_query": '{is_subtotal} = 1'},
                     "backgroundColor": "#eaf1f8", "fontWeight": "bold"},
                ],
                page_size=40, sort_action="none",
            ),
            html.Small("Greeks récupérés depuis notre chaîne collectée (le broker ne "
                       "fournit pas les greeks). Δ/Γ/ν/Θ/ρ bruts par contrat · Δ€…ρ€ = "
                       "monétisés × quantité (contribution de la ligne au risque). "
                       "Lignes Σ = net par straddle : un straddle ATM a un Δ€ net ≈ 0. "
                       "Si ton strike n'est pas dans la grille de delta du cycle, les "
                       "greeks sont interpolés entre les deux strikes encadrants (plus "
                       "de clignotement).",
                       className="text-muted d-block mt-2"),
        ]),
    ], className="card"),
], fluid=True)


def _mb(value, label, css=""):
    from src.utils.fmt import fr_num
    value = fr_num(value)
    return dbc.Col(html.Div([
        html.Div(str(value), className="metric-value", style={"fontSize": "1.0rem"}),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}"), width=2)


def _ibkr_to_internal():
    """Map ticker IBKR → symbole interne (le broker renvoie p.ex. 'SAN1' pour Sanofi,
    'NDA FI' pour Nordea, alors que notre chaîne est indexée par le symbole interne)."""
    m = {}
    for u in datasource._get_universe_cfg().get("underlyings", []):
        internal = u["symbol"]
        m[internal] = internal
        if u.get("ibkr_symbol"):
            m[u["ibkr_symbol"]] = internal
    return m


_GREEK_KEYS = ("implied_vol", "delta", "gamma", "vega", "theta", "rho",
               "eur_delta", "eur_gamma", "eur_vega", "eur_theta", "eur_rho")


def _interp_greeks(cand, strike):
    """Greeks d'une position au strike exact, SINON interpolés linéairement entre les
    deux strikes encadrants de la grille collectée. La grille (échelle de delta, ~5
    strikes) est clairsemée et RECALCULÉE à chaque cycle autour du forward → le strike
    détenu n'y est pas toujours listé. Sans interpolation, la ligne clignote (greeks
    présents un cycle, vides le suivant). Hors bornes → strike le plus proche (clamp).
    Retourne (dict_greeks, days_to_expiry)."""
    if cand is None or cand.empty or "strike" not in cand.columns:
        return {}, None
    c = cand.dropna(subset=["strike"]).sort_values("strike")
    if c.empty:
        return {}, None
    dte = None
    if "days_to_expiry" in c.columns:
        dd = c["days_to_expiry"].dropna()
        dte = float(dd.iloc[0]) if not dd.empty else None
    k = float(strike)
    exact = c[(c["strike"].astype(float) - k).abs() < 1e-6]
    if not exact.empty:                       # strike pile dans la grille → valeurs brutes
        r = exact.iloc[0]
        return ({gk: (float(r[gk]) if gk in c.columns and r[gk] == r[gk] else None)
                 for gk in _GREEK_KEYS}, dte)
    g = {}
    for gk in _GREEK_KEYS:                     # interpolation par greek sur points non-NaN
        if gk not in c.columns:
            g[gk] = None
            continue
        sub = c[["strike", gk]].dropna()
        ks = [float(x) for x in sub["strike"]]
        ys = [float(y) for y in sub[gk]]
        if not ks:
            g[gk] = None
        elif k <= ks[0]:
            g[gk] = ys[0]
        elif k >= ks[-1]:
            g[gk] = ys[-1]
        else:
            g[gk] = next(
                (ys[i - 1] + (ys[i] - ys[i - 1]) * (k - ks[i - 1]) / (ks[i] - ks[i - 1])
                 for i in range(1, len(ks)) if ks[i] >= k and ks[i] != ks[i - 1]),
                ys[-1])
    return g, dte


def _enrich_positions(positions, index_level=None):
    """Associe chaque position à notre chaîne collectée pour récupérer les greeks
    (le broker ne les fournit pas), et monétise × quantité. Le future indice est
    valorisé en delta (qté × mult × niveau)."""
    imap = _ibkr_to_internal()
    chains, out = {}, []
    for p in positions:
        # Retraduit le ticker IBKR en symbole interne (SAN1→SAN) pour l'affichage
        # ET la recherche de chaîne, sinon greeks introuvables et nom non reconnu.
        sym = imap.get(p.get("underlying_symbol") or "", p.get("underlying_symbol") or "")
        qty = float(p.get("quantity") or 0)
        sectype = (p.get("sec_type") or "").upper()
        # FUTURE indice : instrument LINÉAIRE. Delta = ±1 (qté × mult × niveau en €) ;
        # gamma/vega/theta/rho = 0 EXACTEMENT (aucune convexité, aucune dépendance vol
        # ni temps — le carry/rho est négligeable et conventionnellement ignoré). On
        # affiche 0 explicite (pas vide) : ces greeks sont CONNUS = zéro, pas inconnus.
        # C'est tout l'intérêt du future comme hedge : il enlève le delta SANS toucher
        # au gamma/vega/theta du book de straddles. Aucune donnée IBKR supplémentaire.
        if "FUT" in sectype or (not p.get("right") and sym in ("ESTX50",)):
            idx = index_level or float(p.get("market_price") or 0)
            # Le future indice (FESX) a un multiplicateur de 10, mais IBKR renvoie
            # parfois le défaut 100 (actions) → delta 10× trop grand. On déduit le
            # vrai multiplicateur depuis avg_cost / prix de marché (≈10), sinon 10.
            mult = float(p.get("multiplier") or 0)
            ac, mp = p.get("avg_cost"), p.get("market_price")
            if mult <= 0 or mult >= 50:
                mult = round(float(ac) / float(mp)) if ac and mp and float(mp) else 10
            out.append({"underlying_symbol": sym, "right": "FUT", "strike": None,
                        "quantity": qty, "avg_cost": p.get("avg_cost"),
                        "market_price": p.get("market_price"),
                        "unrealized_pnl": p.get("unrealized_pnl"), "is_subtotal": 0,
                        "implied_vol": None, "delta": (1.0 if qty >= 0 else -1.0),
                        "gamma": 0.0, "vega": 0.0, "theta": 0.0, "rho": 0.0,
                        "expiry": p.get("expiry"), "days_to_expiry": None,
                        "pos_eur_delta": qty * mult * idx, "pos_eur_gamma": 0.0,
                        "pos_eur_vega": 0.0, "pos_eur_theta": 0.0, "pos_eur_rho": 0.0})
            continue
        if sym not in chains:
            chains[sym] = datasource.get_option_chain(sym)
        ch = chains[sym]
        g, dte = {}, None
        if ch is not None and not ch.empty and p.get("strike") is not None:
            cand = ch[(ch["expiry"].astype(str) == str(p.get("expiry"))) &
                      (ch["right"] == p.get("right"))]
            g, dte = _interp_greeks(cand, float(p["strike"]))
        row = {"underlying_symbol": sym, "right": p.get("right"), "strike": p.get("strike"),
               "quantity": qty, "avg_cost": p.get("avg_cost"),
               "market_price": p.get("market_price"),
               "unrealized_pnl": p.get("unrealized_pnl"), "is_subtotal": 0,
               "expiry": p.get("expiry"), "days_to_expiry": dte,
               "implied_vol": g.get("implied_vol"), "delta": g.get("delta"),
               "gamma": g.get("gamma"), "vega": g.get("vega"), "theta": g.get("theta"),
               "rho": g.get("rho")}
        for gk in ("delta", "gamma", "vega", "theta", "rho"):
            ev = g.get(f"eur_{gk}")
            row[f"pos_eur_{gk}"] = ev * qty if ev is not None else None
        out.append(row)
    return out


def _eod_decision(enriched, index_level, band=(0.40, 0.60), min_dte=270, idx_mult=10):
    """Applique les règles du prof aux positions → décisions concrètes du soir :
    (1) combien de futures vendre/racheter pour ramener le Δ€ net à ~0 ;
    (2) quels straddles roller (jambe |Δ| hors [0.40;0.60], ou maturité < 9 mois)."""
    from collections import defaultdict
    opts = [r for r in enriched if r.get("right") in ("C", "P")]
    fut_qty = sum(r["quantity"] for r in enriched if r.get("right") == "FUT")
    opt_delta = sum(r["pos_eur_delta"] for r in opts if r.get("pos_eur_delta") is not None)
    one_fut = idx_mult * index_level if index_level else None
    required = round(-opt_delta / one_fut) if one_fut else 0      # nb de futures à DÉTENIR
    adj = required - fut_qty                                       # ajustement vs position actuelle
    net_after = opt_delta + required * one_fut if one_fut else None

    legs_by = defaultdict(list)
    for r in opts:
        legs_by[r["underlying_symbol"]].append(r)
    straddles, n_roll = [], 0
    for sym, legs in sorted(legs_by.items()):
        call = next((l for l in legs if l["right"] == "C"), {})
        put = next((l for l in legs if l["right"] == "P"), {})
        dtes = [l["days_to_expiry"] for l in legs if l.get("days_to_expiry") is not None]
        dte = min(dtes) if dtes else None
        band_out = any(l.get("delta") is not None
                       and not (band[0] <= abs(l["delta"]) <= band[1]) for l in legs)
        mat_out = dte is not None and dte < min_dte
        if band_out or mat_out:
            n_roll += 1
        straddles.append({
            "underlying": sym,
            "strike": call.get("strike") if call.get("strike") is not None else put.get("strike"),
            "expiry": (call.get("expiry") or put.get("expiry")),
            "months_left": round(dte / 30.44, 1) if dte is not None else None,
            "delta_call": call.get("delta"), "delta_put": put.get("delta"),
            "roll_band": "ROLL" if band_out else "ok",
            "roll_mat": "ROLL" if mat_out else "ok",
            "decision": "ROLL" if (band_out or mat_out) else "Conserver",
        })
    return {"opt_delta": opt_delta, "fut_qty": fut_qty, "required": required, "adj": adj,
            "net_after": net_after, "index_level": index_level, "one_fut": one_fut,
            "band": band, "min_dte": min_dte, "straddles": straddles, "n_roll": n_roll}


# ── Peuplement des dropdowns ────────────────────────────────────────────────

@callback(Output("trd-underlying", "options"), Output("trd-underlying", "value"),
          Input("trd-interval", "n_intervals"), State("trd-underlying", "value"))
def fill_underlyings(_, current):
    syms = datasource.available_symbols
    opts = [{"label": s, "value": s} for s in syms]
    return opts, current or (syms[0] if syms else None)


@callback(Output("trd-expiry", "options"), Output("trd-expiry", "value"),
          Input("trd-underlying", "value"), Input("trd-instrument", "value"),
          State("trd-expiry", "value"))
def fill_expiries(sym, instrument, current):
    sym = sym or "ESTX50"
    chain = datasource.get_option_chain(sym)
    if chain.empty or "expiry" not in chain.columns:
        return [], None
    exps = sorted(chain["expiry"].dropna().unique())
    opts = [{"label": e, "value": e} for e in exps]
    return opts, current if current in exps else (exps[0] if exps else None)


@callback(Output("trd-resolved", "children"),
          Input("trd-underlying", "value"), Input("trd-instrument", "value"),
          Input("trd-expiry", "value"), Input("trd-moneyness", "value"))
def show_resolved_strike(sym, instrument, expiry, moneyness):
    if instrument == "FUT":
        return "Future indice (front month) — pas de strike."
    if not (sym and expiry):
        return ""
    chain = datasource.get_option_chain(sym)
    k = resolve_strike(chain, expiry, instrument, moneyness)
    if k is None:
        return "Strike introuvable pour cette échéance."
    return f"Strike retenu : {k:g}"


# ── Préparation + confirmation + envoi ──────────────────────────────────────

@callback(Output("trd-confirm", "children"), Output("trd-pending", "data"),
          Input("trd-prepare", "n_clicks"),
          State("trd-underlying", "value"), State("trd-instrument", "value"),
          State("trd-expiry", "value"), State("trd-moneyness", "value"),
          State("trd-side", "value"), State("trd-qty", "value"),
          State("trd-ordertype", "value"), State("trd-limit", "value"),
          prevent_initial_call=True)
def prepare(_n, sym, instrument, expiry, moneyness, side, qty, otype, limit):
    if not (sym and side and qty):
        return dbc.Alert("Champs incomplets.", color="warning", className="py-1"), None
    strike = None
    if instrument != "FUT":
        chain = datasource.get_option_chain(sym)
        strike = resolve_strike(chain, expiry, instrument, moneyness)
        if strike is None:
            return dbc.Alert("Strike introuvable.", color="danger", className="py-1"), None
    label = (f"{side} {qty} × {sym} "
             + ("FUTURE indice" if instrument == "FUT"
                else f"{instrument} {strike:g} {expiry} ({moneyness})")
             + f" — {otype}" + (f" @ {limit}" if otype == "LMT" and limit else ""))
    pending = {"underlying": sym, "instrument": instrument, "expiry": expiry,
               "strike": strike, "moneyness": moneyness, "side": side,
               "quantity": qty, "order_type": otype, "limit_price": limit}
    confirm = dbc.Alert([
        html.Strong("Confirmer l'ordre : "), html.Span(label),
        html.Div([
            dbc.Button("✓ Envoyer", id="trd-send", color="success", size="sm",
                       className="me-2 mt-2"),
            dbc.Button("Annuler", id="trd-abort", color="secondary", size="sm",
                       className="mt-2"),
        ]),
    ], color="info", className="py-2")
    return confirm, pending


@callback(Output("trd-confirm", "children", allow_duplicate=True),
          Input("trd-send", "n_clicks"), Input("trd-abort", "n_clicks"),
          State("trd-pending", "data"), prevent_initial_call=True)
def send_or_abort(_s, _a, pending):
    if ctx.triggered_id == "trd-abort" or not pending:
        return html.Div()
    t = OrderTicket(
        underlying=pending["underlying"], instrument=pending["instrument"],
        side=pending["side"], quantity=float(pending["quantity"]),
        order_type=pending.get("order_type", "MKT"),
        limit_price=pending.get("limit_price"),
        expiry=pending.get("expiry"), strike=pending.get("strike"),
        moneyness=pending.get("moneyness"))
    tid = submit_ticket(t)
    return dbc.Alert(f"Ticket {tid} déposé — le collecteur l'exécutera sous ~40 s. "
                     "Suis son statut dans le blotter →", color="success",
                     className="py-2")


# ── Blotter (tickets + positions) ───────────────────────────────────────────

_EUR_KEYS = ("pos_eur_delta", "pos_eur_gamma", "pos_eur_vega", "pos_eur_theta", "pos_eur_rho")


def _render_eod(dec):
    """Décision du soir en deux tableaux : hedge delta + roll par straddle."""
    if not dec.get("index_level"):
        return html.Div("Niveau d'indice indisponible — décision impossible pour l'instant.",
                        className="text-muted")
    b = dec["band"]
    sens = ("VENDRE" if dec["adj"] < 0 else "RACHETER" if dec["adj"] > 0 else "Aucune action")
    order_txt = (f"{sens} {abs(int(dec['adj']))} FESX" if dec["adj"] != 0
                 else "Aucune action (delta déjà neutre)")

    # Tableau 1 — hedge delta (une ligne)
    hedge_tbl = dash_table.DataTable(
        columns=[{"name": c, "id": c} for c in
                 ["Δ€ net (options)", "Futures détenus", "Futures cible",
                  "Ajustement", "Δ€ net après hedge", "Ordre"]],
        data=[{"Δ€ net (options)": fr_num(f"{dec['opt_delta']:+,.0f}"),
               "Futures détenus": int(dec["fut_qty"]),
               "Futures cible": int(dec["required"]),
               "Ajustement": int(dec["adj"]),
               "Δ€ net après hedge": fr_num(f"{dec['net_after']:+,.0f}"),
               "Ordre": order_txt}],
        style_header={"textTransform": "none", "fontWeight": "bold"},
        style_cell={"textAlign": "center", "padding": "6px", "fontSize": "12px"},
        style_data_conditional=[
            {"if": {"filter_query": '{Ordre} != "Aucune action (delta déjà neutre)"',
                    "column_id": "Ordre"}, "color": "#cf222e", "fontWeight": "bold"}],
    )

    # Tableau 2 — décision de roll par straddle
    rows = []
    for s in dec["straddles"]:
        rows.append({
            "Sous-jacent": s["underlying"], "Strike": s["strike"], "Échéance": s["expiry"],
            "Mois restants": s["months_left"],
            "Δ call": round(s["delta_call"], 3) if s["delta_call"] is not None else None,
            "Δ put": round(s["delta_put"], 3) if s["delta_put"] is not None else None,
            "|Δ| ∈ [0.40;0.60] ?": s["roll_band"],
            "Maturité ≥ 9 mois ?": s["roll_mat"],
            "Décision": s["decision"],
        })
    roll_tbl = dash_table.DataTable(
        columns=[
            {"name": "Sous-jacent", "id": "Sous-jacent"},
            {"name": "Strike", "id": "Strike", "type": "numeric", "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ",.0f"}},
            {"name": "Échéance", "id": "Échéance"},
            {"name": "Mois restants", "id": "Mois restants", "type": "numeric",
             "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ".1f"}},
            {"name": "Δ call", "id": "Δ call", "type": "numeric", "format": {"locale": {"group": " ", "decimal": ","}, "specifier": "+.3f"}},
            {"name": "Δ put", "id": "Δ put", "type": "numeric", "format": {"locale": {"group": " ", "decimal": ","}, "specifier": "+.3f"}},
            {"name": "Bande Δ", "id": "|Δ| ∈ [0.40;0.60] ?"},
            {"name": "Maturité", "id": "Maturité ≥ 9 mois ?"},
            {"name": "Décision", "id": "Décision"},
        ],
        data=rows,
        style_header={"textTransform": "none", "fontWeight": "bold"},
        style_table={"overflowX": "auto"},
        style_cell={"textAlign": "center", "padding": "5px", "fontSize": "12px"},
        style_data_conditional=[
            # Δ hors bande / maturité courte / décision ROLL → rouge gras
            {"if": {"filter_query": '{|Δ| ∈ [0.40;0.60] ?} = "ROLL"',
                    "column_id": "|Δ| ∈ [0.40;0.60] ?"}, "color": "#cf222e", "fontWeight": "bold"},
            {"if": {"filter_query": '{Maturité ≥ 9 mois ?} = "ROLL"',
                    "column_id": "Maturité ≥ 9 mois ?"}, "color": "#cf222e", "fontWeight": "bold"},
            {"if": {"filter_query": '{Décision} = "ROLL"', "column_id": "Décision"},
             "color": "#cf222e", "fontWeight": "bold", "backgroundColor": "#fbeaec"},
            {"if": {"filter_query": '{Décision} = "Conserver"', "column_id": "Décision"},
             "color": "#1a7f37"},
        ],
        sort_action="native", page_size=12,
    )

    return html.Div([
        html.Div("Hedge delta du book", className="text-muted small mb-1"),
        hedge_tbl,
        html.Div("Décision de roll par straddle", className="text-muted small mb-1 mt-3"),
        roll_tbl,
        html.Small(f"Règles : roll si une jambe a |Δ| hors [{b[0]:.2f} ; {b[1]:.2f}], "
                   f"ou si maturité < {dec['min_dte']/30.44:.0f} mois. "
                   f"{dec['n_roll']} straddle(s) à roller.",
                   className="text-muted d-block mt-2"),
    ])


@callback(Output("trd-tickets", "data"), Output("trd-positions", "data"),
          Output("trd-blotter-status", "children"), Output("trd-pf-totals", "children"),
          Output("trd-eod", "children"),
          Input("trd-interval", "n_intervals"))
def refresh_blotter(_):
    tickets = datasource.get_order_tickets()

    def _dt(v):
        s = str(v or "")
        return f"{s[8:10]}/{s[5:7]} {s[:4]} {s[11:16]}" if len(s) >= 16 else (s or "—")

    def _ck(sym, instr, strike):           # clé contrat pour matcher position↔ticket
        return (sym, instr, None if instr == "FUT"
                else (round(float(strike), 2) if strike is not None else None))

    # Date d'EXÉCUTION par contrat, depuis les tickets soumis/remplis (AVANT formatage).
    exec_lookup = {}
    for t in tickets:
        if t.get("status") in ("submitted", "filled"):
            exec_lookup[_ck(t.get("underlying"), t.get("instrument"), t.get("strike"))] = \
                t.get("updated_ts") or t.get("created_ts")
    # Affichage des tickets : on ne garde que la date d'exécution (jj/mm AAAA hh:mm).
    for t in tickets:
        t["updated_ts"] = _dt(t.get("updated_ts"))

    blotter = datasource.get_blotter()
    raw_positions = blotter.get("positions", [])

    # Statut d'EXÉCUTION réel + croix d'annulation. Le ticket porte un statut de
    # WORKFLOW (pending/submitted/...), qui ne dit PAS si l'ordre est passé. On croise
    # avec les ordres vivants IBKR (blotter) qui, eux, portent le vrai statut
    # (Submitted = au carnet / Filled = exécuté). Un ordre soumis encore "au carnet"
    # est annulable (✕) ; une fois exécuté, plus de croix.
    def _oid(o):
        return str(o.get("orderId") or o.get("order_id") or o.get("orderid") or "")
    ib_status = {_oid(o): str(o.get("status") or o.get("order_status") or "").lower()
                 for o in (blotter.get("orders") or []) if isinstance(o, dict) and _oid(o)}
    for t in tickets:
        t["id"] = t.get("ticket_id")                 # row_id pour le clic sur ✕
        st = t.get("status")
        boid = str(t.get("broker_order_id") or "")
        if st == "filled":
            t["exec_state"] = "exécuté"
        elif st == "pending":
            t["exec_state"] = "non envoyé"
        elif st in ("cancelled", "cancel_requested"):
            t["exec_state"] = "annulé"
        elif st in ("rejected", "error"):
            t["exec_state"] = "rejeté"
        elif st == "submitted":
            ibs = ib_status.get(boid, "")
            if "fill" in ibs and "partial" not in ibs:
                t["exec_state"] = "exécuté"
            elif "partial" in ibs:
                t["exec_state"] = "partiel"
            elif any(k in ibs for k in ("submit", "pending", "active", "presubmit")):
                t["exec_state"] = "en attente"
            elif "cancel" in ibs:
                t["exec_state"] = "annulé"
            else:
                # Plus dans le carnet vivant : on ne peut pas confirmer → "envoyé".
                t["exec_state"] = "envoyé"
        else:
            t["exec_state"] = st or "—"
        # Annulable uniquement tant que l'ordre n'est pas confirmé exécuté/terminé.
        t["cancel"] = "✕" if t["exec_state"] in (
            "non envoyé", "en attente", "partiel", "envoyé") else ""
    ts = blotter.get("ts")
    status = (f"Blotter publié à {str(ts)[11:19]} UTC · {len(raw_positions)} position(s)"
              if ts else "En attente d'un cycle du collecteur pour publier le blotter.")

    index_level = datasource.get_spot("ESTX50")
    enriched = _enrich_positions(raw_positions, index_level)
    # Date d'exécution sur chaque ligne de position (matchée au ticket correspondant).
    for r in enriched:
        instr = "FUT" if r.get("right") == "FUT" else r.get("right")
        r["exec_date"] = _dt(exec_lookup.get(_ck(r.get("underlying_symbol"), instr,
                                                 r.get("strike"))))
    eod = _render_eod(_eod_decision(enriched, index_level)) if enriched else html.Div(
        "Aucune position — passe tes straddles d'abord.", className="text-muted")

    # Regroupe par sous-jacent, avec une ligne Σ (net du straddle) après chaque groupe.
    def _s(rows, key):
        return sum(r[key] for r in rows if r.get(key) is not None)
    grouped, pf = [], {k: 0.0 for k in _EUR_KEYS}
    pf_pnl = 0.0
    for sym in sorted({r["underlying_symbol"] for r in enriched}):
        legs = [r for r in enriched if r["underlying_symbol"] == sym]
        grouped.extend(legs)
        sub = {"underlying_symbol": f"Σ {sym}", "right": "", "is_subtotal": 1,
               "unrealized_pnl": _s(legs, "unrealized_pnl")}
        for k in _EUR_KEYS:
            sub[k] = _s(legs, k)
        grouped.append(sub)
        pf_pnl += sub["unrealized_pnl"]
        for k in _EUR_KEYS:
            pf[k] += sub[k]

    # « Jambes » et « prime payée » = OPTIONS uniquement. Un future ne coûte pas de
    # prime (juste de la marge) — l'inclure gonflerait faussement la prime de son
    # notionnel (bug du même type que le multiplicateur).
    opt_legs = [r for r in enriched if r.get("right") in ("C", "P")]
    n_legs = len(opt_legs)
    gross = sum(abs(float(r.get("avg_cost") or 0)) for r in opt_legs)
    totals = [
        _mb(f"{pf_pnl:+,.0f} €", "P&L latent total",
            "positive" if pf_pnl >= 0 else "negative"),
        _mb(f"{pf['pos_eur_delta']:+,.0f} €", "Δ € net (à hedger)",
            "warning" if abs(pf["pos_eur_delta"]) > 5000 else "positive"),
        _mb(f"{pf['pos_eur_gamma']:+,.0f} €", "Γ € net"),
        _mb(f"{pf['pos_eur_vega']:+,.0f} €", "ν € net (/pt vol)", "info"),
        _mb(f"{pf['pos_eur_theta']:+,.1f} €", "Θ € net (/jour)",
            "negative" if pf["pos_eur_theta"] < 0 else "positive"),
        _mb(f"{n_legs} · {gross:,.0f} €", "Jambes · prime payée"),
    ]
    return tickets, grouped, status, totals, eod


@callback(Output("trd-blotter-status", "children", allow_duplicate=True),
          Input("trd-tickets", "active_cell"), prevent_initial_call=True)
def cancel_ticket(active_cell):
    """Clic sur la ✕ d'une ligne → annule l'ordre. Un ticket 'pending' n'a pas encore
    été envoyé → on le marque annulé directement. Un 'submitted' est déjà au broker →
    on demande l'annulation, le collecteur l'exécute auprès d'IBKR (~40 s)."""
    if not active_cell or active_cell.get("column_id") != "cancel":
        return no_update
    tid = active_cell.get("row_id")
    if not tid:
        return no_update
    from src.trading.order_book import all_tickets, request_cancel, update_ticket
    t = next((x for x in all_tickets() if x.get("ticket_id") == tid), None)
    if not t:
        return no_update
    st = t.get("status")
    if st == "pending":
        update_ticket(tid, "cancelled", message="annulé avant envoi")
        return dbc.Alert(f"Ticket {tid} annulé (il n'avait pas encore été envoyé).",
                         color="secondary", className="py-1")
    if st == "submitted":
        request_cancel(tid)
        return dbc.Alert(f"Annulation demandée pour {tid} — le collecteur l'annule "
                         "auprès d'IBKR au prochain passage (~40 s).",
                         color="warning", className="py-1")
    return dbc.Alert(f"Ticket {tid} déjà « {st} » — rien à annuler.",
                     color="secondary", className="py-1")


# ── Clôture / roll d'un straddle ─────────────────────────────────────────────

def _held_option_legs(internal_sym):
    """Jambes d'options réellement détenues (≠0) pour un sous-jacent interne."""
    imap = _ibkr_to_internal()
    out = []
    for p in datasource.get_blotter().get("positions", []):
        sym = imap.get(p.get("underlying_symbol") or "", p.get("underlying_symbol") or "")
        if sym == internal_sym and p.get("right") in ("C", "P") \
                and float(p.get("quantity") or 0) != 0:
            out.append(p)
    return out


@callback(Output("trd-close-sym", "options"), Output("trd-close-sym", "value"),
          Input("trd-interval", "n_intervals"), State("trd-close-sym", "value"))
def fill_close_syms(_, current):
    imap = _ibkr_to_internal()
    syms = sorted({imap.get(p.get("underlying_symbol") or "", p.get("underlying_symbol") or "")
                   for p in datasource.get_blotter().get("positions", [])
                   if p.get("right") in ("C", "P") and float(p.get("quantity") or 0) != 0})
    opts = [{"label": s, "value": s} for s in syms]
    return opts, current if current in syms else (syms[0] if syms else None)


@callback(Output("trd-close-msg", "children"), Output("trd-close-pending", "data"),
          Input("trd-close-prep", "n_clicks"), State("trd-close-sym", "value"),
          prevent_initial_call=True)
def prepare_close(_n, sym):
    if not sym:
        return dbc.Alert("Choisis un straddle.", color="warning", className="py-1"), None
    legs = _held_option_legs(sym)
    if not legs:
        return dbc.Alert(f"Aucune jambe détenue pour {sym}.", color="warning",
                         className="py-1"), None
    desc = " · ".join(f"VENDRE {abs(float(p['quantity'])):g} {p['right']} {p['strike']:g}"
                      for p in legs)
    pending = [{"underlying": sym, "right": p["right"], "strike": float(p["strike"]),
                "expiry": str(p.get("expiry"))[:10],
                "quantity": abs(float(p["quantity"])),
                "side": "SELL" if float(p["quantity"]) > 0 else "BUY"} for p in legs]
    confirm = dbc.Alert([
        html.Strong(f"Confirmer la clôture de {sym} : "), html.Span(desc),
        html.Div([
            dbc.Button("✓ Confirmer la vente", id="trd-close-send", color="success",
                       size="sm", className="me-2 mt-2"),
            dbc.Button("Annuler", id="trd-close-abort", color="secondary", size="sm",
                       className="mt-2"),
        ]),
    ], color="info", className="py-2")
    return confirm, pending


@callback(Output("trd-close-msg", "children", allow_duplicate=True),
          Input("trd-close-send", "n_clicks"), Input("trd-close-abort", "n_clicks"),
          State("trd-close-pending", "data"), prevent_initial_call=True)
def send_close(_s, _a, pending):
    if ctx.triggered_id == "trd-close-abort" or not pending:
        return html.Div()
    ids = []
    for leg in pending:
        t = OrderTicket(underlying=leg["underlying"], instrument=leg["right"],
                        side=leg["side"], quantity=leg["quantity"],
                        expiry=leg["expiry"], strike=leg["strike"], moneyness="close")
        ids.append(submit_ticket(t))
    return dbc.Alert(
        f"Clôture déposée : {len(ids)} ordre(s) ({', '.join(ids)}). Le collecteur les "
        "exécute sous ~40 s. Pour roller, rouvre ensuite un straddle ATM via le ticket.",
        color="success", className="py-2")
