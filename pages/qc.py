"""Page 10 — QC & Validation, symbol-aware avec callbacks dynamiques."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dash
from dash import html, dcc, dash_table, callback, Input, Output
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

from src.data.source import datasource
from src.data import no_data_alert

dash.register_page(__name__, path="/qc", name="QC & Validation")

STATUS_COLORS = {"pass": "#3fb950", "warn": "#d29922", "fail": "#f85149"}
STATUS_ICONS  = {"pass": "✓", "warn": "⚠", "fail": "✗"}

layout = dbc.Container([
    html.Div([
        html.H2("QC & Validation"),
        html.P("Suite de contrôles qualité. Chaque check : statut pass/warn/fail, valeur mesurée, seuil, code raison."),
    ], className="page-header"),

    dcc.Interval(id="qc-interval", interval=30000, n_intervals=0),

    # Jauge + compteurs
    dbc.Row([
        dbc.Col(dcc.Graph(id="qc-gauge", config={"displayModeBar": False}), width=3),
        dbc.Col([
            dbc.Row([
                dbc.Col(html.Div(id="qc-count-pass", className="metric-box positive"), width=4),
                dbc.Col(html.Div(id="qc-count-warn", className="metric-box warning"),  width=4),
                dbc.Col(html.Div(id="qc-count-fail", className="metric-box negative"), width=4),
            ], className="g-3 mb-3"),
            html.Div(id="qc-alert-area"),
        ], width=9),
    ], className="mb-4"),

    # Checks individuels
    html.P("RÉSULTATS DES CHECKS", className="section-title"),
    html.Div(id="qc-cards", className="row g-3"),

    # Tableau complet
    dbc.Card([
        dbc.CardHeader("Rapport QC complet"),
        dbc.CardBody(dash_table.DataTable(
            id="qc-table",
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
        )),
    ], className="card mt-2"),
], fluid=True)


@callback(
    Output("qc-gauge",      "figure"),
    Output("qc-count-pass", "children"),
    Output("qc-count-warn", "children"),
    Output("qc-count-fail", "children"),
    Output("qc-alert-area", "children"),
    Output("qc-cards",      "children"),
    Output("qc-table",      "data"),
    Input("qc-interval",    "n_intervals"),
    Input("selected-symbol","data"),
)
def refresh_qc(_, symbol):
    sym = symbol or "SPY"
    qc  = datasource.get_qc(sym)
    src = datasource.get_data_source_label()

    if qc.empty:
        alert = no_data_alert(sym) if not datasource.is_connected else dbc.Alert(
            "Aucun résultat QC disponible. Lancez le pipeline EOD pour générer les checks.",
            color="info")
        return go.Figure(), _count("0", "PASS"), _count("0", "WARN"), _count("0", "FAIL"), alert, [], []

    n_pass = int((qc["status"] == "pass").sum())
    n_warn = int((qc["status"] == "warn").sum())
    n_fail = int((qc["status"] == "fail").sum())
    total  = len(qc)
    pct    = round(n_pass / total * 100, 1) if total > 0 else 0

    # Gauge
    fig_gauge = go.Figure(go.Indicator(
        mode="gauge+number",
        value=pct,
        number={"suffix": "%", "font": {"color": "#58a6ff", "size": 40}},
        gauge={
            "axis":  {"range": [0, 100], "tickcolor": "#8b949e"},
            "bar":   {"color": "#58a6ff"},
            "bgcolor": "#21262d",
            "steps": [
                {"range": [0, 60],   "color": "#3d1c1c"},
                {"range": [60, 85],  "color": "#3d2e00"},
                {"range": [85, 100], "color": "#0d2818"},
            ],
            "threshold": {"line": {"color": "#3fb950", "width": 3}, "value": 85},
        },
    ))
    fig_gauge.update_layout(
        template="plotly_dark", paper_bgcolor="#161b22",
        margin=dict(l=30, r=30, t=30, b=10), height=220,
        font=dict(color="#e6edf3"),
    )

    # Alertes fail/warn
    alerts = []
    for _, row in qc[qc["status"].isin(["fail", "warn"])].iterrows():
        color = "danger" if row["status"] == "fail" else "warning"
        alerts.append(dbc.Alert(
            f"{STATUS_ICONS[row['status']]} {row['check_name']} — {row['reason_code']} "
            f"(val={row['measured_value']:.4f} / seuil={row['threshold']:.4f})",
            color=color, className="mb-1 py-2",
        ))

    # Anomalies vs baseline glissante (étape 14)
    try:
        anomalies = datasource.get_qc_anomalies(sym)
        if anomalies is not None and not anomalies.empty:
            alerts.insert(0, dbc.Alert(
                f"⚡ {len(anomalies)} anomalie(s) détectée(s) vs baseline historique "
                f"(z-score robuste > 3.5) — voir qc_anomalies.",
                color="danger", className="mb-1 py-2"))
    except Exception:
        pass

    src_badge = dbc.Badge(
        f"Source: {src}", color="success" if src != "Mock" else "warning",
        className="ms-2",
    )

    # Cards individuels
    cards = [
        dbc.Col(
            dbc.Card([
                dbc.CardBody([
                    html.Div([
                        html.Span(STATUS_ICONS.get(row["status"], "?") + " ",
                                  className="me-1"),
                        html.Span(row["check_name"], className="fw-bold small"),
                    ]),
                    html.Small(f"Cible : {row['target_key']}", className="text-muted d-block"),
                    html.Div([
                        html.Span("Val: ", className="text-muted small"),
                        html.Span(f"{row['measured_value']:.4f}",
                                  style={"color": STATUS_COLORS.get(row["status"], "#e6edf3")},
                                  className="small"),
                        html.Span(f" / {row['threshold']:.4f}", className="text-muted small"),
                    ], className="mt-1"),
                    html.Small(row["reason_code"], className="text-muted"),
                ]),
            ], className="card h-100",
               style={"borderLeft": f"3px solid {STATUS_COLORS.get(row['status'], '#8b949e')}"}),
            width=3, className="mb-3",
        )
        for _, row in qc.iterrows()
    ]

    return (
        fig_gauge,
        _count(str(n_pass), "PASS"),
        _count(str(n_warn), "WARN"),
        _count(str(n_fail), "FAIL"),
        [src_badge, *alerts] if alerts else src_badge,
        cards,
        qc.round(4).to_dict("records"),
    )


def _count(value, label):
    return [
        html.Div(value, className="metric-value"),
        html.Div(label, className="metric-label"),
    ]
