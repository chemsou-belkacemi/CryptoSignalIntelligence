# Données publiques du marché à terme (positionnement)

Demande du propriétaire (2026-10-01) : « ajoute des données publiques ». Les bougies Spot ont été testées
sous tous les angles (stratégies A à C, criblage des familles D à I, ML intraday, ML swing : 626 essais,
aucun avantage démontré). Ce lot ajoute une **source d'information différente** : le positionnement des
autres participants sur le marché à terme USDⓈ-M de Binance.

## Règles

- **Lecture seule de données publiques.** Aucune clé, aucun ordre, aucun compte. La liste blanche de
  `data/http.py` n'ouvre que des routes publiques de marché :
  - financement ;
  - prime ;
  - intérêt ouvert ;
  - ratios acheteurs/vendeurs ;
  - archives `/data/futures/um/`.

  Les routes d'ordres, de compte, de levier et de flux utilisateur restent refusées avant tout appel
  réseau (`tests/test_data.py`).
- **Aucun contrat à terme n'est négocié.** Ces données servent d'information. Les signaux de CSI restent
  des achats Spot de l'univers validé par le propriétaire (`docs/UNIVERSE.md`). Si le propriétaire ne
  souhaite pas utiliser des données de produits dérivés, même comme simple information, ce lot se retire
  sans toucher au reste.
- **Rien n'entre dans une décision** tant qu'un protocole déclaré à l'avance n'a pas démontré un avantage
  hors échantillon.

## Ce qui est lu

| Donnée | Route publique | Historique en archives | Sens |
|---|---|---|---|
| Financement | `/fapi/v1/fundingRate`, `/fapi/v1/premiumIndex` | mensuel, depuis 2020 (HBAR : 2021-03) | taux payé à chaque règlement par les acheteurs aux vendeurs s'il est positif (l'inverse s'il est négatif) |
| Prime du perpétuel | `/fapi/v1/premiumIndexKlines` | bougies 1 h, mensuel, depuis 2020 | écart du perpétuel sur l'indice Spot |
| Intérêt ouvert | `/futures/data/openInterestHist` (30 derniers jours) | « metrics » 5 min, journalier, depuis 2021-12 (BTC : 2020-09) | valeur des contrats ouverts |
| Comptes acheteurs/vendeurs | `/futures/data/globalLongShortAccountRatio` | dans « metrics » | tous les comptes |
| Gros comptes | `/futures/data/topLongShortPositionRatio` | dans « metrics » | positions des plus gros comptes |
| Achats/ventes agressifs | `/futures/data/takerlongshortRatio` | dans « metrics » | ordres au marché |

Les 16 paires de l'univers ont toutes un contrat perpétuel. La couverture a été vérifiée le 2026-10-01
par requêtes publiques.

## Tableau de bord : carte « Marché à terme »

Dans l'onglet « Analyser une paire », sous la situation actuelle :
- les valeurs du moment de la paire ;
- pour chacune, son **rang** dans son propre historique récent : part des valeurs de la fenêtre
  strictement inférieures ; la fenêtre est de 90 jours pour le financement, 30 jours pour la prime et
  environ 20 jours pour le reste ;
- une période en cours, non terminée, n'est jamais comptée.

La carte est une description, jamais un signal. Elle relit les données au plus toutes les 5 minutes
(`[derivatives] live_cache_seconds`). Route : `GET /derivatives?symbol=ETHUSDT` (docs/API.md).

## Historique et disponibilité (recherche)

Hypothèses déclarées, prudentes, puisqu'aucune heure de publication réelle n'est connue :
- financement connu 1 minute après son règlement (`funding_latency_seconds`) ;
- prime connue comme une bougie : fin de l'heure + 2 s ;
- valeurs « metrics » de 5 minutes connues à la **fin** de leur période + 2 s
  (`metrics_latency_seconds` = 302).

Ce dernier choix est confirmé par l'API : à 09:39, la dernière ligne d'achats agressifs horaires était
celle de 08:00, c'est-à-dire de la période 08:00–09:00.

Les jointures se feront vers le passé, sur `available_at`, avec un âge maximal. Le contrôle « données
tronquées / futur falsifié » et un test de mutation seront exigés, comme pour les bougies.

## Suite

1. Téléchargement de l'historique (archives vérifiées par SHA-256), puis contrôle de qualité.
2. Criblage **déclaré ici avant exécution** :
   - quelques conditions de positionnement fixées à l'avance ;
   - horizons de 1, 3 et 7 jours ;
   - excès sur la dérive et seuil de coûts ;
   - IC de Student par blocs calendaires, corrigé du nombre d'essais.

   Les essais seront comptés dans le programme. Relecture indépendante avant exécution.
3. Une condition qui passerait le criblage ne deviendrait une stratégie qu'avec une fiche, un
   walk-forward et une confirmation sur des données jamais consultées.

## Historique

- 2026-10-01 : données du moment dans le tableau de bord (carte « Marché à terme ») ; liste blanche
  publique du marché à terme ; couverture des archives vérifiée.
