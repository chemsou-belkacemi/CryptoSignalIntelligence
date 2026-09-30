# Format TXT des signaux — version 3

Implémentation : `signals/schema.py` (modèle), `signals/txt.py` (sérialiseur et parseur strict),
`signals/analyze.py` (`build_signal`). La version 2 (2026-09-29) n'a jamais été consommée en
production ; elle n'est plus ni écrite ni lue (refus explicite).

## Règles générales

- UTF-8, une clé par ligne, `CLE=VALEUR` sans espace autour de `=` ni en bord de valeur.
- Première ligne obligatoire : `SIGNAL_VERSION=3`. Toute autre version est refusée.
- Clés en majuscules ; **clé dupliquée ou inconnue = refus**.
- Nombres : point décimal, pas de séparateur de milliers, pas d'exposant ; `NaN`/`Infinity` interdits.
- Horodatages : `YYYY-MM-DDTHH:MM:SSZ` (UTC, à la seconde).
- `NONE` : absence autorisée, uniquement pour les champs optionnels.
- Tout ce qui suit la ligne `---ANALYSIS---` est de la prose ignorée par le consommateur ; elle
  ne peut pas modifier le contrat.
- Le consommateur ne lit que les fichiers `.txt` (jamais `.txt.tmp`).

## Champs (ordre d'écriture)

| Clé | Obligatoire | Règle |
|---|---|---|
| SIGNAL_VERSION | oui | `3` |
| SIGNAL_ID | oui | identifiant unique de publication ; une retransmission garde le même |
| IDEMPOTENCY_KEY | oui | clé logique stable : marché, paire, stratégie/version, bougie de décision, politique de sortie |
| DATA_AS_OF | oui | dernière donnée utilisée (clôture de la bougie de décision) |
| DECISION_AT | oui | clôture de la bougie qui a déclenché la décision |
| CREATED_AT, VALID_FROM | oui | création du fichier ; début de validité |
| EXPIRES_AT | oui | **fin d'acceptation du message** par le consommateur (création + `message_ttl_minutes`, 15 min par défaut) |
| ENTRY_EXPIRES_AT | oui | **fin de validité des entrées non remplies** (fenêtre d'entrée de la stratégie, même règle que le backtest) |
| MARKET_DATA_SOURCE | oui | ex. `BINANCE_SPOT_PUBLIC` (marché réel) |
| ENVIRONMENT | oui | `DEMO` (remplace `INTENDED_EXECUTION_ENVIRONMENT` de la V2) |
| MARKET_TYPE / ACTION | oui | `SPOT` / `BUY` (NO_TRADE n'est jamais publié) |
| SYMBOL | oui | paire USDT/USDC |
| STRATEGY, STRATEGY_VERSION, TIMEFRAME_SETUP | oui | |
| ENTRY_MODE | oui | `LIMIT` |
| ENTRY_COUNT | oui | 1 ou 2 |
| ENTRY_1 | oui | prix limite |
| ENTRY_2 | selon ENTRY_COUNT | `NONE` si et seulement si ENTRY_COUNT=1 ; sinon STOP_LOSS < ENTRY_2 < ENTRY_1 |
| ENTRY_WEIGHTS, WEIGHT_BASIS | oui | un poids > 0 par entrée, somme = 1 ; `BASE_QUANTITY` ou `QUOTE_BUDGET` |
| STOP_LOSS | oui | < ENTRY_1 (et < ENTRY_2) |
| TP_COUNT | oui | 1 à 4 ; TP_n au-delà = `NONE` |
| TP_1..TP_4 | TP_1 oui | strictement croissants, > ENTRY_1 |
| TP_WEIGHTS | oui | un poids > 0 par TP, somme = 1, en fraction de la quantité réellement remplie |
| EXIT_POLICY_ID | oui | politique déclarée dans `config/exit_policies.json` |
| EXIT_POLICY_HASH | oui | empreinte de cette politique ; différente = refus (règles différentes) |
| MAX_HOLD_MINUTES | selon la politique | obligatoire si la politique a une sortie temporelle, `NONE` sinon |
| RR_REFERENCE | oui | `ENTRY_1`, ou `WEIGHTED_ENTRY` (prix moyen PRÉVU si toutes les entrées étaient remplies) ; `ENTRY_1` obligatoire avec une seule entrée |
| RR_TPn_GROSS | selon TP_COUNT | **recalculé** : (TP − référence)/(référence − STOP), 3 décimales ; une valeur différente est refusée |
| TECHNICAL_SCORE | optionnel | jamais une probabilité |
| ML_PROBABILITY, MODEL_ID, ML_TARGET_ID, ML_HORIZON_MINUTES, ML_CALIBRATION_ID | optionnels | **tous ou aucun** : une probabilité n'a de sens qu'avec sa cible, son horizon et sa calibration |
| TREND_REGIME, VOLATILITY_REGIME | oui | énumérations |
| NEWS_STATUS | oui | `OFF` (module absent ou éteint), `OBSERVE` (news enregistrées, sans influence), `GATE_CLEAR` (mode blocage actif, sources opérationnelles, aucun événement bloquant). Une source en panne n'est jamais `GATE_CLEAR`. |
| MAX_ENTRY_DEVIATION_BPS | oui | écart maximal accepté entre le prix courant et ENTRY_1, de 0 à 500. CSI publie l'écart RÉEL entre la clôture de décision et ENTRY_1 (prime de la limite, arrondi au tick compris) plus `entry_tolerance_bps` (25 pb) : au moment de la publication, le signal est donc toujours acceptable. Cette bande n'est pas simulée par le backtest, qui accepte tout écart. |
| VALIDATION_STATUS | oui | RESEARCH, VALIDATED_OOS, SHADOW, DEMO_ELIGIBLE, SCHEMA_EXAMPLE_ONLY |
| INTEGRATION_STATUS | optionnel à la lecture | `INTEGRATION_UNVERIFIED` par défaut ; toujours écrit |
| STATUS | oui | `NEW` |

Ordre des dates exigé : DATA_AS_OF ≤ DECISION_AT ≤ CREATED_AT ≤ VALID_FROM < EXPIRES_AT ≤ ENTRY_EXPIRES_AT.

## Deux expirations

- **EXPIRES_AT** : après cette heure, le consommateur refuse le message (REJECTED, « expiré à
  réception ») et ne place plus d'entrée. Il la recontrôle juste avant d'envoyer l'ordre.
- **ENTRY_EXPIRES_AT** : les ordres d'entrée encore ouverts sont annulés à cette heure.
- Une position déjà ouverte n'expire pas : elle suit sa politique de sortie jusqu'au bout.

## Politique de sortie partagée

Les règles complètes (TP au marché ou limite, stop fixe/break-even/suiveur, type d'ordre stop,
moment d'application d'un stop remonté, sortie temporelle, reliquats, entrées non remplies,
nombre d'entrées) sont définies dans `backtest/exits.py` et exportées dans
`config/exit_policies.json` (`exit-policies --write`). L'empreinte est un SHA-256 du JSON
canonique : modifier une règle change l'empreinte, ce qui impose une nouvelle version de
politique (`…_V2`). Le backtest et l'exécuteur ne comparent leurs performances que sur une
politique qu'ils reconnaissent tous deux, identifiant ET empreinte (détails : [EXIT_POLICIES.md](EXIT_POLICIES.md)).

Hors shadow, CSI ne publie qu'avec une politique listée dans `publication.consumer_policies`
(celles que BinanceSpotManager exécute réellement), sinon NO_TRADE `EXIT_POLICY_MISMATCH`.

## Compatibilité avec BinanceSpotManager : **INTEGRATION_UNVERIFIED**

- `main` de BSM : parseur historique `PAIR: …/ENTRY 1:/T1:/SL:`, qui refuse le V3 (échec sûr,
  vérifié par test).
- Branche `feat/csi-v2-drop` (à relire avant toute fusion) : lit le V2 depuis
  `data/signal_drop/incoming` (fichiers `<SIGNAL_ID>.txt`, jamais `.txt.tmp`), dédoublonne sur
  `IDEMPOTENCY_KEY`, refuse les signaux expirés ou trop éloignés du prix, applique `TP_WEIGHTS`,
  écrit le retour d'exécution. Passage au V3 en cours sur la même branche.

### Test de contrat contre le vrai parseur

`tests/test_bsm_contract.py` charge `binance_spot_manager/signal_parser.py` de BSM par chemin
(sans importer son paquet : aucun code réseau, aucune clé) :

- toujours : un parseur BSM qui ne connaît pas le V3 le refuse ;
- si BSM expose `parse_csi_signal` : lecture exacte (paire, entrées, stop, TP dans l'ordre, poids,
  identifiants, politique et empreinte, quatre horodatages, écart maximal, NEWS_STATUS), refus des
  corruptions que CSI refuse, refus de toute politique que BSM n'exécute pas réellement, prose
  ignorée, exemple `SCHEMA_EXAMPLE_ONLY` jamais exécutable.

```powershell
# Contre la branche (worktree) ; sans BSM_PATH, le dépôt voisin BinanceSpotManager (main) est utilisé.
$env:BSM_PATH = "C:\Users\chams\Downloads\BinanceSpotManager\BinanceSpotManager\.claude\worktrees\csi-v2-drop"
.\.venv\Scripts\python.exe -m pytest tests\test_bsm_contract.py -q -rs
```

`integration_verified` reste `false` et la publication reste dans `signals/shadow` tant que :
la branche n'a pas été relue et fusionnée par son auteur ; le test de contrat ne passe pas
contre `main` ; `publication.outbox_dir` ne pointe pas vers le dossier de dépôt de BSM. Ce
contrat porte sur la lecture du fichier ; l'exécution reste Binance Demo uniquement.

## Historique

- 2026-09-30 : MAX_ENTRY_DEVIATION_BPS corrigé. Il publiait la prime de la limite (10 pb) alors que
  l'écart réel, après arrondi au tick supérieur, atteint 16 pb sur certaines paires (1,239 → 1,241) :
  un consommateur qui compare |prix/ENTRY_1 − 1| à cette valeur aurait refusé le signal dès sa
  création. Relevé par la conception du routage BSM, corrigé côté CSI sans changer le contrat
  (`test_published_band_accepts_the_price_at_publication`). Alternative non retenue, à décider par
  le propriétaire : rendre l'écart unilatéral dans le contrat (un prix sous ENTRY_1 ne compterait pas).
