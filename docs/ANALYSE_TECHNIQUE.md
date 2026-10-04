# Analyse technique d'une paire (onglet Marché)

Demande du propriétaire du 2026-10-04 : produire, comme les analystes qui joignent un graphique à leurs signaux, une
lecture du graphique (supports, résistances, cassures, figures, objectifs). Code : `technical/analysis.py` ; route
`GET /analysis?symbol=…&timeframe=1h|4h|1d` ; carte « Analyse technique d'une paire » de l'onglet Marché.

**Information seulement.** Aucune de ces lectures n'a d'avantage démontré : les cassures de pivots ont été testées
(K2 ne tient pas sur l'univers à date, `UNIVERSE_PIT.md`) et les figures sont mesurées en direct par F15 (verdict vers
mars 2027). La carte ne publie aucun signal et n'influence aucun test.

## Données

Bougies 1 h CLÔTURÉES : la série la plus récente du magasin de F15 (166 paires halal depuis 2025-08, mise à jour chaque
heure) ou du magasin de l'univers ; 4 h et 1 jour agrégés depuis 00:00 UTC, bougies complètes seulement. Rien de
postérieur à l'heure de l'analyse n'est lu (testé : falsifier la suite ne change rien).

## Lectures (définitions de `INDICATEURS.md`, aucun réglage nouveau sauf ceux-ci)

- **Supports et résistances** : pivots ZigZag (seuil m × ATR de l'unité de temps, § 1) des 300 dernières bougies,
  regroupés quand ils sont à moins de 0,5 ATR (niveau = moyenne, touches = nombre de pivots) ; au-dessus de la
  dernière clôture : résistance, en dessous : support (un ancien haut cassé devient support).
- **Structure** : dernière cassure BOS / CHoCH sur pivots fractals (§ 5) ; « cassure en cours » si elle date des
  3 dernières bougies.
- **Figures** : le détecteur de F15 (§ 9), celles détectées dans les 20 dernières bougies.
- **Zones** : FVG non comblés et order blocks non invalidés apparus dans la fenêtre affichée (§ 2, 3, 10.6).
- **Repères** : plus haut et plus bas de la veille et de la semaine, chiffres ronds (§ 10.1, 10.3), RSI 14, moyennes
  50 et 200.

## Plan indicatif (long seulement)

Contexte baissier (dernière cassure vers le bas) : pas d'achat. Sinon : entrée à la dernière clôture ; stop sous le
support le plus proche moins 0,25 ATR ; objectifs aux résistances suivantes (au plus 3, au moins 0,2 % au-dessus de
l'entrée) ; R = (objectif − entrée) / (entrée − stop), un premier objectif sous 1 R est signalé. Prix arrondis au pas
de cotation (configuration, paire ajoutée, sinon lu sur Binance). Testé dans `tests/test_technical.py`.

## Suite possible

Mesurer, déclaré à l'avance, si le prix cale réellement aux résistances mécaniques (TP placés sur ces niveaux contre
les TP des analystes, sur l'historique des groupes Telegram audités) : c'est la preuve qui manque pour dire que ces
objectifs sont « plus corrects ».
