# Relecture des branches de BinanceSpotManager (préparée le 2026-10-03)

> **Fusionné et poussé le 2026-10-03 à la demande du propriétaire** (`main` de BSM : `e725308`) : dans l'ordre
> `fix/parser-stop-tp`, `feat/csi-v2-drop`, `feat/signal-routing`, `fix/reprise-audit`, `feat/location`. Conflits
> résolus en gardant les garde-fous des deux côtés ; 1 031 tests BSM réussis, 0 échec ; test de contrat CSI : 44
> réussis contre le `main` fusionné ; verrou Demo inchangé. Le bot en service n'est **pas** redémarré : à faire
> par le propriétaire (`make backup` puis `make up`, et les vérifications de la liste ci-dessous).

Dossier préparé pour le propriétaire, **sans rien modifier, fusionner ni pousser** dans BSM. Il décide seul de la
fusion. `main` de BSM : `c5a8d5c`, propre, identique à `origin/main`.

## En bref

- `feat/csi-v2-drop` et `feat/signal-routing` passent **tous leurs tests**, ne créent **aucun conflit** avec `main`
  (avance rapide possible) et **ne touchent pas au verrou Demo**.
- `fix/reprise-audit` (déjà poussée, correctifs de sécurité anti-double-achat) **entre en conflit** avec elles :
  il faut choisir l'ordre et résoudre avec soin.

## Nouvelle branche du 2026-10-03 : `fix/parser-stop-tp` (petite, à fusionner en premier)

Deux soucis signalés par le propriétaire, corrigés dans une branche partie de `main` (worktree
`.claude/worktrees/parser-stop-tp`), ni fusionnée ni poussée :

- **« Stop: 223. »** (point sans décimale) était refusé comme « prix mal formé » : il vaut maintenant 223.
  « 1.442. », « 223.. », « 1.2.3 » restent refusés. Même règle dans le parser de CSI.
- **Répartition personnalisée des TP réglée pour 5 TP** : un signal à 4 TP était refusé (« la répartition doit
  contenir 5 pourcentages »). C'est maintenant un **maximum** : les 4 premières parts sont gardées et remises à
  l'échelle pour faire 100 % (40;25;15;10;10 → 44,4 / 27,8 / 16,7 / 11,1). Un signal qui a plus de TP que la
  répartition garde les N premiers (comme avant). Même chose pour les entrées.
- Tests : 522 réussis sur la branche (7 nouveaux, dont le signal QNT réel) ; fusion simulée sans conflit avec
  `main`, `feat/csi-v2-drop`, `feat/signal-routing`, `fix/reprise-audit` et `feat/location` ; fusion d'essai avec
  `feat/signal-routing` : 700 tests réussis.
- Pour l'avoir en service : la fusionner dans `main`, puis `make up` (reconstruit l'image du bot).

## Les branches

| Branche | Avance / retard sur main | Contenu |
|---|---|---|
| `fix/parser-stop-tp` | +2 / 0 | prix « 223. » accepté ; répartition personnalisée = maximum de TP |
| `feat/csi-v2-drop` | +15 / 0 | lecture des signaux TXT V3 de CSI par dossier de dépôt, retour d'exécution vers CSI |
| `feat/signal-routing` | +21 / 0 | contient `csi-v2-drop` + routage AUTO / « À confirmer » / rejeté |
| `fix/reprise-audit` | +7 / 0 | achat unique par signal, ordres orphelins, SL incertain, « au marché » |
| `feat/location` | +14 / 0 | contient `fix/reprise-audit` + location (coffre, licence, comptes) |
| `feat/ml-signal-drop` | +1 / −17 | déjà incluse dans `csi-v2-drop` : à supprimer après fusion, jamais seule |

### `feat/csi-v2-drop` : ce qui change pour toi

- BSM lit les signaux **TXT V3** de CSI depuis `data/signal_drop/incoming/` ; un texte CSI arrivé par Telegram ou
  collé à la main est refusé (pas de doublon).
- Seules les politiques de sortie que BSM applique réellement sont acceptées (`BSM_MARKET_TP_FIXED_SL_V2`,
  `BSM_MARKET_TP_BREAK_EVEN_V2`), empreinte vérifiée.
- Fenêtre de validité et écart d'entrée maximal revérifiés juste avant l'ordre.
- **Retour d'exécution** (JSONL v2) écrit dans `data/signal_drop/outgoing/` pour la boucle Demo de CSI.
- Pour **toutes** les positions : une position dont toutes les entrées ont expiré sans achat est close « terminée
  sans achat » (rien n'est vendu ; pas fermée si un SL est actif). Pour les positions CSI seulement : une tranche de
  TP trop petite pour Binance est reportée sur le TP suivant.
- **Tout est désactivé par défaut** (`signal_drop_enabled`, `signal_drop_auto_enabled`).

### `feat/signal-routing` : ce qui change en plus

- Ta règle : **confirmation manuelle si le risque est élevé ou la confiance faible ou inconnue**. Chaque signal
  sort AUTO, « À confirmer » ou REJETÉ, avec ses motifs affichés.
- AUTO seulement si l'automatique est activé et sans aucun motif de revue ; la confiance est **déclarée** (CSI
  `DEMO_ELIGIBLE`, groupe marqué de confiance, actif dans ta liste), jamais mesurée.
- Garde-fous non calibrés : risque au stop ≤ 0,5 % (plafonné par ta limite dure), stop entre 1 et 10 %, 4 ordres
  automatiques par 24 h, coupe-circuit à 2 % de perte journalière ou 3 pertes de suite, une commande AUTO par cycle.
- En `DEMO_MANUAL`, rien ne part automatiquement (réglage `signal_route_honor_demo_manual`, activé par défaut).
- Nouvelle liste « À confirmer » dans la page Signaux ; notifications « Signal reçu / rejeté » remplacées par
  « Signal à confirmer » (désactivée par défaut).

## Risques vérifiés dans le code

- **Verrou Demo intact** : `config.py`, `binance_client.py`, `execution_engine.py`, `risk_engine.py` non touchés ;
  liste blanche des URL Demo et `assert_write_allowed()` inchangées ; le mode `LIVE` reste refusé.
- **Ordres** : aucun nouvel appel d'achat direct ; tout passe par la file de commandes habituelle. Le seul nouvel
  appel Binance (`get_my_trades`) est en lecture.
- **Secrets** : aucune clé ajoutée.
- **Dimensionnement** : logique inchangée ; le routage ne peut que resserrer ta limite dure.
- **À surveiller** : le dossier de dépôt est un volume partagé (quiconque y écrit peut proposer un signal ; il ne
  part en AUTO que si tu l'as activé et s'il est `DEMO_ELIGIBLE`) ; migration de `signals.db` au premier démarrage.

## Conflits (simulés sans toucher l'arbre)

| Fusion | Résultat |
|---|---|
| main + `csi-v2-drop` ou `signal-routing` | aucun conflit |
| `signal-routing` + `fix/reprise-audit` | 4 fichiers : `signal_auto_execution.py`, `signal_plan.py`, `pages/8_Signaux.py`, `scripts/bot_worker.py` |
| `signal-routing` + `feat/location` | 6 fichiers (les 4 + `signal_inbox.py`, `ui_common.py`) |

Dans `bot_worker.py`, **garder les deux** : la vérification des ordres orphelins (`fix/reprise-audit`) **avant**
`process_pending()`, et le try/except du routage. Deux corrections concurrentes d'un même bug de migration SQLite
(`duplicate column` toléré contre `BEGIN IMMEDIATE`) font le conflit de `signal_inbox.py`.

## Tests (lancés dans des copies temporaires, sans réseau, sans `.env`)

| Arbre | Résultat |
|---|---|
| `main` | 514 réussis, 1 échec intermittent (course dans la migration de `signals.db`, corrigée par les deux branches) |
| `feat/csi-v2-drop` | 645 réussis, 0 échec |
| `feat/signal-routing` | 693 réussis, 0 échec |
| Contrat avec CSI (BSM `test_signal_csi.py`) | 104/104 et 106/106 |
| Contrat côté CSI (`tests/test_bsm_contract.py`) | 44 réussis sur chaque branche (14 sur `main`) |

Non vérifié : `make integration` (clés Demo), un cycle Demo de bout en bout, la construction Docker, les tests de
`fix/reprise-audit` et `feat/location`, la ligne changée de `.env.example`.

## Recommandation

0. Fusionner `fix/parser-stop-tp` (petite, sans conflit, corrige tes deux soucis du jour).
1. Fusionner `feat/csi-v2-drop` dans `main` (avance rapide).
2. Fusionner `feat/signal-routing` (avance rapide).
3. Supprimer `feat/ml-signal-drop`, absorbée.
4. Intégrer `fix/reprise-audit` en résolvant les 4 conflits (garder les deux garde-fous), relancer toute la suite.
5. `feat/location` en dernier (autre projet, 6 conflits).

Variante prudente : intégrer d'abord `fix/reprise-audit` (anti-double-achat) puis les deux branches ; mêmes
conflits, résolus dans l'autre sens. Dans tous les cas, **automatique désactivé** tant que les deux ne sont pas
réunis.

### À vérifier toi-même avant de dire oui

- Après la mise à jour, Réglages → Signaux : dépôt CSI **désactivé**, exécution automatique du dépôt
  **désactivée**, « DEMO_MANUAL impose la confirmation » **cochée**, `exit_on_crossed_stop` **activé**.
- `make check` affiche une URL Demo (`testnet.binance.vision` ou `demo-api.binance.com`) et l'environnement DEMO.
- Lire la ligne changée de `.env.example` (`git diff main...feat/signal-routing -- .env.example`) et l'ajouter à la
  main à ton `.env` si besoin.
- `make backup` avant le premier démarrage (migration de `signals.db`), puis `make up`.
- Côté CSI, rien ne bascule : `integration_verified` et `outbox_enabled` restent à false jusqu'à ce que le test de
  contrat passe contre le `main` fusionné et que `outbox_dir` pointe vers `data/signal_drop/incoming`.
- Décider si la confirmation manuelle d'un signal CSI non `DEMO_ELIGIBLE` reste possible (aujourd'hui : oui, avec
  avertissement).
- Premier essai en Demo avec un seul signal CSI de test en « À confirmer », puis lire le retour dans
  `data/signal_drop/outgoing/`.
