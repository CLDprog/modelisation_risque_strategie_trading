"""Page bonus — Trade de dispersion : vendre la corrélation implicite (Eq.23).
Short vol indice / long vol composantes en vega-weighted ; P&L approché sous
scénarios de corrélation réalisée ; construction du panier depuis nos greeks €."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math

import dash
from dash import html, dcc, dash_table, callback, Input, Output
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import numpy as np
import pandas as pd

from src.data.source import datasource
from src.data import no_data_alert
from src.risk.dispersion import _atm_iv_by_tenor

dash.register_page(__name__, path="/dispersion", name="Dispersion (bonus)")

_LAYOUT = dict(template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
               font=dict(size=11))

layout = dbc.Container([
    html.Div([
        html.H2("Bonus — Trade de dispersion : vendre la corrélation implicite"),
        html.P("L'indice « surpaye » structurellement la corrélation : sa vol implicite "
               "embarque un ρ̄ supérieur à la corrélation qui se réalise en moyenne. Le "
               "trade classique de prop : VENDRE la vol de l'indice, ACHETER la vol des "
               "composantes (vega-weighted) → on est short ρ̄, à peu près vega-flat. "
               "Tout vient de nos tables : ρ̄ d'Eq.23, IV ATM de la grille, vega € des "
               "greeks monétisés."),
    ], className="page-header"),

    dcc.Interval(id="dt-interval", interval=60000, n_intervals=0),

    dbc.Row([
        dbc.Col([
            html.Label("Tenor", className="text-muted small"),
            dcc.Dropdown(id="dt-tenor", clearable=False, style={"fontSize": "12px"}),
        ], width=2),
        dbc.Col([
            html.Label("Vega notionnel (€ par point de vol, jambe indice)",
                       className="text-muted small"),
            dcc.Slider(id="dt-vega", min=500, max=10_000, step=500, value=2_000,
                       marks={i: f"{i//1000}k" for i in range(1000, 10001, 3000)},
                       tooltip={"placement": "bottom"}),
        ], width=5),
    ], className="g-2 mb-3"),

    dbc.Row(id="dt-metrics", className="g-3 mb-3"),

    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Niveaux d'entrée — ρ̄ implicite par tenor (Eq.23)"),
            dbc.CardBody(dcc.Graph(id="dt-rho-fig", config={"displayModeBar": False}),
                         className="p-2"),
        ], className="card h-100"), width=5),
        dbc.Col(dbc.Card([
            dbc.CardHeader("P&L approché vs corrélation RÉALISÉE (vols inchangées)"),
            dbc.CardBody([
                dcc.Graph(id="dt-pnl-fig", config={"displayModeBar": False}),
                html.Small("Approximation au 1er ordre : seules les jambes indice bougent "
                           "quand ρ réalisé ≠ ρ̄ d'entrée, σ_I(ρ) = √(Σw²σ² + ρ·[(Σwσ)² − "
                           "Σw²σ²]). Breakeven exactement au ρ̄ d'entrée. Le repricing "
                           "complet (Scénarios) reste la source de vérité.",
                           className="text-muted"),
            ], className="p-2"),
        ], className="card h-100"), width=7),
    ], className="g-2 mb-3"),

    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Historique du signal — ρ̄ implicite par cycle "
                           "(table dispersion_history, append-only)"),
            dbc.CardBody(dcc.Graph(id="dt-hist-fig", config={"displayModeBar": False}),
                         className="p-2"),
        ], className="card h-100"), width=6),
        dbc.Col(dbc.Card([
            dbc.CardHeader("Risque & coûts du package (vega-flat par construction)"),
            dbc.CardBody(html.Div(id="dt-risk-panel"), className="p-2"),
        ], className="card h-100"), width=6),
    ], className="g-2 mb-3"),

    dbc.Card([
        dbc.CardHeader("Construction du panier (jambe composantes, vega-weighted, "
                       "poids Eq.23)"),
        dbc.CardBody(dash_table.DataTable(
            id="dt-basket-table",
            columns=[
                {"name": "Composante", "id": "symbol"},
                {"name": "σ ATM (%)",  "id": "iv", "type": "numeric",
                 "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ".1f"}},
                {"name": "Poids w",    "id": "weight", "type": "numeric",
                 "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ".3f"}},
                {"name": "Vega cible (€/pt)", "id": "vega_target", "type": "numeric",
                 "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ",.0f"}},
                {"name": "Vega ATM €/contrat", "id": "vega_per_contract", "type": "numeric",
                 "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ",.1f"}},
                {"name": "≈ Contrats", "id": "contracts", "type": "numeric",
                 "format": {"locale": {"group": " ", "decimal": ","}, "specifier": ",.1f"}},
            ],
            style_header={"textTransform": "none"},
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "padding": "4px", "fontSize": "12px"},
            sort_action="native", page_size=15,
        ), className="p-2"),
    ], className="card"),
], fluid=True)


def _sigma_index(rho: np.ndarray, s2: float, avg: float) -> np.ndarray:
    """σ_I(ρ) sous corrélation constante : Eq.23 inversée. s2 = Σw²σ², avg = Σwσ."""
    return np.sqrt(np.maximum(s2 + rho * (avg ** 2 - s2), 1e-12))


def _trade_data(tenor: int):
    """ρ̄ d'entrée + IV ATM des composantes + vegas € au tenor choisi."""
    disp = datasource.get_dispersion()
    if disp.empty or tenor not in set(disp["tenor_days"]):
        return None
    row = disp[disp["tenor_days"] == tenor].iloc[0]

    iv = datasource._read_analytics("iv_points")
    comp, atm = {}, {}

    def _atm_row(grp):
        """Ligne ATM la plus proche du tenor : vega/gamma/theta € + demi-spread."""
        g = grp.dropna(subset=["eur_vega", "log_moneyness", "days_to_expiry"])
        if g.empty:
            return None
        nearest = g.loc[(g["days_to_expiry"] - tenor).abs().idxmin(), "days_to_expiry"]
        sl = g[g["days_to_expiry"] == nearest]
        r = sl.loc[sl["log_moneyness"].abs().idxmin()]
        half = None
        if pd.notna(r.get("bid")) and pd.notna(r.get("ask")):
            half = (float(r["ask"]) - float(r["bid"])) / 2 * float(r.get("multiplier", 100))
        return {"vega": float(r["eur_vega"]),
                "gamma": float(r.get("eur_gamma") or 0),
                "theta": float(r.get("eur_theta") or 0),
                "half_spread": half}

    if not iv.empty:
        for sym, grp in iv.groupby("underlying_symbol"):
            if sym == "ESTX50":
                idx_atm = _atm_row(grp)
                if idx_atm:
                    atm["ESTX50"] = idx_atm
                continue
            ivs = _atm_iv_by_tenor(grp, [tenor])
            if tenor not in ivs:
                continue
            comp[sym] = ivs[tenor]
            a = _atm_row(grp)
            if a:
                atm[sym] = a
    if len(comp) < 5 or "ESTX50" not in atm:
        return None

    syms = sorted(comp)
    vols = np.array([comp[s] for s in syms])
    w = np.full(len(syms), 1.0 / len(syms))
    s2 = float(np.sum((w * vols) ** 2))
    avg = float(np.sum(w * vols))
    return {"row": row, "syms": syms, "vols": vols, "w": w, "s2": s2, "avg": avg,
            "atm": atm,
            "rho_entry": float(row["implied_correlation"]),
            "index_iv": float(row["index_iv"])}


@callback(
    Output("dt-tenor", "options"),
    Output("dt-tenor", "value"),
    Input("dt-interval", "n_intervals"),
)
def init_tenors(_):
    disp = datasource.get_dispersion()
    if disp.empty:
        return [], None
    tenors = sorted(disp["tenor_days"].unique())
    opts = [{"label": f"{t} j", "value": int(t)} for t in tenors]
    default = 91 if 91 in tenors else int(tenors[0])
    return opts, default


@callback(
    Output("dt-metrics",      "children"),
    Output("dt-rho-fig",      "figure"),
    Output("dt-pnl-fig",      "figure"),
    Output("dt-basket-table", "data"),
    Output("dt-hist-fig",     "figure"),
    Output("dt-risk-panel",   "children"),
    Input("dt-tenor",         "value"),
    Input("dt-vega",          "value"),
)
def refresh_trade(tenor, vega_notional):
    empty = go.Figure()
    if not tenor:
        return [dbc.Col(no_data_alert("ESTX50"), width=12)], empty, empty, [], empty, None
    data = _trade_data(int(tenor))
    if data is None:
        return ([dbc.Col(dbc.Alert("Pas assez de composantes à ce tenor.",
                                   color="warning"), width=12)],
                empty, empty, [], empty, None)

    vega = float(vega_notional or 2000)
    rho0, sig_i = data["rho_entry"], data["index_iv"]
    spread = data["avg"] - sig_i

    # Sensibilité « corr-vega » : dP&L/dρ = −vega·dσ_I/dρ·100 (en pts de vol)
    dsig_drho = (data["avg"] ** 2 - data["s2"]) / (2 * sig_i)
    corr_vega = vega * dsig_drho * 100

    metrics = [
        dbc.Col(_mb(f"{rho0:.0%}", "ρ̄ implicite (niveau d'entrée)", "info"), width=2),
        dbc.Col(_mb(f"{sig_i:.1%}", "IV indice (jambe SHORT)"), width=2),
        dbc.Col(_mb(f"{data['avg']:.1%}", "IV panier (jambe LONG)"), width=2),
        dbc.Col(_mb(f"+{spread * 100:.1f} pts", "Spread de dispersion encaissé"), width=2),
        dbc.Col(_mb(f"{corr_vega:,.0f} € / 0.01 ρ", "Sensibilité corrélation", "warning"),
                width=4),
    ]

    # 1) ρ̄ par tenor avec le tenor sélectionné en évidence
    disp = datasource.get_dispersion().sort_values("tenor_days")
    fig_rho = go.Figure(go.Bar(
        x=[f"{t}j" for t in disp["tenor_days"]],
        y=disp["implied_correlation"],
        marker_color=["#0a3069" if int(t) == int(tenor) else "#0969da"
                      for t in disp["tenor_days"]],
        hovertemplate="ρ̄ = %{y:.2f}<extra></extra>"))
    fig_rho.add_hline(y=rho0, line_dash="dot", line_color="#cf222e",
                      annotation_text=f"entrée {rho0:.0%}")
    fig_rho.update_layout(height=300, margin=dict(l=45, r=15, t=8, b=30),
                          yaxis=dict(title="ρ̄", range=[0, 1]), **_LAYOUT)

    # 2) P&L vs corrélation réalisée (short ρ : on gagne si ρ_réel < ρ̄ d'entrée)
    rho_grid = np.linspace(0.0, 1.0, 101)
    pnl = -vega * (_sigma_index(rho_grid, data["s2"], data["avg"]) - sig_i) * 100
    fig_pnl = go.Figure()
    fig_pnl.add_trace(go.Scatter(
        x=rho_grid, y=pnl, mode="lines", line=dict(color="#0969da", width=2),
        fill="tozeroy", fillcolor="rgba(9,105,218,0.07)"))
    fig_pnl.add_vline(x=rho0, line_dash="dash", line_color="#cf222e",
                      annotation_text=f"breakeven = ρ̄ entrée ({rho0:.0%})")
    fig_pnl.add_hline(y=0, line_color="#57606a", line_width=1)
    fig_pnl.update_layout(height=300, margin=dict(l=45, r=15, t=8, b=35),
                          xaxis_title="Corrélation réalisée ρ",
                          yaxis_title=f"P&L (€) — vega {vega:,.0f} €/pt", **_LAYOUT)

    # 3) Panier vega-weighted + greeks/coûts du package
    rows = []
    pkg = {"gamma": 0.0, "theta": 0.0, "cost": 0.0, "n_spread": 0}
    for s, v, wi in zip(data["syms"], data["vols"], data["w"]):
        vt = vega * wi * v / data["avg"]          # répartition vega ∝ w·σ (poids Eq.23)
        a = data["atm"].get(s)
        vpc = a["vega"] if a else None
        contracts = (vt / vpc) if vpc else None
        rows.append({"symbol": s, "iv": v * 100, "weight": wi,
                     "vega_target": vt,
                     "vega_per_contract": vpc,
                     "contracts": contracts})
        if a and contracts:
            scale = contracts
            pkg["gamma"] += scale * a["gamma"]
            pkg["theta"] += scale * a["theta"]
            if a["half_spread"] is not None:
                pkg["cost"] += abs(scale) * a["half_spread"]
                pkg["n_spread"] += 1
    rows.sort(key=lambda r: -(r["vega_target"] or 0))

    # Jambe indice (SHORT vega notional)
    ia = data["atm"]["ESTX50"]
    idx_contracts = vega / ia["vega"] if ia["vega"] else 0.0
    pkg["gamma"] -= idx_contracts * ia["gamma"]
    pkg["theta"] -= idx_contracts * ia["theta"]
    if ia["half_spread"] is not None:
        pkg["cost"] += idx_contracts * ia["half_spread"]
        pkg["n_spread"] += 1
    breakeven_shift = pkg["cost"] / corr_vega if corr_vega else 0.0

    # 4) Historique de ρ̄ (append-only, par cycle)
    hist = datasource.get_dispersion_history(days=7)
    fig_hist = go.Figure()
    if not hist.empty and "tenor_days" in hist.columns:
        h = hist[hist["tenor_days"] == int(tenor)]
        if not h.empty:
            fig_hist.add_trace(go.Scatter(
                x=h["ts"], y=h["implied_correlation"], mode="lines+markers",
                line=dict(color="#0969da", width=2), marker=dict(size=6)))
            fig_hist.add_hline(y=rho0, line_dash="dot", line_color="#cf222e",
                               annotation_text="niveau actuel")
    if not fig_hist.data:
        fig_hist.add_annotation(x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False,
                                text="L'historique se remplit à chaque cycle du collecteur",
                                font=dict(size=12, color="#57606a"))
    fig_hist.update_layout(height=280, margin=dict(l=45, r=15, t=8, b=35),
                           xaxis_title="Cycle (UTC)",
                           yaxis=dict(title="ρ̄", range=[0, 1]), **_LAYOUT)

    # 5) Panneau risque & coûts
    risk_panel = dbc.Row([
        dbc.Col(_mb("≈ 0 €/pt", "Vega net (par construction)", "positive"), width=6),
        dbc.Col(_mb(f"{pkg['gamma']:+,.0f} €", "Gamma € net du package",
                    "positive" if pkg["gamma"] >= 0 else "negative"), width=6),
        dbc.Col(_mb(f"{pkg['theta']:+,.0f} €/j", "Theta € net du package",
                    "positive" if pkg["theta"] >= 0 else "negative"), width=6),
        dbc.Col(_mb(f"{pkg['cost']:,.0f} € · ρ̄ +{breakeven_shift:.3f}",
                    f"Coût d'entrée (½ spread, {pkg['n_spread']} jambes) · "
                    "décalage du breakeven", "warning"), width=6),
    ], className="g-2")

    return metrics, fig_rho, fig_pnl, rows, fig_hist, risk_panel


def _mb(value, label, css=""):
    from src.utils.fmt import fr_num
    value = fr_num(value)
    return html.Div([
        html.Div(str(value), className="metric-value", style={"fontSize": "1.0rem"}),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
