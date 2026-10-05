# Protocole déclaré : taille et stop de BinanceSpotManager d'après le risque à 24 h de CSI

Déclaré le 2026-10-06, **avant toute donnée** (plan de travail § 2 bis, point 2 ; demande du propriétaire). Suite de
`RISK_SHADOW.md` : CSI publie chaque jour, pour les paires du service, l'ampleur typique des 24 prochaines heures et
une taille relative à risque égal (seule prévision de volatilité confirmée hors échantillon, `VOLATILITY.md` § 19).

## Ce qui tourne (information seulement)

BinanceSpotManager lit `GET /risk` de CSI à chaque signal automatique et note dans les métriques de son routage
(`csi_size`) ce que CSI proposerait : budget × taille relative, et le stop du signal comparé à l'ampleur typique sur
24 h (`stop_inside_move`). **Rien n'est appliqué** : le budget reste celui des réglages de BSM. Une panne de CSI ne
retient aucun signal. Affichage : page Signaux de BSM.

## Hypothèses (déclarées, rien n'est démontré)

- **H1 — taille** : dimensionner les signaux avec la taille relative de CSI réduit la dispersion des résultats par
  trade (en % du capital) sans baisser le résultat moyen. C'est une hypothèse de gestion du risque, pas de gain : la
  taille ne crée aucun avantage directionnel.
- **H2 — stop** : un stop placé à l'intérieur de l'ampleur typique des 24 h est touché plus souvent qu'un stop placé
  au-delà.

## Données

- Positions **terminées** de BSM issues de signaux automatiques **avec** un conseil de CSI disponible (`csi_size`
  `available`), Binance Demo, à partir du 2026-10-06. Jointure : étiquette de position `["signal", id]` → ligne de la
  boîte des signaux → `payload.route.metrics`.
- Résultat d'un trade : PnL réalisé frais compris (frais BNB valorisés comme dans History de BSM) ÷ capital total au
  moment du signal (`total_capital_usdt`).
- **Bloqué aujourd'hui** : BSM tourne sur le VPS ; CSI n'a pas ses données (même blocage que F4/F16). Il faut une
  copie de la boîte des signaux et des positions de BSM **[propriétaire]**.

## Mesure

- **Contrefactuel H1** : résultat_i × taille_i, puis remise à l'échelle pour que le capital total engagé soit le même
  que le réel (Σ budget_i ÷ Σ budget_i × taille_i). La mise à l'échelle linéaire ignore les minimums Binance.
- **Critère H1** (les trois) : rapport des écarts-types (CSI ÷ réel) avec IC 95 % par bootstrap en blocs de jours,
  borne haute < 1 ; rapport sous le 5e centile de 1 000 placebos (tailles relatives permutées entre les trades) ;
  différence des moyennes (CSI − réel) avec IC 95 %, borne basse > −0,05 % du capital par trade.
- **H2** : part des stops touchés par groupe (`stop_inside_move` vrai / faux), intervalles de Wilson et IC de la
  différence ; descriptif seulement sous 30 trades par groupe.
- **Quand** : une seule lecture, quand les deux conditions sont réunies : au moins 60 positions terminées avec
  conseil, et pas avant le 2026-11-13 (revue intermédiaire). Deux essais comptés dans `program_trials` à ce moment.

## Décision

- H1 passe : le propriétaire décide s'il ajoute à BSM une option « multiplier le budget par la taille de CSI », en
  Demo d'abord. H1 échoue ou reste non concluant : le conseil reste une information.
- H2 : information pour le réglage des stops, aucune règle automatique.
- Aucun chiffre de ce protocole n'est une probabilité de gain ni une rentabilité.
