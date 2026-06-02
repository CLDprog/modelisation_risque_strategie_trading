import dash
from dash import html, dash_table
import dash_bootstrap_components as dbc
import pandas as pd, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.data.source import datasource

dash.register_page(__name__, path="/universe", name="Instrument Master")

chain = datasource.get_option_chain()
summary = chain.groupby(["underlying_symbol","expiry","days_to_expiry"]).agg(
    n_strikes=("strike","nunique"), min_strike=("strike","min"),
    max_strike=("strike","max"), avg_iv=("implied_vol","mean")).reset_index()

layout = dbc.Container([
    html.Div([html.H2("Instrument Master"),
              html.P("Source de vérité canonique pour tous les instruments. Chaque contrat a une clé unique immuable.")],
             className="page-header"),
    dbc.Row([
        dbc.Col(html.Div([html.Div("1",  className="metric-value"), html.Div("Underlyings", className="metric-label")], className="metric-box"), width=3),
        dbc.Col(html.Div([html.Div(str(len(chain["expiry"].unique())), className="metric-value"), html.Div("Maturités", className="metric-label")], className="metric-box"), width=3),
        dbc.Col(html.Div([html.Div(str(len(chain)), className="metric-value"), html.Div("Contrats options", className="metric-label")], className="metric-box"), width=3),
        dbc.Col(html.Div([html.Div("100", className="metric-value"), html.Div("Multiplier", className="metric-label")], className="metric-box"), width=3),
    ], className="g-3 mb-4"),
    dbc.Card([
        dbc.CardHeader("Chaîne d'options par maturité"),
        dbc.CardBody(dash_table.DataTable(
            data=summary.round(4).to_dict("records"),
            columns=[{"name": c, "id": c} for c in summary.columns],
            sort_action="native", page_size=15,
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "padding": "8px"},
        )),
    ], className="card"),
], fluid=True)
