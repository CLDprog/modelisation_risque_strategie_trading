"""Page 4 — Forward & Carry Engine, symbol-aware."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dash
from dash import html, dcc, dash_table, callback, Input, Output
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

from src.data.source import datasource
from src.data import no_data_alert

dash.register_page(__name__, path="/forward", name="Forward & Carry")

layout = dbc.Container([
    html.Div([
        html.H2("Forward & Carry Engine"),
        html.P("Reconstruction du prix forward F(T) par maturité via la parité put-call."),
    ], className="page-header"),

    dcc.Interval(id="fwd-interval", interval=30000, n_intervals=0),

    dbc.Row(id="fwd-metrics", className="g-3 mb-4"),

    dbc.Card([
        dbc.CardHeader("Formules utilisées"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.P("Eq. 2 — Forward par parité (par strike) :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown("$$F_i = K + e^{rT}(C_{mid} - P_{mid})$$", mathjax=True),
                             className="formula-box"),
                ], width=6),
                dbc.Col([
                    html.P("Eq. 4 — Moyenne pondérée des forwards candidats :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$F(T) = \frac{\sum w_i F_i}{\sum w_i}$$", mathjax=True),
                             className="formula-box"),
                ], width=6),
            ]),
            dbc.Row([
                dbc.Col([
                    html.P("Eq. 5 — Carry implicite :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$q = -\frac{\ln(F/S) - rT}{T}$$", mathjax=True),
                             className="formula-box"),
                ], width=6),
                dbc.Col([
                    html.P("Eq. 24 — Z-score robuste (MAD) :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$z_i = \frac{F_i - \text{med}(F)}{1.4826 \cdot \text{MAD}(F)}$$", mathjax=True),
                             className="formula-box"),
                ], width=6),
            ], className="mt-3"),
        ]),
    ], className="card mb-4"),

    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Courbe Forward F(T)"),
            dbc.CardBody(dcc.Graph(id="fwd-graph", config={"displayModeBar": False}),
                         className="p-2"),
        ], className="card h-100"), width=6),
        dbc.Col(dbc.Card([
            dbc.CardHeader("Score de confiance"),
            dbc.CardBody(dcc.Graph(id="fwd-conf-graph", config={"displayModeBar": False}),
                         className="p-2"),
        ], className="card h-100"), width=3),
        dbc.Col(dbc.Card([
            dbc.CardHeader("Carry implicite q(T)"),
            dbc.CardBody(dcc.Graph(id="fwd-carry-graph", config={"displayModeBar": False}),
                         className="p-2"),
        ], className="card h-100"), width=3),
    ], className="g-2 mb-4"),

    dbc.Card([
        dbc.CardHeader("Résultats détaillés par maturité"),
        dbc.CardBody(dash_table.DataTable(
            id="fwd-table",
            columns=[
                {"name": "Expiry",        "id": "expiry"},
                {"name": "Jours",         "id": "days_to_expiry"},
                {"name": "T (années)",    "id": "maturity_years"},
                {"name": "Forward F(T)",  "id": "chosen_forward"},
                {"name": "Carry q",       "id": "implied_carry"},
                {"name": "Confiance",     "id": "confidence_score"},
                {"name": "Qualité",       "id": "quality_flag"},
            ],
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "padding": "8px"},
            sort_action="native",
        )),
    ], className="card mb-4"),

    dbc.Card([
        dbc.CardHeader("Diagnostics — candidats forward par strike (y compris rejetés)"),
        dbc.CardBody([
            html.P("Chaque strike de chaque maturité produit un candidat F_i = K + e^{rT}(C−P). "
                   "Les candidats hors-z-score (MAD) ou illiquides sont rejetés (used = false).",
                   className="text-muted small"),
            dash_table.DataTable(
                id="fwd-diag-table",
                columns=[
                    {"name": "Expiry",     "id": "expiry"},
                    {"name": "Strike",     "id": "strike"},
                    {"name": "Call mid",   "id": "call_mid"},
                    {"name": "Put mid",    "id": "put_mid"},
                    {"name": "F candidat", "id": "forward_estimate"},
                    {"name": "Poids",      "id": "weight"},
                    {"name": "Qualité",    "id": "quality_flag"},
                    {"name": "Utilisé",    "id": "used"},
                    {"name": "F retenu",   "id": "chosen_forward"},
                ],
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "center", "padding": "6px", "fontSize": "13px"},
                style_data_conditional=[
                    {"if": {"filter_query": '{used} contains "false"'},
                     "color": "#cf222e"},
                ],
                sort_action="native", filter_action="native", page_size=15,
            ),
        ]),
    ], className="card"),
], fluid=True)


@callback(
    Output("fwd-metrics",     "children"),
    Output("fwd-graph",       "figure"),
    Output("fwd-conf-graph",  "figure"),
    Output("fwd-carry-graph", "figure"),
    Output("fwd-table",       "data"),
    Output("fwd-diag-table",  "data"),
    Input("fwd-interval",     "n_intervals"),
    Input("selected-symbol",  "data"),
)
def refresh_forward(_, symbol):
    sym    = symbol or "SPY"
    fwd_df = datasource.get_forward_curve(sym)
    spot   = datasource.get_spot(sym)
    src    = datasource.get_data_source_label()

    if fwd_df.empty:
        alert = no_data_alert(sym)
        return [dbc.Col(alert, width=12)], go.Figure(), go.Figure(), go.Figure(), [], []

    from src.utils.config import load_config
    pricing_cfg = load_config("pricing")
    rate    = pricing_cfg.get("risk_free_rate", {}).get("value", 0.053)
    carry   = 0.0   # carry implicite MOYEN sur les maturités
    n_mat   = 0
    if "implied_carry" in fwd_df.columns:
        carry = float(fwd_df["implied_carry"].mean())
        n_mat = int(fwd_df["implied_carry"].notna().sum())
    p = {"rate": rate, "carry": carry}

    spot_str = f"{spot:,.2f} €" if spot else "—"
    carry_label = f"Carry q (moy. {n_mat} mat.)" if n_mat else "Carry q (moyenne)"
    # Carry monétisé : q × S = revenu implicite annuel en € par unité de sous-jacent
    # (≈ dividende implicite annuel ; × mult pour l'avoir par contrat).
    carry_eur_str = f"{carry * spot:,.2f} €" if spot else "—"
    metrics = [
        dbc.Col(_mb(spot_str,                      f"Spot {sym}"),           width=3),
        dbc.Col(_mb(f"{p['rate']:.1%}",            "Taux r"),                width=2),
        dbc.Col(_mb(f"{p['carry']:.2%}",           carry_label),             width=2),
        dbc.Col(_mb(carry_eur_str,                 "Carry en € / an (q×S)"), width=2),
        dbc.Col(_mb(src, "Source",
                    "positive" if src == "Live IBKR"
                    else "info" if src == "Analytics (EOD)"
                    else "warning"),                                           width=3),
    ]

    # Forward graph
    fig_fwd = go.Figure()
    if not fwd_df.empty and "chosen_forward" in fwd_df.columns:
        fig_fwd.add_trace(go.Scatter(
            x=fwd_df["days_to_expiry"], y=fwd_df["chosen_forward"],
            mode="lines+markers+text",
            text=[f"{v:.2f}" for v in fwd_df["chosen_forward"]],
            textposition="top center",
            line=dict(color="#0969da", width=2), marker=dict(size=8),
            name=f"Forward {sym}",
        ))
        if spot is not None:
            fig_fwd.add_hline(y=spot, line_dash="dash", line_color="#57606a",
                              annotation_text=f"Spot = {spot:.2f}")
    fig_fwd.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        margin=dict(l=40, r=20, t=30, b=40),
        xaxis_title="Jours à l'expiration", yaxis_title="Prix forward (€)",
    )

    # Confidence graph
    fig_conf = go.Figure()
    if not fwd_df.empty and "confidence_score" in fwd_df.columns:
        fig_conf.add_trace(go.Bar(
            x=fwd_df["days_to_expiry"], y=fwd_df["confidence_score"],
            marker_color=["#1a7f37" if v > 0.7 else "#9a6700" if v > 0.4 else "#cf222e"
                          for v in fwd_df["confidence_score"]],
            text=[f"{v:.0%}" for v in fwd_df["confidence_score"]],
            textposition="outside",
        ))
    fig_conf.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        margin=dict(l=40, r=20, t=30, b=40),
        xaxis_title="Jours", yaxis_title="Confiance", yaxis_range=[0, 1.1],
    )

    # Carry implicite par maturité (q = dividende − repo, annualisé)
    fig_carry = go.Figure()
    if "implied_carry" in fwd_df.columns:
        fig_carry.add_trace(go.Bar(
            x=fwd_df["days_to_expiry"], y=fwd_df["implied_carry"] * 100,
            marker_color="#1a7f37",
            hovertemplate="%{x}j : q = %{y:.2f}%<extra></extra>",
        ))
        fig_carry.add_hline(y=0, line_color="#57606a", line_width=1)
    fig_carry.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        margin=dict(l=40, r=10, t=30, b=40), font=dict(size=11),
        xaxis_title="Jours", yaxis_title="q (%/an)",
    )

    table_data = fwd_df.round(4).to_dict("records") if not fwd_df.empty else []

    # Diagnostics : candidats par strike, y compris rejetés (table forward_diagnostics)
    diag = datasource.get_forward_diagnostics(sym)
    diag_data = []
    if not diag.empty:
        if "used" in diag.columns:
            diag["used"] = diag["used"].map(lambda v: "true" if bool(v) else "false")
        cols = ["expiry", "strike", "call_mid", "put_mid", "forward_estimate",
                "weight", "quality_flag", "used", "chosen_forward"]
        diag_data = diag[[c for c in cols if c in diag.columns]].round(4).to_dict("records")

    return metrics, fig_fwd, fig_conf, fig_carry, table_data, diag_data


def _mb(value, label, css=""):
    from src.utils.fmt import fr_num
    value = fr_num(value)
    return html.Div([
        html.Div(str(value), className="metric-value"),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
