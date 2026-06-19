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
    for right, color, name in [("C", "#0969da", "IV Calls"), ("P", "#bc4c00", "IV Puts")]:
        df = chain[(chain["expiry"] == expiry) & (chain["right"] == right)]
        fig.add_trace(go.Scatter(
            x=df["log_moneyness"], y=df["implied_vol"] * 100,
            mode="lines+markers", name=name,
            line=dict(color=color, dash="solid" if right == "C" else "dash"),
            marker=dict(size=5),
        ))
    fig.add_vline(x=0, line_dash="dot", line_color="#57606a",
                  annotation_text="ATM (k=0)")
    fig.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        margin=dict(l=40, r=20, t=30, b=40),
        xaxis_title="Log-moneyness k = ln(K/F)",
        yaxis_title="Volatilité implicite (%)",
        legend=dict(bgcolor="rgba(0,0,0,0)"),
        title=dict(text=f"Smile IV — {symbol}", font=dict(color="#57606a", size=12)),
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

    # ── Vue d'ensemble : smiles superposés + term structure + skew ────
    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Smiles superposés — toutes les maturités (options OTM)"),
            dbc.CardBody(dcc.Graph(id="iv-smile-overlay", config={"displayModeBar": False}),
                         className="p-2"),
        ], className="card h-100"), width=7),
        dbc.Col(dbc.Card([
            dbc.CardHeader("Term structure ATM (k ≈ 0)"),
            dbc.CardBody(dcc.Graph(id="iv-term-structure", config={"displayModeBar": False}),
                         className="p-2"),
        ], className="card h-100"), width=5),
    ], className="g-2 mb-3"),

    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Skew — risk reversal & butterfly 30Δ par maturité"),
            dbc.CardBody([
                dcc.Graph(id="iv-skew-fig", config={"displayModeBar": False}),
                html.Small("RR30 = IV(put 30Δ) − IV(call 30Δ) : prime de la protection à la "
                           "baisse · BF30 = (IV(put 30Δ)+IV(call 30Δ))/2 − IV(ATM) : convexité "
                           "des ailes.", className="text-muted"),
            ], className="p-2"),
        ], className="card h-100"), width=6),
        dbc.Col(dbc.Card([
            dbc.CardHeader("Smile par maturité — calls vs puts"),
            dbc.CardBody([
                dcc.Dropdown(id="dd-expiry-iv", clearable=False,
                             style={"fontSize": "12px"}, className="mb-2"),
                dcc.Graph(id="graph-smile", config={"displayModeBar": False}),
            ], className="p-2"),
        ], className="card h-100"), width=6),
    ], className="g-2 mb-3"),

    dbc.Card([
        dbc.CardHeader("Points IV détaillés"),
        dbc.CardBody(dash_table.DataTable(
            id="table-iv",
            style_header={"textTransform": "none"},
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
    ], className="card mb-4"),

    dbc.Card([
        dbc.CardHeader("Diagnostics du solveur (iv_diagnostics) — échecs de convergence"),
        dbc.CardBody([
            html.Div(id="iv-diag-summary", className="mb-2"),
            dash_table.DataTable(
                id="iv-diag-table",
                columns=[
                    {"name": "Expiry",   "id": "expiry"},
                    {"name": "Strike",   "id": "strike"},
                    {"name": "C/P",      "id": "right"},
                    {"name": "Mid",      "id": "mid_price"},
                    {"name": "Forward",  "id": "forward"},
                    {"name": "IV",       "id": "implied_vol"},
                    {"name": "Convergé", "id": "converged"},
                    {"name": "Raison d'échec", "id": "failure_reason"},
                ],
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "center", "padding": "6px", "fontSize": "13px"},
                sort_action="native", page_size=10,
            ),
        ]),
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


_TENOR_COLORS = ["#0a3069", "#0969da", "#218bff", "#54aeff", "#1a7f37", "#9a6700", "#bc4c00"]

_BASE_LAYOUT = dict(template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
                    font=dict(size=11))


@callback(
    Output("iv-smile-overlay",  "figure"),
    Output("iv-term-structure", "figure"),
    Output("iv-skew-fig",       "figure"),
    Input("selected-symbol",    "data"),
    Input("iv-interval",        "n_intervals"),
)
def refresh_iv_charts(symbol, _):
    sym   = symbol or "ESTX50"
    chain = datasource.get_option_chain(sym)
    empty = go.Figure()
    if chain.empty:
        return empty, empty, empty
    usable = chain[chain["is_usable"]] if "is_usable" in chain.columns else chain
    usable = usable.dropna(subset=["implied_vol", "log_moneyness"])
    # Convention smile : options OTM uniquement (puts sous le forward, calls au-dessus).
    # Une option très ITM a un prix ≈ intrinsèque → IV bruitée ; comme la grille porte
    # call ET put à chaque strike, mélanger les deux dessinait des segments verticaux
    # (deux IV au même k — constaté sur ESTX50 554j, strike 9000 : call 14.8% vs put
    # ITM 19.9%). Le smile par maturité (carte dédiée) garde la vue calls vs puts.
    usable = usable[((usable["right"] == "P") & (usable["log_moneyness"] < 0)) |
                    ((usable["right"] == "C") & (usable["log_moneyness"] >= 0))]
    expiries = sorted(usable["expiry"].unique())

    # 1) Smiles superposés (OTM, une trace par maturité)
    fig_ov = go.Figure()
    for i, e in enumerate(expiries):
        d = usable[usable["expiry"] == e].sort_values("log_moneyness")
        days = int(d["days_to_expiry"].iloc[0]) if "days_to_expiry" in d.columns else 0
        fig_ov.add_trace(go.Scatter(
            x=d["log_moneyness"], y=d["implied_vol"] * 100,
            mode="lines+markers", name=f"{e} ({days}j)",
            line=dict(color=_TENOR_COLORS[i % len(_TENOR_COLORS)], width=1.5),
            marker=dict(size=4),
        ))
    fig_ov.add_vline(x=0, line_dash="dot", line_color="#57606a")
    fig_ov.add_annotation(
        xref="paper", yref="paper", x=0.0, y=1.13, xanchor="left", yanchor="bottom",
        showarrow=False, align="left", font=dict(size=9.5, color="#57606a"),
        text="Construction : chaque point = la vol implicite de l'option OTM à ce "
             "strike — put à gauche de l'ATM, call à droite, l'ATM (k≈0) au centre.")
    fig_ov.update_layout(height=330, margin=dict(l=45, r=10, t=44, b=40),
                         xaxis_title="Log-moneyness k = ln(K/F)", yaxis_title="IV (%)",
                         legend=dict(font=dict(size=9)), **_BASE_LAYOUT)

    # 2) Term structure ATM : IV du strike au |k| minimal, par maturité
    atm_rows = []
    for e in expiries:
        d = usable[usable["expiry"] == e]
        if d.empty:
            continue
        row = d.loc[d["log_moneyness"].abs().idxmin()]
        atm_rows.append((int(row.get("days_to_expiry", 0)), float(row["implied_vol"]) * 100))
    fig_ts = go.Figure()
    if atm_rows:
        atm_rows.sort()
        fig_ts.add_trace(go.Scatter(
            x=[r[0] for r in atm_rows], y=[r[1] for r in atm_rows],
            mode="lines+markers+text", text=[f"{r[1]:.1f}" for r in atm_rows],
            textposition="top center", textfont=dict(size=9),
            line=dict(color="#0969da", width=2), marker=dict(size=7),
        ))
    fig_ts.update_layout(height=330, margin=dict(l=45, r=15, t=8, b=40),
                         xaxis_title="Jours à expiration", yaxis_title="IV ATM (%)",
                         **_BASE_LAYOUT)

    # 3) Skew RR30 / BF30 par maturité (sélection par delta de la grille ±30Δ)
    skew_rows = []
    if "delta" in usable.columns:
        for e in expiries:
            d = usable[usable["expiry"] == e].dropna(subset=["delta"])
            if d.empty:
                continue
            p30 = d[(d["right"] == "P") & (d["delta"].between(-0.40, -0.20))]["implied_vol"]
            c30 = d[(d["right"] == "C") & (d["delta"].between(0.20, 0.40))]["implied_vol"]
            atm = d.loc[d["log_moneyness"].abs().idxmin(), "implied_vol"]
            if p30.empty or c30.empty:
                continue
            rr = (p30.mean() - c30.mean()) * 100
            bf = ((p30.mean() + c30.mean()) / 2 - atm) * 100
            days = int(d["days_to_expiry"].iloc[0]) if "days_to_expiry" in d.columns else 0
            skew_rows.append((days, rr, bf))
    fig_sk = go.Figure()
    if skew_rows:
        skew_rows.sort()
        x = [f"{r[0]}j" for r in skew_rows]
        fig_sk.add_trace(go.Bar(x=x, y=[r[1] for r in skew_rows], name="RR30 (pts)",
                                marker_color="#cf222e", opacity=0.8))
        fig_sk.add_trace(go.Bar(x=x, y=[r[2] for r in skew_rows], name="BF30 (pts)",
                                marker_color="#0969da", opacity=0.8))
    fig_sk.update_layout(height=300, margin=dict(l=45, r=15, t=8, b=30), barmode="group",
                         yaxis_title="Points de vol",
                         legend=dict(orientation="h", y=1.12, font=dict(size=10)),
                         **_BASE_LAYOUT)

    return fig_ov, fig_ts, fig_sk


@callback(
    Output("iv-diag-summary", "children"),
    Output("iv-diag-table",   "data"),
    Input("selected-symbol",  "data"),
    Input("iv-interval",      "n_intervals"),
)
def refresh_iv_diagnostics(symbol, _):
    sym  = symbol or "ESTX50"
    diag = datasource.get_iv_diagnostics(sym)
    if diag.empty:
        return html.Small("Table iv_diagnostics vide pour ce sous-jacent.",
                          className="text-muted"), []

    n_tot  = len(diag)
    conv   = diag["converged"].astype(bool) if "converged" in diag.columns else None
    n_fail = int((~conv).sum()) if conv is not None else 0
    summary = [
        dbc.Badge(f"{n_tot} options solvées", color="info", className="me-2"),
        dbc.Badge(f"{n_tot - n_fail} convergées", color="success", className="me-2"),
        dbc.Badge(f"{n_fail} échec(s)", color="danger" if n_fail else "secondary"),
    ]
    failures = diag[~conv] if conv is not None and n_fail else diag.iloc[0:0]
    cols = ["expiry", "strike", "right", "mid_price", "forward",
            "implied_vol", "converged", "failure_reason"]
    data = failures[[c for c in cols if c in failures.columns]].round(4).to_dict("records")
    return summary, data


def _mb(value, label, css=""):
    from src.utils.fmt import fr_num
    value = fr_num(value)
    return html.Div([
        html.Div(str(value), className="metric-value"),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
