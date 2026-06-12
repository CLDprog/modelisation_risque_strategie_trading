"""Page 7 — Pricer interactif, symbol-aware."""
import sys, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import dash
from dash import html, dcc, callback, Input, Output, State
import dash_bootstrap_components as dbc

from src.pricing.european import price_european
from src.pricing.american import price_american_binomial
from src.data.source import datasource
from src.data import no_data_alert

dash.register_page(__name__, path="/pricing", name="Pricing")

layout = dbc.Container([
    html.Div([
        html.H2("Pricing Engine"),
        html.P("Pricer interactif Black-Scholes (européen) et CRR Binomial (américain)."),
    ], className="page-header"),

    dbc.Card([
        dbc.CardHeader("Formules Black-Scholes / Black-76"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.P("Eq. 10 — Call européen :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(
                        r"$$C = e^{-rT}\left[F \cdot N(d_1) - K \cdot N(d_2)\right]$$",
                        mathjax=True), className="formula-box"),
                ], width=6),
                dbc.Col([
                    html.P("Eq. 11 — Put européen :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(
                        r"$$P = e^{-rT}\left[K \cdot N(-d_2) - F \cdot N(-d_1)\right]$$",
                        mathjax=True), className="formula-box"),
                ], width=6),
            ]),
            dbc.Row([
                dbc.Col([
                    html.P("Eq. 12 — Arbre binomial CRR :", className="text-muted small mb-1"),
                    html.Div(dcc.Markdown(
                        r"$$V_j^n = \max\!\left(\text{intrinsic}_j,\; e^{-r\Delta t}\left[p\,V_{j+1}^{n+1} + (1-p)\,V_j^{n+1}\right]\right)$$",
                        mathjax=True), className="formula-box"),
                ], width=12),
            ], className="mt-3"),
        ]),
    ], className="card mb-4"),

    dbc.Card([
        dbc.CardHeader("Paramètres du pricer"),
        dbc.CardBody([
            dbc.Row([
                dbc.Col([
                    html.Label("Spot S", className="text-muted small"),
                    dbc.Input(id="p-spot",   type="number", step=1, className="dash-input"),
                ], width=2),
                dbc.Col([
                    html.Label("Strike K", className="text-muted small"),
                    dbc.Input(id="p-strike", type="number", step=1, className="dash-input"),
                ], width=2),
                dbc.Col([
                    html.Label("Vol σ (%)", className="text-muted small"),
                    dbc.Input(id="p-vol",    type="number", step=0.5, className="dash-input"),
                ], width=2),
                dbc.Col([
                    html.Label("Maturité T (jours)", className="text-muted small"),
                    dbc.Input(id="p-days",   type="number", value=30, step=1, className="dash-input"),
                ], width=2),
                dbc.Col([
                    html.Label("Taux r (%)", className="text-muted small"),
                    dbc.Input(id="p-rate",   type="number", value=2.5, step=0.1, className="dash-input"),
                ], width=2),
                dbc.Col([
                    html.Label("Type", className="text-muted small"),
                    dcc.Dropdown(
                        id="p-right",
                        options=[{"label": "Call", "value": "C"}, {"label": "Put", "value": "P"}],
                        value="C", clearable=False,
                        style={"backgroundColor": "#ffffff", "color": "#1f2328"},
                    ),
                ], width=2),
            ], className="g-2 mb-3"),
        ]),
    ], className="card mb-4"),

    html.Div(id="pricing-results"),
], fluid=True)


@callback(
    Output("p-spot",   "value"),
    Output("p-strike", "value"),
    Output("p-vol",    "value"),
    Input("selected-symbol", "data"),
)
def init_pricing_inputs(symbol):
    """
    Pré-remplit les champs à l'ATM, UNIQUEMENT au chargement de la page et au
    changement de produit. Ne se déclenche pas sur l'interval → les éditions
    manuelles de l'utilisateur (strike, vol…) sont préservées.
    """
    sym  = symbol or "SPY"
    spot = datasource.get_spot(sym)
    if spot is None:
        spot = 100.0   # placeholder neutre (visible uniquement si non connecté)
    strike = round(spot / 5) * 5
    # IV ATM depuis la chaîne live si disponible
    chain = datasource.get_option_chain(sym)
    vol   = 18.0   # défaut
    if not chain.empty and "implied_vol" in chain.columns and "log_moneyness" in chain.columns:
        atm = chain[chain["log_moneyness"].abs() < 0.03]["implied_vol"].mean()
        if atm and atm == atm:
            vol = round(float(atm) * 100, 1)
    return spot, strike, vol


@callback(
    Output("pricing-results", "children"),
    Input("p-spot",            "value"),
    Input("p-strike",          "value"),
    Input("p-vol",             "value"),
    Input("p-days",            "value"),
    Input("p-rate",            "value"),
    Input("p-right",           "value"),
    Input("selected-symbol",   "data"),
)
def update_pricing(spot, strike, vol_pct, days, rate_pct, right, symbol):
    if not all([spot, strike, vol_pct, days, rate_pct, right]):
        return dbc.Alert("Remplis tous les paramètres.", color="warning")

    sym   = symbol or "SPY"
    spot  = float(spot)
    K     = float(strike)
    sigma = float(vol_pct) / 100
    T     = float(days) / 365
    rate  = float(rate_pct) / 100

    # Carry depuis la courbe forward réelle si disponible
    carry = 0.0
    fwd_df = datasource.get_forward_curve(sym)
    if not fwd_df.empty and "implied_carry" in fwd_df.columns:
        carry = float(fwd_df["implied_carry"].mean())
    F = spot * math.exp((rate - carry) * T)

    # Multiplicateur réel du contrat (10 pour OESX/indice, 100 pour les actions)
    chain = datasource.get_option_chain(sym)
    mult = 100
    if not chain.empty and "multiplier" in chain.columns:
        m = chain["multiplier"].dropna()
        if not m.empty:
            mult = int(m.iloc[0])

    try:
        euro = price_european(F, K, sigma, T, rate, right, spot, mult)
        amer = price_american_binomial(spot, K, sigma, T, rate, carry, right, 200)
    except Exception as exc:
        return dbc.Alert(f"Erreur de pricing : {exc}", color="danger")

    d1_val = (math.log(F / K) + 0.5 * sigma**2 * T) / (sigma * math.sqrt(T)) if T > 0 and sigma > 0 else 0
    d2_val = d1_val - sigma * math.sqrt(T) if T > 0 else 0
    from scipy.stats import norm
    nd1 = norm.cdf(d1_val if right == "C" else -d1_val)
    nd2 = norm.cdf(d2_val if right == "C" else -d2_val)

    return html.Div([
        dbc.Row([
            dbc.Col(_mb(f"{euro.price:,.4f} €",        "Prix Européen (Black-76)"), width=3),
            dbc.Col(_mb(f"{amer.price:,.4f} €",         "Prix Américain (CRR)"), width=3),
            dbc.Col(_mb(f"{F:,.4f} €",                  "Forward F(T)"),       width=3),
            dbc.Col(_mb(f"{amer.price - euro.price:,.4f} €", "Prime exercice anticipé"), width=3),
        ], className="g-3 mb-4"),

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
            ]),
        ], className="card mb-4"),

        dbc.Card([
            dbc.CardHeader("Greeks bruts (en %)"),
            dbc.CardBody(dbc.Row([
                _gbox("Δ", "Delta",  f"{euro.delta:.6f}",  "dP/dS"),
                _gbox("Γ", "Gamma",  f"{euro.gamma:.8f}",  "d²P/dS²"),
                _gbox("ν", "Vega",   f"{euro.vega:.6f}",   "dP/dσ (par 1 pt de vol)"),
                _gbox("Θ", "Theta",  f"{euro.theta:.6f}",  "dP/dt (par jour)"),
                _gbox("ρ", "Rho",    f"{euro.rho:.6f}",    "dP/dr (par 1 pt de taux)"),
            ], className="g-3")),
        ], className="card mb-4"),

        dbc.Card([
            dbc.CardHeader(f"Greeks monétisés en € (mult = {mult} — spec §5)"),
            dbc.CardBody(dbc.Row([
                _gbox("Δ", "Delta €", f"{euro.delta * mult * spot:,.2f} €",    "Δ × mult × S (cash delta)"),
                _gbox("Γ", "Gamma €", f"{euro.gamma * mult * spot**2:,.2f} €", "Γ × mult × S²"),
                _gbox("ν", "Vega €",  f"{euro.vega * mult:,.2f} €",            "ν × mult (par pt de vol)"),
                _gbox("Θ", "Theta €", f"{euro.theta * mult:,.2f} €",           "Θ × mult (par jour)"),
                _gbox("ρ", "Rho €",   f"{euro.rho * mult:,.2f} €",             "ρ × mult (par pt de taux)"),
            ], className="g-3")),
        ], className="card"),
    ])


def _mb(value, label, css=""):
    return html.Div([
        html.Div(str(value), className="metric-value"),
        html.Div(label,      className="metric-label"),
    ], className=f"metric-box {css}")


def _calc_row(name, value, formula):
    return html.Div([
        html.Span(name + " = ", className="text-muted me-2"),
        html.Span(value, className="text-dark fw-bold me-2"),
        html.Br(),
        html.Small(formula, className="text-muted"),
    ], className="p-2 border border-secondary rounded")


# Style des lettres grecques : serif + grande taille (rendu calligraphique), et
# textTransform none pour échapper aux MAJUSCULES du CSS (sinon ν → Ν, illisible).
_GREEK_STYLE = {
    "fontSize": "2rem", "fontFamily": "Georgia, 'Times New Roman', serif",
    "color": "#0969da", "textTransform": "none", "lineHeight": "1",
    "fontStyle": "italic",
}


def _gbox(letter, name, value, definition):
    return dbc.Col(html.Div([
        html.Div([
            html.Span(letter, style=_GREEK_STYLE, className="me-3"),
            html.Span(value, className="metric-value", style={"fontSize": "1.05rem"}),
        ], className="d-flex align-items-center justify-content-center mb-1"),
        html.Div(name,       className="metric-label"),
        html.Small(definition, className="text-muted"),
    ], className="metric-box"), width=3)
