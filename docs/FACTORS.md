# Lot 7 — Portefeuilles hebdomadaires (classement et régime) : protocole déclaré avant toute exécution

Demande du propriétaire (2026-10-01) : déclarer à l'avance un classement hebdomadaire entre cryptos et un
modèle de régime « investi ou stablecoin », chercher d'autres modèles, et utiliser tout l'historique
disponible. Spot, sans levier, sans vente à découvert, **aucun ordre** : ce protocole mesure, il ne
publie rien.

Pourquoi cette piste : les 638 essais précédents cherchaient à prédire la direction d'une paire à court
terme, et les coûts effaçaient tout. Ici, la question posée au marché change :
- on rééquilibre **une fois par semaine**, donc les frais pèsent peu ;
- on compare un portefeuille à un autre (lequel détenir, ou faut-il être investi), au lieu de prédire
  chaque mouvement.

Ce document est commité **avant** la première exécution ; toute modification ultérieure est datée en
bas. La conclusion « aucune piste » reste un résultat valable.

## 1. Données et univers

- **Historique long** : bougies 1 h depuis la cotation de chaque paire (`research/long_history.py`,
  magasin séparé `long_history/`), coupées à la fin de DEVELOPMENT (2025-06-30) dès la lecture : le
  fichier entier est chargé puis coupé, et aucune bougie postérieure n'entre dans un calcul (un test
  le vérifie en falsifiant ces bougies).
- **Univers figé le 2026-10-01** (`research/universe.py`, 40 paires) : les 30 cryptos favorables au
  relevé halal du jour, plus les 10 acceptées par le propriétaire.
- **Journées.** Une journée (00:00–24:00 UTC) est valide si elle compte au moins 20 bougies horaires ;
  sa clôture est celle de sa dernière bougie. Binance a connu des interruptions de quelques heures :
  exiger 24 bougies écarterait à tort des journées entières. Les signaux lisent la dernière clôture
  valide, vieille de 2 jours au plus.
- **Appartenance à la date.** À chaque décision, une paire est éligible si, avec les seules données
  connues à cet instant :
  - sa dernière journée est valide, ainsi qu'au moins 85 des 90 dernières ;
  - son volume journalier médian des 30 derniers jours atteint 1 M$ (au moins 25 journées valides
    dans ces 30 jours pour calculer la médiane).
- Peu de paires existent au début : 4 en juillet 2018 (BTC, ETH, LTC, NEO), 10 en octobre 2018,
  11 fin 2018, 20 en juillet 2020, 40 en juin 2025. Un portefeuille « des K
  meilleures » détient alors ce qui existe, à 1/K chacune, le reste en USDT.

## 2. Décisions, exécution, coûts

- **Décision** chaque lundi à 00:00 UTC, avec les clôtures journalières connues à cet instant (la
  dernière est celle de dimanche 23:00–24:00).
- **Exécution** à l'ouverture de la bougie 1 h de **01:00 UTC**, la première qui s'ouvre après que la
  décision est calculable. Le portefeuille est valorisé chaque jour à ce prix de 01:00.
- **Coûts** du scénario central sur chaque montant échangé : 13 points de base par côté (frais 10,
  glissement 2, demi-écart 1). Scénario défavorable : 18 points de base par côté et exécution **un jour
  plus tard**.
- Poids entre 0 et 1, somme au plus 1 ; le reste est en USDT, sans rendement. Les limites d'exposition
  centralisées (`[risk]`) réduiraient l'exposition en service sans changer les mesures de comparaison.
- Si le prix de 01:00 d'une paire manque (interruption de Binance), aucun ordre n'est passé sur elle ce
  jour-là : sa position reste inchangée, valorisée à son dernier prix connu, et l'ordre n'est pas
  reporté au lendemain. Le nombre d'ordres ainsi perdus est enregistré pour chaque essai, dans les
  deux scénarios.
- Les frais sont prélevés sur les positions : après un rééquilibrage, la somme investie ne dépasse
  jamais la valeur du portefeuille (aucun levier, même de quelques points de base).

## 3. Références

| Référence | Définition |
|---|---|
| EW | toutes les paires éligibles à parts égales, rééquilibrées chaque lundi, mêmes coûts |
| BTC | 100 % BTC acheté et conservé |

## 4. Hypothèses et essais (18, fixés ici)

Aucun paramètre n'est ajusté sur les données : chaque variante est écrite ci-dessous.

| Famille | Règle à chaque décision | Variantes | Référence |
|---|---|---|---|
| H1 classement (momentum) | les K paires éligibles au meilleur rendement sur L jours, à 1/K chacune | L ∈ {14, 28, 56} × K ∈ {3, 5} : 6 | EW |
| H2 régime | EW si le régime est « investi », sinon 100 % USDT | 4 (ci-dessous) | EW |
| H3 double momentum | les 5 meilleures sur 28 jours, seulement si le régime est « investi » | régimes SMA200 et MOM28 : 2 | EW |
| H4 tendance par paire | chaque paire éligible reçoit 1/N si son propre rendement sur L jours est positif, sinon sa part reste en USDT | L ∈ {28, 56} : 2 | EW |
| H5 exposition selon la volatilité | portefeuille × min(1, cible / volatilité des 28 derniers jours) | sur EW et sur BTC : 2 | EW ; BTC |
| H6 faible volatilité | les 5 paires éligibles à la plus faible volatilité sur 56 jours | 1 | EW |
| H7 retournement | les 5 paires éligibles au plus faible rendement sur 7 jours | 1 | EW |

Régimes de H2 (tous calculés sur BTC, connus à la décision) :
- **SMA200** : clôture de BTC au-dessus de sa moyenne des 200 derniers jours (dernière clôture
  connue reportée sur les journées non valides, pour qu'une panne ne coupe pas la moyenne) ;
- **SMA100** : même règle sur 100 jours ;
- **MOM28** : rendement de BTC sur 28 jours positif ;
- **HMM** : chaîne de Markov cachée à 2 états gaussiens sur les rendements journaliers de BTC.
  - Rendements logarithmiques entre clôtures valides consécutives (après une panne, un rendement
    couvre 2 à 4 jours) ; Baum-Welch, 100 itérations au plus, tolérance relative 10⁻⁶.
  - Réestimée sur tout le passé à la première décision de chaque mois (au moins 300 rendements), en
    partant des paramètres du mois précédent ; première estimation depuis un départ fixe et déclaré
    (états séparés par la médiane des écarts absolus, persistance 0,95).
  - L'état « calme » est celui de plus faible variance, choisi ainsi d'avance : les moyennes sont trop
    bruitées pour identifier un état.
  - « Investi » si la probabilité **filtrée** de l'état calme dépasse 0,5. Filtrée veut dire calculée
    avec les seules données passées ; la probabilité lissée, qui regarde le futur, n'est jamais
    utilisée.

Détails de H5 : la volatilité est l'écart-type des rendements journaliers (clôtures) des 28 derniers
jours, de BTC pour BTC ; pour EW, du rendement moyen de chaque jour passé des paires éligibles CE
jour-là (composition historique du panier, pas celle du jour de la décision). La cible est la médiane de cette volatilité sur
toutes les décisions jusqu'à la présente incluse (au moins 26, sinon exposition 1).

## 5. Période et validations

- Première décision le lundi 2018-07-02, dernière le 2025-06-23 : 365 semaines. Une seule date de
  rééquilibrage (le lundi) : voir les limites. Dans le scénario
  défavorable, la dernière semaine n'a pas de prix de fin dans DEVELOPMENT (jour de retard) : 364.
- **7 validations** d'un an, de juillet à juin (2018-07 → 2019-06, …, 2024-07 → 2025-06).
- Rien n'est estimé sur le futur : les règles sont fixes, et le HMM n'apprend que du passé.

## 6. Mesures

Sur les rendements hebdomadaires nets de coûts, validations enchaînées :
- rendement total, rendement annualisé, Sharpe (moyenne / écart-type × √52), perte maximale
  (journalière), rotation annuelle, exposition moyenne ; taux sans risque nul (l'USDT ne rapporte
  rien), ce qui défavorise un peu les essais souvent en USDT ;
- une série sans variation (portefeuille resté en USDT) a un Sharpe de 0 par convention ;
- **écart de Sharpe** avec la référence, et son intervalle de confiance :
  - rééchantillonnage par blocs circulaires de 8 semaines, les deux séries tirées ensemble ;
  - 50 000 tirages (les bornes sont des quantiles extrêmes : il faut assez de tirages dans chaque
    queue), graine du protocole, mêmes tirages pour tous les essais ;
  - niveau bilatéral 1 − 0,025/18 ≈ 99,86 % : Bonferroni pour 18 essais, divisé encore par deux, car
    la relecture a mesuré sur données simulées que cet intervalle percentile laisse passer environ
    deux fois le taux nominal dans la queue (0,25 % au lieu de 0,14 %) ;
- face à BTC acheté-conservé : l'écart de Sharpe seulement, à titre d'information (ni intervalle, ni
  validations).

## 7. Règle : « piste » seulement si TOUT est vrai

1. Borne basse de l'intervalle de l'écart de Sharpe **> 0** (coûts centraux).
2. Sharpe supérieur à celui de la référence dans **au moins 5 validations sur 7**.
3. Écart de Sharpe encore > 0 dans le scénario défavorable (coûts et jour de retard).
4. Perte maximale au plus égale à celle de la référence.
5. Écart de Sharpe encore > 0 sans la validation la plus favorable (celle où l'écart de Sharpe de
   la validation est le plus grand).
6. Investi au moins 20 % des semaines, une semaine comptant comme investie si au moins 20 % du
   portefeuille est hors USDT après le rééquilibrage (un portefeuille toujours en USDT, ou presque,
   n'est pas une stratégie).

Verdict enregistré : « N PISTE(S) À CONFIRMER » ou « AUCUNE_PISTE ».

**Lecture déclarée.**
- Une piste n'est **pas** un avantage démontré. Elle sort d'une comparaison de 18 variantes sur des
  données déjà parcourues par le programme (656 essais avec ceux-ci). Elle ne deviendrait une
  stratégie qu'après une confirmation sur des données jamais consultées : la période finale réservée,
  ou une observation prospective.
- Un portefeuille hebdomadaire n'est pas un signal d'entrée avec stop et objectif : il ne passe pas par
  le contrat de signaux actuel. Une piste ouvrirait d'abord un suivi « sur papier » dans le tableau de
  bord.

## 8. Audit des fuites, avant tout résultat

Sans audit réussi, aucun résultat n'est produit et aucun essai n'est enregistré.
- Pour 30 décisions tirées avec la graine, les poids de chaque essai recalculés avec les seules
  bougies 1 h antérieures à la décision, puis avec un futur falsifié, doivent être identiques au calcul
  complet.
- Une mutation (signal qui lit la clôture du lendemain) doit être détectée.
- Simulation : la valeur du portefeuille EW jusqu'à la décision du milieu ne change pas quand les prix
  d'exécution postérieurs sont falsifiés.
- Le code exécuté doit être commité : une exécution sur du code modifié est refusée (sinon
  l'enregistrement ne prouverait pas quel code a tourné).
- Données exigées complètes : chaque paire de l'univers présente dans le magasin long jusqu'à 2 jours
  de la fin de DEVELOPMENT, sinon refus. Les empreintes des séries lues sont enregistrées.

## 9. Limites déclarées

- **Survivantes.** L'univers est choisi aujourd'hui : une crypto retirée de la cote ou écartée du
  relevé depuis n'y figure pas. L'appartenance à la date ne corrige que la disponibilité. Ce biais
  favorise surtout les références (EW détient toutes les survivantes) ; son effet sur les écarts est
  inconnu.
- **Peu de données.** 365 semaines, dont environ 45 blocs de 8 semaines : les intervalles seront larges
  et la puissance faible. Un vrai petit avantage peut ne pas passer.
- **Début étroit.** Moins de 10 paires éligibles jusqu'en septembre 2018.
- **Exécution.** Prix de 01:00 sans impact de marché ; volumes modestes supposés.
- **Régimes de marché.** La période contient deux grands cycles ; un filtre de tendance peut devoir
  son résultat à deux ou trois épisodes. Le critère 5 ne le corrige qu'en partie.
- Les regards déjà portés sur 2021-2025 par les protocoles précédents ne sont pas indépendants de
  celui-ci.
- **Règles venues de la littérature.** Moyenne 200 jours, momentum de 2 à 8 semaines, double
  momentum, exposition selon la volatilité, faible volatilité et retournement court ont été rendus
  populaires parce qu'ils « marchaient » publiquement, sur des périodes qui recouvrent en partie
  2018-2022. Aucun paramètre n'est ajusté par CE code, mais le choix des règles n'est pas
  indépendant des données : le nombre effectif d'essais dépasse 18, et la correction de Bonferroni
  ne le couvre pas. Une piste ne vaudrait rien sans la période finale.
- **Chance de calendrier.** Un seul jour de rééquilibrage (lundi 00:00) ; un autre jour pourrait
  donner un autre résultat. Ce n'est ni mesuré ni corrigé.
- **Audit des fuites.** Il compare des poids et une simulation, pas toutes les mesures ; aux
  décisions où un régime est en USDT, les poids nuls ne prouvent rien pour cet essai.

## 10. Résultats (exécution unique du 2026-10-01)

Exécution `FACT-20261001T192318Z-2ff650`, code du commit `6a68808` (arbre propre), 365 décisions du
2018-07-02 au 2025-06-23, audit des fuites réussi (30 décisions, 20 portefeuilles, simulation),
aucun ordre perdu. Programme : **656 essais** ; période finale non consultée.

**Verdict : AUCUNE_PISTE.** Aucun des 18 essais n'a un intervalle de l'écart de Sharpe entièrement
au-dessus de 0 (critère 1).

| Référence | Rendement annualisé | Sharpe | Perte maximale |
|---|---|---|---|
| EW (toutes les paires éligibles) | +30 % | 0,74 | −82 % |
| BTC acheté-conservé | +50 % | 0,96 | −77 % |

| Essai | Sharpe | Écart | IC de l'écart (99,86 %) | Validations | Perte max. | Investi |
|---|---|---|---|---|---|---|
| DUAL_MOM28 | 1,11 | +0,37 | [−0,40 ; +1,20] | 6/7 | −67 % | 55 % |
| REGIME_MOM28 | 1,08 | +0,33 | [−0,38 ; +1,10] | 3/7 | −64 % | 55 % |
| TSMOM_L28 | 1,06 | +0,31 | [−0,29 ; +0,93] | 4/7 | −48 % | 64 % |
| MOM_L14_K5 | 0,99 | +0,25 | [−0,25 ; +0,69] | 5/7 | −90 % | 100 % |
| TSMOM_L56 | 0,98 | +0,24 | [−0,48 ; +0,95] | 4/7 | −56 % | 62 % |
| REGIME_SMA100 | 0,91 | +0,16 | [−0,66 ; +0,95] | 4/7 | −71 % | 56 % |
| REGIME_HMM | 0,57 | −0,18 | [−0,75 ; +0,35] | 1/7 | −77 % | 84 % |
| REVERSAL_K5 | 0,30 | −0,45 | [−0,94 ; −0,01] | 2/7 | −92 % | 100 % |

(Les 10 autres essais : écarts de −0,09 à +0,19, tous avec un intervalle contenant 0 ; détail dans
`reports/FACT-20261001T192318Z-2ff650/summary.json`.)

Lecture, sans en tirer de règle :
- **DUAL_MOM28** remplit tous les critères sauf le premier : meilleur Sharpe dans 6 validations sur
  7, positif en scénario défavorable et sans sa meilleure validation, perte maximale moindre. Son
  intervalle reste très large ; c'est exactement le cas qu'une sélection parmi 18 essais produit
  par hasard. Ce n'est pas une piste au sens du protocole.
- Les filtres de tendance (H2 à H4) **réduisent nettement la perte maximale** (−48 % à −71 % contre
  −82 %) en restant 40 à 45 % du temps en USDT ; ce protocole ne teste pas cette réduction comme
  critère, et elle n'est donc pas démontrée non plus.
- Le classement pur (H1) garde toute la volatilité des altcoins (pertes maximales de −87 à −92 %).
- **REVERSAL_K5** (acheter les plus fortes baisses de la semaine) fait moins bien que la référence,
  avec un intervalle entièrement sous 0.
- Rien de tout cela ne peut être confirmé sans données jamais consultées : la période finale reste
  réservée, et une nouvelle hypothèse inspirée de ces chiffres serait un nouvel essai.

## Historique

- 2026-10-01, v1 : version initiale, avant toute exécution.
- 2026-10-01, v2 (avant toute exécution, après la relecture indépendante) : niveau 1 − 0,025/18 et
  50 000 tirages ; « investi » = au moins 20 % hors USDT ; moyenne 200 jours de BTC insensible aux
  pannes ; audit sur 30 décisions et contrôle de la simulation ; code commité exigé ; ordres perdus
  enregistrés par essai ; documentation alignée sur le code (liquidité, HMM, panier H5, critère 5,
  comparaison à BTC) ; limites ajoutées (règles de la littérature, chance de calendrier, taux sans
  risque nul, portée de l'audit). Programme : 656 essais si ce protocole est exécuté avant les autres
  protocoles déclarés le même jour.
