"""Helpers communs pour le layer de données."""


def no_data_alert(symbol: str = "") -> object:
    """Composant Dash indiquant qu'aucune donnée n'est disponible (collecteur arrêté)."""
    import dash_bootstrap_components as dbc
    from dash import html
    sym_label = f" pour {symbol}" if symbol else ""
    return dbc.Alert([
        html.Strong("Aucune donnée disponible"),
        html.Br(),
        html.Span(
            f"Le store est vide{sym_label}. "
            "Lancez le collecteur (python run_collector.py) avec le gateway IBKR Web "
        ),
        html.Strong("authentifié"),
        html.Span(
            " (https://localhost:5000), ou rejouez une journée via le pipeline EOD."
        ),
    ], color="warning", className="mt-3")
