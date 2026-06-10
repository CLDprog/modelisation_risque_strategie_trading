"""Page 3 — Market Data snapshots, dynamique et symbol-aware."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dash
from dash import html, dcc, dash_table, callback, Input, Output, State, no_update
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

from src.data.source import datasource
from src.data import no_data_alert

dash.register_page(__name__, path="/market-data", name="Market Data")

layout = dbc.Container([
    html.Div([
        html.H2("Market Data — Snapshots"),
        html.P("Données brutes normalisées en snapshots cohérents. "
               "Le champ reference_type indique la source du prix (mid/last/fallback)."),
    ], className="page-header"),

    dcc.Interval(id="md-interval", interval=15000, n_intervals=0),

    dbc.Row(id="md-metrics", className="g-3 mb-4"),

    dbc.Row([
        dbc.Col([
            html.Label("Maturité :", className="text-muted small"),
            dcc.Dropdown(id="md-dd-expiry", clearable=False,
                         style={"backgroundColor": "#ffffff", "color": "#1f2328"}),
        ], width=3),
        dbc.Col([
            html.Label("Type de quote :", className="text-muted small"),
            dcc.Dropdown(
                id="md-dd-right",
                options=[
                    {"label": "Calls + Puts", "value": "both"},
                    {"label": "Calls seulement", "value": "C"},
                    {"label": "Puts seulement",  "value": "P"},
                ],
                value="both", clearable=False,
                style={"backgroundColor": "#ffffff", "color": "#1f2328"},
            ),
        ], width=3),
    ], className="mb-3"),

    dbc.Card([
        dbc.CardHeader("Prix calls & puts par strike"),
        dbc.CardBody(dcc.Graph(id="md-fig-prices", config={"displayModeBar": False})),
    ], className="card mb-4"),

    dbc.Card([
        dbc.CardHeader("Snapshot des quotes"),
        dbc.CardBody(dash_table.DataTable(
            id="md-table",
            columns=[
                {"name": "Strike",        "id": "strike"},
                {"name": "Right",         "id": "right"},
                {"name": "Bid",           "id": "bid"},
                {"name": "Ask",           "id": "ask"},
                {"name": "Mid",           "id": "mid_price"},
                {"name": "IV σ",          "id": "implied_vol"},
                {"name": "Δ Delta",       "id": "delta"},
                {"name": "Utilisable",    "id": "is_usable"},
                {"name": "Source",        "id": "data_source"},
            ],
            sort_action="native", filter_action="native", page_size=15,
            style_header={"textTransform": "none"},
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "padding": "7px"},
            style_data_conditional=[
                {"if": {"filter_query": "{is_usable} = false", "column_id": "is_usable"},
                 "color": "#cf222e"},
            ],
        )),
    ], className="card"),
], fluid=True)


@callback(
    Output("md-metrics",    "children"),
    Output("md-dd-expiry",  "options"),
    Output("md-dd-expiry",  "value"),
    Input("md-interval",    "n_intervals"),
    Input("selected-symbol","data"),
    State("md-dd-expiry",   "value"),
)
def refresh_md(_, symbol, current_expiry):
    sym   = symbol or "SPY"
    chain = datasource.get_option_chain(sym)
    spot  = datasource.get_spot(sym)
    src   = datasource.get_data_source_label()

    if chain.empty:
        alert = no_data_alert(sym)
        return [dbc.Col(alert, width=12)], [], None

    expiries = sorted(chain["expiry"].unique())
    n_usable = int(chain["is_usable"].sum()) if "is_usable" in chain.columns else len(chain)
    total    = len(chain)

    spot_str = f"${spot:.2f}" if spot is not None else "—"
    metrics = [
        dbc.Col(_mb(spot_str, f"Spot {sym}"), width=3),
        dbc.Col(_mb(str(total // 2), "Strikes", "positive"), width=3),
        dbc.Col(_mb(str(n_usable),   "Quotes utilisables", "positive"), width=3),
        dbc.Col(_mb(src, "Source",
                    "positive" if src == "Live IBKR"
                    else "info" if src == "Analytics (EOD)" else "warning"), width=3),
    ]
    opts = [{"label": e, "value": e} for e in expiries]

    # Préserve la sélection de l'utilisateur ; ne réinitialise que si invalide
    if current_expiry in expiries:
        value_out = no_update
    else:
        value_out = expiries[2] if len(expiries) > 2 else expiries[0]
    return metrics, opts, value_out


@callback(
    Output("md-fig-prices", "figure"),
    Output("md-table",      "data"),
    Input("md-dd-expiry",   "value"),
    Input("md-dd-right",    "value"),
    Input("selected-symbol","data"),
    Input("md-interval",    "n_intervals"),
)
def update_md_chart(expiry, right_filter, symbol, _):
    sym   = symbol or "SPY"
    spot  = datasource.get_spot(sym)
    chain = datasource.get_option_chain(sym)

    if not expiry or chain.empty:
        return go.Figure(), []

    df = chain[chain["expiry"] == expiry].copy()
    if right_filter != "both":
        df = df[df["right"] == right_filter]

    fig = go.Figure()
    for right, color, name in [("C", "#0969da", "Calls"), ("P", "#bc4c00", "Puts")]:
        sub = df[df["right"] == right]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub["strike"], y=sub["mid_price"], mode="lines+markers",
            name=name, line=dict(color=color), marker=dict(size=5),
        ))
    if spot is not None:
        fig.add_vline(x=spot, line_dash="dash", line_color="#57606a",
                      annotation_text=f"Spot {spot:.2f}")
    fig.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        margin=dict(l=40, r=20, t=30, b=40),
        xaxis_title="Strike", yaxis_title="Prix mid ($)",
    )

    cols = ["strike", "right", "bid", "ask", "mid_price", "implied_vol",
            "delta", "is_usable"]
    if "data_source" not in df.columns:
        df["data_source"] = "collector"
    cols.append("data_source")
    data = df[[c for c in cols if c in df.columns]].round(4).to_dict("records")
    return fig, data


def _mb(value, label, css=""):
    return html.Div([
        html.Div(str(value), className="metric-value"),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
