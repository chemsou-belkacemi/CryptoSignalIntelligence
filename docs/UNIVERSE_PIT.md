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

## Historique

- 2026-10-03 : déclaré avant toute exécution.
