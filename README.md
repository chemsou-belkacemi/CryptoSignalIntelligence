# CryptoSignalIntelligence

**Données → recherche → analyse → validation → signaux TXT.** Binance Spot, long uniquement
(BUY ou NO_TRADE). Ce projet **ne crée, ne modifie ni n'annule aucun ordre** : il n'a
aucune route vers les endpoints privés Binance (vérifié par test) et n'utilise aucune clé.
L'exécution reste le rôle exclusif de BinanceSpotManager.

Aucune promesse de rendement. Zéro signal est une réponse valable.

Cahier des charges complet : [docs/PROMPT_MAITRE.md](docs/PROMPT_MAITRE.md) (ajouts du 2026-09-30 en section 26).
Ce qui est testé, non vérifié, bloqué ou non implémenté : [docs/DELIVERY_STATUS.md](docs/DELIVERY_STATUS.md).

## État : lots 0 à 2 livrés, lots 3 et 4 en cours

| Livré | Fichiers |
|---|---|
| **Lot 2** — stratégies B `EMA_PULLBACK_CONTINUATION` et C `RANGE_REENTRY` + fiches | `strategies/`, [fiche B](docs/strategies/EMA_PULLBACK_CONTINUATION.md), [fiche C](docs/strategies/RANGE_REENTRY.md) |
| **Lot 2** — walk-forward purgé, recalibrage par grille grossière (plateau), agrégat hors échantillon, verdict automatique selon les critères déclarés | `research/walk_forward.py`, `research/admission.py`, [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| **Lot 3 (en cours)** — contrat TXT **V3** : DECISION_AT, ENVIRONMENT, deux expirations (EXPIRES_AT pour le message, ENTRY_EXPIRES_AT pour les entrées), ENTRY_COUNT, RR_REFERENCE, EXIT_POLICY_HASH, NEWS_STATUS, probabilité ML liée à sa cible, son horizon et sa calibration | `signals/`, [docs/SIGNAL_FORMAT.md](docs/SIGNAL_FORMAT.md) |
| **Lot 3 (en cours)** — politiques de sortie partagées avec l'exécuteur, identifiées par une empreinte (`config/exit_policies.json`) ; hors shadow, seules les politiques réellement exécutées par le consommateur sont publiées | `backtest/exits.py`, [docs/EXIT_POLICIES.md](docs/EXIT_POLICIES.md) |
| **Lot 3 (en cours)** — retour d'exécution JSONL v2 : message reçu / ordre envoyé / rempli, UNKNOWN sans confirmation, frais réels et devise ; rapport backtest / prospectif (rejeu selon la politique du signal) / Demo avec écarts expliqués | `feedback/`, [docs/FEEDBACK_FORMAT.md](docs/FEEDBACK_FORMAT.md) |
| **Lot 3 (en cours)** — moteur de sorties : TP partiels pondérés, stop remonté (break-even, TP précédent) sans rétroactivité, TP au marché sur déclenchement, sans sortie temporelle ; profil observé de BinanceSpotManager rejoué en variante `profil_BSM` | `backtest/exits.py`, [docs/BSM_PROFILE.md](docs/BSM_PROFILE.md) |
| **Lot 3 (en cours)** — test de contrat contre le vrai parseur de BinanceSpotManager : échec sûr vérifié (un BSM qui ne lit pas le V3 le refuse) ; lecture V3 en cours sur la branche `feat/csi-v2-drop` | `tests/test_bsm_contract.py`, [docs/SIGNAL_FORMAT.md](docs/SIGNAL_FORMAT.md) |
| **Lot 4 (en cours)** — `scan` et `run --mode shadow` : cycle à chaque clôture 15m, attente bornée des bougies 15m/1h, REST des seules bougies manquantes, fenêtres en mémoire, verrou d'instance, reprise sans republication, état de santé | `live/`, [docs/DELIVERY_STATUS.md](docs/DELIVERY_STATUS.md) |
| **Lot 4 (en cours)** — exploitation : Docker Compose (volume persistant, limites, redémarrage, santé « prêt »), arrêt propre sur SIGTERM, tâche planifiée Windows | `Dockerfile`, `docker-compose.yml`, [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| **Lot 4 (en cours)** — sauvegarde cohérente, restauration vérifiée, publication suspendue jusqu'à réconciliation ; travaux lourds en priorité CPU basse | `live/backup.py`, `live/priority.py` |
| **Lot 4 (en cours)** — collecte d'actualités sans LLM (8 sources vérifiées), traçabilité, corrections, reprises regroupées, santé des sources, mode observe | `news/`, [docs/NEWS.md](docs/NEWS.md) |
| Évaluation de signaux externes (groupes Telegram) : vetos, contexte, taux de base, registre, résolution, bilan par source | `external/`, [docs/EXTERNAL_SIGNALS.md](docs/EXTERNAL_SIGNALS.md) |
| Univers de 16 paires screenées (halal, disponibilité, historique, liquidité) | [docs/UNIVERSE.md](docs/UNIVERSE.md) |
| Contrats typés (contexte, résultat, signal V3), config TOML, CLI, `doctor` | `domain/`, `signals/schema.py`, `config.py`, `cli.py` |
| Données : archives officielles avec SHA-256, unités ms/µs par source, REST paginé, cache Parquet idempotent, quarantaine, contrôles qualité | `data/`, [docs/DATA.md](docs/DATA.md) |
| Features causales, jointure 15m/1h vers le passé, régimes sur 4 axes | `features/`, `regimes/` |
| Stratégie A `DONCHIAN_VOLUME_BREAKOUT` + fiche d'hypothèse | `strategies/`, [fiche](docs/strategies/DONCHIAN_VOLUME_BREAKOUT.md) |
| Niveaux arrondis au tick, RR recalculés, vetos NO_TRADE codés | `levels/`, `validation/gates.py` |
| Simulation événementielle, 3 scénarios de coûts, ablations, références, registre d'expériences | `backtest/`, `research/`, [docs/PROTOCOL.md](docs/PROTOCOL.md) |
| Sérialiseur/parseur TXT strict, publication atomique + registre + réconciliation, dossier shadow | `signals/`, [docs/SIGNAL_FORMAT.md](docs/SIGNAL_FORMAT.md) |

**Intégration avec BinanceSpotManager : INTEGRATION_UNVERIFIED** — `main` de BSM refuse le format
V3 (échec sûr, vérifié par test). La branche `feat/csi-v2-drop` lit le V2 et passe au V3 ; le test de
contrat `tests/test_bsm_contract.py` décidera. Sortie vers `signals/shadow` tant que la branche n'est
pas relue et fusionnée.

## Résultats du lot 2 (2026-09-30) : les trois stratégies sont REJECTED

Walk-forward purgé sur DEVELOPMENT : 6 fenêtres de test de 6 mois (2022-07 → 2025-06), BTC + ETH,
recalibrage sur le passé de chaque fenêtre, agrégat hors échantillon en coûts centraux, signaux
indépendants (pas un portefeuille) :

| Stratégie | Trades clos | E[R] net | IC95 (bootstrap par blocs) | Sans filtre 1h | Verdict |
|---|---|---|---|---|---|
| A `DONCHIAN_VOLUME_BREAKOUT` | 288 | −0,23 R | [−0,45 ; −0,03] | −0,23 R | REJECTED |
| B `EMA_PULLBACK_CONTINUATION` | 68 | −0,12 R | [−0,38 ; −0,02] | +0,01 R (IC contient 0) | REJECTED |
| C `RANGE_REENTRY` | 147 | −0,34 R | [−0,58 ; −0,04] | −0,30 R | REJECTED |

- Chiffres identiques sur trois exécutions indépendantes (`reports/WF-*`, registre `experiments/`).
- Le filtre de tendance 1h n'apporte rien : sans lui, chaque stratégie fait aussi bien ou mieux
  (critère 7 du protocole). Il ne sauve aucune stratégie pour autant.
- Aucune stratégie ne justifie l'envoi de signaux à BinanceSpotManager. `analyze` reste utilisable
  pour observer en `shadow`, avec `VALIDATION_STATUS=RESEARCH`.
- Détail par fenêtre, par paire, par année et par régime : `reports/<run_id>/report.md` ; liste : `report`.

### Même protocole sur l'univers de 16 paires (2026-09-30)

| Stratégie | Trades clos | E[R] net | IC95 | Profil BSM | Sans filtre 1h | Verdict | Rapport |
|---|---|---|---|---|---|---|---|
| A `DONCHIAN_VOLUME_BREAKOUT` | 1 920 | −0,11 R | [−0,18 ; −0,05] | −0,12 R | −0,14 R | REJECTED | `WF-20260930T004823Z-06aea6` |
| B `EMA_PULLBACK_CONTINUATION` | 3 672 | −0,17 R | [−0,22 ; −0,10] | −0,17 R | −0,15 R | REJECTED | `WF-20260930T012708Z-878a6a` |
| C `RANGE_REENTRY` | 1 479 | −0,21 R | [−0,31 ; −0,12] | −0,21 R | −0,21 R | REJECTED | `WF-20260930T083431Z-b29864` |

- Avec 5 à 25 fois plus de trades, les intervalles se resserrent et restent entièrement négatifs :
  ces règles perdent de façon nette, pas par manque d'échantillon.
- Le profil d'exécution de BinanceSpotManager (TP au marché, sans sortie temporelle) ne change
  pratiquement rien.
- Biais de survie : l'univers est choisi aujourd'hui parmi des paires encore cotées ; il flatte
  plutôt les résultats, ce qui renforce le rejet.

### Pourquoi elles perdent, et criblage des familles suivantes

Avant frais, A, B et C n'ont pratiquement aucun avantage (≈ +0,01 R, −0,06 R, +0,03 R) : les coûts
font la perte. Le criblage `screen` teste donc d'abord l'avantage BRUT des familles D à I (retest de
cassure, retour sur support, compression, VWAP, force relative, momentum résiduel) sur les 16 paires :
**aucune ne dépasse la dérive ET les coûts**. Seule E (retour au-dessus d'un support enfoncé) montre
un excès positif cohérent, trop faible pour couvrir les frais à 4 h. Détail : [docs/SCREENING.md](docs/SCREENING.md).

## Signaux externes (groupes Telegram)

`evaluate-signal` lit un signal reçu (mêmes formats que BinanceSpotManager), applique les vetos
déterministes, décrit le contexte et donne le **taux de base historique** de la même géométrie
sur la même paire (TP1 avant SL, espérance nette en R), puis un avis REFUSE / DEFAVORABLE /
INDETERMINE / FAVORABLE. Chaque signal est enregistré ; `resolve-signals` mesure ensuite son
issue réelle et `sources` compare chaque groupe à son taux de base. Ce n'est pas une prédiction
du signal : détails et limites dans [docs/EXTERNAL_SIGNALS.md](docs/EXTERNAL_SIGNALS.md).

## Univers de paires

16 paires USDT depuis le 2026-09-30 : BTC, ETH et 14 paires retenues par un screening halal croisé
sur trois sources publiques, plus des critères de disponibilité, d'historique et de liquidité sur
Binance Spot. Règle, sources, exclusions et limites : [docs/UNIVERSE.md](docs/UNIVERSE.md). Ce
projet ne certifie rien ; la liste se modifie dans `config/default.toml`. Les résultats du lot 2
ci-dessus portent sur BTC + ETH seulement.

## Dans Docker Desktop

```powershell
.\scripts\docker-init.ps1      # surveillance shadow + tableau de bord : http://127.0.0.1:8502/dashboard.html
```

Détails (état séparé du dossier local, démarrage automatique, arrêt) : [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

Évaluer un signal Telegram dans l'état Docker (résolu ensuite automatiquement, bilan par groupe au
tableau de bord) : `.\scripts\evaluer-signal.ps1 -Source "Nom du groupe" -Fichier signal.txt`.

API locale (lecture et évaluation seulement, 127.0.0.1:8503) pour l'interface de BinanceSpotManager :
routes, sécurité et intégration prévue dans [docs/API.md](docs/API.md).

## Installation (Windows PowerShell)

Python 3.12+ requis (testé avec 3.14.7). Pas besoin d'activer le venv : on appelle son python.

```powershell
cd C:\Users\chams\Downloads\BinanceSpotManager\CryptoSignalIntelligence
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
# Versions exactes et hashes figés (PEP 751, fonction expérimentale de pip 26) :
.\.venv\Scripts\python.exe -m pip install -r pylock.toml
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
```

Vérifié le 2026-09-30 dans un environnement neuf (Python 3.14.7) : installation depuis
`pylock.toml` puis 55 tests réussis (lot 1). Lot 2 : 81 tests réussis dans ce même environnement,
sans nouvelle dépendance. Régénérer le verrou : voir `pip lock` dans l'historique git.

## Commandes

```powershell
.\.venv\Scripts\python.exe -m crypto_signal_intelligence doctor
.\.venv\Scripts\python.exe -m crypto_signal_intelligence download                      # toutes les paires, 15m+1h, depuis 2021
.\.venv\Scripts\python.exe -m crypto_signal_intelligence download --symbol BTCUSDT --timeframe 15m
.\.venv\Scripts\python.exe -m crypto_signal_intelligence data-quality --symbol BTCUSDT
.\.venv\Scripts\python.exe -m crypto_signal_intelligence validate-causality --symbol ETHUSDT
.\.venv\Scripts\python.exe -m crypto_signal_intelligence backtest --strategy DONCHIAN_VOLUME_BREAKOUT
.\.venv\Scripts\python.exe -m crypto_signal_intelligence walk-forward                  # les 3 stratégies, verdict
.\.venv\Scripts\python.exe -m crypto_signal_intelligence walk-forward --strategy RANGE_REENTRY
.\.venv\Scripts\python.exe -m crypto_signal_intelligence screen                        # criblage brut des familles D à I
.\.venv\Scripts\python.exe -m crypto_signal_intelligence analyze --mode shadow         # toutes les stratégies
.\.venv\Scripts\python.exe -m crypto_signal_intelligence analyze --symbol BTCUSDT --strategy EMA_PULLBACK_CONTINUATION
.\.venv\Scripts\python.exe -m crypto_signal_intelligence evaluate-signal --source "Suhaib" --file signal.txt
.\.venv\Scripts\python.exe -m crypto_signal_intelligence resolve-signals               # issues des signaux évalués
.\.venv\Scripts\python.exe -m crypto_signal_intelligence sources                       # bilan par groupe Telegram
.\.venv\Scripts\python.exe -m crypto_signal_intelligence import-feedback --file feedback.jsonl   # retour Demo du bot
.\.venv\Scripts\python.exe -m crypto_signal_intelligence execution-report              # backtest / prospectif / Demo + écarts
.\.venv\Scripts\python.exe -m crypto_signal_intelligence exit-policies                 # politiques de sortie et empreintes
.\.venv\Scripts\python.exe -m crypto_signal_intelligence scan                          # un cycle : 16 paires × stratégies, shadow
.\.venv\Scripts\python.exe -m crypto_signal_intelligence run --mode shadow             # surveillance continue (Ctrl+C pour arrêter)
.\.venv\Scripts\python.exe -m crypto_signal_intelligence news-sources --check          # vérifie les sources d'actualités
.\.venv\Scripts\python.exe -m crypto_signal_intelligence news --hours 24                # actualités (observe : sans influence)
.\.venv\Scripts\python.exe -m crypto_signal_intelligence dashboard --open              # tableau de bord HTML (state/dashboard.html)
.\.venv\Scripts\python.exe -m crypto_signal_intelligence backup                        # sauvegarde vérifiée de l'état (backups/)
.\.venv\Scripts\python.exe -m crypto_signal_intelligence restore --file backups\<archive>.zip --yes   # puis publication-resume
.\.venv\Scripts\python.exe -m crypto_signal_intelligence report                        # liste des expériences
.\.venv\Scripts\python.exe -m crypto_signal_intelligence report --run-id <IDENTIFIANT>
.\.venv\Scripts\python.exe -m crypto_signal_intelligence signals-reconcile
```

Tests et qualité :

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe check src tests
.\.venv\Scripts\mypy.exe
.\.venv\Scripts\pyright.exe            # imports et noms introuvables (types : mypy)
```

Les fixtures de test sont **synthétiques** : elles vérifient le code, jamais une performance de marché.

## Pas encore disponible (lots suivants)

Lot 4 : calcul incrémental des indicateurs, plafond de RAM des travaux lourds, vérification sur
un vrai VPS/NAS, observation prolongée (écarts théorie/Demo mesurés). Lot 3 : fusion de la branche BinanceSpotManager
(relecture du propriétaire), deux entrées simulées (Binance Demo uniquement). ML (lot 5) ; agents
(lot 6). Détail et preuves : [docs/DELIVERY_STATUS.md](docs/DELIVERY_STATUS.md).
