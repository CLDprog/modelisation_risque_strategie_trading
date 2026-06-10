"""Page 2 — Instrument Master, symbol-aware avec callback dynamique."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dash
from dash import html, dcc, dash_table, callback, Input, Output
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

from src.data.source import datasource
from src.data import no_data_alert

dash.register_page(__name__, path="/universe", name="Instrument Master")

layout = dbc.Container([
    html.Div([
        html.H2("Instrument Master"),
        html.P("Source de vérité canonique pour tous les instruments. "
               "Chaque contrat a une clé unique immuable : symbol|OPT|expiry|strike|right|exch|ccy"),
    ], className="page-header"),

    dcc.Interval(id="uni-interval", interval=60000, n_intervals=0),

    dbc.Row(id="uni-metrics", className="g-3 mb-4"),

    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Distribution des strikes par maturité"),
            dbc.CardBody(dcc.Graph(id="uni-strikes-fig", config={"displayModeBar": False})),
        ], className="card"), width=8),
        dbc.Col(dbc.Card([
            dbc.CardHeader("IV ATM par maturité"),
            dbc.CardBody(dcc.Graph(id="uni-atm-fig", config={"displayModeBar": False})),
        ], className="card"), width=4),
    ], className="mb-4"),

    dbc.Card([
        dbc.CardHeader("Chaîne d'options par maturité"),
        dbc.CardBody(dash_table.DataTable(
            id="uni-table",
            columns=[
                {"name": "Symbole",     "id": "underlying_symbol"},
                {"name": "Expiry",      "id": "expiry"},
                {"name": "Jours",       "id": "days_to_expiry"},
                {"name": "Nb strikes",  "id": "n_strikes"},
                {"name": "Strike min",  "id": "min_strike"},
                {"name": "Strike max",  "id": "max_strike"},
                {"name": "IV ATM (%)",  "id": "avg_iv"},
            ],
            sort_action="native", page_size=12,
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "padding": "8px"},
        )),
    ], className="card"),
], fluid=True)


@callback(
    Output("uni-metrics",     "children"),
    Output("uni-strikes-fig", "figure"),
    Output("uni-atm-fig",     "figure"),
    Output("uni-table",       "data"),
    Input("uni-interval",     "n_intervals"),
    Input("selected-symbol",  "data"),
)
def refresh_universe(_, symbol):
    sym   = symbol or "SPY"
    chain = datasource.get_option_chain(sym)
    src   = datasource.get_data_source_label()

    if chain.empty:
        alert = no_data_alert(sym)
        return [dbc.Col(alert, width=12)], go.Figure(), go.Figure(), []

    n_expiries = len(chain["expiry"].unique())
    n_contracts = len(chain)
    n_usable = int(chain["is_usable"].sum()) if "is_usable" in chain.columns else n_contracts
    mult = int(chain["multiplier"].iloc[0]) if "multiplier" in chain.columns else 100

    metrics = [
        dbc.Col(_mb("1",                   "Underlyings"),          width=3),
        dbc.Col(_mb(str(n_expiries),       "Maturités"),            width=3),
        dbc.Col(_mb(str(n_contracts // 2), "Strikes", "positive"),  width=3),
        dbc.Col(_mb(str(mult),             "Multiplier"),           width=3),
    ]

    # Strikes par maturité
    summary = (
        chain.groupby(["underlying_symbol", "expiry", "days_to_expiry"])
        .agg(n_strikes=("strike", "nunique"),
             min_strike=("strike", "min"),
             max_strike=("strike", "max"),
             avg_iv=("implied_vol", "mean"))
        .reset_index()
    )

    fig_strikes = go.Figure(go.Bar(
        x=summary["days_to_expiry"], y=summary["n_strikes"],
        marker_color="#0969da",
        text=summary["n_strikes"], textposition="outside",
    ))
    fig_strikes.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        margin=dict(l=40, r=20, t=20, b=40),
        xaxis_title="Jours à l'expiration", yaxis_title="Nombre de strikes",
    )

    # IV ATM par maturité
    atm = chain[
        (chain["right"] == "C") & (chain["log_moneyness"].abs() < 0.03)
    ].groupby("days_to_expiry")["implied_vol"].mean().reset_index()

    fig_atm = go.Figure(go.Scatter(
        x=atm["days_to_expiry"], y=atm["implied_vol"] * 100,
        mode="lines+markers+text",
        text=[f"{v:.1f}%" for v in atm["implied_vol"] * 100],
        textposition="top center",
        line=dict(color="#1a7f37", width=2), marker=dict(size=8),
    ))
    fig_atm.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        margin=dict(l=40, r=20, t=20, b=40),
        xaxis_title="Jours", yaxis_title="IV ATM (%)",
    )

    # Afficher avg_iv en %
    summary_disp = summary.copy()
    summary_disp["avg_iv"] = (summary_disp["avg_iv"] * 100).round(2)
    return (
        metrics,
        fig_strikes,
        fig_atm,
        summary_disp.round(2).to_dict("records"),
    )


def _mb(value, label, css=""):
    return html.Div([
        html.Div(str(value), className="metric-value"),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
