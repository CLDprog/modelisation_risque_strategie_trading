"""Page 0 — Market Monitor EURO STOXX 50 : cross-section de l'univers (51 sous-jacents),
matrice de vol ATM, moniteur de dispersion (Eq.23), table de l'univers."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dash
from dash import html, dcc, dash_table, callback, Input, Output, State, no_update
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import numpy as np
import pandas as pd

from src.data.source import datasource
from src.risk.dispersion import _atm_iv_by_tenor

dash.register_page(__name__, path="/", name="Vue d'ensemble")

_TENORS = [30, 91, 182, 273, 365, 548, 730]

_TABLE_COLUMNS = [
    {"name": "Symbole",     "id": "symbol"},
    {"name": "Société",     "id": "description"},
    {"name": "Type",        "id": "sec_type"},
    {"name": "Bourse",      "id": "exchange"},
    {"name": "Options",     "id": "option_exchange"},
    {"name": "Spot €",      "id": "spot"},
    {"name": "Usable",      "id": "n_usable"},
    {"name": "Quotes",      "id": "n_quotes"},
    {"name": "Pts IV",      "id": "n_options"},
    {"name": "Maturités",   "id": "n_expiries"},
    {"name": "IV moy.",     "id": "iv_mean"},
    {"name": "QC ⚠",        "id": "qc_warn"},
    {"name": "QC ✗",        "id": "qc_fail"},
    {"name": "MAJ",         "id": "updated"},
]

_FIG_LAYOUT = dict(template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
                   font=dict(size=11))


def _card(title, body):
    return dbc.Card([dbc.CardHeader(title), dbc.CardBody(body, className="p-2")],
                    className="card h-100")


layout = dbc.Container([
    html.Div([
        html.H2("Market Monitor — EURO STOXX 50"),
        html.P("Cross-section de l'indice et de ses 50 composantes : vol implicite, "
               "term structures, dispersion et qualité de données. "
               "Cliquer une ligne de la table (ou une colonne de la matrice) sélectionne "
               "le produit sur toutes les pages."),
    ], className="page-header"),

    dcc.Interval(id="ov-interval", interval=30000, n_intervals=0),

    dbc.Row(id="ov-metrics", className="g-3 mb-3"),

    dbc.Tabs([
        dbc.Tab(label="Vol monitor", tab_id="ov-tab-vol", children=[
            dbc.Row([
                dbc.Col(_card("Matrice de vol ATM — sous-jacents × tenors (cliquer = sélectionner)",
                              dcc.Graph(id="ov-vol-matrix", config={"displayModeBar": False})),
                        width=12),
            ], className="g-2 mt-1"),
            dbc.Row([
                dbc.Col(_card("Niveau vs pente de term structure (taille = nb d'options)",
                              dcc.Graph(id="ov-scatter", config={"displayModeBar": False})),
                        width=6),
                dbc.Col(_card("Dispersion indice vs composantes — corrélation implicite (Eq.23)",
                              dcc.Graph(id="ov-dispersion", config={"displayModeBar": False})),
                        width=6),
            ], className="g-2 mt-1"),
        ]),
        dbc.Tab(label="Couverture de collecte", tab_id="ov-tab-cov", children=[
            dbc.Row([
                dbc.Col(_card("Options usable par sous-jacent (vert ≥ 40 · bleu > 0 · rouge = 0)",
                              dcc.Graph(id="ov-bars", config={"displayModeBar": False})),
                        width=12),
            ], className="g-2 mt-1"),
        ]),
        dbc.Tab(label="Table de l'univers", tab_id="ov-tab-table", children=[
            dbc.Row([dbc.Col(_card("Univers — 51 sous-jacents", dash_table.DataTable(
                id="ov-table",
                columns=_TABLE_COLUMNS,
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "center", "padding": "4px", "fontSize": "12px"},
                style_cell_conditional=[
                    {"if": {"column_id": "description"}, "textAlign": "left"},
                ],
                style_data_conditional=[
                    {"if": {"filter_query": "{n_usable} = 0"},
                     "color": "#57606a", "backgroundColor": "#ffebe9"},
                    {"if": {"filter_query": "{qc_fail} > 0", "column_id": "qc_fail"},
                     "color": "#cf222e", "fontWeight": "bold"},
                    {"if": {"filter_query": "{qc_warn} > 0", "column_id": "qc_warn"},
                     "color": "#9a6700"},
                    {"if": {"state": "active"},
                     "backgroundColor": "#ddf4ff", "border": "1px solid #0969da"},
                ],
                sort_action="native", filter_action="native", page_size=51,
            )), width=12)], className="g-2 mt-1"),
        ]),
    ], active_tab="ov-tab-vol"),
], fluid=True)


def _atm_matrix(iv: pd.DataFrame) -> pd.DataFrame:
    """IV ATM par (symbole × tenor cible) — réutilise la sélection ATM d'Eq.23."""
    rows = {}
    if iv.empty or "underlying_symbol" not in iv.columns:
        return pd.DataFrame()
    for sym, grp in iv.groupby("underlying_symbol"):
        ivs = _atm_iv_by_tenor(grp, _TENORS)
        if ivs:
            rows[sym] = ivs
    return pd.DataFrame(rows).T  # index = symbole, colonnes = tenor


@callback(
    Output("ov-metrics",    "children"),
    Output("ov-vol-matrix", "figure"),
    Output("ov-scatter",    "figure"),
    Output("ov-dispersion", "figure"),
    Output("ov-bars",       "figure"),
    Output("ov-table",      "data"),
    Input("ov-interval",    "n_intervals"),
)
def refresh_overview(_):
    ov = datasource.get_universe_overview()
    status = datasource.collector_status()
    empty = go.Figure()

    if ov.empty:
        alert = [dbc.Col(dbc.Alert("Univers vide — vérifier configs/universe.yaml.",
                                   color="warning"), width=12)]
        return alert, empty, empty, empty, empty, []

    # ── Bandeau de métriques ──────────────────────────────────────────
    n_total     = len(ov)
    n_collected = int((ov["n_usable"] > 0).sum())
    tot_usable  = int(ov["n_usable"].sum())
    tot_quotes  = int(ov["n_quotes"].sum())
    last_cycle  = (status.get("last_cycle") or "—")[11:19] or "—"
    cycle_secs  = status.get("last_cycle_secs")
    src         = datasource.get_data_source_label()

    disp = datasource.get_dispersion()
    rho = (float(disp["implied_correlation"].mean())
           if not disp.empty and "implied_correlation" in disp.columns else None)

    metrics = [
        dbc.Col(_mb(f"{n_collected}/{n_total}", "Sous-jacents collectés",
                    "positive" if n_collected >= n_total - 1 else "warning"), width=2),
        dbc.Col(_mb(f"{tot_usable:,}",  "Options usable",   "positive"), width=2),
        dbc.Col(_mb(f"{tot_quotes:,}",  "Quotes collectées"),            width=2),
        dbc.Col(_mb(f"{rho:.0%}" if rho is not None else "—",
                    "Corrélation implicite ρ̄", "info"),                  width=2),
        dbc.Col(_mb(f"{last_cycle} · {cycle_secs:.0f}s" if cycle_secs else last_cycle,
                    "Dernier cycle (UTC · durée)"),                       width=2),
        dbc.Col(_mb(src, "Source",
                    "positive" if src == "Live (collecteur)" else "info"), width=2),
    ]

    # ── Matrice de vol ATM (symboles × tenors) ────────────────────────
    iv = datasource._read_analytics("iv_points")
    mat = _atm_matrix(iv)
    fig_mat = go.Figure()
    if not mat.empty:
        mat = mat.sort_index()
        z = (mat * 100).round(1)
        fig_mat.add_trace(go.Heatmap(
            z=z.T.values, x=list(z.index), y=[f"{t}j" for t in z.columns],
            colorscale="YlOrRd", colorbar=dict(title="IV %", tickfont=dict(size=10)),
            hovertemplate="%{x} · %{y}<br>IV ATM = %{z:.1f}%<extra></extra>",
            xgap=1, ygap=1,
        ))
    fig_mat.update_layout(height=260, margin=dict(l=45, r=10, t=8, b=55),
                          xaxis=dict(tickangle=-60, tickfont=dict(size=9)),
                          yaxis=dict(autorange="reversed"), **_FIG_LAYOUT)

    # ── Scatter niveau (1m) vs pente (12m − 1m) ───────────────────────
    fig_sc = go.Figure()
    if not mat.empty and 30 in mat.columns and 365 in mat.columns:
        pts = mat.dropna(subset=[30, 365]).join(
            ov.set_index("symbol")[["description", "n_usable", "qc_fail"]], how="left")
        colors = ["#cf222e" if f else "#0969da" for f in pts["qc_fail"].fillna(0)]
        fig_sc.add_trace(go.Scatter(
            x=pts[30] * 100, y=(pts[365] - pts[30]) * 100,
            mode="markers+text", text=list(pts.index), textposition="top center",
            textfont=dict(size=8, color="#57606a"),
            marker=dict(size=np.clip(pts["n_usable"].fillna(10) / 4, 4, 16),
                        color=colors, opacity=0.75,
                        line=dict(width=0.5, color="#ffffff")),
            customdata=pts["description"],
            hovertemplate="<b>%{text}</b> — %{customdata}<br>IV 1m = %{x:.1f}% · "
                          "pente 12m−1m = %{y:.1f} pts<extra></extra>",
        ))
        fig_sc.add_hline(y=0, line_dash="dot", line_color="#9a6700",
                         annotation_text="backwardation ↓ / contango ↑")
    fig_sc.update_layout(height=320, margin=dict(l=45, r=10, t=8, b=40),
                         xaxis_title="IV ATM 1 mois (%)",
                         yaxis_title="Pente 12m − 1m (pts de vol)", **_FIG_LAYOUT)

    # ── Dispersion : ρ̄ + IV indice vs panier par tenor ────────────────
    fig_disp = make_subplots(specs=[[{"secondary_y": True}]])
    if not disp.empty:
        d = disp.sort_values("tenor_days")
        x = [f"{t}j" for t in d["tenor_days"]]
        fig_disp.add_trace(go.Bar(
            x=x, y=d["implied_correlation"], name="ρ̄ implicite",
            marker_color="#0969da", opacity=0.75,
            hovertemplate="ρ̄ = %{y:.2f}<extra></extra>"), secondary_y=False)
        fig_disp.add_trace(go.Scatter(
            x=x, y=d["index_iv"] * 100, name="IV indice",
            mode="lines+markers", line=dict(color="#cf222e", width=2)), secondary_y=True)
        fig_disp.add_trace(go.Scatter(
            x=x, y=d["basket_avg_iv"] * 100, name="IV panier (moy. pondérée)",
            mode="lines+markers", line=dict(color="#1a7f37", width=2, dash="dash")),
            secondary_y=True)
        fig_disp.update_yaxes(title_text="ρ̄", range=[0, 1], secondary_y=False)
        fig_disp.update_yaxes(title_text="IV (%)", secondary_y=True)
    fig_disp.update_layout(height=320, margin=dict(l=45, r=45, t=8, b=30),
                           legend=dict(orientation="h", y=1.12, font=dict(size=10)),
                           **_FIG_LAYOUT)

    # ── Couverture : bar chart usable ─────────────────────────────────
    bars = ov.sort_values("n_usable", ascending=False)
    fig_bars = go.Figure(go.Bar(
        x=bars["symbol"], y=bars["n_usable"],
        marker_color=["#cf222e" if v == 0 else "#1a7f37" if v >= 40 else "#0969da"
                      for v in bars["n_usable"]],
        hovertext=bars["description"],
    ))
    fig_bars.update_layout(height=300, margin=dict(l=40, r=20, t=10, b=60),
                           xaxis=dict(tickangle=-60, tickfont=dict(size=9)),
                           yaxis_title="Options usable", **_FIG_LAYOUT)

    # ── Table formatée ────────────────────────────────────────────────
    tbl = ov.copy()
    tbl["spot"]    = tbl["spot"].map(lambda v: f"{v:,.2f}" if pd.notna(v) else "—")
    tbl["iv_mean"] = tbl["iv_mean"].map(lambda v: f"{v:.1%}" if pd.notna(v) else "—")
    tbl["updated"] = tbl["updated"].map(lambda v: str(v)[11:19] if v else "—")

    return metrics, fig_mat, fig_sc, fig_disp, fig_bars, tbl.to_dict("records")


@callback(
    Output("symbol-selector", "value"),
    Input("ov-table",      "active_cell"),
    Input("ov-vol-matrix", "clickData"),
    State("ov-table",      "derived_viewport_data"),
    prevent_initial_call=True,
)
def select_symbol(active_cell, matrix_click, viewport):
    """Clic sur une ligne de table OU une colonne de la matrice → sélectionne le produit."""
    try:
        trig = dash.ctx.triggered_id
    except Exception:  # hors contexte de callback (tests unitaires)
        trig = "ov-table" if active_cell else "ov-vol-matrix"
    if trig == "ov-vol-matrix" and matrix_click:
        try:
            return matrix_click["points"][0]["x"]
        except (KeyError, IndexError):
            return no_update
    if trig == "ov-table" and active_cell and viewport:
        try:
            return viewport[active_cell["row"]]["symbol"]
        except (IndexError, KeyError):
            return no_update
    return no_update


def _mb(value, label, css=""):
    from src.utils.fmt import fr_num
    value = fr_num(value)
    return html.Div([
        html.Div(str(value), className="metric-value"),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
