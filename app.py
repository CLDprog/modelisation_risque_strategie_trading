"""Point d'entrée principal de l'app Dash."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import dash
import dash_bootstrap_components as dbc
from dash import html, dcc, callback, Input, Output, State

from src.utils.config import load_config

# Symboles disponibles — définis dans configs/universe.yaml uniquement
_universe_cfg    = load_config("universe")
SUPPORTED_SYMBOLS = [u["symbol"] for u in _universe_cfg.get("underlyings", [])]
SYMBOL_DESC       = {u["symbol"]: u.get("description", u["symbol"])
                     for u in _universe_cfg.get("underlyings", [])}

app = dash.Dash(
    __name__,
    use_pages=True,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,
    title="Vol Risk Infrastructure",
)

PAGES = [
    {"name": "Collecteur",             "href": "/"},
    {"name": "Instrument Master",      "href": "/universe"},
    {"name": "Market Data",            "href": "/market-data"},
    {"name": "Forward & Carry",        "href": "/forward"},
    {"name": "Volatilité Implicite",   "href": "/implied-vol"},
    {"name": "Surface de Vol",         "href": "/surface"},
    {"name": "Pricing",                "href": "/pricing"},
    {"name": "Greeks & Risk",          "href": "/greeks"},
    {"name": "Scénarios",              "href": "/scenarios"},
    {"name": "QC & Validation",        "href": "/qc"},
]

sidebar = html.Div([
    html.Div([
        html.H5("Vol Risk", className="text-white fw-bold mb-0"),
        html.Small("Infrastructure · v2.0", className="text-muted"),
    ], className="mb-3 pb-2 border-bottom border-secondary"),

    # ── Sélecteur de produit ──────────────────────────────────────────
    html.P("PRODUIT ACTIF", className="nav-title"),
    dcc.Dropdown(
        id="symbol-selector",
        options=[{"label": f"{sym} — {SYMBOL_DESC.get(sym, sym)}", "value": sym}
                 for sym in SUPPORTED_SYMBOLS],
        value=SUPPORTED_SYMBOLS[0] if SUPPORTED_SYMBOLS else "SPY",
        clearable=False,
        searchable=False,
        style={
            "backgroundColor": "#21262d",
            "color":           "#e6edf3",
            "border":          "1px solid #30363d",
            "borderRadius":    "6px",
            "fontSize":        "13px",
        },
        className="mb-1",
    ),
    html.Div(id="symbol-spot-info", className="mb-3"),

    # ── Navigation ───────────────────────────────────────────────────
    html.P("NAVIGATION", className="nav-title mt-3"),
    dbc.Nav(
        [dbc.NavLink(p["name"], href=p["href"], active="exact", className="nav-link")
         for p in PAGES],
        vertical=True, pills=False,
    ),

    html.Hr(className="border-secondary mt-4"),

    # ── Statut connexion ─────────────────────────────────────────────
    html.Div(id="sidebar-status", children=[
        html.Small("● ", className="text-danger"),
        html.Small("Non connecté", className="text-danger"),
    ]),
    dcc.Interval(id="sidebar-interval", interval=5000, n_intervals=0),
], className="sidebar")


app.layout = dbc.Container([
    dcc.Store(id="selected-symbol",    data=SUPPORTED_SYMBOLS[0] if SUPPORTED_SYMBOLS else "SPY", storage_type="session"),
    dcc.Store(id="selected-portfolio", data="default",                                             storage_type="session"),

    dbc.Row([
        dbc.Col(sidebar, width=2),
        dbc.Col(html.Div(dash.page_container, className="p-4"), width=10),
    ], className="g-0"),
], fluid=True)


# ── Callbacks sidebar ──────────────────────────────────────────────────────────

@callback(
    Output("selected-symbol", "data"),
    Input("symbol-selector",  "value"),
)
def set_selected_symbol(symbol):
    """Ne change le Store QUE lorsque l'utilisateur choisit un autre produit."""
    from src.data.source import datasource
    sym = symbol or (SUPPORTED_SYMBOLS[0] if SUPPORTED_SYMBOLS else "SPY")
    datasource.selected_symbol = sym
    return sym


@callback(
    Output("symbol-spot-info", "children"),
    Input("selected-symbol",   "data"),
    Input("sidebar-interval",  "n_intervals"),
)
def update_spot_info(symbol, _):
    """Rafraîchit l'affichage du spot sur l'interval (sans toucher au Store)."""
    from src.data.source import datasource
    sym = symbol or "SPY"
    spot = datasource.get_spot(sym)
    if spot:
        return html.Div(
            html.Small(f"Spot : ${spot:.2f}", className="text-info d-block"),
            className="px-1")
    return html.Div(
        html.Small("Démarrez le collecteur", className="text-muted d-block"),
        className="px-1")


@callback(
    Output("sidebar-status", "children"),
    Input("sidebar-interval", "n_intervals"),
)
def update_sidebar_status(_):
    from src.data.source import datasource
    src = datasource.get_data_source_label()

    if src == "Live (collecteur)":
        dot, cls, label = "● ", "text-success", "Live (collecteur)"
    elif src == "Analytics (store)":
        dot, cls, label = "● ", "text-info", "Store (collecteur off)"
    else:
        dot, cls, label = "● ", "text-danger", "Collecteur arrêté"

    return [
        html.Small(dot,   className=cls),
        html.Small(label, className=cls),
    ]


if __name__ == "__main__":
    # use_reloader=False : indispensable ici. Le projet est dans un dossier OneDrive ;
    # la synchro peut toucher les fichiers .py et déclencher un redémarrage du reloader
    # Flask, ce qui couperait la connexion IBKR en cours. On garde le débogueur, sans
    # le rechargement automatique.
    app.run(debug=True, use_reloader=False, port=8050)
