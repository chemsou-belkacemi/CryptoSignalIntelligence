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

## Gestion avec stop suiveur (celle du propriétaire, jugée par défaut)

Depuis le 2026-10-02, l'avis juge la gestion réelle du propriétaire (`[external] management = "stop_suiveur"`) :
- ventes aux 5 premiers objectifs (`tp_count`), parts 33 / 27 / 20 / 13 / 7 % ;
- TP1 touché → stop à l'**entrée 1** du signal ; TP2 → il y reste ; TP3 → TP1 ; TP4 → TP2 ; TPk → TP(k−2) ;
- le stop remonté s'applique à la bougie suivante (pire cas si le plus bas de la bougie du TP le traverse déjà) ;
- au-delà de 30 jours (`trail_max_hold_bars`), le reste est vendu.

Le **taux de base** rejoue cette gestion entière sur les mêmes ordres aveugles que le taux TP1 (même régime,
historique jusqu'à la fin de DEVELOPMENT) : FAVORABLE si l'IC95 de son espérance est entièrement > 0,
DEFAVORABLE s'il est entièrement ≤ 0. Le veto « RR TP1 ≥ 0,8 » ne s'applique plus (un TP1 proche protège la
position) ; TP1 doit seulement couvrir les coûts. Les signaux enregistrés sont résolus avec la même gestion,
et le bilan d'historique l'affiche comme 4e façon de jouer (c'est elle qui sert à la preuve). Le taux TP1 reste
affiché pour comparaison. `management = "tp1"` rétablit l'ancienne convention.

Moteur unique (`external/trailing.py`) pour le signal réel et les ordres aveugles : un test vérifie qu'ils
donnent le même résultat.

## Comparer les gestions (2 à 7 objectifs, part vendue à TP1, règle du stop)

Une gestion ne crée pas d'avantage à partir d'entrées prises au hasard ; elle peut tirer bien plus (ou bien
moins) d'un groupe qui choisit bien ses entrées. Deux usages :
- **Un signal** (onglet « Évaluer un signal ») : carte « Autres gestions de ce signal », 8 gestions typiques
  rejouées sur les mêmes ordres aveugles que le taux de base. Un aperçu, pas un choix.
- **Un groupe** (bilan d'historique) : 97 gestions (1 à 7 objectifs ; à TP1 parts décroissantes, 50, 60 ou
  70 % ; stop fixe, à l'entrée 1 après TP1, ou suiveur à 1 ou 2 objectifs d'écart) rejouées sur chacun de ses
  signaux réels (`external/managements.py`). La meilleure est **choisie sur les deux premiers tiers** des
  signaux (ordre chronologique) et **vérifiée sur le dernier tiers**, jamais vu pendant le choix : R moyen,
  et écart avec ta gestion signal par signal, avec IC95 par jours de publication. Conclusion seulement si le
  dernier tiers compte au moins 20 signaux sur 10 jours. Essayer 97 gestions et garder la meilleure sur les
  mêmes signaux trouverait toujours un gagnant : seule la vérification compte.

## Historique d'un groupe (bilan sans attendre)

Attendre 20 signaux résolus prend des semaines. Un groupe a déjà un passé : CSI peut le rejouer.

- **Tableau de bord**, onglet « Évaluer un signal », carte « Bilan d'un groupe sur son historique » :
  choisir le fichier `result.json` d'un export de Telegram Desktop (ouvrir le groupe, menu ⋮ →
  « Exporter l'historique du chat », sans photos ni vidéos, format JSON). Seul le texte des messages
  est envoyé à CSI.
- **Terminal** : `csi audit-telegram --file result.json`, ou `csi audit-telegram --bsm-inbox
  <signals.sqlite3 de BinanceSpotManager>` pour les messages que le bot a reçus en direct.
- **Signaux en image** : exporter **avec les photos**, puis `csi audit-telegram --file result.json --ocr`
  (extra « ocr »), ou copier le dossier de l'export dans `exports/` et cliquer « Mesurer avec les images » dans la
  même carte du tableau de bord (audit en arrière-plan, images lues sur la machine de CSI). Les captures
  TradingView sans texte lisible sont lues localement ; au moindre doute, l'image est ignorée ; ces signaux ont un
  bilan à part, hors preuve. Détails et limites : [OCR.md](OCR.md).

Chaque signal est rejoué sur les bougies 15 min **clôturées après sa publication** (heure exacte
`date_unixtime` de l'export), avec les coûts du scénario central, de trois façons affichées côte à
côte :

| Façon | Règle |
|---|---|
| TP1 au contact | convention de CSI : entrée 1, sortie à TP1 ou au stop touché, 7 jours au plus |
| Stop à la clôture | la même, mais le stop ne joue qu'à la clôture d'une bougie de l'unité écrite dans le signal (« Stop: 0.0739 (4h) ») ; vente à cette clôture, la perte peut dépasser 1 R |
| Comme le bot | entrée 1, tous les objectifs, stop fixe, sans sortie temporelle (politique `BSM_MARKET_TP_FIXED_SL_V2`) ; parts vendues « early » (5/15, 4/15… comme le réglage du bot) ou égales ; une position encore ouverte est valorisée au dernier prix, en **provisoire** tant qu'elle a moins de 30 jours |

Sont écartés et comptés à part : messages illisibles, doublons (même signal sous 7 jours), signaux
déjà morts (prix au stop), déjà joués (prix à TP1) ou périmés (entrée > 3 % au-dessus du prix) au
moment de la publication, paires sans bougies publiques. Les pictogrammes ajoutés après coup
(« ENTRY 1 ✅ ») ne rendent plus un signal illisible : écarter les signaux réussis et décorés
fausserait le bilan contre le groupe.

Le bilan affiche aussi la part de TP1 qu'il faut atteindre pour être à zéro avant frais,
1 / (1 + gain/risque) : avec un TP1 à +4 % et un stop à −11 %, il faut 73 % de réussite. **Gagner
souvent ne veut pas dire gagner de l'argent.**

## Avis lié au groupe

Un groupe **prouvé** rend son signal FAVORABLE (fondement « groupe »), même si la géométrie seule
serait défavorable (stop large, TP1 proche) : ses résultats mesurés l'emportent sur l'a priori.
Les refus (données, univers, signal mort ou joué) et le veto « entrée déjà dépassée » restent
bloquants. La preuve est par groupe (le nom écrit en tête du signal, ou le nom donné à la source).

| Preuve | Critères (tous) |
|---|---|
| En direct (registre de CSI) | au moins 30 signaux résolus sur 15 jours ; IC95 du R réalisé **et** IC95 de l'écart au taux de base entièrement > 0 |
| Sur historique (export ou boîte du bot) | au moins 50 signaux résolus sur 20 jours, mesurés **comme le bot** ; IC95 du R net moyen entièrement > 0 ; au plus 10 % de messages supprimés ; valable 30 jours (refaire l'export ensuite) |

Messages supprimés : Telegram numérote les messages d'un chat sans trou. Un numéro absent de l'export
est un message supprimé (ou non exporté). Un groupe qui efface ses pertes laisse donc des trous, et
sa preuve est refusée. Les messages reçus en direct par le bot n'ont pas ce biais.

Ce que la preuve ne dit pas : que le prochain signal gagnera, ni que le groupe continuera. Une
preuve sur historique ne retire pas la dérive du marché (en marché haussier, beaucoup d'achats
gagnent) ; la preuve en direct, elle, exige aussi de battre le taux de base. Cette mesure porte sur
une source externe : elle ne compte pas dans les essais de recherche de CSI et n'utilise que des
bougies publiques.

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

- 2026-10-06 : **groupes de confiance halal** (décision du propriétaire) : une paire USDT publiée par EL MAHWASHI, LEGEND TRADING, IN CRYPTO ou WHALE HUNTING, reconnus par l'identifiant de leur conversation Telegram transmis par le relais (jamais par un nom dans le texte), est ajoutée à l'univers si elle se négocie sur Binance Spot, jamais contre son refus ni un avis défavorable (`UNIVERSE.md`). L'avis d'un signal ne change pas ; F4/F16 gardent leur liste figée.
- 2026-10-06 : **mesure d'un groupe indépendante de la lecture des images et des autres groupes** (relecture
  leak-auditor de l'audit avec images depuis le tableau de bord). Trois changements dans `external/audit.py` :
  (1) un signal TEXTE n'est plus déclaré doublon d'un signal lu sur IMAGE plus ancien (avant, une capture suivie
  du même signal en texte retirait le texte du bilan et de la preuve : sur un export réel, 65 signaux texte
  résolus sans OCR contre 57 avec, R moyen +0,413 contre +0,463, donc une preuve qui dépendait de l'option `--ocr`) ;
  une image reste doublon d'un texte ou d'une image antérieurs ; (2) les doublons se cherchent **dans le même
  groupe** seulement : un groupe qui reprend les signaux d'un autre garde les siens, audité seul ou avec lui
  (avant, `--dir` sur plusieurs exports ou la boîte de BSM pouvait lui retirer ces signaux) ; (3) « Gestions
  comparées » ne prend que les signaux texte, comme le bilan. Effet sur la preuve d'un groupe : elle ne peut que
  compter AUTANT ou PLUS de signaux texte qu'avant (ceux qu'une image ou un autre groupe masquait). Aucun groupe
  n'était prouvé au 2026-10-05 (`PLAN_DE_TRAVAIL.md` § 5) ; les bilans déjà faits avec `--ocr` ou sur plusieurs
  exports à la fois sont à refaire pour une preuve à jour. Tests : `tests/test_audit_independence.py`.
- 2026-10-05 : **nom du trader lu en tête du signal** (`external/parser.group_of`), même lecture et même forme
  canonique que BinanceSpotManager (`trader_name.trader_of` et `name_key`, comparées en-tête par en-tête par
  `tests/test_group_name.py`) : dernière ligne utile avant la paire ou la première étiquette ; formules (« بسم الله
  … », reconnues par phrase entière, diacritiques et tatouil retirés), mots-dièses, données (« Type: Spot »,
  « Risk Level - High »), dates et lignes d'événement (« Harmonic Pattern Detected ») ignorés ; préfixes (« Trader/ »,
  « Ph. ») et suffixes (« Harmonic Indicator Ultra », « SIGNAL ALERT ») retirés. Forme canonique **stricte**, parce
  qu'un nom peut lever des vetos par la preuve de son groupe : majuscules sans accents, particules collées
  (« AL-MAHWASHI » = « ALMAHWASHI », « ABD ELOUADOUD » = « ABDELOUADOUD »), aucun mot retiré (« CRYPTO LEGEND » reste
  distinct de « LEGEND TRADING ») ; les variantes d'un même nom ne sont réunies que par une liste déclarée
  (`NAME_ALIASES` : « AL-MAHWASHI CRYPTO TRADING » = « AL-MAHWASHI CRYPTO », « ALAFIFY TRADING » = « ALAFIFY ») ; un
  nom fait seulement de mots banals (« VIP », « CRYPTO VIP ») ne nomme personne. Sur les exports du propriétaire,
  un nom est lu pour 867 signaux lisibles sur 874, avec les mêmes groupes dans CSI et dans BSM ; l'ancienne règle (première ligne lisible) en
  rangeait une partie sous une ligne d'événement (« HARMONIC TRADE DETECTED » pour Suhaib AlMashhadani, « Harmonic
  Pattern Detected » pour Al-Afify comme pour Apex). Décision du propriétaire : corriger tout de suite. Effet : les
  sources des avis et des bilans prennent les nouveaux noms ; les fournisseurs de F4 et F16 aussi, à partir du
  redémarrage de la surveillance avec l'image reconstruite (ce redémarrage sera inscrit dans « Démarrages » de
  `FORWARD_TESTS.md`) ; aucune décision F4/F16 ni aucune preuve de groupe n'existait avant (deux relectures
  leak-auditor le 2026-10-05).
- 2026-10-02 : taux de base `LIMIT_ALIGNED_V4` : l'écart entre l'entrée et le dernier prix est mesuré en ATR,
  comme le stop et la cible. En % fixe, un signal reçu après une baisse (entrée au-dessus du prix, achat aussitôt
  au marché) plaçait, dans les périodes calmes de l'historique, le stop au-dessus du prix d'achat : stops
  immédiats et taux de base faussement mauvais (constaté sur MOVR : 94 % de stops). Gestion « stop suiveur »
  jugée par défaut.
- 2026-10-01 : bilan d'un groupe sur son historique (`audit-telegram`, carte du tableau de bord,
  `POST /sources/history`), stop à la clôture de bougie écrit dans le signal, avis lié au groupe
  (preuve en direct ou sur historique), nom du groupe lu en tête du signal quand la source est
  générique (« telegram <id> »), pictogrammes ignorés dans les lignes de prix.
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
