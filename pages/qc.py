import dash
from dash import html, dcc, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.data.mock import generate_qc

dash.register_page(__name__, path="/qc", name="QC & Validation")

qc = generate_qc()
n_pass = (qc["status"] == "pass").sum()
n_warn = (qc["status"] == "warn").sum()
n_fail = (qc["status"] == "fail").sum()

STATUS_COLORS = {"pass": "#3fb950", "warn": "#d29922", "fail": "#f85149"}
STATUS_ICONS  = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}

fig_gauge = go.Figure(go.Indicator(
    mode="gauge+number",
    value=round(n_pass / len(qc) * 100, 1),
    number={"suffix": "%", "font": {"color": "#58a6ff", "size": 40}},
    gauge={
        "axis": {"range": [0, 100], "tickcolor": "#8b949e"},
        "bar":  {"color": "#58a6ff"},
        "bgcolor": "#21262d",
        "steps": [
            {"range": [0, 60],  "color": "#3d1c1c"},
            {"range": [60, 85], "color": "#3d2e00"},
            {"range": [85, 100],"color": "#0d2818"},
        ],
        "threshold": {"line": {"color": "#3fb950", "width": 3}, "value": 85},
    },
))
fig_gauge.update_layout(
    template="plotly_dark", paper_bgcolor="#161b22",
    margin=dict(l=30, r=30, t=30, b=10), height=220,
    font=dict(color="#e6edf3"),
)

layout = dbc.Container([
    html.Div([
        html.H2("QC & Validation"),
        html.P("Suite de contrôles qualité automatisés. Chaque check retourne : statut, valeur mesurée, seuil, code raison."),
    ], className="page-header"),

    # Métriques
    dbc.Row([
        dbc.Col(dcc.Graph(figure=fig_gauge, config={"displayModeBar": False}), width=3),
        dbc.Col([
            dbc.Row([
                dbc.Col(html.Div([html.Div(str(n_pass), className="metric-value"),
                                  html.Div("PASS", className="metric-label")],
                                 className="metric-box positive"), width=4),
                dbc.Col(html.Div([html.Div(str(n_warn), className="metric-value"),
                                  html.Div("WARN", className="metric-label")],
                                 className="metric-box warning"), width=4),
                dbc.Col(html.Div([html.Div(str(n_fail), className="metric-value"),
                                  html.Div("FAIL", className="metric-label")],
                                 className="metric-box negative"), width=4),
            ], className="g-3 mb-3"),
            dbc.Alert(
                "WARN — surface_fit : RMSE = 0.019 > seuil 0.020 — Tranche SPY 30j avec peu de points. "
                "Action : inspecter les quotes filtrées pour cette maturité.",
                color="warning", className="mb-0",
            ),
        ], width=9),
    ], className="mb-4"),

    # Checks individuels
    html.P("RÉSULTATS DES CHECKS", className="section-title"),
    dbc.Row([
        dbc.Col(
            dbc.Card([
                dbc.CardBody([
                    html.Div([
                        html.Span(STATUS_ICONS[row["status"]] + " ", className="me-2"),
                        html.Span(row["check_name"], className="fw-bold"),
                    ]),
                    html.Small(f"Cible : {row['target_key']}", className="text-muted d-block"),
                    html.Div([
                        html.Span("Valeur : ", className="text-muted small"),
                        html.Span(f"{row['measured_value']:.4f}", className="small",
                                  style={"color": STATUS_COLORS[row["status"]]}),
                        html.Span(f" / seuil {row['threshold']:.4f}", className="text-muted small"),
                    ], className="mt-1"),
                    html.Small(row["reason_code"], className="text-muted"),
                ]),
            ], className="card h-100",
               style={"borderLeft": f"3px solid {STATUS_COLORS[row['status']]}"}),
            width=3, className="mb-3",
        )
        for _, row in qc.iterrows()
    ]),

    # Tableau complet
    dbc.Card([
        dbc.CardHeader("Rapport QC complet"),
        dbc.CardBody(
            dash_table.DataTable(
                data=qc.round(4).to_dict("records"),
                columns=[
                    {"name": "Check",          "id": "check_name"},
                    {"name": "Cible",          "id": "target_key"},
                    {"name": "Statut",         "id": "status"},
                    {"name": "Sévérité",       "id": "severity"},
                    {"name": "Valeur mesurée", "id": "measured_value"},
                    {"name": "Seuil",          "id": "threshold"},
                    {"name": "Raison",         "id": "reason_code"},
                ],
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "center", "padding": "8px"},
                style_data_conditional=[
                    {"if": {"filter_query": '{status} = "fail"', "column_id": "status"},
                     "color": "#f85149", "fontWeight": "bold"},
                    {"if": {"filter_query": '{status} = "warn"', "column_id": "status"},
                     "color": "#d29922", "fontWeight": "bold"},
                    {"if": {"filter_query": '{status} = "pass"', "column_id": "status"},
                     "color": "#3fb950"},
                ],
                sort_action="native",
            )
        ),
    ], className="card mt-2"),
], fluid=True)
