"""Page bonus — Analyseur de risques de stratégies (réplique de l'outil de référence
du cours, validée au centième dans tests/test_strategy_risk.py).
Onglets : Stratégies (agrégation + corrélations) · Mensuel · Journalier · Intervalle
(IC du Sharpe, méthode delta / Lo 2002)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math

import dash
from dash import html, dcc, dash_table, callback, Input, Output, State, no_update
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import numpy as np

from src.risk.strategy_risk import (portfolio_moments, symmetrize_correlation,
                                    horizon_stats, sharpe_confidence_interval)

dash.register_page(__name__, path="/strategy-risk", name="Risque de stratégie (bonus)")

_LAYOUT = dict(template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
               font=dict(size=11))

_DEFAULT_STRATS = [
    {"name": "Stratégie A (réf. outil)", "mu": 10.0, "sigma": 20.0, "weight": 0.5},
    {"name": "Dispersion ESTX50",        "mu": 6.0,  "sigma": 8.0,  "weight": 0.5},
]

layout = dbc.Container([
    html.Div([
        html.H2("Bonus — Analyseur de risques de stratégies"),
        html.P("Réplique de l'outil de référence du cours, branchée sur notre stack : "
               "agrégation pondérée des stratégies (la structure de covariance est "
               "CONSERVÉE au changement d'horizon — Eq.23), lois exactes des périodes "
               "rouges (binomiale + séries consécutives par récurrence dynamique), et "
               "intervalle de confiance asymptotique du Sharpe (méthode delta, Lo 2002). "
               "Chaque chiffre est validé au centième contre l'outil original "
               "(tests/test_strategy_risk.py)."),
    ], className="page-header"),

    dcc.Store(id="sr-portfolio"),

    dbc.Tabs([
        # ── Onglet 1 : Stratégies ─────────────────────────────────────────
        dbc.Tab(label="Stratégies", tab_id="sr-tab-strats", children=[
            dbc.Row([
                dbc.Col(dbc.Card([
                    dbc.CardHeader("Stratégies (μ, σ annuels en %, poids)"),
                    dbc.CardBody([
                        dash_table.DataTable(
                            id="sr-strats-table",
                            columns=[
                                {"name": "Stratégie", "id": "name"},
                                {"name": "μ annuel (%)", "id": "mu", "type": "numeric"},
                                {"name": "σ annuel (%)", "id": "sigma", "type": "numeric"},
                                {"name": "Poids", "id": "weight", "type": "numeric"},
                            ],
                            data=list(_DEFAULT_STRATS),
                            editable=True, row_deletable=True,
                            style_header={"textTransform": "none"},
                            style_cell={"textAlign": "center", "padding": "4px",
                                        "fontSize": "12px"},
                        ),
                        dbc.Button("Ajouter une stratégie", id="sr-add-strat",
                                   size="sm", color="secondary", className="mt-2 me-2"),
                        dbc.Button("Calculer le portefeuille", id="sr-compute",
                                   size="sm", color="primary", className="mt-2"),
                    ]),
                ], className="card h-100"), width=6),
                dbc.Col(dbc.Card([
                    dbc.CardHeader("Matrice de corrélation (éditable — symétrisée au calcul)"),
                    dbc.CardBody([
                        dash_table.DataTable(
                            id="sr-corr-table", editable=True,
                            style_header={"textTransform": "none"},
                            style_cell={"textAlign": "center", "padding": "4px",
                                        "fontSize": "12px"},
                        ),
                        html.Small("Diagonale forcée à 1 ; la matrice est symétrisée et sa "
                                   "semi-définie-positivité vérifiée (un triangle de "
                                   "corrélations impossible est signalé).",
                                   className="text-muted d-block mt-2"),
                    ]),
                ], className="card h-100"), width=6),
            ], className="g-2 mt-1 mb-3"),
            dbc.Row(id="sr-pf-metrics", className="g-3"),
        ]),

        # ── Onglets 2 & 3 : horizon mensuel / journalier ──────────────────
        dbc.Tab(label="Mensuel", tab_id="sr-tab-month", children=[
            html.Div(id="sr-month-content", className="mt-2"),
        ]),
        dbc.Tab(label="Journalier", tab_id="sr-tab-day", children=[
            html.Div(id="sr-day-content", className="mt-2"),
        ]),

        # ── Onglet 4 : IC du Sharpe ───────────────────────────────────────
        dbc.Tab(label="Intervalle", tab_id="sr-tab-ci", children=[
            dbc.Row([
                dbc.Col([
                    html.Label("Sharpe annuel estimé (Ŝ)", className="text-muted small"),
                    dbc.Input(id="sr-ci-sharpe", type="number", value=1.5, step=0.1,
                              className="dash-input"),
                ], width=2),
                dbc.Col([
                    html.Label("Jours d'observation (T)", className="text-muted small"),
                    dbc.Input(id="sr-ci-days", type="number", value=252, step=1,
                              className="dash-input"),
                ], width=2),
                dbc.Col([
                    html.Label("Niveau de confiance", className="text-muted small"),
                    dcc.Dropdown(id="sr-ci-conf",
                                 options=[{"label": f"{c} %", "value": c / 100}
                                          for c in (90, 95, 99)],
                                 value=0.95, clearable=False,
                                 style={"fontSize": "12px"}),
                ], width=2),
            ], className="g-2 mt-2 mb-2"),
            dbc.Row(id="sr-ci-metrics", className="g-3 mb-3"),
            dbc.Card([
                dbc.CardHeader("L'intervalle se resserre en 1/√T — combien d'années pour "
                               "qu'un track record devienne significatif ?"),
                dbc.CardBody(dcc.Graph(id="sr-ci-fig", config={"displayModeBar": False}),
                             className="p-2"),
            ], className="card"),
        ]),
    ], active_tab="sr-tab-strats"),
], fluid=True)


# ── Onglet Stratégies ──────────────────────────────────────────────────────

@callback(
    Output("sr-strats-table", "data"),
    Input("sr-add-strat", "n_clicks"),
    State("sr-strats-table", "data"),
    prevent_initial_call=True,
)
def add_strategy(_n, rows):
    rows = rows or []
    rows.append({"name": f"Stratégie {chr(65 + len(rows))}", "mu": 8.0,
                 "sigma": 15.0, "weight": 0.0})
    return rows


@callback(
    Output("sr-corr-table", "columns"),
    Output("sr-corr-table", "data"),
    Input("sr-strats-table", "data"),
    State("sr-corr-table", "data"),
)
def rebuild_corr(strats, old):
    names = [r.get("name", f"S{i}") for i, r in enumerate(strats or [])]
    cols = [{"name": "", "id": "_row", "editable": False}] + \
           [{"name": n, "id": n, "type": "numeric"} for n in names]
    old_map = {}
    for r in old or []:
        for k, v in r.items():
            if k != "_row":
                old_map[(r.get("_row"), k)] = v
    data = []
    for ni in names:
        row = {"_row": ni}
        for nj in names:
            row[nj] = 1.0 if ni == nj else old_map.get((ni, nj), 0.0)
        data.append(row)
    return cols, data


@callback(
    Output("sr-portfolio",  "data"),
    Output("sr-pf-metrics", "children"),
    Input("sr-compute",     "n_clicks"),
    Input("sr-strats-table", "data"),
    Input("sr-corr-table",  "data"),
)
def compute_portfolio(_n, strats, corr_rows):
    strats = [r for r in (strats or []) if r.get("sigma") not in (None, "")]
    if not strats:
        return None, [dbc.Col(dbc.Alert("Ajoute au moins une stratégie.",
                                        color="warning"), width=12)]
    names = [r["name"] for r in strats]
    mus = [float(r.get("mu") or 0) / 100 for r in strats]
    sigmas = [float(r.get("sigma") or 0) / 100 for r in strats]
    weights = [float(r.get("weight") or 0) for r in strats]
    n = len(names)
    corr = np.eye(n)
    by_row = {r.get("_row"): r for r in (corr_rows or [])}
    for i, ni in enumerate(names):
        for j, nj in enumerate(names):
            v = by_row.get(ni, {}).get(nj)
            if v not in (None, ""):
                corr[i, j] = float(v)
    corr, psd_ok, eig_min = symmetrize_correlation(corr)

    mu_p, sig_p = portfolio_moments(mus, sigmas, weights, corr)
    sharpe = mu_p / sig_p if sig_p > 0 else float("nan")
    w_sum = sum(weights)

    metrics = [
        dbc.Col(_mb(f"{mu_p:.2%}", "μ annuel du portefeuille", "positive"), width=2),
        dbc.Col(_mb(f"{sig_p:.2%}", "σ annuel du portefeuille"), width=2),
        dbc.Col(_mb(f"{sharpe:.2f}", "Sharpe (hors taux)", "info"), width=2),
        dbc.Col(_mb(f"{w_sum:.2f}", "Σ poids" + ("" if abs(w_sum - 1) < 1e-9 else " ≠ 1 !"),
                    "" if abs(w_sum - 1) < 1e-9 else "warning"), width=2),
        dbc.Col(_mb("OK" if psd_ok else f"NON (λmin={eig_min:.2f})",
                    "Matrice de corrélation cohérente (PSD)",
                    "positive" if psd_ok else "negative"), width=4),
    ]
    return {"mu": mu_p, "sigma": sig_p}, metrics


# ── Onglets Mensuel / Journalier ───────────────────────────────────────────

def _horizon_view(pf, n_periods, label, unit):
    if not pf:
        return dbc.Alert("Calcule d'abord le portefeuille (onglet Stratégies).",
                         color="info", className="mt-2")
    st = horizon_stats(pf["mu"], pf["sigma"], n_periods)

    metrics = dbc.Row([
        dbc.Col(_mb(f"{st['mu_h']:.3%}", f"Espérance {label}", "positive"), width=2),
        dbc.Col(_mb(f"{st['sigma_h']:.3%}", f"Volatilité {label}"), width=2),
        dbc.Col(_mb(f"{st['p_red']:.2%}", f"P({unit} négatif)", "warning"), width=2),
        dbc.Col(_mb(f"{st['p_at_least_one']:.2%}", f"P(≥1 {unit} rouge / an)",
                    "negative"), width=3),
        dbc.Col(_mb(f"{st['mean_reds']:.3g} ± {st['std_reds']:.3g}",
                    f"Nb de {unit}s rouges / an (moy ± σ)"), width=3),
    ], className="g-3 mb-3")

    ks = list(range(n_periods + 1))
    fig_pmf = go.Figure(go.Bar(x=ks, y=[p * 100 for p in st["pmf"]],
                               marker_color="#0969da"))
    fig_pmf.update_layout(height=280, margin=dict(l=45, r=15, t=8, b=35),
                          xaxis_title=f"Nombre de {unit}s rouges dans l'année (loi exacte)",
                          yaxis_title="Probabilité (%)", **_LAYOUT)
    if n_periods > 30:
        lo = max(int(st["mean_reds"] - 4 * st["std_reds"]), 0)
        hi = int(st["mean_reds"] + 4 * st["std_reds"])
        fig_pmf.update_xaxes(range=[lo, hi])

    runs = st["runs"]
    fig_runs = go.Figure(go.Bar(x=[f"≥{x}" for x, _ in runs],
                                y=[p * 100 for _, p in runs],
                                marker_color="#bc4c00"))
    fig_runs.update_layout(height=280, margin=dict(l=45, r=15, t=8, b=35),
                           xaxis_title=f"Série de {unit}s rouges CONSÉCUTIFS "
                                       "(récurrence dynamique exacte)",
                           yaxis_title="Probabilité (%)", **_LAYOUT)

    return html.Div([
        metrics,
        dbc.Row([
            dbc.Col(dbc.Card([dbc.CardHeader(f"Loi du nombre de {unit}s rouges — "
                                             f"Binomiale({n_periods}, p)"),
                              dbc.CardBody(dcc.Graph(figure=fig_pmf,
                                                     config={"displayModeBar": False}),
                                           className="p-2")],
                             className="card h-100"), width=6),
            dbc.Col(dbc.Card([dbc.CardHeader("Probabilité d'au moins X rouges consécutifs"),
                              dbc.CardBody(dcc.Graph(figure=fig_runs,
                                                     config={"displayModeBar": False}),
                                           className="p-2")],
                             className="card h-100"), width=6),
        ], className="g-2"),
        html.Small("Hypothèses : rendements gaussiens i.i.d. (μ/n, σ/√n), périodes "
                   "indépendantes ; la structure de covariance du portefeuille est "
                   "transmise à la fréquence — on ne mensualise pas chaque stratégie "
                   "isolément.", className="text-muted d-block mt-2"),
    ])


@callback(Output("sr-month-content", "children"), Input("sr-portfolio", "data"))
def month_view(pf):
    return _horizon_view(pf, 12, "mensuelle", "mois")


@callback(Output("sr-day-content", "children"), Input("sr-portfolio", "data"))
def day_view(pf):
    return _horizon_view(pf, 252, "journalière", "jour")


# ── Onglet Intervalle ──────────────────────────────────────────────────────

@callback(
    Output("sr-ci-metrics", "children"),
    Output("sr-ci-fig",     "figure"),
    Input("sr-ci-sharpe",   "value"),
    Input("sr-ci-days",     "value"),
    Input("sr-ci-conf",     "value"),
)
def ci_view(sharpe, days, conf):
    fig = go.Figure()
    if not sharpe or not days:
        return [], fig
    r = sharpe_confidence_interval(float(sharpe), int(days), float(conf or 0.95))
    metrics = [
        dbc.Col(_mb(f"{r['sharpe']:.3f}", "Sharpe estimé"), width=2),
        dbc.Col(_mb(f"[ {r['lo']:.3f} ; {r['hi']:.3f} ]",
                    f"IC asymptotique à {int(r['confidence']*100)} %", "info"), width=4),
        dbc.Col(_mb("OUI" if r["significant"] else "NON",
                    "Significativement > 0 ?",
                    "positive" if r["significant"] else "negative"), width=2),
        dbc.Col(_mb(f"{r['t_days_for_significance']:,} j "
                    f"(≈ {r['t_days_for_significance']/252:.1f} ans)"
                    if r["t_days_for_significance"] else "—",
                    "Track record requis pour la significativité", "warning"), width=4),
    ]
    # Courbe : bornes de l'IC en fonction de T
    ts = np.unique(np.geomspace(21, max(int(days) * 4, 2520), 120).astype(int))
    los, his = [], []
    for t in ts:
        c = sharpe_confidence_interval(float(sharpe), int(t), float(conf or 0.95))
        los.append(c["lo"]); his.append(c["hi"])
    fig.add_trace(go.Scatter(x=np.concatenate([ts, ts[::-1]]),
                             y=np.concatenate([his, los[::-1]]),
                             fill="toself", fillcolor="rgba(9,105,218,0.10)",
                             line=dict(width=0), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=ts, y=his, name="borne haute",
                             line=dict(color="#0969da", width=1.5)))
    fig.add_trace(go.Scatter(x=ts, y=los, name="borne basse",
                             line=dict(color="#0969da", width=1.5)))
    fig.add_hline(y=float(sharpe), line_dash="dot", line_color="#1a7f37",
                  annotation_text="Ŝ")
    fig.add_hline(y=0, line_color="#cf222e", line_width=1)
    if r["t_days_for_significance"]:
        fig.add_vline(x=r["t_days_for_significance"], line_dash="dash",
                      line_color="#9a6700",
                      annotation_text=f"significatif dès T = "
                                      f"{r['t_days_for_significance']:,} j")
    fig.add_vline(x=int(days), line_dash="dot", line_color="#57606a",
                  annotation_text="T actuel")
    fig.update_layout(height=320, margin=dict(l=45, r=15, t=8, b=35),
                      xaxis=dict(type="log", title="Jours d'observation T (log)"),
                      yaxis_title="Sharpe annualisé",
                      legend=dict(orientation="h", y=1.12, font=dict(size=10)),
                      **_LAYOUT)
    return metrics, fig


def _mb(value, label, css=""):
    return html.Div([
        html.Div(str(value), className="metric-value", style={"fontSize": "1.0rem"}),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
