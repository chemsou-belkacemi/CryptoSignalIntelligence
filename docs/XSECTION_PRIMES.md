# Primes coréenne et Coinbase des altcoins : annoncent-elles les gagnants de la semaine ? (déclaré le 2026-10-04, avant code et exécution)

Suite de `CONTEXTE_PREDICTION.md` (aucune variable de contexte ne prédit démontrablement BTC ou ETH : une seule série
par actif, trop peu de cycles). Ici, **en coupe** : chaque semaine, on compare les altcoins entre eux, ce qui donne
beaucoup plus d'observations indépendantes et retire le facteur commun (marché, écart USDT/USD, prime coréenne
générale). Demande du propriétaire du 2026-10-04 (« avancer dans la prédiction »). Code : `research/xsection_premium.py` ;
tests : `tests/test_xsection_premium.py` ; commande : `csi xsection-premium`. DEVELOPMENT seulement.

## Données et univers

Magasin de contexte (`CONTEXTE.md`, lignes HISTORIQUE) : clôtures journalières Binance (paires USDT), Upbit (KRW) et
Coinbase (USD), taux BCE. Univers d'une semaine : les paires de la liste halal **actuelle** (hors BTC, ETH et PAXG) cotées à
la fois sur Binance et sur Upbit (prime coréenne) ou sur Coinbase (prime Coinbase), avec les données requises ; au
moins **10 paires** dans la semaine, sinon la semaine est écartée.

**Biais de survivance déclaré** : l'univers est la liste halal Binance d'aujourd'hui (de 14 paires en 2019 à 64 en 2025 :
les premières années ne contiennent que des paires qui ont longtemps survécu) ; Upbit ne donne plus les marchés fermés
(Coinbase, si). Son **sens n'est pas établi** : une paire retirée après un avertissement d'Upbit (prime sous la moyenne)
manque, ce qui pousse vers `HAUT_MOINS` ; une paire effondrée après une suspension de dépôts (prime haute) manque, ce
qui pousse vers `HAUT_MIEUX`. Tout résultat est lu avec cette réserve.

## Variables (4, fixées ici)

Prime du jour `d` d'une paire : coréenne = clôture Upbit en wons ÷ wons par dollar BCE (reporté vers l'avant) ÷ clôture
Binance − 1 ; Coinbase = clôture Coinbase en dollars ÷ clôture Binance − 1. Décision le lundi `T` (00:00 UTC), avec les
journées jusqu'au dimanche `T − 1` :

| Code | Calcul |
|---|---|
| `KR_LEVEL` | moyenne de la prime coréenne de la paire sur les 7 jours `T − 7` à `T − 1` (au moins 5 présents) |
| `KR_JUMP` | `KR_LEVEL` − moyenne de la prime coréenne sur les 28 jours précédents (`T − 35` à `T − 8`, au moins 20 présents) |
| `CB_LEVEL` | idem avec la prime Coinbase |
| `CB_JUMP` | idem avec la prime Coinbase |

## Mesure

Rendement de la semaine suivante comme `XSECTION.md` : achat à la clôture du lundi `T` (un jour de retard sur la
décision), vente à la clôture du lundi `T + 7` ; une paire sans clôture à la sortie est valorisée à sa dernière clôture
de la semaine. Chaque semaine : paires classées par la variable ; **écart = rendement moyen du tiers haut − rendement
moyen du tiers bas** (au moins 3 paires par tiers). Série hebdomadaire des écarts ; moyenne et IC
(`intervals.calendar_mean_ci`, blocs de 56 jours, au moins 10 blocs) au niveau **1 − 0,05/4** (4 comparaisons, 4 essais
au registre). Seules les semaines dont la sortie est dans DEVELOPMENT (au plus tard le 2025-06-30) comptent.

**Lecture** pour chaque variable : `HAUT_MIEUX` si l'IC est entièrement au-dessus de 0 (forte prime → meilleure semaine
relative) ; `HAUT_MOINS` s'il est entièrement en dessous ; `RIEN` sinon.

Descriptif, hors verdict : nombre de semaines et de paires par semaine ; tiers haut − moyenne et tiers bas − moyenne
(lecture long seulement : acheter le tiers haut, ou éviter le tiers bas) ; corrélation de rang moyenne ; écart par
année et par moitié de période. Pas de coûts dans le verdict (question de prédiction) : un résultat non nul resterait à
tester en direct, coûts et rotation compris.

Causalité testée (falsifier les données postérieures au dimanche ne change aucune variable ; pic au jour `d` → première
variation au lundi suivant) ; relecture indépendante avant l'exécution unique.

## Historique

- 2026-10-04 : déclaré avant tout code et toute exécution.
- 2026-10-04, relecture avant l'exécution (primes, prix et comptages seulement, aucun rendement) : la borne
  [−50 % ; +100 %] envisagée d'abord est **abandonnée** (elle retirait de vraies primes : HBAR à +100 à +343 % sur
  28 jours début 2020, LSK, IOST, IOTX côté Coinbase, et laissait passer une collision) ; remplacée par une liste
  nommée de séries non comparables : **STRAX exclue** (Upbit à −90 % de Binance jusqu'au 2024-03-27 : ancien jeton sous
  le même symbole ; changement d'unité ÷10 sur Binance en mars 2024) et **IOTX exclue du côté Coinbase** (prime
  structurelle d'environ +22 % au-dessus des autres paires sur 2021-11 à 2025-06, jeton non interchangeable). Aucune
  autre collision durable n'est visible (médiane glissante de la prime de chaque paire moins celle des autres) ;
  - descriptif ajouté : contribution de chaque paire à l'écart moyen (5 premières) et écart sans la paire qui contribue
    le plus ;
  - **faible dispersion** déclarée : l'écart interquartile des primes entre paires est d'environ 5 points de base
    (Coinbase) et 14 (Corée) en médiane ; les semaines calmes sont classées en partie par du bruit de cotation (paires
    peu échangées sur Coinbase : 38 à 80 k$ par jour pour MTL, POWR, CELR) ; un `RIEN` traduira surtout un manque de
    puissance, pas l'absence d'effet ; aucun filtre de volume n'est appliqué ;
  - tests ajoutés (rendement du lundi au lundi vérifié dans l'exécution, taux BCE du vendredi le dimanche, prime
    Coinbase à la main et au jour près, niveau de l'IC).
