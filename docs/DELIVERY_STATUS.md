# Preuves de livraison (point 22 du cahier des charges)

Mise à jour : 2026-09-30. Quatre statuts seulement :

- **TESTÉ** : implémenté, et vérifié par des tests exécutés (et sur données réelles quand c'est indiqué) ;
- **NON VÉRIFIÉ** : implémenté, mais jamais exécuté dans les conditions réelles visées ;
- **BLOQUÉ** : dépend d'un accès ou d'une décision extérieure à ce projet ;
- **NON IMPLÉMENTÉ**.

Un logiciel livré ne signifie pas qu'une stratégie est validée pour être utilisée : les trois
stratégies actuelles sont **REJECTED** hors échantillon (README). Vérification de la suite :
`pytest`, `ruff check src tests` et `mypy` sur tout le paquet (62 fichiers), le 2026-09-30.

## Données, recherche, signaux

| Fonctionnalité | Statut | Preuve ou manque |
|---|---|---|
| Téléchargement archives + REST, SHA-256, quarantaine, qualité | TESTÉ | tests `test_data*` ; 16 paires 15m/1h téléchargées depuis 2021 (2026-09-30), 0 quarantaine |
| Features causales, jointure 15m/1h, régimes | TESTÉ | `validate-causality`, tests d'invariance au futur |
| Stratégies A, B, C (règles calculables, fiches complètes point 10) | TESTÉ | tests unitaires ; walk-forward réel → REJECTED (BTC+ETH), univers 16 paires en cours |
| Simulateur : fills LIMIT stricts, gaps, TP/SL même bougie ambigus, pas de plus haut pré-entrée crédité (point 14) | TESTÉ | `test_simulator`, `test_exits` ; non-régression sur 246 trades réels |
| Frais sur quantités remplies, spread compté une fois (point 14) | TESTÉ | frais par remplissage pondéré (`pnl_per_unit`) ; glissement + demi-spread appliqués une fois à l'entrée au marché et à chaque sortie au marché |
| Walk-forward purgé, verdict automatique, registre d'expériences | TESTÉ | `test_walk_forward` ; 17 walk-forwards réels (24 exécutions DEVELOPMENT, 251 essais) ; protocole durci après l'audit du 2026-09-30 (docs/PROTOCOL.md), A/B/C relancées ensuite : toujours REJECTED |
| Criblage brut des familles D à I (dérive retirée, seuil de coûts, IC par blocs de jours, essais comptés) | TESTÉ | `test_screen` (causalité, entrée à l'ouverture suivante, IC encadrant la moyenne) ; exécution réelle 16 paires : aucune famille ne passe ([SCREENING.md](SCREENING.md)) |
| Contrat TXT V3 (point 11), deux expirations (point 12) | TESTÉ | `test_signals` (34 cas dont 27 refus) ; texte V3 produit sur un vrai setup ETH |
| Politique de sortie partagée et hachée (point 13) | TESTÉ | `test_exits` : empreintes figées, fichier `config/exit_policies.json` identique au code |
| Publication hors shadow limitée aux politiques du consommateur | TESTÉ | `test_outbox_publication_requires_a_policy_the_consumer_executes` |
| Publication atomique, registre, reprise après crash | TESTÉ | `test_publication_is_atomic_idempotent_and_recoverable` |
| Retour d'exécution v2 : reçu / ordre envoyé / rempli, UNKNOWN (point 15) | TESTÉ | `test_feedback` (contrat strict, états, UNKNOWN, NOT_CONSUMED) |
| Écarts expliqués, backtest / prospectif / Demo séparés (point 16) | TESTÉ sur fixtures | `test_deviations_*`, `test_prospective_replay_*` ; aucun retour Demo réel encore |
| Évaluation de signaux Telegram (taux de base, registre, résolution) | TESTÉ | `test_external` ; démonstration sur données réelles (SOL) |
| Évaluateur Telegram v2 : refus « signal invalidé / déjà joué », géométrie sur l'entrée obtenue, taux de base LIMIT_ALIGNED_V2 (même ordre que la résolution), IC par blocs de jours | TESTÉ | `test_blind_limit_orders_are_resolved_exactly_like_replay` (≥ 30 ordres identiques), `test_day_block_interval_*` ; essai réel SOL (IC désormais disponible par régime) |
| Bilan Telegram : doublons comptés une fois, copies marquées, suivi depuis la réception, résolution automatique dans `run`, bilan par groupe avec IC et seuil de 20 signaux | TESTÉ | `test_same_text_is_counted_once_*`, `test_resolution_starts_at_*`, `test_monitor_resolves_external_signals_*`, `test_source_record_*` |
| Test croisé CSI ↔ BSM sur les messages Telegram réels (mêmes prix lus) | TESTÉ | `test_csi_and_bsm_read_the_same_telegram_signal` (main et branche) |
| MAX_ENTRY_DEVIATION_BPS couvrant l'écart réel référence → ENTRY_1 (+ tolérance 25 pb) | TESTÉ | `test_published_band_accepts_the_price_at_publication` |
| Registre des publications portable entre machines (chemins relatifs, chemins étrangers ramenés) | TESTÉ | `test_registry_paths_survive_a_move_between_machines` |

## Intégration avec BinanceSpotManager (Binance Demo uniquement)

| Fonctionnalité | Statut | Preuve ou manque |
|---|---|---|
| Échec sûr : un BSM qui ne lit pas le V3 le refuse | TESTÉ | `test_any_bsm_parser_refuses_v3_it_does_not_understand` (main et branche) |
| Lecture V3 par BSM (`parse_csi_signal`), politiques V2 seules acceptées | TESTÉ sur la branche | 31 tests de contrat réussis contre `feat/csi-v2-drop` (2026-09-30) ; ignorés contre `main` |
| Fusion de la branche BSM | BLOQUÉ | relecture et décision du propriétaire |
| Retour Demo réel importé et comparé | BLOQUÉ | exige la branche fusionnée, l'intégration activée et une stratégie DEMO_ELIGIBLE (aucune aujourd'hui) |
| `integration_verified = true` | NON IMPLÉMENTÉ (volontairement) | conditions dans docs/SIGNAL_FORMAT.md |

## Lot 4 et suivants (points 1-9, 17-21)

| Fonctionnalité | Statut | Preuve ou manque |
|---|---|---|
| `scan` (un cycle) et `run --mode shadow` continu, `analyze` ponctuel (point 1) | TESTÉ | `test_live` ; `scan` réel le 2026-09-30 : 16 paires × 3 stratégies en 18 s (données 11 s, analyse 3,6 s) |
| Déclenchement aux clôtures 15m, attente bornée des bougies 15m/1h (et BTC), pas de publication si le contexte manque (point 2) | TESTÉ | `test_missing_hourly_context_*`, `test_missing_setup_candle_*` (horloge simulée) |
| Rafraîchissement REST des seules bougies manquantes, fenêtres en mémoire, fusion du seul chevauchement en Arrow, fichiers en blocs de 16 384 lignes (point 3) | TESTÉ | équivalences `test_tail_merge_*`, `test_upsert_tail_*`, `test_tail_load_*`, `test_live_window_*` ; lecture des bougies récentes 7 ms au lieu de 127 ms |
| Calcul incrémental des indicateurs, contexte BTC/ETH calculé une fois pour toutes les paires (point 3) | NON IMPLÉMENTÉ | les indicateurs sont recalculés sur la fenêtre à chaque cycle (3,7 s pour 48 analyses) |
| Processus séparés, priorité CPU basse des travaux lourds (point 4) | TESTÉ | `backtest` et `walk-forward` passent en priorité « inférieure à la normale » (`test_heavy_jobs_can_lower_their_own_priority`) ; ce sont des commandes distinctes de `run` |
| Plafond de RAM des travaux lourds (point 4) | TESTÉ (Windows) | `backtest`, `walk-forward` et `screen` : priorité basse et plafond mémoire du processus par Job Object (`live.heavy_job_max_memory_mb`, 3 000 Mo par défaut) ; `test_heavy_jobs_get_a_memory_ceiling_that_fails_cleanly` (au-delà : MemoryError propre, en dessous : inchangé) ; Linux/Docker : `mem_limit` du service `tools` ; walk-forward réel sous plafond NON VÉRIFIÉ |
| Reprise sans republication de signal périmé (point 5) | TESTÉ | `test_restart_never_republishes_an_old_setup` (même identifiant, rien après fermeture de la fenêtre) |
| Coupure réseau : réessais pendant l'attente bornée, rattrapage au cycle suivant (point 5) | TESTÉ | `test_network_outage_is_retried_then_caught_up_without_stale_signals` ; coupure réelle longue non provoquée |
| Verrou d'instance, registre persistant, réconciliation au démarrage (point 6) | TESTÉ | `test_only_one_instance_can_hold_the_lock`, `test_run_forever_*` |
| Collecteur RSS/API sans LLM, 8 sources vérifiées (point 7) | TESTÉ | `test_news` ; vérification et collecte réelles le 2026-09-30 : 311 éléments ([NEWS.md](NEWS.md)) |
| Calendrier macro prévisionnel (point 7) | BLOQUÉ | le calendrier du BLS répond 403 aux clients automatisés |
| Traçabilité, corrections, reprises, santé des sources (point 8) | TESTÉ | `test_store_keeps_first_seen_*`, `test_failing_source_is_down_never_silent` |
| Modes off / observe (point 9) | TESTÉ | `NEWS_STATUS` écrit dans chaque signal ; aucune influence sur les décisions |
| Mode gate (point 9) | NON IMPLÉMENTÉ (volontairement) | refusé par la configuration tant qu'aucune règle n'est évaluée |
| ML sur résultats mûrs, calibration datée (point 17) | NON IMPLÉMENTÉ | lot 5 ; les champs ML du contrat sont prêts |
| Agents IA, budget, cache, recontrôle après réponse lente (points 17-18) | NON IMPLÉMENTÉ | lot 6 ; aucun appel payant n'existe |
| Arrêt propre (SIGTERM), santé « démarré ≠ prêt » (point 19) | TESTÉ | `test_run_stops_cleanly_when_asked`, `test_health_distinguishes_*` |
| Image Docker, Compose (volume persistant, limites mémoire/CPU, redémarrage, contrôle de santé) (point 19) | TESTÉ en local | 2026-09-30, Docker Desktop, 1 Go et 1 CPU : `doctor` OK, `health` = pas prêt avant tout cycle, téléchargement ETH+BTC (461 s), cycle `run` réel (clôture 09:15Z, fin 26 s après), puis `health` = prêt ; empreintes des roues Linux non vérifiées |
| Fonctionnement après fermeture de la session d'administration sur VPS/NAS (point 19) | BLOQUÉ | aucune machine cible accessible |
| Sauvegarde cohérente, restauration vérifiée, suspension de publication jusqu'à réconciliation (point 20) | TESTÉ | `test_backup` (archive altérée refusée, restauration refusée pendant `run`, publication bloquée puis reprise) ; sauvegarde réelle le 2026-09-30 : 7 fichiers, 329 Ko |
| Mesures de durée (données, analyse, délai de publication), état de santé JSON (point 21) | TESTÉ | `state/run_status.json` ; données passées de 45 s à 11 s (connexion HTTP réutilisée, 4 séries en parallèle) |
| Interface utile (point 21) | TESTÉ | `state/dashboard.html` (Docker : http://127.0.0.1:8502/dashboard.html) : santé, dernière analyse, rejets, verdicts, signaux, fraîcheur par paire, sources de news (INCONNU si collecteur arrêté), groupes Telegram, légende, heure de Paris, bandeau « tableau figé » après 20 min ; texte externe échappé |
| API locale lecture et évaluation pour l'interface de BSM | TESTÉ | `tests/test_api.py` (routes, validation, jeton, taille, type, méthodes) ; service Docker `api` démarré le 2026-09-30 (127.0.0.1:8503, réseau `csi-bridge`), évaluation réelle d'un signal ETH par HTTP ; côté BSM : page Avis CSI, avis sur la page Signaux, contrôle avant exécution automatique (fusionné dans BSM `main` le 2026-09-30) |
| Univers validé par le propriétaire : un signal soumis à la main ajoute sa paire (docs/UNIVERSE.md) | TESTÉ | `tests/test_universe.py`, `test_live` (téléchargement entre deux cycles), `test_api` (`user_validated`, `/universe`) ; verdict `EN_ATTENTE` non enregistré ; un signal automatique n'ajoute jamais rien ; première paire réelle (QTUMUSDT) à confirmer en Docker |

Tests de résilience demandés au point 22 : doublons, données périmées, bougies absentes, contexte
manquant, redémarrage et reprise de publication après crash sont couverts. Coupure réseau réelle,
saturation et restauration ne le sont pas encore.
