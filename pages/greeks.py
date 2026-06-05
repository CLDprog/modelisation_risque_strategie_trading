"""Page 8 — Greeks & Risk Analytics, symbol-aware avec callbacks dynamiques."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dash
from dash import html, dcc, dash_table, callback, Input, Output
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

from src.data.source import datasource
from src.data import no_data_alert

dash.register_page(__name__, path="/greeks", name="Greeks & Risk")

layout = dbc.Container([
    html.Div([
        html.H2("Greeks & Risk Analytics"),
        html.P("Sensibilités par contrat et agrégées au niveau du portefeuille. Eqs. 13–19."),
    ], className="page-header"),

    dcc.Interval(id="grk-interval", interval=30000, n_intervals=0),

    # Formules
    dbc.Card([
        dbc.CardHeader("Formules des Greeks"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.P("Eq. 13 — Delta :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$\Delta = e^{-rT} N(d_1)$$", mathjax=True),
                             className="formula-box"),
                ], width=3),
                dbc.Col([
                    html.P("Eq. 14 — Gamma :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$\Gamma = \frac{e^{-rT} N'(d_1)}{F\,\sigma\sqrt{T}}$$",
                                          mathjax=True), className="formula-box"),
                ], width=3),
                dbc.Col([
                    html.P("Eq. 15 — Vega (par 1% vol) :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$\nu = 0.01 \cdot e^{-rT} F \, N'(d_1) \sqrt{T}$$",
                                          mathjax=True), className="formula-box"),
                ], width=3),
                dbc.Col([
                    html.P("Eq. 16 — Theta (par jour) :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$\Theta = \frac{1}{365}\frac{\partial V}{\partial t}$$",
                                          mathjax=True), className="formula-box"),
                ], width=3),
            ]),
        ]),
    ], className="card mb-4"),

    # Métriques portefeuille (remplies par callback)
    dbc.Row(id="grk-metrics", className="g-3 mb-4"),

    # Graphes
    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Delta par position"),
            dbc.CardBody(dcc.Graph(id="grk-delta-bar", config={"displayModeBar": False})),
        ], className="card"), width=6),
        dbc.Col(dbc.Card([
            dbc.CardHeader("Vega par position"),
            dbc.CardBody(dcc.Graph(id="grk-vega-bar", config={"displayModeBar": False})),
        ], className="card"), width=6),
    ], className="mb-4"),

    # Tableau positions
    dbc.Card([
        dbc.CardHeader("Détail des positions — Greeks ligne par ligne"),
        dbc.CardBody(dash_table.DataTable(
            id="grk-table",
            columns=[
                {"name": "Contrat",   "id": "contract_key"},
                {"name": "Qty",       "id": "quantity"},
                {"name": "Strike",    "id": "strike"},
                {"name": "Right",     "id": "right"},
                {"name": "Expiry",    "id": "expiry"},
                {"name": "IV σ",      "id": "implied_vol"},
                {"name": "Prix",      "id": "price"},
                {"name": "Δ Delta",   "id": "delta"},
                {"name": "Γ Gamma",   "id": "gamma"},
                {"name": "ν Vega",    "id": "vega"},
                {"name": "Θ Theta",   "id": "theta"},
                {"name": "Δ portef.", "id": "portfolio_delta"},
                {"name": "ν portef.", "id": "portfolio_vega"},
                {"name": "Θ portef.", "id": "portfolio_theta"},
            ],
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "padding": "8px"},
            sort_action="native",
        )),
    ], className="card"),
], fluid=True)


@callback(
    Output("grk-metrics",    "children"),
    Output("grk-delta-bar",  "figure"),
    Output("grk-vega-bar",   "figure"),
    Output("grk-table",      "data"),
    Input("grk-interval",    "n_intervals"),
    Input("selected-symbol", "data"),
)
def refresh_greeks(_, symbol):
    sym       = symbol or "SPY"
    portfolio = datasource.get_portfolio(sym)
    src       = datasource.get_data_source_label()

    if portfolio.empty:
        empty_fig = go.Figure()
        alert = no_data_alert(sym)
        if not datasource.is_connected:
            return [dbc.Col(alert, width=12)], empty_fig, empty_fig, []
        # Connecté mais pas de positions
        msg = [dbc.Col(dbc.Alert(
            f"Aucune position options {sym} dans le portefeuille paper trading.",
            color="info"), width=12)]
        return msg, empty_fig, empty_fig, []

    total_delta  = portfolio["portfolio_delta"].sum()
    total_gamma  = portfolio["portfolio_gamma"].sum()
    total_vega   = portfolio["portfolio_vega"].sum()
    total_theta  = portfolio["portfolio_theta"].sum()
    total_dgamma = portfolio.get("portfolio_dollar_gamma", portfolio["portfolio_gamma"] * 0).sum()
    total_pnl    = portfolio["pnl_approx"].sum()

    metrics = [
        dbc.Col(_mb(f"{total_delta:,.0f}",  "Δ Delta total",
                    "positive" if total_delta >= 0 else "negative"), width=2),
        dbc.Col(_mb(f"{total_gamma:,.2f}", "Γ Gamma total",
                    "positive" if total_gamma >= 0 else "negative"), width=2),
        dbc.Col(_mb(f"{total_vega:,.0f}",  "ν Vega total",
                    "positive" if total_vega >= 0 else "negative"), width=2),
        dbc.Col(_mb(f"{total_theta:,.0f}", "Θ Theta total",
                    "positive" if total_theta >= 0 else "negative"), width=2),
        dbc.Col(_mb(f"${total_dgamma:,.0f}", "$ Gamma",
                    "positive" if total_dgamma >= 0 else "negative"), width=2),
        dbc.Col(_mb(f"${total_pnl:,.0f}",   "PnL valorisé",
                    "positive" if total_pnl >= 0 else "negative"), width=2),
    ]

    def bar_fig(col, color):
        fig = go.Figure(go.Bar(
            x=portfolio["contract_key"], y=portfolio[col],
            marker_color=[color if v >= 0 else "#f85149" for v in portfolio[col]],
            text=[f"{v:.2f}" for v in portfolio[col]],
            textposition="outside",
        ))
        fig.update_layout(
            template="plotly_dark", paper_bgcolor="#161b22",
            plot_bgcolor="#161b22", margin=dict(l=40, r=20, t=20, b=80),
            height=280, xaxis=dict(tickangle=-20),
        )
        return fig

    cols = ["contract_key", "quantity", "strike", "right", "expiry",
            "implied_vol", "price", "delta", "gamma", "vega", "theta",
            "portfolio_delta", "portfolio_vega", "portfolio_theta"]
    data = portfolio[[c for c in cols if c in portfolio.columns]].round(4).to_dict("records")

    return metrics, bar_fig("portfolio_delta", "#58a6ff"), bar_fig("portfolio_vega", "#3fb950"), data


def _mb(value, label, css=""):
    return html.Div([
        html.Div(str(value), className="metric-value"),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
