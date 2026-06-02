"""Point d'entrée principal de l'app Dash."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import dash
import dash_bootstrap_components as dbc
from dash import html, dcc, callback, Input, Output

app = dash.Dash(
    __name__,
    use_pages=True,
    external_stylesheets=[dbc.themes.DARKLY],
    suppress_callback_exceptions=True,
    title="Vol Risk Infrastructure",
)

PAGES = [
    {"name": "Connexion IBKR",        "href": "/"},
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
        html.Small("Infrastructure · v1.0", className="text-muted"),
    ], className="mb-4 pb-3 border-bottom border-secondary"),

    html.P("NAVIGATION", className="nav-title"),
    dbc.Nav([
        dbc.NavLink(
            p["name"],
            href=p["href"],
            active="exact",
            className="nav-link",
        )
        for p in PAGES
    ], vertical=True, pills=False),

    html.Hr(className="border-secondary mt-4"),
    html.Div(id="sidebar-mode-badge", children=[
        html.Small("Mode: ", className="text-muted"),
        html.Small("Mock", className="text-warning"),
    ]),
    dcc.Interval(id="sidebar-interval", interval=5000, n_intervals=0),
], className="sidebar")

app.layout = dbc.Container([
    dbc.Row([
        dbc.Col(sidebar, width=2),
        dbc.Col(
            html.Div(dash.page_container, className="p-4"),
            width=10,
        ),
    ], className="g-0"),
], fluid=True)

@callback(
    Output("sidebar-mode-badge", "children"),
    Input("sidebar-interval",    "n_intervals"),
)
def update_sidebar_mode(_):
    from src.data.source import datasource
    if datasource.mode == "live" and datasource.is_connected:
        return [
            html.Small("Mode: ", className="text-muted"),
            html.Small("Live IBKR", className="text-success fw-bold"),
        ]
    return [
        html.Small("Mode: ", className="text-muted"),
        html.Small("Mock", className="text-warning"),
    ]


if __name__ == "__main__":
    app.run(debug=True, port=8050)
