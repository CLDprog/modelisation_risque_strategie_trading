"""Page bonus — Monte Carlo desk : option asiatique arithmétique sous ℚ.
Inputs calibrés sur NOTRE infra (spot du store, σ ATM de la surface IV, drift calé
point par point sur NOTRE courbe forward de parité). Sobol + variate de contrôle,
greeks à nombres aléatoires communs, strike ladder sur les mêmes chemins."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math

import dash
from dash import html, dcc, dash_table, callback, Input, Output, State
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import numpy as np

from src.data.source import datasource
from src.data import no_data_alert
from src.pricing.monte_carlo import price_asian_mc, strike_ladder

dash.register_page(__name__, path="/monte-carlo", name="Monte Carlo (bonus)")

_LAYOUT = dict(template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
               font=dict(size=11))

layout = dbc.Container([
    html.Div([
        html.H2("Bonus — Monte Carlo desk : option asiatique (mesure ℚ)"),
        html.P("Payoff path-dependent → territoire de Monte Carlo. GBM sous la mesure "
               "risque-neutre avec drift calé POINT PAR POINT sur notre courbe forward de "
               "parité, σ ATM de notre surface IV, quasi-Monte Carlo (Sobol) + variate de "
               "contrôle géométrique, greeks à nombres aléatoires communs, et strike ladder "
               "repricé sur les mêmes chemins."),
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
                                 value="C", clearable=False, style={"fontSize": "12px"}),
                ], width=1),
                dbc.Col([
                    html.Label("Observations", className="text-muted small"),
                    dcc.Dropdown(id="mc-fixing",
                                 options=[{"label": "Mensuelles", "value": "M"},
                                          {"label": "Hebdomadaires", "value": "W"},
                                          {"label": "Quotidiennes", "value": "D"}],
                                 value="W", clearable=False, style={"fontSize": "12px"}),
                ], width=2),
                dbc.Col([
                    html.Label("Aléas", className="text-muted small"),
                    dcc.Dropdown(id="mc-method",
                                 options=[{"label": "Sobol (quasi-MC)", "value": "sobol"},
                                          {"label": "Pseudo + antithétiques", "value": "pseudo"}],
                                 value="sobol", clearable=False, style={"fontSize": "12px"}),
                ], width=2),
                dbc.Col([
                    html.Label("Chemins", className="text-muted small"),
                    dcc.Dropdown(id="mc-paths",
                                 options=[{"label": f"{n:,}", "value": n}
                                          for n in (10_000, 25_000, 50_000, 100_000)],
                                 value=25_000, clearable=False, style={"fontSize": "12px"}),
                ], width=1),
            ], className="g-2 mb-2"),
            dbc.Button("Simuler", id="mc-run", color="primary", size="sm"),
        ]),
    ], className="card mb-3"),

    dcc.Loading([
        dbc.Row(id="mc-metrics", className="g-3 mb-2"),
        dbc.Row(id="mc-greeks", className="g-3 mb-3"),
        dbc.Row([
            dbc.Col(dbc.Card([
                dbc.CardHeader("Trajectoires simulées + forwards de PARITÉ du marché (♦) "
                               "— la moyenne MC doit passer dessus"),
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
                dbc.CardHeader("Strike ladder — repricé sur les MÊMES chemins"),
                dbc.CardBody(dcc.Graph(id="mc-ladder-fig", config={"displayModeBar": False}),
                             className="p-2"),
            ], className="card h-100"), width=7),
            dbc.Col(dbc.Card([
                dbc.CardHeader("Ladder (détail)"),
                dbc.CardBody(dash_table.DataTable(
                    id="mc-ladder-table",
                    columns=[
                        {"name": "Strike", "id": "strike", "type": "numeric",
                         "format": {"specifier": ",.0f"}},
                        {"name": "Asiatique MC", "id": "asian_mc", "type": "numeric",
                         "format": {"specifier": ",.2f"}},
                        {"name": "± IC95", "id": "ci", "type": "numeric",
                         "format": {"specifier": ".2f"}},
                        {"name": "Géom. exacte", "id": "geo_cf", "type": "numeric",
                         "format": {"specifier": ",.2f"}},
                        {"name": "Euro. Black", "id": "european_bs", "type": "numeric",
                         "format": {"specifier": ",.2f"}},
                    ],
                    style_header={"textTransform": "none"},
                    style_table={"overflowX": "auto"},
                    style_cell={"textAlign": "center", "padding": "4px", "fontSize": "12px"},
                ), className="p-2"),
            ], className="card h-100"), width=5),
        ], className="g-2 mb-3"),
        dbc.Row([
            dbc.Col(dbc.Card([
                dbc.CardHeader("Simulateur de DELTA-HEDGE (vanille européenne, mêmes "
                               "S/K/σ/T) — « le prix d'une option est le coût de son hedge »"),
                dbc.CardBody([
                    dcc.Graph(id="mc-hedge-fig", config={"displayModeBar": False}),
                    html.Div(id="mc-hedge-stats", className="mt-1"),
                    html.Small("On VEND la vanille au prix Black puis on delta-hedge le long "
                               "de chaque chemin. Sans couverture : P&L dispersé et asymétrique. "
                               "Hedgé : centré sur 0, et l'écart-type se resserre en "
                               "~1/√(fréquence) — la démonstration empirique de Black-Scholes.",
                               className="text-muted"),
                ], className="p-2"),
            ], className="card"), width=12),
        ], className="g-2 mb-3"),
        dbc.Row([
            dbc.Col(dbc.Card([
                dbc.CardHeader("Convergence de l'estimateur — brut vs variate de contrôle"),
                dbc.CardBody([
                    dcc.Graph(id="mc-conv-fig", config={"displayModeBar": False}),
                    html.Small("Bande = ±1.96 erreur-type (IC 95 %). La variate de contrôle "
                               "corrige l'estimateur arithmétique par l'erreur OBSERVÉE du "
                               "géométrique (prix exact connu). Sobol : les points remplissent "
                               "l'hypercube uniformément → erreur ~1/N au lieu de 1/√N.",
                               className="text-muted"),
                ], className="p-2"),
            ], className="card"), width=12),
        ], className="g-2"),
    ]),
], fluid=True)


def _infra_inputs(sym: str, maturity: float):
    """Spot, taux, σ ATM et COURBE FORWARD tirés de nos tables (rien d'inventé)."""
    spot = datasource.get_spot(sym)
    rate = 0.025
    try:
        from src.utils.config import load_config
        rate = load_config("pricing").get("risk_free_rate", {}).get("value", 0.025)
    except Exception:
        pass

    # Courbe forward de parité → interpolation log-linéaire de F(t), extrapolation
    # au carry du dernier segment. Fallback : carry constant nul.
    fwd = datasource.get_forward_curve(sym)
    fwd_pts = []
    if not fwd.empty and {"maturity_years", "chosen_forward"}.issubset(fwd.columns):
        c = fwd.dropna(subset=["maturity_years", "chosen_forward"]).sort_values("maturity_years")
        fwd_pts = list(zip(c["maturity_years"].astype(float), c["chosen_forward"].astype(float)))

    def forward_curve(t):
        t = np.atleast_1d(np.asarray(t, dtype=float))
        if not spot:
            return np.full_like(t, np.nan)
        if not fwd_pts:
            return spot * np.exp(rate * t)            # fallback : drift = r (q=0)
        ts = np.array([0.0] + [p[0] for p in fwd_pts])
        ln_fs = np.log(np.array([spot] + [p[1] for p in fwd_pts]))
        out = np.interp(t, ts, ln_fs)                  # log-linéaire entre les points
        if len(ts) >= 2:                               # extrapolation au dernier carry
            slope = (ln_fs[-1] - ln_fs[-2]) / max(ts[-1] - ts[-2], 1e-9)
            beyond = t > ts[-1]
            out[beyond] = ln_fs[-1] + slope * (t[beyond] - ts[-1])
        return np.exp(out)

    sigma = None
    chain = datasource.get_option_chain(sym)
    if not chain.empty and "implied_vol" in chain.columns:
        u = chain[chain["is_usable"]] if "is_usable" in chain.columns else chain
        u = u.dropna(subset=["implied_vol", "log_moneyness", "maturity_years"])
        if not u.empty:
            nearest_t = u.loc[(u["maturity_years"] - maturity).abs().idxmin(), "maturity_years"]
            sl = u[u["maturity_years"] == nearest_t]
            sigma = float(sl.loc[sl["log_moneyness"].abs().idxmin(), "implied_vol"])

    return spot, rate, sigma, forward_curve, fwd_pts


@callback(
    Output("mc-metrics",     "children"),
    Output("mc-greeks",      "children"),
    Output("mc-paths-fig",   "figure"),
    Output("mc-dist-fig",    "figure"),
    Output("mc-ladder-fig",  "figure"),
    Output("mc-ladder-table", "data"),
    Output("mc-conv-fig",    "figure"),
    Input("mc-run",          "n_clicks"),
    Input("selected-symbol", "data"),
    State("mc-months",       "value"),
    State("mc-strike-pct",   "value"),
    State("mc-right",        "value"),
    State("mc-fixing",       "value"),
    State("mc-method",       "value"),
    State("mc-paths",        "value"),
)
def run_mc(_clicks, symbol, months, strike_pct, right, fixing, method, n_paths):
    sym = symbol or "ESTX50"
    empty = go.Figure()
    T = (months or 12) / 12.0

    spot, rate, sigma, fcurve, fwd_pts = _infra_inputs(sym, T)
    if not spot or not sigma:
        alert = [dbc.Col(no_data_alert(sym), width=12)]
        return alert, [], empty, empty, empty, [], empty

    strike = round(spot * (strike_pct or 100) / 100.0, 2)
    n_steps = max(int(T * {"M": 12, "W": 52, "D": 252}[fixing or "W"]), 2)

    res = price_asian_mc(spot, strike, sigma, T, rate, 0.0, right or "C",
                         n_paths=n_paths or 25_000, n_steps=n_steps, seed=42,
                         method=method or "sobol", forward_curve=fcurve,
                         compute_greeks=True)

    ci = 1.96 * res.std_error
    mult = 10 if sym == "ESTX50" else 100
    metrics = [
        dbc.Col(_mb(f"{res.price:,.2f} € ± {ci:,.2f}",
                    "Asiatique arithmétique (MC+CV, IC 95%)", "positive"), width=3),
        dbc.Col(_mb(f"{res.geo_closed_form:,.2f} €", "Géométrique exacte (Kemna-Vorst)"), width=2),
        dbc.Col(_mb(f"{res.european_bs:,.2f} €", "Européenne équivalente (Black)"), width=2),
        dbc.Col(_mb(f"×{res.variance_reduction:,.0f}", "Réduction de variance (CV)", "info"), width=2),
        dbc.Col(_mb(f"S={spot:,.0f} · σ={sigma:.1%} · {'Sobol' if (method or 'sobol')=='sobol' else 'pseudo'}",
                    "Inputs (store · surface IV · parité)"), width=3),
    ]
    greeks = [
        dbc.Col(_mb(f"{res.delta:+.4f}", "Delta (pathwise, exact)"), width=3),
        dbc.Col(_mb(f"{res.gamma:.6f}", "Gamma (re-scaling CRN)"), width=3),
        dbc.Col(_mb(f"{res.vega:,.4f} € / pt", "Vega (bump ±1pt, CRN)"), width=3),
        dbc.Col(_mb(f"{res.theta:,.4f} € / jour" if res.theta is not None else "—",
                    "Theta (bump −1j, CRN)"), width=3),
    ]

    # 1) Éventail + forwards de parité superposés
    fig_paths = go.Figure()
    t_axis = np.linspace(0, T * 365, res.sample_paths.shape[1])
    for p in res.sample_paths[:100]:
        fig_paths.add_trace(go.Scatter(x=t_axis, y=p, mode="lines",
                                       line=dict(width=0.7, color="rgba(9,105,218,0.16)"),
                                       showlegend=False, hoverinfo="skip"))
    fig_paths.add_trace(go.Scatter(x=t_axis, y=res.sample_paths.mean(axis=0),
                                   mode="lines", name="moyenne MC",
                                   line=dict(width=2.5, color="#0a3069")))
    mkt = [(t * 365, f) for t, f in fwd_pts if t <= T * 1.02]
    if mkt:
        fig_paths.add_trace(go.Scatter(
            x=[m[0] for m in mkt], y=[m[1] for m in mkt],
            mode="markers", name="forwards de parité (marché)",
            marker=dict(symbol="diamond", size=10, color="#cf222e",
                        line=dict(width=1, color="#ffffff")),
        ))
    fig_paths.add_hline(y=strike, line_dash="dash", line_color="#cf222e",
                        annotation_text=f"K = {strike:,.0f}")
    fig_paths.update_layout(height=340, margin=dict(l=45, r=15, t=8, b=35),
                            xaxis_title="Jours", yaxis_title="Niveau simulé",
                            legend=dict(orientation="h", y=1.1, font=dict(size=10)),
                            **_LAYOUT)

    # 2) Distribution de la moyenne
    fig_dist = go.Figure()
    fig_dist.add_trace(go.Histogram(x=res.averages, nbinsx=80, marker_color="#0969da",
                                    opacity=0.8))
    fig_dist.add_vline(x=strike, line_dash="dash", line_color="#cf222e", annotation_text="K")
    fig_dist.add_vline(x=float(np.mean(res.averages)), line_dash="dot",
                       line_color="#1a7f37", annotation_text="E[A]")
    fig_dist.update_layout(height=340, margin=dict(l=45, r=15, t=8, b=35),
                           xaxis_title="Moyenne arithmétique du chemin",
                           yaxis_title="Fréquence", showlegend=False, **_LAYOUT)

    # 3) Strike ladder (mêmes chemins)
    strikes = [round(spot * k / 100.0, 2) for k in range(80, 121, 5)]
    ladder = strike_ladder(res, strikes)
    fig_lad = go.Figure()
    fig_lad.add_trace(go.Scatter(x=[r["strike"] for r in ladder],
                                 y=[r["asian_mc"] for r in ladder],
                                 name="asiatique MC+CV", mode="lines+markers",
                                 line=dict(color="#0969da", width=2)))
    fig_lad.add_trace(go.Scatter(x=[r["strike"] for r in ladder],
                                 y=[r["geo_cf"] for r in ladder],
                                 name="géométrique exacte", mode="lines",
                                 line=dict(color="#57606a", dash="dot")))
    fig_lad.add_trace(go.Scatter(x=[r["strike"] for r in ladder],
                                 y=[r["european_bs"] for r in ladder],
                                 name="européenne Black", mode="lines",
                                 line=dict(color="#bc4c00", dash="dash")))
    fig_lad.add_vline(x=spot, line_dash="dot", line_color="#1a7f37", annotation_text="S₀")
    fig_lad.update_layout(height=320, margin=dict(l=45, r=15, t=8, b=35),
                          xaxis_title="Strike", yaxis_title="Prix (€)",
                          legend=dict(orientation="h", y=1.12, font=dict(size=10)),
                          **_LAYOUT)
    ladder_rows = [{"strike": r["strike"], "asian_mc": r["asian_mc"],
                    "ci": 1.96 * r["se"], "geo_cf": r["geo_cf"],
                    "european_bs": r["european_bs"]} for r in ladder]

    # 4) Convergence
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

    return metrics, greeks, fig_paths, fig_dist, fig_lad, ladder_rows, fig_conv


@callback(
    Output("mc-hedge-fig",   "figure"),
    Output("mc-hedge-stats", "children"),
    Input("mc-run",          "n_clicks"),
    Input("selected-symbol", "data"),
    State("mc-months",       "value"),
    State("mc-strike-pct",   "value"),
    State("mc-right",        "value"),
)
def run_hedge(_clicks, symbol, months, strike_pct, right):
    from src.pricing.monte_carlo import delta_hedge_pnl
    sym = symbol or "ESTX50"
    T = (months or 12) / 12.0
    spot, rate, sigma, fcurve, _ = _infra_inputs(sym, T)
    fig = go.Figure()
    if not spot or not sigma:
        return fig, None
    strike = round(spot * (strike_pct or 100) / 100.0, 2)
    # Carry constant équivalent (pour le hedge, le carry moyen suffit)
    b = math.log(float(fcurve(np.array([T]))[0]) / spot) / T

    configs = [("Sans couverture", 0, "#cf222e"),
               ("Hedge hebdomadaire", 5, "#bc4c00"),
               ("Hedge quotidien", 1, "#1a7f37")]
    stats = []
    for name, every, color in configs:
        pnl = delta_hedge_pnl(spot, strike, sigma, T, rate, b, right or "C",
                              n_paths=4000, rebalance_steps=every, seed=42)
        fig.add_trace(go.Histogram(x=pnl, nbinsx=120, name=name, opacity=0.6,
                                   marker_color=color))
        stats.append((name, float(pnl.mean()), float(pnl.std(ddof=1))))
    fig.add_vline(x=0, line_dash="dash", line_color="#57606a")
    fig.update_layout(height=300, margin=dict(l=45, r=15, t=8, b=35), barmode="overlay",
                      xaxis_title="P&L du vendeur hedgé (€, actualisé)",
                      yaxis_title="Fréquence",
                      legend=dict(orientation="h", y=1.12, font=dict(size=10)),
                      **_LAYOUT)
    badges = [dbc.Badge(f"{n} : moy {m:+,.1f} € · σ {s:,.1f} €",
                        color=("danger" if "Sans" in n else
                               "warning" if "hebdo" in n else "success"),
                        className="me-2") for n, m, s in stats]
    return fig, badges


def _mb(value, label, css=""):
    return html.Div([
        html.Div(str(value), className="metric-value", style={"fontSize": "0.95rem"}),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
