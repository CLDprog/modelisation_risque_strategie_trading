import dash
from dash import html, dcc, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.data.mock import generate_portfolio, generate_option_chain, SPOT

dash.register_page(__name__, path="/greeks", name="Greeks & Risk")

portfolio = generate_portfolio()

# Agrégats
total_delta  = portfolio["portfolio_delta"].sum()
total_gamma  = portfolio["portfolio_gamma"].sum()
total_vega   = portfolio["portfolio_vega"].sum()
total_theta  = portfolio["portfolio_theta"].sum()
total_dgamma = portfolio["portfolio_dollar_gamma"].sum() if "portfolio_dollar_gamma" in portfolio else 0
total_pnl    = portfolio["pnl_approx"].sum()

# ── Graphe: Greeks par position ──────────────────────────────────────────────
def bar_greek(col, color, title):
    fig = go.Figure(go.Bar(
        x=portfolio["contract_key"],
        y=portfolio[col],
        marker_color=[color if v >= 0 else "#f85149" for v in portfolio[col]],
        text=[f"{v:.2f}" for v in portfolio[col]],
        textposition="outside",
    ))
    fig.update_layout(template="plotly_dark", paper_bgcolor="#161b22",
                      plot_bgcolor="#161b22", margin=dict(l=40, r=20, t=30, b=80),
                      title=title, height=280,
                      xaxis=dict(tickangle=-20))
    return fig

layout = dbc.Container([
    html.Div([
        html.H2("Greeks & Risk Analytics"),
        html.P("Sensibilités par contrat et agrégées au niveau du portefeuille. Formules Eqs. 13–19."),
    ], className="page-header"),

    # Formules
    dbc.Card([
        dbc.CardHeader("Formules des Greeks"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.P("Eq. 13 — Delta :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$\Delta = e^{-rT} N(d_1)$$", mathjax=True), className="formula-box"),
                ], width=3),
                dbc.Col([
                    html.P("Eq. 14 — Gamma :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$\Gamma = \frac{e^{-rT} N'(d_1)}{F\,\sigma\sqrt{T}}$$", mathjax=True), className="formula-box"),
                ], width=3),
                dbc.Col([
                    html.P("Eq. 15 — Vega (par 1% vol) :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$\nu = 0.01 \cdot e^{-rT} F \, N'(d_1) \sqrt{T}$$", mathjax=True), className="formula-box"),
                ], width=3),
                dbc.Col([
                    html.P("Eq. 16 — Theta (par jour) :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$\Theta = \frac{1}{365}\frac{\partial V}{\partial t}$$", mathjax=True), className="formula-box"),
                ], width=3),
            ]),
            dbc.Row([
                dbc.Col([
                    html.P("Eq. 17 — Dollar Gamma :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$\$\Gamma = \Gamma \cdot S^2 \cdot \text{mult} / 100$$", mathjax=True), className="formula-box"),
                ], width=4),
                dbc.Col([
                    html.P("Eq. 18 — Dollar Vega :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$\$\nu = \nu \cdot \text{mult}$$", mathjax=True), className="formula-box"),
                ], width=4),
                dbc.Col([
                    html.P("Eq. 19 — PnL approximatif (Greeks locaux) :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$\delta P \approx \Delta\delta S + \tfrac{1}{2}\Gamma\delta S^2 + \nu\,\delta\sigma + \Theta\,\delta t$$", mathjax=True), className="formula-box"),
                ], width=4),
            ], className="mt-3"),
        ]),
    ], className="card mb-4"),

    # Métriques portefeuille
    dbc.Row([
        dbc.Col(html.Div([html.Div(f"{total_delta:,.0f}",  className="metric-value"),
                          html.Div("Δ Delta total", className="metric-label")],
                         className=f"metric-box {'positive' if total_delta >= 0 else 'negative'}"), width=2),
        dbc.Col(html.Div([html.Div(f"{total_gamma:,.2f}", className="metric-value"),
                          html.Div("Γ Gamma total", className="metric-label")],
                         className=f"metric-box {'positive' if total_gamma >= 0 else 'negative'}"), width=2),
        dbc.Col(html.Div([html.Div(f"{total_vega:,.0f}",  className="metric-value"),
                          html.Div("ν Vega total", className="metric-label")],
                         className=f"metric-box {'positive' if total_vega >= 0 else 'negative'}"), width=2),
        dbc.Col(html.Div([html.Div(f"{total_theta:,.0f}", className="metric-value"),
                          html.Div("Θ Theta total", className="metric-label")],
                         className=f"metric-box {'positive' if total_theta >= 0 else 'negative'}"), width=2),
        dbc.Col(html.Div([html.Div(f"${total_dgamma:,.0f}", className="metric-value"),
                          html.Div("$ Gamma total", className="metric-label")],
                         className=f"metric-box {'positive' if total_dgamma >= 0 else 'negative'}"), width=2),
        dbc.Col(html.Div([html.Div(f"${total_pnl:,.0f}", className="metric-value"),
                          html.Div("PnL valorisé", className="metric-label")],
                         className=f"metric-box {'positive' if total_pnl >= 0 else 'negative'}"), width=2),
    ], className="g-3 mb-4"),

    # Graphes Greeks par position
    dbc.Row([
        dbc.Col(dbc.Card([dbc.CardHeader("Delta par position"),
                           dbc.CardBody(dcc.Graph(figure=bar_greek("portfolio_delta", "#58a6ff", ""), config={"displayModeBar": False}))],
                         className="card"), width=6),
        dbc.Col(dbc.Card([dbc.CardHeader("Vega par position"),
                           dbc.CardBody(dcc.Graph(figure=bar_greek("portfolio_vega", "#3fb950", ""), config={"displayModeBar": False}))],
                         className="card"), width=6),
    ], className="mb-4"),

    # Tableau positions
    dbc.Card([
        dbc.CardHeader("Détail des positions — Greeks ligne par ligne"),
        dbc.CardBody(
            dash_table.DataTable(
                data=portfolio.round(4).to_dict("records"),
                columns=[
                    {"name": "Contrat",      "id": "contract_key"},
                    {"name": "Qty",          "id": "quantity"},
                    {"name": "Strike",       "id": "strike"},
                    {"name": "Right",        "id": "right"},
                    {"name": "Expiry",       "id": "expiry"},
                    {"name": "IV σ",         "id": "implied_vol"},
                    {"name": "Prix",         "id": "price"},
                    {"name": "Δ Delta",      "id": "delta"},
                    {"name": "Γ Gamma",      "id": "gamma"},
                    {"name": "ν Vega",       "id": "vega"},
                    {"name": "Θ Theta",      "id": "theta"},
                    {"name": "Δ portef.",    "id": "portfolio_delta"},
                    {"name": "ν portef.",    "id": "portfolio_vega"},
                    {"name": "Θ portef.",    "id": "portfolio_theta"},
                ],
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "center", "padding": "8px"},
                sort_action="native",
            )
        ),
    ], className="card"),
], fluid=True)
