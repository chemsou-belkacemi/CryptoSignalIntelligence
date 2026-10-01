# Signaux externes (groupes Telegram) : évaluation, résolution, bilan par source

Objectif : tu reçois un signal d'un groupe, tu le colles, le projet dit ce qu'il en pense
**avant** que tu décides d'entrer (sur Binance Demo, via BinanceSpotManager). Rien n'est
exécuté ici ; aucun ordre, aucune clé.

```powershell
.\.venv\Scripts\python.exe -m crypto_signal_intelligence evaluate-signal --source "Suhaib" --file signal.txt
.\.venv\Scripts\python.exe -m crypto_signal_intelligence evaluate-signal --source "ABK" --text "PAIR: SOL/USDT`nENTRY 1: 150.10`nT1: 154.00`nSL: 147.20"
Get-Content signal.txt | .\.venv\Scripts\python.exe -m crypto_signal_intelligence evaluate-signal --source "Cleo"
.\.venv\Scripts\python.exe -m crypto_signal_intelligence resolve-signals     # plus tard : TP1, SL, TIMEOUT, UNFILLED
.\.venv\Scripts\python.exe -m crypto_signal_intelligence sources             # bilan par groupe
```

Avec Docker (recommandé, l'évaluation est résolue automatiquement par la surveillance) :

```powershell
.\scripts\evaluer-signal.ps1 -Source "Suhaib" -Fichier signal.txt
Get-Clipboard | .\scripts\evaluer-signal.ps1 -Source "Cleo"     # texte copié
```

Formats lus : les mêmes que BinanceSpotManager (PAIR / ENTRY n / Tn / SL ; Coin / Entry Zone /
Target → ; #PAIRE/USDT / Entryn / TPn / Stop ; BUY / ENTRY / TP / SL). Tout texte ambigu est
refusé : aucun prix deviné, pas de short, pas de levier, une seule paire explicite.

## Ce que dit l'avis, et ce qu'il ne dit pas

L'avis combine trois choses, dans cet ordre :

1. **Contrôles déterministes** (vetos, jamais contournables) : signal lisible ; paire dans
   l'univers screené ([UNIVERSE.md](UNIVERSE.md)) ; données fraîches ; **signal encore valable**
   (dernier prix ni sous le stop, ni au-dessus de TP1 : sinon il est refusé et n'entre jamais
   dans le bilan du groupe) ; entrée pas déjà dépassée par le prix (`max_entry_deviation_pct`) ;
   stop ni trop serré ni trop large en ATR14 (`min_stop_atr`, `max_stop_atr`) ; RR du TP1
   **recalculé** à partir des prix, puis net de coûts centraux ≥ `min_net_rr`. Stop et RR sont
   calculés sur l'**entrée réellement obtenue** : un achat limite placé au-dessus du marché
   s'exécute tout de suite, au prix du marché.
2. **Contexte** : tendance, volatilité et liquidité 1h, RSI, rendement 24 h de BTC. Descriptif.
3. **Taux de base historique** (méthode `LIMIT_ALIGNED_V3`) : sur la même paire, à **chaque**
   bougie de l'historique (donc sans aucune sélection), on place le **même ordre limite** que le
   signal (même écart au dernier prix, même stop en ATR, même cible en R, même fenêtre de
   24 h), résolu avec **exactement** les règles de `resolve-signals` (vérifié par un test
   d'équivalence), **jusqu'à la fin de DEVELOPMENT seulement** (2025-06-30 : la période suivante
   reste réservée au test final de la recherche). Dans le même régime 1h quand l'échantillon suffit, sinon sur tous les
   régimes (signalé) : taux de remplissage, part des cas où TP1 est atteint avant le stop parmi
   les ordres remplis (avec intervalle), part des stops, part des « ni l'un ni l'autre » à
   l'horizon (`max_hold_bars`, 7 jours), et espérance nette en R par ordre rempli avec
   intervalle (bootstrap par blocs de 7 jours calendaires consécutifs, sur le même échantillon
   que la moyenne ; au moins 10 blocs, soit environ 70 jours de données).

Le pourcentage affiché est donc **« TP1 avant SL » pour des ordres aveugles de même
géométrie et de même type**, après coûts. Ce n'est pas la probabilité que *ce* signal réussisse :
le groupe peut avoir un avantage de sélection (ou l'inverse). Cet avantage se mesure ensuite,
signal après signal, dans `sources` : TP1 réalisé contre taux de base, R réalisé contre R de
base, avec les mêmes règles des deux côtés.

| Avis | Signification |
|---|---|
| REFUSE | non évaluable : texte ambigu, paire hors univers, données périmées ou absentes, signal déjà invalidé (stop atteint) ou déjà joué (TP1 atteint) |
| DEFAVORABLE | un veto, ou espérance nette de la géométrie négative (IC95 entièrement ≤ 0) |
| INDETERMINE | aucun veto, mais échantillon insuffisant ou IC95 contenant 0 |
| FAVORABLE | aucun veto et espérance nette de la géométrie positive (IC95 > 0), hors avantage de la source |

Avec des coûts réalistes, les entrées aveugles perdent le plus souvent : un avis DEFAVORABLE
est le cas normal pour une géométrie courte (cible proche, stop large). C'est une information,
pas un défaut de l'outil. Un avis ne modifie jamais un ordre ; la décision reste la tienne.

## Résolution et bilan par source

Chaque évaluation est enregistrée (`signals/external.sqlite3`) avec ses prix et sa bougie de
décision. `resolve-signals` rejoue ensuite chaque signal sur les bougies 15m clôturées :

- ordre LIMIT à l'entrée 1, valable `entry_window_bars` (24 h, comme BinanceSpotManager) ;
  rempli à l'ouverture si elle est sous l'entrée, sinon seulement si le plus bas **pénètre**
  l'entrée (un simple contact ne garantit rien) ;
- puis TP1 ou stop en premier, stop si les deux dans la même bougie (convention pessimiste),
  ouverture sous le stop exécutée à l'ouverture ; TIMEOUT au close après `max_hold_bars` ;
- R = PnL net (frais, glissement) rapporté au risque prévu (entrée − stop du signal).

- suivi à partir de la première bougie qui s'ouvre **après la réception** : la bougie en cours
  au moment de l'avis a commencé avant, ses extrêmes n'étaient pas atteignables (règle prudente :
  un remplissage réel pendant cette bougie peut être manqué, faute de données à la minute) ;
- résolution **automatique** après chaque cycle de la surveillance (`run`, Docker) ; `resolve-signals`
  reste disponible à la main ;
- un même texte recollé pour le même groupe dans les 7 jours est gardé mais **jamais compté deux
  fois** ; reçu d'un autre groupe, il compte pour ce groupe et est marqué « copie ».

`sources` (et la section « Groupes Telegram » du tableau de bord) compare, par groupe, le réalisé
au taux de base des mêmes signaux : écart moyen en R (R réalisé − R de base, mêmes règles des deux
côtés) avec son intervalle à 95 %, tiré par **jours de réception** (les signaux d'un même jour suivent
le même marché : ils ne comptent pas comme des observations indépendantes). **Aucune conclusion avant
20 signaux résolus répartis sur au moins 10 jours** ; ensuite seulement « au-dessus », « en dessous »
ou « pas d'écart démontré ». Ce n'est ni une note du groupe ni une probabilité de réussite du
prochain signal.

Regards répétés : le bilan est recalculé à chaque signal. Décider de faire confiance à un groupe la
première fois que l'intervalle passe au-dessus de 0 augmente le risque de faux positif ; une
conclusion ne vaut que si elle tient ensuite, sur des signaux reçus après cette décision.

## Limites

- Simulation sur OHLCV : un prix qui touche l'entrée n'est pas la preuve d'un remplissage
  réel ; Binance Demo peut différer. Seul le retour d'exécution du bot (lot 3) dira ce qui a
  vraiment été rempli.
- Le taux de base ne connaît que la géométrie, le type d'ordre et le régime : ni le raisonnement
  du groupe, ni ses entrées multiples, ni ses TP partiels ni ses stops déplacés. Le profil simulé
  est « entrée 1, stop fixe, TP1 », des deux côtés (taux de base et résolution).
- « Signal encore valable » se juge sur le dernier prix : un stop ou un TP1 touché puis quitté
  entre la publication et la réception n'est pas détecté.
- Paires hors univers : refusées pour un signal reçu automatiquement (pas de données, pas de
  screening). Un signal **soumis à la main par le propriétaire** vaut validation de la paire : elle
  est ajoutée à l'univers, l'avis est `EN_ATTENTE` le temps du téléchargement de l'historique par la
  surveillance, puis le signal se réévalue normalement ([UNIVERSE.md](UNIVERSE.md)).
- Une probabilité calibrée par apprentissage (lot 5) ne viendra qu'avec assez de signaux
  résolus ; d'ici là, aucun chiffre n'est présenté comme une prédiction.

## Historique

- 2026-10-01 : taux de base `LIMIT_ALIGNED_V3` : seuls les ordres dont la fenêtre complète se termine
  avant la fin de DEVELOPMENT comptent (champ `history_end`). Une paire cotée après cette date n'a
  pas de taux de base (avis indéterminé). Les évaluations antérieures gardent leur méthode
  (champ `method`).
- 2026-09-30 : taux de base `LIMIT_ALIGNED_V2`. Avant, il supposait une entrée au marché à chaque
  bougie alors que la résolution place un ordre limite : l'écart « réalisé − base » d'un groupe
  mélangeait son apport et l'effet du type d'ordre. L'intervalle exigeait 10 blocs de 672 entrées
  (6 720) et ne portait pas sur le même échantillon que la moyenne : l'avis restait « indéterminé »
  par construction dans la plupart des régimes. Ajout des refus « signal invalidé » et « signal
  déjà joué », et de la géométrie sur l'entrée réellement obtenue. Les évaluations enregistrées
  avant cette date gardent l'ancienne méthode (champ `method` absent dans leur taux de base).
