"""Page 9 — Moteur de Scénarios, symbol-aware avec callbacks dynamiques."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dash
from dash import html, dcc, dash_table, callback, Input, Output
import dash_bootstrap_components as dbc
import plotly.graph_objects as go

from src.data.source import datasource
from src.data import no_data_alert

dash.register_page(__name__, path="/scenarios", name="Scénarios")

layout = dbc.Container([
    html.Div([
        html.H2("Moteur de Scénarios"),
        html.P("Repricing complet du portefeuille sous chocs de spot, volatilité et temps. Eq. 19."),
    ], className="page-header"),

    dcc.Interval(id="scen-interval", interval=60000, n_intervals=0),

    dbc.Card([
        dbc.CardHeader("Formule d'approximation par les Greeks (Eq. 19)"),
        dbc.CardBody([
            html.Div(dcc.Markdown(
                r"$$\delta P \approx \Delta \cdot \delta S + \frac{1}{2}\Gamma \cdot \delta S^2 + \nu \cdot \delta\sigma + \Theta \cdot \delta t$$",
                mathjax=True), className="formula-box"),
            dbc.Alert(
                "Repricing complet (Black-Scholes sous paramètres choqués). "
                "L'approximation Greeks est utilisée pour la surveillance intraday rapide uniquement.",
                color="dark", className="mt-3 mb-0 border border-secondary",
            ),
        ]),
    ], className="card mb-4"),

    # ── Vue BOOK (positions réelles, hedge inclus) — pertinente pour la stratégie ──
    dbc.Card([
        dbc.CardHeader("Scénarios au niveau BOOK — positions réelles, delta-hedge inclus"),
        dbc.CardBody([
            dbc.Row(id="scen-book-metrics", className="g-3 mb-3"),
            dcc.Graph(id="scen-book-bar", config={"displayModeBar": False}),
            html.Small(
                "Repricing du book entier (tous les straddles, agrégés) sous chaque choc, "
                "via Eq.19. « Sans hedge » = options seules. « Delta-neutre » = book ramené "
                "à Δ≈0 par le future indice (ce que la stratégie maintient chaque jour). "
                "L'écart sur les chocs de SPOT montre l'effet du hedge : il annule le "
                "directionnel et ne laisse que le gamma. Sur vol/temps, le hedge n'a aucun "
                "effet (le future n'a ni vega ni theta) — c'est là qu'est ton vrai pari.",
                className="text-muted d-block mt-2"),
        ]),
    ], className="card mb-4"),

    dbc.Row(id="scen-metrics", className="g-3 mb-4"),

    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("PnL total par scénario (sous-jacent sélectionné)"),
            dbc.CardBody(dcc.Graph(id="scen-bar", config={"displayModeBar": False})),
        ], className="card"), width=6),
        dbc.Col(dbc.Card([
            dbc.CardHeader("PnL par scénario × contrat"),
            dbc.CardBody(dcc.Graph(id="scen-heat", config={"displayModeBar": False})),
        ], className="card"), width=6),
    ], className="mb-4"),

    dbc.Card([
        dbc.CardHeader("Grille de scénarios (versionnée)"),
        dbc.CardBody(dash_table.DataTable(
            id="scen-table",
            columns=[
                {"name": "Scénario ID",    "id": "scenario_id"},
                {"name": "Description",    "id": "description"},
                {"name": "Choc spot (%)",  "id": "spot_shift_pct"},
                {"name": "Choc vol (pts)", "id": "vol_shift_abs"},
                {"name": "Roll temps (j)", "id": "time_roll_days"},
                {"name": "PnL total ($)",  "id": "scenario_pnl"},
            ],
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "padding": "8px"},
            style_data_conditional=[
                {"if": {"filter_query": "{scenario_pnl} < 0", "column_id": "scenario_pnl"},
                 "color": "#cf222e"},
                {"if": {"filter_query": "{scenario_pnl} >= 0", "column_id": "scenario_pnl"},
                 "color": "#1a7f37"},
            ],
            sort_action="native",
        )),
    ], className="card"),
], fluid=True)


def _bmb(value, label, css=""):
    from src.utils.fmt import fr_num
    value = fr_num(value)
    return dbc.Col(html.Div([
        html.Div(str(value), className="metric-value", style={"fontSize": "1.0rem"}),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}"), width=3)


@callback(
    Output("scen-book-metrics", "children"),
    Output("scen-book-bar",     "figure"),
    Input("scen-interval",      "n_intervals"),
)
def refresh_book_scenarios(_):
    """Scénarios agrégés sur le book réel (blotter), avec/sans delta-hedge.
    Eq.19 : δP = Δ€·s + ½Γ€·s² + ν€·(Δσ en pts) + Θ€·Δt. Le hedge delta-neutre
    retire le terme directionnel (Δ€·s), laissant le gamma/vega/theta."""
    from pages.trading import _enrich_positions
    from src.utils.config import load_config
    fig = go.Figure()
    blotter = datasource.get_blotter()
    positions = blotter.get("positions", [])
    idx = datasource.get_spot("ESTX50")
    if not positions or not idx:
        return ([dbc.Col(dbc.Alert("Aucune position dans le book — passe tes straddles "
                                   "d'abord (page Trading).", color="info"), width=12)], fig)
    enr = _enrich_positions(positions, idx)
    opts = [r for r in enr if r.get("right") in ("C", "P")]
    opt_net_delta = sum(r.get("pos_eur_delta") or 0 for r in opts)

    def leg(r, s, dvol_pts, days):
        return ((r.get("pos_eur_delta") or 0) * s
                + 0.5 * (r.get("pos_eur_gamma") or 0) * s * s
                + (r.get("pos_eur_vega") or 0) * dvol_pts
                + (r.get("pos_eur_theta") or 0) * days)

    names, no_h, hedged = [], [], []
    for sc in load_config("scenarios").get("scenarios", []):
        s = float(sc.get("spot_shift_pct") or 0)
        dvol_pts = float(sc.get("vol_shift_abs") or 0) * 100      # 0.10 → 10 pts de vol
        days = float(sc.get("time_roll_days") or 0)
        opt_pnl = sum(leg(r, s, dvol_pts, days) for r in opts)
        names.append(sc.get("description") or sc.get("scenario_id"))
        no_h.append(opt_pnl)
        hedged.append(opt_pnl - opt_net_delta * s)                # delta neutralisé

    fig.add_trace(go.Bar(x=names, y=no_h, name="Sans hedge (options seules)",
                         marker_color="#bc4c00"))
    fig.add_trace(go.Bar(x=names, y=hedged, name="Delta-neutre (book hedgé)",
                         marker_color="#0969da"))
    fig.update_layout(barmode="group", height=340, template="plotly_white",
                      paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
                      margin=dict(l=50, r=15, t=10, b=90), font=dict(size=10),
                      yaxis_title="P&L book (€)", xaxis=dict(tickangle=-35, tickfont=dict(size=9)),
                      legend=dict(orientation="h", y=1.12, font=dict(size=10)))
    worst = min(hedged) if hedged else 0
    best = max(hedged) if hedged else 0
    iw = hedged.index(worst) if hedged else 0
    ib = hedged.index(best) if hedged else 0
    metrics = [
        _bmb(f"{worst:+,.0f} €", f"Pire (hedgé) : {names[iw]}" if hedged else "—", "negative"),
        _bmb(f"{best:+,.0f} €", f"Meilleur (hedgé) : {names[ib]}" if hedged else "—", "positive"),
        _bmb(f"{len(opts)} jambes", "Options dans le book", "info"),
        _bmb(f"{opt_net_delta:+,.0f} €", "Δ€ net options (avant hedge)", "warning"),
    ]
    return metrics, fig


@callback(
    Output("scen-metrics", "children"),
    Output("scen-bar",     "figure"),
    Output("scen-heat",    "figure"),
    Output("scen-table",   "data"),
    Input("scen-interval", "n_intervals"),
    Input("selected-symbol","data"),
)
def refresh_scenarios(_, symbol):
    sym  = symbol or "SPY"
    scen = datasource.get_scenarios(sym)
    src  = datasource.get_data_source_label()

    if scen.empty:
        empty = go.Figure()
        if not datasource.is_connected:
            alert = no_data_alert(sym)
            return [dbc.Col(alert, width=12)], empty, empty, []
        return [dbc.Col(dbc.Alert(
            "Aucun scénario disponible. Lancez le pipeline EOD ou ajoutez des positions.",
            color="info"), width=12)], empty, empty, []

    pnl_col = "scenario_pnl_full" if "scenario_pnl_full" in scen.columns else "scenario_pnl"

    summary = (
        scen.groupby(["scenario_id", "description", "spot_shift_pct",
                      "vol_shift_abs", "time_roll_days"])[pnl_col]
        .sum().reset_index()
        .rename(columns={pnl_col: "scenario_pnl"})
        .sort_values("scenario_pnl")
    )

    worst = summary.iloc[0]
    best  = summary.iloc[-1]
    n_pos = len(scen["contract_key"].unique()) if "contract_key" in scen.columns else 0

    metrics = [
        dbc.Col(_mb(f"${worst['scenario_pnl']:,.0f}", "Pire scénario",
                    "negative"),                                           width=3),
        dbc.Col(_mb(f"${best['scenario_pnl']:,.0f}", "Meilleur scénario",
                    "positive"),                                           width=3),
        dbc.Col(_mb(str(len(summary)),  "Scénarios exécutés"),             width=3),
        dbc.Col(_mb(str(n_pos),         "Positions"),                      width=3),
    ]

    # Bar chart
    fig_bar = go.Figure(go.Bar(
        x=summary["description"], y=summary["scenario_pnl"],
        marker_color=["#1a7f37" if v >= 0 else "#cf222e" for v in summary["scenario_pnl"]],
        text=[f"${v:,.0f}" for v in summary["scenario_pnl"]],
        textposition="outside",
    ))
    fig_bar.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        margin=dict(l=40, r=20, t=20, b=120),
        xaxis=dict(tickangle=-30), yaxis_title="PnL ($)", height=380,
    )

    # Heatmap
    fig_heat = go.Figure()
    if "contract_key" in scen.columns:
        try:
            pivot = scen.pivot_table(
                index="contract_key", columns="description",
                values=pnl_col, aggfunc="sum"
            )
            fig_heat.add_trace(go.Heatmap(
                z=pivot.values, x=pivot.columns.tolist(), y=pivot.index.tolist(),
                colorscale="RdYlGn",
                text=[[f"${v:,.0f}" for v in row] for row in pivot.values],
                texttemplate="%{text}", textfont=dict(size=10),
                colorbar=dict(title="PnL ($)", tickfont=dict(color="#1f2328")),
            ))
        except Exception:
            pass
    fig_heat.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        margin=dict(l=160, r=20, t=20, b=120),
        xaxis=dict(tickangle=-30), height=320,
    )

    return metrics, fig_bar, fig_heat, summary.round(4).to_dict("records")


def _mb(value, label, css=""):
    from src.utils.fmt import fr_num
    value = fr_num(value)
    return html.Div([
        html.Div(str(value), className="metric-value"),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
