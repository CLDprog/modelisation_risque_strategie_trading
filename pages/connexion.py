"""Page 1 — Connexion IBKR avec callbacks fonctionnels."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dash
from dash import html, dcc, callback, Input, Output, State
import dash_bootstrap_components as dbc
from datetime import datetime, timezone

from src.data.source import datasource

dash.register_page(__name__, path="/", name="Connexion IBKR")


# ── Helpers UI ────────────────────────────────────────────────────────────────

def _info_row(label: str, value: str):
    return html.Div([
        html.Span(label + " : ", className="text-muted me-2 small"),
        html.Span(value, className="text-light small"),
    ], className="mb-1")


def _metric_box(value: str, label: str, css: str = ""):
    return html.Div([
        html.Div(value, className="metric-value"),
        html.Div(label, className="metric-label"),
    ], className=f"metric-box {css}")


def _status_panel():
    """Panneau statut initial (déconnecté)."""
    return [
        html.Div([
            html.Span("● ", className="status-disconnected"),
            html.Span("DÉCONNECTÉ", className="status-disconnected"),
        ], className="mb-3 fs-5"),
        _info_row("Heartbeat",    "—"),
        _info_row("Reconnexions", "0"),
        _info_row("Connecté à",   "—"),
        _info_row("Mode données", "Simulées (mock)"),
    ]


# ── Layout ────────────────────────────────────────────────────────────────────

layout = dbc.Container([
    html.Div([
        html.H2("Connexion IBKR"),
        html.P("Connexion à TWS ou IB Gateway via l'API Socket. Données différées 15 min (paper trading)."),
    ], className="page-header"),

    # Interval pour rafraîchir le statut toutes les 5s
    dcc.Interval(id="interval-status", interval=5000, n_intervals=0),

    # Alerte retour d'action (connect/disconnect)
    html.Div(id="conn-alert"),

    dbc.Row([
        # Paramètres
        dbc.Col([
            dbc.Card([
                dbc.CardHeader("Paramètres de connexion"),
                dbc.CardBody([
                    dbc.Row([
                        dbc.Col([
                            html.Label("Host", className="text-muted small"),
                            dbc.Input(id="input-host", value="127.0.0.1",
                                      type="text", className="dash-input mb-3"),
                        ], width=5),
                        dbc.Col([
                            html.Label("Port", className="text-muted small"),
                            dbc.Input(id="input-port", value="7497",
                                      type="number", className="dash-input mb-3"),
                            html.Small("7497 = Paper TWS · 4002 = Paper Gateway",
                                       className="text-muted"),
                        ], width=4),
                        dbc.Col([
                            html.Label("Client ID", className="text-muted small"),
                            dbc.Input(id="input-client-id", value="1",
                                      type="number", className="dash-input mb-3"),
                        ], width=3),
                    ]),
                    dbc.Row([
                        dbc.Col([
                            dbc.Button("Connecter",    id="btn-connect",
                                       color="success", className="me-2"),
                            dbc.Button("Déconnecter",  id="btn-disconnect",
                                       color="danger",  outline=True),
                        ])
                    ]),
                ]),
            ], className="card mb-4"),

            # Configuration requise
            dbc.Card([
                dbc.CardHeader("Configuration requise dans TWS"),
                dbc.CardBody([
                    dbc.ListGroup([
                        dbc.ListGroupItem(
                            "Edit → Global Configuration → API → Settings",
                            className="bg-transparent border-secondary text-light"),
                        dbc.ListGroupItem(
                            "Cocher : Enable ActiveX and Socket Clients",
                            className="bg-transparent border-secondary text-light"),
                        dbc.ListGroupItem(
                            "Décocher : Read-Only API",
                            className="bg-transparent border-secondary text-light"),
                        dbc.ListGroupItem(
                            "Socket port : 7497 · Localhost only : coché",
                            className="bg-transparent border-secondary text-light"),
                    ], flush=True),
                    dbc.Alert(
                        "TWS doit être ouvert et connecté avant de cliquer Connecter.",
                        color="warning", className="mt-3 mb-0",
                    ),
                ]),
            ], className="card"),
        ], width=7),

        # Statut live
        dbc.Col([
            dbc.Card([
                dbc.CardHeader("Statut de session"),
                dbc.CardBody(html.Div(id="session-status",
                                      children=_status_panel())),
            ], className="card mb-4"),

            dbc.Card([
                dbc.CardHeader("Source de données active"),
                dbc.CardBody(html.Div(id="data-source-badge", children=[
                    dbc.Badge("MOCK — données simulées", color="warning",
                              className="fs-6 p-2"),
                    html.P("Spot SPY fictif · Chaîne d'options calculée",
                           className="text-muted small mt-2 mb-0"),
                ])),
            ], className="card"),
        ], width=5),
    ]),

    # Métriques
    dbc.Row([
        dbc.Col(html.Div(id="metric-state",     children=_metric_box("DÉCONNECTÉ", "État")),       width=3),
        dbc.Col(html.Div(id="metric-heartbeat", children=_metric_box("—",           "Heartbeat")), width=3),
        dbc.Col(html.Div(id="metric-spot",      children=_metric_box("—",           "Spot SPY")),  width=3),
        dbc.Col(html.Div(id="metric-mode",      children=_metric_box("Mock",        "Mode")),      width=3),
    ], className="mt-4 g-3"),

], fluid=True)


# ── Callbacks ─────────────────────────────────────────────────────────────────

@callback(
    Output("conn-alert",        "children"),
    Output("session-status",    "children"),
    Output("data-source-badge", "children"),
    Output("metric-state",      "children"),
    Output("metric-heartbeat",  "children"),
    Output("metric-spot",       "children"),
    Output("metric-mode",       "children"),
    Input("btn-connect",        "n_clicks"),
    Input("btn-disconnect",     "n_clicks"),
    Input("interval-status",    "n_intervals"),
    State("input-host",         "value"),
    State("input-port",         "value"),
    State("input-client-id",    "value"),
    prevent_initial_call=True,
)
def handle_connection(n_connect, n_disconnect, n_intervals, host, port, client_id):
    from dash import ctx

    triggered = ctx.triggered_id

    # ── Bouton Connecter — non-bloquant ──
    if triggered == "btn-connect":
        datasource.connect(
            host=host or "127.0.0.1",
            port=int(port or 7497),
            client_id=int(client_id or 1),
        )
        alert = dbc.Alert("Connexion en cours... (statut mis à jour dans 5s)",
                          color="info", dismissable=True, duration=6000)

    # ── Bouton Déconnecter ──
    elif triggered == "btn-disconnect":
        datasource.disconnect()
        alert = dbc.Alert("Déconnecté. Retour en mode mock.",
                          color="warning", dismissable=True, duration=3000)

    # ── Rafraîchissement automatique ──
    else:
        # Afficher l'erreur si connexion échouée
        if datasource.last_error and not datasource.is_connected:
            alert = dbc.Alert(f"Erreur : {datasource.last_error}",
                              color="danger", dismissable=True)
        else:
            alert = ""

    # ── Construction du panneau statut ──
    connected = datasource.is_connected
    state_lbl = datasource.state_label
    hb_age    = datasource.heartbeat_age
    hb_str    = f"{hb_age:.0f}s" if hb_age is not None else "—"
    conn_at   = datasource.connected_at_str

    status_color  = "status-connected" if connected else "status-disconnected"
    status_dot    = "●"

    status_panel = [
        html.Div([
            html.Span(f"{status_dot} ", className=status_color),
            html.Span(state_lbl,        className=status_color),
        ], className="mb-3 fs-5"),
        _info_row("Heartbeat",    hb_str),
        _info_row("Connecté à",   conn_at),
        _info_row("Mode données", "Live (IBKR)" if connected else "Simulées (mock)"),
        _info_row("Erreur",       datasource.last_error or "—"),
    ]

    # ── Badge source de données ──
    if connected:
        spot = datasource.get_spot()
        badge = [
            dbc.Badge("LIVE — données IBKR différées 15 min",
                      color="success", className="fs-6 p-2"),
            html.P(f"Spot SPY live : ${spot:.2f} · Chaîne calculée sur spot réel",
                   className="text-muted small mt-2 mb-0"),
        ]
        spot_str = f"${spot:.2f}"
        mode_str = "Live"
    else:
        spot_str = f"${datasource.get_spot():.2f} (mock)"
        mode_str = "Mock"
        badge = [
            dbc.Badge("MOCK — données simulées", color="warning", className="fs-6 p-2"),
            html.P("Spot SPY fictif · Chaîne d'options calculée",
                   className="text-muted small mt-2 mb-0"),
        ]

    # ── Métriques ──
    css_state = "positive" if connected else "negative"
    m_state   = _metric_box(state_lbl,  "État",      css_state)
    m_hb      = _metric_box(hb_str,     "Heartbeat", "positive" if connected else "")
    m_spot    = _metric_box(spot_str,   "Spot SPY")
    m_mode    = _metric_box(mode_str,   "Mode",      "positive" if connected else "warning")

    return alert, status_panel, badge, m_state, m_hb, m_spot, m_mode
