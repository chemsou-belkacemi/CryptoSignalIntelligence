# Journal des modifications

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/), simplifié : Ajouté / Modifié / Corrigé, plus Recherche.
Pas de numéro de version : le projet avance par étapes datées sur `main`. Les résultats de recherche sont notés
tels quels, négatifs compris ; aucun ne démontre de gain. Détail : `git log`, [docs/DELIVERY_STATUS.md](docs/DELIVERY_STATUS.md).

## Non publié

- Ajouté : `CONTRIBUTING.md`, `SECURITY.md`, ce journal, modèles GitHub, « Démarrage rapide » du README.

## 2026-10-09 — Assistant de marché, collecteur en shadow

### Ajouté
- Assistant de marché en shadow (régime, repli ou rejet du support, filtres) et test en direct
  **F18_ASSISTANT** pré-inscrit puis démarré ; contrôle sous l'hypothèse nulle : excès −0,03 R [−0,15 ; +0,09],
  aucun biais détectable. Appels envoyés au propriétaire par le relais Telegram.
- Collecteur en shadow (liquidations, carnet, flux, options, attention) : journaux en ajout seul, route
  `GET /collecte`, service Compose `collecteur`. Aucun test, aucun seuil, aucune prédiction avant pré-inscription.

### Recherche
- Étude « météo du marché » (branche `recherche/meteo`) abandonnée avant toute lecture de données réelles :
  l'instrument échoue à deux contrôles synthétiques (`INSTRUMENT_NON_VALIDE`).

### Modifié
- Attention mesurée par Wikipédia et CoinGecko trending à la place de Reddit ; liquidations muettes depuis ce
  réseau, avec réessai horaire.

### Corrigé
- Collecteur : arrêt propre, seuils par paire, débit de Wikipédia (reprise après HTTP 429).
- Relais : le propriétaire est reconnu par ses transferts, jamais par le `/start` d'un inconnu.

## 2026-10-08 — Licence, feu de protection, relevé de liquidité

### Ajouté
- Publication sous **GNU AGPL-3.0-or-later** (`LICENSE`).
- Feu de protection du marché VERT / ORANGE / ROUGE / INCONNU (`GET /meteo`) : outil de gestion du risque issu
  d'une règle déclarée, pas une stratégie ; aucun gain démontré.
- Relevé de liquidité en shadow (carnet, glissement) à chaque signal Telegram et toutes les 15 minutes.

## 2026-10-06 — Relais Telegram et groupes de confiance

### Ajouté
- Carte « Relais Telegram » du tableau de bord ; audit d'un export Telegram avec ses images depuis le tableau de bord.
- Groupes de confiance halal : une paire qu'ils publient est ajoutée si elle se négocie sur Binance Spot.

### Corrigé
- Parseur : cryptos d'une seule lettre lues comme dans BinanceSpotManager ; nom du trader lu en tête du signal,
  même forme canonique que BinanceSpotManager (2026-10-05).

## 2026-10-04 et 2026-10-05 — Méthodes d'analystes rejouées : aucun avantage confirmé

### Ajouté
- Analyse technique d'une paire dans le tableau de bord (bougies, supports et résistances, figures), données de
  contexte gratuites.

### Recherche (une exécution chacune, déclarée avant le code)
- Figures et méthodes d'analystes (ICT/SMC, harmoniques, triangles…) contre placebos : **aucune supérieure au hasard**
  après frais ; order blocks, sweeps et divergences RSI perdent.
- Supports et résistances, données de contexte (21 comparaisons), primes des altcoins : **RIEN**.
- Gestion du propriétaire sur le double creux, stops resserrés : **NON_DEMONTRE**.
- Cassures de ligne de tendance en 1 h : piste confirmée sur 214 paires jamais utilisées (excès +0,12 R), puis
  **NON_CONFIRMEE** sur la période réservée (excès +0,04, R −0,03 après frais).
- IA locale qui lit les graphiques (2 modèles) : **PAS_MIEUX** qu'une règle de moyennes à 72 h.

## 2026-10-03 — Tests en direct F13 à F16, volatilité confirmée

### Ajouté
- Tests en direct **F13** à **F16** pré-inscrits et démarrés (cassures de pivots, figures, signaux Telegram en
  image lus par OCR) ; empreintes gelées.
- Bibliothèque d'indicateurs et de figures (pivots, ZigZag, FVG, order blocks, sweeps, BOS/CHoCH, figures
  chartistes), définitions écrites avant le code.
- Lecture locale (OCR) des signaux publiés en image ; bougies de 1 minute et de 1 seconde pour l'exécution simulée.
- Prévision de volatilité à 24 h branchée en shadow (taille à risque égal, `GET /risk`).

### Recherche
- Lecture unique de la période finale pour la volatilité : prévision à **24 h confirmée** (l'ampleur, pas la
  direction) ; prévisions journalières non confirmées.
- CNN sur images de graphiques : **NE_PASSE_PAS** (AUC 0,506). Volatilité v4 et v5 : aucune amélioration.
- Univers à date (paires retirées comprises) : la piste K2 ne tient pas hors biais de survivance ; portefeuilles
  hebdomadaires, grille et DCA : rien ne bat « garder » ou l'univers ; saisonnalité : rien ne passe.

### Modifié
- Frais centraux à 7,5 points de base par ordre (remise BNB).

## 2026-10-02 — Tests en direct pré-inscrits F1 à F12

### Ajouté
- Cadre des tests en direct : pré-inscription, règles figées par empreinte, journal en ajout seul à empreintes
  chaînées, rapport quotidien ; **F1** à **F12** démarrés (maker/taker, échelles de TP, stablecoins, signaux
  Telegram, modèle A, capitulation, listings, news, purge d'intérêt ouvert, pivots, pression vendeuse, volatilité).
- Garde-fou `tests/test_frozen_running_tests.py` sur les modules gelés.

### Recherche
- Lot 8 v2 (horizons longs, ciblage de volatilité) : **NON_INTERESSANT** face à l'allocation statique.
- Suivi de tendance journalier : **NON_INTERESSANT**. Criblage J (flux, MVRV) : rien ne passe. Criblage K : une
  piste (K2), plus tard réfutée à date.

## 2026-10-01 — ML, marché à terme, portefeuilles : aucun avantage

### Ajouté
- Tableau de bord interactif (`http://127.0.0.1:8503/`) : paire, marché, opportunités, évaluation de signaux, suivi.
- Admission des paires par avis halal ; historique long (bougies 1 h depuis la cotation) ; kit VPS.
- Migration sous Ubuntu (installation par contraintes tirées de `pylock.toml`).

### Recherche
- Lot 5 (méta-labeling) : **NOT_USEFUL**. ML intraday, swing et swing long : **aucun avantage démontré**.
- Positionnement du marché à terme : **AUCUNE_PISTE**. Lot 7, portefeuilles hebdomadaires : **AUCUNE_PISTE**.
- Prévision de volatilité à 1, 3 et 7 jours : **utile pour l'ampleur**, rien sur la direction.

## 2026-09-30 — Lots 0 à 2 : socle, stratégies, walk-forward

### Ajouté
- Données : archives officielles vérifiées par SHA-256, REST paginé, cache Parquet idempotent, quarantaine.
- Features causales sur `available_at`, jointure 15m/1h vers le passé, contrôle de causalité.
- Stratégies A, B et C avec fiches d'hypothèse ; simulateur événementiel, trois scénarios de coûts, ablations.
- Walk-forward purgé, verdict automatique, registre d'expériences ; période finale réservée.
- Contrat TXT V3, publication atomique en shadow, politiques de sortie à empreinte, retour d'exécution v2.
- API locale de lecture et d'évaluation ; CI GitHub Actions ; test anti-secrets ; univers de 16 paires.

### Recherche
- A, B et C **REJECTED** hors échantillon (BTC + ETH, puis 16 paires), avant et après l'audit look-ahead.

### Corrigé
- Paquets `data/` et `news/` masqués par le `.gitignore` (garde `tests/test_repository.py`).
