# Univers à date (point 2 du plan de travail) — déclaré le 2026-10-03 avant exécution

Code : `research/pit_universe.py`. Tests : `tests/test_pit_universe.py`. Commandes : `csi pit-universe` (construction)
et `csi screen-pivot-pit` (criblage K à date).

**Pourquoi.** L'univers de recherche (40 paires, `research/universe.py`) a été choisi en 2026 parmi les paires encore
cotées. Les cryptos retirées de la cote, souvent celles qui ont le plus mal fini, manquent donc à tous les backtests :
c'est le biais de survivance, déclaré partout mais jamais mesuré. Binance publie encore les bougies de ses paires
retirées (statut `BREAK` : 253 paires USDT au 2026-10-03, dont des renommages comme MATIC → POL ou FTM → S).

## 1. Construction

- **Recensement** : toutes les paires USDT de `exchangeInfo` (cotées et `BREAK`), moins les exclusions structurelles du
  cadre (`config/halal_screen.yaml` : stablecoins et monnaies, tokens adossés, à levier) et les cryptos jugées
  **haram** par au moins une source du relevé halal (`config/halal_screening.toml`). Les cryptos retirées n'ont, pour
  la plupart, **pas de relevé halal** (statut `NON_RELEVE`) : elles entrent dans cette mesure de recherche, jamais dans
  une liste de trading ; c'est déclaré.
- **Bougies journalières** de chaque paire par l'API publique (`/api/v3/klines`, liste blanche), jusqu'à la fin de
  DEVELOPMENT (la période finale n'est pas lue).
- **Top 40 à date** : au 1er de chaque mois M depuis janvier 2018, les 40 paires de plus forte médiane de volume en
  USDT sur les 30 journées qui finissent la veille de M (au moins 30 journées) ; rien de postérieur à M n'est lu.
- **Historique 1 h** (archives publiques, magasin long) des paires entrées au moins une fois dans ce top et absentes
  de l'univers de recherche.
- Rapport : part des places du top tenues par l'univers de recherche, par des paires cotées hors univers, par des
  paires retirées ou renommées, par année.

- **Exclusions historiques** (ajoutées le 2026-10-03 avant le criblage K à date, après la lecture du premier
  recensement) : les listes du cadre ne visent que les actifs encore cotés ; s'y ajoutent les stablecoins retirés
  (BUSD, PAX, USDSOLD, UST…), les tokens à levier retirés (BULL, BEAR et leurs variantes) et LEND, prédécesseur
  d'AAVE (haram pour au moins une source). L'appartenance est recalculée sans nouveau téléchargement.

## 2. Criblage K à date (4 essais)

Les conditions K1 et K2 du criblage K (`SCREENING.md`), mêmes cadres (4 h, 1 jour), mêmes horizons, mêmes fonctions
(`aggregate`, `events_of`, `collect`), même mesure : un événement ne compte que si sa paire est dans le top 40 **du
mois de l'événement**. Paires retirées comprises ; un événement dont la sortie tombe après la dernière bougie d'une
paire retirée n'a pas de rendement et ne compte pas (limite : la toute fin d'une paire retirée, souvent la pire, est
perdue). **4 essais** de plus au programme.

**Lecture déclarée.** La question est : **K2 à 1 jour tient-il hors biais de survivance ?** S'il passe encore (même
règle : brut > seuil de coûts ET borne basse de l'IC95 de l'excès > 0), la piste se renforce ; s'il ne passe plus, son
résultat sur les survivantes venait au moins en partie du biais, et F10, F13 et F14 sont lus avec cette réserve.
Aucun autre usage : aucun seuil, aucun réglage n'est choisi sur ce résultat.

### Résultat (`SCREEN-20261003T040325Z-7c5fd1`, programme : 787 essais)

263 paires passées par le top 40 à date ; historique 1 h de 222 paires hors univers téléchargé (0 échec). Excès
sur la dérive de la paire, IC à 95 % (blocs de jours), seuil de coûts central.

| Condition | Horizon | Événements | Paires | Brut | Excès | IC95 de l'excès | Paires > 0 | Années > 0 | Passe |
|---|---|---|---|---|---|---|---|---|---|
| K1 support, 4 h | 24 h | 13 704 | 253 | −0,03 % | −0,50 % | [−0,74 ; −0,26] | 40 % | 1/7 | non |
| K1 support, 1 jour | 7 jours | 1 703 | 200 | −0,59 % | −3,59 % | [−5,84 ; −1,31] | 35 % | 0/7 | non |
| K2 cassure, 4 h | 24 h | 18 118 | 253 | +0,25 % | −0,19 % | [−0,45 ; +0,07] | 46 % | 2/7 | non |
| **K2 cassure, 1 jour** | **7 jours** | **2 913** | **212** | **+2,13 %** | **+0,01 %** | **[−2,14 ; +2,55]** | **39 %** | **4/7** | **non** |

**Lecture déclarée : K2 à 1 jour ne tient pas hors biais de survivance.** Sur les 40 survivantes, son excès était de
+2,04 % à 7 jours (IC [+0,19 ; +4,55]) ; sur l'univers à date, il tombe à +0,01 %, avec 39 % des paires positives
(78 % sur les survivantes). Le résultat des survivantes venait au moins en partie du biais : une cassure de résistance sur une paire
qui a survécu jusqu'en 2026 est rétrospectivement une bonne affaire, pas sur une paire qui a ensuite disparu.

- **F10, F13 et F14 continuent** (ils sont pré-enregistrés et ne coûtent rien), mais sont lus avec cette réserve :
  un succès en direct sur des paires choisies aujourd'hui garderait le même biais de sélection.
- **K1 (rebond sur support) est franchement négatif à date** (IC entièrement sous 0, à 1 et 7 jours) : acheter un
  rebond sur un support d'une paire qui finit par mourir est le pire cas. Ce n'est pas une piste à la baisse (CSI
  reste acheteur seulement), mais c'est un veto possible, qui serait à déclarer avant tout usage.
- Limites : 8 paires passées par le top 40 n'ont pas d'historique 1 h (AION, ANT, GAL, JST, LEVER, SC, SKL, SUN :
  archives absentes sous ce nom) ; la toute fin d'une paire retirée est perdue (déclaré).

## Historique

- 2026-10-03 : déclaré avant toute exécution ; exécuté le même jour (4 essais, programme 787).
