PROMPT MAÎTRE — CRYPTO SIGNAL INTELLIGENCE V2
Version de conception : 29 septembre 2026
Document autonome à copier dans un nouveau projet de développement.
Les règles et paramètres proposés sont des hypothèses à tester, pas des performances démontrées.

TU ES MON INGÉNIEUR PYTHON ET MON PARTENAIRE DE RECHERCHE QUANTITATIVE.

Construis progressivement un projet Python professionnel nommé CryptoSignalIntelligence. Tu dois produire du code exécutable, vérifier les comportements critiques, expliquer les choix et avancer par livrables utilisables. Ne te contente pas de répéter ce cahier des charges. Ne construis pas toutes les fonctionnalités dès le départ.

1. MON CONTEXTE ET LA FRONTIÈRE DU PROJET

Je travaille sous Windows avec PowerShell. J'ai DÉJÀ un autre bot, BinanceSpotManager, qui exécute des ordres sur Binance Demo et gère les entrées, TP, SL et déplacements de SL. Son code et son parseur ne sont pas fournis par ce prompt : ne prétends pas les connaître.

Le nouveau projet fait uniquement :
DONNÉES → RECHERCHE → ANALYSE → VALIDATION → SIGNAUX TXT.

Il ne doit contenir aucune fonction permettant de créer, modifier ou annuler un ordre Binance, en démo ou en réel. Aucune clé privée Binance n'est nécessaire au moteur d'analyse. Les simulations historiques sont locales et ne passent aucun ordre. Le bot existant reste seul responsable des quantités, soldes, positions, ordres et limites de risque effectives.

Périmètre initial : Binance Spot, sans emprunt ni levier, opportunités LONG uniquement. Les décisions sont BUY ou NO_TRADE. Un contexte baissier ne signifie pas « ouvrir un short ». Ne pas émettre SELL comme instruction ambiguë. Un éventuel EXIT_LONG futur exige un contrat distinct et un état de position fourni par le bot d'exécution. Les futures et shorts sont hors V1.

Les données publiques du marché réel et les prix/fills Binance Demo sont deux sources distinctes. Identifier MARKET_DATA_SOURCE et INTENDED_EXECUTION_ENVIRONMENT. Ne jamais supposer des prix, une liquidité ou des remplissages identiques. Le consommateur doit revalider la fraîcheur et le prix avant toute exécution.

But mesurable : démontrer ou réfuter l'utilité des stratégies après coûts, hors échantillon, puis produire des signaux explicables et consommables. Aucune promesse de rendement, de taux de réussite ni de signal quotidien. Zéro signal peut être la bonne réponse.

2. PRIORITÉS ET LIMITES DE COMPLEXITÉ

Ordre de priorité : causalité des données, contrat de signal, simulation réaliste, reproductibilité, validation statistique, fonctionnement continu, ML, puis agents LLM.

Commencer avec BTCUSDT et ETHUSDT. Ajouter SOLUSDT et un univers plus large après vérification de leur disponibilité et de l'historique. Utiliser 15m pour le setup et 1h pour le contexte. Prévoir 4h et 5m dans les interfaces ; ne les activer qu'après validation des jointures temporelles. Le 1m pourra servir à examiner les événements intrabougie, sans prétendre résoudre toutes les ambiguïtés.

V1 : trois stratégies simples, une entrée, un SL, un TP de référence, un moteur de simulation cohérent et un export en dossier shadow. Les quatre TP, l'entrée 2 et le SL évolutif constituent un lot ultérieur obligatoire avant de reproduire fidèlement le comportement du bot existant.

Ne pas forcer quatre TP artificiels pour embellir un signal. Prévoir le schéma pour les sorties partielles ; n'activer que les modes réellement simulés et compris par le consommateur.

3. ARCHITECTURE ET STACK

Un seul projet Python modulaire. Pas de microservices, Kubernetes, Kafka ni système distribué en V1. Une CLI et un stockage local suffisent.

Proposition à ajuster à l'environnement :
- Python 3.12 si les dépendances choisies sont compatibles ; vérifier avant installation.
- numpy, pandas : calculs et transformations.
- httpx : client HTTP unique ; éviter le doublon requests/httpx sans justification.
- pydantic, pydantic-settings : contrats typés et configuration.
- pyarrow/Parquet : séries historiques ; DuckDB : requêtes analytiques.
- SQLite : registre des signaux, événements et expériences ; un processus écrivain en V1.
- typer, rich, logging standard avec rotation.
- TOML pour la configuration initiale, pathlib pour les chemins.
- pytest, ruff ; mypy ciblé sur les frontières importantes.

Dépendances optionnelles par groupe :
- research : vectorbt et Optuna, lorsque les simulations de référence existent.
- ml : scikit-learn puis un seul de LightGBM/XGBoost.
- agents : un fournisseur LLM, puis LangGraph uniquement si l'orchestration le justifie.
- streaming : websockets lorsque REST ne suffit plus.
- reports : bibliothèques de graphiques et éventuellement une interface légère plus tard.

Pour les indicateurs, choisir une implémentation unique et testée. Si pandas-ta-classic est retenu, vérifier installation, licence et conventions. Ne pas mélanger des RSI/ATR aux méthodes de lissage différentes sans documentation. Vectorbt est un outil de recherche vectorisée ; ne pas supposer l'existence d'un « moteur Rust VectorBT » ou la disponibilité gratuite de fonctions PRO. Vérifier les capacités de la version utilisée.

Créer pyproject.toml, un verrouillage reproductible des dépendances et .env.example. Les versions exactes doivent être vérifiées, pas inventées. Pas de GPU nécessaire à la V1. Ne pas imposer Docker/WSL2 au cœur ; isoler les outils de recherche qui en ont besoin.

Structure indicative :
src/crypto_signal_intelligence/
  cli.py
  config.py
  domain/          # contrats, événements, identifiants, règles communes
  data/            # HTTP public, archives, normalisation, cache, qualité
  features/        # indicateurs, jointures causales, contexte BTC
  regimes/         # tendance, volatilité, liquidité
  strategies/      # interface, registre, règles indépendantes
  levels/          # entrées, invalidation, stops, cibles, arrondis
  validation/      # fraîcheur, niveaux, coût, admissibilité
  backtest/        # simulation événementielle locale
  research/        # protocoles, expériences, walk-forward
  signals/         # schéma, sérialiseur, outbox, adaptateurs
  feedback/        # retours du bot externe, résultats théoriques
  ml/              # ajouté à son étape
  agents/          # ajouté à son étape
  reporting/
config/
tests/
data/
signals/shadow/
signals/outbox/
reports/
experiments/
models/
docs/
README.md
pyproject.toml
.env.example
.gitignore

Ne pas remplir tous ces dossiers de classes vides. Créer les modules au moment de leur usage. Aucune logique métier dans main.py ou la CLI.

4. DONNÉES HISTORIQUES ET QUALITÉ

Utiliser les endpoints publics Binance documentés. Pour plusieurs années, privilégier les archives officielles quotidiennes/mensuelles Binance Public Data, contrôler les checksums, puis compléter les données récentes par REST paginé. Respecter les limites de débit, timeouts, Retry-After quand fourni et erreurs temporaires. Ne pas supposer que tous les endpoints autorisent la même taille de page.

Attention documentée : les archives Spot à partir du 1er janvier 2025 utilisent des timestamps en microsecondes. Les autres sources peuvent avoir une autre unité. Définir l'unité par adaptateur/source, vérifier les ordres de grandeur et normaliser sans ambiguïté. Ne pas traiter tous les timestamps comme des millisecondes.

Schéma canonique : exchange, market_type, symbol, timeframe, open_time, close_time, available_at, ingested_at, open, high, low, close, base_volume, quote_volume, number_of_trades, taker_buy_base_volume, taker_buy_quote_volume, is_closed, source, source_version.

Tout est horodaté en UTC. Distinguer le moment économique d'un événement du moment où le système a pu en disposer. Pour l'historique sans heure de réception réelle, documenter une hypothèse de disponibilité et une latence simulée. Ne pas utiliser le téléchargement effectué aujourd'hui comme heure de disponibilité historique.

Contrôles : unicité, chronologie, trous, alignement des intervalles, OHLC cohérents, volumes non négatifs, ratios taker cohérents, bougies ouvertes, âge des données, historique suffisant. Aucun forward-fill silencieux des OHLC manquants. Une donnée invalide est mise en quarantaine ; un signal dépendant est bloqué.

Cache incrémental idempotent avec un petit recouvrement contrôlé pour réconcilier la dernière bougie. Conserver le hash et la version des archives : elles peuvent être corrigées après publication. Les données brutes doivent rester traçables.

Le spread actuel ne reconstitue pas le spread historique. Un carnet actuel ne reconstitue pas le carnet passé. S'il manque une mesure historique, utiliser une hypothèse de coût explicite et plusieurs scénarios ; ne pas afficher une précision fictive.

5. UNIVERS HISTORIQUE ET BIAIS DE SÉLECTION

Un backtest sur les gagnants encore cotés aujourd'hui peut être biaisé. Pour chaque date, utiliser si disponible l'univers alors accessible, avec dates de cotation/retrait et critères de liquidité calculés sur le passé.

Si l'univers point-in-time ne peut pas être reconstruit, afficher cette limite dans les rapports. Un test exploratoire sur BTC/ETH reste utile, mais ne démontre pas une performance sur toutes les cryptos.

Ne jamais filtrer un backtest ancien avec les volumes 24h d'aujourd'hui. Stocker progressivement les snapshots de l'univers et métadonnées d'instruments.

6. CAUSALITÉ ET FEATURES

Les stratégies ne reçoivent qu'un MarketContext immuable contenant les informations disponibles avant la décision. Utiliser uniquement des bougies clôturées. Les features multi-timeframes sont jointes à partir de leur available_at avec une jointure vers le passé, jamais une jointure vers une bougie future.

À 10h17, la valeur finale de la bougie 1h 10h–11h n'est pas utilisable. Un pivot confirmé grâce à deux bougies à droite devient utilisable après ces deux bougies, pas à la date du pivot. Stocker pivot_time et confirmed_at.

Features initiales : rendements, EMA20/50, ATR14, RSI14, moyennes/ratios de volume décalés, extrema glissants décalés, volatilité et contexte BTC. Ajouter Bollinger20 pour le retour à la moyenne. MACD, ADX, EMA200, OBV et taker ratio seulement quand une hypothèse ou une stratégie les utilise. Comparer le volume courant à une référence qui exclut cette bougie.

Normaliser les distances par prix ou ATR pour comparer les actifs. Éviter les moyennes globales du dataset, fenêtres centrées et normalisations ajustées sur tout l'historique. Scalers, imputations, sélection de features et PCA se fit uniquement sur l'entraînement.

Le futur est interdit dans les features et décisions. Il est nécessaire et autorisé dans un module séparé de construction des labels/résultats. Ne pas interdire aveuglément shift(-1) partout : empêcher sa remontée dans le chemin de décision.

Test central de causalité : calculer les résultats jusqu'à t, ajouter ou modifier les lignes après t, recalculer, et vérifier que les décisions et features à t ne changent pas. Ce test réduit le risque sans prouver à lui seul l'absence de toute fuite.

7. RÉGIMES ET CONTEXTE

Éviter une seule catégorie mélangeant tendance et volatilité. Un marché peut être haussier ET très volatil.

MarketRegime contient au moins :
trend = BULL | BEAR | RANGE | UNKNOWN
volatility = LOW | NORMAL | HIGH | UNKNOWN
liquidity = ACCEPTABLE | LOW | UNKNOWN
transition = STABLE | CHANGING

Définir des règles causales, lisibles et configurables. Ajouter de l'hystérésis si les changements de régime sont trop fréquents. Les seuils de quantiles utilisent une fenêtre passée ou un entraînement ; jamais tout le dataset futur.

BTC est un contexte possible pour les altcoins : rendement, volatilité, tendance, stress. Vérifier par ablation si ce filtre améliore réellement les résultats ; ne pas supposer qu'il est toujours utile. Ne pas pénaliser une stratégie de tendance parce que son RSI est élevé sans avoir testé cette règle.

8. INTERFACE DE STRATÉGIE ET FICHES D'HYPOTHÈSE

Strategy.evaluate(context) -> StrategyResult

Résultat : strategy_id/version, action, setup_time, regime, entry_intent, invalidation_reference, exit_policy_id, reasons, warnings, features_used, state_transition, technical_score facultatif.

Une stratégie ne télécharge rien, n'appelle aucun LLM, n'écrit aucun fichier et n'exécute aucun ordre. Les stratégies à retest ont une machine à états persistable : niveau observé, cassure confirmée, attente, retest, validation ou expiration.

Avant chaque stratégie, rédiger une fiche : hypothèse économique/comportementale, régime attendu, univers, données, règle exacte, entrée, stop, sortie, expiration, coûts, modes d'échec, plage de paramètres, protocole de validation et critère d'abandon.

Tous les réglages ci-dessous sont des points de départ expérimentaux. Ne pas les qualifier d'optimaux ni de rentables.

9. STRATÉGIES V1

A. DONCHIAN_VOLUME_BREAKOUT
Hypothèse : une cassure de consolidation peut prolonger un mouvement.
Référence : maximum des high des 20 bougies 15m précédentes, excluant la bougie courante. Signal à la clôture au-dessus du niveau, volume supérieur par exemple à 1,5 fois la moyenne des 20 bougies précédentes, contexte 1h favorable. Évaluer séparément la valeur du filtre volume.
Entrée simulée au plus tôt après décision et latence. Stop structurel ou ATR, règle figée par version. Expiration et distance maximale au niveau. Échecs : fausses cassures, entrée tardive, frais disproportionnés. Comparer à la même cassure sans filtre.

B. EMA_PULLBACK_CONTINUATION
Hypothèse : un repli dans une tendance établie peut offrir une poursuite avec invalidation proche.
Contexte 1h EMA20 > EMA50 avec pente passée positive. Sur 15m, contact d'une zone EMA20 définie en ATR, puis clôture de reprise au-dessus de la zone. Définir précisément contact et reprise ; éviter les descriptions visuelles non codables.
Stop sous le plus bas confirmé du repli avec buffer ATR. Expiration rapide si le setup se dégrade. Échecs : inversion de tendance et achats répétés pendant une baisse. Un seul setup actif par actif/famille.

C. RANGE_REENTRY
Hypothèse : une excursion hors d'un range stable peut revenir vers son centre.
Contexte RANGE, absence de stress BTC selon règle testée. Clôture précédente sous Bollinger basse puis clôture de retour à l'intérieur ; RSI faible facultatif et testé séparément. Cible initiale : centre de bande/range, stop sous l'extrême observé avec buffer. Refuser si la cible restante ne couvre pas suffisamment les coûts et le risque.
Échecs : début de tendance baissière, range trop étroit. Interdire l'accumulation d'entrées pour « se refaire ».

10. STRATÉGIES À RECHERCHER ENSUITE

D. BREAKOUT_RETEST
Attendre une clôture de cassure d'un niveau causal, puis son retest dans les N bougies suivantes, et une clôture de reprise. Acheter après confirmation ; stop sous le retest. Expirer si retest absent, niveau invalidé ou prix déjà trop éloigné. Tester si le meilleur prix compense les cassures manquées.

E. FAILED_BREAKDOWN_RECLAIM
Un support déterminé avant le signal est brièvement enfoncé, puis le prix clôture à nouveau au-dessus. Entrée après réintégration ; stop sous l'excursion ; cible centre/haut du range. Refuser en tendance baissière forte. Ne pas appeler automatiquement cette figure « chasse aux stops institutionnelle » : l'OHLCV ne révèle pas l'intention des intervenants.

F. VOLATILITY_CONTRACTION_EXPANSION
Largeur de Bollinger ou volatilité réalisée dans un quantile bas calculé sur le passé, puis cassure du range avec expansion. Définir fenêtre, quantile, niveau et expiration. Comparer à Donchian pour savoir s'il s'agit d'un apport indépendant ou d'un doublon.

G. RELATIVE_STRENGTH_LEADERS
Classer un univers historiquement disponible selon rendements ajustés de volatilité, et éventuellement performance relative à BTC sur fenêtres passées. Acheter seulement les leaders ayant leur propre setup ; conserver une limite de corrélation entre candidats. Ce classement n'est pas une stratégie couverte et reste exposé à la baisse globale.
Échecs : rotations brutales, survivance, concentration. Tester le système complet de sélection et de capital, pas seulement chaque paire isolée.

H. ANCHORED_VWAP_RECLAIM
Ancrer le VWAP à un événement objectivement identifiable à l'époque : début de journée UTC ou cassure déjà confirmée. Après repli, attendre réintégration du VWAP avec tendance favorable. Stop structurel, cible prédéfinie. Ne jamais choisir après coup le « plus beau » creux d'ancrage. En crypto 24/7, la session UTC est une convention à tester.

I. RESIDUAL_MOMENTUM
Estimer sur données passées une relation de l'altcoin à BTC/ETH puis observer la composante de rendement non expliquée. Utiliser cette force résiduelle comme filtre de setup ou de classement. Tester stabilité du coefficient et turnover. Une position long-only ainsi sélectionnée n'est pas neutre au marché.

J. EVENT_RISK_FILTER
Évaluer une période d'abstention autour d'événements macro connus à l'avance, annonces de retrait ou incidents de sécurité vérifiés. C'est d'abord un filtre de risque ; ne pas transformer automatiquement un titre positif en achat. Les informations doivent être disponibles à la date simulée.

Carnet d'ordres, flux de transactions, funding et open interest : lot de recherche ultérieur, sous réserve d'historique fiable et de licences/coûts connus. Les variables de dérivés peuvent être du contexte d'un signal Spot ; elles ne convertissent pas une stratégie Spot en arbitrage de funding.

Arbitrage multi-plateformes, market making, scalping sub-seconde, stratégies à deux jambes et RL de portefeuille ne sont pas adaptés au contrat TXT initial. Les garder hors MVP.

11. NIVEAUX, SORTIES ET SCORE

TradeLevelEngine calcule tous les prix en Python. Pour une entrée long simple :
risk = entry - stop_loss, strictement positif.
RR_TPi_GROSS = (TP_i - entry) / risk.

Exemple arithmétique synthétique, non signal de marché : entrée 100, stop 98, cibles 102/103/104/106 donnent 1R/1,5R/2R/3R avant frais. Les champs RR sont calculés et testés, jamais écrits arbitrairement. Dans un exemple à entrée 123456,10, stop 122500 et TP1 124000, RR_TP1 vaut environ 0,569, pas 1,2.

Distinguer ratio brut, rendement net simulé et espérance. Un RR élevé n'est pas une probabilité de réussite. Une moyenne pondérée de cibles n'est pas une espérance sans probabilités et trajectoires de remplissage.

Appliquer les règles de précision/tickSize du marché cible quand elles sont connues ; recalculer niveaux et ratios après arrondis. Le bot d'exécution reste responsable des stepSize, quantités, minimums notionnels et règles de son environnement. Ne pas supposer que les filtres du marché réel décrivent exactement la démo.

Lot sorties avancées :
- parts d'entrée explicites, avec définition en quantité ou budget ; ne pas confondre les deux ;
- prix moyen calculé seulement sur les entrées effectivement remplies ;
- fractions de sortie explicites et somme égale à 1 sur la quantité concernée ;
- cas entrée 2 non remplie, ordre partiel, entrée 2 après TP1 et annulation des entrées restantes ;
- règle d'activation d'un SL évolutif et délai d'application ;
- comparer SL fixe, break-even et trailing séparément ;
- break-even économique inclut les coûts, il n'est pas nécessairement égal au prix d'achat ;
- durée maximale et règle de sortie à expiration de la position simulée.

Le profil de gestion du bot externe doit être versionné et simulé à l'identique avant de revendiquer une performance comparable. En son absence, indiquer « profil théorique non aligné au consommateur ».

TECHNICAL_SCORE est optionnel, explicable et propre à la stratégie. Il n'est jamais une probabilité. Un seuil global identique pour un retour à la moyenne et une cassure n'est pas automatiquement pertinent. Comparer règles simples sans score, score, puis ML.

12. BACKTEST DE RÉFÉRENCE

Construire tôt un simulateur événementiel local qui réutilise les décisions et politiques de sortie du moteur d'analyse. Vectorbt peut accélérer le tri d'hypothèses, mais une simulation de référence doit vérifier les candidats retenus, surtout avec sorties partielles et stops évolutifs.

Modéliser : signal émis après clôture, latence, entrée après émission, type d'ordre, expiration, frais par remplissage, spread, slippage, gaps, sorties partielles, liquidité approximative et capital disponible. Une entrée limite n'est pas garantie parce que le low touche son prix. Une simulation OHLCV doit exposer sa règle de fill et ses limites de précision.

TP et SL dans la même bougie : chercher des données plus fines. Si l'ordre reste indéterminé, appliquer une convention pessimiste cohérente et produire si utile des bornes pessimiste/optimiste. Compter les cas ambigus. Ne jamais choisir systématiquement le scénario gagnant. La granularité 1m ne garantit pas une trajectoire exacte.

Si le marché ouvre au-delà d'un stop, ne pas garantir un fill au prix du stop. Distinguer ordre stop-market et stop-limit si ce comportement est simulé. Un stop-limit peut ne pas être rempli.

Même discipline pour un stop remonté après TP1 : pas de déplacement rétroactif dans une bougie au bénéfice du backtest.

Portefeuille : capital initial, allocation, risque simulé par position, maximum de positions, réservations, chevauchement et corrélations. Ne pas additionner les rendements de plusieurs paires comme si chacune disposait de tout le capital. Si seul un backtest de signaux indépendants est disponible, l'étiqueter ainsi.

Scénarios de coûts : central, défavorable et stress, notamment frais/slippage augmentés et retard d'entrée. Les frais ne doivent pas être présentés comme le tarif réel du compte sans information vérifiée.

13. VALIDATION ET CRITÈRES D'ADMISSION

Fixer le protocole avant optimisation : entraînement, validation, puis test final chronologique réservé. Optuna, choix de features, poids, seuils et sélection des stratégies n'accèdent pas au test final. Après consultation répétée, ce test est devenu une validation ; réserver une nouvelle période future.

Walk-forward : réentraîner/recalibrer sur le passé, tester la fenêtre suivante, agréger uniquement les résultats hors échantillon. Les événements de labels qui chevauchent une frontière nécessitent une purge adaptée ; prévoir un gap/embargo selon la structure des fenêtres et le temps d'information réel. Un simple découpage aléatoire est inadmissible.

Conserver toutes les expériences, y compris échecs et abandons, et le nombre d'essais. Tester des plages de paramètres grossières et rechercher des zones stables ; éviter les recherches illimitées de milliers de combinaisons sans budget ni correction du risque de sélection.

Rapports : nombre de candidats, entrées remplies/non remplies/expirées, trades clos et censurés, exposition, turnover, PnL brut/net, expectancy, profit factor, drawdown en mark-to-market, durée, MAE/MFE, performance par régime/actif/période, coûts et ambiguïtés de simulation.

Sharpe/Sortino sur une série de rendements de portefeuille à fréquence régulière, avec convention d'annualisation explicite et hypothèses documentées. Ne pas annualiser arbitrairement une liste de trades irréguliers.

Comparer cash, buy-and-hold, règle simple et version sans chaque filtre. Comparer aussi exposition et risque : une stratégie peu exposée n'a pas le même objectif qu'un portefeuille investi en permanence.

Utiliser des intervalles d'incertitude et un bootstrap par blocs quand la dépendance temporelle le justifie ; ne pas traiter chaque trade corrélé comme une observation indépendante. Une permutation des trades est un stress de séquence limité, pas une preuve de robustesse future. DSR/PSR possibles plus tard avec hypothèses et nombre d'essais documentés.

Statuts : RESEARCH → VALIDATED_OOS → SHADOW → DEMO_ELIGIBLE, ou DISABLED. Les seuils d'admission doivent être définis avant test et peuvent conduire à INCONCLUSIVE. Exiger notamment intégrité des données, absence de fuite détectée, résultats nets hors échantillon, robustesse aux coûts, diversité des fenêtres, stabilité et drawdown acceptable au budget choisi.

Pas de règle magique « 100 trades = validé » ni « 30 jours = rentable ». Taille d'échantillon, dépendance, régimes et largeur des intervalles comptent. Aucun modèle n'est promu uniquement parce qu'il bat légèrement un score sur une seule période.

14. SCANNER ET ABSTENTION

Étape rapide : statut de cotation, quote autorisée, ancienneté, volume passé, spread observé, fraîcheur, disponibilité de l'historique et exclusion des actifs non adaptés.
Étape complète : features causales, régimes, stratégies, niveaux, coût, validation et classement.

Une opportunité ne devient pas meilleure parce que quatre stratégies corrélées l'ont détectée. Grouper les signaux par actif et famille, limiter l'exposition commune à BTC et les candidats fortement corrélés. Limiter le nombre de signaux par scan sans quota minimum.

NO_TRADE doit fournir un code : NO_SETUP, STALE_DATA, DATA_GAP, INSUFFICIENT_HISTORY, UNKNOWN_REGIME, SPREAD_TOO_HIGH, POOR_NET_PROFILE, UNVALIDATED_STRATEGY, DUPLICATE, EXPIRED, PRICE_MOVED, CORRELATED_CANDIDATE, MODEL_STALE, REQUIRED_CONTEXT_UNAVAILABLE.

Un veto déterministe ne peut jamais être contourné par un LLM. Une fonctionnalité optionnelle indisponible est signalée ; une donnée obligatoire indisponible bloque la publication. Ne pas traduire une panne de news par « aucune mauvaise nouvelle ».

15. CONTRAT TXT ET COMPATIBILITÉ AVEC MON BOT

Le vrai format accepté par BinanceSpotManager doit être vérifié à partir d'un exemple réel ou de son parseur. Ne pas confondre le format proposé dans ce prompt et le format actuellement supporté.

Tant que cette compatibilité n'est pas vérifiée : construire le reste, écrire exclusivement dans signals/shadow et afficher INTEGRATION_UNVERIFIED. Demander uniquement l'exemple/parseur nécessaire lors de l'intégration, sans bloquer le développement indépendant.

Créer un objet canonique Signal validé par Pydantic, un sérialiseur TXT, un parseur strict et éventuellement un adaptateur legacy. Ne jamais retirer silencieusement une expiration, une sémantique d'action ou une règle essentielle pour faire passer un format ancien. Si le consommateur ne sait pas les respecter, signaler l'incompatibilité.

Règles de format : UTF-8, point décimal, temps ISO8601 UTC, clé unique par ligne, champs et enums documentés, version explicite, prix positifs finis, NONE pour absences autorisées, aucun NaN/Infinity. Préciser champs obligatoires, optionnels, clés inconnues et comportement par version. Le consommateur ignore la prose après ---ANALYSIS---.

Exemple V2 synthétique de spécification, non connecté et non signal réel. Le support des quatre TP dans cet exemple ne signifie pas qu'ils sont implémentés au lot initial :

SIGNAL_VERSION=2
SIGNAL_ID=EXAMPLE_ONLY_001
IDEMPOTENCY_KEY=EXAMPLE_ONLY_SETUP_001
CREATED_AT=2026-09-29T12:00:05Z
DATA_AS_OF=2026-09-29T12:00:00Z
VALID_FROM=2026-09-29T12:00:05Z
EXPIRES_AT=2026-09-29T12:15:00Z
MARKET_DATA_SOURCE=SYNTHETIC_FIXTURE
INTENDED_EXECUTION_ENVIRONMENT=DEMO
MARKET_TYPE=SPOT
SYMBOL=TESTUSDT
ACTION=BUY
STRATEGY=EMA_PULLBACK_CONTINUATION
STRATEGY_VERSION=1
TIMEFRAME_SETUP=15m
ENTRY_MODE=LIMIT
ENTRY_1=100.00
ENTRY_2=NONE
ENTRY_WEIGHTS=1.0
WEIGHT_BASIS=BASE_QUANTITY
STOP_LOSS=98.00
TP_COUNT=4
TP_1=102.00
TP_2=103.00
TP_3=104.00
TP_4=106.00
TP_WEIGHTS=0.25,0.25,0.25,0.25
EXIT_POLICY_ID=FIXED_SL_FOUR_TP_V1
RR_TP1_GROSS=1.0
RR_TP2_GROSS=1.5
RR_TP3_GROSS=2.0
RR_TP4_GROSS=3.0
TECHNICAL_SCORE=NONE
ML_PROBABILITY=NONE
MODEL_ID=NONE
TREND_REGIME=BULL
VOLATILITY_REGIME=NORMAL
MAX_ENTRY_DEVIATION_BPS=20
VALIDATION_STATUS=SCHEMA_EXAMPLE_ONLY
STATUS=NEW
---ANALYSIS---
REASONS=EXEMPLE_SYNTHETIQUE_NE_PAS_EXECUTER

La V1 à un TP émet TP_COUNT=1, TP_1 valide, TP_2 à TP_4=NONE, TP_WEIGHTS=1.0 et une politique de sortie à un TP. Les seuils d'expiration et de déviation sont des paramètres expérimentaux par stratégie, pas des valeurs universelles.

Distinguer ID unique de publication et clé logique de dédoublonnage stable : marché, actif, stratégie/version, setup, bougie de décision et profil. Inclure un contrôle des signaux concurrents d'autres stratégies sur le même actif. Le texte d'analyse ne peut pas modifier le contrat.

16. PUBLICATION FIABLE ET RETOUR D'EXÉCUTION

Publication : enregistrement durable PENDING avec payload canonique, écriture .tmp dans le même dossier/système de fichiers, flush, synchronisation si disponible, fermeture puis renommage atomique vers .txt. Gérer verrous Windows et retries bornés. Marquer PUBLISHED après publication ; après crash réconcilier registre et fichiers avec la même clé et le même contenu.

Ne pas promettre « exactly once » grâce à un renommage seul. Le consommateur doit tenir un registre durable des IDs traités, refuser les doublons et signaux expirés, puis renvoyer un ACK/REJECT. Une reprise peut retransmettre un même ID ; elle ne doit pas produire deux exécutions.

Prévoir un retour d'événements en lecture depuis le bot existant : RECEIVED, REJECTED, ENTRY_PARTIAL, ENTRY_FILLED, TP_FILLED, STOP_FILLED, CLOSED, avec event_id unique, signal_id, UTC, quantités réellement remplies, prix, frais et raisons. Ce projet les importe ; il ne pilote pas les ordres.

Séparer dans les rapports : résultat théorique, résultat du shadow, résultat Binance Demo observé. L'absence de retour d'exécution n'est pas une perte. Un prix ayant touché une entrée n'est pas la preuve que le bot a été rempli.

Le module peut limiter les candidats corrélés ; une limite fiable sur le portefeuille réel exige un snapshot fourni par le bot externe. Sans celui-ci, afficher PORTFOLIO_STATE_UNKNOWN et ne pas revendiquer un contrôle du risque global.

17. MACHINE LEARNING APRÈS VALIDATION QUANTITATIVE

Première tâche : filtrer les setups produits par les stratégies, plutôt que prédire arbitrairement le prochain prix. Commencer par une régression logistique ; comparer ensuite un seul gradient boosting. Ni deep learning ni RL par défaut.

Définir précisément l'événement prédit : TP de référence atteint avant SL, après entrée remplie, avant une échéance, selon un profil de sortie donné. Associer à chaque probabilité le target_id, horizon, model_id, calibration_id et domaine d'application.

Traiter explicitement UNFILLED, TIMEOUT/NEITHER, AMBIGUOUS et données censurées ; ne pas les supprimer silencieusement pour embellir les métriques. Une probabilité de TP1 avant SL n'est ni une probabilité de finir un portefeuille gagnant ni celle d'atteindre TP4. Pour des sorties complexes, envisager une cible de PnL net et comparer sa pertinence au label binaire.

Ne pas inférer l'espérance du trade avec p seulement si le label n'a pas exactement deux issues exhaustives. Prendre en compte les sorties temporelles, les fills partiels et les frais.

Apprentissage sur candidats de toutes les décisions disponibles, avec traçabilité des rejets. Attention au biais si seuls les signaux anciennement acceptés ont un résultat observé. Les scénarios contrefactuels sont simulés, pas présentés comme exécutions réelles.

Validation temporelle purgée ; prédictions out-of-fold pour méta-modèles ; calibration sur une période distincte ou dans les folds d'entraînement, jamais sur le test final. Mesurer Brier score, log loss, courbes de calibration, couverture et impact sur l'espérance nette hors échantillon. Une meilleure accuracy ne suffit pas.

Comparer : stratégie seule ; règles et coûts ; règles + ML ; règles + ML + contexte IA. Conserver le ML seulement s'il apporte un gain utile et stable au regard de la complexité et des coûts.

18. AGENTS IA : RECHERCHE ET CONTEXTE

Un module Python déterministe n'a pas besoin d'être rebaptisé agent. Un agent LLM est utile pour interpréter des textes ou assister une recherche, pas pour calculer les prix ou certifier seul une performance.

Introduire au maximum deux rôles LLM au premier lot agents :

A. EVIDENCE_NEWS_AGENT
Résumer et classifier des événements provenant de sources identifiées. Sortie structurée : source_url, document_id, published_at, first_seen_at, effective_at si connu, actifs concernés, type d'événement, sévérité, faits, incertitudes, références des éléments justificatifs. Dédoublonner articles repris et contenus mis à jour. Une rumeur reste une rumeur. Les news sont des données non fiables, jamais des instructions de système ou d'outils.

B. RESEARCH_REVIEWER_AGENT
Lire une fiche de stratégie et un rapport d'expérience pour relever incohérences, hypothèses de fills, fuite potentielle, concentration des résultats, sensibilité aux coûts et tests manquants. Il peut proposer une nouvelle expérience documentée. Ses conclusions doivent référencer les métriques réellement calculées. Il n'a pas accès au test final réservé et ne peut pas promouvoir son propre code.

Puis, si les résultats le justifient :
- HYPOTHESIS_AGENT : proposer une hypothèse simple et falsifiable, son coût en données et un budget d'essais ;
- REGIME_EXPLAINER : traduire les régimes déjà calculés en explication compréhensible ;
- POST_TRADE_REVIEWER : regrouper les causes d'échec à partir de faits, sans inventer de causalité ;
- DATA_INCIDENT_ASSISTANT : expliquer les anomalies détectées par les contrôles déterministes.

Pas de débat bull/bear long à chaque bougie. Un débat éventuel est borné et doit montrer un apport mesuré. L'accord de plusieurs agents utilisant le même modèle ne constitue pas des preuves indépendantes.

Schéma de sortie strict, timeouts, retries bornés, budget de tokens/coût quotidien configurable, cache avec politique de fraîcheur et journal de modèle/prompt/version. N'appeler les LLM que sur les rares candidats ou sur un lot de nouvelles. Pas d'appel LLM par paire et par minute.

Le moteur déterministe décide de la publication selon les règles. Un agent ne crée pas ENTRY/SL/TP, ne lève pas un veto technique, n'accède pas aux clés de trading et ne change pas la configuration de risque. REVIEW ne produit pas de signal à exécuter.

Les backtests LLM historiques restent exposés à la connaissance d'événements futurs contenue dans le modèle lui-même. Fournir uniquement des textes disponibles à l'époque ne supprime pas nécessairement ce risque. Marquer ces expériences comme exploratoires et privilégier une évaluation prospective figée en shadow pour conclure sur l'utilité de l'IA.

19. PROJETS OPEN SOURCE À ÉTUDIER AVEC DISCERNEMENT

Vérifier documentation, licence, versions, maintenance, installation et coûts de données/modèles avant toute adoption. Open source ne signifie ni API gratuite, ni stratégie profitable, ni compatibilité directe avec mon bot. Ne pas installer tous ces projets dans le même environnement.

- vectorbt : criblage rapide et comparaisons de paramètres ; garder la simulation événementielle de référence pour les cas complexes.
- Freqtrade/FreqAI : étudier analyses lookahead/recursive, validation et réentraînement temporel. FreqAI appartient à l'écosystème Freqtrade ; ce n'est pas un simple agent autonome exportant mon TXT. Ne pas importer son exécuteur dans ce projet.
- Microsoft Qlib : organisation de datasets, facteurs, modèles et expériences. L'intégration de Binance crypto exige un travail d'adaptation et de validation.
- Microsoft RD-Agent : recherche automatisée de facteurs et modèles ; intéressant plus tard dans un environnement isolé, avec budget d'expériences et jeu de test inaccessible à la boucle d'optimisation.
- TradingAgents de TauricResearch : inspiration pour les rôles d'analyse et de critique. Adapter les données et le périmètre ; ne pas reprendre aveuglément l'exécution ou une promesse de performance.
- LangGraph : orchestration persistante de workflows d'agents si nécessaire ; ce n'est ni un modèle prédictif ni une stratégie de trading.
- FinRL : cadre de recherche en apprentissage par renforcement. Hors MVP : qualité du simulateur, récompense et validation sont des difficultés supplémentaires.

FinGPT/FinRobot et d'autres frameworks financiers peuvent être examinés si une tâche textuelle précise le nécessite ; ne pas ajouter une dépendance uniquement parce qu'elle porte le mot finance ou agent.

20. EXPÉRIENCES, DRIFT ET ÉVOLUTION

Chaque expérience conserve : hypothèse, période, univers, data hash/version, commit, configuration, versions de dépendances, seed, stratégie, modèle, coûts, règles de simulation, métriques et statut. Un run simple en JSON/SQLite suffit au départ ; MLflow peut venir ensuite.

Model registry : période d'entraînement, features, cible, calibration, métriques hors échantillon, expiration, compatibilité du schéma. Surveiller dérive des données, qualité de calibration une fois les labels mûrs, turnover et écart simulation/demo. La dérive n'implique pas automatiquement un réentraînement immédiat.

Réentraînement hors ligne sur résultats arrivés à maturité, puis validation et shadow. Champion/challenger avec promotion explicite et rollback. Aucun apprentissage incontrôlé qui modifie en continu les règles de production.

Un arrêt de publication peut être déclenché par données invalides, coût excessif, incompatibilité ou modèle périmé. Cet arrêt ne ferme aucune position : le bot d'exécution conserve ses propres protections.

21. OBSERVABILITÉ ET RAPPORTS UTILISABLES

CLI lisible, logs structurés avec run_id/signal_id, rotation, latences, fraîcheur des données, erreurs réseau, raisons des rejets, budgets et âge du modèle. Pas de secrets dans les logs.

Vue synthétique future : état des données ; candidats et motifs ; signaux actifs/expirés ; performance théorique séparée de la démo ; stratégies et statut ; coûts IA et santé système. L'interface doit afficher des informations utiles, pas une liste brute de fichiers JSON.

Créer une commande doctor vérifiant environnement, dépendances nécessaires au mode courant, config, droits d'écriture, connexion aux données publiques et intégrité du stockage. L'absence d'une clé LLM ne doit pas bloquer le mode quantitatif.

22. TESTS CIBLÉS SUR LES RISQUES RÉELS

Vérifier notamment :
- unités ms/microsecondes et limites de dates ;
- bougie ouverte exclue, jointure multi-timeframe causale, pivot disponible seulement après confirmation ;
- invariance des décisions passées lorsque le futur est ajouté ou modifié ;
- comportement aux trous, doublons, reprises, manque de warm-up et données périmées ;
- convention d'indicateurs et convergence selon profondeur d'historique ;
- décision à clôture puis fill ultérieur, gaps, TP/SL simultanés, entrée expirée et fills partiels ;
- prix/poids/RR cohérents après arrondis ;
- round-trip TXT, versions inconnues, clés doublonnées, valeurs invalides et compatibilité du parseur réel ;
- crash entre registre et publication, redémarrage, idempotence et absence de signal partiellement lisible ;
- séparation features/labels et fit des transformations uniquement sur training ;
- aucune route vers les endpoints d'ordres privés dans le client de ce projet ;
- timeout/JSON invalide/injection d'instructions dans une news sans pouvoir sur la décision ou les outils.

Les fixtures synthétiques sont étiquetées. Elles testent le code mais ne prouvent pas une performance de marché. Ne jamais déclarer un test ou une installation réussis sans les avoir exécutés. Rapporter clairement ce que l'environnement n'a pas permis de vérifier.

23. ROADMAP AVEC LIVRABLES ET GATES

Lot 0 — Contrats et environnement
Inspecter le dossier réellement disponible, lire ses instructions, vérifier Python et dépendances. Ne pas prétendre accéder à mon Windows depuis un autre environnement. Choisir stack minimale, données et protocole. Créer pyproject, configuration, CLI doctor, schémas et fixtures.
Gate : installation du cœur reproductible et tests des contrats réussis.

Lot 1 — Données + stratégie de référence + simulation
BTC/ETH, historiques 15m/1h, cache/qualité, causalité, Donchian, entrée unique/stop/TP, simulation locale avec coûts, rapport et TXT shadow. Première livraison utile de bout en bout.
Gate : scénario synthétique vérifiable et backtest réel reproductible, limites documentées ; aucune prétention de rentabilité requise.

Lot 2 — Trois familles + laboratoire
Ajouter EMA pullback et retour au range, walkthrough temporel, comparaison aux baselines, stress des coûts, journal d'expériences et rapports hors échantillon. Vectorbt/Optuna seulement selon besoin.
Gate : statut VALIDATED_OOS ou INCONCLUSIVE/REJECTED honnête par stratégie.

Lot 3 — Gestion avancée et compatibilité du bot
Obtenir exemple TXT/parseur et politique réelle de sorties. Implémenter et simuler entrées multiples, TP partiels et SL évolutif si nécessaires. Ajouter adaptateur, expiration, idempotence, événements de retour et tests de contrat. Le code d'exécution reste dans l'autre projet.
Gate : consommateur compatible, shadow fiable, profil de simulation documenté.

Lot 4 — Scanner continu et observation démo
Univers progressif, planification, limites de débit, restart, filtrage corrélation et observabilité. Publication au dossier consommé seulement après activation explicite de l'intégration démo, compatible avec l'autorisation courante.
Gate : flux stable et écarts théorie/demo mesurés sur une période et un échantillon suffisants au protocole ; ne pas fixer une durée comme preuve universelle.

Lot 5 — ML
Baseline logistique, méta-labeling, validation purgée, calibration, ablation, champion/challenger.
Gate : apport hors échantillon et prospectif suffisant pour justifier le maintien du modèle.

Lot 6 — News et agents
Sources horodatées, deux agents maximum au départ, budgets, cache, évaluation prospective.
Gate : utilité mesurable sur décisions, incidents ou temps de recherche au regard du coût et de la latence.

Lot 7 — Extensions choisies
Autres stratégies, facteurs, données de dérivés, outils RD-Agent/Qlib, interface et surveillance de dérive selon les résultats. Ne pas confondre ajout de fonctionnalités et amélioration de l'avantage statistique.

24. COMMANDES CIBLES ET FAÇON DE TRAVAILLER AVEC MOI

Utiliser une CLI unique ; exemples cibles à rendre réellement disponibles progressivement :
python -m crypto_signal_intelligence doctor
python -m crypto_signal_intelligence download --symbol BTCUSDT --timeframe 15m
python -m crypto_signal_intelligence data-quality --symbol BTCUSDT
python -m crypto_signal_intelligence analyze --symbol BTCUSDT --mode shadow
python -m crypto_signal_intelligence backtest --strategy DONCHIAN_VOLUME_BREAKOUT
python -m crypto_signal_intelligence walk-forward --strategy DONCHIAN_VOLUME_BREAKOUT
python -m crypto_signal_intelligence validate-causality
python -m crypto_signal_intelligence scan --mode shadow
python -m crypto_signal_intelligence import-feedback --file feedback.jsonl
python -m crypto_signal_intelligence report --run-id IDENTIFIANT

Fournir les commandes Windows PowerShell adaptées. Quand utile, appeler directement .\.venv\Scripts\python.exe afin de ne pas dépendre de l'activation des scripts. Ne pas modifier globalement la politique de sécurité de PowerShell pour contourner un problème d'activation.

Quand tu modifies le projet :
- si tu peux écrire dans le dépôt, effectue les changements et montre uniquement le résumé utile ;
- si je dois remplacer manuellement des fichiers, fournis seulement les fichiers NEW/MODIFIED nécessaires avec leurs chemins ;
- ne renvoie pas toute l'archive à chaque correction ;
- préserve les modifications existantes et n'écrase pas un projet présent en supposant qu'il est vide ;
- explique ce qui fonctionne, les tests exécutés, les limites restantes et la prochaine étape ;
- avance sans demander une confirmation sur chaque choix réversible ;
- n'invente ni données historiques, ni benchmarks, ni performances, ni compatibilité de mon bot.

PREMIÈRE ACTION ATTENDUE

Commence maintenant par inspecter l'environnement disponible et livrer les lots 0 puis 1 dans la mesure de tes accès. Si aucun environnement de code n'est accessible, donne les commandes PowerShell et les seuls fichiers nécessaires au lot 0, puis poursuis par étapes. Explique d'abord brièvement les hypothèses retenues. Ne commence pas par installer dix frameworks d'agents. Ne crée aucune fonction d'exécution Binance.

25. SOURCES OFFICIELLES POUR VÉRIFIER LES CHOIX

Références consultées pour cette conception le 29 septembre 2026 ; revérifier les versions lors de l'implémentation. Elles documentent des outils et conventions, pas la rentabilité des stratégies proposées.

Binance Public Data : https://github.com/binance/binance-public-data
Filtres Spot Binance : https://developers.binance.com/en/docs/products/spot/filters
Backtests Freqtrade : https://www.freqtrade.io/en/stable/backtesting/
Lookahead Freqtrade : https://www.freqtrade.io/en/stable/lookahead-analysis/
FreqAI : https://www.freqtrade.io/en/stable/freqai/
vectorbt : https://vectorbt.dev/
Qlib : https://github.com/microsoft/qlib
RD-Agent : https://github.com/microsoft/RD-Agent
TradingAgents : https://github.com/TauricResearch/TradingAgents
LangGraph : https://docs.langchain.com/oss/python/langgraph/overview
FinRL : https://github.com/AI4Finance-Foundation/FinRL

26. AJOUTS ET PRÉCISIONS DU 30 SEPTEMBRE 2026

Texte fourni par l'auteur du projet, intégré sans réécrire les sections précédentes. En cas de
conflit, ces ajouts précisent les sections citées entre crochets.

1. Ajouter un véritable mode continu [§14, lot 4]
   Prévoir une commande run --mode shadow qui lance la surveillance jusqu'à l'arrêt du programme. Distinguer analyze pour une analyse ponctuelle, scan pour un cycle et run pour le fonctionnement permanent.
2. Déclencher les analyses aux clôtures des bougies [§14, lot 4]
   Analyser les stratégies lorsqu'une nouvelle bougie est clôturée. Lors des clôtures simultanées 15m/1h/4h, attendre les données nécessaires pendant un délai limité. Si un contexte obligatoire manque, ne pas publier.
3. Optimiser les calculs et les téléchargements [§4, §6, lot 4]
   Charger l'historique une seule fois, récupérer seulement les données manquantes, conserver les fenêtres utiles en mémoire et actualiser les indicateurs progressivement. Calculer le contexte BTC/ETH une fois pour toutes les paires concernées.
4. Séparer surveillance et travaux lourds [§3, lot 4]
   Exécuter backtests, optimisation et entraînement dans des processus distincts. Limiter leur consommation CPU/RAM pour préserver le scanner. Les appels IA ne doivent pas bloquer la réception des données.
5. Gérer les coupures et le passage historique → direct [§4, lot 4]
   Prévoir reconnexion, reprise des abonnements et récupération des bougies manquantes. Éviter les pertes d'événements pendant le démarrage. Les anciennes bougies récupérées reconstruisent l'état sans republier des signaux périmés.
6. Empêcher plusieurs instances de publier simultanément [§16, lot 4]
   Ajouter un verrou d'instance et un registre persistant. Après redémarrage, conserver les setups en attente, les signaux publiés et les confirmations reçues.
7. Collecter réellement les news dès les premières étapes [§18, avancé avant le lot 6]
   Ajouter un collecteur RSS/API indépendant des LLM pour constituer un historique prospectif. Prévoir des adaptateurs pour les actualités crypto, les communiqués officiels et les calendriers macro. Vérifier chaque source avant de la déclarer opérationnelle.
8. Rendre les actualités traçables [§18]
   Conserver source, URL, titre, actifs concernés, date de publication, première réception, corrections et identifiant d'événement. Regrouper les articles qui reprennent la même information. Une panne de source ne signifie jamais « aucune mauvaise nouvelle ».
9. Séparer observation et influence des news [§14, §18]
   Prévoir trois modes : off, observe et gate. Commencer par afficher et enregistrer les événements sans modifier les signaux. Activer ensuite uniquement les règles de blocage évaluées. Une news positive ne doit pas suffire à créer un achat.
10. Préciser les règles des stratégies [§8, §9, §10]
    Remplacer les expressions comme « support solide » ou « beau rejet » par des conditions calculables. Pour chaque stratégie, définir fenêtres, seuils, déclenchement, entrée, invalidation, expiration, sortie temporelle et condition permettant un nouveau setup.
11. Compléter le contrat TXT [§15, lot 3]
    Ajouter ou préciser : DECISION_AT, ENVIRONMENT, ENTRY_EXPIRES_AT, ENTRY_COUNT, RR_REFERENCE, EXIT_POLICY_HASH et NEWS_STATUS. Pour une probabilité ML, ajouter sa cible, son horizon et sa calibration. Augmenter la version du contrat et vérifier sa compatibilité avec ton parseur.
12. Distinguer les deux expirations [§15, lot 3]
    EXPIRES_AT indique jusqu'à quand le bot peut accepter le message. ENTRY_EXPIRES_AT indique jusqu'à quand les entrées restantes peuvent être remplies. Une position déjà ouverte continue de suivre sa politique de sortie.
13. Partager une politique de gestion exacte entre les bots [§11, lot 3]
    Identifier et versionner les règles d'entrée 2, TP partiels, déplacement du SL, sortie temporelle et traitement des reliquats. Le backtest et l'exécuteur doivent reconnaître la même politique avant de comparer leurs performances.
14. Renforcer le réalisme des simulations [§12]
    Ne pas utiliser un plus haut ou un plus bas survenu avant l'entrée pour créditer une sortie après celle-ci. Traiter les ordres d'événements indéterminés comme ambigus. Calculer les frais sur les quantités effectivement remplies et éviter de compter deux fois le spread.
15. Ajouter une réconciliation des publications et des exécutions [§16, lot 3]
    Distinguer fichier publié, message reçu, ordre envoyé et ordre rempli. Une absence de confirmation reste UNKNOWN. Une retransmission conserve le même identifiant ; elle ne doit pas fabriquer un nouveau signal pour contourner un état incertain.
16. Exploiter précisément les retours du bot démo [§16, lot 3]
    Importer prix, quantités, remplissages partiels, frais et devise des frais. Comparer séparément backtest, simulation prospective et exécution démo. Expliquer les écarts : délai, prix, entrée manquée, taille ou politique différente.
17. Encadrer davantage le ML et les agents IA [§17, §18, lots 5-6]
    Entraîner uniquement sur des résultats arrivés à maturité. Utiliser des découpages temporels explicites pour la calibration. Limiter les agents à leurs documents et outils autorisés ; aucun accès aux clés Binance, au dossier de publication ou au test final réservé.
18. Contrôler les coûts et les réponses périmées de l'IA [§18, lot 6]
    Prévoir budget quotidien, plafonds de tokens/appels, concurrence limitée, timeouts et cache versionné. Après une réponse lente, recontrôler prix, fraîcheur et expiration. Les appels payants restent désactivés tant qu'un budget n'est pas configuré.
19. Préparer l'exploitation sur VPS ou NAS [§3, lot 4]
    Fournir le lancement automatique, les volumes persistants, les limites de ressources, l'arrêt propre et le redémarrage après panne. Distinguer processus démarré et service prêt. Vérifier le fonctionnement après fermeture de la session d'administration.
20. Ajouter sauvegarde, restauration et procédure de reprise [§16, lot 4]
    Sauvegarder les données mais aussi les identifiants, confirmations, états des stratégies et modèles actifs. Après restauration, suspendre la publication jusqu'à réconciliation avec le consommateur pour éviter les doublons.
21. Mesurer la rapidité et afficher une interface utile [§21, lot 4]
    Mesurer temps réseau, calcul, disque, IA et délai de publication. Afficher santé, dernière analyse, opportunités, rejets, news et performances séparées. Éviter une interface composée de fichiers JSON bruts.
22. Définir des preuves de livraison [§22, tous les lots]
    Pour chaque fonctionnalité, indiquer : implémentée et testée, implémentée mais non vérifiée, bloquée par un accès externe ou non implémentée. Tester notamment coupures, doublons, données périmées, saturation et restauration. Un logiciel livré ne signifie pas qu'une stratégie est validée pour être utilisée.

Conséquences sur la roadmap (§23) :
- Lot 3 absorbe les points 11 à 16 (contrat V3, deux expirations, politique de gestion partagée et
  hachée, réalisme des fills, réconciliation publication/exécution, retours démo détaillés).
- Lot 4 absorbe les points 1 à 6 et 19 à 21 (run continu, déclenchement aux clôtures, calcul
  incrémental, processus séparés, reprise après coupure, verrou d'instance, VPS/NAS, sauvegarde,
  mesures et interface).
- Le collecteur de news sans LLM (points 7 à 9, mode observe seulement) est avancé : il démarre
  avec le lot 4 pour constituer un historique prospectif, sans influence sur les signaux ; le mode
  gate attend des règles de blocage évaluées. Les agents restent au lot 6 (points 17 et 18).
- Le point 10 s'applique aux fiches existantes (A, B, C) et à toute nouvelle stratégie.
- Le point 22 est tenu dans docs/DELIVERY_STATUS.md.

FIN DU PROMPT
