"""Page 1 — Monitoring du collecteur (roadmap : runbook start-of-day)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timezone

import dash
from dash import html, dcc, dash_table, callback, Input, Output
import dash_bootstrap_components as dbc

from src.data.source import datasource

dash.register_page(__name__, path="/collecteur", name="Collecteur")


def _metric_box(value, label, css=""):
    return html.Div([
        html.Div(value, className="metric-value"),
        html.Div(label, className="metric-label"),
    ], className=f"metric-box {css}")


def _info_row(label, value):
    return html.Div([
        html.Span(label + " : ", className="text-muted me-2 small"),
        html.Span(value, className="text-dark small"),
    ], className="mb-1")


def _age_str(iso_ts):
    if not iso_ts:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_ts)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        if age < 60:
            return f"il y a {age:.0f}s"
        if age < 3600:
            return f"il y a {age/60:.0f} min"
        return f"il y a {age/3600:.1f} h"
    except Exception:
        return iso_ts


layout = dbc.Container([
    html.Div([
        html.H2("Collecteur de données"),
        html.P("Le collecteur (process séparé) possède la connexion IBKR et alimente le store. "
               "Le dashboard lit uniquement ce store — il ne se connecte jamais directement à IBKR."),
    ], className="page-header"),

    dcc.Interval(id="col-interval", interval=5000, n_intervals=0),

    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("État du collecteur"),
            dbc.CardBody(html.Div(id="col-status")),
        ], className="card mb-4"), width=6),

        dbc.Col(dbc.Card([
            dbc.CardHeader("Source de données active"),
            dbc.CardBody(html.Div(id="col-source-badge")),
        ], className="card mb-4"), width=6),
    ]),

    dbc.Row([
        dbc.Col(html.Div(id="col-metric-state"),  width=3),
        dbc.Col(html.Div(id="col-metric-cycles"), width=3),
        dbc.Col(html.Div(id="col-metric-last"),   width=3),
        dbc.Col(html.Div(id="col-metric-syms"),   width=3),
    ], className="g-3 mb-4"),

    dbc.Card([
        dbc.CardHeader("Couverture par sous-jacent"),
        dbc.CardBody(dash_table.DataTable(
            id="col-symbols-table",
            columns=[
                {"name": "Symbole",        "id": "symbol"},
                {"name": "Spot",           "id": "spot"},
                {"name": "Quotes options", "id": "n_quotes"},
                {"name": "Dernière MAJ",   "id": "updated"},
                {"name": "État",           "id": "state"},
            ],
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "padding": "8px"},
        )),
    ], className="card mb-4"),

    dbc.Card([
        dbc.CardHeader("Démarrer le collecteur"),
        dbc.CardBody([
            html.P("Dans un terminal séparé, à la racine du projet :", className="text-muted small mb-2"),
            dcc.Markdown("```bash\npython run_collector.py\n```"),
            html.P("Options : --interval 60 (cycle en secondes) · --account-id DU… (sinon auto-découvert)",
                   className="text-muted small mb-3"),
            dbc.Alert(
                "Le gateway IBKR Web (Client Portal) doit être lancé et authentifié "
                "sur https://localhost:5000. Le collecteur reconnecte automatiquement "
                "si la session tombe.",
                color="secondary", className="mb-0 border",
            ),
        ]),
    ], className="card"),
], fluid=True)


@callback(
    Output("col-status",         "children"),
    Output("col-source-badge",   "children"),
    Output("col-metric-state",   "children"),
    Output("col-metric-cycles",  "children"),
    Output("col-metric-last",    "children"),
    Output("col-metric-syms",    "children"),
    Output("col-symbols-table",  "data"),
    Input("col-interval",        "n_intervals"),
)
def refresh_collector(_):
    st        = datasource.collector_status()
    connected = datasource.is_collector_connected
    src       = datasource.get_data_source_label()

    # Panneau statut
    if not st:
        dot_cls, state_lbl = "status-disconnected", "NON DÉMARRÉ"
    elif connected:
        dot_cls, state_lbl = "status-connected", "ACTIF · CONNECTÉ"
    else:
        dot_cls, state_lbl = "status-disconnected", "ARRÊTÉ / DÉCONNECTÉ"

    status_panel = [
        html.Div([
            html.Span("● ", className=dot_cls),
            html.Span(state_lbl, className=dot_cls),
        ], className="mb-3 fs-5"),
        _info_row("Démarré",       _age_str(st.get("started_at"))),
        _info_row("Dernier cycle", _age_str(st.get("last_cycle"))),
        _info_row("Cycles",        str(st.get("cycle_count", 0))),
        _info_row("Connexion IBKR", "oui" if st.get("connected") else "non"),
    ]

    # Badge source
    if src == "Live (collecteur)":
        badge = [dbc.Badge("LIVE — collecteur actif", color="success", className="fs-6 p-2"),
                 html.P("Données rafraîchies en continu par le collecteur.",
                        className="text-muted small mt-2 mb-0")]
    elif src == "Analytics (store)":
        badge = [dbc.Badge("STORE — collecteur inactif", color="info", className="fs-6 p-2"),
                 html.P("Dernières données persistées (collecteur arrêté).",
                        className="text-muted small mt-2 mb-0")]
    else:
        badge = [dbc.Badge("AUCUNE DONNÉE", color="danger", className="fs-6 p-2"),
                 html.P("Lancez le collecteur pour alimenter le dashboard.",
                        className="text-muted small mt-2 mb-0")]

    # Métriques
    symbols = st.get("symbols", {})
    n_with_data = sum(1 for s in symbols.values() if s.get("spot"))
    m_state  = _metric_box(state_lbl.split(" ")[0], "État",
                           "positive" if connected else "negative")
    m_cycles = _metric_box(str(st.get("cycle_count", 0)), "Cycles")
    m_last   = _metric_box(_age_str(st.get("last_cycle")), "Dernier cycle",
                           "positive" if connected else "")
    m_syms   = _metric_box(f"{n_with_data}/{len(symbols) or len(datasource.available_symbols)}",
                           "Symboles avec données")

    # Table des symboles
    rows = []
    for sym in datasource.available_symbols:
        sd = symbols.get(sym, {})
        spot = sd.get("spot")
        rows.append({
            "symbol":   sym,
            "spot":     f"{float(spot):,.2f} €" if spot else "—",
            "n_quotes": sd.get("n_quotes", 0),
            "updated":  _age_str(sd.get("updated")),
            "state":    "OK" if spot else ("erreur" if sd.get("error") else "—"),
        })

    return status_panel, badge, m_state, m_cycles, m_last, m_syms, rows
