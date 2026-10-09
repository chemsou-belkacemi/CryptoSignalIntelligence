# Contribuer à CryptoSignalIntelligence

Merci de votre intérêt. Avant tout, sachez ce qu'est ce projet :

- **un outil de recherche et de mesure** pour Binance Spot, long uniquement (`BUY` ou `NO_TRADE`) ;
- **il ne passe, ne modifie ni n'annule aucun ordre** et n'utilise aucune clé API. L'exécution appartient à un autre
  dépôt, BinanceSpotManager, verrouillé sur Binance Demo ;
- à ce jour, la recherche n'a démontré **aucun avantage directionnel après frais** (plus de 850 essais déclarés à
  l'avance, tous comptés). Des tests en direct pré-inscrits tournent ; leurs verdicts ne sont pas encore connus.

Le projet est écrit **en français** : code, commentaires, messages de la CLI, documentation, issues et demandes de
fusion. Merci d'écrire en français vous aussi.

À lire avant une contribution importante :

- [docs/PROTOCOL.md](docs/PROTOCOL.md) : périodes, walk-forward, critères d'admission ;
- [docs/DELIVERY_STATUS.md](docs/DELIVERY_STATUS.md) : ce qui est testé, non vérifié, bloqué ou non implémenté ;
- [docs/SIGNAL_FORMAT.md](docs/SIGNAL_FORMAT.md) : contrat TXT V3 lu par BinanceSpotManager ;
- [docs/FORWARD_TESTS.md](docs/FORWARD_TESTS.md) : tests en direct pré-inscrits (et figés) ;
- [CLAUDE.md](CLAUDE.md) : vue d'ensemble de l'architecture (écrite pour l'assistant de code, utile à tous).

## Installer pour développer (Linux)

Python **3.14** (paquet `python3.14-venv` sous Ubuntu). `pylock.toml` (PEP 751) fige les versions mais ne liste que
des roues Windows : sous Linux, on en tire des contraintes et pip choisit les roues Linux. C'est la méthode de la CI.

```bash
git clone <url-du-dépôt> CryptoSignalIntelligence
cd CryptoSignalIntelligence
python3.14 -m venv .venv
.venv/bin/python -c "import tomllib; d = tomllib.load(open('pylock.toml', 'rb')); open('constraints.txt', 'w').write(''.join(f\"{p['name']}=={p['version']}\n\" for p in d['packages'] if 'version' in p))"
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -c constraints.txt -e ".[dev,ml]"
```

Inutile d'activer le venv : on appelle directement `.venv/bin/python`, `.venv/bin/csi`, `.venv/bin/ruff`…
Sous Windows : `pip install -r pylock.toml`, puis `pip install --no-deps -e .`.

Aucune clé, aucun jeton, aucun fichier `.env` n'est nécessaire pour développer ni pour lancer les tests.

## Tests, lint, types

```bash
.venv/bin/python -m pytest -m "not network"                   # suite sans Internet (comme la CI)
.venv/bin/python -m pytest tests/test_signals.py::test_nom    # un seul test
.venv/bin/python -m pytest                                    # tout, y compris les tests qui vont sur Internet
.venv/bin/ruff check src tests
.venv/bin/mypy                                                # configuré dans pyproject.toml (src/)
.venv/bin/pyright                                             # facultatif : imports et noms introuvables
```

- La CI GitHub Actions (`.github/workflows/ci.yml`) lance les tests sans réseau, ruff et mypy sous Ubuntu, Python 3.14.
  Une demande de fusion doit passer ces trois étapes.
- Les fixtures (`tests/conftest.py`) sont des **marches aléatoires synthétiques** : elles vérifient que le code
  fonctionne, jamais qu'une stratégie gagne.
- `tests/test_bsm_contract.py` charge le vrai parseur de BinanceSpotManager depuis `../BinanceSpotManager` ou
  `$BSM_PATH` ; il est sauté si ce dépôt est absent. C'est normal sur un clone isolé.
- Gardes à garder vertes en toutes circonstances : `tests/test_secrets.py` (aucun secret en dur),
  `tests/test_repository.py` (aucun fichier source masqué par le `.gitignore`), `tests/test_data.py` (endpoints
  privés refusés), `tests/test_features.py` et `csi validate-causality` (causalité), `tests/test_frozen_running_tests.py`
  (modules gelés des tests en direct).

## Style

- **Français** partout (noms de variables et de fonctions : l'anglais existant reste, ne renommez pas pour renommer).
- Lignes de 110 caractères au plus ; règles ruff de `pyproject.toml` (E, F, W, I, B, UP, SIM).
- Les stratégies sont des **fonctions pures** (`Strategy.evaluate(MarketContext) -> StrategyResult`) : aucun accès
  réseau, aucune E/S, aucun appel à un LLM. Une nouvelle stratégie arrive avec sa classe, son enregistrement dans
  `strategies/registry.py`, sa section `[strategies.<ID>]` et sa fiche d'hypothèse dans `docs/strategies/`.
- Un seul chemin de décision, partagé par le backtest et le direct (`signals/analyze.py`) : ne le dupliquez pas.
- **Tests obligatoires** pour tout calcul de niveau d'entrée, de stop, d'objectif (TP), de R, de coût ou de
  remplissage simulé. Une correction de calcul arrive avec le test qui l'aurait détectée.
- Les prix (entrée, stop, TP) sont arrondis au `tickSize` dans CSI ; la quantité (`stepSize`) et le `minNotional`
  sont l'affaire de BinanceSpotManager.
- Un chiffre présenté comme une probabilité est accompagné de sa définition (cible, horizon, calibration).
- Mettez à jour `docs/DELIVERY_STATUS.md` quand un livrable change d'état.

## Règles qui ne se négocient pas

1. **Aucun ordre.** CSI ne crée, ne modifie ni n'annule aucun ordre Binance, dans aucun mode. Aucune route vers un
   endpoint de compte ou d'ordre, aucune clé API, aucun secret.
2. **La liste blanche de `data/http.py` n'est jamais élargie** vers un endpoint privé (ordres, compte, clés
   d'écoute…). Elle est vérifiée avant tout appel réseau et par `tests/test_data.py`.
3. **Pas de biais de look-ahead.** Toute jointure et toute coupure se font sur `available_at` (clôture + 1 ms +
   latence supposée), jamais sur `open_time`. Décision à la clôture, remplissage au plus tôt à la bougie suivante.
   Le contrôle de causalité et son test de mutation restent verts.
4. **Walk-forward uniquement.** Aucun paramètre choisi sur toute la période. La période `FINAL_TEST` (après le
   2025-06-30, figée dans `research/protocol.py`) ne se lit qu'avec `--i-understand-final-test`, et chaque lecture
   est enregistrée. Tout nouvel essai est déclaré avant son exécution et compté dans `program_trials`.
5. **Modules gelés des tests en direct.** Les tests F1 à F16 et F18 tournent avec des règles figées par empreinte
   (`tests/test_frozen_running_tests.py`). Ne modifiez **jamais** un module gelé ni `docs/FORWARD_TESTS.md` (hors
   section « Démarrages », en ajout seul), et ne mettez **jamais** à jour une empreinte de ce test pour le faire
   passer : cela arrêterait pour de bon un test en cours. Une variante se fait dans un **nouveau** module, avec un
   **nouveau** test pré-inscrit.
6. **Aucun secret en dur**, et aucun fichier `.env` versionné (seul `.env.example` l'est).
7. **Honnêteté des chiffres.** Aucune rentabilité annoncée ; les verdicts viennent du protocole
   ([docs/PROTOCOL.md](docs/PROTOCOL.md)), y compris quand ils sont négatifs. Zéro signal est un résultat valable.

Quelques fichiers demandent une relecture renforcée : `research/protocol.py`, `backtest/exits.py`,
`config/exit_policies.json` (changer une politique change son empreinte : régénérer avec `csi exit-policies --write`)
et `data/http.py`.

## Proposer une modification

1. Ouvrez d'abord une issue (modèle « Idée » ou « Bug ») pour tout changement de comportement, de protocole ou de
   contrat : on en discute avant le code.
2. Créez une branche depuis `main` : `feat/…`, `fix/…`, `docs/…`, `test/…`.
3. Commits courts, en français, au format `type(portée): description` (par exemple
   `fix(niveaux): arrondi du stop vers le bas`).
4. Avant d'ouvrir la demande de fusion : `pytest -m "not network"`, `ruff check src tests` et `mypy` verts.
5. Décrivez dans la demande de fusion ce qui change, pourquoi, comment c'est testé, et cochez la liste du modèle.
   Pour un travail de recherche : le protocole déclaré, le nombre d'essais, la période lue, le verdict tel quel.
6. Rien n'est fusionné sans la relecture du propriétaire du dépôt.

## Ce qui sera refusé

- Toute phrase, tout chiffre ou toute capture qui promet un gain ou présente une stratégie comme rentable.
- Des paramètres optimisés sur toute la période, un résultat obtenu en regardant `FINAL_TEST`, un essai non compté.
- Tout ajout d'exécution d'ordres, de clé API ou d'accès à un endpoint privé dans CSI.
- Toute modification d'un module gelé d'un test en direct, ou d'une empreinte de `tests/test_frozen_running_tests.py`.
- Toute jointure ou coupure sur `open_time` au lieu de `available_at`.
- Un calcul de niveau, de coût ou de R sans test.
- Un secret, un jeton ou un fichier `.env` dans le dépôt.
- Des signaux de vente à découvert (SELL, short) : un contexte baissier donne `NO_TRADE`.

## Licence

En contribuant, vous acceptez que votre contribution soit publiée sous la licence du projet,
GNU AGPL-3.0-or-later ([LICENSE](LICENSE)).
