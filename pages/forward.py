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
            dbc.CardBody(dcc.Graph(id="fwd-graph", config={"displayModeBar": False})),
        ], className="card"), width=8),
        dbc.Col(dbc.Card([
            dbc.CardHeader("Score de confiance"),
            dbc.CardBody(dcc.Graph(id="fwd-conf-graph", config={"displayModeBar": False})),
        ], className="card"), width=4),
    ], className="mb-4"),

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
    ], className="card"),
], fluid=True)


@callback(
    Output("fwd-metrics",     "children"),
    Output("fwd-graph",       "figure"),
    Output("fwd-conf-graph",  "figure"),
    Output("fwd-table",       "data"),
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
        return [dbc.Col(alert, width=12)], go.Figure(), go.Figure(), []

    from src.utils.config import load_config
    pricing_cfg = load_config("pricing")
    rate    = pricing_cfg.get("risk_free_rate", {}).get("value", 0.053)
    carry   = 0.0   # carry implicite MOYEN sur les maturités
    n_mat   = 0
    if "implied_carry" in fwd_df.columns:
        carry = float(fwd_df["implied_carry"].mean())
        n_mat = int(fwd_df["implied_carry"].notna().sum())
    p = {"rate": rate, "carry": carry}

    spot_str = f"${spot:.2f}" if spot else "—"
    carry_label = f"Carry q (moy. {n_mat} mat.)" if n_mat else "Carry q (moyenne)"
    metrics = [
        dbc.Col(_mb(spot_str,                      f"Spot {sym}"),           width=3),
        dbc.Col(_mb(f"{p['rate']:.1%}",            "Taux r"),                width=3),
        dbc.Col(_mb(f"{p['carry']:.2%}",           carry_label),             width=3),
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
            line=dict(color="#58a6ff", width=2), marker=dict(size=8),
            name=f"Forward {sym}",
        ))
        if spot is not None:
            fig_fwd.add_hline(y=spot, line_dash="dash", line_color="#8b949e",
                              annotation_text=f"Spot = {spot:.2f}")
    fig_fwd.update_layout(
        template="plotly_dark", paper_bgcolor="#161b22", plot_bgcolor="#161b22",
        margin=dict(l=40, r=20, t=30, b=40),
        xaxis_title="Jours à l'expiration", yaxis_title="Prix forward ($)",
    )

    # Confidence graph
    fig_conf = go.Figure()
    if not fwd_df.empty and "confidence_score" in fwd_df.columns:
        fig_conf.add_trace(go.Bar(
            x=fwd_df["days_to_expiry"], y=fwd_df["confidence_score"],
            marker_color=["#3fb950" if v > 0.7 else "#d29922" if v > 0.4 else "#f85149"
                          for v in fwd_df["confidence_score"]],
            text=[f"{v:.0%}" for v in fwd_df["confidence_score"]],
            textposition="outside",
        ))
    fig_conf.update_layout(
        template="plotly_dark", paper_bgcolor="#161b22", plot_bgcolor="#161b22",
        margin=dict(l=40, r=20, t=30, b=40),
        xaxis_title="Jours", yaxis_title="Confiance", yaxis_range=[0, 1.1],
    )

    table_data = fwd_df.round(4).to_dict("records") if not fwd_df.empty else []
    return metrics, fig_fwd, fig_conf, table_data


def _mb(value, label, css=""):
    return html.Div([
        html.Div(str(value), className="metric-value"),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
