# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

CryptoSignalIntelligence (CSI) is a pipeline for Binance Spot, long only: data → research → analysis → validation → TXT signals. Every decision is `BUY` or `NO_TRADE`. It **never places, modifies or cancels orders** and uses no API keys. The sibling repo `../BinanceSpotManager` (BSM) is the only component that executes trades; it reads the TXT signals CSI writes.

Code, comments, docs and CLI output are in **French**. Keep new code and docs in French to match.

- Full specification: `docs/PROMPT_MAITRE.md`. Section 26 holds the 2026-09-30 additions, which are referenced as "point N" in docstrings. Section 24 describes how the owner wants changes delivered.
- What is tested, unverified, blocked or not yet built: `docs/DELIVERY_STATUS.md`. Update it when a deliverable changes state.
- Work is organised in numbered "lots": 0–2 are delivered, 3–4 are in progress, 5 (ML) and 6 (LLM agents) come later.

## Règles du propriétaire (prioritaires)

- **Réponses en français**, informelles et concrètes.
- **Aucun secret en dur** : ni clé API, ni jeton Telegram, ni mot de passe dans le code, la
  configuration ou les scripts. CSI n'en a besoin d'aucun ; `tests/test_secrets.py` doit rester vert.
  Ne jamais lire `.env` ni `.env.*` (refusé par `.claude/settings.json`).
- **Pas de mode de trading dans CSI** : CSI ne passe, ne modifie ni n'annule aucun ordre, en aucun
  mode. L'exécution appartient à BinanceSpotManager, verrouillé sur **Binance Demo** ; aucun passage
  en réel sans demande explicite du propriétaire, et jamais depuis CSI.
- **Validation walk-forward uniquement** : aucun paramètre choisi sur toute la période, aucun regard
  sur le test final sans `--i-understand-final-test`. Tout nouvel essai compte dans `program_trials`.
- **Pas de biais de look-ahead** : jointures et coupures sur `available_at`, décision à la clôture,
  remplissage au plus tôt à la bougie suivante ; le contrôle de causalité et son test de mutation
  doivent rester verts.
- **Filtres Binance** : prix arrondis au `tickSize` (niveaux d'entrée, stop, TP) dans CSI ; la
  quantité (`stepSize`) et le `minNotional` sont appliqués par BSM, qui dimensionne les ordres.
- **Tests pytest obligatoires** pour tout calcul de niveau d'entrée, de stop, de TP, de R, de coût
  ou de remplissage simulé. Une correction de calcul arrive avec le test qui l'aurait détectée.
- **Honnêteté des chiffres** : jamais un nombre présenté comme une probabilité sans sa définition,
  jamais de rentabilité annoncée ; les verdicts viennent du protocole (`docs/PROTOCOL.md`).
- Rien n'est fusionné ni poussé sans le propriétaire ; commit seulement sur demande, hors `main`.

## Automatisations Claude Code (`.claude/`, `.github/`)

- **Hook** : après chaque modification d'un `.py`, `ruff check --fix` sur ce fichier
  (`.claude/hooks/ruff_on_edit.py`) ; les erreurs restantes reviennent à Claude.
- **Confirmation demandée** avant de modifier `research/protocol.py`, `backtest/exits.py`,
  `config/exit_policies.json` et `data/http.py` ; lecture de `.env` / `.env.*` interdite.
- **Sous-agents** : `leak-auditor` (look-ahead, overfitting, mesure : à lancer après toute
  modification de features/, backtest/, research/, external/) et `contract-guard` (contrat TXT V3,
  empreintes des politiques, retour d'exécution, test de contrat BSM).
- **Commandes** : `/walk-forward` (propriétaire seulement : un travail lourd à la fois, comparaison au
  run précédent) et `/evaluer` (signal Telegram → verdict expliqué).
- **CI GitHub Actions** : tests sans réseau, ruff, mypy sous Linux ; `tests/test_secrets.py` et
  `tests/test_repository.py` (aucun secret, aucun fichier source masqué par le .gitignore).

## Commands

The venv is a **Windows** venv (`.venv/Scripts/python.exe`) and the owner uses PowerShell. From WSL, call the same executable through interop (it works, and there is no need to activate the venv):

```bash
./.venv/Scripts/python.exe -m pytest                                   # full suite (~20 s)
./.venv/Scripts/python.exe -m pytest tests/test_signals.py::test_name  # single test
./.venv/Scripts/python.exe -m pytest -m "not network"                  # skip tests that need Internet
./.venv/Scripts/ruff.exe check src tests
./.venv/Scripts/mypy.exe                                               # configured in pyproject to check src/
./.venv/Scripts/pyright.exe                                            # language server checks only (imports, undefined names)
./.venv/Scripts/python.exe -m crypto_signal_intelligence <command>     # CLI (also exposed as `csi`)
```

- Install: `pip install -r pylock.toml`, then `pip install --no-deps -e .`. Pinned versions live in `pylock.toml` (PEP 751).
- The CLI is Typer and lives in `cli.py`. Main commands: `doctor`, `download`, `data-quality`, `validate-causality`, `backtest`, `walk-forward`, `screen`, `analyze`, `scan`, `run --mode shadow`, `evaluate-signal`, `import-feedback`, `execution-report`, `exit-policies`, `dashboard`, `backup` / `restore`, `report`. The README lists them with examples.
- `tests/test_bsm_contract.py` loads BSM's real `signal_parser.py` by file path, from `../BinanceSpotManager` or from `$BSM_PATH`, for example a worktree of branch `feat/csi-v2-drop`. The test skips when that file is missing. Most of the 31 skipped tests in a normal run are this contract test.
- Test fixtures (`tests/conftest.py`) are **synthetic** random walks. They check that the code works, never how a strategy performs in the market. The `settings` fixture points `CSI_ROOT` at a temp dir and loads `config/default.toml`.

## Configuration

`config/default.toml` is loaded through pydantic-settings (`config.py`). You can override values with environment variables that use the `CSI_` prefix and `__` between section and key (e.g. `CSI_DATA__SYMBOLS`, `CSI_PUBLICATION__MODE`), or point `CSI_CONFIG_FILE` at another file. `CSI_ROOT` sets the base directory for `data/`, `state/`, `signals/`, `reports/` and `experiments/`. Strategy parameters live in `[strategies.<ID>]` sections. Every strategy and cost value is an experimental hypothesis, not a tuned optimum.

## Architecture

**One decision path shared by backtest and live.** `signals/analyze.py` runs context → vetoes (`validation/gates.py`) → strategy → levels (`levels/engine.py`, which rounds to the tick size and recomputes RR) → vetoes → signal. The backtest simulator (`backtest/simulator.py`) and the live scanner (`live/scanner.py`) run this same path. Do not fork decision logic between them.

**Strategies are pure functions.** `strategies/base.py` defines `Strategy.evaluate(MarketContext) -> StrategyResult`. A strategy downloads nothing, does no I/O and calls no LLM. Each strategy declares:
- `params_model` (frozen pydantic)
- `setup_keys`
- `ablations` and `extensions` (walk-forward variants)
- `calibration_grid` (the coarse recalibration grid; its size is the trial budget)

To add a strategy, write the class, register it in `strategies/registry.py`, add a `[strategies.<ID>]` section to the config, and write a hypothesis sheet in `docs/strategies/`.

**Causality is the top priority.** Every candle has an `available_at` timestamp: close time + 1 ms + the assumed latency. Features (`features/builder.py`, `features/loader.py`) and the 15m↔1h join always use `available_at`, never `open_time`, so a 1h candle that is still forming is excluded. `validation/causality.py` recomputes decisions three ways (only the data available at decision time, the full history, and a falsified future) and requires identical results. Any change to features or joins must keep this check and `tests/test_features.py` passing.

**Data layer (`data/`).**
- `http.py` is the only HTTP client. It enforces a whitelist of public paths (klines, exchangeInfo, ping, time, and the archive prefix `/data/spot/`) and refuses anything else **before** any network call. `tests/test_data.py` asserts that private endpoints are rejected. Never widen this whitelist to order or account endpoints.
- Official archives are checked against their SHA-256 and handled in ms or µs units depending on the source. Paginated REST is used to fill gaps. The Parquet cache (`store.py`) is idempotent, with a quarantine and quality checks.

**Research protocol (`research/`).**
- `protocol.py` hard-codes the DEVELOPMENT / FINAL_TEST boundary (`FROZEN_DEVELOPMENT_END` = 2025-06-30). Config can move it earlier but never later.
- Reading the final test period requires `--i-understand-final-test`, and every such read is recorded.
- `walk_forward.py` runs a purged walk-forward with recalibration on each window's past only. `admission.py` produces the automatic verdict. Experiments are logged in `experiments/`, and reports go to `reports/<run_id>/`.
- Current result: all three strategies (A/B/C) are **REJECTED**, and screening of families D–I (`screen.py`, `docs/SCREENING.md`) found no edge after costs. Do not present any strategy as profitable.

**Signal contract (`signals/`).**
- TXT format **V3** (`docs/SIGNAL_FORMAT.md`). The strict serializer and parser are in `txt.py` and the schema in `schema.py`. A V3 signal carries two expirations: `EXPIRES_AT` for the message and `ENTRY_EXPIRES_AT` for the entry window.
- Publishing (`outbox.py`) goes through a SQLite registry: PENDING → write `.tmp` → fsync → atomic rename to `.txt` → PUBLISHED. `reconcile()` repairs crashes. Consumers deduplicate on `SIGNAL_ID` / `IDEMPOTENCY_KEY`.
- Signals go only to `signals/shadow` unless both `publication.outbox_enabled` and `integration_verified` are true. Both are false while BSM integration is `INTEGRATION_UNVERIFIED`.

**Exit policies (`backtest/exits.py`, `config/exit_policies.json`).** Each policy is identified by an id plus a content hash. Outside shadow mode, only policies that BSM actually executes, with the same id and the same hash, may be published (`publication.consumer_policies`). If you change a policy definition, its hash changes, so regenerate with `exit-policies --write`.

**Live operation (`live/`).**
- `scan_cycle` and `run_forever` run one cycle per 15m close, with a bounded wait for the 15m/1h candles. REST fetches only the missing candles.
- An instance lock (`lock.py`) allows one process at a time. After a restart, only the latest closed candle can produce a signal: stale setups come out as NO_TRADE `EXPIRED` or `DUPLICATE`.
- `backup.py` can suspend publication until reconciliation. `priority.py` runs heavy jobs at low CPU priority.
- Docker Compose and the Windows scheduled task are described in `docs/DEPLOYMENT.md`.

**Other modules.**
- `feedback/`: imports BSM execution feedback (JSONL v2) and reconciles backtest vs forward vs Demo results.
- `external/`: evaluates Telegram signals against historical base rates.
- `news/`: collects news without an LLM, in "observe" mode only, so news has no influence on decisions.
- `reporting/dashboard.py`: writes `state/dashboard.html`.

## Hard rules from the specification

- Never add code that could create, modify or cancel a Binance order, and never require API keys.
- No SELL or short signals; bearish context means NO_TRADE. Zero signals is a valid outcome.
- Do not invent historical data, benchmarks, performance figures, or compatibility with BSM. Integration counts as verified only once the BSM contract test passes against a real `parse_csi_signal`.
- Keep market data (public Binance Spot) and the execution environment (Binance Demo) separate. Do not assume they have the same prices or fills.
