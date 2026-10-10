"""Gestion commune des positions « price action » et de leurs placebos (docs/PRICE_ACTION.md § 3), identique pour
l'étude historique, le contrôle sous H0 et le test en direct F19.

`assistant/rules.simulate` (F18) n'est pas réutilisée : elle fixe le stop de clôture aux clôtures 4 h et la durée à
10 jours, alors que les configurations journalières vérifient le stop à la clôture JOURNALIÈRE et tiennent 30 jours.
La règle est la même (vérifié par un test : pour une configuration 4 h, les deux fonctions donnent le même R).

Position achetée à la clôture `entry_at` au prix `entry` (frais et glissement taker du modèle commun
`forward/costs`), gérée sur les bougies 1 h qui suivent :
- stop de secours dur à entrée − 1,5 R, touché si un plus bas 1 h ≤ niveau : sortie au niveau (ou à l'ouverture si
  elle est déjà dessous) ; une bougie qui touche aussi un objectif compte le stop (prudence) ;
- TP1 : moitié à +1 R (plus haut 1 h ≥ TP1), puis stop de clôture ramené à l'entrée ; objectif : l'autre moitié ;
- stop de clôture : à chaque clôture de l'unité (4 h UTC, ou 00:00 UTC pour les configurations journalières), si la
  clôture ≤ stop de clôture, sortie à cette clôture ;
- durée maximale (10 jours en 4 h, 30 jours en journalier) : sortie à la clôture de la dernière bougie 1 h.
Toutes les sorties sont comptées au marché (taker). R = résultat net / (entrée − stop).

`complete=False` (direct) : il faut des bougies 1 h contiguës depuis l'entrée, sinon EN_COURS (on attend).
`complete=True` (historique, données définitives) : une heure absente est sautée (marché fermé) ; si les bougies de la
paire s'arrêtent avant l'échéance (paire retirée de la cote), sortie à la dernière clôture connue (FIN_DE_COTATION).
"""
from __future__ import annotations

import hashlib
import random

import numpy as np
import pandas as pd

from ..forward.costs import costs_for
from . import detect as D

HARD_STOP_FACTOR = 1.5
TP1_SHARE = 0.5
MAX_HOLD_DAYS = {"4h": 10, "1d": 30}
REST_HOURS = 48                     # discipline : 48 h de repos par paire et par configuration après la sortie
PLACEBOS = 20
PLACEBO_HOURS = (5, 84)             # configurations 4 h : clôtures 1 h dans [t − 84 h ; t + 84 h] hors ±4 h
PLACEBO_DAYS = (2, 15)              # configurations journalières : t ± 2…15 jours (même heure), hors ±1 jour
SEED_PREFIX = "PRICE_ACTION"

RESOLVED, RUNNING, GAP = "RESOLU", "EN_COURS", "TROU"
STOP_HARD, STOP_CLOSE, TARGET, TIME, DELISTED = "STOP_SECOURS", "STOP_CLOTURE", "OBJECTIF", "TEMPS", "FIN_DE_COTATION"


class Hourly:
    """Bougies 1 h en tableaux (ouverture en ns, o, h, l, c), pour des simulations rapides."""

    __slots__ = ("c", "h", "l", "o", "t")

    def __init__(self, t, o, h, l, c):  # noqa: E741 - l = plus bas
        self.t, self.o, self.h, self.l, self.c = t, o, h, l, c

    @classmethod
    def of(cls, bars) -> Hourly:
        """Depuis un `detect.Frame` 1 h, un `detect.Bars` ou un DataFrame (open_time, open, high, low, close)."""
        if isinstance(bars, Hourly):
            return bars
        if isinstance(bars, D.Bars):
            bars = bars.h1
        if isinstance(bars, D.Frame):
            return cls(bars.t, bars.o, bars.h, bars.l, bars.c)
        frame = bars.assign(quote_volume=bars["quote_volume"] if "quote_volume" in bars.columns else 0.0)
        f = D.hourly(frame)
        return cls(f.t, f.o, f.h, f.l, f.c)


def max_hold_ns(unit: str) -> int:
    return MAX_HOLD_DAYS[unit] * D.DAY_NS


def levels(entry: float, stop: float, objective: float) -> dict:
    """TP1 (+1 R) et stop de secours (−1,5 R) d'une position."""
    risk = entry - stop
    return {"tp1": entry + risk, "hard_stop": entry - HARD_STOP_FACTOR * risk, "risk": risk}


def simulate(bars, *, entry_at, entry: float, stop: float, objective: float, symbol: str, scenario: str, unit: str,
             complete: bool, tp1: float | None = None, hard_stop: float | None = None) -> dict:
    """Une position (règles du module). `entry_at` : clôture d'entrée (horodatage ou ns). `tp1` et `hard_stop` : niveaux
    arrondis (direct), sinon +1 R et −1,5 R exacts."""
    hours = Hourly.of(bars)
    start = int(entry_at) if isinstance(entry_at, int | np.integer) else int(D.to_ns([entry_at])[0])
    risk = entry - stop
    if not risk > 0:
        raise ValueError("stop au-dessus de l'entrée")
    tp1 = entry + risk if tp1 is None else tp1
    hard = entry - HARD_STOP_FACTOR * risk if hard_stop is None else hard_stop
    c = costs_for(symbol, scenario)
    cost_in = entry * (1 + c.market) * (1 + c.fee)

    def sell(price: float) -> float:
        return price * (1 - c.market) * (1 - c.fee)

    horizon = start + max_hold_ns(unit)
    lo, hi = np.searchsorted(hours.t, [start, horizon])
    t, o, h, low, cl = (a[lo:hi] for a in (hours.t, hours.o, hours.h, hours.l, hours.c))
    n = len(t)
    if not complete:
        expected = start + D.HOUR_NS * np.arange(n)
        breaks = np.flatnonzero(t != expected)
        n = int(breaks[0]) if len(breaks) else n
    step = D.H4_NS if unit == "4h" else D.DAY_NS
    unit_close = ((t[:n] + D.HOUR_NS) % step) == 0
    hard_hit = low[:n] <= hard
    tp1_hit = h[:n] >= tp1
    target_hit = h[:n] >= objective
    close_stop = unit_close & (cl[:n] <= stop)
    close_even = unit_close & (cl[:n] <= entry)

    def first(mask: np.ndarray, frm: int = 0) -> int:
        idx = np.flatnonzero(mask[frm:])
        return int(idx[0]) + frm if len(idx) else n

    k1 = first(tp1_hit)
    k_hard = first(hard_hit)
    k_close = first(close_stop)
    proceeds, hits, outcome, exit_k = 0.0, 0, None, None
    if k_hard <= k1 and k_hard < n and k_hard <= k_close:          # même bougie : secours d'abord
        proceeds, outcome, exit_k = sell(min(hard, float(o[k_hard]))), STOP_HARD, k_hard
    elif k_close < k1 and k_close < n:
        proceeds, outcome, exit_k = sell(float(cl[k_close])), STOP_CLOSE, k_close
    elif k1 < n:
        proceeds, hits = TP1_SHARE * sell(tp1), 1
        rest = 1 - TP1_SHARE
        k2_hard = first(hard_hit, k1 + 1)
        k2_target = first(target_hit, k1)
        k2_close = first(close_even, k1)
        k2 = min(k2_hard, k2_target, k2_close)
        if k2 < n:
            if k2 == k2_hard:
                proceeds += rest * sell(min(hard, float(o[k2])))
                outcome = STOP_HARD
            elif k2 == k2_target:
                proceeds += rest * sell(objective)
                outcome = TARGET
            else:
                proceeds += rest * sell(float(cl[k2]))
                outcome = STOP_CLOSE
            exit_k = k2
    remaining = 0.0 if outcome is not None else (1 - TP1_SHARE if hits else 1.0)
    exit_at = None
    if outcome is None:
        if not complete:
            if n < len(t) or n == 0 or int(t[n - 1]) + D.HOUR_NS < horizon:
                return {"status": RUNNING}
            proceeds += remaining * sell(float(cl[n - 1]))
            outcome, exit_at = TIME, horizon
        elif n == 0:                                                   # plus aucune bougie après l'entrée
            proceeds += remaining * sell(entry)
            outcome, exit_at = DELISTED, start
        else:
            proceeds += remaining * sell(float(cl[n - 1]))
            last_close = int(t[n - 1]) + D.HOUR_NS
            outcome, exit_at = (TIME if last_close == horizon else DELISTED), last_close
    else:
        exit_at = int(t[exit_k]) + D.HOUR_NS
    label = outcome if hits == 0 or outcome == TARGET else f"{outcome}_APRES_TP1"
    r = (proceeds - cost_in) / risk
    return {"status": RESOLVED, "outcome": label, "hits": hits, "r": round(float(r), 6), "exit_at": D.stamp(exit_at),
            "exit_ns": int(exit_at), "entry_at": D.stamp(start), "entry": entry}


# --- Placebos ---------------------------------------------------------------------------------------------------------------

def signal_id(config: str, symbol: str, at) -> str:
    """Identifiant d'un signal (historique et direct) : sha256("PRICE_ACTION:<config>:<paire>:<instant ISO>")[:16]."""
    stamp_ = pd.Timestamp(at).tz_convert("UTC").isoformat()
    return hashlib.sha256(f"{SEED_PREFIX}:{config}:{symbol}:{stamp_}".encode()).hexdigest()[:16]


def placebo_offsets(ident: str, unit: str) -> list[int]:
    """20 décalages en HEURES tirés sans remise, graine sha256("PRICE_ACTION:" + id) : ±5…84 h (4 h) ou ±2…15 jours
    × 24 h (journalier)."""
    rng = random.Random(int(hashlib.sha256(f"{SEED_PREFIX}:{ident}".encode()).hexdigest()[:16], 16))
    if unit == "4h":
        low, high, scale = *PLACEBO_HOURS, 1
    else:
        low, high, scale = *PLACEBO_DAYS, 24
    candidates = [*range(-high, -low + 1), *range(low, high + 1)]
    return sorted(scale * x for x in rng.sample(candidates, PLACEBOS))


def placebo_reach_ns(unit: str) -> int:
    """Décalage maximal d'un placebo, en ns (84 h ou 15 jours)."""
    return (PLACEBO_HOURS[1] * D.HOUR_NS) if unit == "4h" else PLACEBO_DAYS[1] * D.DAY_NS


def placebo(bars, *, at, offset_h: int, entry: float, stop: float, objective: float, symbol: str, scenario: str,
            unit: str, complete: bool, tp1: float | None = None, hard_stop: float | None = None) -> dict:
    """Entrée au marché à la clôture 1 h `at + décalage`, même géométrie en % que le signal (niveaux × q / entrée),
    même gestion, mêmes frais ; TROU si la bougie 1 h de cette clôture manque."""
    hours = Hourly.of(bars)
    start = int(at) if isinstance(at, int | np.integer) else int(D.to_ns([at])[0])
    when = start + offset_h * D.HOUR_NS
    k = np.searchsorted(hours.t, when - D.HOUR_NS)
    if k >= len(hours.t) or hours.t[k] != when - D.HOUR_NS:
        return {"status": GAP, "entry_ns": when}
    q = float(hours.c[k])
    return simulate(hours, entry_at=when, entry=q, stop=q * stop / entry, objective=q * objective / entry, symbol=symbol,
                    scenario=scenario, unit=unit, complete=complete, tp1=None if tp1 is None else q * tp1 / entry,
                    hard_stop=None if hard_stop is None else q * hard_stop / entry)


def measure(bars, *, ident: str, at, entry: float, stop: float, objective: float, symbol: str, scenario: str,
            unit: str, complete: bool, tp1: float | None = None, hard_stop: float | None = None) -> dict | None:
    """Signal et ses 20 placebos dans un scénario de coûts : None tant que quelque chose est EN_COURS ; sinon R du
    signal, R des placebos (None : placebo inutilisable), moyennes avant / arrière et excès."""
    hours = Hourly.of(bars)
    trade = simulate(hours, entry_at=at, entry=entry, stop=stop, objective=objective, symbol=symbol, scenario=scenario,
                     unit=unit, complete=complete, tp1=tp1, hard_stop=hard_stop)
    if trade["status"] == RUNNING:
        return None
    offsets = placebo_offsets(ident, unit)
    values: list[float | None] = []
    pending = False
    for offset in offsets:
        p = placebo(hours, at=at, offset_h=offset, entry=entry, stop=stop, objective=objective, symbol=symbol,
                    scenario=scenario, unit=unit, complete=complete, tp1=tp1, hard_stop=hard_stop)
        pending = pending or p["status"] == RUNNING
        values.append(p["r"] if p["status"] == RESOLVED else None)
    usable = [v for v in values if v is not None]
    back = [v for v, o in zip(values, offsets, strict=True) if v is not None and o < 0]
    ahead = [v for v, o in zip(values, offsets, strict=True) if v is not None and o > 0]

    def mean(xs: list[float]) -> float | None:
        return round(float(np.mean(xs)), 6) if xs else None
    m, mb, mf = mean(usable), mean(back), mean(ahead)
    return {"outcome": trade["outcome"], "hits": trade["hits"], "r": trade["r"], "exit_at": trade["exit_at"],
            "exit_ns": trade["exit_ns"], "placebos": values, "placebos_resolved": len(usable), "placebo_mean": m,
            "excess": round(trade["r"] - m, 6) if m is not None else None, "placebo_mean_back": mb,
            "placebo_mean_forward": mf, "excess_back": round(trade["r"] - mb, 6) if mb is not None else None,
            "excess_forward": round(trade["r"] - mf, 6) if mf is not None else None, "pending": pending}
