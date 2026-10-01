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
  magasin séparé `long_history/`), coupées à la fin de DEVELOPMENT (2025-06-30). La période finale
  n'est pas lue.
- **Univers figé le 2026-10-01** (`research/universe.py`, 40 paires) : les 30 cryptos favorables au
  relevé halal du jour, plus les 10 acceptées par le propriétaire.
- **Journées.** Une journée (00:00–24:00 UTC) est valide si elle compte au moins 20 bougies horaires ;
  sa clôture est celle de sa dernière bougie. Binance a connu des interruptions de quelques heures :
  exiger 24 bougies écarterait à tort des journées entières. Les signaux lisent la dernière clôture
  valide, vieille de 2 jours au plus.
- **Appartenance à la date.** À chaque décision, une paire est éligible si, avec les seules données
  connues à cet instant :
  - sa dernière journée est valide, ainsi qu'au moins 85 des 90 dernières ;
  - son volume journalier médian des 30 derniers jours atteint 1 M$.
- Peu de paires existent au début : 4 en juillet 2018, une dizaine fin 2018. Un portefeuille « des K
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
  reporté au lendemain. Le nombre d'ordres ainsi perdus est enregistré.
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
- **SMA200** : clôture de BTC au-dessus de sa moyenne des 200 derniers jours ;
- **SMA100** : même règle sur 100 jours ;
- **MOM28** : rendement de BTC sur 28 jours positif ;
- **HMM** : chaîne de Markov cachée à 2 états gaussiens sur les rendements journaliers de BTC.
  - Réestimée sur tout le passé à la première décision de chaque mois (au moins 300 rendements), en
    partant des paramètres du mois précédent ; première estimation depuis un départ fixe et déclaré
    (états séparés par la médiane des écarts absolus, persistance 0,95).
  - L'état « calme » est celui de plus faible variance, choisi ainsi d'avance : les moyennes sont trop
    bruitées pour identifier un état.
  - « Investi » si la probabilité **filtrée** de l'état calme dépasse 0,5. Filtrée veut dire calculée
    avec les seules données passées ; la probabilité lissée, qui regarde le futur, n'est jamais
    utilisée.

Détails de H5 : la volatilité est l'écart-type des rendements journaliers (clôtures) des 28 derniers
jours, du panier éligible pour EW, de BTC pour BTC. La cible est la médiane de cette volatilité sur
toutes les décisions jusqu'à la présente incluse (au moins 26, sinon exposition 1).

## 5. Période et validations

- Première décision le lundi 2018-07-02, dernière le 2025-06-23 : 365 semaines. Dans le scénario
  défavorable, la dernière semaine n'a pas de prix de fin dans DEVELOPMENT (jour de retard) : 364.
- **7 validations** d'un an, de juillet à juin (2018-07 → 2019-06, …, 2024-07 → 2025-06).
- Rien n'est estimé sur le futur : les règles sont fixes, et le HMM n'apprend que du passé.

## 6. Mesures

Sur les rendements hebdomadaires nets de coûts, validations enchaînées :
- rendement total, rendement annualisé, Sharpe (moyenne / écart-type × √52), perte maximale
  (journalière), rotation annuelle, exposition moyenne ;
- une série sans variation (portefeuille resté en USDT) a un Sharpe de 0 par convention ;
- **écart de Sharpe** avec la référence, et son intervalle de confiance :
  - rééchantillonnage par blocs circulaires de 8 semaines, les deux séries tirées ensemble ;
  - 20 000 tirages (les bornes sont des quantiles extrêmes : il faut assez de tirages dans chaque
    queue), graine du protocole, mêmes tirages pour tous les essais ;
  - niveau corrigé de Bonferroni pour les 18 essais : 1 − 0,05/18 ≈ 99,72 %, bilatéral ;
- mêmes mesures face à BTC acheté-conservé, à titre d'information.

## 7. Règle : « piste » seulement si TOUT est vrai

1. Borne basse de l'intervalle de l'écart de Sharpe **> 0** (coûts centraux).
2. Sharpe supérieur à celui de la référence dans **au moins 5 validations sur 7**.
3. Écart de Sharpe encore > 0 dans le scénario défavorable (coûts et jour de retard).
4. Perte maximale au plus égale à celle de la référence.
5. Écart de Sharpe encore > 0 sans la validation la plus favorable.
6. Investi au moins 20 % des semaines (un portefeuille toujours en USDT n'est pas une stratégie).

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
- Pour 5 décisions tirées avec la graine, les poids de chaque essai recalculés avec les seules bougies
  1 h antérieures à la décision, puis avec un futur falsifié, doivent être identiques au calcul complet.
- Une mutation (signal qui lit la clôture du lendemain) doit être détectée.
- Données exigées complètes : chaque paire de l'univers présente dans le magasin long jusqu'à 2 jours
  de la fin de DEVELOPMENT, sinon refus. Les empreintes des séries lues sont enregistrées.

## 9. Limites déclarées

- **Survivantes.** L'univers est choisi aujourd'hui : une crypto retirée de la cote ou écartée du
  relevé depuis n'y figure pas. L'appartenance à la date ne corrige que la disponibilité. Ce biais
  favorise surtout les références (EW détient toutes les survivantes) ; son effet sur les écarts est
  inconnu.
- **Peu de données.** 365 semaines, dont environ 45 blocs de 8 semaines : les intervalles seront larges
  et la puissance faible. Un vrai petit avantage peut ne pas passer.
- **Début étroit.** Moins de 10 paires éligibles jusqu'à fin 2018.
- **Exécution.** Prix de 01:00 sans impact de marché ; volumes modestes supposés.
- **Régimes de marché.** La période contient deux grands cycles ; un filtre de tendance peut devoir
  son résultat à deux ou trois épisodes. Le critère 5 ne le corrige qu'en partie.
- Les regards déjà portés sur 2021-2025 par les protocoles précédents ne sont pas indépendants de
  celui-ci.

## Historique

- 2026-10-01, v1 : version initiale, avant toute exécution.
