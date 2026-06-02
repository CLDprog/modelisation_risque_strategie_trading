import dash
from dash import html, dcc, dash_table
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.data.mock import generate_scenarios

dash.register_page(__name__, path="/scenarios", name="Scénarios")

scen = generate_scenarios()
summary = scen.groupby(["scenario_id", "description", "spot_shift_pct",
                         "vol_shift_abs", "time_roll_days"])["scenario_pnl"].sum().reset_index()
summary = summary.sort_values("scenario_pnl")

# ── Waterfall PnL ────────────────────────────────────────────────────────────
fig_bar = go.Figure(go.Bar(
    x=summary["description"],
    y=summary["scenario_pnl"],
    marker_color=["#3fb950" if v >= 0 else "#f85149" for v in summary["scenario_pnl"]],
    text=[f"${v:,.0f}" for v in summary["scenario_pnl"]],
    textposition="outside",
))
fig_bar.update_layout(
    template="plotly_dark", paper_bgcolor="#161b22", plot_bgcolor="#161b22",
    margin=dict(l=40, r=20, t=20, b=120),
    xaxis=dict(tickangle=-30), yaxis_title="PnL ($)",
    height=380,
)

# ── Heatmap scénario × contrat ───────────────────────────────────────────────
pivot = scen.pivot_table(index="contract_key", columns="description",
                          values="scenario_pnl", aggfunc="sum")
fig_heat = go.Figure(go.Heatmap(
    z=pivot.values,
    x=pivot.columns.tolist(),
    y=pivot.index.tolist(),
    colorscale="RdYlGn",
    text=[[f"${v:,.0f}" for v in row] for row in pivot.values],
    texttemplate="%{text}",
    textfont=dict(size=10),
    colorbar=dict(title="PnL ($)", tickfont=dict(color="#e6edf3")),
))
fig_heat.update_layout(
    template="plotly_dark", paper_bgcolor="#161b22", plot_bgcolor="#161b22",
    margin=dict(l=160, r=20, t=20, b=120),
    xaxis=dict(tickangle=-30), height=320,
)

# ── Layout ───────────────────────────────────────────────────────────────────
worst = summary.iloc[0]
best  = summary.iloc[-1]

layout = dbc.Container([
    html.Div([
        html.H2("Moteur de Scénarios"),
        html.P("Repricing complet du portefeuille sous chocs de spot, volatilité et temps. Eq. 19."),
    ], className="page-header"),

    # Formule PnL approx
    dbc.Card([
        dbc.CardHeader("Formule d'approximation par les Greeks (Eq. 19)"),
        dbc.CardBody([
            html.Div(dcc.Markdown(
                r"$$\delta P \approx \Delta \cdot \delta S + \frac{1}{2}\Gamma \cdot \delta S^2 + \nu \cdot \delta\sigma + \Theta \cdot \delta t$$",
                mathjax=True), className="formula-box"),
            dbc.Alert(
                "Source de vérité = repricing complet (Black-Scholes sous paramètres choqués). "
                "L'approximation Greeks est utilisée pour la surveillance intraday uniquement.",
                color="dark", className="mt-3 mb-0 border border-secondary",
            ),
        ]),
    ], className="card mb-4"),

    # Métriques
    dbc.Row([
        dbc.Col(html.Div([html.Div(f"${worst['scenario_pnl']:,.0f}", className="metric-value"),
                          html.Div("Pire scénario",      className="metric-label"),
                          html.Small(worst["description"], className="text-muted")],
                         className="metric-box negative"), width=3),
        dbc.Col(html.Div([html.Div(f"${best['scenario_pnl']:,.0f}", className="metric-value"),
                          html.Div("Meilleur scénario",  className="metric-label"),
                          html.Small(best["description"], className="text-muted")],
                         className="metric-box positive"), width=3),
        dbc.Col(html.Div([html.Div(f"{len(summary)}",   className="metric-value"),
                          html.Div("Scénarios exécutés", className="metric-label")],
                         className="metric-box"), width=3),
        dbc.Col(html.Div([html.Div(f"{len(scen['contract_key'].unique())}", className="metric-value"),
                          html.Div("Positions",          className="metric-label")],
                         className="metric-box"), width=3),
    ], className="g-3 mb-4"),

    # Graphiques
    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("PnL total par scénario"),
            dbc.CardBody(dcc.Graph(figure=fig_bar, config={"displayModeBar": False})),
        ], className="card"), width=6),
        dbc.Col(dbc.Card([
            dbc.CardHeader("PnL par scénario × contrat"),
            dbc.CardBody(dcc.Graph(figure=fig_heat, config={"displayModeBar": False})),
        ], className="card"), width=6),
    ], className="mb-4"),

    # Tableau grille de scénarios
    dbc.Card([
        dbc.CardHeader("Grille de scénarios (versionée)"),
        dbc.CardBody(
            dash_table.DataTable(
                data=summary.round(4).to_dict("records"),
                columns=[
                    {"name": "Scénario ID",   "id": "scenario_id"},
                    {"name": "Description",   "id": "description"},
                    {"name": "Choc spot (%)", "id": "spot_shift_pct"},
                    {"name": "Choc vol (pts)","id": "vol_shift_abs"},
                    {"name": "Roll temps (j)","id": "time_roll_days"},
                    {"name": "PnL total ($)", "id": "scenario_pnl"},
                ],
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "center", "padding": "8px"},
                style_data_conditional=[
                    {"if": {"filter_query": "{scenario_pnl} < 0", "column_id": "scenario_pnl"},
                     "color": "#f85149"},
                    {"if": {"filter_query": "{scenario_pnl} >= 0", "column_id": "scenario_pnl"},
                     "color": "#3fb950"},
                ],
                sort_action="native",
            )
        ),
    ], className="card"),
], fluid=True)
