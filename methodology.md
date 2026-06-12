# Méthodologie mathématique

Infrastructure de volatilité — Équations de référence (roadmap v4.0)

---

## 1. Prix de référence (Spot)

### Eq. 1 — Mid-price

Le prix de référence est le mid-price du bid/ask quand le spread est raisonnable :

$$S_{ref} = \frac{bid + ask}{2}$$

**Règle de fallback** (en ordre de priorité) :
1. `mid` si bid > 0 et spread% ≤ seuil
2. `last` si mid indisponible
3. `bid_fallback` si last indisponible
4. Le champ `reference_type` est toujours stocké pour traçabilité

---

## 2. Forward et carry (Étape 6)

### Eq. 2 — Forward par parité put-call (par strike)

$$F_i = K + e^{rT}(C_{mid} - P_{mid})$$

La parité put-call permet de reconstruire le forward sans dépendre d'une source externe de dividendes.

### Eq. 3 — Identité spot-forward-carry

$$F(T) = S \cdot e^{(r - q)T}$$

où $q$ est le taux de carry (dividendes + coûts de financement).

### Eq. 4 — Agrégation pondérée des forwards candidats

$$F(T) = \frac{\sum_{i} w_i F_i}{\sum_{i} w_i}$$

Les poids $w_i = 1 / (\text{spread\_call}_i + \text{spread\_put}_i)$ favorisent les strikes les plus liquides.

### Eq. 5 — Carry implicite

$$q = -\frac{\ln(F/S) - rT}{T}$$

---

## 3. Log-moneyness et variance totale (Étapes 7–8)

### Eq. 6 — Log-moneyness

$$k = \ln\!\left(\frac{K}{F(T)}\right)$$

Défini par rapport au **forward** (pas au spot) pour la stabilité cross-maturités.

- $k < 0$ : strike OTM (put) / ITM (call)  
- $k = 0$ : ATM forward  
- $k > 0$ : strike ITM (put) / OTM (call)

### Eq. 7 — Variance totale

$$w = \sigma^2 \cdot T$$

L'interpolation et la calibration de surface sont toujours faites en **espace de variance totale** $w$ plutôt qu'en volatilité brute $\sigma$, car :
- Supprime l'effet de scaling en $\sqrt{T}$
- Les conditions de non-arbitrage sont plus simples à vérifier
- La comparaison cross-maturités est directe

---

## 4. Pricing européen — Black-Scholes / Black-76 (Étape 10)

### Eq. 8 — d₁

$$d_1 = \frac{\ln(F/K) + \frac{1}{2}\sigma^2 T}{\sigma\sqrt{T}}$$

### Eq. 9 — d₂

$$d_2 = d_1 - \sigma\sqrt{T}$$

### Eq. 10 — Prix d'un call européen

$$C = e^{-rT}\left[F \cdot N(d_1) - K \cdot N(d_2)\right]$$

### Eq. 11 — Prix d'un put européen

$$P = e^{-rT}\left[K \cdot N(-d_2) - F \cdot N(-d_1)\right]$$

**Vérification :** Parité put-call : $C - P = e^{-rT}(F - K)$

---

## 5. Pricing américain — Arbre binomial CRR (Étape 10)

### Eq. 12 — Induction à rebours

À chaque nœud de l'arbre :

$$V_j^n = \max\!\left(\text{intrinsic}(S_j^n),\; e^{-r\Delta t}\left[p\,V_{j+1}^{n+1} + (1-p)\,V_j^{n+1}\right]\right)$$

**Facteurs CRR :**

$$u = e^{\sigma\sqrt{\Delta t}}, \quad d = \frac{1}{u}, \quad p = \frac{e^{(r-q)\Delta t} - d}{u - d}$$

L'exercice anticipé est capturé par le $\max$ entre valeur de continuation et valeur intrinsèque.

---

## 6. Greeks (Étapes 11)

### Eq. 13 — Delta

$$\Delta = e^{-rT} N(\pm d_1)$$

$(+)$ pour un call, $(-)$ pour un put. Interprétation : variation du prix pour $+1\$$ de spot.

### Eq. 14 — Gamma

$$\Gamma = \frac{e^{-rT} N'(d_1)}{F\,\sigma\sqrt{T}}$$

Identique pour call et put. Toujours positif pour une option longue.

### Eq. 15 — Vega (par 1 point de vol)

$$\nu = 0.01 \cdot e^{-rT} \cdot F \cdot N'(d_1) \cdot \sqrt{T}$$

Exprimé **par 1% de volatilité** (convention du projet).

### Eq. 16 — Theta (par jour calendaire)

$$\Theta = \frac{1}{365} \frac{\partial V}{\partial t}$$

Toujours négatif pour une option longue (décroissance temporelle).

### Eq. 17 — Dollar Gamma

$$\$\Gamma = \Gamma \cdot S^2 \cdot \frac{\text{multiplier}}{100}$$

Gain/perte de delta pour un mouvement de 1% du spot.

### Eq. 18 — Dollar Vega

$$\$\nu = \nu \cdot \text{multiplier}$$

---

## 7. Surface de volatilité — SVI (Étape 9)

### Eq. 20 — Paramétrie SVI par tranche de maturité

$$w(k) = a + b\left[\rho(k - m) + \sqrt{(k-m)^2 + \sigma^2}\right]$$

**Paramètres** (5 à calibrer par maturité) :

| Paramètre | Interprétation |
|-----------|----------------|
| $a$       | Niveau général de variance (ATM) |
| $b$       | Pente globale (amplitude du smile) |
| $\rho$    | Corrélation spot-vol (skew négatif = $\rho < 0$) |
| $m$       | ATM minimum de variance |
| $\sigma$  | Courbure au minimum (smile) |

**Calibration :** minimisation des moindres carrés en $w$ par tranche, puis vérification des bornes.

### Eq. 21 — Condition de monotonicité calendaire (no-arbitrage)

$$w(k, T_1) \leq w(k, T_2) \quad \forall k, \quad T_1 < T_2$$

La variance totale doit être non décroissante avec la maturité (sinon arbitrage calendaire).

### Eq. 22 — Interpolation linéaire en variance totale

$$w(k, T) = \frac{T_2 - T}{T_2 - T_1}\,w(k,T_1) + \frac{T - T_1}{T_2 - T_1}\,w(k,T_2)$$

---

## 8. Scénarios de stress (Étape 12)

### Eq. 19 — Approximation P&L par les Greeks

$$\delta P \approx \Delta\,\delta S + \frac{1}{2}\,\Gamma\,(\delta S)^2 + \nu\,\delta\sigma + \Theta\,\delta t$$

**Source de vérité :** repricing complet Black-Scholes sous paramètres choqués.  
L'approximation par les Greeks est utilisée uniquement pour la surveillance intraday rapide.

**Grille de scénarios (v1.0) :**

| Scénario | $\delta S$ | $\delta\sigma$ | $\delta t$ |
|----------|------------|----------------|------------|
| Crash    | -20%       | +10 pts        | 0 j        |
| Correction | -10%     | +5 pts         | 0 j        |
| Rally    | +10%       | -3 pts         | 0 j        |
| Vol spike | 0%        | +15 pts        | 0 j        |
| Vol crush | 0%        | -10 pts        | 0 j        |
| Theta 1j | 0%         | 0 pts          | 1 j        |
| Theta 5j | 0%         | 0 pts          | 5 j        |

---

## 9. Statistiques robustes QC (Étape 14)

### Eq. 24 — Z-score robuste (MAD)

$$z_i = \frac{x_i - \text{med}(X)}{1.4826 \cdot \text{MAD}(X)}$$

où $\text{MAD}(X) = \text{med}(|x_i - \text{med}(X)|)$

Utilisé pour rejeter les forwards outliers. Plus robuste que le z-score classique sur des données de marché avec quelques quotes aberrantes.

### Eq. 25 — Diagnostics spread et mid

$$\text{spread\%} = \frac{ask - bid}{(ask + bid)/2}, \quad mid = \frac{ask + bid}{2}$$

---

## 10. Identité variance panier (optionnel — Étape générique)

### Eq. 23 — Variance d'un portefeuille pondéré

$$\sigma_P^2 = \sum_i w_i^2 \sigma_i^2 + 2\sum_{i < j} w_i w_j \rho_{ij} \sigma_i \sigma_j$$

Utilisée pour les diagnostics de corrélation ou la décomposition de variance de portefeuille.
**Implémentée** dans `src/risk/dispersion.py` : corrélation implicite moyenne
$\bar\rho = (\sigma_I^2 - \Sigma w_i^2\sigma_i^2)/((\Sigma w_i\sigma_i)^2 - \Sigma w_i^2\sigma_i^2)$
calculée par tenor (indice vs 50 composantes) → table `dispersion_diagnostics`.

---

## 11. Méthodes bonus (hors roadmap — extensions quant)

> Ces méthodes ne sont pas exigées par la roadmap. Elles RÉUTILISENT l'infrastructure
> (spot du store, σ de la surface SVI, forwards de parité, vega € de la grille) pour
> faire de la finance de desk. Distinction fondamentale : tout le pricing se fait sous
> la **mesure risque-neutre ℚ** ($E^{\mathbb Q}[S_T] = F$ — pricer n'est PAS prédire).

### 11.1 Monte Carlo — option asiatique arithmétique (`src/pricing/monte_carlo.py`)

Payoff path-dependent (moyenne du chemin) → pas de forme fermée, l'arbre explose.

- **Chemins GBM sous ℚ** : $S_{t+\Delta} = S_t\,e^{(b-\sigma^2/2)\Delta + \sigma\sqrt{\Delta}Z}$,
  drift calé **point par point** sur la courbe forward de parité ($E[S_{t_i}] = F(t_i)$).
- **Variate de contrôle géométrique** : la moyenne géométrique d'un GBM est lognormale ⇒
  prix exact (Kemna-Vorst discret) :
  $E[\ln G] = \ln S_0 + (b-\tfrac{\sigma^2}{2})\,T\,\tfrac{n+1}{2n}$,
  $\;Var[\ln G] = \sigma^2 T\,\tfrac{(n+1)(2n+1)}{6n^2}$.
  Estimateur corrigé (β optimal) : $\hat P = \bar P_A - \beta(\bar P_G - P_G^{exact})$ —
  variance ÷ 20-100.
- **Quasi-Monte Carlo** : suites de Sobol brouillées (Owen) → erreur ~1/N au lieu de 1/√N.
- **Greeks** : delta *pathwise* (exact, car $A \propto S_0$) ; gamma par **re-scaling des
  chemins** (différence centrale à aléas strictement identiques, zéro re-simulation) ;
  vega/theta par bump à **nombres aléatoires communs** (CRN).
- Garde-fous testés : asiatique ∈ [géométrique exacte, européenne Black] ; vol effective ≈ σ/√3.

### 11.2 Variance swap & mini-VSTOXX (`src/pricing/varswap.py`)

Réplication **model-free** du log-contrat (Demeterfi et al. 1999, méthodologie VIX/VSTOXX) :

$$\sigma^2_{var} = \frac{2e^{rT}}{T}\sum_i \frac{\Delta K_i}{K_i^2}\,Q(K_i)
\;-\; \frac{1}{T}\Big(\frac{F}{K_0}-1\Big)^2$$

- $Q(K)$ = option OTM au strike $K$ ; strip de 400 strikes **densifié par la surface SVI**
  (la grille collectée n'a que ~5 strikes/maturité).
- Indice 30 j : interpolation **linéaire en variance totale** entre les deux maturités
  encadrantes (standard VIX) ; mini-VSTOXX $= 100\cdot\sigma_{30j}$ — comparable au VSTOXX officiel.
- **Prime de convexité** $= \sigma_{var} - \sigma_{ATM} \geq 0$ : le varswap « achète »
  tout le skew (poids en $1/K^2$).
- Version **exécutable** : même formule sur la vraie grille au bid/ask → spread de réplication.

### 11.3 Delta-hedge discret (`delta_hedge_pnl`)

On vend la vanille au prix Black ($\sigma_{impl}$), on delta-hedge à fréquence $\Delta t$ le long
de chemins qui réalisent $\sigma_{real}$ :

- $\sigma_{real} = \sigma_{impl}$, sans coûts : P&L centré sur 0, écart-type qui se resserre
  avec la fréquence — *le prix d'une option est le coût de son hedge*.
- **Leçon 1 (coûts)** : des frais de $c$ bps par rebalancement retranchent
  $c\cdot|\Delta\delta|\cdot S$ à chaque tour → il existe une fréquence optimale.
- **Leçon 2 (gamma trading)** : $E[P\&L] \approx \nu\,(\sigma_{impl}-\sigma_{real})$ —
  on gagne en vendant la vol plus cher qu'elle ne se réalise, pas en devinant la direction.

### 11.4 Trade de dispersion (page `/dispersion`, application d'Eq.23)

Short vol indice / long vol composantes, **vega-weighted** (vega net ≈ 0) :

- Niveau d'entrée = $\bar\rho$ implicite. Sous corrélation constante :
  $\sigma_I(\rho) = \sqrt{\Sigma w_i^2\sigma_i^2 + \rho\,[(\Sigma w_i\sigma_i)^2 -
  \Sigma w_i^2\sigma_i^2]}$.
- P&L approché (vols inchangées) : $-Vega\cdot(\sigma_I(\rho_{real}) - \sigma_I(\bar\rho))$
  → breakeven exactement à $\bar\rho$ ; sensibilité « corr-vega » $= Vega\cdot
  \partial\sigma_I/\partial\rho$.
- Coût d'entrée = somme des demi-spreads des jambes (contrats réels depuis les vega € de la
  grille) → décalage du breakeven en $\rho$.

---

### Complément aux Eq. 13-18 — rho (ajout demandé en cours)

$$\rho_{call} = K\,T\,e^{-rT}\,N(d_2)\times 0.01 \qquad
\rho_{put} = -K\,T\,e^{-rT}\,N(-d_2)\times 0.01$$

Sensibilité au taux d'intérêt, exprimée **par point de taux** (même convention que le
vega par point de vol), spot tenu fixe (le forward $F = S e^{(r-q)T}$ se réapprécie
avec $r$). Parité : $\rho_C - \rho_P = K T e^{-rT}\times0.01$. Pour les options
**américaines**, rho est obtenu par re-pricing de l'arbre CRR avec taux ET carry
bumpés ensemble ($q$ fixe — convention identique). Monétisation : $\rho_{EUR} =
\rho \times mult$. Implémenté dans `bs_rho` (european.py), `greeks_american`
(american.py), la chaîne enrichie (`rho`/`eur_rho` dans iv_points) et le risque de
position (`portfolio_rho`).

---

## Conventions du projet

| Quantité | Convention |
|----------|------------|
| Vega | Par **1 point de vol** (0.01 absolu) |
| Theta | Par **jour calendaire** (/365) |
| Dollar Gamma | $\Gamma \times S^2 \times mult / 100$ |
| Day-count | ACT/365 |
| Taux | Continu, annualisé |
| Timestamps | UTC exclusivement |
| Moneyness | Log-moneyness $k = \ln(K/F)$ |
| Surface | En variance totale $w = \sigma^2 T$ |
