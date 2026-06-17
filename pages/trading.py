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
                        {"name": "Heure", "id": "created_ts"},
                        {"name": "Sous-jacent", "id": "underlying"},
                        {"name": "Instr.", "id": "instrument"},
                        {"name": "Échéance", "id": "expiry"},
                        {"name": "Strike", "id": "strike"},
                        {"name": "Sens", "id": "side"},
                        {"name": "Qté", "id": "quantity"},
                        {"name": "Statut", "id": "status"},
                        {"name": "Message", "id": "message"},
                    ],
                    style_header={"textTransform": "none"},
                    style_table={"overflowX": "auto"},
                    style_cell={"textAlign": "center", "padding": "4px", "fontSize": "11px"},
                    style_data_conditional=[
                        {"if": {"filter_query": f'{{status}} = "{s}"', "column_id": "status"},
                         "color": c, "fontWeight": "bold"}
                        for s, c in _STATUS_COLOR.items()
                    ],
                    page_size=10, sort_action="native",
                ),
            ]),
        ], className="card h-100"), width=7),
    ], className="g-2 mb-3"),

    dbc.Card([
        dbc.CardHeader("Positions ouvertes (compte paper)"),
        dbc.CardBody(dash_table.DataTable(
            id="trd-positions",
            columns=[
                {"name": "Sous-jacent", "id": "underlying_symbol"},
                {"name": "Type", "id": "sec_type"},
                {"name": "Échéance", "id": "expiry"},
                {"name": "Strike", "id": "strike"},
                {"name": "C/P", "id": "right"},
                {"name": "Qté", "id": "quantity"},
                {"name": "Prix moyen", "id": "avg_cost", "type": "numeric",
                 "format": {"specifier": ",.2f"}},
                {"name": "Prix marché", "id": "market_price", "type": "numeric",
                 "format": {"specifier": ",.2f"}},
                {"name": "P&L latent", "id": "unrealized_pnl", "type": "numeric",
                 "format": {"specifier": "+,.0f"}},
            ],
            style_header={"textTransform": "none"},
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "padding": "4px", "fontSize": "12px"},
            page_size=15, sort_action="native",
        )),
    ], className="card"),
], fluid=True)


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

@callback(Output("trd-tickets", "data"), Output("trd-positions", "data"),
          Output("trd-blotter-status", "children"),
          Input("trd-interval", "n_intervals"))
def refresh_blotter(_):
    tickets = datasource.get_order_tickets()
    for t in tickets:
        if t.get("created_ts"):
            t["created_ts"] = str(t["created_ts"])[11:19]   # HH:MM:SS
    blotter = datasource.get_blotter()
    positions = blotter.get("positions", [])
    ts = blotter.get("ts")
    status = (f"Blotter publié à {str(ts)[11:19]} UTC · {len(positions)} position(s)"
              if ts else "En attente d'un cycle du collecteur pour publier le blotter.")
    return tickets, positions, status
