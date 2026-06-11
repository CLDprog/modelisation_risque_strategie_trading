"""Page bonus — Monte Carlo sous ℚ : option asiatique arithmétique sur le produit
sélectionné, calibrée sur NOTRE infra (spot du store, carry du forward de parité,
σ de la surface IV). Variate de contrôle géométrique (Kemna-Vorst)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math

import dash
from dash import html, dcc, callback, Input, Output, State
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import numpy as np

from src.data.source import datasource
from src.data import no_data_alert
from src.pricing.monte_carlo import price_asian_mc

dash.register_page(__name__, path="/monte-carlo", name="Monte Carlo (bonus)")

_LAYOUT = dict(template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
               font=dict(size=11))

layout = dbc.Container([
    html.Div([
        html.H2("Bonus — Monte Carlo : option asiatique (mesure ℚ)"),
        html.P("Le payoff asiatique dépend de la MOYENNE du chemin → pas de forme fermée, "
               "l'arbre explose : c'est le territoire de Monte Carlo. On simule un GBM sous "
               "la mesure risque-neutre, calibré sur notre infra : spot du store, drift "
               "b = r − q tiré de notre forward de parité, σ de notre surface IV. "
               "Précision par variate de contrôle géométrique (Kemna-Vorst exact)."),
    ], className="page-header"),

    dbc.Card([
        dbc.CardHeader("Paramètres de simulation"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.Label("Maturité (mois)", className="text-muted small"),
                    dcc.Slider(id="mc-months", min=1, max=24, step=1, value=12,
                               marks={i: str(i) for i in (1, 6, 12, 18, 24)},
                               tooltip={"placement": "bottom"}),
                ], width=3),
                dbc.Col([
                    html.Label("Strike (% du spot)", className="text-muted small"),
                    dcc.Slider(id="mc-strike-pct", min=70, max=130, step=1, value=100,
                               marks={i: f"{i}%" for i in (70, 85, 100, 115, 130)},
                               tooltip={"placement": "bottom"}),
                ], width=3),
                dbc.Col([
                    html.Label("Type", className="text-muted small"),
                    dcc.Dropdown(id="mc-right",
                                 options=[{"label": "Call", "value": "C"},
                                          {"label": "Put", "value": "P"}],
                                 value="C", clearable=False,
                                 style={"fontSize": "12px"}),
                ], width=2),
                dbc.Col([
                    html.Label("Observations de la moyenne", className="text-muted small"),
                    dcc.Dropdown(id="mc-fixing",
                                 options=[{"label": "Mensuelles", "value": "M"},
                                          {"label": "Hebdomadaires", "value": "W"},
                                          {"label": "Quotidiennes", "value": "D"}],
                                 value="W", clearable=False,
                                 style={"fontSize": "12px"}),
                ], width=2),
                dbc.Col([
                    html.Label("Chemins", className="text-muted small"),
                    dcc.Dropdown(id="mc-paths",
                                 options=[{"label": f"{n:,}", "value": n}
                                          for n in (10_000, 25_000, 50_000, 100_000)],
                                 value=25_000, clearable=False,
                                 style={"fontSize": "12px"}),
                ], width=2),
            ], className="g-2 mb-2"),
            dbc.Button("Simuler", id="mc-run", color="primary", size="sm"),
        ]),
    ], className="card mb-3"),

    dcc.Loading([
        dbc.Row(id="mc-metrics", className="g-3 mb-3"),
        dbc.Row([
            dbc.Col(dbc.Card([
                dbc.CardHeader("Éventail de trajectoires simulées (GBM sous ℚ)"),
                dbc.CardBody(dcc.Graph(id="mc-paths-fig", config={"displayModeBar": False}),
                             className="p-2"),
            ], className="card h-100"), width=7),
            dbc.Col(dbc.Card([
                dbc.CardHeader("Distribution de la moyenne arithmétique vs strike"),
                dbc.CardBody(dcc.Graph(id="mc-dist-fig", config={"displayModeBar": False}),
                             className="p-2"),
            ], className="card h-100"), width=5),
        ], className="g-2 mb-3"),
        dbc.Row([
            dbc.Col(dbc.Card([
                dbc.CardHeader("Convergence de l'estimateur — brut vs variate de contrôle"),
                dbc.CardBody([
                    dcc.Graph(id="mc-conv-fig", config={"displayModeBar": False}),
                    html.Small("La bande = ±1.96 erreur-type (IC 95 %). La variate de contrôle "
                               "corrige l'estimateur arithmétique par l'erreur OBSERVÉE du "
                               "géométrique, dont le prix exact (Kemna-Vorst) est connu — même "
                               "précision avec ~50× moins de chemins.",
                               className="text-muted"),
                ], className="p-2"),
            ], className="card"), width=12),
        ], className="g-2"),
    ]),
], fluid=True)


def _infra_inputs(sym: str, maturity: float):
    """Spot, carry implicite et σ ATM tirés de NOS tables (pas de valeurs inventées)."""
    spot = datasource.get_spot(sym)
    rate = 0.025
    try:
        from src.utils.config import load_config
        rate = load_config("pricing").get("risk_free_rate", {}).get("value", 0.025)
    except Exception:
        pass
    carry_q = 0.0
    fwd = datasource.get_forward_curve(sym)
    if not fwd.empty and "implied_carry" in fwd.columns:
        c = fwd.dropna(subset=["implied_carry"])
        if not c.empty and "maturity_years" in c.columns:
            idx = (c["maturity_years"] - maturity).abs().idxmin()
            carry_q = float(c.loc[idx, "implied_carry"])
    sigma = None
    chain = datasource.get_option_chain(sym)
    if not chain.empty and "implied_vol" in chain.columns:
        u = chain[chain["is_usable"]] if "is_usable" in chain.columns else chain
        u = u.dropna(subset=["implied_vol", "log_moneyness", "maturity_years"])
        if not u.empty:
            nearest_t = u.loc[(u["maturity_years"] - maturity).abs().idxmin(), "maturity_years"]
            sl = u[u["maturity_years"] == nearest_t]
            sigma = float(sl.loc[sl["log_moneyness"].abs().idxmin(), "implied_vol"])
    return spot, rate, carry_q, sigma


@callback(
    Output("mc-metrics",   "children"),
    Output("mc-paths-fig", "figure"),
    Output("mc-dist-fig",  "figure"),
    Output("mc-conv-fig",  "figure"),
    Input("mc-run",        "n_clicks"),
    Input("selected-symbol", "data"),
    State("mc-months",     "value"),
    State("mc-strike-pct", "value"),
    State("mc-right",      "value"),
    State("mc-fixing",     "value"),
    State("mc-paths",      "value"),
)
def run_mc(_clicks, symbol, months, strike_pct, right, fixing, n_paths):
    sym = symbol or "ESTX50"
    empty = go.Figure()
    T = (months or 12) / 12.0

    spot, rate, carry_q, sigma = _infra_inputs(sym, T)
    if not spot or not sigma:
        return [dbc.Col(no_data_alert(sym), width=12)], empty, empty, empty

    strike = round(spot * (strike_pct or 100) / 100.0, 2)
    b = rate - carry_q                       # drift ℚ = r − q (carry de la parité)
    n_steps = max(int(T * {"M": 12, "W": 52, "D": 252}[fixing or "W"]), 2)

    res = price_asian_mc(spot, strike, sigma, T, rate, b, right or "C",
                         n_paths=n_paths or 25_000, n_steps=n_steps, seed=42)

    ci = 1.96 * res.std_error
    metrics = [
        dbc.Col(_mb(f"{res.price:,.2f} € ± {ci:,.2f}", "Asiatique arithmétique (MC + CV, IC 95%)",
                    "positive"), width=3),
        dbc.Col(_mb(f"{res.geo_closed_form:,.2f} €", "Géométrique exacte (Kemna-Vorst)"), width=2),
        dbc.Col(_mb(f"{res.european_bs:,.2f} €", "Européenne équivalente (Black)"), width=2),
        dbc.Col(_mb(f"×{res.variance_reduction:,.0f}", "Réduction de variance (CV)", "info"), width=2),
        dbc.Col(_mb(f"S={spot:,.0f} · σ={sigma:.1%} · b={b:+.2%}",
                    "Inputs (store · surface IV · parité)"), width=3),
    ]

    # 1) Éventail de trajectoires
    fig_paths = go.Figure()
    t_axis = np.linspace(0, T * 365, res.sample_paths.shape[1])
    for p in res.sample_paths[:100]:
        fig_paths.add_trace(go.Scatter(x=t_axis, y=p, mode="lines",
                                       line=dict(width=0.7, color="rgba(9,105,218,0.18)"),
                                       showlegend=False, hoverinfo="skip"))
    mean_path = res.sample_paths.mean(axis=0)
    fig_paths.add_trace(go.Scatter(x=t_axis, y=mean_path, mode="lines", name="moyenne",
                                   line=dict(width=2.5, color="#0a3069")))
    fig_paths.add_hline(y=strike, line_dash="dash", line_color="#cf222e",
                        annotation_text=f"K = {strike:,.0f}")
    fig_paths.add_hline(y=spot, line_dash="dot", line_color="#57606a",
                        annotation_text=f"S₀ = {spot:,.0f}")
    fig_paths.update_layout(height=340, margin=dict(l=45, r=15, t=8, b=35),
                            xaxis_title="Jours", yaxis_title="Niveau simulé", **_LAYOUT)

    # 2) Distribution de la moyenne arithmétique
    fig_dist = go.Figure()
    fig_dist.add_trace(go.Histogram(x=res.averages, nbinsx=80, marker_color="#0969da",
                                    opacity=0.8, name="moyenne A"))
    fig_dist.add_vline(x=strike, line_dash="dash", line_color="#cf222e",
                       annotation_text="K")
    fig_dist.add_vline(x=float(np.mean(res.averages)), line_dash="dot", line_color="#1a7f37",
                       annotation_text="E[A]")
    fig_dist.update_layout(height=340, margin=dict(l=45, r=15, t=8, b=35),
                           xaxis_title="Moyenne arithmétique du chemin",
                           yaxis_title="Fréquence", showlegend=False, **_LAYOUT)

    # 3) Convergence brut vs variate de contrôle
    fig_conv = go.Figure()
    n = res.convergence_n
    fig_conv.add_trace(go.Scatter(
        x=np.concatenate([n, n[::-1]]),
        y=np.concatenate([res.convergence_cv + 1.96 * res.convergence_se,
                          (res.convergence_cv - 1.96 * res.convergence_se)[::-1]]),
        fill="toself", fillcolor="rgba(26,127,55,0.12)",
        line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig_conv.add_trace(go.Scatter(x=n, y=res.convergence_raw, name="MC brut",
                                  line=dict(color="#bc4c00", width=1.3)))
    fig_conv.add_trace(go.Scatter(x=n, y=res.convergence_cv, name="MC + variate de contrôle",
                                  line=dict(color="#1a7f37", width=2)))
    fig_conv.add_hline(y=res.geo_closed_form, line_dash="dot", line_color="#57606a",
                       annotation_text="géométrique exacte (repère)")
    fig_conv.update_layout(height=300, margin=dict(l=45, r=15, t=8, b=35),
                           xaxis=dict(type="log", title="Nombre de chemins (log)"),
                           yaxis_title="Prix estimé (€)",
                           legend=dict(orientation="h", y=1.12, font=dict(size=10)),
                           **_LAYOUT)

    return metrics, fig_paths, fig_dist, fig_conv


def _mb(value, label, css=""):
    return html.Div([
        html.Div(str(value), className="metric-value", style={"fontSize": "0.95rem"}),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
