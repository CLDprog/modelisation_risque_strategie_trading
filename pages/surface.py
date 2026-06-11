"""Page 6 — Surface de Volatilité : grille SVI calibrée + paramètres par tranche."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dash
from dash import html, dcc, dash_table, callback, Input, Output
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import numpy as np
import pandas as pd

from src.data.source import datasource
from src.data import no_data_alert
from src.surfaces.calibration import svi_total_variance

dash.register_page(__name__, path="/surface", name="Surface de Vol")

_PARAM_COLUMNS = [
    {"name": "Expiry",      "id": "expiry"},
    {"name": "T (années)",  "id": "maturity_years", "type": "numeric", "format": {"specifier": ".3f"}},
    {"name": "Modèle",      "id": "model"},
    {"name": "Points",      "id": "n_points"},
    {"name": "RMSE",        "id": "fit_rmse",  "type": "numeric", "format": {"specifier": ".5f"}},
    {"name": "Qualité",     "id": "quality_flag"},
    {"name": "a",           "id": "svi_a",     "type": "numeric", "format": {"specifier": ".5f"}},
    {"name": "b",           "id": "svi_b",     "type": "numeric", "format": {"specifier": ".5f"}},
    {"name": "ρ",           "id": "svi_rho",   "type": "numeric", "format": {"specifier": ".4f"}},
    {"name": "m",           "id": "svi_m",     "type": "numeric", "format": {"specifier": ".4f"}},
    {"name": "σ",           "id": "svi_sigma", "type": "numeric", "format": {"specifier": ".4f"}},
]

layout = dbc.Container([
    html.Div([
        html.H2("Surface de Volatilité"),
        html.P("Surface SVI calibrée par tranche de maturité (table surface_grid), "
               "interpolée en espace de variance totale, avec les paramètres calibrés "
               "et leur qualité d'ajustement (table surface_parameters)."),
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
        dbc.CardHeader("Paramètres SVI calibrés par tranche (surface_parameters)"),
        dbc.CardBody(dash_table.DataTable(
            id="surf-params-table",
            columns=_PARAM_COLUMNS,
            style_header={"textTransform": "none"},
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "padding": "6px", "fontSize": "13px"},
            style_data_conditional=[
                {"if": {"filter_query": '{quality_flag} != "ok"', "column_id": "quality_flag"},
                 "color": "#9a6700", "fontWeight": "bold"},
            ],
            sort_action="native",
        )),
    ], className="card mb-4"),

    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Surface de volatilité — Vue 3D (grille modèle)"),
            dbc.CardBody(dcc.Graph(id="surf-3d", config={"displayModeBar": True}),
                         className="p-2"),
        ], className="card h-100"), width=7),
        dbc.Col(dbc.Card([
            dbc.CardHeader("Heatmap IV — Moneyness standardisée × Maturité"),
            dbc.CardBody(dcc.Graph(id="surf-heat", config={"displayModeBar": False}),
                         className="p-2"),
        ], className="card h-100"), width=5),
    ], className="g-2 mb-3"),

    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Term structure des paramètres SVI (a · b · σ | ρ)"),
            dbc.CardBody(dcc.Graph(id="surf-svi-term", config={"displayModeBar": False}),
                         className="p-2"),
        ], className="card h-100"), width=7),
        dbc.Col(dbc.Card([
            dbc.CardHeader("No-arbitrage calendaire (Eq.21) — w(0,T) croissante"),
            dbc.CardBody(dcc.Graph(id="surf-calendar", config={"displayModeBar": False}),
                         className="p-2"),
        ], className="card h-100"), width=5),
    ], className="g-2"),
], fluid=True)


@callback(
    Output("surf-metrics",      "children"),
    Output("surf-params-table", "data"),
    Output("surf-3d",           "figure"),
    Output("surf-heat",         "figure"),
    Output("surf-svi-term",     "figure"),
    Output("surf-calendar",     "figure"),
    Input("surf-interval",      "n_intervals"),
    Input("selected-symbol",    "data"),
)
def refresh_surface(_, symbol):
    sym    = symbol or "ESTX50"
    grid   = datasource.get_surface_grid(sym)
    params = datasource.get_surface_parameters(sym)

    if grid.empty:
        empty = go.Figure()
        return [dbc.Col(no_data_alert(sym), width=12)], [], empty, empty, empty, empty

    # Chaque tranche de maturité a SA PROPRE grille de k (issue de ses points
    # calibrés) → un pivot brut donne une matrice quasi-vide (NaN partout).
    # On interpole donc chaque tranche sur une grille k COMMUNE.
    slices = {}
    for T, grp in grid.groupby("maturity_years"):
        g = grp.dropna(subset=["log_moneyness", "implied_vol"]).sort_values("log_moneyness")
        if len(g) >= 2:
            slices[float(T)] = (g["log_moneyness"].to_numpy(dtype=float),
                                g["implied_vol"].to_numpy(dtype=float) * 100)
    Ts = sorted(slices)
    if Ts:
        # Surface tracée en MONEYNESS STANDARDISÉE z = k/(σ_ATM·√T) (≈ espace
        # delta, convention desk) : chaque maturité couvre ainsi le MÊME domaine
        # (la grille collectée ±10Δ ↔ z ≈ ±1.3 quel que soit T). On évalue la
        # FORMULE SVI de chaque tranche (modèle, défini pour tout k) aux k = z·σ√T
        # correspondants → surface rectangulaire complète SANS extrapoler la
        # tranche courte sur le domaine absolu de la longue (k=±0.35 à 30j = des
        # deltas de 0.0001, ailes explosives sans signification).
        # Fallback interp + NaN hors plage pour les tranches sans paramètres SVI.
        svi_by_T = {}
        if not params.empty and "svi_b" in params.columns:
            for _, r in params.iterrows():
                if r.get("model") == "svi" and pd.notna(r.get("svi_b")):
                    svi_by_T[round(float(r["maturity_years"]), 6)] = (
                        float(r["svi_a"]), float(r["svi_b"]), float(r["svi_rho"]),
                        float(r["svi_m"]), float(r["svi_sigma"]))
        x = np.linspace(-1.5, 1.5, 41)                  # z, ~ ±7Δ
        rows = []
        for T in Ts:
            kk, vv = slices[T]
            sig_atm = float(np.interp(0.0, kk, vv)) / 100.0
            k_eval = x * sig_atm * np.sqrt(T)
            p = svi_by_T.get(round(T, 6))
            if p is not None:
                w = svi_total_variance(k_eval, *p)
                zi = np.sqrt(np.maximum(w / T, 0.0)) * 100.0
            else:
                zi = np.interp(k_eval, kk, vv)
                zi[(k_eval < kk.min() - 1e-12) | (k_eval > kk.max() + 1e-12)] = np.nan
            rows.append(zi)
        Z = np.array(rows)
        y = np.array(Ts) * 365
    else:
        x, y, Z = np.array([]), np.array([]), np.array([[]])

    # Métriques depuis surface_parameters
    n_slices = int(params.shape[0]) if not params.empty else int(grid["expiry"].nunique())
    rmse_max = float(params["fit_rmse"].max()) if "fit_rmse" in params.columns and not params.empty else None
    n_bad    = (int((params["quality_flag"] != "ok").sum())
                if "quality_flag" in params.columns and not params.empty else 0)
    model    = (params["model"].iloc[0]
                if "model" in params.columns and not params.empty else "SVI")

    metrics = [
        dbc.Col(_mb(str(n_slices), "Tranches calibrées", "positive"), width=3),
        dbc.Col(_mb(str(int(grid.shape[0])), "Points de grille modèle"), width=3),
        dbc.Col(_mb(f"{rmse_max:.5f}" if rmse_max is not None else "—", "RMSE max",
                    "positive" if (rmse_max or 0) < 0.01 else "warning"), width=3),
        dbc.Col(_mb(str(model) + (f" · {n_bad} flag(s)" if n_bad else ""), "Modèle / qualité",
                    "warning" if n_bad else "info"), width=3),
    ]

    fig_3d = go.Figure()
    if Z.size > 0 and not np.all(np.isnan(Z)):
        fig_3d.add_trace(go.Surface(
            # Viridis : lisible sur fond BLANC ("Blues" démarre au blanc → la nappe
            # disparaissait dans le fond clair).
            z=Z, x=x, y=y, colorscale="Viridis",
            colorbar=dict(title=dict(text="IV (%)"), tickfont=dict(color="#1f2328")),
            opacity=0.97,
        ))
    fig_3d.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff",
        margin=dict(l=0, r=0, t=30, b=0), height=520,
        scene=dict(
            xaxis=dict(title="Moneyness standardisée z = k/(σ√T)", color="#57606a",
                       gridcolor="#d0d7de"),
            yaxis=dict(title="Jours à expiration", color="#57606a", gridcolor="#d0d7de"),
            zaxis=dict(title="IV (%)", color="#57606a", gridcolor="#d0d7de"),
            bgcolor="#ffffff",
        ),
        title=dict(text=f"Surface IV calibrée — {sym}", font=dict(color="#57606a", size=12)),
    )

    fig_heat = go.Figure()
    if Z.size > 0 and not np.all(np.isnan(Z)):
        fig_heat.add_trace(go.Heatmap(
            z=Z, x=np.round(x, 4), y=[int(v) for v in y],
            colorscale="RdYlGn_r",
            colorbar=dict(title="IV (%)", tickfont=dict(color="#1f2328")),
            hovertemplate="k=%{x}<br>%{y}j<br>IV=%{z:.2f}%<extra></extra>",
        ))
    fig_heat.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        margin=dict(l=60, r=20, t=30, b=60),
        xaxis_title="Moneyness standardisée z = k/(σ√T)",
        yaxis_title="Jours à expiration", height=520,
    )

    # SVI : term structure des paramètres calibrés (a,b,σ | ρ,m)
    fig_svi = go.Figure()
    if not params.empty and "maturity_years" in params.columns:
        p = params.sort_values("maturity_years")
        days = (p["maturity_years"] * 365).round().astype(int)
        for col, color, name in [("svi_a", "#0969da", "a (niveau)"),
                                 ("svi_b", "#1a7f37", "b (pente ailes)"),
                                 ("svi_sigma", "#9a6700", "σ (courbure)")]:
            if col in p.columns:
                fig_svi.add_trace(go.Scatter(x=days, y=p[col], name=name,
                                             mode="lines+markers", line=dict(color=color)))
        if "svi_rho" in p.columns:
            fig_svi.add_trace(go.Scatter(x=days, y=p["svi_rho"], name="ρ (corrélation/skew)",
                                         mode="lines+markers",
                                         line=dict(color="#cf222e", dash="dash"),
                                         yaxis="y2"))
    fig_svi.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        height=300, margin=dict(l=45, r=45, t=8, b=35), font=dict(size=11),
        xaxis_title="Jours à expiration", yaxis_title="a · b · σ",
        yaxis2=dict(title="ρ", overlaying="y", side="right", range=[-1, 1]),
        legend=dict(orientation="h", y=1.14, font=dict(size=10)),
    )

    # No-arbitrage calendaire (Eq.21) : w(0,T) doit être croissante en T
    fig_cal = go.Figure()
    if Ts:
        w_atm = [float(np.interp(0.0, *slices[T])) ** 2 / 1e4 * T for T in Ts]  # (iv%→frac)²·T
        ok = all(w_atm[i] <= w_atm[i + 1] + 1e-12 for i in range(len(w_atm) - 1))
        fig_cal.add_trace(go.Scatter(
            x=[T * 365 for T in Ts], y=w_atm, mode="lines+markers",
            line=dict(color="#1a7f37" if ok else "#cf222e", width=2),
            marker=dict(size=7), name="w(0,T)"))
        fig_cal.add_annotation(x=0.02, y=0.98, xref="paper", yref="paper", showarrow=False,
                               text=("✓ monotone (pas d'arbitrage calendaire)" if ok
                                     else "✗ violation de monotonie"),
                               font=dict(size=11, color="#1a7f37" if ok else "#cf222e"))
    fig_cal.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        height=300, margin=dict(l=45, r=15, t=8, b=35), font=dict(size=11),
        xaxis_title="Jours à expiration", yaxis_title="Variance totale ATM w = σ²T",
    )

    table = params.round(6).to_dict("records") if not params.empty else []
    return metrics, table, fig_3d, fig_heat, fig_svi, fig_cal


def _mb(value, label, css=""):
    return html.Div([
        html.Div(str(value), className="metric-value"),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
