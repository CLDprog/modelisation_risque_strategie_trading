"""Page 5 — Volatilité Implicite, symbol-aware."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dash
from dash import html, dcc, dash_table, callback, Input, Output, State, no_update
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

from src.data.source import datasource
from src.data import no_data_alert

dash.register_page(__name__, path="/implied-vol", name="Volatilité Implicite")


def _smile_fig(chain, expiry: str, symbol: str) -> go.Figure:
    fig = go.Figure()
    for right, color, name in [("C", "#58a6ff", "IV Calls"), ("P", "#f78166", "IV Puts")]:
        df = chain[(chain["expiry"] == expiry) & (chain["right"] == right)]
        fig.add_trace(go.Scatter(
            x=df["log_moneyness"], y=df["implied_vol"] * 100,
            mode="lines+markers", name=name,
            line=dict(color=color, dash="solid" if right == "C" else "dash"),
            marker=dict(size=5),
        ))
    fig.add_vline(x=0, line_dash="dot", line_color="#8b949e",
                  annotation_text="ATM (k=0)")
    fig.update_layout(
        template="plotly_dark", paper_bgcolor="#161b22", plot_bgcolor="#161b22",
        margin=dict(l=40, r=20, t=30, b=40),
        xaxis_title="Log-moneyness k = ln(K/F)",
        yaxis_title="Volatilité implicite (%)",
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        title=dict(text=f"Smile IV — {symbol}", font=dict(color="#8b949e", size=12)),
    )
    return fig


layout = dbc.Container([
    html.Div([
        html.H2("Volatilité Implicite"),
        html.P("Inversion numérique du prix d'option → IV via Black-Scholes (solveur de Brent)."),
    ], className="page-header"),

    dcc.Interval(id="iv-interval", interval=30000, n_intervals=0),

    dbc.Row(id="iv-metrics", className="g-3 mb-4"),

    dbc.Card([
        dbc.CardHeader("Formules — Solveur IV (Black-76)"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.P("Eq. 8 — d1 :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(
                        r"$$d_1 = \frac{\ln(F/K) + \frac{1}{2}\sigma^2 T}{\sigma\sqrt{T}}$$",
                        mathjax=True), className="formula-box"),
                ], width=4),
                dbc.Col([
                    html.P("Eq. 9 — d2 :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$d_2 = d_1 - \sigma\sqrt{T}$$", mathjax=True),
                             className="formula-box"),
                ], width=4),
                dbc.Col([
                    html.P("Eq. 6–7 — Log-moneyness & variance totale :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$k = \ln(K/F) \quad w = \sigma^2 T$$", mathjax=True),
                             className="formula-box"),
                ], width=4),
            ]),
        ]),
    ], className="card mb-4"),

    dbc.Card([
        dbc.CardHeader("Smile de volatilité par maturité"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.Label("Maturité :", className="text-muted small"),
                    dcc.Dropdown(id="dd-expiry-iv", clearable=False,
                                 style={"backgroundColor": "#21262d", "color": "#e6edf3"}),
                ], width=4),
            ], className="mb-3"),
            dcc.Graph(id="graph-smile", config={"displayModeBar": False}),
        ]),
    ], className="card mb-4"),

    dbc.Card([
        dbc.CardHeader("Points IV détaillés"),
        dbc.CardBody(dash_table.DataTable(
            id="table-iv",
            columns=[
                {"name": "Expiry",          "id": "expiry"},
                {"name": "Strike",          "id": "strike"},
                {"name": "Right",           "id": "right"},
                {"name": "Forward",         "id": "forward"},
                {"name": "Log-moneyness k", "id": "log_moneyness"},
                {"name": "Mid price",       "id": "mid_price"},
                {"name": "IV σ",            "id": "implied_vol"},
                {"name": "Var totale w",    "id": "total_variance"},
                {"name": "Converge",        "id": "converged"},
            ],
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "padding": "6px"},
            sort_action="native", filter_action="native", page_size=15,
        )),
    ], className="card"),
], fluid=True)


@callback(
    Output("iv-metrics",    "children"),
    Output("dd-expiry-iv",  "options"),
    Output("dd-expiry-iv",  "value"),
    Input("iv-interval",    "n_intervals"),
    Input("selected-symbol","data"),
    State("dd-expiry-iv",   "value"),
)
def refresh_iv_data(_, symbol, current_expiry):
    sym   = symbol or "SPY"
    chain = datasource.get_option_chain(sym)
    src   = datasource.get_data_source_label()

    if chain.empty:
        alert = no_data_alert(sym)
        return [dbc.Col(alert, width=12)], [], None

    expiries = sorted(chain["expiry"].unique())
    n_iv   = int(chain["converged"].sum()) if "converged" in chain.columns else len(chain)
    conv_r = n_iv / max(len(chain), 1)

    # IV ATM robuste : on vise 30j mais on prend la maturité DISPONIBLE la plus
    # proche (donc 13j si c'est le max), puis le strike le plus proche de l'ATM
    # (|log_moneyness| minimal). Affiche toujours une valeur s'il y a des données.
    atm_str, atm_days = "—", 30
    conv = chain[chain.get("converged", True) == True] if "converged" in chain.columns else chain
    conv = conv[conv["implied_vol"].notna()] if "implied_vol" in conv.columns else conv
    if not conv.empty and "days_to_expiry" in conv.columns:
        target = 30
        avail = conv["days_to_expiry"].unique()
        atm_days = int(min(avail, key=lambda d: abs(d - target)))
        slice_df = conv[(conv["days_to_expiry"] == atm_days) & (conv["right"] == "C")].copy()
        if slice_df.empty:
            slice_df = conv[conv["days_to_expiry"] == atm_days].copy()
        if not slice_df.empty and "log_moneyness" in slice_df.columns:
            slice_df["k_abs"] = slice_df["log_moneyness"].abs()
            atm_iv = float(slice_df.sort_values("k_abs")["implied_vol"].iloc[0])
            atm_str = f"{atm_iv:.1%}"

    metrics = [
        dbc.Col(_mb(str(n_iv),            "IVs résolues",      "positive"), width=3),
        dbc.Col(_mb(f"{conv_r:.1%}",      "Taux de convergence","positive"), width=3),
        dbc.Col(_mb(atm_str,              f"IV ATM ~{atm_days}j"),           width=3),
        dbc.Col(_mb(src, "Source",
                    "positive" if src == "Live IBKR"
                    else "info" if src == "Analytics (EOD)" else "warning"), width=3),
    ]
    opts = [{"label": e, "value": e} for e in expiries]

    # Préserve la sélection de l'utilisateur ; ne réinitialise que si invalide
    if current_expiry in expiries:
        value_out = no_update
    else:
        value_out = expiries[2] if len(expiries) > 2 else expiries[0]
    return metrics, opts, value_out


@callback(
    Output("graph-smile", "figure"),
    Output("table-iv",    "data"),
    Input("dd-expiry-iv", "value"),
    Input("selected-symbol","data"),
    Input("iv-interval",  "n_intervals"),
)
def update_smile(expiry, symbol, _):
    sym   = symbol or "SPY"
    if not expiry:
        return go.Figure(), []
    chain = datasource.get_option_chain(sym)
    fig   = _smile_fig(chain, expiry, sym)
    data  = chain[chain["expiry"] == expiry].round(5).to_dict("records")
    return fig, data


def _mb(value, label, css=""):
    return html.Div([
        html.Div(str(value), className="metric-value"),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
