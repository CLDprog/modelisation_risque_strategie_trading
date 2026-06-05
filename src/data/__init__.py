"""Helpers communs pour le layer de données."""


def no_data_alert(symbol: str = "") -> object:
    """Retourne un composant Dash indiquant qu'une connexion TWS est requise."""
    import dash_bootstrap_components as dbc
    from dash import html
    sym_label = f" pour {symbol}" if symbol else ""
    return dbc.Alert([
        html.Strong("Connexion TWS requise"),
        html.Br(),
        html.Span(
            f"Aucune donnée disponible{sym_label}. "
            "Connectez-vous à TWS (port 7497) depuis la page "
        ),
        html.Strong("Connexion IBKR"),
        html.Span(
            ", ou lancez le pipeline EOD pour charger des données historiques réelles."
        ),
    ], color="warning", className="mt-3")
