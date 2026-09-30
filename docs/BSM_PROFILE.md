# Profil d'exécution observé de BinanceSpotManager (2026-09-30)

Relevé en lecture seule dans le code de BinanceSpotManager (branche `main`, commit `ea84d51`,
et branche `feat/ml-signal-drop`). Ce profil sert à simuler nos signaux **comme le bot les
exécuterait** (variante `profil_BSM` des backtests et du walk-forward, politique
`BSM_MARKET_TP_FIXED_SL_V2` dans `backtest/exits.py`). Binance Demo uniquement.

## Ce que fait le bot

| Élément | Comportement observé | Référence |
|---|---|---|
| Entrées | LIMIT GTC, budget partagé à parts égales entre les entrées, toutes placées d'un coup ; expiration **locale** 24 h après préparation ; entrées restantes annulées après le TP1 | `signal_plan.py`, `automation_engine.py` |
| Détection des remplissages | un LIMIT rempli plus tard n'est vu que par la réconciliation, toutes les 12 boucles (≈ 60 s) ; le stop est placé après cette détection | `bot_worker.py`, `reconciliation_engine.py` |
| Take-profits | tous les objectifs du signal, parts égales de la position initiale ; **pas d'ordres au repos** : quand le dernier prix ≥ TP, le bot annule le stop, vend **au marché** la part, puis recrée le stop ; au plus un TP par cycle (5 s) | `automation_engine.py`, `strategy_engine.py` |
| Stop | un seul STOP_LOSS_LIMIT GTC sur la quantité nette, prix limite = stop × (1 − 0,3 %) ; **jamais déplacé** sur `main` ; la branche ajoute une préférence (break-even, break-even + frais, TP précédent) appliquée après chaque TP sauf le dernier ; stop déjà franchi → vente au marché (`STOP_CROSSED`) | `execution_engine.py`, `position_engine.py` |
| Sortie temporelle | **aucune** : la position reste jusqu'au TP ou au stop | — |
| Entrées jamais remplies | la position reste `PENDING_ENTRIES` et occupe un emplacement (défaut connu, correction demandée) | `risk_engine.py` |
| Taille | ADAPTIVE : 5 % de la valeur du portefeuille (2 % si moins de 30 % de liquidités), plafonnée par la réserve ; quantités au pas de lot, prix au pas de cotation | `signal_sizing.py`, `symbol_rules.py` |
| Frais | commissions réelles (base, devise de cotation ou BNB) | `accounting.py` |
| Environnement | DEMO imposé au niveau transport : URL en liste blanche, `LIVE` ramené à `DRY_RUN`, requêtes signées refusées hors Demo | `config.py`, `binance_client.py` |

## Ce que le bot ne sait pas faire aujourd'hui (branche `main`)

- lire le format TXT de ce projet, V2 puis V3 (il attend `PAIR:` / `ENTRY 1:` / `T1:` / `SL:`) ;
- respecter `EXPIRES_AT` ou `MAX_ENTRY_DEVIATION_BPS` (seule une limite de 1 % par rapport au prix
  de référence de la préparation existe) ;
- dédoublonner sur `IDEMPOTENCY_KEY` (seul le hachage du texte) ;
- pondérer les TP (`TP_WEIGHTS`) ;
- renvoyer un retour d'exécution par signal.

La branche `feat/ml-signal-drop` ajoute un dossier de dépôt **JSON** (contrat v1) mais aucune de
ces sémantiques. La branche `feat/csi-v2-drop` (à relire avant toute fusion) apporte depuis le
2026-09-30 : dépôt `.txt` V2, expiration, écart d'entrée, registre d'idempotence, poids de TP,
clôture des positions jamais remplies, fichier d'événements ([FEEDBACK_FORMAT.md](FEEDBACK_FORMAT.md)).
Passage au contrat V3 en cours sur la même branche : deux expirations, empreinte de politique,
refus des politiques que BSM n'exécute pas, retour d'exécution v2 avec frais réels.

## Politiques acceptées par BSM

Seules `BSM_MARKET_TP_FIXED_SL_V2` et `BSM_MARKET_TP_BREAK_EVEN_V2` décrivent ce que le bot
fait réellement. Leurs règles complètes et leurs empreintes sont dans `config/exit_policies.json`
([EXIT_POLICIES.md](EXIT_POLICIES.md)). Une règle que BSM n'applique pas exactement fera l'objet
d'une nouvelle politique `…_V2` décrivant le comportement réel, jamais d'un ajustement silencieux.

## Correspondance avec la simulation

| Politique `EXIT_POLICY_ID` | Simulation | Règle stop chez BSM |
|---|---|---|
| `FIXED_SL_ONE_TP_V1`, `FIXED_SL_FOUR_TP_V1` | TP limite (dépassement strict), stop fixe, sortie temporelle après `max_hold_bars` (profil théorique) | NO_CHANGE |
| `BREAK_EVEN_AFTER_TP1_V1` | stop remonté au prix d'entrée après TP1, dès la bougie suivante | BREAK_EVEN |
| `TRAIL_PREVIOUS_TP_V1` | stop remonté au TP précédent après chaque TP | PREVIOUS_TP |
| `BSM_MARKET_TP_FIXED_SL_V2` | TP au marché sur **contact** (high ≥ TP), rempli à TP − glissement ; stop fixe ; **pas** de sortie temporelle | profil `main` |
| `BSM_MARKET_TP_BREAK_EVEN_V2` | idem, stop au prix moyen d'achat réel après le 1er TP rempli | préférence BREAK_EVEN |

Non modélisé, à garder en tête : la latence de détection des remplissages (jusqu'à ≈ 60 s sans
stop), le décalage de 0,3 % du stop-limite dans une chute rapide (vente `STOP_CROSSED` au
marché), la limite d'un TP par cycle, les frais en BNB, et l'annulation des entrées restantes
après TP1 (nos signaux n'ont qu'une entrée).
