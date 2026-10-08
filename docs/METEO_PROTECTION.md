# Feu de protection du marché (« météo du marché »)

Demande du propriétaire du 2026-10-08 : ne pas prendre de risque quand « le marché est merdique, tout le monde
perd ».

> **Ce feu est un outil de gestion du risque, comme la perte maximale du jour. Ce n'est PAS une stratégie et aucun
> gain n'est annoncé ni démontré.** Une étude séparée, [`METEO_MARCHE.md`](METEO_MARCHE.md) (branche
> `recherche/meteo`, pré-enregistrée, pas encore exécutée), mesurera plus tard ce qu'il vaut. Le résultat de cette
> étude peut très bien être « rien » ; le feu ne change pas d'ici là.

## 1. La règle (déclarée, sans aucun réglage optimisé)

Trois composantes, calculées sur les données déjà présentes dans la surveillance :

| Composante | Calcul exact | Connue à |
|---|---|---|
| **Volatilité prévue** | Prévision à 24 h de BTCUSDT par **H1_HAR_PROFILE** (HAR + profil heure × jour), seule prévision de volatilité **confirmée** hors échantillon (`VOLATILITY.md` § 19). Elle est calculée chaque jour à l'origine 00:00 UTC par le test en direct **F12** ; le feu **lit** son journal (`forward/F12_VOL_FORWARD.jsonl`), sans rien y écrire et sans modifier F12 (gelé). **Rang** = part des prévisions des **365 jours précédents** (jours `d − 365` à `d − 1`) **strictement inférieures** à celle du jour ; il en faut au moins 300. Prévision de plus de 36 h : périmée. | heure d'inscription de la prévision par F12 (≈ 00:10 UTC) |
| **Structure BTC** | Clôture journalière de BTCUSDT **≤** son EMA50 journalière. Clôture journalière = clôture de la bougie 1 h de 23:00 d'une journée UTC **complète** (24 bougies). EMA : α = 2/51, départ par la moyenne simple des 50 premières clôtures complètes des 500 derniers jours (le départ est oublié à 10⁻⁸ près). Dernière journée complète plus vieille que 2 jours : absente. | `available_at` de la bougie de 23:00 (≈ 00:00:02 UTC) |
| **Largeur** | Parmi les paires de la configuration (`data.symbols`, BTC compris) qui ont la même dernière journée complète que BTC et au moins 50 clôtures, part de celles dont la clôture est **>** leur EMA50 journalière. Moins de 10 paires éligibles : absente. | la plus tardive des `available_at` utilisées |

Couleur :

- **ROUGE** si le rang de volatilité est **≥ 90 %**, OU si BTC est sous son EMA50 **ET** la largeur est **< 1/3** ;
- sinon **ORANGE** si le rang est **≥ 75 %**, OU si BTC est sous son EMA50, OU si la largeur est **< 1/2** ;
- sinon **VERT** ;
- **INCONNU** si une composante manque ; la réponse dit laquelle et pourquoi.

Les seuils (90 %, 75 %, 1/3, 1/2, 365 jours, EMA50) sont des choix **a priori**, repris de F5 et de
`METEO_MARCHE.md`. Aucun n'a été choisi en regardant des résultats. La décision se fait sur les valeurs exactes (4
paires sur 12 = 1/3 exactement, donc pas « < 1/3 ») ; les arrondis ne servent qu'à l'affichage.

## 2. Causalité

- Seules les bougies dont `available_at` ≤ maintenant entrent ; une journée n'est utilisée qu'une fois complète.
- Le rang compare la prévision du jour aux seules prévisions des jours d'avant ; la valeur du jour n'entre pas dans
  sa propre référence.
- Les entrées du journal de F12 ne sont lues que si leur horodatage est ≤ maintenant.
- `tests/test_market_light.py` falsifie les bougies futures et ajoute une prévision future : le feu ne change pas ;
  vues au moment où elles existent, les mêmes données le changent bien (contrôle de la mutation).

## 3. Historique du rang

F12 ne tourne que depuis le 2026-10-03 : il n'a pas 365 jours de prévisions. Les jours manquants sont recalculés
**une fois** par la surveillance (`ensure_vol_history`, fil des tests en direct, ≈ 20 s et ≈ 1 Go sur 16 paires)
avec les **mêmes fonctions gelées** que F12 (`volatility_hourly.hourly_frame`, `complete_rows`, `fit_at`,
`quarter_forecasts`), les mêmes paires (configuration ∩ liste halal figée au démarrage de F12), la même graine et le
même réajustement trimestriel sur le seul passé purgé, comme F5 l'a fait pour sa prévision à 7 jours. Un test vérifie
que la valeur recalculée d'un jour est celle que F12 inscrit ce jour-là. Résultat :
`state/market_light_vol_history.json` (avec l'empreinte du code). Pour un même jour, la valeur du journal de F12
l'emporte. Si la fenêtre reste incomplète, au plus un nouveau calcul par jour. L'API ne fait jamais ce calcul : tant
qu'il manque, le feu est INCONNU (« historique du rang pas encore calculé »).

## 4. Où le voir

- **API** : `GET /meteo` (même jeton `CSI_API_TOKEN` que les autres routes), recalculée au plus toutes les 2 minutes.
  Réponse : `color`, `explanation` (phrase en français), `reasons`, `missing` (composante, raison), `components`
  (valeur et heure de connaissance de chacune), `thresholds`, `rule`, `note`, `computed_at`, `places_orders: false`.
- **Tableau de bord** (port 8503) : carte « Météo du marché (protection) » en tête de l'onglet Marché, avec le texte
  « outil de prudence, aucun gain démontré ; étude en cours ».
- **Journal quotidien** en ajout seul : `state/market_light.jsonl`, un feu par jour UTC, inscrit dès que la
  prévision du jour de F12 est là, au plus tard après 06:00 UTC (même INCONNU). Il sert à comparer plus tard.
- **BinanceSpotManager** lit `GET /meteo` dans son garde-fou « Feu de protection CSI », **désactivé par défaut** (voir
  le README de BSM). CSI, lui, ne passe aucun ordre et ne change rien à ses propres signaux.

## 5. Limites (à lire avant de s'y fier)

- **Pas une preuve de gain.** Rien ne dit aujourd'hui que sauter les jours rouges améliore le résultat. Les
  composantes « BTC sous sa moyenne » et « largeur faible » suivent la tendance : elles seront surtout rouges dans
  les marchés baissiers déjà installés, et peuvent faire rater les rebonds (les meilleurs jours suivent souvent les
  pires). C'est exactement ce que `METEO_MARCHE.md` mesurera, contre des placebos.
- **Dépend de F12.** Quand F12 s'arrête ou se termine (verdict prévu le 2026-12-25), la prévision du jour n'est plus
  inscrite et le feu devient INCONNU ; il faudra alors une autre source de la même prévision.
- **Largeur sur 16 paires** (celles de la configuration), pas sur le marché entier.
- **BSM sur le VPS** ne joint pas forcément le CSI du PC : son réglage par défaut est « aucune action » quand CSI
  est injoignable ou INCONNU.

Code : `src/crypto_signal_intelligence/risk/market_light.py` ; tests : `tests/test_market_light.py`.
