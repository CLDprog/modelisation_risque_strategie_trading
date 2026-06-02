import dash
from dash import html, dash_table, dcc
import dash_bootstrap_components as dbc
import plotly.graph_objects as go, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.data.source import datasource

dash.register_page(__name__, path="/market-data", name="Market Data")

chain  = datasource.get_option_chain()
SPOT   = datasource.get_spot()
snap30 = chain[chain["days_to_expiry"] == 30].copy()

fig = go.Figure()
for right, color, name in [("C","#58a6ff","Calls"), ("P","#f78166","Puts")]:
    df = snap30[snap30["right"] == right]
    fig.add_trace(go.Scatter(x=df["strike"], y=df["mid_price"], mode="lines+markers",
                             name=name, line=dict(color=color), marker=dict(size=5)))
fig.add_vline(x=SPOT, line_dash="dash", line_color="#8b949e", annotation_text=f"Spot {SPOT}")
fig.update_layout(template="plotly_dark", paper_bgcolor="#161b22", plot_bgcolor="#161b22",
                  margin=dict(l=40,r=20,t=30,b=40), xaxis_title="Strike", yaxis_title="Prix mid ($)")

layout = dbc.Container([
    html.Div([html.H2("Market Data — Snapshots"),
              html.P("Données brutes normalisées en snapshots cohérents. Champ reference_type indique la source du prix.")],
             className="page-header"),
    dbc.Row([
        dbc.Col(html.Div([html.Div(f"${SPOT:.2f}", className="metric-value"), html.Div("Spot SPY", className="metric-label")], className="metric-box"), width=3),
        dbc.Col(html.Div([html.Div(str(len(snap30)), className="metric-value"), html.Div("Quotes actives 30j", className="metric-label")], className="metric-box"), width=3),
        dbc.Col(html.Div([html.Div("mid", className="metric-value"), html.Div("Reference type", className="metric-label")], className="metric-box positive"), width=3),
        dbc.Col(html.Div([html.Div("0s", className="metric-value"), html.Div("Quote age", className="metric-label")], className="metric-box"), width=3),
    ], className="g-3 mb-4"),
    dbc.Card([dbc.CardHeader("Prix calls & puts — Maturité 30 jours"),
              dbc.CardBody(dcc.Graph(figure=fig, config={"displayModeBar": False}))], className="card mb-4"),
    dbc.Card([
        dbc.CardHeader("Snapshot des quotes (30 jours)"),
        dbc.CardBody(dash_table.DataTable(
            data=snap30[["strike","right","bid","ask","mid_price","implied_vol","delta","is_usable"]].round(4).to_dict("records"),
            columns=[{"name": c, "id": c} for c in ["strike","right","bid","ask","mid_price","implied_vol","delta","is_usable"]],
            sort_action="native", filter_action="native", page_size=15,
            style_table={"overflowX": "auto"}, style_cell={"textAlign": "center", "padding": "7px"},
        )),
    ], className="card"),
], fluid=True)
