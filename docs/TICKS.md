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

## Historique

- 2026-10-03 : déclaré avant toute exécution.
