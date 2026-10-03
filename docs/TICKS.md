# Coûts d'exécution mesurés sur les transactions (point 3 du plan) — déclaré le 2026-10-03 avant exécution

Code : `research/ticks.py`. Tests : `tests/test_ticks.py`. Commande : `csi ticks`. 0 essai : c'est une mesure des
coûts, aucune hypothèse de marché.

**Pourquoi.** Tous nos backtests supposent des coûts (glissement 2 pb pour BTC/ETH, 5 pb ailleurs, demi-écart 1 pb ;
ordre limite rempli dès que la mèche de la bougie touche son prix). Ces hypothèses n'ont jamais été vérifiées sur les
vraies transactions.

**Données.** Archives publiques `aggTrades` de Binance Spot (chaque transaction agrégée : prix, quantité, heure, côté
de l'initiateur), vérifiées contre leur empreinte publiée. Échantillon figé : **BTC, ETH, SOL, XRP, ALGO, ATOM** ×
**12 journées** (le 15 de chaque mois, de juillet 2024 à juin 2025), soit 96 clôtures 15 min par journée. Période de
DEVELOPMENT seulement.

**Limite majeure, déclarée.** Les archives gratuites du Spot n'ont **pas de carnet d'ordres** (niveau 2). Le moteur
`hftbacktest`, qui simule la file d'attente d'un ordre limite, en a besoin : il ne peut pas tourner sur ces données.
Il reste installé pour le jour où un carnet sera disponible (Tardis, payant, décision du propriétaire). On mesure ici
ce que les transactions seules permettent de mesurer honnêtement.

## Mesures (à chaque clôture 15 min)

1. **Écart acheteur / vendeur** : dernier prix payé par un acheteur au marché moins dernier prix reçu par un vendeur au
   marché, dans les 60 s avant la clôture, rapporté au milieu (pb). Médiane et 90e centile par paire.
2. **Petit achat au marché après un délai** (1, 5, 40 s ; 40 s ≈ notre délai de publication) : première transaction
   initiée par un acheteur après clôture + délai, contre le prix de clôture (pb). Moyenne, médiane, 90e centile de
   l'écart absolu. À comparer à ce que nos simulations supposent : glissement + demi-écart.
3. **Ordre limite d'achat** posé à la clôture à prix − x (x = 0 ; 0,1 % ; 0,3 %), vivant 15 ou 60 min :
   - « la bougie dit rempli » : le plus bas de la fenêtre touche le prix (notre règle actuelle) ;
   - « traversé » : une vente au marché s'exécute **strictement sous** le prix → rempli à coup sûr ;
   - « touché seulement » : le prix est atteint sans être traversé → rempli ou non selon la file d'attente.
   La part « touché seulement » est l'optimisme maximal de notre règle de remplissage.

## Lecture déclarée

- Si le petit achat au marché coûte en moyenne moins que glissement + demi-écart supposés, nos coûts centraux sont
  prudents ; s'il coûte plus, ils sont optimistes et seront révisés (révision déclarée dans `PROTOCOL.md`, valable
  pour les exécutions futures).
- Si « touché seulement » dépasse quelques pour cent, la règle de remplissage des bougies est optimiste d'autant et
  sera déclarée comme telle (les résultats passés ne sont pas recalculés) ; un ordre « touché seulement » sera
  compté au mieux comme rempli pour moitié dans la lecture.
- Ce sont des **petits** ordres : aucune mesure de l'impact d'un gros ordre (il faudrait le carnet).

## Résultats (`reports/TICKS-20261003T030058`, 0 essai)

42 millions de transactions lues (6 paires × 12 journées, 96 clôtures par journée), aucune erreur.

| Paire | Écart médian (pb) | Petit achat au marché, écart moyen à la clôture : +1 s · +5 s · +40 s (pb) | Limite au prix, 15 min : bougie · traversé · touché seulement |
|---|---|---|---|
| BTC | 0,00 | −0,2 · −0,1 · +0,0 | 99,4 % · 96,8 % · 2,6 % |
| ETH | 0,04 | −0,3 · −0,3 · −0,1 | 99,0 % · 97,1 % · 1,8 % |
| SOL | 0,66 | −0,3 · −0,1 · −0,1 | 98,8 % · 96,7 % · 2,1 % |
| XRP | 0,92 | −0,3 · −0,3 · −0,1 | 98,9 % · 96,1 % · 2,7 % |
| ATOM | 2,21 | −0,6 · −0,3 · −0,3 | 97,3 % · 92,0 % · 5,3 % |
| ALGO | 5,01 | +0,0 · +0,3 · +0,8 | 97,7 % · 90,7 % · 7,0 % |

Ordres limites plus bas que le prix : « touché seulement » ≤ 0,2 % pour toutes les paires dès 0,1 % sous le prix
(15 ou 60 min), 0 % à 0,3 %.

Lecture (règles déclarées) :
- **Nos coûts sont prudents pour de petits ordres.** Un petit achat au marché paie en moyenne entre −0,6 et +0,8 pb
  de plus que le prix de clôture de la bougie, médiane 0 ; nos simulations supposent 3 pb par côté en central
  (glissement 2 pb + demi-écart 1 pb ; 5 + 1 pour les paires autres que BTC et ETH) et 8 pb en défavorable. Aucune
  révision : la règle prévoyait de réviser seulement si les coûts étaient optimistes. La dispersion à 40 s (90e
  centile de l'écart absolu : 8 pb pour BTC, 15 à 24 pb pour les petites paires) est un risque de prix pendant le
  délai, pas un coût moyen.
- **La règle de remplissage des bougies est optimiste seulement pour une limite posée au prix courant** : 2 à 3 %
  des cas sur les grandes paires, 5 à 7 % sur ATOM et ALGO, sont « touchés sans être traversés » (remplis ou non
  selon la file). Dès 0,1 % sous le prix, l'écart est négligeable (≤ 0,2 %). Les entrées de nos stratégies et des
  signaux sont en général plus bas que le prix courant : l'effet sur les résultats passés est faible ; il est
  déclaré, pas corrigé.
- Limite : petits ordres seulement, 12 journées, période 2024-2025.

## Historique

- 2026-10-03 : déclaré avant toute exécution.
- 2026-10-03 : exécuté (`reports/TICKS-20261003T030058`) ; résultats ajoutés.
