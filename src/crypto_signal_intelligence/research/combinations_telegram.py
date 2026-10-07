"""Étape 3, voie A : filtre des signaux Telegram du propriétaire par les briques (docs/COMBINAISONS.md, § 5).

Consultation DÉCLARÉE de la période réservée (consultation n° 4, 2 essais sur FINAL_TEST) : rien ne s'exécute sans
`--i-understand-final-test`, et l'ordre interne est celui du § 5.8 : contrôle de complétude des bougies → consultation
inscrite → briques et comptages → résultats → rapport. Les comptages qui n'exigent aucune donnée de marché
(`message_counts`) se font sans rien consulter.

Tout est calculé sur les bougies dont l'`available_at` est ≤ l'heure de réception (`date_unixtime`) : refus, briques,
« dernier profil disponible ». `external/audit.py` n'est pas modifié (sa bougie de référence ignore la latence).
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci
from ..config import Settings
from ..external.audit import DUPLICATE_DAYS, HistoryItem, parser_fingerprint, read_telegram_export
from ..external.parser import parse
from ..external.trailing import replay_trailing
from ..patterns.indicators import ema
from . import combinations as cb
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode
from .protocol import FinalTestLocked
from .volatility import _window_means

KIND = STRATEGY = "COMBO_TELEGRAM"
EXPORTS = ("ChatExport_2026-10-02 (3)", "ChatExport_2026-10-02 (4)", "ChatExport_2026-10-02 (5)")
CUTOFF = pd.Timestamp("2026-10-07", tz="UTC")
FOLLOW = pd.Timedelta(days=30)
ENTRY_WINDOW = pd.Timedelta(hours=24)
LAST_RECEPTION = CUTOFF - ENTRY_WINDOW - FOLLOW
DOWNLOAD_START = pd.Timestamp("2025-11-01", tz="UTC")
STORE_DIR = "combinaisons_telegram"
STEP15 = pd.Timedelta(minutes=15)
MAX_REFERENCE_AGE = pd.Timedelta(hours=2)
BRICKS = ("TENDANCE", "PROFIL", "FLUX", "STOP_VOL", "BTC_HAUSSIER")
FILTERS = {"FILTRE_3": 3, "FILTRE_4": 4}
N_TRIALS = 2
HOLDOUT_SHARE = 0.30
MIN_GROUP = 4
DRAWS, SEED = 10_000, 20261007
ALPHA_STUDY, ALPHA_HOLDOUT, HARMFUL = 0.05 / 2, 0.05, 0.975
MIN_USEFUL_STUDY, MIN_USEFUL_HOLDOUT = 100, 40
MIN_KEPT_STUDY, MIN_DAYS_STUDY, MIN_KEPT_HOLDOUT = 30, 10, 20
GAIN_BLOCK_DAYS = 7
FLOW_BARS = 24
VOL_HOURS = 168
# Statuts
OK, UNREADABLE, DUP_GROUP, DUP_CROSS, LATE = "OK", "ILLISIBLE", "DOUBLON", "DOUBLON_ENTRE_GROUPES", "EXCLU_DATE"
NO_DATA, INVALID, PLAYED, STALE, NO_BRICK = "SANS_DONNEES", "INVALIDE", "DEJA_JOUE", "PERIME", "BRIQUE_NON_CALCULABLE"
CONFIRMED, NOT_CONFIRMED, INSUFFICIENT, HARMFUL_STATUS, NOTHING = (
    "FILTRE_CONFIRME", "NON_CONFIRME", "INSUFFISANT", "FILTRE_NEFASTE", "RIEN")
STUDY, HOLDOUT = "etude", "ecart"


class AlreadyConsulted(RuntimeError):
    pass


class IncompleteBars(RuntimeError):
    pass


@dataclass
class Signal:
    received: pd.Timestamp
    group: str
    status: str
    reason: str = ""
    symbol: str = ""
    entries: tuple[float, ...] = ()
    stop: float = math.nan
    targets: tuple[float, ...] = ()
    message_id: str = ""
    votes: dict[str, float] = field(default_factory=dict)
    part: str = ""
    r: dict[str, float] = field(default_factory=dict)
    outcome: str = ""

    @property
    def entry(self) -> float:
        return self.entries[0]

    @property
    def month(self) -> str:
        return f"{self.received:%Y-%m}"


# --- Lecture et doublons (§ 5.2) -----------------------------------------------------------------------------------

def read_exports(root: Path) -> list[HistoryItem]:
    """Les trois exports Telegram Desktop, TEXTE seulement (aucune image lue)."""
    items: list[HistoryItem] = []
    for name in EXPORTS:
        path = root / name / "result.json"
        items += read_telegram_export(json.loads(path.read_text(encoding="utf-8")))
    return items


def signals_from(items: list[HistoryItem]) -> list[Signal]:
    out = []
    for item in sorted(items, key=lambda i: (i.received_at, i.message_id)):
        received = pd.Timestamp(item.received_at)
        received = received.tz_localize("UTC") if received.tzinfo is None else received.tz_convert("UTC")
        parsed = parse(item.text)
        group = item.group or "inconnu"
        if parsed.errors or parsed.stop is None:
            out.append(Signal(received, group, UNREADABLE, message_id=item.message_id))
            continue
        out.append(Signal(received, group, OK, symbol=parsed.symbol, entries=tuple(parsed.entries), stop=float(parsed.stop),
                          targets=tuple(parsed.targets), message_id=item.message_id))
    return out


def dedupe(signals: list[Signal]) -> list[Signal]:
    """(1) doublons PAR GROUPE sur 7 jours (même clé exacte qu'`audit.py`) ; (2) puis, parmi les restants, doublons
    ENTRE GROUPES : même paire, même jour UTC de réception, seul le premier reçu est gardé."""
    seen: dict[tuple, pd.Timestamp] = {}
    for s in sorted(signals, key=lambda x: x.received):
        if s.status != OK:
            continue
        key = (s.group, s.symbol, s.entries, s.stop, s.targets)
        if key in seen and s.received - seen[key] <= pd.Timedelta(days=DUPLICATE_DAYS):
            s.status, s.reason = DUP_GROUP, f"même signal du groupe le {seen[key]:%Y-%m-%d %H:%M}"
            continue
        seen[key] = s.received
    first: dict[tuple, Signal] = {}
    for s in sorted(signals, key=lambda x: x.received):
        if s.status != OK:
            continue
        day_key = (s.symbol, s.received.floor("D"))
        if day_key in first:
            s.status, s.reason = DUP_CROSS, f"déjà reçu de {first[day_key].group} le même jour"
            continue
        first[day_key] = s
    return signals


def apply_cutoff(signals: list[Signal]) -> list[Signal]:
    """Exclusion PAR LA DATE (jamais par l'étiquette « provisoire ») : réception + 24 h + 30 jours ≤ coupure."""
    for s in signals:
        if s.status == OK and s.received + ENTRY_WINDOW + FOLLOW > CUTOFF:
            s.status, s.reason = LATE, "suivi de 30 jours non terminé à la coupure du 2026-10-07"
    return signals


def message_counts(settings: Settings) -> dict:
    """Comptages sans aucun prix (inscrits avant la consultation) : messages, signaux lisibles, doublons, coupure,
    parties d'étude et tenue à l'écart AVANT les refus (bornes hautes), cases groupe × mois."""
    items = read_exports(settings.root / "imports" / "telegram")
    signals = apply_cutoff(dedupe(signals_from(items)))
    status = pd.Series([s.status for s in signals]).value_counts().to_dict()
    ok = [s for s in signals if s.status == OK]
    split(ok)
    cells = pd.DataFrame([(s.part, s.group, s.month) for s in ok], columns=["part", "group", "month"])
    sizes = cells.value_counts().reset_index(name="n") if len(cells) else pd.DataFrame(columns=["part", "group", "month", "n"])
    per_part = {}
    for part in (STUDY, HOLDOUT):
        mine = sizes[sizes["part"] == part]
        per_part[part] = {"signals": int(mine["n"].sum()), "cells": int(len(mine)),
                          "cells_ge_2": int((mine["n"] >= 2).sum()), "signals_in_cells_ge_2": int(mine.loc[mine["n"] >= 2, "n"].sum())}
    return {"messages": len(items), "status": status,
            "readable_by_month": pd.Series([s.month for s in signals if s.status != UNREADABLE]).value_counts().sort_index().to_dict(),
            "kept_by_month": pd.Series([s.month for s in ok]).value_counts().sort_index().to_dict(),
            "kept_by_group": pd.Series([s.group for s in ok]).value_counts().to_dict(),
            "groups": len({s.group for s in ok}), "parts_before_refusals": per_part,
            "parser": parser_fingerprint()[:16], "cutoff": str(CUTOFF), "last_reception": str(LAST_RECEPTION)}


# --- Refus et briques sur `available_at` (§ 5.2, 5.3) -----------------------------------------------------------------

def refuse(signal: Signal, bars15: pd.DataFrame, max_deviation_pct: float) -> tuple[str, str]:
    """Mêmes règles que `audit.measure`, la bougie de référence étant la dernière dont l'`available_at` ≤ réception."""
    before = bars15[pd.to_datetime(bars15["available_at"], utc=True) <= signal.received]
    if before.empty or signal.received - (pd.Timestamp(before["open_time"].iloc[-1]) + STEP15) > MAX_REFERENCE_AGE:
        return NO_DATA, "aucune bougie disponible juste avant la réception"
    close = float(before["close"].iloc[-1])
    if close <= signal.stop:
        return INVALID, f"prix {close:g} déjà au stop {signal.stop:g} ou dessous"
    if close >= signal.targets[0]:
        return PLAYED, f"prix {close:g} déjà à TP1 {signal.targets[0]:g} ou au-dessus"
    if (signal.entry / close - 1) * 100 > max_deviation_pct:
        return STALE, f"entrée {signal.entry:g} à plus de {max_deviation_pct:g} % au-dessus du prix {close:g}"
    return OK, ""


def bricks_at(signal: Signal, h1: pd.DataFrame, daily_btc: pd.DataFrame, *, latency: pd.Timedelta,
              mutation: str | None = None) -> dict[str, float]:
    """Les 5 votes (1 / 0 / NaN si non calculable) à la réception, sur les seules bougies 1 h disponibles
    (`available_at` ≤ réception). « Dernier profil disponible » : `P_d` (jour de la réception) seulement si sa dernière
    bougie, celle de 23:00 du jour `d − 1`, est disponible (`d` + latence ≤ réception), sinon `P_{d−1}`. Mutation
    `profil_courant` (fuite, pour les tests) : `P_d` toujours, calculé sur toutes les bougies."""
    frame = cb.prepare(h1)
    known = frame[frame["available_at"] <= signal.received].reset_index(drop=True)
    out = dict.fromkeys(BRICKS, math.nan)
    if len(known) < 2:
        return out
    grid = cb.Grid.of(known)
    close = known["close"].to_numpy(float)
    e200 = ema(close, cb.EMA_TREND)
    if np.isfinite(e200[-1]):
        out["TENDANCE"] = float(close[-1] > e200[-1])
    day = signal.received.floor("D")
    if mutation == "profil_courant":
        _, val, _, _ = cb.profile_window(frame, cb.Grid.of(frame), day)
    else:
        chosen = day if day + latency <= signal.received else day - pd.Timedelta(days=1)
        _, val, _, _ = cb.profile_window(known, grid, chosen)
    if np.isfinite(val):
        out["PROFIL"] = float(signal.entry >= val)
    _, delta = cb.centred_delta(known, grid)
    flow = delta[-FLOW_BARS:]
    if len(flow) == FLOW_BARS and np.isfinite(flow).all():
        out["FLUX"] = float(flow.sum() > 0)
    spread = grid.spread(close)
    log_close = np.log(np.where(spread > 0, spread, np.nan))
    squared = np.full(grid.size, np.nan)
    squared[1:] = np.diff(log_close) ** 2
    var = _window_means(squared, np.array([grid.size - VOL_HOURS]), VOL_HOURS)[0]
    if np.isfinite(var) and signal.stop > 0:
        out["STOP_VOL"] = float(math.log(signal.entry / signal.stop) >= math.sqrt(24 * var))
    btc = cb.join_btc(pd.Series([signal.received]), daily_btc)[0]
    out["BTC_HAUSSIER"] = float(btc) if np.isfinite(btc) else math.nan
    return out


# --- Parties, cases, placebo, décisions (§ 5.6) -----------------------------------------------------------------------

def split(signals: list[Signal]) -> None:
    """Par groupe, signaux triés par réception : les 30 % les plus récents (arrondi vers le bas) tenus à l'écart ; un
    groupe de moins de 4 signaux va entièrement dans la partie d'étude."""
    groups: dict[str, list[Signal]] = {}
    for s in signals:
        groups.setdefault(s.group, []).append(s)
    for mine in groups.values():
        mine.sort(key=lambda x: (x.received, x.message_id))
        held = math.floor(HOLDOUT_SHARE * len(mine)) if len(mine) >= MIN_GROUP else 0
        for k, s in enumerate(mine):
            s.part = HOLDOUT if k >= len(mine) - held else STUDY


def useful_cells(groups: np.ndarray, months: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """Masque des signaux en cases utiles : case groupe × mois d'au moins 2 signaux où le filtre garde au moins 1
    signal sans les garder tous."""
    frame = pd.DataFrame({"g": groups, "m": months, "k": keep.astype(int)})
    agg = frame.groupby(["g", "m"])["k"].agg(["size", "sum"])
    ok = agg[(agg["size"] >= 2) & (agg["sum"] >= 1) & (agg["sum"] < agg["size"])].index
    return pd.MultiIndex.from_arrays([groups, months]).isin(ok)


def placebo_p(r: np.ndarray, keep: np.ndarray, cells: np.ndarray, *, draws: int = DRAWS, seed: int = SEED) -> tuple[float, float]:
    """(R moyen des gardés en cases utiles, p) : dans chaque case utile, autant de signaux tirés au hasard que le
    filtre en garde ; p = (1 + tirages ≥ observé) / (1 + tirages)."""
    observed = float(r[keep].mean())
    rng = np.random.default_rng(seed)
    totals = np.zeros(draws)
    for code in np.unique(cells):
        idx = np.flatnonzero(cells == code)
        k = int(keep[idx].sum())
        picks = np.argsort(rng.random((draws, len(idx))), axis=1)[:, :k]
        totals += r[idx][picks].sum(axis=1)
    means = totals / int(keep.sum())
    return round(observed, 6), float((1 + np.sum(means >= observed - 1e-12)) / (1 + draws))


def judge_part(signals: list[Signal], name: str, part: str) -> dict:
    """Comptages, statistique et p d'un filtre dans une partie (cases utiles seulement)."""
    mine = [s for s in signals if s.part == part]
    need = FILTERS[name]
    keep = np.array([sum(v == 1 for v in s.votes.values()) >= need for s in mine], dtype=bool)
    groups = np.array([s.group for s in mine], dtype=object)
    months = np.array([s.month for s in mine], dtype=object)
    useful = useful_cells(groups, months, keep) if len(mine) else np.zeros(0, dtype=bool)
    out: dict = {"signals": len(mine), "kept": int(keep.sum()), "useful_signals": int(useful.sum()),
                 "useful_kept": int((keep & useful).sum()),
                 "useful_cells": int(len({(g, m) for g, m, u in zip(groups, months, useful, strict=True) if u})),
                 "kept_days": int(len({s.received.floor("D") for s, k, u in zip(mine, keep, useful, strict=True) if k and u}))}
    if not (keep & useful).any():
        return out | {"p": None}
    r = np.array([s.r["central"] for s in mine])[useful]
    codes = np.array([f"{g}|{m}" for g, m in zip(groups[useful], months[useful], strict=True)])
    stat, p = placebo_p(r, keep[useful], codes)
    return out | {"stat": stat, "all_mean": round(float(r.mean()), 6), "gain_vs_all": round(stat - float(r.mean()), 6), "p": p}


def decide(study: dict, holdout: dict) -> str:
    if study["useful_signals"] < MIN_USEFUL_STUDY or study.get("p") is None:
        return INSUFFICIENT
    if study["p"] <= ALPHA_STUDY:
        if study["useful_kept"] < MIN_KEPT_STUDY or study["kept_days"] < MIN_DAYS_STUDY:
            return INSUFFICIENT
        if holdout["useful_signals"] < MIN_USEFUL_HOLDOUT or holdout["useful_kept"] < MIN_KEPT_HOLDOUT \
                or holdout.get("p") is None:
            return INSUFFICIENT
        return CONFIRMED if holdout["p"] <= ALPHA_HOLDOUT else NOT_CONFIRMED
    if study["p"] >= HARMFUL:
        return HARMFUL_STATUS
    return NOTHING


def gain_description(signals: list[Signal], name: str) -> dict:
    """R moyen des signaux gardés, intervalles par blocs de 7 jours de réception (90 % et 95 %), sans verdict."""
    need = FILTERS[name]
    out: dict = {}
    for part in (STUDY, HOLDOUT):
        mine = [s for s in signals if s.part == part and sum(v == 1 for v in s.votes.values()) >= need]
        times = np.array([s.received.to_datetime64() for s in mine])
        for scenario in ("central", "defavorable"):
            r = np.array([s.r[scenario] for s in mine])
            row: dict = {"n": len(r), "mean": round(float(r.mean()), 4) if len(r) else None}
            for level in (0.90, 0.95):
                ci, blocks = day_block_ci(r, times, block_days=GAIN_BLOCK_DAYS, samples=DRAWS, seed=SEED, level=level) \
                    if len(r) else (None, 0)
                row[f"ci_{round(level * 100)}"] = ci
                row["blocks"] = blocks
            out[f"{part}_{scenario}"] = row
    return out


# --- Bougies de la période réservée (téléchargement après la relecture, jamais ici sans autorisation) ----------------------

def store_settings(settings: Settings, start: pd.Timestamp = DOWNLOAD_START) -> Settings:
    data = settings.data.model_copy(update={"history_start": start.date()})
    return settings.model_copy(update={"root": settings.root / STORE_DIR, "data": data})


def download_bars(settings: Settings, symbols: list[str], *, allow_final_test: bool,
                  progress: Callable[[str], None] | None = None) -> list[dict]:
    """Bougies 1 h (volume taker, nombre de transactions) et 15 min des paires des signaux, du 2025-11-01 à la coupure.
    Lecture de la période réservée : exige `--i-understand-final-test` (le téléchargement n'est pas la consultation,
    qui est inscrite au calcul)."""
    if not allow_final_test:
        raise FinalTestLocked("bougies de la période réservée : ajouter --i-understand-final-test")
    from ..data.pipeline import download
    say = progress or (lambda _t: None)
    target = store_settings(settings)
    out = []
    for symbol in sorted(set(symbols) | {"BTCUSDT"}):
        for timeframe in ("1h", "15m"):
            try:
                summary = download(target, symbol, timeframe, now=CUTOFF.to_pydatetime())
                out.append({"symbol": symbol, "timeframe": timeframe, "rows": summary.report.rows if summary.report else 0})
            except Exception as exc:  # noqa: BLE001 - une paire en échec est rapportée, la complétude bloquera
                out.append({"symbol": symbol, "timeframe": timeframe, "error": f"{type(exc).__name__}: {exc}"})
            say(f"{symbol} {timeframe}")
    return out


def load_bars(settings: Settings, symbol: str, timeframe: str) -> pd.DataFrame | None:
    from ..features.loader import MissingData, load_candles
    try:
        frame = load_candles(store_settings(settings), symbol, timeframe)
    except MissingData:
        return None
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    return frame[frame["open_time"] + pd.Timedelta(timeframe) <= CUTOFF].reset_index(drop=True)


def completeness(signals: list[Signal], bars: dict[str, dict[str, pd.DataFrame | None]]) -> dict[str, dict]:
    """Avant la consultation : bougies 1 h et 15 min présentes pour chaque paire, 1 h jusqu'à la dernière réception,
    15 min jusqu'à la dernière réception + 24 h + 30 jours. Une paire cotée tard n'est pas un manque : ses signaux sans
    brique calculable sont exclus et comptés (§ 5.2)."""
    out: dict[str, dict] = {}
    for symbol in sorted({s.symbol for s in signals if s.status == OK}):
        last = max(s.received for s in signals if s.status == OK and s.symbol == symbol)
        h1, m15 = bars.get(symbol, {}).get("1h"), bars.get(symbol, {}).get("15m")
        ok_h1 = h1 is not None and len(h1) > 0 and pd.Timestamp(h1["open_time"].max()) + pd.Timedelta(hours=2) >= last
        ok_15 = (m15 is not None and len(m15) > 0
                 and pd.Timestamp(m15["open_time"].max()) + STEP15 >= last + ENTRY_WINDOW + FOLLOW - pd.Timedelta(hours=1))
        out[symbol] = {"ok": bool(ok_h1 and ok_15), "h1": bool(ok_h1), "m15": bool(ok_15)}
    return out


def measure_signal(signal: Signal, m15: pd.DataFrame, settings: Settings) -> None:
    """R par signal (gestion « stop suiveur » par défaut), central et défavorable ; jamais rempli = 0 R."""
    after = m15[m15["open_time"] >= signal.received.ceil("15min")].reset_index(drop=True)
    for scenario, costs in (("central", settings.costs["central"]), ("defavorable", settings.costs["adverse"])):
        issue, r = replay_trailing(after, entry=signal.entry, stop=signal.stop, targets=list(signal.targets),
                                   entry_window=settings.external.entry_window_bars,
                                   max_hold=int(FOLLOW / STEP15), costs=costs, tp_count=settings.external.tp_count)
        if issue == "PENDING":
            raise IncompleteBars(f"{signal.symbol} {signal.received} : bougies 15 min insuffisantes")
        signal.r[scenario] = 0.0 if issue == "UNFILLED" else float(r if r is not None else 0.0)
        if scenario == "central":
            signal.outcome = issue


def pre_counts(measured: list[Signal]) -> dict:
    """Comptages sans R (§ 5.6) : votes oui par brique, signaux gardés par filtre, par partie et par groupe, cases
    utiles et signaux dans ces cases."""
    out: dict = {"votes_yes": {b: int(sum(s.votes.get(b) == 1 for s in measured)) for b in BRICKS},
                 "by_part": {p: sum(s.part == p for s in measured) for p in (STUDY, HOLDOUT)}, "filters": {}}
    for name, need in FILTERS.items():
        row: dict = {}
        for part in (STUDY, HOLDOUT):
            mine = [s for s in measured if s.part == part]
            keep = np.array([sum(v == 1 for v in s.votes.values()) >= need for s in mine], dtype=bool)
            groups = np.array([s.group for s in mine], dtype=object)
            months = np.array([s.month for s in mine], dtype=object)
            useful = useful_cells(groups, months, keep) if len(mine) else np.zeros(0, dtype=bool)
            row[part] = {"signals": len(mine), "kept": int(keep.sum()), "useful_signals": int(useful.sum()),
                         "useful_kept": int((keep & useful).sum()),
                         "useful_cells": len({(g, m) for g, m, u in zip(groups, months, useful, strict=True) if u}),
                         "kept_by_group": pd.Series(groups[keep]).value_counts().to_dict() if keep.any() else {}}
        out["filters"][name] = row
    return out


def evaluate(measured: list[Signal]) -> dict:
    """Décisions des deux filtres (parties déjà attribuées par `split`)."""
    rows = {}
    for name in FILTERS:
        study, holdout = judge_part(measured, name, STUDY), judge_part(measured, name, HOLDOUT)
        rows[name] = {"study": study, "holdout": holdout, "decision": decide(study, holdout),
                      "gain_descriptif": gain_description(measured, name)}
    return {"filters": rows}


def _failed_once(registry: ExperimentRegistry) -> bool:
    """Une seule consultation, terminée en panne sans aucun chiffre : une reprise est permise (règle de
    LIGNES_DE_TENDANCE.md)."""
    with registry.connect() as db:
        rows = db.execute("SELECT status FROM runs WHERE kind=?", (KIND,)).fetchall()
    return registry.final_test_consulted(STRATEGY) == 1 and [r[0] for r in rows] == ["FAILED"]


def run(settings: Settings, *, now: datetime, allow_final_test: bool, retry: bool = False,
        progress: Callable[[str], None] | None = None) -> dict:
    """Voie A, exécution unique : complétude → consultation inscrite → briques → comptages → R → décisions → rapport.
    `retry` : seule reprise permise après une panne sans aucun chiffre produit."""
    say = progress or (lambda _t: None)
    state = code_state()
    if state.endswith("+DIRTY") or state == "NO_GIT_COMMIT":
        raise DirtyCode(f"code non commité ({state}) : exécution refusée")
    from . import combinations_study as cs
    if cs.CODE_REVIEW is None:
        raise cs.NotReady("relecture leak-auditor du code non inscrite (CODE_REVIEW) : voie A refusée (§ 5.8)")
    if not allow_final_test:
        raise FinalTestLocked("voie A : consultation déclarée de la période réservée, ajouter --i-understand-final-test")
    registry = ExperimentRegistry(settings.experiments_db)
    if registry.final_test_consulted(STRATEGY) and not (retry and _failed_once(registry)):
        raise AlreadyConsulted(f"{STRATEGY} a déjà consulté la période réservée : aucune seconde lecture")
    signals = apply_cutoff(dedupe(signals_from(read_exports(settings.root / "imports" / "telegram"))))
    symbols = sorted({s.symbol for s in signals if s.status == OK})
    bars = {sym: {"1h": load_bars(settings, sym, "1h"), "15m": load_bars(settings, sym, "15m")} for sym in symbols}
    btc = load_bars(settings, "BTCUSDT", "1h")
    checks = completeness(signals, bars)
    missing = sorted(k for k, v in checks.items() if not v["ok"])
    if btc is None or missing:
        raise IncompleteBars(f"bougies incomplètes ({missing[:10]}, BTC {'absente' if btc is None else 'présente'}) : "
                             "rien n'est consulté")
    run_id = new_run_id("CMBT")
    consultations = registry.consult_final_test(run_id, STRATEGY)
    try:
        result = _compute(settings, signals, bars, btc, run_id, say)
    except BaseException as exc:                            # consultation consommée sans résultat : trace au registre
        registry.record(run_id=run_id, created_at=now.isoformat(), kind=KIND, hypothesis="voie A (panne)",
                        strategy=STRATEGY, strategy_version=1, variant="définitions figées (docs/COMBINAISONS.md § 5)",
                        params={}, period_label="FINAL_TEST", period_start=str(DOWNLOAD_START), period_end=str(CUTOFF),
                        universe=[], data_hashes={}, git_commit=state, dependencies=dependency_versions(), seed=SEED,
                        cost_scenario="", simulation_rules={}, status="FAILED", report_dir=None,
                        metrics={"n_trials": N_TRIALS, "consultations_total": consultations,
                                 "error": f"{type(exc).__name__}: {exc}"})
        raise
    report_dir = settings.reports_dir / run_id
    payload = {"run_id": run_id, "n_trials": N_TRIALS, "consultations_total": consultations, "result": result,
               "parser": parser_fingerprint(), "doc": cb.DOC, "checks": checks}
    registry.record(run_id=run_id, created_at=now.isoformat(), kind=KIND,
                    hypothesis="garder les signaux Telegram où au moins 3 (ou 4) briques sur 5 sont d'accord améliore-t-il "
                               "le R par signal par rapport au même nombre tiré au hasard dans le même groupe et le même mois ?",
                    strategy=STRATEGY, strategy_version=1, variant="définitions figées (docs/COMBINAISONS.md § 5)",
                    params={"filters": FILTERS, "cutoff": str(CUTOFF), "draws": DRAWS, "seed": SEED},
                    period_label="FINAL_TEST", period_start=str(DOWNLOAD_START), period_end=str(CUTOFF),
                    universe=symbols, data_hashes={"parser": parser_fingerprint()}, git_commit=state,
                    dependencies=dependency_versions(), seed=SEED, cost_scenario="central (décision) et défavorable",
                    simulation_rules={"gestion": "stop suiveur (external/trailing.py)", "non_rempli": "0 R"},
                    metrics={"n_trials": N_TRIALS, "decisions": {k: v["decision"] for k, v in result["filters"].items()}},
                    status="COMPLETED", report_dir=str(report_dir))
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    pd.DataFrame([asdict(s) for s in signals]).to_json(report_dir / "signaux.json", orient="records", force_ascii=False,
                                                       date_format="iso")
    return payload


def _compute(settings: Settings, signals: list[Signal], bars: dict, btc: pd.DataFrame, run_id: str,
             say: Callable[[str], None]) -> dict:
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    daily = cb.btc_daily(btc)
    latency = pd.Timedelta(seconds=settings.data.assumed_availability_latency_seconds)
    deviation = settings.external.max_entry_deviation_pct
    for s in signals:
        if s.status != OK:
            continue
        b = bars[s.symbol]
        s.status, s.reason = refuse(s, b["15m"], deviation)
        if s.status != OK:
            continue
        s.votes = bricks_at(s, b["1h"], daily, latency=latency)
        if any(math.isnan(v) for v in s.votes.values()):
            s.status, s.reason = NO_BRICK, "une brique n'est pas calculable"
        say(f"{s.symbol} {s.received:%Y-%m-%d}")
    measured = [s for s in signals if s.status == OK]
    split(measured)
    before = pre_counts(measured)                         # comptages inscrits AVANT tout R (§ 5.6)
    (report_dir / "comptages.json").write_text(json.dumps(before, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    for s in measured:
        measure_signal(s, bars[s.symbol]["15m"], settings)
    result = evaluate(measured)
    result["counts_before_r"] = before
    result["status"] = pd.Series([s.status for s in signals]).value_counts().to_dict()
    return result
