# Étude historique « peur sur les options » : DVOL de BTC au plus haut de 30 jours (pré-inscription, 2026-10-10)

Demande du 2026-10-10, liée au test en direct `F29_PEUR_OPTIONS` (`FORWARD_TESTS.md`). **Pré-inscrite avant tout calcul
sur données réelles** : aucun rendement, aucun événement réel n'a été calculé au moment de l'écrire. Code :
`research/options_peur.py` ; tests : `tests/test_options_peur.py` ; commandes `csi options-peur controle-h0` et
`csi options-peur executer --executer`. **Aucun gain n'est annoncé ni démontré** ; attendu : `RIEN` ou `INSUFFISANT`.

## 1. Question

Quand le DVOL de BTC (indice de volatilité implicite à 30 jours de Deribit) clôt la journée au-dessus du quantile 0,95
de ses 30 clôtures précédentes, un achat de BTCUSDT peu après la clôture rapporte-t-il en moyenne un **rendement net
> 0 à 24 h** ? C'est la version historique du **repli** de F29 : l'asymétrie 25-delta, elle, n'a **pas d'historique
gratuit** ; le DVOL en a un (Deribit, public, depuis le 2021-03-24).

## 2. Données

- **DVOL** : `GET https://www.deribit.com/api/v2/public/get_volatility_index_data`, `currency=BTC`, `resolution=1D`,
  pages suivies par `continuation`, par le client à liste fermée du collecteur (`collect/net.py`, préfixe
  `https://www.deribit.com/api/v2/public/` déjà autorisé) ; `data/http.py` n'est pas élargi.
  - **Format vérifié le 2026-10-10** sur une sonde de quelques jours (mars et avril 2021, valeurs DVOL seulement, aucun
    prix, aucun rendement) : bougie journalière horodatée au **début** du jour UTC, close = valeur à 00:00 du jour
    suivant (égale à la close de sa dernière bougie horaire) ; premier jour disponible : **2021-03-24**.
  - Période : du 2021-03-01 (premier jour réel : 2021-03-24) à la bougie du **2025-06-29** incluse, dont la close tombe le
    2025-06-30 à 00:00. La bougie du 2025-06-30 (close le 2025-07-01) n'est **pas** demandée : elle appartient à la
    période réservée (`research/protocol.py`, DEVELOPMENT jusqu'au 2025-06-30 23:59:59). Le téléchargement refuse
    toute date de fin au-delà (`download_dvol`, testé).
- **Prix** : clôtures 1 h de BTCUSDT du **magasin long** (`<racine>/long_history`, archives officielles de Binance),
  coupées à la fin de DEVELOPMENT avant tout calcul (`load_btc_hours`).

## 3. Règle (figée)

- Jour D : événement si la close du DVOL de D est **strictement supérieure au quantile 0,95** (interpolation linéaire)
  des closes des **30 jours strictement précédents**, avec au moins 24 de ces 30 jours présents.
- **Un événement par 5 jours au plus** (le suivant au moins 5 jours après le précédent retenu).
- Connu à la close (D + 1 jour, 00:00 UTC) ; **entrée** à la clôture de la bougie 1 h de 01:00 UTC le lendemain (une
  heure de marge sur la publication de Deribit, choix prudent).
- Un événement n'est gardé que si sa sortie la plus lointaine (7 jours) se clôt **au plus tard le 2025-06-30
  23:59:59** : aucune bougie de la période réservée n'est lue.

## 4. Mesure

Rendement de BTCUSDT entre la clôture d'entrée et la clôture **24 h** plus tard (horizon de décision) ; 72 h et 7 jours
en descriptif. Brut, net central et net défavorable avec les frais de `forward/costs.py` (ceux de F29 : taker aller-retour,
0,075 % par ordre + 0,02 % par côté pour BTC ; défavorable 0,10 % et écart doublé).

## 5. Décision (rendement net seul) et essai

- `INSUFFISANT` sous **30 événements** mesurés ;
- `PISTE` si l'**IC 99 %** du rendement **net** moyen à 24 h est entièrement > 0 **en central ET en défavorable**
  (`backtest.metrics.day_block_ci`, un bloc par jour d'événement : les événements sont espacés d'au moins 5 jours et
  l'horizon est de 24 h ; 10 000 tirages, graine 20261013, au moins 30 blocs), **et** si cela tient encore **sans
  l'année civile** dont la somme des rendements nets centraux est la plus forte (garde-fou) ;
- sinon `RIEN`.
- **1 essai** au registre (période DEVELOPMENT, `kind = HISTORICAL_STUDY`, stratégie `OPTIONS_PEUR`), compté à
  l'exécution. Une `PISTE` ne change rien en direct : F29 reste mesuré tel qu'il est pré-inscrit.

## 6. Contrôle sous l'hypothèse nulle (déclaré avant son passage, lancé une seule fois)

`csi options-peur controle-h0` (ou `tests/test_options_peur.py::test_controle_h0_options_peur_once`, marqué `slow`) :
- **200 marchés synthétiques indépendants** (graine 20261014) de 2021-03-24 au 2025-06-29 : BTC en **martingale**
  (prix = Π(1 + r), r = σ_t z, z Student à 4 degrés de variance 1, σ_t GARCH(1,1) α = 0,05 β = 0,94, σ moyen horaire
  0,6 %), et un **DVOL synthétique lié aux rendements PASSÉS** : close du jour = 10 + 0,85 × la veille + 0,15 × la
  volatilité réalisée annualisée des 7 jours écoulés (en %) + 40 × la baisse des 7 jours écoulés (si baisse) + bruit
  gaussien (σ 1,5). Aucune information sur le futur : l'espérance du rendement brut à 24 h est nulle.
- **Chaîne exacte** de l'étude (`events`, `measure`, `decide`).
- **Critère** : faux `PISTE` ≤ **0,02** (2 × 0,01). Sinon `ECHEC` : étude non exécutée, 0 essai.
- **Contrôle positif** : +0,5 % ajouté au rendement brut à 24 h de chaque événement (prix de sortie × 1,005) ;
  **puissance** = part des 200 marchés décidés `PISTE`. Critère : **≥ 0,50**, sinon `INSTRUMENT_TROP_FAIBLE` : étude
  **non exécutée**, 0 essai (même règle que `PRICE_ACTION.md` § 11.4).
- Fichier `reports/OPTIONS_PEUR-H0-<commit>/criteres.json`, inscrit dans `research/options_peur.CONTROLE_H0` avec son
  empreinte SHA-256 ; l'exécution refuse s'il manque, a changé ou n'est pas `PASSE`. Pas de seconde itération.

**Résultats** : voir § 9 (passage unique).

**Essai de chronométrage déclaré** (2026-10-10, avant les contrôles sous H0) : il a porté sur le contrôle des tests en
direct F25 à F30 (3 répliques par test, 2 000 tirages ; verdicts vus : tous `NON_DEMONTRE` sauf F29 `INSUFFISANT` ;
générateur non modifié ensuite). Pour cette étude-ci, aucun passage préalable : seuls les tests rapides de
`tests/test_options_peur.py` ont tourné (déterminisme du marché synthétique, nombre d'événements > 30), sans décision
regardée ; générateur non modifié ensuite.

## 7. Garde d'exécution

`csi options-peur executer --executer` refuse, **sans rien télécharger ni lire**, tant que : le contrôle sous H0 n'est pas
inscrit et `PASSE` ; la relecture du code n'est pas inscrite (`CODE_REVIEW`) ; le code n'est pas commité ; l'étude a déjà
été exécutée (registre). **L'exécution réelle n'a pas été lancée** : relecture du coordinateur d'abord.

## 8. Nombre d'événements attendu et puissance (honnête)

Environ 1 560 journées de DVOL. Un quantile 0,95 est dépassé un jour sur vingt en moyenne, mais par épisodes (le DVOL
est très persistant) ; avec 5 jours d'écart minimum, de l'ordre de **30 à 70 événements** (estimation, non mesurée sur
les données réelles), donc **`INSUFFISANT` possible**. L'écart-type d'un rendement de BTC à 24 h est d'environ 3 à 4 %
(plus après une poussée de volatilité) : avec 50 événements, l'erreur type de la moyenne est d'environ 0,5 % ; un
effet vrai de +0,5 % net serait donc **très rarement** déclaré à 99 % (le contrôle positif le dira) ; l'étude ne peut
voir qu'un effet grand.

## 9. Résultats du contrôle sous H0

À inscrire ici après le passage unique, avant toute exécution réelle.

## 10. Limites déclarées

DVOL d'une seule bourse d'options ; une poussée du DVOL suit souvent une forte baisse déjà dans le prix d'entrée (aucun
regard en avant : l'entrée est après la close) ; événements peu nombreux et groupés par régimes ; 2021-2025 est une
seule période ; une `PISTE` historique ne vaut pas validation (le direct F29 tranche, avec l'asymétrie).

## Historique

- 2026-10-10 : pré-inscription, code et tests (aucune donnée réelle hors de la sonde de format du DVOL de 2021).
