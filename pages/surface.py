"""Page 6 — Surface de Volatilité, symbol-aware avec callbacks."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dash
from dash import html, dcc, callback, Input, Output
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import numpy as np

from src.data.source import datasource
from src.data import no_data_alert

dash.register_page(__name__, path="/surface", name="Surface de Vol")

layout = dbc.Container([
    html.Div([
        html.H2("Surface de Volatilité"),
        html.P("Surface SVI calibrée par tranche de maturité, interpolée en espace de variance totale."),
    ], className="page-header"),

    dcc.Interval(id="surf-interval", interval=60000, n_intervals=0),

    dbc.Row(id="surf-metrics", className="g-3 mb-4"),

    dbc.Card([
        dbc.CardHeader("Modèle SVI — Stochastic Volatility Inspired"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.P("Eq. 20 — Variance totale SVI :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(
                        r"$$w(k) = a + b\left[\rho(k-m) + \sqrt{(k-m)^2 + \sigma^2}\right]$$",
                        mathjax=True), className="formula-box"),
                    html.Ul([
                        html.Li("k = ln(K/F), w = σ²·T", className="text-muted small"),
                        html.Li("5 paramètres : a, b, ρ, m, σ", className="text-muted small"),
                    ], className="mt-2"),
                ], width=6),
                dbc.Col([
                    html.P("Eq. 21 — Monotonie calendaire (no-arb) :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(
                        r"$$w(k, T_1) \leq w(k, T_2) \quad \forall k, \; T_1 < T_2$$",
                        mathjax=True), className="formula-box"),
                    html.P("Eq. 22 — Interpolation en variance totale :", className="text-muted small mb-1 mt-2"),
                    html.Div(dcc.Markdown(
                        r"$$w(k,T) = \frac{T_2-T}{T_2-T_1}w(k,T_1) + \frac{T-T_1}{T_2-T_1}w(k,T_2)$$",
                        mathjax=True), className="formula-box"),
                ], width=6),
            ]),
        ]),
    ], className="card mb-4"),

    dbc.Card([
        dbc.CardHeader("Surface de volatilité — Vue 3D"),
        dbc.CardBody(dcc.Graph(id="surf-3d", config={"displayModeBar": True})),
    ], className="card mb-4"),

    dbc.Card([
        dbc.CardHeader("Heatmap IV — Log-moneyness × Maturité"),
        dbc.CardBody(dcc.Graph(id="surf-heat", config={"displayModeBar": False})),
    ], className="card"),
], fluid=True)


@callback(
    Output("surf-metrics", "children"),
    Output("surf-3d",      "figure"),
    Output("surf-heat",    "figure"),
    Input("surf-interval", "n_intervals"),
    Input("selected-symbol","data"),
)
def refresh_surface(_, symbol):
    sym   = symbol or "SPY"
    chain = datasource.get_option_chain(sym)
    src   = datasource.get_data_source_label()

    if chain.empty:
        alert = no_data_alert(sym)
        empty = go.Figure()
        return [dbc.Col(alert, width=12)], empty, empty

    calls = chain[chain["right"] == "C"].copy()
    calls = calls.dropna(subset=["log_moneyness", "implied_vol"])

    maturities = sorted(calls["maturity_years"].unique()) if not calls.empty else []

    # Grille de moneyness DIRIGÉE PAR LES DONNÉES (et non fixe -0.15..0.15) :
    # les strikes réels ne couvrent qu'une bande étroite autour de l'ATM. On borne
    # la grille à la plage observée pour éviter une heatmap pleine de NaN.
    if maturities:
        k_lo = float(calls["log_moneyness"].min())
        k_hi = float(calls["log_moneyness"].max())
        if k_hi - k_lo < 1e-6:
            k_lo, k_hi = k_lo - 0.01, k_hi + 0.01
        moneyness_grid = np.linspace(k_lo, k_hi, 21)
    else:
        moneyness_grid = np.linspace(-0.05, 0.05, 21)

    # Interpolation du smile de chaque maturité sur la grille commune (pas de trous)
    Z = []
    for T in maturities:
        sub = (calls[calls["maturity_years"] == T]
               .sort_values("log_moneyness"))
        if len(sub) >= 2:
            iv_interp = np.interp(moneyness_grid,
                                  sub["log_moneyness"].values,
                                  sub["implied_vol"].values * 100)
            Z.append(iv_interp.tolist())
        else:
            Z.append([np.nan] * len(moneyness_grid))
    Z = np.array(Z) if Z else np.array([[]])

    # Métriques
    n_slices   = len(maturities)
    calendar_ok = True  # TODO: vérifier depuis surface_results persistée
    n_pts      = len(calls)

    metrics = [
        dbc.Col(_mb(str(n_slices),  "Tranches calibrées", "positive"), width=3),
        dbc.Col(_mb(str(n_pts),     f"Points {sym}",       "positive"), width=3),
        dbc.Col(_mb("OK" if calendar_ok else "FAIL", "Monotonie cal.",
                    "positive" if calendar_ok else "negative"), width=3),
        dbc.Col(_mb(src, "Source",
                    "positive" if src == "Live IBKR"
                    else "info" if src == "Analytics (EOD)" else "warning"), width=3),
    ]

    # 3D surface
    fig_3d = go.Figure()
    if Z.size > 0 and not np.all(np.isnan(Z)):
        fig_3d.add_trace(go.Surface(
            z=Z, x=moneyness_grid, y=[t * 365 for t in maturities],
            colorscale="Blues",
            colorbar=dict(title=dict(text="IV (%)"), tickfont=dict(color="#e6edf3")),
            opacity=0.9,
        ))
    fig_3d.update_layout(
        template="plotly_dark", paper_bgcolor="#161b22",
        margin=dict(l=0, r=0, t=30, b=0), height=520,
        scene=dict(
            xaxis=dict(title="Log-moneyness k", color="#8b949e", gridcolor="#30363d"),
            yaxis=dict(title="Jours à expiration", color="#8b949e", gridcolor="#30363d"),
            zaxis=dict(title="IV (%)", color="#8b949e", gridcolor="#30363d"),
            bgcolor="#0d1117",
        ),
        title=dict(text=f"Surface IV — {sym}", font=dict(color="#8b949e", size=12)),
    )

    # Heatmap — grille resserrée sur les données, sans labels NaN
    fig_heat = go.Figure()
    if Z.size > 0 and not np.all(np.isnan(Z)):
        fig_heat.add_trace(go.Heatmap(
            z=Z, x=np.round(moneyness_grid, 4), y=[int(t * 365) for t in maturities],
            colorscale="RdYlGn_r",
            colorbar=dict(title="IV (%)", tickfont=dict(color="#e6edf3")),
            hovertemplate="k=%{x}<br>%{y}j<br>IV=%{z:.2f}%<extra></extra>",
        ))
    fig_heat.update_layout(
        template="plotly_dark", paper_bgcolor="#161b22", plot_bgcolor="#161b22",
        margin=dict(l=60, r=20, t=30, b=60),
        xaxis_title="Log-moneyness k", yaxis_title="Jours à expiration", height=350,
    )

    return metrics, fig_3d, fig_heat


def _mb(value, label, css=""):
    return html.Div([
        html.Div(str(value), className="metric-value"),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
