"""Tests en direct F25 à F30 : événements de marché lus dans les journaux du COLLECTEUR (docs/COLLECTE.md ;
docs/FORWARD_TESTS.md, sections F25_LIQ_CASCADE … F30_TRENDING). Module commun, gelé par les six tests ;
`forward/f25.py` … `f30.py` ne font que le nommer.

Ce que fait chaque test, à chaque passage horaire de la surveillance :
1. **Lecture** (seule) des journaux `C_<SOURCE>-AAAA-MM.jsonl` jusqu'à l'instant du passage, par `read_entries` :
   aucune écriture, aucun appel au collecteur ; une entrée n'est retenue que si elle a été écrite au plus tard
   `LAG_*` après la fin de sa minute, de son heure ou de son relevé (règle par entrée, indépendante de l'heure du
   passage : un passage en retard trouve exactement les mêmes événements).
2. **Séries** (fonctions pures) sur une grille régulière de fins de fenêtre (15 min ou 1 h) : une fenêtre touchée par
   un **trou du collecteur** (10 minutes consécutives sans entrée de minute, résumé horaire absent, tardif ou à moins de
   50/60 du nombre d'échantillons d'une heure pleine, relevé absent) n'a pas de valeur : elle n'est **pas évaluable** et
   comptée comme telle.
3. **Seuil auto-calibré de façon causale** : quantile (ou médiane) des valeurs de la même grandeur, même paire, sur les
   fenêtres STRICTEMENT antérieures (7 jours, 24 h ou 30 jours), au moins 80 % de fenêtres évaluables.
4. **Événements** : fenêtres finies au moins 7 jours après le démarrage (rodage), avant la fin du recueil ; un seul par
   paire et par 24 h (le suivant ≥ 24 h après le précédent inscrit).
5. **Entrée** à la clôture 15 min Binance Spot qui suit l'instant où l'événement est connu (fin de fenêtre + délai),
   jamais avant ; 20 placebos à des clôtures 15 min tirées dans [entrée + 1 h ; entrée + 7 j] (placebos « avant »
   seulement, `docs/PRICE_ACTION.md` § 11.3), descriptifs.
6. **Mesure** sur les bougies 15 min publiques de Binance Spot (`data/http.py`, `/api/v3/klines`, liste blanche
   inchangée) une fois l'entrée + 7 j + 72 h passée : rendement brut et net (frais taker aller-retour de
   `forward/costs`, central et défavorable) à 4 h, 24 h (horizon de décision) et 72 h.
7. **Verdict au rendement seul** (leçon de `PRICE_ACTION.md` § 11) : hypothèse positive, `SUPERIEUR_A_ZERO` si
   l'intervalle 1 − 0,05/6 du rendement NET à 24 h est > 0 en central ET en défavorable ; hypothèse négative,
   `INFERIEUR_A_ZERO` si l'intervalle 1 − 0,05/6 du rendement BRUT à 24 h est < 0 ; `INSUFFISANT` sous 30
   événements résolus ou 50 jours distincts ; sinon `NON_DEMONTRE`.

Rien n'est écrit ailleurs que dans le journal du test (`forward/<ID>.jsonl`) : ni `signals/`, ni `SignalRegistry`, ni
`state/assistant*`, ni Telegram. Ces tests mesurent des événements : ce ne sont pas des appels à suivre. Aucun ordre,
aucun gain démontré.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci, day_block_ci95
from ..config import Settings
from .costs import ADVERSE, CENTRAL, SCENARIOS, costs_for
from .journal import Journal, utc_iso
from .registry import ForwardTest

# --- Constantes communes (figées avec ce module) --------------------------------------------------------------------------

LIQUIDATIONS, CARNET, FLUX, OPTIONS, ATTENTION = "LIQUIDATIONS", "CARNET", "FLUX", "OPTIONS", "ATTENTION"
LIQ_MINUTE, FLUX_MINUTE, FLUX_LARGE = "LIQ_MINUTE", "FLUX_MINUTE", "FLUX_GROS"
CARNET_RESUME, OPTIONS_15M, TRENDING_H = "CARNET_RESUME", "OPTIONS_15M", "ATTENTION_TRENDING_H"
COLLECT_PREFIX = "C_"

LIQ, IMB, BID_DROP, WHALE, FEAR, TREND = "LIQ_CASCADE", "MUR_ACHETEURS", "RETRAIT_LIQUIDITE", "BALEINES", "PEUR_OPTIONS", "TRENDING"
F15_ID = "F15_FIGURES"
BTC = "BTCUSDT"

MINUTE, STEP, HOUR, DAY = pd.Timedelta(minutes=1), pd.Timedelta(minutes=15), pd.Timedelta(hours=1), pd.Timedelta(days=1)
LAG_MINUTE = pd.Timedelta(minutes=2)          # entrée de minute retenue si écrite ≤ fin de la minute + 2 min
LAG_RESUME = pd.Timedelta(minutes=5)          # résumé horaire du carnet retenu si écrit ≤ fin de l'heure + 5 min
LAG_SNAPSHOT = pd.Timedelta(minutes=10)       # relevé d'options ou « trending » retenu si écrit ≤ son créneau + 10 min
MAX_SILENCE_MINUTES = 10                      # 10 minutes consécutives sans entrée : trou du collecteur
CARNET_NOMINAL = 720                          # échantillons de 5 s d'une heure pleine, à défaut d'historique
CARNET_MIN_SHARE = 50 / 60                    # résumé horaire : au moins 50/60 du nombre « plein » (plus de 10 min sans carnet = trou)
COVERAGE = 0.8                                # référence : au moins 80 % de fenêtres évaluables
RODAGE = pd.Timedelta(days=7)
DEDUP = pd.Timedelta(hours=24)
READ_MARGIN = pd.Timedelta(days=1)
MAX_HOURS_PER_PASS = 72                       # rattrapage borné par passage (surveillance arrêtée plusieurs jours)

PLACEBOS = 20
PLACEBO_MIN, PLACEBO_MAX = pd.Timedelta(hours=1), pd.Timedelta(days=7)
HORIZONS = {"4h": pd.Timedelta(hours=4), "24h": pd.Timedelta(hours=24), "72h": pd.Timedelta(hours=72)}
PRIMARY = "24h"
GROSS = "brut"
MEASURES = (GROSS, *SCENARIOS)
RESOLVE_AFTER = PLACEBO_MAX + HORIZONS["72h"] + pd.Timedelta(minutes=30)
GAP_AFTER = pd.Timedelta(days=2)
KLINES_PATH, KLINES_LIMIT = "/api/v3/klines", 1000

ALPHA, FAMILY = 0.05, 6
LEVEL = 1 - ALPHA / FAMILY                    # intervalle de décision : 1 − 0,05/6 (famille « données du collecteur »)
MIN_EVENTS, MIN_DAYS = 30, 50
SAMPLES, SEED, BLOCK_DAYS, MIN_BLOCKS = 10_000, 20261011, 7, 8

CONTROL, EVENT, RESOLUTION = "CONTROLE", "EVENEMENT", "RESOLUTION"
RESOLVED, GAP = "RESOLU", "TROU"
ABOVE, BELOW, NOT_SHOWN, INSUFFICIENT, RUNNING = ("SUPERIEUR_A_ZERO", "INFERIEUR_A_ZERO", "NON_DEMONTRE", "INSUFFISANT",
                                                  "EN_COURS")
NOTE = ("Événements de marché lus dans les journaux du collecteur : mesure seulement, aucun appel à suivre, aucun "
        "message, aucun ordre, aucun gain démontré.")


@dataclass(frozen=True)
class Spec:
    """Règle d'un test : grandeur lue, grille, délai de connaissance, référence et condition."""
    test_id: str
    kind: str
    sign: int                       # +1 : hypothèse positive (net > 0) ; −1 : hypothèse négative (brut < 0)
    universe: str                   # "F15" (liste figée de F15), "CONFIG" (data.symbols), "BTC"
    step: pd.Timedelta              # pas de la grille des fins de fenêtre
    lag: pd.Timedelta               # délai entre la fin de fenêtre et l'instant où l'événement est connu
    reference: pd.Timedelta         # profondeur de la référence du seuil
    window: int                     # fenêtres de grille dans la référence
    min_periods: int                # fenêtres évaluables exigées dans la référence
    quantile: float | None = None
    floor: float | None = None      # plancher absolu (F25 : 250 000 USDT)
    ratio: float | None = None      # F27 : valeur < ratio × médiane
    consecutive: int = 1            # F26 : 2 heures consécutives au-dessus du seuil

    @property
    def short(self) -> str:
        return self.test_id.split("_", 1)[0]

    def describe(self) -> dict:
        return {"kind": self.kind, "sign": self.sign, "universe": self.universe, "step_minutes": int(self.step / MINUTE),
                "lag_minutes": int(self.lag / MINUTE), "reference_hours": int(self.reference / HOUR),
                "reference_windows": self.window, "reference_min_windows": self.min_periods, "quantile": self.quantile,
                "floor_usdt": self.floor, "ratio": self.ratio, "consecutive": self.consecutive}


def _min_periods(window: int) -> int:
    return math.ceil(COVERAGE * window)


SPECS: dict[str, Spec] = {
    "F25_LIQ_CASCADE": Spec("F25_LIQ_CASCADE", LIQ, +1, "F15", STEP, LAG_MINUTE, pd.Timedelta(days=7), 672, _min_periods(672),
                            quantile=0.99, floor=250_000.0),
    "F26_MUR_ACHETEURS": Spec("F26_MUR_ACHETEURS", IMB, +1, "CONFIG", HOUR, LAG_RESUME, pd.Timedelta(days=7), 168,
                              _min_periods(168), quantile=0.95, consecutive=2),
    "F27_RETRAIT_LIQUIDITE": Spec("F27_RETRAIT_LIQUIDITE", BID_DROP, -1, "CONFIG", HOUR, LAG_RESUME, pd.Timedelta(hours=24), 24,
                                  _min_periods(24), ratio=0.5),
    "F28_BALEINES": Spec("F28_BALEINES", WHALE, +1, "CONFIG", STEP, LAG_MINUTE, pd.Timedelta(days=7), 672, _min_periods(672),
                         quantile=0.99),
    "F29_PEUR_OPTIONS": Spec("F29_PEUR_OPTIONS", FEAR, +1, "BTC", STEP, LAG_SNAPSHOT, pd.Timedelta(days=30), 2880,
                             _min_periods(2880), quantile=0.95),
    "F30_TRENDING": Spec("F30_TRENDING", TREND, -1, "F15", HOUR, LAG_SNAPSHOT, HOUR, 1, 1),
}
TEST_IDS = tuple(SPECS)
SOURCE_OF = {LIQ: LIQUIDATIONS, IMB: CARNET, BID_DROP: CARNET, WHALE: FLUX, FEAR: OPTIONS, TREND: ATTENTION}


# --- Lecture des journaux du collecteur (lecture seule) ----------------------------------------------------------------------

def journal_files(root: Path, source: str, since: pd.Timestamp, until: pd.Timestamp) -> list[Path]:
    """Fichiers mensuels `C_<source>-AAAA-MM.jsonl` qui couvrent [since, until] (existants seulement)."""
    out: list[Path] = []
    month = pd.Timestamp(year=since.year, month=since.month, day=1, tz="UTC")
    while month <= until:
        path = Path(root) / "forward" / f"{COLLECT_PREFIX}{source}-{month:%Y-%m}.jsonl"
        if path.exists():
            out.append(path)
        month = (month + pd.Timedelta(days=32)).replace(day=1)
    return out


def read_entries(root: Path, source: str, kinds: set[str], *, since: pd.Timestamp, until: pd.Timestamp) -> list[dict]:
    """Entrées valides des journaux du collecteur dont l'horodatage d'écriture `at` est dans [since, until], dans l'ordre
    des fichiers. Filtre rapide sur le début de la ligne (JSON canonique : `{"at":"…` en tête) avant tout décodage ;
    une ligne coupée ou illisible est passée (elle est signalée par le journal lui-même)."""
    since, until = pd.Timestamp(since), pd.Timestamp(until)
    low, high = utc_iso(since - pd.Timedelta(seconds=1))[:19], utc_iso(until + pd.Timedelta(seconds=1))[:19]
    needles = [f'"kind":"{k}"'.encode() for k in kinds]
    out: list[dict] = []
    for path in journal_files(root, source, since, until):
        with path.open("rb") as handle:
            for line in handle:
                if not line.startswith(b'{"at":"'):
                    continue
                stamp = line[7:26].decode("ascii", "replace")
                if stamp < low or stamp > high or not any(n in line for n in needles):
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get("kind") not in kinds or not isinstance(entry.get("data"), dict):
                    continue
                if since <= _when(entry["at"]) <= until:
                    out.append(entry)
    return out


# --- Séries (fonctions pures) -------------------------------------------------------------------------------------------------

def minute_grid(ends: pd.DatetimeIndex, window: pd.Timedelta) -> pd.DatetimeIndex:
    """Minutes (débuts) de [première fin − fenêtre − 15 min ; dernière fin[."""
    return pd.date_range(ends.min() - window - pd.Timedelta(minutes=MAX_SILENCE_MINUTES + 5), ends.max() - MINUTE, freq="min")


def silence_ok(known: Iterable[pd.Timestamp], ends: pd.DatetimeIndex, *, window: pd.Timedelta) -> np.ndarray:
    """Vrai pour chaque fin E dont la fenêtre [E − fenêtre, E) ne touche aucun trou : aucune minute de la fenêtre n'est la
    10e (ou plus) d'une suite de minutes sans entrée. La longueur d'une suite est comptée à partir de son début et
    JUSQU'À la minute considérée (jamais au-delà de E) : causal."""
    if len(ends) == 0:
        return np.zeros(0, bool)
    minutes = minute_grid(ends, window)
    stamps = pd.DatetimeIndex(sorted(set(known))).as_unit("ns")
    present = minutes.isin(stamps.tz_convert("UTC") if len(stamps) else pd.DatetimeIndex([], tz="UTC"))
    idx = np.arange(len(minutes))
    last_present = np.maximum.accumulate(np.where(present, idx, -1))
    run = np.where(present, 0, idx - last_present)
    width = int(window / MINUTE)
    longest = pd.Series(run).rolling(width, min_periods=1).max().to_numpy()
    pos = ((ends - minutes[0]) / MINUTE).astype(int) - 1
    return longest[pos] < MAX_SILENCE_MINUTES


def _when(text) -> datetime:
    """Horodatage ISO (avec fuseau) du journal, lu vite (`datetime.fromisoformat`)."""
    return datetime.fromisoformat(str(text))


def known_minutes(entries: Iterable[dict], *, lag: pd.Timedelta = LAG_MINUTE) -> dict[datetime, dict]:
    """Données des entrées de minute écrites au plus tard `lag` après la fin de leur minute ; une minute écrite deux fois
    (redémarrage du collecteur) : la première entrée compte."""
    out: dict[datetime, dict] = {}
    delay = (MINUTE + lag).to_pytimedelta()
    for entry in entries:
        data = entry["data"]
        try:
            minute = _when(data["minute"])
        except (KeyError, ValueError, TypeError):
            continue
        if _when(entry["at"]) <= minute + delay and minute not in out:
            out[minute] = data
    return out


def _window_sums(per_minute: Mapping[str, Mapping[datetime, float]], ends: pd.DatetimeIndex,
                 window: pd.Timedelta) -> pd.DataFrame:
    """Somme par paire des valeurs par minute sur [E − fenêtre, E), pour chaque fin E."""
    minutes = minute_grid(ends, window)
    width = int(window / MINUTE)
    pos = ((ends - minutes[0]) / MINUTE).astype(int)
    columns = {}
    for symbol, values in sorted(per_minute.items()):
        arr = np.zeros(len(minutes))
        if values:
            stamps = pd.DatetimeIndex(list(values.keys())).as_unit("ns")
            k = ((stamps.asi8 - minutes[0].value) // MINUTE.value).astype(int)
            keep = (k >= 0) & (k < len(minutes))
            np.add.at(arr, k[keep], np.asarray(list(values.values()), float)[keep])
        cs = np.concatenate([[0.0], np.cumsum(arr)])
        columns[symbol] = cs[pos] - cs[pos - width]
    return pd.DataFrame(columns, index=ends, dtype=float)


def liq_values(entries: Iterable[dict], symbols: Iterable[str], ends: pd.DatetimeIndex, *, fields: list[str],
               window: pd.Timedelta = HOUR) -> tuple[pd.DataFrame, np.ndarray]:
    """F25 : notionnel des LONGS liquidés par paire sur [E − 1 h, E), d'après `LIQ_MINUTE` (les 10 paires les plus
    liquidées de chaque minute ; le reste, agrégé, n'est attribué à aucune paire : minimum). Valeurs NaN pour une fenêtre
    touchée par un trou ; une paire de l'univers sans aucune liquidation lue vaut 0 (fenêtre évaluable)."""
    wanted = set(symbols)
    minutes = known_minutes(entries)
    i_long = fields.index("long_liq_usdt")
    per: dict[str, dict[datetime, float]] = {}
    for minute, data in minutes.items():
        for symbol, row in (data.get("pairs") or {}).items():
            if symbol in wanted:
                per.setdefault(symbol, {})[minute] = float(row[i_long])
    ok = silence_ok(minutes.keys(), ends, window=window)
    values = _window_sums(per, ends, window)
    for symbol in sorted(wanted - set(values.columns)):
        values[symbol] = 0.0
    values = values[sorted(values.columns)]
    values.loc[~ok] = np.nan
    return values, ok


def whale_values(minute_entries: Iterable[dict], large_entries: Iterable[dict], symbols: Iterable[str],
                 ends: pd.DatetimeIndex, *, window: pd.Timedelta = HOUR) -> tuple[pd.DataFrame, np.ndarray]:
    """F28 : (gros achats taker − grosses ventes taker) en USDT par paire sur [E − 1 h, E), d'après `FLUX_GROS` (seuil de
    gros ordre du collecteur, 3 au plus par paire et par minute : les suivants ne sont pas attribués à un côté, minimum).
    Couverture lue sur `FLUX_MINUTE` (une entrée par minute) ; NaN pour une fenêtre touchée par un trou."""
    wanted = set(symbols)
    minutes = known_minutes(minute_entries)
    per: dict[str, dict[datetime, float]] = {s: {} for s in wanted}
    delay = (MINUTE + LAG_MINUTE).to_pytimedelta()
    for entry in large_entries:
        data = entry["data"]
        symbol = data.get("symbol")
        if symbol not in wanted:
            continue
        try:
            minute = _when(data["time"]).replace(second=0, microsecond=0)
            notional = float(data["notional_usdt"])
        except (KeyError, ValueError, TypeError):
            continue
        if _when(entry["at"]) > minute + delay:
            continue
        signed = notional if data.get("taker_side") == "BUY" else -notional if data.get("taker_side") == "SELL" else 0.0
        per[symbol][minute] = per[symbol].get(minute, 0.0) + signed
    ok = silence_ok(minutes.keys(), ends, window=window)
    values = _window_sums(per, ends, window)
    values.loc[~ok] = np.nan
    return values, ok


def carnet_values(entries: Iterable[dict], symbols: Iterable[str], ends: pd.DatetimeIndex) -> dict[str, pd.DataFrame]:
    """F26, F27 : moyenne horaire de `imb_1` et de `bid_1` (`CARNET_RESUME`, tous les échantillons de 5 s de l'heure) par
    paire, rangée à la FIN de l'heure. NaN si le résumé manque, a été écrit plus de 5 min après la fin de l'heure, n'a
    pas la grandeur, ou compte moins de 50/60 du nombre « plein » d'échantillons de la paire (plus de 10 min sans carnet) :
    le nombre plein est le plus grand des résumés de la même paire sur les 24 heures PRÉCÉDENTES (720 à défaut). La
    cadence réelle varie (≈ 713 par heure le 2026-10-09, ≈ 600 à 645 le 2026-10-10) : un seuil fixe écarterait des heures
    pleines."""
    wanted = sorted(set(symbols))
    imb = pd.DataFrame(np.nan, index=ends, columns=wanted, dtype=float)
    bid = pd.DataFrame(np.nan, index=ends, columns=wanted, dtype=float)
    by_symbol: dict[str, dict[pd.Timestamp, dict]] = {}
    for entry in entries:
        data = entry["data"]
        symbol = data.get("symbol")
        if symbol not in imb.columns:
            continue
        try:
            end = pd.Timestamp(data["hour"]) + HOUR
        except (KeyError, ValueError, TypeError):
            continue
        if pd.Timestamp(entry["at"]) > end + LAG_RESUME:
            continue
        by_symbol.setdefault(symbol, {}).setdefault(end, data)
    for symbol, hours in by_symbol.items():
        known = sorted(hours)
        for i, end in enumerate(known):
            if end not in imb.index:
                continue
            data = hours[end]
            previous = [int(hours[k].get("samples") or 0) for k in known[max(0, i - 24):i] if end - k <= DAY]
            nominal = max(previous) if previous else CARNET_NOMINAL
            if int(data.get("samples") or 0) < CARNET_MIN_SHARE * nominal:
                continue
            stats = data.get("stats") or {}
            for frame, key in ((imb, "imb_1"), (bid, "bid_1")):
                value = (stats.get(key) or {}).get("mean")
                if value is not None:
                    frame.loc[end, symbol] = float(value)
    return {"imb_1": imb, "bid_1": bid}


def options_values(entries: Iterable[dict], slots: pd.DatetimeIndex, *, currency: str = "BTC") -> pd.DataFrame:
    """F29 : asymétrie 25-delta (put − call, points de volatilité) et DVOL de BTC par créneau de 15 min (`OPTIONS_15M`,
    créneau = début du quart d'heure du relevé). NaN si le relevé manque ou a été écrit plus de 10 min après son
    créneau ; premier relevé du créneau seulement."""
    out = pd.DataFrame(np.nan, index=slots, columns=["skew", "dvol"], dtype=float)
    seen: set[pd.Timestamp] = set()
    for entry in entries:
        data = entry["data"]
        if data.get("currency") != currency:
            continue
        try:
            slot = pd.Timestamp(data["time"]).floor("15min")
        except (KeyError, ValueError, TypeError):
            continue
        if slot not in out.index or slot in seen:
            continue
        seen.add(slot)
        if pd.Timestamp(entry["at"]) > slot + LAG_SNAPSHOT:
            continue
        skew = (data.get("skew_25d") or {}).get("skew_pts")
        dvol = (data.get("dvol") or {}).get("value")
        out.loc[slot, "skew"] = float(skew) if skew is not None else np.nan
        out.loc[slot, "dvol"] = float(dvol) if dvol is not None else np.nan
    return out


def trending_presence(entries: Iterable[dict], symbols: Iterable[str], hours: pd.DatetimeIndex) -> pd.DataFrame:
    """F30 : présence (1/0) de chaque paire de l'univers parmi les pièces « trending » de CoinGecko, par heure
    (`ATTENTION_TRENDING_H` ; symbole CoinGecko + « USDT » = paire). Ligne NaN si le relevé de l'heure manque ou a été
    écrit plus de 10 min après son heure."""
    wanted = sorted(set(symbols))
    out = pd.DataFrame(np.nan, index=hours, columns=wanted, dtype=float)
    seen: set[pd.Timestamp] = set()
    for entry in entries:
        data = entry["data"]
        try:
            hour = pd.Timestamp(data["hour"])
        except (KeyError, ValueError, TypeError):
            continue
        if hour not in out.index or hour in seen:
            continue
        seen.add(hour)
        if pd.Timestamp(entry["at"]) > hour + LAG_SNAPSHOT or not isinstance(data.get("coins"), list):
            continue
        present = {f"{str(c).upper()}USDT" for c in data["coins"]}
        out.loc[hour] = [1.0 if s in present else 0.0 for s in wanted]
    return out


# --- Seuils et déclenchements (fonctions pures) -----------------------------------------------------------------------------

def thresholds(values: pd.DataFrame, *, window: int, min_periods: int, quantile: float | None = None,
               median: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Seuil causal de chaque fenêtre : quantile (interpolation linéaire) ou médiane des `window` fenêtres de grille
    STRICTEMENT antérieures de la même colonne, NaN sous `min_periods` fenêtres évaluables ; et leur nombre."""
    past = values.shift(1)
    roll = past.rolling(window, min_periods=min_periods)
    thr = roll.median() if median else roll.quantile(quantile, interpolation="linear")
    count = past.notna().astype(float).rolling(window, min_periods=1).sum()
    return thr, count


def triggers(spec: Spec, values: pd.DataFrame) -> pd.DataFrame:
    """Déclenchements de la règle sur toutes les fenêtres de `values` (grille régulière, NaN = non évaluable) : table
    longue `end, symbol, value, threshold, reference_n, measure, evaluable, reference_ok, trigger`. Pour F29, `values` a
    les colonnes `skew` et `dvol` ; pour F30, une présence 1/0 par paire."""
    if spec.kind == FEAR:
        return _fear_triggers(spec, values)
    if spec.kind == TREND:
        prev = values.shift(1)
        evaluable = values.notna() & prev.notna()
        trig = evaluable & (values == 1.0) & (prev == 0.0)
        return _long(values, values.where(evaluable), prev, evaluable.astype(float), evaluable, evaluable, trig, "ENTREE")
    if spec.kind == BID_DROP:
        thr, count = thresholds(values, window=spec.window, min_periods=spec.min_periods, median=True)
        trig = values < spec.ratio * thr
    else:
        thr, count = thresholds(values, window=spec.window, min_periods=spec.min_periods, quantile=spec.quantile)
        high = values > thr
        if spec.kind == IMB and spec.consecutive > 1:
            trig = high.astype(int).rolling(spec.consecutive, min_periods=spec.consecutive).sum() == spec.consecutive
        else:
            trig = high
        if spec.kind == LIQ:
            trig = trig & (values >= spec.floor)
        if spec.kind == WHALE:
            trig = trig & (values > 0)
    evaluable = values.notna()
    reference_ok = thr.notna()
    if spec.kind == IMB and spec.consecutive > 1:
        evaluable = evaluable.astype(int).rolling(spec.consecutive, min_periods=spec.consecutive).sum() == spec.consecutive
        reference_ok = reference_ok.astype(int).rolling(spec.consecutive, min_periods=spec.consecutive).sum() == spec.consecutive
    trig = trig.fillna(False).astype(bool) & evaluable & reference_ok
    return _long(values, values, thr, count, evaluable, reference_ok, trig, spec.kind)


ROW_COLUMNS = ["end", "symbol", "value", "threshold", "reference_n", "measure", "evaluable", "reference_ok", "trigger"]


def _empty_rows() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype=bool if c in ("evaluable", "reference_ok", "trigger") else object)
                         for c in ROW_COLUMNS})


def _long(values, shown, thr, count, evaluable, reference_ok, trig, measure: str) -> pd.DataFrame:
    if values.empty or len(values.columns) == 0:
        return _empty_rows()
    frame = pd.DataFrame({
        "value": shown.stack(future_stack=True), "threshold": thr.stack(future_stack=True),
        "reference_n": count.stack(future_stack=True), "evaluable": evaluable.stack(future_stack=True),
        "reference_ok": reference_ok.stack(future_stack=True), "trigger": trig.stack(future_stack=True)})
    frame.index.names = ["end", "symbol"]
    frame = frame.reset_index()
    frame["measure"] = measure
    frame[["evaluable", "reference_ok", "trigger"]] = frame[["evaluable", "reference_ok", "trigger"]].fillna(False).astype(bool)
    return frame


def _fear_triggers(spec: Spec, values: pd.DataFrame) -> pd.DataFrame:
    """F29 : asymétrie au-dessus de son quantile 0,95 des 30 jours précédents ; REPLI déclaré sur le DVOL (même règle)
    quand l'asymétrie du créneau manque ou que sa référence est insuffisante."""
    skew, dvol = values[["skew"]], values[["dvol"]]
    thr_s, n_s = thresholds(skew, window=spec.window, min_periods=spec.min_periods, quantile=spec.quantile)
    thr_d, n_d = thresholds(dvol, window=spec.window, min_periods=spec.min_periods, quantile=spec.quantile)
    s = skew["skew"].to_numpy(float)
    ts_, ns = thr_s["skew"].to_numpy(float), n_s["skew"].to_numpy(float)
    d = dvol["dvol"].to_numpy(float)
    td, nd = thr_d["dvol"].to_numpy(float), n_d["dvol"].to_numpy(float)
    has_s, has_d = np.isfinite(s), np.isfinite(d)
    use_skew = has_s & np.isfinite(ts_)
    use_dvol = ~use_skew & has_d & np.isfinite(td)
    with np.errstate(invalid="ignore"):
        trigger = (use_skew & (s > ts_)) | (use_dvol & (d > td))
    return pd.DataFrame({
        "end": values.index, "symbol": BTC,
        "value": np.where(use_skew, s, np.where(use_dvol, d, np.where(has_s, s, d))),
        "threshold": np.where(use_skew, ts_, np.where(use_dvol, td, np.nan)),
        "reference_n": np.where(use_skew | (has_s & ~use_dvol), ns, nd),
        "measure": np.where(use_skew, "ASYMETRIE_25D", np.where(use_dvol, "DVOL_REPLI", "AUCUNE")),
        "evaluable": has_s | has_d, "reference_ok": use_skew | use_dvol, "trigger": trigger})


def select_events(rows: pd.DataFrame, *, start_at: pd.Timestamp, final_at: pd.Timestamp,
                  last_by_symbol: Mapping[str, pd.Timestamp]) -> list[dict]:
    """Déclenchements retenus comme événements : fin de fenêtre ≥ démarrage + 7 jours (rodage) et < fin du recueil ; un
    seul par paire et par 24 h (le suivant ≥ 24 h après le précédent RETENU, y compris ceux déjà inscrits)."""
    last = dict(last_by_symbol)
    out = []
    fired = rows[rows["trigger"]].sort_values(["end", "symbol"])
    for row in fired.itertuples(index=False):
        end = pd.Timestamp(row.end)
        if end < start_at + RODAGE or end >= final_at:
            continue
        previous = last.get(row.symbol)
        if previous is not None and end - previous < DEDUP:
            continue
        last[row.symbol] = end
        out.append({"end": end, "symbol": row.symbol, "value": _num(row.value), "threshold": _num(row.threshold),
                    "reference_n": int(row.reference_n) if pd.notna(row.reference_n) else None, "measure": row.measure})
    return out


def _num(value) -> float | None:
    return round(float(value), 6) if value is not None and pd.notna(value) else None


def entry_time(known_at: pd.Timestamp) -> pd.Timestamp:
    """Première clôture 15 min Binance Spot STRICTEMENT après l'instant où l'événement est connu."""
    return pd.Timestamp(known_at).floor("15min") + STEP


def event_id(test_id: str, symbol: str, end: pd.Timestamp) -> str:
    return hashlib.sha256(f"{test_id}:{symbol}:{utc_iso(end)}".encode()).hexdigest()[:16]


def placebo_entries(short: str, ident: str, entry_at: pd.Timestamp) -> list[pd.Timestamp]:
    """20 clôtures 15 min distinctes tirées dans [entrée + 1 h ; entrée + 7 j] (après l'événement seulement), graine
    sha256("<F2x>:" + identifiant de l'événement)."""
    rng = random.Random(int(hashlib.sha256(f"{short}:{ident}".encode()).hexdigest()[:16], 16))
    slots = range(int(PLACEBO_MIN / STEP), int(PLACEBO_MAX / STEP) + 1)
    return [pd.Timestamp(entry_at) + STEP * k for k in sorted(rng.sample(list(slots), PLACEBOS))]


# --- Mesure (fonctions pures) -----------------------------------------------------------------------------------------------

def net_return(entry: float, exit_: float, symbol: str, scenario: str) -> float:
    """Rendement net d'un achat puis d'une vente au marché (taker) : frais par ordre + écart et glissement par côté."""
    costs = costs_for(symbol, scenario)
    return exit_ * (1 - costs.market) * (1 - costs.fee) / (entry * (1 + costs.market) * (1 + costs.fee)) - 1


def returns_from(closes: Mapping[pd.Timestamp, float], start: pd.Timestamp, symbol: str) -> dict[str, dict | None]:
    """Rendements brut et nets d'une entrée à la clôture `start`, par horizon (None si une clôture manque)."""
    entry = closes.get(start)
    out: dict[str, dict | None] = {}
    for name, horizon in HORIZONS.items():
        exit_ = closes.get(start + horizon)
        if entry is None or exit_ is None or not entry > 0 or not exit_ > 0:
            out[name] = None
            continue
        out[name] = {GROSS: round(exit_ / entry - 1, 8),
                     **{s: round(net_return(entry, exit_, symbol, s), 8) for s in SCENARIOS}}
    return out


def measure(closes: Mapping[pd.Timestamp, float], *, symbol: str, entry_at: pd.Timestamp,
            placebos: list[pd.Timestamp]) -> dict | None:
    """Événement et placebos sur les clôtures 15 min ; None si le rendement à 24 h de l'événement n'est pas mesurable."""
    event = returns_from(closes, pd.Timestamp(entry_at), symbol)
    if event[PRIMARY] is None:
        return None
    rows = [returns_from(closes, pd.Timestamp(p), symbol) for p in placebos]
    placebo: dict[str, dict] = {}
    excess: dict[str, dict] = {}
    for name in HORIZONS:
        usable: list[dict] = [x for r in rows if (x := r[name]) is not None]
        placebo[name] = {"n": len(usable), **{m: (round(float(np.mean([u[m] for u in usable])), 8) if usable else None)
                                              for m in MEASURES}}
        own = event[name]
        excess[name] = {m: (round(own[m] - placebo[name][m], 8) if own is not None and placebo[name][m] is not None else None)
                        for m in MEASURES}
    return {"event": event, "placebo_mean": placebo, "excess": excess}


def needed_closes(entry_at: pd.Timestamp, placebos: list[pd.Timestamp]) -> list[pd.Timestamp]:
    starts = [pd.Timestamp(entry_at), *map(pd.Timestamp, placebos)]
    return sorted({s + h for s in starts for h in (pd.Timedelta(0), *HORIZONS.values())})


def fetch_closes(rest, symbol: str, first: pd.Timestamp, last: pd.Timestamp) -> dict[pd.Timestamp, float]:
    """Clôtures des bougies 15 min de Binance Spot (REST public, `/api/v3/klines`) dont la clôture est dans [first,
    last] ; clé = instant de clôture (ouverture + 15 min). Une seule demande (961 bougies au plus)."""
    rows = rest.get_json(KLINES_PATH, {"symbol": symbol, "interval": "15m",
                                       "startTime": int((first - STEP).timestamp() * 1000),
                                       "endTime": int((last - STEP).timestamp() * 1000), "limit": KLINES_LIMIT})
    out: dict[pd.Timestamp, float] = {}
    for k in rows or []:
        close_at = pd.Timestamp(int(k[0]), unit="ms", tz="UTC") + STEP
        if first <= close_at <= last:
            out[close_at] = float(k[4])
    return out


# --- Verdict -----------------------------------------------------------------------------------------------------------------

def summarize(rows: list[dict], *, samples: int = SAMPLES, seed: int = SEED) -> dict:
    """Par mesure (brut, central, défavorable) et horizon : n, jours distincts, moyenne, IC95 et intervalle de décision
    (1 − 0,05/6, blocs de 7 jours avec événements, au moins 8 blocs), part positive, moyenne et excès des placebos."""
    out: dict[str, dict] = {}
    for m in MEASURES:
        out[m] = {}
        for name in HORIZONS:
            usable = [r for r in rows if r["results"]["event"].get(name) is not None]
            if not usable:
                out[m][name] = {"n": 0, "days": 0}
                continue
            usable.sort(key=lambda r: r["entry_at"])
            values = np.array([r["results"]["event"][name][m] for r in usable], float)
            times = pd.to_datetime([r["entry_at"] for r in usable], utc=True).to_numpy()
            ci95, _ = day_block_ci95(values, times, block_days=BLOCK_DAYS, samples=samples, seed=seed, min_blocks=MIN_BLOCKS)
            ci, blocks = day_block_ci(values, times, block_days=BLOCK_DAYS, samples=samples, seed=seed, level=LEVEL,
                                      min_blocks=MIN_BLOCKS)
            placebo = [r["results"]["placebo_mean"][name][m] for r in usable if r["results"]["placebo_mean"][name][m] is not None]
            excess = [r["results"]["excess"][name][m] for r in usable if r["results"]["excess"][name][m] is not None]
            out[m][name] = {"n": int(len(values)), "days": int(pd.DatetimeIndex(times).floor("D").nunique()), "blocks": int(blocks),
                            "mean": round(float(values.mean()), 6), "ci95": ci95, "ci_decision": ci,
                            "positive_share": round(float((values > 0).mean()), 4),
                            "placebo_mean": round(float(np.mean(placebo)), 6) if placebo else None,
                            "excess_mean": round(float(np.mean(excess)), 6) if excess else None}
    return out


def verdict(measures: dict, *, sign: int, ended: bool) -> str:
    """Seuil de décision (rendement à 24 h SEUL ; placebos descriptifs) : `INSUFFISANT` sous 30 événements résolus, sous
    50 jours distincts ou sans intervalle calculable ; hypothèse positive : `SUPERIEUR_A_ZERO` si l'intervalle
    1 − 0,05/6 du rendement NET est > 0 en central ET en défavorable ; hypothèse négative : `INFERIEUR_A_ZERO` si
    l'intervalle 1 − 0,05/6 du rendement BRUT est < 0 (le net ferait passer le test par les seuls frais) ; sinon
    `NON_DEMONTRE`."""
    if not ended:
        return RUNNING
    central = measures.get(CENTRAL, {}).get(PRIMARY, {})
    if central.get("n", 0) < MIN_EVENTS or central.get("days", 0) < MIN_DAYS:
        return INSUFFICIENT
    if sign > 0:
        adverse = measures.get(ADVERSE, {}).get(PRIMARY, {})
        if central.get("ci_decision") is None or adverse.get("ci_decision") is None:
            return INSUFFICIENT
        return ABOVE if central["ci_decision"][0] > 0 and adverse["ci_decision"][0] > 0 else NOT_SHOWN
    gross = measures.get(GROSS, {}).get(PRIMARY, {})
    if gross.get("ci_decision") is None:
        return INSUFFICIENT
    return BELOW if gross["ci_decision"][1] < 0 else NOT_SHOWN


# --- Univers ------------------------------------------------------------------------------------------------------------------

def universe(spec: Spec, settings: Settings, start: dict) -> tuple[list[str], str]:
    """Paires du test : liste halal figée au DEMARRAGE de F15 (repli : celle figée au démarrage du test) ; paires de la
    configuration (`data.symbols`) admises par la liste figée du test ; ou BTCUSDT s'il y est admis."""
    halal = list(start["halal"]["symbols"])
    if spec.universe == "BTC":
        return ([BTC] if BTC in halal else []), "BTC"
    if spec.universe == "CONFIG":
        allowed = set(halal)
        return [s for s in settings.data.symbols if s in allowed], "data.symbols ∩ liste halal figée du test"
    from .registry import START, journal_for
    first = journal_for(settings, F15_ID).first(START)
    if first is not None:
        return list(first["data"]["halal"]["symbols"]), F15_ID
    return halal, spec.test_id


# --- Détection sur les journaux --------------------------------------------------------------------------------------------

def detect(spec: Spec, root: Path, *, ends: pd.DatetimeIndex, symbols: list[str], now: pd.Timestamp) -> pd.DataFrame:
    """Évaluation causale des fenêtres `ends` (table longue de `triggers`, restreinte à `ends`) à partir des journaux du
    collecteur écrits au plus tard à `now`. La grille couvre aussi la référence (7 jours, 24 h ou 30 jours avant)."""
    if len(ends) == 0 or not symbols:
        return _empty_rows()
    first, last = ends.min(), ends.max()
    grid = pd.date_range(first - spec.reference - spec.step * spec.consecutive, last, freq=spec.step)
    since = grid[0] - HOUR - READ_MARGIN
    until = min(pd.Timestamp(now), last + spec.lag)
    if spec.kind == LIQ:
        from ..collect import liquidations
        entries = read_entries(root, LIQUIDATIONS, {LIQ_MINUTE}, since=since, until=until)
        values, _ = liq_values(entries, symbols, grid, fields=list(liquidations.FIELDS))
    elif spec.kind == WHALE:
        minutes = read_entries(root, FLUX, {FLUX_MINUTE}, since=since, until=until)
        large = read_entries(root, FLUX, {FLUX_LARGE}, since=since, until=until)
        values, _ = whale_values(minutes, large, symbols, grid)
    elif spec.kind in (IMB, BID_DROP):
        entries = read_entries(root, CARNET, {CARNET_RESUME}, since=since, until=until)
        values = carnet_values(entries, symbols, grid)["imb_1" if spec.kind == IMB else "bid_1"]
    elif spec.kind == FEAR:
        entries = read_entries(root, OPTIONS, {OPTIONS_15M}, since=since, until=until)
        values = options_values(entries, grid)
    else:
        entries = read_entries(root, ATTENTION, {TRENDING_H}, since=since, until=until)
        values = trending_presence(entries, symbols, grid)
    rows = triggers(spec, values)
    return rows[rows["end"].isin(ends)].reset_index(drop=True)


def counts_by_end(rows: pd.DataFrame, ends: pd.DatetimeIndex) -> pd.DataFrame:
    """Par fin de fenêtre : fenêtres paire × fin, évaluables (collecteur sans trou et référence suffisante), touchées par
    un trou, à référence insuffisante, déclenchements."""
    if rows.empty:
        return pd.DataFrame({k: 0 for k in ("windows", "evaluable", "trou", "reference", "triggers")}, index=ends)
    flags = pd.DataFrame({"end": rows["end"], "windows": 1,
                          "evaluable": rows["evaluable"] & rows["reference_ok"], "trou": ~rows["evaluable"],
                          "reference": rows["evaluable"] & ~rows["reference_ok"], "triggers": rows["trigger"]})
    grouped = flags.groupby("end").sum(numeric_only=False).astype(int)
    return grouped.reindex(ends, fill_value=0)


# --- Journal du test ----------------------------------------------------------------------------------------------------------

def _first_by(journal: Journal, kind: str, field: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in journal.entries({kind}):
        out.setdefault(entry["data"][field], entry["data"])
    return out


def events(journal: Journal) -> dict[str, dict]:
    return _first_by(journal, EVENT, "event_id")


def resolutions(journal: Journal) -> dict[str, dict]:
    return _first_by(journal, RESOLUTION, "event_id")


def last_control(journal: Journal) -> dict | None:
    last = None
    for entry in journal.entries({CONTROL}):
        last = entry["data"]
    return last


def record_decisions(spec: Spec, settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    """Évalue, heure par heure, les fenêtres finies depuis le dernier CONTROLE (72 h au plus par passage) dont la fin +
    le délai de connaissance est passée ; inscrit les EVENEMENT (identifiant déjà présent : passé) puis un CONTROLE par
    heure (comptes de fenêtres évaluables, trous, références insuffisantes, déclenchements, événements)."""
    moment = pd.Timestamp(now)
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    previous = last_control(journal)
    done = pd.Timestamp(previous["until"]) if previous else started.floor("h")
    target = min((moment - spec.lag).floor("h"), final.ceil("h"), done + HOUR * MAX_HOURS_PER_PASS)
    if target <= done:
        return {"hours": 0, "events": 0}
    symbols, source = universe(spec, settings, start)
    ends = pd.date_range(done + spec.step, target, freq=spec.step)
    rows = detect(spec, settings.root, ends=ends, symbols=symbols, now=moment)
    known = events(journal)
    last_by_symbol: dict[str, pd.Timestamp] = {}
    for e in known.values():
        end = pd.Timestamp(e["window_end"])
        if e["symbol"] not in last_by_symbol or end > last_by_symbol[e["symbol"]]:
            last_by_symbol[e["symbol"]] = end
    chosen = select_events(rows, start_at=started, final_at=final, last_by_symbol=last_by_symbol)
    written = 0
    by_hour: dict[pd.Timestamp, list[str]] = {}
    for item in chosen:
        ident = event_id(spec.test_id, item["symbol"], item["end"])
        by_hour.setdefault(item["end"].ceil("h"), []).append(ident)
        if ident in known:
            continue
        known_at = item["end"] + spec.lag
        entry = entry_time(known_at)
        placebos = placebo_entries(spec.short, ident, entry)
        journal.append(EVENT, {"event_id": ident, "test_id": spec.test_id, "symbol": item["symbol"],
                               "window_end": utc_iso(item["end"]), "known_at": utc_iso(known_at), "entry_at": utc_iso(entry),
                               "value": item["value"], "threshold": item["threshold"], "reference_n": item["reference_n"],
                               "measure": item["measure"], "placebo_entries": [utc_iso(p) for p in placebos],
                               "placebo_seed": f"sha256({spec.short}:event_id)", "detected_at": utc_iso(moment),
                               "detected_after_h": round((moment - known_at) / HOUR, 3), "universe_source": source},
                       now=moment)
        written += 1
    counts = counts_by_end(rows, ends)
    hours = pd.date_range(done + HOUR, target, freq="h")
    for hour in hours:
        part = counts[(counts.index > hour - HOUR) & (counts.index <= hour)]
        journal.append(CONTROL, {"until": utc_iso(hour), "windows_end": [utc_iso(part.index.min()), utc_iso(part.index.max())],
                                 "pairs": len(symbols), "universe_source": source,
                                 "rodage": bool(hour < started + RODAGE), "after_final": bool(hour > final),
                                 **{k: int(part[k].sum()) for k in ("windows", "evaluable", "trou", "reference", "triggers")},
                                 "events": by_hour.get(hour, [])}, now=moment)
    return {"hours": len(hours), "events": written}


def resolve(spec: Spec, settings: Settings, journal: Journal, *, now: datetime, rest=None,
            fetch: Callable | None = None) -> dict:
    """Mesure chaque événement une fois l'entrée + 7 j + 72 h passée (dernier placebo à 72 h), sur les bougies 15 min
    publiques de Binance Spot ; `TROU` si le rendement à 24 h de l'événement n'est toujours pas mesurable 2 jours plus
    tard (paire retirée de la cote, source muette)."""
    moment = pd.Timestamp(now)
    done = resolutions(journal)
    due = [e for k, e in events(journal).items() if k not in done and moment >= pd.Timestamp(e["entry_at"]) + RESOLVE_AFTER]
    if not due:
        return {}
    client = rest
    owned = False
    if fetch is None and client is None:
        from ..data.http import PublicHttpClient
        client, owned = PublicHttpClient.rest(settings.data.rest_base_url), True
    get = fetch or (lambda symbol, first, last: fetch_closes(client, symbol, first, last))
    counts: dict[str, int] = {}
    try:
        for event in due:
            entry_at = pd.Timestamp(event["entry_at"])
            placebos = [pd.Timestamp(p) for p in event["placebo_entries"]]
            wanted = needed_closes(entry_at, placebos)
            late = moment >= entry_at + RESOLVE_AFTER + GAP_AFTER
            try:
                closes = get(event["symbol"], wanted[0], wanted[-1])
                error = None
            except Exception as exc:  # noqa: BLE001 - source muette ou paire retirée : nouvel essai, trou constaté plus tard
                closes, error = {}, f"{type(exc).__name__}: {exc}"[:200]
            result = measure(closes, symbol=event["symbol"], entry_at=entry_at, placebos=placebos)
            if result is None and not late:
                continue
            used = {utc_iso(t): closes[t] for t in wanted if t in closes}
            data = {"event_id": event["event_id"], "symbol": event["symbol"], "entry_at": event["entry_at"],
                    "status": RESOLVED if result is not None else GAP, "results": result, "closes": used,
                    "closes_missing": len(wanted) - len(used), "error": error,
                    "closes_sha256": hashlib.sha256(json.dumps(used, sort_keys=True).encode()).hexdigest()}
            journal.append(RESOLUTION, data, now=moment)
            counts[data["status"]] = counts.get(data["status"], 0) + 1
    finally:
        if owned and client is not None:
            client.close()
    return counts


def stats(spec: Spec, journal: Journal, start: dict, *, now: datetime, samples: int = SAMPLES) -> dict:
    moment = pd.Timestamp(now)
    started, final = pd.Timestamp(start["started_at"]), pd.Timestamp(start["final_at"])
    controls = [e["data"] for e in journal.entries({CONTROL})]
    found = events(journal)
    done = resolutions(journal)
    rows = [r for k, r in done.items() if k in found and r["status"] == RESOLVED and r.get("results")]
    totals = {k: sum(int(c.get(k, 0)) for c in controls) for k in ("windows", "evaluable", "trou", "reference", "triggers")}
    pending = sum(1 for k in found if k not in done)
    covered = bool(controls) and pd.Timestamp(controls[-1]["until"]) >= final
    ended = moment >= final and covered and pending == 0
    measures = summarize(rows, samples=samples)
    by_symbol: dict[str, int] = {}
    for e in found.values():
        by_symbol[e["symbol"]] = by_symbol.get(e["symbol"], 0) + 1
    last = sorted(found.values(), key=lambda e: e["window_end"])[-5:]
    span = max(1.0, (min(moment, final) - (started + RODAGE)) / DAY)
    return {"collecte_events": True, "test_id": spec.test_id, "kind": spec.kind, "source": SOURCE_OF[spec.kind], "sign": spec.sign,
            "hypothesis": "positive (net > 0)" if spec.sign > 0 else "négative (brut < 0)",
            "rodage_until": utc_iso(started + RODAGE), "in_rodage": bool(moment < started + RODAGE),
            "controls": len(controls), "covered_until": controls[-1]["until"] if controls else None, **totals,
            "events": len(found), "events_per_month": round(len(found) / span * 30.44, 1) if moment > started + RODAGE else None,
            "by_symbol": dict(sorted(by_symbol.items(), key=lambda kv: -kv[1])), "decisions": len(found),
            "resolved": len(rows), "gaps": sum(1 for r in done.values() if r["status"] == GAP), "pending": pending,
            "last_events": [{k: e.get(k) for k in ("symbol", "window_end", "entry_at", "value", "threshold", "measure")}
                            for e in reversed(last)],
            "measures": measures, "ended": bool(ended), "verdict": verdict(measures, sign=spec.sign, ended=ended)}


def finalize(spec: Spec, journal: Journal, start: dict, *, now: datetime) -> str | None:
    from .registry import CLOSURE, VERDICT
    if journal.first(CLOSURE) is not None:
        return None
    result = stats(spec, journal, start, now=now)
    if not result["ended"]:
        return None
    if journal.first(VERDICT) is None:
        journal.append(VERDICT, {"verdict": result["verdict"], "measures": result["measures"], "events": result["events"],
                                 "resolved": result["resolved"], "gaps": result["gaps"]}, now=now)
        return VERDICT
    journal.append(CLOSURE, {"pending": 0}, now=now)
    return CLOSURE


def summary(spec: Spec, settings: Settings, *, now: datetime) -> dict:
    """Ligne de la carte « Événements de marché (collecteur) » (`GET /evenements`) : état, rodage, comptes, derniers
    événements, verdict inscrit ou `EN_COURS`. Lecture seule du journal du test."""
    from .registry import NOT_STARTED, journal_for, status
    test = make_test(spec.test_id)
    state = status(settings, test, now=now)
    row: dict = {"test_id": spec.test_id, "kind": spec.kind, "title": test.title, "state": state["state"],
                 "hypothesis": "positive" if spec.sign > 0 else "négative"}
    if state["state"] == NOT_STARTED:
        return row
    result = stats(spec, journal_for(settings, spec.test_id), state["start"], now=now, samples=1000)
    keep = ("rodage_until", "in_rodage", "covered_until", "windows", "evaluable", "trou", "reference", "events",
            "events_per_month", "resolved", "gaps", "pending", "last_events")
    row |= {k: result[k] for k in keep}
    row["started_at"], row["final_at"] = state["start"]["started_at"], state["start"]["final_at"]
    row["verdict"] = state["verdict"]["verdict"] if "verdict" in state else result["verdict"]
    return row


# --- Définition des tests ---------------------------------------------------------------------------------------------------

TITLES = {
    "F25_LIQ_CASCADE": "Cascade de liquidations de positions longues (1 h) sur une paire, achat Spot ensuite",
    "F26_MUR_ACHETEURS": "Mur d'acheteurs : déséquilibre du carnet à ±1 % élevé deux heures de suite, achat ensuite",
    "F27_RETRAIT_LIQUIDITE": "Retrait de la liquidité acheteuse à ±1 % (veto attendu : rendement suivant négatif)",
    "F28_BALEINES": "Baleines : solde des gros achats au marché sur 1 h au plus haut de 7 jours, achat ensuite",
    "F29_PEUR_OPTIONS": "Peur sur les options : asymétrie 25-delta de BTC au plus haut de 30 jours, achat de BTCUSDT",
    "F30_TRENDING": "Entrée dans les « trending » de CoinGecko (euphorie, veto attendu : rendement suivant négatif)",
}
HYPOTHESES = {
    "F25_LIQ_CASCADE": ("Après une heure où les liquidations de positions longues d'une paire (marché à terme USDⓈ-M) dépassent "
                        "le quantile 0,99 de ses 7 jours précédents et 250 000 USDT, un achat Spot à la clôture 15 min suivante "
                        "rapporte en moyenne un rendement NET > 0 à 24 h (central et défavorable). Attendu : NON_DEMONTRE ou "
                        "INSUFFISANT."),
    "F26_MUR_ACHETEURS": ("Après deux heures consécutives où le déséquilibre moyen du carnet à ±1 % (achats − ventes) d'une paire "
                          "dépasse le quantile 0,95 de ses 7 jours précédents, un achat Spot à la clôture 15 min suivante "
                          "rapporte en moyenne un rendement NET > 0 à 24 h. Attendu : NON_DEMONTRE ou INSUFFISANT."),
    "F27_RETRAIT_LIQUIDITE": ("Après une heure où la profondeur acheteuse moyenne à −1 % d'une paire tombe sous la moitié de sa "
                              "médiane des 24 h précédentes, le rendement BRUT à 24 h d'un achat à la clôture 15 min suivante "
                              "est en moyenne < 0 (veto). Attendu : NON_DEMONTRE ou INSUFFISANT."),
    "F28_BALEINES": ("Après une heure où le solde des gros ordres au marché (achats − ventes taker, seuil de gros ordre du "
                     "collecteur) d'une paire dépasse le quantile 0,99 de ses 7 jours précédents, un achat Spot à la clôture "
                     "15 min suivante rapporte en moyenne un rendement NET > 0 à 24 h. Attendu : NON_DEMONTRE ou INSUFFISANT."),
    "F29_PEUR_OPTIONS": ("Quand l'asymétrie 25-delta (put − call) des options BTC à ~30 jours dépasse le quantile 0,95 de ses 30 "
                         "jours précédents (repli déclaré : DVOL), un achat de BTCUSDT à la clôture 15 min suivante rapporte en "
                         "moyenne un rendement NET > 0 à 24 h. Attendu : INSUFFISANT (BTC seul, un événement par 24 h au plus)."),
    "F30_TRENDING": ("Quand une paire de l'univers entre dans les pièces « trending » de CoinGecko (absente l'heure précédente), "
                     "le rendement BRUT à 24 h d'un achat à la clôture 15 min suivante est en moyenne < 0 (euphorie, veto). "
                     "Attendu : NON_DEMONTRE."),
}
COLLECTOR_FUNCTIONS = {
    LIQ: (("crypto_signal_intelligence.collect.liquidations", "parse"),
          ("crypto_signal_intelligence.collect.liquidations", "MinuteAggregator"),
          ("crypto_signal_intelligence.collect.base", "minute_floor"), ("crypto_signal_intelligence.collect.base", "iso_ms")),
    IMB: (("crypto_signal_intelligence.collect.carnet", "book_sample"), ("crypto_signal_intelligence.collect.carnet", "PairTracker"),
          ("crypto_signal_intelligence.collect.carnet", "parse"), ("crypto_signal_intelligence.collect.base", "iso_ms")),
    WHALE: (("crypto_signal_intelligence.collect.flux", "parse"), ("crypto_signal_intelligence.collect.flux", "MinuteAggregator"),
            ("crypto_signal_intelligence.collect.flux", "large_usdt"), ("crypto_signal_intelligence.collect.flux", "imbalance"),
            ("crypto_signal_intelligence.collect.base", "minute_floor"), ("crypto_signal_intelligence.collect.base", "iso_ms")),
    FEAR: (("crypto_signal_intelligence.collect.options", "summarize"), ("crypto_signal_intelligence.collect.options", "bs_delta"),
           ("crypto_signal_intelligence.collect.options", "_norm_cdf"),
           ("crypto_signal_intelligence.collect.options", "parse_instrument")),
    TREND: (("crypto_signal_intelligence.collect.attention", "parse_trending"),),
}
COLLECTOR_FUNCTIONS[BID_DROP] = COLLECTOR_FUNCTIONS[IMB]


def collector_params(kind: str) -> dict:
    """Constantes du collecteur dont dépend la grandeur mesurée (entrent dans l'empreinte des PARAMÈTRES : les changer
    arrête le test). Les adresses des flux et le superviseur n'y sont pas : on peut les corriger sans arrêter le test."""
    from ..collect import attention, carnet, flux, liquidations, options
    if kind == LIQ:
        return {"kinds": [LIQ_MINUTE], "fields": liquidations.FIELDS, "top_pairs": liquidations.TOP_PAIRS,
                "flush_grace_ms": liquidations.FLUSH_GRACE_MS}
    if kind in (IMB, BID_DROP):
        return {"kinds": [CARNET_RESUME], "bands": list(carnet.BANDS), "sample_seconds": carnet.SAMPLE_SECONDS,
                "stale_seconds": carnet.STALE_SECONDS, "resume_fields": list(carnet.RESUME_FIELDS)}
    if kind == WHALE:
        return {"kinds": [FLUX_MINUTE, FLUX_LARGE], "fields": flux.FIELDS, "large_usdt": flux.LARGE_USDT,
                "large_usdt_by_pair": flux.LARGE_USDT_BY_PAIR, "max_large_per_minute": flux.MAX_LARGE_PER_MINUTE,
                "flush_grace_ms": flux.FLUSH_GRACE_MS}
    if kind == FEAR:
        return {"kinds": [OPTIONS_15M], "target_days": options.TARGET_DAYS, "delta_target": options.DELTA_TARGET,
                "every_minutes": int(options.EVERY / MINUTE), "offset_seconds": int(options.OFFSET.total_seconds())}
    return {"kinds": [TRENDING_H], "max_trending": attention.MAX_TRENDING}


def rule_objects(kind: str) -> tuple:
    if kind in (IMB, BID_DROP):
        from ..collect import carnet
        return (carnet.BookCollector.sample_all,)
    return ()


def make_test(test_id: str) -> ForwardTest:
    spec = SPECS[test_id]
    short = spec.short.lower()
    return ForwardTest(
        test_id=test_id, title=TITLES[test_id], hypothesis=HYPOTHESES[test_id],
        params={"rule": spec.describe(), "collector": collector_params(spec.kind),
                "lags_minutes": {"minute": int(LAG_MINUTE / MINUTE), "resume": int(LAG_RESUME / MINUTE),
                                 "snapshot": int(LAG_SNAPSHOT / MINUTE)},
                "max_silence_minutes": MAX_SILENCE_MINUTES, "carnet_min_share": round(CARNET_MIN_SHARE, 6),
                "carnet_nominal_default": CARNET_NOMINAL, "coverage": COVERAGE,
                "rodage_days": RODAGE.days, "dedup_hours": int(DEDUP / HOUR), "entry": "première clôture 15 min Binance Spot "
                "strictement après fin de fenêtre + délai", "placebos": PLACEBOS,
                "placebo_window_hours": [int(PLACEBO_MIN / HOUR), int(PLACEBO_MAX / HOUR)], "placebo_seed": f"sha256({spec.short}:event_id)",
                "horizons": list(HORIZONS), "primary": PRIMARY, "prices": "bougies 15 min Binance Spot, REST public /api/v3/klines",
                "costs": "forward/costs (taker aller-retour, central et défavorable)", "alpha": ALPHA, "family": FAMILY,
                "decision_level": LEVEL, "decision": ("rendement NET à 24 h, central ET défavorable" if spec.sign > 0
                                                      else "rendement BRUT à 24 h"),
                "min_events": MIN_EVENTS, "min_days": MIN_DAYS, "samples": SAMPLES, "seed": SEED, "block_days": BLOCK_DAYS,
                "min_blocks": MIN_BLOCKS, "gap_after_days": GAP_AFTER.days, "max_hours_per_pass": MAX_HOURS_PER_PASS},
        rule_objects=rule_objects(spec.kind),
        config_keys=("data.rest_base_url", "data.symbols") if spec.universe == "CONFIG" else ("data.rest_base_url",),
        frozen_modules=(f"crypto_signal_intelligence.forward.{short}", "crypto_signal_intelligence.forward.collecte_events",
                        "crypto_signal_intelligence.forward.costs", "crypto_signal_intelligence.forward.registry",
                        "crypto_signal_intelligence.forward.journal"),
        frozen_functions=(("crypto_signal_intelligence.backtest.metrics", "day_block_ci95"),
                          ("crypto_signal_intelligence.backtest.metrics", "day_block_ci"),
                          *COLLECTOR_FUNCTIONS[spec.kind]),
    )
