"""Page bonus — Variance swap & mini-VSTOXX : réplication model-free du log-contrat
sur strip SVI densifié, indice 30 j interpolé façon VIX, comparaison au VSTOXX officiel."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dash
from dash import html, dcc, dash_table, callback, Input, Output
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import numpy as np

from src.data.source import datasource
from src.data import no_data_alert
from src.pricing.varswap import variance_term_structure, interpolate_variance_index

dash.register_page(__name__, path="/varswap", name="Variance & VSTOXX (bonus)")

_LAYOUT = dict(template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
               font=dict(size=11))

layout = dbc.Container([
    html.Div([
        html.H2("Bonus — Variance swap & mini-VSTOXX (réplication model-free)"),
        html.P("Le strike d'un variance swap se réplique SANS MODÈLE par un strip "
               "d'options OTM pondérées en 1/K² (log-contrat, méthodologie VIX/VSTOXX). "
               "Notre grille n'a que ~5 strikes par maturité → on densifie le strip avec "
               "la surface SVI calibrée, on intègre, puis on interpole à 30 jours en "
               "variance totale. Sur ESTX50, le résultat se compare directement au "
               "VSTOXX officiel : la validation EXTERNE de toute la chaîne."),
    ], className="page-header"),

    dcc.Interval(id="vs-interval", interval=60000, n_intervals=0),

    dbc.Card([
        dbc.CardHeader("Formule (Demeterfi et al. 1999 / méthodologie VIX)"),
        dbc.CardBody(dbc.Row([
            dbc.Col(html.Div(dcc.Markdown(
                r"$$\sigma^2_{var} = \frac{2e^{rT}}{T}\sum_i \frac{\Delta K_i}{K_i^2}\,Q(K_i)"
                r" \;-\; \frac{1}{T}\left(\frac{F}{K_0}-1\right)^2$$",
                mathjax=True), className="formula-box"), width=8),
            dbc.Col(html.Small(
                "Q(K) = option OTM au strike K (put sous le forward, call au-dessus), "
                "pricée sur la tranche SVI · K₀ = premier strike ≤ F · aucun modèle de "
                "dynamique supposé, seulement l'absence d'arbitrage.",
                className="text-muted"), width=4),
        ])),
    ], className="card mb-3"),

    dbc.Row(id="vs-metrics", className="g-3 mb-2"),
    dbc.Row([
        dbc.Col([
            html.Label("VSTOXX officiel (saisie manuelle, ex. depuis stoxx.com) :",
                       className="text-muted small me-2"),
            dbc.Input(id="vs-official", type="number", step=0.1, placeholder="ex. 18.5",
                      style={"maxWidth": "140px", "display": "inline-block"},
                      className="dash-input"),
        ], width=6),
        dbc.Col(html.Div(id="vs-gap"), width=6),
    ], className="mb-3 align-items-center"),

    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Term structure : vol de variance swap vs vol ATM "
                           "(l'écart = prime de convexité, payée pour le skew)"),
            dbc.CardBody(dcc.Graph(id="vs-term-fig", config={"displayModeBar": False}),
                         className="p-2"),
        ], className="card h-100"), width=7),
        dbc.Col(dbc.Card([
            dbc.CardHeader("Strip répliquant (tranche ~30 j) — contributions ΔK/K²·Q(K)"),
            dbc.CardBody(dcc.Graph(id="vs-strip-fig", config={"displayModeBar": False}),
                         className="p-2"),
        ], className="card h-100"), width=5),
    ], className="g-2 mb-3"),

    dbc.Card([
        dbc.CardHeader("Détail par maturité"),
        dbc.CardBody(dash_table.DataTable(
            id="vs-table",
            columns=[
                {"name": "Expiry",        "id": "expiry"},
                {"name": "T (années)",    "id": "maturity_years", "type": "numeric",
                 "format": {"specifier": ".3f"}},
                {"name": "Forward",       "id": "forward", "type": "numeric",
                 "format": {"specifier": ",.1f"}},
                {"name": "K_var (vol %)", "id": "vol_strike", "type": "numeric",
                 "format": {"specifier": ".2f"}},
                {"name": "σ ATM (%)",     "id": "atm_vol", "type": "numeric",
                 "format": {"specifier": ".2f"}},
                {"name": "Prime convexité (pts)", "id": "convexity_premium",
                 "type": "numeric", "format": {"specifier": ".2f"}},
                {"name": "Strikes du strip", "id": "n_strikes"},
            ],
            style_header={"textTransform": "none"},
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "padding": "4px", "fontSize": "12px"},
        ), className="p-2"),
    ], className="card"),
], fluid=True)


def _compute(sym: str):
    rate = 0.025
    try:
        from src.utils.config import load_config
        rate = load_config("pricing").get("risk_free_rate", {}).get("value", 0.025)
    except Exception:
        pass
    params = datasource.get_surface_parameters(sym)
    fwd = datasource.get_forward_curve(sym)
    term = variance_term_structure(params, fwd, rate)
    idx = interpolate_variance_index(term, 30)
    return term, idx


@callback(
    Output("vs-metrics",   "children"),
    Output("vs-term-fig",  "figure"),
    Output("vs-strip-fig", "figure"),
    Output("vs-table",     "data"),
    Input("vs-interval",   "n_intervals"),
    Input("selected-symbol", "data"),
)
def refresh_varswap(_, symbol):
    sym = symbol or "ESTX50"
    empty = go.Figure()
    term, idx = _compute(sym)
    if not term or idx is None:
        return [dbc.Col(no_data_alert(sym), width=12)], empty, empty, []

    label = "Mini-VSTOXX maison (30j)" if sym == "ESTX50" else f"Indice de variance 30j — {sym}"
    near = min(term, key=lambda r: abs(r.maturity_years - 30 / 365))
    metrics = [
        dbc.Col(_mb(f"{idx['index']:.2f}", label, "positive"), width=3),
        dbc.Col(_mb(f"{near.atm_vol * 100:.2f} %", "σ ATM tranche ~30j (repère)"), width=3),
        dbc.Col(_mb(f"+{near.convexity_premium * 100:.2f} pts",
                    "Prime de convexité (le prix du skew)", "info"), width=3),
        dbc.Col(_mb(f"{idx['t1']} ↔ {idx['t2']}" + (" (clampé)" if idx["clamped"] else ""),
                    "Maturités d'interpolation"), width=3),
    ]

    # Term structure : K_var vs ATM
    fig_term = go.Figure()
    days = [r.maturity_years * 365 for r in term]
    fig_term.add_trace(go.Scatter(x=days, y=[r.vol_strike * 100 for r in term],
                                  name="vol de variance swap (K_var)",
                                  mode="lines+markers", line=dict(color="#0969da", width=2)))
    fig_term.add_trace(go.Scatter(x=days, y=[r.atm_vol * 100 for r in term],
                                  name="vol ATM (SVI)", mode="lines+markers",
                                  line=dict(color="#57606a", dash="dash")))
    fig_term.add_vline(x=30, line_dash="dot", line_color="#1a7f37",
                       annotation_text="30j (indice)")
    fig_term.update_layout(height=330, margin=dict(l=45, r=15, t=8, b=35),
                           xaxis_title="Jours à expiration", yaxis_title="Vol (%)",
                           legend=dict(orientation="h", y=1.12, font=dict(size=10)),
                           **_LAYOUT)

    # Strip de la tranche la plus proche de 30j
    fig_strip = go.Figure()
    if near.strikes is not None:
        fig_strip.add_trace(go.Bar(x=near.strikes, y=near.contributions * 1e4,
                                   marker_color="#0969da",
                                   hovertemplate="K=%{x:,.0f}<br>%{y:.3f} bp<extra></extra>"))
        fig_strip.add_vline(x=near.forward, line_dash="dash", line_color="#cf222e",
                            annotation_text=f"F = {near.forward:,.0f}")
    fig_strip.update_layout(height=330, margin=dict(l=45, r=15, t=8, b=35),
                            xaxis_title="Strike", yaxis_title="Contribution (bp de variance)",
                            showlegend=False, **_LAYOUT)

    rows = [{"expiry": r.expiry, "maturity_years": r.maturity_years,
             "forward": r.forward, "vol_strike": r.vol_strike * 100,
             "atm_vol": r.atm_vol * 100,
             "convexity_premium": r.convexity_premium * 100,
             "n_strikes": r.n_strikes} for r in term]

    return metrics, fig_term, fig_strip, rows


@callback(
    Output("vs-gap", "children"),
    Input("vs-official", "value"),
    Input("selected-symbol", "data"),
)
def show_gap(official, symbol):
    sym = symbol or "ESTX50"
    if not official:
        return html.Small("Saisis le niveau officiel pour mesurer l'écart "
                          "(données différées + grille réduite → 1-2 pts attendus).",
                          className="text-muted")
    _, idx = _compute(sym)
    if idx is None:
        return None
    gap = idx["index"] - float(official)
    color = "success" if abs(gap) <= 2 else "warning" if abs(gap) <= 4 else "danger"
    verdict = ("chaîne quotes→IV→surface VALIDÉE par l'extérieur" if abs(gap) <= 2
               else "écart à investiguer (heure de calcul ? grille ?)")
    return dbc.Alert(
        f"Maison {idx['index']:.2f} vs officiel {float(official):.2f} → "
        f"écart {gap:+.2f} pts — {verdict}",
        color=color, className="py-2 mb-0")


def _mb(value, label, css=""):
    return html.Div([
        html.Div(str(value), className="metric-value", style={"fontSize": "1.0rem"}),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
