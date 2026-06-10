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

STATUS_COLORS = {"pass": "#1a7f37", "warn": "#9a6700", "fail": "#cf222e"}
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
                 "color": "#cf222e", "fontWeight": "bold"},
                {"if": {"filter_query": '{status} = "warn"', "column_id": "status"},
                 "color": "#9a6700", "fontWeight": "bold"},
                {"if": {"filter_query": '{status} = "pass"', "column_id": "status"},
                 "color": "#1a7f37"},
            ],
            sort_action="native",
        )),
    ], className="card mt-2"),

    dbc.Card([
        dbc.CardHeader("Réconciliation des greeks — publiés vs différences finies"),
        dbc.CardBody([
            html.P("Recalcul indépendant des greeks par re-pricing (Black-76 européen / CRR américain) "
                   "sur un échantillon de la chaîne. Verdict basé sur delta et vega (gamma/theta = info : "
                   "bump bruité et écart de convention forward/spot).", className="text-muted small"),
            html.Div(id="qc-recon-summary", className="mb-2"),
            dash_table.DataTable(
                id="qc-recon-table",
                style_header={"textTransform": "none"},
                columns=[
                    {"name": "Expiry",  "id": "expiry"},
                    {"name": "Strike",  "id": "strike"},
                    {"name": "C/P",     "id": "right"},
                    {"name": "Modèle",  "id": "model"},
                    {"name": "Δ publié","id": "delta_pub"},
                    {"name": "Δ diff-finies", "id": "delta_fd"},
                    {"name": "Δ écart", "id": "delta_diff"},
                    {"name": "ν publié","id": "vega_pub"},
                    {"name": "ν diff-finies", "id": "vega_fd"},
                    {"name": "ν écart", "id": "vega_diff"},
                    {"name": "Γ écart", "id": "gamma_diff"},
                    {"name": "Θ écart", "id": "theta_diff"},
                ],
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "center", "padding": "6px", "fontSize": "13px"},
                sort_action="native", page_size=12,
            ),
        ]),
    ], className="card mt-4"),

    dbc.Card([
        dbc.CardHeader("Triage (qc_triage) — checks en warn/fail du run"),
        dbc.CardBody(dash_table.DataTable(
            id="qc-triage-table",
            columns=[
                {"name": "Check",   "id": "check_name"},
                {"name": "Cible",   "id": "target_key"},
                {"name": "Statut",  "id": "status"},
                {"name": "Sévérité","id": "severity"},
                {"name": "Valeur",  "id": "measured_value"},
                {"name": "Seuil",   "id": "threshold"},
                {"name": "Raison",  "id": "reason_code"},
                {"name": "Run",     "id": "run_id"},
            ],
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "padding": "6px", "fontSize": "13px"},
            style_data_conditional=[
                {"if": {"filter_query": '{status} = "fail"', "column_id": "status"},
                 "color": "#cf222e", "fontWeight": "bold"},
                {"if": {"filter_query": '{status} = "warn"', "column_id": "status"},
                 "color": "#9a6700", "fontWeight": "bold"},
            ],
            sort_action="native", page_size=10,
        )),
    ], className="card mt-4"),
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
        number={"suffix": "%", "font": {"color": "#0969da", "size": 40}},
        gauge={
            "axis":  {"range": [0, 100], "tickcolor": "#57606a"},
            "bar":   {"color": "#0969da"},
            "bgcolor": "#ffffff",
            "steps": [
                {"range": [0, 60],   "color": "#3d1c1c"},
                {"range": [60, 85],  "color": "#3d2e00"},
                {"range": [85, 100], "color": "#0d2818"},
            ],
            "threshold": {"line": {"color": "#1a7f37", "width": 3}, "value": 85},
        },
    ))
    fig_gauge.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff",
        margin=dict(l=30, r=30, t=30, b=10), height=220,
        font=dict(color="#1f2328"),
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
                                  style={"color": STATUS_COLORS.get(row["status"], "#1f2328")},
                                  className="small"),
                        html.Span(f" / {row['threshold']:.4f}", className="text-muted small"),
                    ], className="mt-1"),
                    html.Small(row["reason_code"], className="text-muted"),
                ]),
            ], className="card h-100",
               style={"borderLeft": f"3px solid {STATUS_COLORS.get(row['status'], '#57606a')}"}),
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


@callback(
    Output("qc-recon-summary", "children"),
    Output("qc-recon-table",   "data"),
    Output("qc-triage-table",  "data"),
    Input("qc-interval",       "n_intervals"),
    Input("selected-symbol",   "data"),
)
def refresh_qc_extras(_, symbol):
    sym = symbol or "ESTX50"

    recon = datasource.get_greeks_reconciliation(sym)
    summary, recon_data = html.Small("Pas d'échantillon de réconciliation pour ce sous-jacent.",
                                     className="text-muted"), []
    if not recon.empty:
        d_max = float(recon["delta_diff"].abs().max()) if "delta_diff" in recon.columns else 0.0
        v_max = float(recon["vega_diff"].abs().max()) if "vega_diff" in recon.columns else 0.0
        ok = d_max < 0.05 and v_max < 0.05
        summary = [
            dbc.Badge(f"{len(recon)} options échantillonnées", color="info", className="me-2"),
            dbc.Badge(f"max |Δ écart| = {d_max:.4f}", color="success" if d_max < 0.05 else "warning",
                      className="me-2"),
            dbc.Badge(f"max |ν écart| = {v_max:.4f}", color="success" if v_max < 0.05 else "warning",
                      className="me-2"),
            dbc.Badge("greeks cohérents" if ok else "écarts à examiner",
                      color="success" if ok else "warning"),
        ]
        cols = [c["id"] for c in [
            {"id": "expiry"}, {"id": "strike"}, {"id": "right"}, {"id": "model"},
            {"id": "delta_pub"}, {"id": "delta_fd"}, {"id": "delta_diff"},
            {"id": "vega_pub"}, {"id": "vega_fd"}, {"id": "vega_diff"},
            {"id": "gamma_diff"}, {"id": "theta_diff"}]]
        recon_data = recon[[c for c in cols if c in recon.columns]].round(5).to_dict("records")

    triage = datasource.get_qc_triage(sym)
    triage_data = triage.round(4).to_dict("records") if not triage.empty else []

    return summary, recon_data, triage_data


def _count(value, label):
    return [
        html.Div(value, className="metric-value"),
        html.Div(label, className="metric-label"),
    ]
