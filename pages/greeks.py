"""Page 8 — Greeks & Risk : grille spec §5 (greeks bruts + € monétisés) + portefeuille."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dash
from dash import html, dcc, dash_table, callback, Input, Output, State, no_update
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import pandas as pd

from src.data.source import datasource
from src.data import no_data_alert

dash.register_page(__name__, path="/greeks", name="Greeks & Risk")

_FMT2 = {"specifier": ".2f"}
_FMT4 = {"specifier": ".4f"}

_GRID_COLUMNS = [
    {"name": ["", "Expiry"],          "id": "expiry"},
    {"name": ["", "Strike"],          "id": "strike",   "type": "numeric", "format": _FMT2},
    {"name": ["", "C/P"],             "id": "right"},
    {"name": ["Cotation", "Bid sz"],  "id": "bid_size", "type": "numeric",
     "format": {"specifier": ",.0f"}},
    {"name": ["Cotation", "Bid"],     "id": "bid",      "type": "numeric", "format": _FMT4},
    {"name": ["Cotation", "Mid"],     "id": "mid_price","type": "numeric", "format": _FMT4},
    {"name": ["Cotation", "Ask"],     "id": "ask",      "type": "numeric", "format": _FMT4},
    {"name": ["Cotation", "Ask sz"],  "id": "ask_size", "type": "numeric",
     "format": {"specifier": ",.0f"}},
    {"name": ["Volumétrie", "Volume"],"id": "volume",   "type": "numeric",
     "format": {"specifier": ",.0f"}},
    {"name": ["Volumétrie", "OI"],    "id": "open_interest", "type": "numeric",
     "format": {"specifier": ",.0f"}},
    {"name": ["", "IV σ"],            "id": "implied_vol", "type": "numeric", "format": _FMT4},
    {"name": ["Greeks bruts", "Δ"],   "id": "delta",    "type": "numeric", "format": _FMT4},
    {"name": ["Greeks bruts", "Γ"],   "id": "gamma",    "type": "numeric", "format": {"specifier": ".5f"}},
    {"name": ["Greeks bruts", "ν"],   "id": "vega",     "type": "numeric", "format": _FMT4},
    {"name": ["Greeks bruts", "Θ"],   "id": "theta",    "type": "numeric", "format": _FMT4},
    {"name": ["Greeks bruts", "ρ"],   "id": "rho",      "type": "numeric", "format": _FMT4},
    {"name": ["Greeks € (monétisés)", "Δ €"], "id": "eur_delta", "type": "numeric", "format": _FMT2},
    {"name": ["Greeks € (monétisés)", "Γ €"], "id": "eur_gamma", "type": "numeric", "format": _FMT2},
    {"name": ["Greeks € (monétisés)", "ν €"], "id": "eur_vega",  "type": "numeric", "format": _FMT2},
    {"name": ["Greeks € (monétisés)", "Θ €"], "id": "eur_theta", "type": "numeric", "format": _FMT2},
    {"name": ["Greeks € (monétisés)", "ρ €"], "id": "eur_rho",   "type": "numeric", "format": _FMT2},
    # Lecture desk : risque normalisé pour un mouvement de +1% du spot (convention
    # hedge fund / dealing desk ; cohérent avec le dollar-gamma /100 de la roadmap).
    {"name": ["Lecture desk (+1% spot)", "P&L Δ"],   "id": "pnl_delta_1pct",
     "type": "numeric", "format": _FMT2},
    {"name": ["Lecture desk (+1% spot)", "P&L Γ"],   "id": "pnl_gamma_1pct",
     "type": "numeric", "format": _FMT2},
    {"name": ["Lecture desk (+1% spot)", "var. Δ €"], "id": "shift_delta_1pct",
     "type": "numeric", "format": _FMT2},
    # « Le Reste » (demande du prof) : les greeks expliquent ~95 % du P&L —
    # Reste € = P&L exact par repricing complet à +1 % − (P&L Δ + P&L Γ).
    {"name": ["Lecture desk (+1% spot)", "Reste €"], "id": "reste_1pct",
     "type": "numeric", "format": _FMT2},
]

_POS_COLUMNS = [
    {"name": "Contrat",  "id": "contract_key"},
    {"name": "Qty",      "id": "quantity"},
    {"name": "Mult",     "id": "multiplier"},
    {"name": "IV σ",     "id": "implied_vol"},
    {"name": "Prix",     "id": "price"},
    {"name": "Δ portef.","id": "portfolio_delta"},
    {"name": "Γ portef.","id": "portfolio_gamma"},
    {"name": "ν portef.","id": "portfolio_vega"},
    {"name": "Θ portef.","id": "portfolio_theta"},
    {"name": "ρ portef.","id": "portfolio_rho"},
    {"name": "PnL",      "id": "pnl_approx"},
]

def _pricing_context(sym):
    """(taux, américain ?) du produit — taux de la config, exercice selon sec_type."""
    from src.utils.config import load_config
    rate = load_config("pricing").get("risk_free_rate", {}).get("value", 0.025)
    und = next((u for u in datasource._get_universe_cfg().get("underlyings", [])
                if u.get("symbol") == sym), {})
    return rate, und.get("sec_type", "STK") == "STK"


def _exact_repricing_pnl(sub, rate, american, s=0.0, dv=0.0, dt_eff=None):
    """P&L EXACT par repricing complet sous le choc (€/contrat, modèle base →
    modèle choqué) — la référence contre laquelle on mesure « le Reste » que les
    greeks (Eq.19) n'expliquent pas : termes d'ordre supérieur et croisés
    (speed, vanna, volga, charm…), typiquement ~5 % du P&L."""
    import math as _m
    from src.pricing.european import bs_price
    from src.pricing.american import price_american_binomial
    out = pd.Series(float("nan"), index=sub.index)
    for idx, r in sub.iterrows():
        try:
            iv = float(r["implied_vol"]); T = float(r["maturity_years"])
            F = float(r["forward"]); K = float(r["strike"])
            right = str(r["right"]); mult = float(r.get("multiplier") or 100)
            S = float(r.get("reference_spot") or 0)
            if not (iv > 0 and T > 0 and F > 0 and K > 0):
                continue
            sig2 = max(iv + dv, 0.005)
            dt_y = (float(dt_eff.loc[idx]) / 365.0) if dt_eff is not None else 0.0
            T2 = max(T - dt_y, 0.0)
            if american and S > 0:
                q = rate - _m.log(F / S) / T
                v0 = price_american_binomial(S, K, iv, T, rate, q, right, 200).price
                if T2 > 1e-9:
                    v1 = price_american_binomial(S * (1 + s), K, sig2, T2, rate,
                                                 q, right, 200).price
                else:
                    v1 = (max(S * (1 + s) - K, 0.0) if right.upper().startswith("C")
                          else max(K - S * (1 + s), 0.0))
            else:
                v0 = bs_price(F, K, iv, T, rate, right)
                v1 = bs_price(F * (1 + s), K, sig2, T2, rate, right)
            out[idx] = (v1 - v0) * mult
        except Exception:
            pass
    return out


layout = dbc.Container([
    html.Div([
        html.H2("Greeks & Risk Analytics"),
        html.P("Greeks bruts ET monétisés en € pour chaque option de la grille "
               "(maturité × échelle de delta, call & put) — sortie §5 de la spec. "
               "Indice : Black-76 européen · composantes : CRR américain."),
    ], className="page-header"),

    dcc.Interval(id="grk-interval", interval=30000, n_intervals=0),

    # ── A. Greeks de la grille collectée ──────────────────────────────
    dbc.Row(id="grk-metrics", className="g-3 mb-4"),

    dbc.Card([
        dbc.CardHeader("Conventions de monétisation (multiplicateur du contrat)"),
        dbc.CardBody(dbc.Row([
            dbc.Col([
                html.Div(dcc.Markdown(r"$$\Delta_{EUR} = \Delta \times mult \times S$$",
                                      mathjax=True), className="formula-box"),
                html.Small("cash delta (€)", className="text-muted"),
            ]),
            dbc.Col([
                html.Div(dcc.Markdown(r"$$\Gamma_{EUR} = \Gamma \times mult \times S^2$$",
                                      mathjax=True), className="formula-box"),
                html.Small("gamma monétisé (€)", className="text-muted"),
            ]),
            dbc.Col([
                html.Div(dcc.Markdown(r"$$\nu_{EUR} = \nu \times mult$$",
                                      mathjax=True), className="formula-box"),
                html.Small("€ par point de vol", className="text-muted"),
            ]),
            dbc.Col([
                html.Div(dcc.Markdown(r"$$\Theta_{EUR} = \Theta \times mult$$",
                                      mathjax=True), className="formula-box"),
                html.Small("€ par jour calendaire", className="text-muted"),
            ]),
            dbc.Col([
                html.Div(dcc.Markdown(r"$$\rho_{EUR} = \rho \times mult$$",
                                      mathjax=True), className="formula-box"),
                html.Small("€ par point de taux", className="text-muted"),
            ]),
        ])),
    ], className="card mb-4"),

    dbc.Row([
        dbc.Col([
            html.Label("Maturité :", className="text-muted small"),
            dcc.Dropdown(id="grk-expiry", clearable=False,
                         style={"backgroundColor": "#ffffff", "color": "#1f2328"}),
        ], width=4),
    ], className="mb-3"),

    dbc.Row([
        dbc.Col(dbc.Card([
            dbc.CardHeader("Delta par strike (échelle ATM ± 10/30Δ)"),
            dbc.CardBody(dcc.Graph(id="grk-delta-fig", config={"displayModeBar": False})),
        ], className="card"), width=6),
        dbc.Col(dbc.Card([
            dbc.CardHeader("Vega monétisé (€ par point de vol)"),
            dbc.CardBody(dcc.Graph(id="grk-vega-fig", config={"displayModeBar": False})),
        ], className="card"), width=6),
    ], className="mb-4"),

    dbc.Card([
        dbc.CardHeader("Grille complète — greeks bruts et € (maturité sélectionnée)"),
        dbc.CardBody([
            dash_table.DataTable(
                id="grk-grid-table",
                columns=_GRID_COLUMNS,
                merge_duplicate_headers=True,
                style_header={"textTransform": "none"},   # garde ν/σ en vraies lettres grecques
                style_table={"overflowX": "auto"},
                style_cell={"textAlign": "center", "padding": "6px", "fontSize": "13px"},
                sort_action="native", page_size=20,
            ),
            html.Small([
                "Lecture : Δ € = exposition actions équivalente en € d'un contrat (cash delta) · ",
                "P&L Δ = gain/perte ≈ pour +1% de spot (Δ €/100) · P&L Γ = gain de convexité pour ",
                "±1% (½·Γ €·(1%)², toujours positif si long gamma) · var. Δ € = déplacement du cash ",
                "delta pour +1% (Γ €/100) · ν € = P&L par +1 pt de vol · Θ € = P&L par jour calendaire. ",
                "P&L total d'un mouvement de ±1% ≈ ±P&L Δ + P&L Γ + Reste. ",
                "Reste € = P&L exact (REPRICING complet à +1%) − P&L Δ − P&L Γ : la part "
                "du P&L que les greeks n'expliquent PAS (ordres supérieurs : speed, "
                "convexités croisées) — les greeks en expliquent typiquement ~95-99 %. ",
                "ρ = sensibilité au TAUX d'intérêt, par +1 pt de taux (ρ € = ρ×mult) : "
                "un call s'apprécie quand les taux montent, un put se déprécie ; l'effet "
                "croît avec la maturité (ρ ∝ K·T·e⁻ʳᵀ). ",
                "Volumétrie : Bid sz / Ask sz = nb de contrats disponibles AU bid / À "
                "l'ask (profondeur du 1er niveau du carnet — la liquidité exécutable "
                "maintenant ; un 1×1 est fragile, un 150×200 est ferme) · Volume = "
                "contrats échangés aujourd'hui (différé 15 min) · OI = open interest, "
                "positions ouvertes. Plus la volumétrie est étoffée, plus le mid (et "
                "donc l'IV et les greeks qui en découlent) est digne de confiance.",
            ], className="text-muted d-block mt-2"),
        ]),
    ], className="card mb-4"),

    # ── A-bis. Simulateur de choc interactif (Eq.19 — approximation greeks) ──
    dbc.Card([
        dbc.CardHeader("Simulateur de choc — P&L par strike (Eq.19 : δP ≈ Δ·δS + ½Γ·δS² + ν·δσ + Θ·δt)"),
        dbc.CardBody([
            dbc.Row([
                # Plages DESK : spot ±20% (le crash roadmap = −20%), vol ±15 pts (le
                # spike roadmap = +15), horizon ≤ 30j — le theta est un greek de COURT
                # terme ; au-delà d'un mois on raisonne en repricing (page Scénarios),
                # pas en θ×jours. Les bornes par option (expiration, valeur temps)
                # restent actives en garde-fou.
                dbc.Col([
                    html.Label("Choc spot (%)", className="text-muted small"),
                    dcc.Slider(id="grk-shock-spot", min=-20, max=20, step=1, value=-5,
                               marks={i: f"{i:+d}%" for i in range(-20, 21, 5)},
                               tooltip={"placement": "bottom"}),
                ], width=4),
                dbc.Col([
                    html.Label("Choc de vol (points)", className="text-muted small"),
                    dcc.Slider(id="grk-shock-vol", min=-15, max=15, step=1, value=5,
                               marks={i: f"{i:+d}" for i in range(-15, 16, 5)},
                               tooltip={"placement": "bottom"}),
                ], width=4),
                dbc.Col([
                    html.Label("Horizon theta (jours — borné par expiration et valeur temps)",
                               className="text-muted small"),
                    dcc.Slider(id="grk-shock-days", min=0, max=30, step=1, value=1,
                               marks={i: f"{i}j" for i in (0, 1, 5, 10, 20, 30)},
                               tooltip={"placement": "bottom"}),
                ], width=4),
            ], className="mb-2"),
            dbc.Row([
                dbc.Col(dcc.Graph(id="grk-shock-fig", config={"displayModeBar": False}),
                        width=9),
                dbc.Col(html.Div(id="grk-shock-summary"), width=3),
            ]),
        ]),
    ], className="card mb-4"),

    # ── B. Risque du portefeuille (positions réelles du compte) ──────
    html.P("PORTEFEUILLE (positions du compte paper)", className="section-title"),
    html.Div(id="grk-pf-status", className="mb-3"),
    dbc.Row(id="grk-pf-metrics", className="g-3 mb-3"),
    dbc.Card([
        dbc.CardHeader("Détail par position (position_risk)"),
        dbc.CardBody(dash_table.DataTable(
            id="grk-pf-table",
            columns=_POS_COLUMNS,
            style_header={"textTransform": "none"},
            style_table={"overflowX": "auto"},
            style_cell={"textAlign": "center", "padding": "6px", "fontSize": "13px"},
            sort_action="native", page_size=15,
        )),
    ], className="card"),
], fluid=True)


@callback(
    Output("grk-metrics", "children"),
    Output("grk-expiry",  "options"),
    Output("grk-expiry",  "value"),
    Input("grk-interval", "n_intervals"),
    Input("selected-symbol", "data"),
    State("grk-expiry",   "value"),
)
def refresh_grid_meta(_, symbol, current):
    sym   = symbol or "ESTX50"
    chain = datasource.get_option_chain(sym)
    if chain.empty:
        return [dbc.Col(no_data_alert(sym), width=12)], [], None

    usable = chain[chain["is_usable"]] if "is_usable" in chain.columns else chain
    mult   = int(chain["multiplier"].iloc[0]) if "multiplier" in chain.columns else 100
    model  = "Black-76 (européen)" if sym == "ESTX50" else "CRR (américain)"

    sum_ev = usable["eur_vega"].abs().sum() if "eur_vega" in usable.columns else 0.0
    metrics = [
        dbc.Col(_mb(str(len(usable)),     f"Options usable {sym}", "positive"), width=2),
        dbc.Col(_mb(str(chain["expiry"].nunique()), "Maturités"),               width=2),
        dbc.Col(_mb(f"×{mult}",           "Multiplicateur"),                    width=2),
        dbc.Col(_mb(model,                "Modèle de pricing", "info"),         width=3),
        dbc.Col(_mb(f"{sum_ev:,.0f} €",   "Σ |Vega €| de la grille"),           width=3),
    ]

    expiries = sorted(chain["expiry"].unique())
    opts = [{"label": e, "value": e} for e in expiries]
    value = current if current in expiries else (expiries[0] if expiries else None)
    return metrics, opts, value if value != current else no_update


@callback(
    Output("grk-delta-fig",  "figure"),
    Output("grk-vega-fig",   "figure"),
    Output("grk-grid-table", "data"),
    Input("grk-expiry",      "value"),
    Input("selected-symbol", "data"),
    Input("grk-interval",    "n_intervals"),
)
def refresh_grid(expiry, symbol, _):
    sym = symbol or "ESTX50"
    if not expiry:
        return go.Figure(), go.Figure(), []
    chain = datasource.get_option_chain(sym)
    if chain.empty:
        return go.Figure(), go.Figure(), []
    sub = chain[chain["expiry"] == expiry].sort_values("strike")
    # Lecture desk : normalisation par +1% de spot (calcul de PRÉSENTATION uniquement —
    # les tables persistées gardent les conventions de la spec §5).
    if "eur_delta" in sub.columns:
        sub = sub.assign(pnl_delta_1pct=sub["eur_delta"] / 100.0)
    if "eur_gamma" in sub.columns:
        # var. Δ€ = Γ€/100 (déplacement du cash delta pour +1%) ;
        # P&L Γ = ½·Γ€·(1%)² = Γ€/20 000 (gain de convexité, même signe à la hausse
        # comme à la baisse — s'AJOUTE au P&L Δ pour le P&L total du mouvement).
        sub = sub.assign(shift_delta_1pct=sub["eur_gamma"] / 100.0,
                         pnl_gamma_1pct=sub["eur_gamma"] / 20000.0)
    # « Le Reste » en € : P&L exact par repricing complet à +1 % de spot, moins la
    # part expliquée par Δ et Γ — ce sont les ordres supérieurs (speed, etc.).
    if {"pnl_delta_1pct", "pnl_gamma_1pct", "implied_vol", "forward"}.issubset(sub.columns):
        rate, american = _pricing_context(sym)
        exact = _exact_repricing_pnl(sub, rate, american, s=0.01)
        sub = sub.assign(reste_1pct=exact - sub["pnl_delta_1pct"].fillna(0)
                                          - sub["pnl_gamma_1pct"].fillna(0))

    def scatter_fig(col, ytitle, fmt=".4f"):
        fig = go.Figure()
        for right, color, name in [("C", "#0969da", "Calls"), ("P", "#bc4c00", "Puts")]:
            d = sub[sub["right"] == right]
            if col in d.columns:
                fig.add_trace(go.Scatter(
                    x=d["strike"], y=d[col], mode="lines+markers", name=name,
                    line=dict(color=color, dash="solid" if right == "C" else "dash"),
                    marker=dict(size=7),
                    hovertemplate="K=%{x}<br>%{y:" + fmt + "}<extra></extra>",
                ))
        fig.update_layout(
            template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
            margin=dict(l=40, r=20, t=20, b=40), height=300,
            xaxis_title="Strike", yaxis_title=ytitle,
            legend=dict(bgcolor="rgba(0,0,0,0)"),
        )
        return fig

    cols = [c["id"] for c in _GRID_COLUMNS]
    data = sub[[c for c in cols if c in sub.columns]].to_dict("records")
    return (scatter_fig("delta", "Delta"),
            scatter_fig("eur_vega", "Vega € / pt de vol", ",.0f"),
            data)


@callback(
    Output("grk-shock-fig",     "figure"),
    Output("grk-shock-summary", "children"),
    Input("grk-shock-spot",     "value"),
    Input("grk-shock-vol",      "value"),
    Input("grk-shock-days",     "value"),
    Input("grk-expiry",         "value"),
    Input("selected-symbol",    "data"),
)
def refresh_shock(s_pct, dvol, days, expiry, symbol):
    """P&L approx par les greeks (Eq.19) de chaque option de la maturité sélectionnée,
    monétisé € pour 1 contrat — interactif via les sliders."""
    sym = symbol or "ESTX50"
    fig = go.Figure()
    if not expiry:
        return fig, None
    chain = datasource.get_option_chain(sym)
    if chain.empty:
        return fig, None
    sub = chain[chain["expiry"] == expiry].dropna(subset=["eur_delta"]).sort_values("strike")
    if sub.empty:
        return fig, None

    s = (s_pct or 0) / 100.0
    dv, dt = (dvol or 0), (days or 0)

    # Theta HONNÊTE à long horizon : (1) il ne court que jusqu'à l'expiration de
    # CHAQUE option ; (2) la perte est plafonnée à la valeur temps de l'option
    # (mid − intrinsèque, monétisée) — on ne peut pas saigner plus que ce qui existe.
    # Sans ces bornes, θ×365j ferait perdre 12× sa valeur à une option 1 mois.
    import numpy as np
    dte = sub.get("days_to_expiry", pd.Series(dt, index=sub.index)).clip(lower=0)
    dt_eff = np.minimum(dt, dte)
    spot_ref = sub.get("reference_spot", pd.Series(np.nan, index=sub.index))
    intrinsic = np.where(sub["right"].astype(str).str.upper().str[0] == "C",
                         (spot_ref - sub["strike"]).clip(lower=0),
                         (sub["strike"] - spot_ref).clip(lower=0))
    mult = sub.get("multiplier", pd.Series(100, index=sub.index)).fillna(100)
    time_value_eur = ((sub["mid_price"] - intrinsic).clip(lower=0) * mult).fillna(0)
    pnl_theta = np.maximum(sub["eur_theta"].fillna(0) * dt_eff, -time_value_eur)

    pnl = (sub["eur_delta"] * s
           + 0.5 * sub["eur_gamma"].fillna(0) * s ** 2
           + sub["eur_vega"].fillna(0) * dv
           + pnl_theta)

    # « Le Reste » (demande du prof) : P&L EXACT par repricing complet sous le même
    # choc — l'écart avec l'approximation greeks = les ordres supérieurs, en €.
    rate, american = _pricing_context(sym)
    exact = _exact_repricing_pnl(sub, rate, american, s=s, dv=dv / 100.0,
                                 dt_eff=pd.Series(dt_eff, index=sub.index))
    reste = exact - pnl

    labels = [f"{k:g} {r}" for k, r in zip(sub["strike"], sub["right"])]
    fig.add_trace(go.Bar(
        x=labels, y=pnl, name="Greeks (Eq.19)",
        marker_color=["#1a7f37" if v >= 0 else "#cf222e" for v in pnl],
        hovertemplate="%{x}<br>P&L greeks ≈ %{y:,.0f} €<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=labels, y=exact, name="Repricing exact", mode="markers",
        marker=dict(symbol="diamond", size=8, color="#0969da",
                    line=dict(width=1, color="#ffffff")),
        hovertemplate="%{x}<br>P&L exact = %{y:,.0f} €<extra></extra>",
    ))
    fig.update_layout(
        template="plotly_white", paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
        height=280, margin=dict(l=45, r=10, t=8, b=60), font=dict(size=11),
        xaxis=dict(tickangle=-45, tickfont=dict(size=9)),
        yaxis_title="P&L (€ / contrat)",
        legend=dict(orientation="h", y=1.15, font=dict(size=10)),
    )

    total = float(pnl.sum())
    total_exact = float(exact.sum()) if exact.notna().any() else float("nan")
    total_reste = float(reste.sum()) if reste.notna().any() else float("nan")
    decomp = [
        ("Δ (directionnel)", float((sub["eur_delta"] * s).sum())),
        ("Γ (convexité)",    float((0.5 * sub["eur_gamma"].fillna(0) * s ** 2).sum())),
        ("ν (vol)",          float((sub["eur_vega"].fillna(0) * dv).sum())),
        ("Θ (temps, borné)", float(pnl_theta.sum())),
        ("Reste (ordres sup.)", total_reste),
    ]
    # Garde-fou : l'approximation greeks (Eq.19) est locale — pour des chocs
    # extrêmes le repricing complet (page Scénarios, source de vérité roadmap)
    # diverge sensiblement (delta/gamma figés, pas de skew dynamique).
    warning = None
    if abs(s_pct or 0) > 10 or abs(dv) > 10:
        warning = dbc.Alert(
            "Choc extrême : l'approximation par les greeks sous-estime les non-linéarités "
            "— le repricing complet (page Scénarios) fait foi.",
            color="warning", className="py-1 px-2 mt-2 mb-0",
            style={"fontSize": "0.72rem"})

    pct_explained = (100.0 * total / total_exact
                     if total_exact == total_exact and abs(total_exact) > 50 else None)
    summary = html.Div([
        _mb(f"{total_exact:,.0f} €" if total_exact == total_exact else "—",
            "P&L EXACT (repricing complet)",
            "positive" if total_exact >= 0 else "negative"),
        html.Div([html.Div([
            html.Span(name + " : ", className="text-muted small"),
            html.Span(f"{v:,.0f} €" if v == v else "—", className="small fw-bold",
                      style={"color": "#1a7f37" if v >= 0 else "#cf222e"}),
        ]) for name, v in decomp], className="mt-2"),
        html.Div(html.Small(
            f"Les greeks expliquent {pct_explained:.1f} % du P&L exact"
            if pct_explained is not None else
            "Choc trop petit pour un % d'explication significatif",
            className="text-muted fst-italic"), className="mt-1"),
        warning,
    ])
    return fig, summary


@callback(
    Output("grk-pf-status",  "children"),
    Output("grk-pf-metrics", "children"),
    Output("grk-pf-table",   "data"),
    Input("grk-interval",    "n_intervals"),
    Input("selected-symbol", "data"),
)
def refresh_portfolio(_, symbol):
    sym  = symbol or "ESTX50"
    agg  = datasource.get_portfolio(sym)        # risk_aggregates (agrégé par bucket)
    pos  = datasource.get_position_risk(sym)    # détail ligne-à-ligne

    if agg.empty and pos.empty:
        status = dbc.Alert(
            f"Aucune position {sym} dans le compte paper — les tables position_risk / "
            "risk_aggregates sont vides (comportement conforme : elles ne se remplissent "
            "qu'avec des positions réelles).", color="info", className="py-2")
        return status, [], []

    metrics = []
    if not agg.empty:
        row = agg.iloc[0]
        for col, label in [("portfolio_delta", "Delta € agrégé"), ("portfolio_gamma", "Gamma € agrégé"),
                           ("portfolio_vega", "Vega € agrégé"), ("portfolio_theta", "Theta € agrégé"),
                           ("pnl_approx", "PnL valorisé")]:
            v = float(row.get(col, 0) or 0)
            metrics.append(dbc.Col(_mb(f"{v:,.0f} €", label,
                                       "positive" if v >= 0 else "negative"), width=2))

    data = pos.round(4).to_dict("records") if not pos.empty else []
    return None, metrics, data


def _mb(value, label, css=""):
    return html.Div([
        html.Div(str(value), className="metric-value"),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")
