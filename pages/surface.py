import dash
from dash import html, dcc
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import numpy as np
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.data.source import datasource

dash.register_page(__name__, path="/surface", name="Surface de Vol")

chain = datasource.get_option_chain()
calls = chain[chain["right"] == "C"].copy()

# ── Surface 3D ───────────────────────────────────────────────────────────────
maturities = sorted(calls["maturity_years"].unique())
moneyness_grid = np.linspace(-0.15, 0.15, 40)

Z = []
for T in maturities:
    row = []
    sub = calls[calls["maturity_years"] == T].sort_values("log_moneyness")
    for k in moneyness_grid:
        iv_vals = sub[abs(sub["log_moneyness"] - k) < 0.02]["implied_vol"]
        row.append(float(iv_vals.mean() * 100) if len(iv_vals) > 0 else np.nan)
    Z.append(row)

Z = np.array(Z)

fig_3d = go.Figure(data=[go.Surface(
    z=Z,
    x=moneyness_grid,
    y=[t * 365 for t in maturities],
    colorscale="Blues",
    colorbar=dict(title=dict(text="IV (%)"), tickfont=dict(color="#e6edf3")),
    opacity=0.9,
)])
fig_3d.update_layout(
    template="plotly_dark", paper_bgcolor="#161b22",
    margin=dict(l=0, r=0, t=30, b=0),
    height=520,
    scene=dict(
        xaxis=dict(title="Log-moneyness k", color="#8b949e", gridcolor="#30363d"),
        yaxis=dict(title="Jours à expiration", color="#8b949e", gridcolor="#30363d"),
        zaxis=dict(title="IV (%)", color="#8b949e", gridcolor="#30363d"),
        bgcolor="#0d1117",
    ),
)

# ── Surface 2D heatmap ───────────────────────────────────────────────────────
fig_heat = go.Figure(data=go.Heatmap(
    z=Z,
    x=np.round(moneyness_grid, 3),
    y=[int(t * 365) for t in maturities],
    colorscale="RdYlGn_r",
    colorbar=dict(title="IV (%)", tickfont=dict(color="#e6edf3")),
    text=np.round(Z, 1),
    texttemplate="%{text}%",
    textfont=dict(size=9),
))
fig_heat.update_layout(
    template="plotly_dark", paper_bgcolor="#161b22", plot_bgcolor="#161b22",
    margin=dict(l=60, r=20, t=30, b=60),
    xaxis_title="Log-moneyness k", yaxis_title="Jours à expiration",
    height=350,
)

# ── Layout ───────────────────────────────────────────────────────────────────
layout = dbc.Container([
    html.Div([
        html.H2("Surface de Volatilité"),
        html.P("Surface SVI calibrée par tranche de maturité, interpolée en espace de variance totale."),
    ], className="page-header"),

    # Formules SVI
    dbc.Card([
        dbc.CardHeader("Modèle SVI — Stochastic Volatility Inspired"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.P("Eq. 20 — Variance totale SVI par tranche :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(
                        r"$$w(k) = a + b\left[\rho(k-m) + \sqrt{(k-m)^2 + \sigma^2}\right]$$",
                        mathjax=True), className="formula-box"),
                    html.Ul([
                        html.Li("k = log-moneyness = ln(K/F)", className="text-muted small"),
                        html.Li("w = variance totale = σ²·T", className="text-muted small"),
                        html.Li("a, b, ρ, m, σ = 5 paramètres à calibrer", className="text-muted small"),
                    ], className="mt-2"),
                ], width=6),
                dbc.Col([
                    html.P("Eq. 21 — Condition de monotonie calendaire (no-arb) :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(
                        r"$$w(k, T_1) \leq w(k, T_2) \quad \forall k, \; T_1 < T_2$$",
                        mathjax=True), className="formula-box"),
                    html.P("Eq. 22 — Interpolation en variance totale :", className="text-muted small mb-1 mt-3"),
                    html.Div(dcc.Markdown(
                        r"$$w(k, T) = \frac{T - T_1}{T_2 - T_1} w(k,T_2) + \frac{T_2 - T}{T_2 - T_1} w(k,T_1)$$",
                        mathjax=True), className="formula-box"),
                ], width=6),
            ]),
        ]),
    ], className="card mb-4"),

    # Surface 3D
    dbc.Card([
        dbc.CardHeader("Surface de volatilité implicite — Vue 3D"),
        dbc.CardBody(dcc.Graph(figure=fig_3d, config={"displayModeBar": True})),
    ], className="card mb-4"),

    # Heatmap
    dbc.Card([
        dbc.CardHeader("Heatmap IV — Log-moneyness × Maturité"),
        dbc.CardBody(dcc.Graph(figure=fig_heat, config={"displayModeBar": False})),
    ], className="card"),
], fluid=True)
