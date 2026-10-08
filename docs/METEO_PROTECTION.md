# Feu de protection du marché (« météo du marché »)

Demande du propriétaire du 2026-10-08 : ne pas prendre de risque quand « le marché est merdique, tout le monde
perd ».

> **Ce feu est un outil de gestion du risque, comme la perte maximale du jour. Ce n'est PAS une stratégie et aucun
> gain n'est annoncé ni démontré.**
>
> **Ce feu n'est pas la règle que mesure l'étude en préparation** (« météo du marché », branche `recherche/meteo`,
> pré-enregistrée, pas encore exécutée). L'étude compte 6 dangers (structure, largeur, volatilité, peur, financement,
> pertes récentes) et ne passe au rouge qu'à partir de 3 ; ce feu en garde 3 et passe au rouge sur le seul critère
> « rang ≥ 90 % ». L'étude teste donc une règle **voisine**, pas celle-ci. Ce feu ne sera mesuré que par son propre
> journal (`state/market_light.jsonl`), et cette mesure future comptera comme **un essai** du programme.

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
- **donnée manquante** : si les composantes présentes suffisent déjà à donner ROUGE (par exemple rang ≥ 90 % alors
  que la largeur manque), le feu est **ROUGE**, avec la mention des données manquantes : un garde-fou ne perd pas
  sa protection parce qu'une composante manque. Dans tous les autres cas, **INCONNU** ; la réponse dit quelle
  donnée manque et pourquoi.

Origine des seuils, tous fixés **a priori** (aucun n'a été choisi en regardant des résultats) :
- **repris de F5 et de l'étude** : 90 % (rang de volatilité), 1/3 (largeur), EMA50, 365 jours et 300 valeurs au
  moins pour le rang ;
- **choix nouveaux** de ce feu : 75 % et 1/2 (seuils de l'orange) et la combinaison des trois composantes en un feu.

La décision se fait sur les valeurs exactes (4 paires sur 12 = 1/3 exactement, donc pas « < 1/3 ») ; les arrondis
ne servent qu'à l'affichage.

## 2. Causalité

- Seules les bougies dont `available_at` ≤ maintenant entrent ; une journée n'est utilisée qu'une fois complète.
- Le rang compare la prévision du jour aux seules prévisions des jours d'avant ; la valeur du jour n'entre pas dans
  sa propre référence.
- Les entrées du journal de F12 ne sont lues que si leur horodatage est ≤ maintenant.
- `tests/test_market_light.py` falsifie les bougies futures et ajoute une prévision future : le feu ne change pas ;
  vues au moment où elles existent, les mêmes données le changent bien (contrôle de la mutation).

## 3. Historique du rang

F12 ne tourne que depuis le 2026-10-03 : il n'a pas 365 jours de prévisions. Les jours manquants sont recalculés
**une fois** par la commande `csi meteo-historique` (Docker : `docker compose run --rm tools meteo-historique`), avec
les **mêmes fonctions gelées** que F12 (`volatility_hourly.hourly_frame`, `complete_rows`, `fit_at`,
`quarter_forecasts`), les mêmes paires (configuration ∩ liste halal figée au démarrage de F12), la même graine et le
même réajustement trimestriel sur le seul passé purgé, comme F5 l'a fait pour sa prévision à 7 jours. Un test vérifie
que la valeur recalculée d'un jour est celle que F12 inscrit ce jour-là (sur les données réelles, 6 jours relus :
écart 1,7·10⁻¹⁵). Résultat : `state/market_light_vol_history.json` (avec l'empreinte du code). Pour un même jour, la
valeur du journal de F12 l'emporte.

- **Coût mesuré** : environ 1 min et environ 0,9 Go sur 16 paires. C'est pourquoi le calcul se fait dans le
  conteneur `tools`, **jamais dans la surveillance** : un manque de mémoire dans `monitor` (plafond 2 Go) arrêterait
  les tests en direct F1 à F16. Ni la surveillance ni l'API ne le lancent ; tant qu'il manque, le feu est INCONNU
  (sauf ROUGE déjà acquis par la structure et la largeur) et la raison donne la commande.
- **Tentatives** : un marqueur `state/market_light_vol_history.attempt.json` est écrit (atomiquement) **avant** le
  calcul ; un calcul qui plante ou qui est tué n'est pas relancé avant 20 h (`--force` passe outre).
- **Le rang mélange plusieurs ajustements** : les 365 prévisions de référence viennent de 4 à 5 ajustements
  trimestriels différents du modèle (et ensuite des ajustements de F12). Un changement d'ajustement peut déplacer le
  niveau des prévisions, donc le rang, sans que le marché ait changé. C'est déclaré ici et non corrigé.

## 4. Où le voir

- **API** : `GET /meteo` (même jeton `CSI_API_TOKEN` que les autres routes), recalculée au plus toutes les 2 minutes.
  Réponse : `color`, `explanation` (phrase en français), `reasons`, `missing` (composante, raison), `components`
  (valeur et heure de connaissance de chacune), `thresholds`, `rule`, `note`, `computed_at`, `places_orders: false`.
- **Tableau de bord** (port 8503) : carte « Météo du marché (protection) » en tête de l'onglet Marché, avec le texte
  « outil de prudence, aucun gain démontré ; étude en cours de préparation (branche recherche/meteo) », qui précise
  que l'étude teste une règle voisine, pas ce feu.
- **Journal quotidien** en ajout seul : `state/market_light.jsonl`, un feu par jour UTC, inscrit dès que la
  prévision du jour de F12 est là, au plus tard après 06:00 UTC (même INCONNU). C'est la seule base sur laquelle ce feu sera mesuré ; cette mesure comptera comme un essai.
- **BinanceSpotManager** lit `GET /meteo` dans son garde-fou « Feu de protection CSI », **désactivé par défaut** (voir
  le README de BSM). CSI, lui, ne passe aucun ordre et ne change rien à ses propres signaux.

## 5. Limites (à lire avant de s'y fier)

- **Pas une preuve de gain.** Rien ne dit aujourd'hui que sauter les jours rouges améliore le résultat. Les
  composantes « BTC sous sa moyenne » et « largeur faible » suivent la tendance : elles seront surtout rouges dans
  les marchés baissiers déjà installés, et peuvent faire rater les rebonds (les meilleurs jours suivent souvent les
  pires). L'étude en préparation (branche `recherche/meteo`) mesurera une règle voisine contre des placebos ; ce feu
  lui-même ne sera jugé que sur son propre journal.
- **Dépend de F12.** Quand F12 s'arrête ou se termine (verdict prévu le 2026-12-25), la prévision du jour n'est plus
  inscrite et le feu devient INCONNU ; il faudra alors une autre source de la même prévision.
- **Déploiement** : sans `meteo-historique` lancé une fois, le rang manque et le feu reste INCONNU (sauf ROUGE
  donné par BTC sous son EMA50 et largeur < 1/3).
- **Largeur sur 16 paires** (celles de la configuration), pas sur le marché entier.
- **BSM sur le VPS** ne joint pas forcément le CSI du PC : son réglage par défaut est « aucune action » quand CSI
  est injoignable ou INCONNU.

Code : `src/crypto_signal_intelligence/risk/market_light.py` ; tests : `tests/test_market_light.py`.
