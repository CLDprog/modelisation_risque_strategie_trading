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

    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Historique du signal — indice de variance 30j par cycle "
                           "(table variance_history, append-only)"),
            dbc.CardBody(dcc.Graph(id="vs-hist-fig", config={"displayModeBar": False}),
                         className="p-2"),
        ], className="card h-100"), width=6),
        dbc.Col(dbc.Card([
            dbc.CardHeader("Strip EXÉCUTABLE — la grille réelle au bid/ask (tranche ~30j)"),
            dbc.CardBody([
                html.Div(id="vs-exec-metrics", className="mb-2"),
                dash_table.DataTable(
                    id="vs-exec-table",
                    columns=[
                        {"name": "Strike", "id": "strike", "type": "numeric",
                         "format": {"specifier": ",.0f"}},
                        {"name": "OTM", "id": "right"},
                        {"name": "Bid", "id": "bid", "type": "numeric",
                         "format": {"specifier": ".2f"}},
                        {"name": "Ask", "id": "ask", "type": "numeric",
                         "format": {"specifier": ".2f"}},
                        {"name": "Poids ΔK/K²", "id": "weight", "type": "numeric",
                         "format": {"specifier": ".3g"}},
                        {"name": "Contrib. (bp var)", "id": "contrib", "type": "numeric",
                         "format": {"specifier": ".1f"}},
                    ],
                    style_header={"textTransform": "none"},
                    style_table={"overflowX": "auto"},
                    style_cell={"textAlign": "center", "padding": "3px", "fontSize": "12px"},
                ),
                html.Small("Le strip théorique (SVI, 400 strikes) donne la juste valeur ; "
                           "le strip exécutable (notre grille de ~5 strikes, prix réels) "
                           "donne ce qu'on PAIE en croisant le spread — l'écart mid↔exécutable "
                           "= coût de réplication + erreur de troncature de la grille.",
                           className="text-muted d-block mt-1"),
            ], className="p-2"),
        ], className="card h-100"), width=6),
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
    Output("vs-hist-fig",     "figure"),
    Output("vs-exec-metrics", "children"),
    Output("vs-exec-table",   "data"),
    Input("vs-interval",      "n_intervals"),
    Input("selected-symbol",  "data"),
)
def refresh_desk_extras(_, symbol):
    import math as _m
    sym = symbol or "ESTX50"

    # Historique du signal (se remplit à chaque cycle du collecteur)
    hist = datasource.get_variance_history(sym, days=7)
    fig_h = go.Figure()
    if not hist.empty:
        fig_h.add_trace(go.Scatter(
            x=hist["ts"], y=hist["var_index_30d"], mode="lines+markers",
            line=dict(color="#0969da", width=2), marker=dict(size=6)))
        fig_h.update_yaxes(title="Indice 30j (pts de vol)")
    else:
        fig_h.add_annotation(x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False,
                             text="L'historique se remplit à chaque cycle du collecteur",
                             font=dict(size=12, color="#57606a"))
    fig_h.update_layout(height=300, margin=dict(l=45, r=15, t=8, b=35),
                        xaxis_title="Cycle (UTC)", **_LAYOUT)

    # Strip EXÉCUTABLE : la vraie grille (~5 strikes) au bid/ask, tranche ~30j
    rate = 0.025
    try:
        from src.utils.config import load_config
        rate = load_config("pricing").get("risk_free_rate", {}).get("value", 0.025)
    except Exception:
        pass
    chain = datasource.get_option_chain(sym)
    rows, metrics = [], None
    if not chain.empty and "days_to_expiry" in chain.columns:
        u = chain[chain["is_usable"]] if "is_usable" in chain.columns else chain
        u = u.dropna(subset=["strike", "forward", "maturity_years"])
        if not u.empty:
            nearest = u.loc[(u["days_to_expiry"] - 30).abs().idxmin(), "days_to_expiry"]
            sl = u[u["days_to_expiry"] == nearest]
            fwd = float(sl["forward"].iloc[0])
            T = float(sl["maturity_years"].iloc[0])
            # options OTM : put sous F, call au-dessus — une ligne par strike
            otm = sl[((sl["right"] == "P") & (sl["strike"] < fwd)) |
                     ((sl["right"] == "C") & (sl["strike"] >= fwd))]
            otm = otm.sort_values("strike").drop_duplicates(subset="strike")
            if len(otm) >= 3:
                ks = otm["strike"].to_numpy(dtype=float)
                dk = np.empty_like(ks)
                dk[1:-1] = (ks[2:] - ks[:-2]) / 2
                dk[0], dk[-1] = ks[1] - ks[0], ks[-1] - ks[-2]
                wgt = (2 * _m.exp(rate * T) / T) * dk / ks ** 2
                k0 = ks[max(int(np.searchsorted(ks, fwd)) - 1, 0)]
                adj = (fwd / k0 - 1.0) ** 2 / T

                def kvar(prices):
                    v = float(np.nansum(wgt * prices) - adj)
                    return 100 * _m.sqrt(max(v, 1e-10))

                mid = otm["mid_price"].to_numpy(dtype=float)
                bid = otm["bid"].to_numpy(dtype=float)
                ask = otm["ask"].to_numpy(dtype=float)
                kv_mid, kv_buy, kv_sell = kvar(mid), kvar(ask), kvar(bid)
                metrics = [
                    dbc.Badge(f"K_var mid : {kv_mid:.2f}", color="info", className="me-2"),
                    dbc.Badge(f"acheter (ask) : {kv_buy:.2f}", color="danger", className="me-2"),
                    dbc.Badge(f"vendre (bid) : {kv_sell:.2f}", color="success", className="me-2"),
                    dbc.Badge(f"spread : {kv_buy - kv_sell:.2f} pts", color="secondary"),
                ]
                for i, (_, r) in enumerate(otm.iterrows()):
                    rows.append({"strike": r["strike"], "right": r["right"],
                                 "bid": r.get("bid"), "ask": r.get("ask"),
                                 "weight": float(wgt[i]),
                                 "contrib": float(wgt[i] * (r.get("mid_price") or 0)) * 1e4})
    return fig_h, metrics, rows


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
