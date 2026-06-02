import dash
from dash import html, dcc, callback, Input, Output
import dash_bootstrap_components as dbc
import sys, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.pricing.european import price_european
from src.pricing.american import price_american_binomial
from src.data.mock import SPOT, RATE

dash.register_page(__name__, path="/pricing", name="Pricing")

layout = dbc.Container([
    html.Div([
        html.H2("Pricing Engine"),
        html.P("Pricer interactif Black-Scholes (européen) et CRR Binomial (américain) avec détail complet des calculs."),
    ], className="page-header"),

    # Formules BS
    dbc.Card([
        dbc.CardHeader("Formules Black-Scholes / Black-76"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.P("Eq. 10 — Call européen :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$C = e^{-rT}\left[F \cdot N(d_1) - K \cdot N(d_2)\right]$$", mathjax=True),
                             className="formula-box"),
                ], width=6),
                dbc.Col([
                    html.P("Eq. 11 — Put européen :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(r"$$P = e^{-rT}\left[K \cdot N(-d_2) - F \cdot N(-d_1)\right]$$", mathjax=True),
                             className="formula-box"),
                ], width=6),
            ]),
            dbc.Row([
                dbc.Col([
                    html.P("Eq. 12 — Arbre binomial (américain) — induction à rebours :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(
                        r"$$V_j^n = \max\!\left(\text{intrinsic}_j,\; e^{-r\Delta t}\left[p\,V_{j+1}^{n+1} + (1-p)\,V_j^{n+1}\right]\right)$$",
                        mathjax=True), className="formula-box"),
                ], width=12),
            ], className="mt-3"),
        ]),
    ], className="card mb-4"),

    # Inputs pricer
    dbc.Card([
        dbc.CardHeader("Paramètres du pricer"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.Label("Spot S", className="text-muted small"),
                    dbc.Input(id="p-spot",   type="number", value=SPOT,  step=1, className="dash-input"),
                ], width=2),
                dbc.Col([
                    html.Label("Strike K", className="text-muted small"),
                    dbc.Input(id="p-strike", type="number", value=520,   step=1, className="dash-input"),
                ], width=2),
                dbc.Col([
                    html.Label("Vol σ (%)", className="text-muted small"),
                    dbc.Input(id="p-vol",    type="number", value=18,    step=0.5, className="dash-input"),
                ], width=2),
                dbc.Col([
                    html.Label("Maturité T (jours)", className="text-muted small"),
                    dbc.Input(id="p-days",   type="number", value=30,    step=1, className="dash-input"),
                ], width=2),
                dbc.Col([
                    html.Label("Taux r (%)", className="text-muted small"),
                    dbc.Input(id="p-rate",   type="number", value=5.3,   step=0.1, className="dash-input"),
                ], width=2),
                dbc.Col([
                    html.Label("Type", className="text-muted small"),
                    dcc.Dropdown(
                        id="p-right",
                        options=[{"label": "Call", "value": "C"}, {"label": "Put", "value": "P"}],
                        value="C", clearable=False,
                        style={"backgroundColor": "#21262d", "color": "#e6edf3"},
                    ),
                ], width=2),
            ], className="g-2 mb-3"),
        ]),
    ], className="card mb-4"),

    # Résultats
    html.Div(id="pricing-results"),

], fluid=True)


@callback(Output("pricing-results", "children"), [
    Input("p-spot",   "value"), Input("p-strike", "value"),
    Input("p-vol",    "value"), Input("p-days",   "value"),
    Input("p-rate",   "value"), Input("p-right",  "value"),
])
def update_pricing(spot, strike, vol_pct, days, rate_pct, right):
    if not all([spot, strike, vol_pct, days, rate_pct, right]):
        return dbc.Alert("Remplis tous les paramètres.", color="warning")

    spot   = float(spot)
    K      = float(strike)
    sigma  = float(vol_pct) / 100
    T      = float(days) / 365
    rate   = float(rate_pct) / 100
    F      = spot * math.exp(rate * T)

    euro = price_european(F, K, sigma, T, rate, right, spot, 100)
    amer = price_american_binomial(spot, K, sigma, T, rate, 0.015, right, 200)

    d1_val = (math.log(F / K) + 0.5 * sigma**2 * T) / (sigma * math.sqrt(T)) if T > 0 else 0
    d2_val = d1_val - sigma * math.sqrt(T)
    from scipy.stats import norm
    nd1 = norm.cdf(d1_val if right == "C" else -d1_val)
    nd2 = norm.cdf(d2_val if right == "C" else -d2_val)

    return html.Div([
        # Prix
        dbc.Row([
            dbc.Col(html.Div([html.Div(f"${euro.price:.4f}",  className="metric-value"),
                              html.Div("Prix Européen (BS)", className="metric-label")],
                             className="metric-box"), width=3),
            dbc.Col(html.Div([html.Div(f"${amer.price:.4f}", className="metric-value"),
                              html.Div("Prix Américain (CRR)", className="metric-label")],
                             className="metric-box"), width=3),
            dbc.Col(html.Div([html.Div(f"${F:.4f}", className="metric-value"),
                              html.Div("Forward F(T)", className="metric-label")],
                             className="metric-box"), width=3),
            dbc.Col(html.Div([html.Div(f"${amer.price - euro.price:.4f}", className="metric-value"),
                              html.Div("Prime exercice anticipé", className="metric-label")],
                             className="metric-box"), width=3),
        ], className="g-3 mb-4"),

        # Détail calcul
        dbc.Card([
            dbc.CardHeader("Détail des calculs intermédiaires"),
            dbc.CardBody([
                dbc.Row([
                    dbc.Col(_calc_row("d₁", f"{d1_val:.6f}",
                                      f"ln({F:.2f}/{K}) + 0.5×{sigma:.4f}²×{T:.4f}) / ({sigma:.4f}×√{T:.4f})"), width=6),
                    dbc.Col(_calc_row("d₂", f"{d2_val:.6f}", f"d₁ - {sigma:.4f}×√{T:.4f}"), width=6),
                ]),
                dbc.Row([
                    dbc.Col(_calc_row("N(d₁)" if right=="C" else "N(-d₁)", f"{nd1:.6f}", "CDF normale standard"), width=6),
                    dbc.Col(_calc_row("N(d₂)" if right=="C" else "N(-d₂)", f"{nd2:.6f}", "CDF normale standard"), width=6),
                ], className="mt-2"),
                dbc.Row([
                    dbc.Col(_calc_row("e^(-rT)", f"{math.exp(-rate*T):.6f}", f"Facteur d'actualisation"), width=6),
                    dbc.Col(_calc_row("σ√T",     f"{sigma*math.sqrt(T):.6f}", "Volatilité totale"), width=6),
                ], className="mt-2"),
            ]),
        ], className="card mb-4"),

        # Greeks
        dbc.Card([
            dbc.CardHeader("Greeks analytiques"),
            dbc.CardBody([
                dbc.Row([
                    _greek_box("Δ Delta",      f"{euro.delta:.6f}",  "dP/dS"),
                    _greek_box("Γ Gamma",      f"{euro.gamma:.8f}",  "d²P/dS²"),
                    _greek_box("ν Vega",       f"{euro.vega:.6f}",   "dP/dσ (par 1%)"),
                    _greek_box("Θ Theta",      f"{euro.theta:.6f}",  "dP/dt (par jour)"),
                    _greek_box("$ Gamma",      f"{euro.dollar_gamma:.2f}", "Γ×S²×mult/100"),
                    _greek_box("$ Vega",       f"{euro.dollar_vega:.2f}",  "ν×mult"),
                ], className="g-3"),
            ]),
        ], className="card"),
    ])


def _calc_row(name, value, formula):
    return html.Div([
        html.Span(name + " = ", className="text-muted me-2"),
        html.Span(value, className="text-light fw-bold me-2"),
        html.Br(),
        html.Small(formula, className="text-muted"),
    ], className="p-2 border border-secondary rounded")


def _greek_box(name, value, definition):
    return dbc.Col(html.Div([
        html.Div(value, className="metric-value", style={"fontSize": "1.2rem"}),
        html.Div(name,       className="metric-label"),
        html.Small(definition, className="text-muted"),
    ], className="metric-box"), width=2)
