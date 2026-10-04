# Primes coréenne et Coinbase des altcoins : annoncent-elles les gagnants de la semaine ? (déclaré le 2026-10-04, avant code et exécution)

Suite de `CONTEXTE_PREDICTION.md` (aucune variable de contexte ne prédit démontrablement BTC ou ETH : une seule série
par actif, trop peu de cycles). Ici, **en coupe** : chaque semaine, on compare les altcoins entre eux, ce qui donne
beaucoup plus d'observations indépendantes et retire le facteur commun (marché, écart USDT/USD, prime coréenne
générale). Demande du propriétaire du 2026-10-04 (« avancer dans la prédiction »). Code : `research/xsection_premium.py` ;
tests : `tests/test_xsection_premium.py` ; commande : `csi xsection-premium`. DEVELOPMENT seulement.

## Données et univers

Magasin de contexte (`CONTEXTE.md`, lignes HISTORIQUE) : clôtures journalières Binance (paires USDT), Upbit (KRW) et
Coinbase (USD), taux BCE. Univers d'une semaine : les paires de la liste halal **actuelle** (hors BTC et ETH) cotées à
la fois sur Binance et sur Upbit (prime coréenne) ou sur Coinbase (prime Coinbase), avec les données requises ; au
moins **10 paires** dans la semaine, sinon la semaine est écartée.

**Biais de survivance déclaré** : seules les paires cotées aujourd'hui sont présentes (les API d'Upbit et de Coinbase ne
donnent plus les marchés fermés). Une paire qui a eu une forte prime puis s'est effondrée et a été retirée manque : ce
biais pousse plutôt vers « prime haute → meilleure semaine » ; un résultat dans ce sens doit être lu avec cette réserve,
un résultat dans l'autre sens est prudent.

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
- 2026-10-04, avant toute exécution, sur les seules primes (aucun rendement regardé) : une prime journalière hors de
  [−50 % ; +100 %] est une erreur de données (deux jetons sous le même symbole ou changement d'unité) et est ignorée ;
  seul STRAX est touché en pratique (Upbit à −90 % de Binance sur 73 % des jours) ; les pics coréens réalistes
  (suspensions de dépôts, jusqu'à +26 % au 95e centile pour quelques paires) restent.
