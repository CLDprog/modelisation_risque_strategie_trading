# Ce module a été supprimé.
# Toutes les données proviennent exclusivement de la couche analytics (store Parquet),
# alimentée par le collecteur via l'API IBKR Web (Client Portal Gateway).
# Aucune donnée simulée ou prédéfinie n'est autorisée dans ce projet.
raise ImportError(
    "src.data.mock a été supprimé. "
    "Lancez le collecteur (python run_collector.py) avec le gateway IBKR Web "
    "authentifié sur https://localhost:5000."
)
