# Spécification données — EURO STOXX 50 (demande du professeur)

> **IMPORTANT — exigence du professeur (2026-06-08).** Ce document fait foi sur ce qui doit
> être produit. Toute implémentation doit s'y conformer.

## 1. Périmètre

- **On se concentre sur l'indice EURO STOXX 50** et **ses 50 sociétés composantes**.
- **On retire SPY, QQQ, AAPL** (univers US) de la collecte.
- Devise de travail : **EUR (€)**.
- **Stockage : format Parquet** (déjà en place — à conserver et réaffirmer).

## 2. Contrats IBKR

| Élément | Symbole IBKR | secType | Bourse | Devise | Exercice | Multiplicateur |
|---|---|---|---|---|---|---|
| Indice EURO STOXX 50 | `ESTX50` (alias SX5E) | `IND` | EUREX | EUR | — | — |
| Options sur l'indice | `OESX` (tradingClass) | `OPT` | EUREX | EUR | **Européen**, cash-settled | **10 €/pt** |
| Sociétés composantes (×50) | ticker propre | `STK` | EUREX / Euronext / XETRA… | EUR | — | — |
| Options sur composantes | tradingClass propre | `OPT` | EUREX | EUR | **Américain** (typique EUREX single-stock) | 100 (à vérifier par contrat) |

> Conséquence pricing : **indice → Black-76 (européen)** ; **composantes → arbre CRR (américain)**.
> Le multiplicateur réel est lu depuis `secdef/info` (ne pas le coder en dur).

## 3. Grille de maturités (12 échéances cibles)

| Cible | Jours (≈) | Années ACT/365 (≈) |
|---|---|---|
| 1 jour | 1 | 0.003 |
| 3 jours | 3 | 0.008 |
| 10 jours | 10 | 0.027 |
| 3 semaines | 21 | 0.058 |
| 1 mois | 30 | 0.082 |
| 3 mois | 91 | 0.249 |
| 6 mois | 182 | 0.499 |
| 9 mois | 273 | 0.748 |
| 12 mois | 365 | 1.000 |
| 18 mois | 548 | 1.500 |
| 24 mois | 730 | 2.000 |
| 3 ans | 1095 | 3.000 |

**Règle** : les options listées n'ont pas exactement ces échéances → pour chaque cible, on
prend **l'échéance listée la plus proche** (et on stocke l'écart). Si aucune échéance proche
n'existe (ex. 3 ans sur certaines actions, 1 j sur des composantes), on **flague « indisponible »**
plutôt que d'inventer.

## 4. Plage de strikes : échelle de delta ATM + ±10/±20/±30

Pour chaque (sous-jacent × maturité), on collecte une **échelle de delta** : l'**ATM**
(≈ 50Δ) plus les strikes aux points **10Δ, 20Δ, 30Δ** de **chaque aile** (calls OTM et
puts OTM). Soit ~7 strikes par maturité :

`[put 10Δ, put 20Δ, put 30Δ, ATM, call 30Δ, call 20Δ, call 10Δ]`

Sélection **basée sur le delta** (et non un comptage de strikes) : on calcule le strike
cible de chaque point de delta puis on prend le strike **listé le plus proche**. À chaque
strike, on sort **call ET put**.

## 5. Sorties exigées

Pour **l'indice ET chaque composante**, et pour **chaque maturité de la grille** et **chaque strike**
de la bande (ATM → ±30Δ), produire :

| Champ | Description |
|---|---|
| `spot` | Prix spot du sous-jacent |
| `forward` | Prix forward pour la maturité |
| `call_price` | Prix du call |
| `put_price` | Prix du put |
| `implied_vol` | Volatilité implicite |
| **Greeks « en % » (bruts)** | `delta`, `gamma`, `vega` (par 1 pt de vol), `theta` (par jour) — éventuellement `rho` |
| **Greeks « en devise » (€)** | `eur_delta` (cash delta), `eur_gamma` (€-gamma), `eur_vega` (€ par pt de vol), `eur_theta` (€/jour) — via le multiplicateur du contrat |

> Les greeks « en € » = greeks bruts **monétisés** par le multiplicateur (et le spot pour delta/gamma).
> Convention exacte à figer (voir §7).

## 6. Stockage

- **Parquet** partitionné (déjà : `data/analytics/<table>/dt=YYYY-MM-DD/`).
- Couche brute immuable conservée (`raw_market_events`).
- Lineage (`code_version`, `config_hash`, `run_id`) sur chaque sortie.

## 7. Points à confirmer (avant implémentation)

1. **Entitlement EUREX** ⚠️ *bloquant* : le compte paper a-t-il les **données de marché EUREX**
   (indice + actions EUR) ? Sans cela, rien ne se collecte. À tester avec un smoke test sur `ESTX50`.
2. **Les 50 composantes** : on collecte **les 50** d'emblée, ou on démarre par l'indice + un
   sous-ensemble puis on étend ? (Volume de données important.)
3. **« Greeks en % »** : confirmer la définition exacte (delta en %, vega par 1 pt de vol,
   theta par jour calendaire) et les monétisations € (cash delta = Δ × spot × mult, €-vega = vega × mult, …).
4. **Liste officielle des composantes** : source = STOXX (révision annuelle en septembre). À figer dans `universe.yaml`.
5. **Tenors très courts (1 j, 3 j)** : disponibles sur l'indice (options journalières) mais souvent
   pas sur les actions → seront flagués « indisponible » le cas échéant.

## 8. Impact implémentation (esquisse)

- `configs/universe.yaml` : retirer SPY/QQQ/AAPL ; ajouter `ESTX50` (IND/EUREX/EUR) + les 50 actions ;
  options en EUR/EUREX ; **grille de maturités cible** + **sélection par delta** (nouveaux paramètres).
- `src/data/live.py` : sélection de strikes **par delta** (≈30Δ) au lieu du comptage ; mapping
  maturité-cible → échéance listée la plus proche ; sortie call **et** put par strike.
- Greeks : ajouter les versions **monétisées (€)** à côté des greeks bruts.
- Pricing : router indice→européen / composantes→américain selon le secType/exercice.
- `configs/pricing.yaml` : taux **EUR** (au lieu de 0.053 USD).
