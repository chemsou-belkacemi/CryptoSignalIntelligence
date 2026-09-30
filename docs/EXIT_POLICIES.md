# Politiques de sortie partagées

Source : `backtest/exits.py` (`EXIT_POLICIES`). Export lu par l'exécuteur :
`config/exit_policies.json`, régénéré par `exit-policies --write` et vérifié par test (le fichier
ne peut pas dériver du code). Chaque signal V3 porte `EXIT_POLICY_ID` et `EXIT_POLICY_HASH`.

## Pourquoi une empreinte

Comparer un backtest et une exécution Demo n'a de sens que si les deux appliquent exactement
les mêmes règles. L'empreinte est calculée sur toutes les règles (pas sur la description) :
`sha256(json canonique, clés triées, sans espaces)`, 16 premiers caractères. Une règle modifiée
donne une autre empreinte, donc une nouvelle politique `…_V2`. Les empreintes existantes sont
figées par test.

## Règles décrites

| Règle | Valeurs |
|---|---|
| tp_execution | `LIMIT` (ordre au repos, rempli si le plus haut dépasse strictement le TP) ; `MARKET_ON_TRIGGER` (vente au marché dès que le prix touche le TP, prix du TP moins glissement) |
| stop_rule | `FIXED` ; `BREAK_EVEN_AFTER_TP1` (stop au prix d'entrée après TP1) ; `BREAK_EVEN_AVG_FILL_AFTER_FIRST_TP` (stop au prix moyen d'achat réel après le premier TP rempli) ; `TRAIL_PREVIOUS_TP` (stop au TP précédent) |
| stop_move_applies | `AFTER_TP_FILL_CONFIRMED` : un stop remonté s'applique après la confirmation du TP ; en simulation, dès la bougie suivante, jamais dans la bougie du TP |
| stop_order, stop_limit_offset_bps | `STOP_MARKET` ; `STOP_LIMIT` avec une limite sous le stop ; `STOP_LIMIT_MARKET_IF_CROSSED` : idem, et vente au marché si la plateforme refuse un stop déjà franchi |
| time_exit | sortie au marché après `MAX_HOLD_MINUTES`, ou aucune sortie temporelle |
| tp_weight_basis | `FILLED_BASE_QUANTITY` : les poids s'appliquent à la quantité réellement achetée ; `NET_FILLED_BASE_QUANTITY` : à cette quantité moins les frais payés en actif de base |
| remainder_rule | `LAST_TP_SELLS_REMAINDER` : le dernier TP vend tout le restant |
| below_minimum_rule | `MERGE_INTO_NEXT_TP` : une tranche sous les minimums de la plateforme est reportée sur le TP suivant |
| unfilled_entries_rule | `CANCEL_AT_ENTRY_EXPIRY_OR_FIRST_TP` : entrées restantes annulées à ENTRY_EXPIRES_AT ou au premier TP |
| max_entries | nombre d'entrées gérées (1 pour toutes les politiques actuelles) |

## Politiques

Empreintes courantes : `.\.venv\Scripts\python.exe -m crypto_signal_intelligence exit-policies`.

| Politique | TP | Stop | Ordre stop | Sortie temporelle | Usage |
|---|---|---|---|---|---|
| FIXED_SL_ONE_TP_V1 | limite | fixe | stop-market | oui | profil théorique des stratégies A, B, C |
| FIXED_SL_FOUR_TP_V1 | limite | fixe | stop-market | oui | TP partiels théoriques |
| BREAK_EVEN_AFTER_TP1_V1 | limite | entrée après TP1 | stop-market | oui | comparaison |
| TRAIL_PREVIOUS_TP_V1 | limite | TP précédent | stop-market | oui | comparaison |
| BSM_MARKET_TP_FIXED_SL_V1 | marché sur déclenchement | fixe | stop-limit −30 pb | non | remplacée par V2 (description inexacte), conservée pour l'historique |
| BSM_MARKET_TP_BREAK_EVEN_V1 | marché sur déclenchement | entrée après TP1 | stop-limit −30 pb | non | remplacée par V2 |
| BSM_MARKET_TP_FIXED_SL_V2 | marché sur déclenchement | fixe | stop-limit −30 pb, vente au marché si déjà franchi | non | **BinanceSpotManager** (`consumer_policies`) |
| BSM_MARKET_TP_BREAK_EVEN_V2 | marché sur déclenchement | prix moyen d'achat réel après le 1er TP rempli | stop-limit −30 pb, vente au marché si déjà franchi | non | **BinanceSpotManager** (préférence break-even) |

## Limites de la simulation

- Le simulateur traite le stop-limit comme un stop-market : remplissage au stop, ou à
  l'ouverture en cas de gap, moins le glissement. Un stop-limit réel peut ne pas être rempli si
  le prix traverse sa limite. Cet écart est connu et reste visible dans `execution-report`.
- Les règles de reliquats et de minimums sont décrites pour l'exécuteur ; la simulation
  travaille en fractions continues et ne connaît pas les minimums de la plateforme.
- Deux entrées : le contrat les accepte, mais aucune politique ne les gère encore
  (`max_entries=1`) et le simulateur les refuse.

## Historique

- 2026-09-30 : `BSM_MARKET_TP_*_V2`. En confrontant la V1 au code de BinanceSpotManager, trois
  écarts sont apparus : poids appliqués à la quantité nette des frais payés en actif de base,
  vente au marché quand un stop déjà franchi est refusé, break-even au prix moyen d'achat réel.
  La V2 les décrit. Avec une entrée unique, la trajectoire simulée est identique à la V1 (vérifié
  par test) ; seule la description devient exacte. BSM a aussi reçu le report des tranches sous
  les minimums de la plateforme (`MERGE_INTO_NEXT_TP`), qu'il n'appliquait pas.
