# Relevé de liquidité en shadow (demande du propriétaire du 2026-10-08) — pré-inscription

Code : `forward/liquidity_log.py`. Tests : `tests/test_liquidity_log.py`. Journaux (ajout seul, empreintes chaînées,
comme les autres relevés) : `<CSI_ROOT>/forward/F0_LIQUIDITE_SIGNAUX.jsonl` (signaux Telegram) et
`<CSI_ROOT>/forward/F0_LIQUIDITE-AAAA-MM.jsonl` (relevé périodique, un fichier par mois). Route :
`GET /liquidity?size=500&limit=20`. Carte « Liquidité au moment des signaux (shadow) » dans l'onglet Suivi.

**Question du propriétaire.** « As-tu pris en considération la liquidité du marché, la quantité qui veut acheter ou
pas ? Analyser avant d'entrer. » Réponse honnête : non, pas encore. L'historique du carnet d'ordres de Binance n'est
pas disponible gratuitement (seules les bougies le sont), donc **aucune étude sur le passé n'est possible**. On
commence à l'enregistrer maintenant, pour mesurer plus tard, sur des données que personne n'a encore vues, si la
liquidité au moment d'un signal annonce le bon ou le mauvais trade.

**Ce relevé ne décide rien.** Il est en mode shadow : aucune influence sur les tests en direct (F4, F16 et les
autres), ni sur les avis donnés aux signaux, ni sur BinanceSpotManager. Le contrôle « avant d'entrer » ci-dessous est
affiché comme une information. Aucun pouvoir prédictif n'est revendiqué tant que la mesure n'est pas faite.

## Ce qui est relevé

**Quand.**

- À chaque **nouveau signal Telegram** lisible (parseur commun, sans erreur) sur une paire **USDT** : signaux déposés
  par le relais (`POST /telegram/live`, dossier lu par F4) et boîte de réception de BSM si elle est configurée. Le
  relevé est fait dans les secondes qui suivent la détection (recherche toutes les 5 s, avant les relevés
  périodiques) ; le délai depuis la réception est inscrit (`delay_s`). Un signal qui n'a pas pu être relevé dans les
  30 minutes est inscrit « MANQUE » : le trou reste visible, il n'est jamais comblé après coup.
- **Toutes les 15 minutes**, 5 minutes après chaque quart d'heure (hh:05, :20, :35, :50, hors de la rafale du
  scanner à la clôture), sur les paires suivies : celles de la configuration puis les paires ajoutées prêtes, **au
  plus 40** par cycle (les autres sont listées « skipped » dans l'entrée CYCLE).

**Quoi**, pour chaque relevé (deux demandes publiques, liste blanche de `data/http.py` inchangée) :

- **Carnet** (`/api/v3/depth`, 1 000 niveaux) : meilleur achat, meilleure vente, écart en % du milieu ; USDT
  cumulés côté achat et côté vente à **±0,5 %, ±1 % et ±2 %** du prix médian ; **déséquilibre**
  (achats − ventes) / (achats + ventes) à chaque distance (+1 : que des achats, −1 : que des ventes) ; indicateur
  `truncated` quand les 1 000 niveaux lus n'atteignent pas la borne (la profondeur est alors un minimum) ; courbe de
  profondeur à 0,05 / 0,1 / 0,2 / 0,3 / 0,5 / 0,75 / 1 / 1,5 / 2 %.
- **Glissement estimé** d'un **achat** au marché de 100, 500 et 2 000 USDT (en remontant les ventes, contre la
  meilleure vente) et d'une **vente** au marché de la même valeur (S / milieu unités, en descendant les achats, contre
  le meilleur achat), en %. Vide (`null`) si le carnet lu ne suffit pas à remplir l'ordre.
- **Flux récent**, calculé avec les **klines 1 minute** (`/api/v3/klines`, 1 000 bougies ; aucun autre endpoint) et
  seulement sur les bougies **closes** au moment du relevé : part des achats au marché (« taker buy », en USDT) sur
  15 et 60 minutes, volume en USDT, nombre de transactions, variation du prix, **volume relatif** = volume moyen par
  minute de la fenêtre / volume moyen par minute des ~15 heures qui précèdent la dernière heure.
- L'**heure exacte** du relevé (réponse du carnet), et pour un signal : son identifiant, son fournisseur, sa
  conversation et son heure de réception.

**Débit vers Binance.** Une demande toutes les 2 s au plus (30 par minute), pour tout le relevé ; poids Binance 52
par paire (carnet 50, klines 2). Un cycle de 40 paires = 80 demandes ≈ 2 min 40 s, poids ≈ 2 080 par quart d'heure
(≈ 140 par minute en moyenne, ≈ 780 par minute au plus pendant le cycle, pour une limite publique de 6 000 par
minute et par adresse IP). Une réponse 429 ou 418 suspend le relevé 10 minutes ; trois pannes de suite (réseau ou
5xx) abandonnent le reste du cycle (entrée MANQUE) et suspendent 2 minutes. Une paire inconnue (400) est inscrite
en ERREUR sans arrêter les autres.

**Où il tourne.** Dans son propre fil de la surveillance (`monitor`), démarré par le fil des paires ajoutées
(`live/scanner.py`, toutes les 30 s s'il ne tourne plus) : le scanner ne l'attend jamais, et une panne du relevé
est journalisée sans rien arrêter d'autre. Désactivable par la configuration (`[forward] liquidity_log = false`, ou
`CSI_FORWARD__LIQUIDITY_LOG=false`).

**Volume des journaux.** ≈ 1,3 Ko par relevé : ≈ 2,5 Mo par jour pour 20 paires, ≈ 5 Mo pour 40 (un fichier par
mois, compris dans les sauvegardes). Le journal des signaux reste petit.

## Contrôle « avant d'entrer » (information)

`entry_check(mesures, taille)` : la profondeur est **SUFFISANTE** pour une taille donnée si l'écart est
**< 0,5 %** et si l'achat **et** la vente au marché de cette taille glissent de **< 0,2 %** (contre le meilleur
prix ; la sortie est estimée sur le carnet du moment de l'entrée). Le relevé connaît le glissement à 100, 500 et
2 000 USDT : pour une autre taille, on prend la plus petite taille relevée qui la dépasse (estimation prudente) ;
au-delà de 2 000 USDT, **INCONNUE**. `entry_check_book(carnet, taille)` fait le même contrôle, exact, sur un carnet
brut. Ces seuils sont des repères de bon sens, **pas des paramètres optimisés** ; ils ne filtrent rien.

## Comment on mesurera plus tard (sans fixer le test aujourd'hui)

Le test n'est **pas** fixé maintenant : on ne sait pas encore combien de signaux arriveront, ni sur quelles paires.
Un protocole sera pré-inscrit (dans `docs/FORWARD_TESTS.md`, nouveau test, compté au programme) **quand il y aura
assez de données : au moins quelques semaines et quelques centaines de signaux relevés**, et **avant** de regarder
le lien entre liquidité et issue des signaux. Il dira, avant toute mesure :

- l'issue mesurée (celle de F4 : achat au premier prix après réception, gestion « stop suiveur », R après coûts),
  et le rapprochement par identifiant de signal (`signal_id`) ;
- les quelques variables candidates, déclarées à l'avance (par exemple déséquilibre à ±1 %, part des achats au
  marché sur 15 min, volume relatif sur 1 h, glissement à 500 USDT), **sans** fouiller toutes les combinaisons ;
- la comparaison : signaux en haut contre en bas de chaque variable (seuils fixés sur une première période, mesurés
  sur la suivante), intervalles par blocs de jours, et la même variable relevée aux heures périodiques comme
  référence (une paire « normalement » liquide à cette heure-là) ;
- la règle de décision : aucune utilisation avant un écart dont l'intervalle exclut zéro sur la période de mesure.

D'ici là, les chiffres du relevé restent une description du marché au moment du signal, sans conclusion.

## Limites

- Un instantané du carnet ne dit rien des ordres annulés juste avant ou après (« spoofing ») ni des ordres cachés
  (iceberg) ; le carnet de Binance peut changer en quelques millisecondes.
- 1 000 niveaux lus : sur BTC et ETH, la profondeur à ±2 % peut être tronquée (indicateur `truncated`).
- Le flux « taker buy » vient des bougies 1 min publiques : il ne dit pas qui achète, seulement de quel côté
  l'ordre au marché a frappé.
- Le marché relevé est Binance Spot public ; l'exécution de BSM se fait sur Binance Demo, dont le carnet est
  différent : le glissement estimé ici n'est pas celui de Demo.
- Les signaux reçus en image (OCR) ne sont pas relevés : leur niveau n'est connu qu'après validation, souvent trop
  tard pour un relevé au moment du signal.
