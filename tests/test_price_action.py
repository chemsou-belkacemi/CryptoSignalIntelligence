"""Étude « price action » (price_action/, research/price_action_study.py, docs/PRICE_ACTION.md) : chaque configuration
(cas positif, cas négatifs, frontières), causalité (couper ou falsifier l'avenir ne change rien), gestion calculée à la
main, placebos reproductibles, pipeline de l'étude (fenêtres, appartenance à date, discipline, décision, garde-fous)
et garde d'exécution. Données SYNTHÉTIQUES : rien ici ne dit ce que valent ces configurations sur le marché."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.assistant import rules as assistant_rules
from crypto_signal_intelligence.price_action import detect as D
from crypto_signal_intelligence.price_action import manage as M
from crypto_signal_intelligence.research import price_action_h0 as H
from crypto_signal_intelligence.research import price_action_study as S

T0 = pd.Timestamp("2026-01-01", tz="UTC")
HOUR = pd.Timedelta(hours=1)


# --- Constructeurs de bougies -----------------------------------------------------------------------------------------------

def hours_from_4h(bars: list[tuple], start: pd.Timestamp = T0) -> pd.DataFrame:
    """Chaque bougie 4 h (o, h, l, c, v) devient 4 bougies 1 h : plus haut, plus bas, puis clôture."""
    rows, t = [], start
    for o, h, low, c, v in bars:
        for oo, hh, ll, cc in ((o, h, o, o), (o, o, low, o), (o, max(o, c), min(o, c), c), (c, c, c, c)):
            rows.append((t, oo, hh, ll, cc, v / 4))
            t += HOUR
    frame = pd.DataFrame(rows, columns=D.COLUMNS)
    frame["available_at"] = frame["open_time"] + HOUR + pd.Timedelta(seconds=2)
    return frame


def day4h(o: float, h: float, low: float, c: float, v: float = 1.0) -> list[tuple]:
    """Une journée en six bougies 4 h (ouverture o, plus haut h, plus bas low, clôture c)."""
    return [(o, h, o, o, v / 6), (o, o, low, o, v / 6), (o, max(o, c), min(o, c), c, v / 6), *[(c, c, c, c, v / 6)] * 3]


def rising_days(n: int, start: float = 50.0, step: float = 0.5, wick: float = 0.5) -> list[tuple]:
    out, p = [], start
    for _ in range(n):
        out += day4h(p, p + step + wick, p - wick, p + step)
        p += step
    return out


def falling_days(n: int, start: float = 100.0, step: float = 0.5, wick: float = 0.5) -> list[tuple]:
    out, p = [], start
    for _ in range(n):
        out += day4h(p, p + wick, p - step - wick, p - step)
        p -= step
    return out


def keyed(cands: list[dict]) -> list[tuple]:
    return [(c["config"], c["symbol"], c["at_ns"], round(c["entry"], 9), round(c["stop"], 9), round(c["objective"], 9))
            for c in cands]


# --- Agrégation ---------------------------------------------------------------------------------------------------------------

def test_aggregation_keeps_complete_blocks_only():
    h1 = hours_from_4h([(10, 11, 9, 10.5, 4), (10.5, 12, 10, 11, 4), (11, 11.5, 10.5, 11.2, 4)])
    h1 = h1.drop(index=5).reset_index(drop=True)                     # une heure manque dans le 2e bloc
    bars = D.Bars(h1)
    assert len(bars.h4) == 2 and list(bars.h4.c) == [10.5, 11.2]
    assert bars.h4.h[0] == 11 and bars.h4.l[0] == 9 and bars.h4.v[0] == pytest.approx(4)
    assert len(bars.d1) == 0                                          # journée incomplète : écartée


# --- 1. BASE_RETEST ----------------------------------------------------------------------------------------------------------

def base_retest_bars(*, height_top: float = 106.0, break_volume: float = 3.0, retest_low: float = 106.3,
                     retest_close: float = 106.5, entry_close: float = 107.2, entry_low: float | None = None,
                     extra: list[tuple] | None = None) -> list[tuple]:
    bars = []
    p = 160.0
    for _ in range(120):                                              # descente : ATR journalier ≈ 5
        bars.append((p, p + 1.5, p - 0.3 - 1.5, p - 0.3, 1.0))
        p -= 0.3
    bars.append((p, p + 0.5, 100.5, 101.0, 1.0))                      # chute brutale : arrête l'extension de la base
    for k in range(12):                                               # base 100 – height_top
        o, c = (100.5, height_top - 1.0) if k % 2 == 0 else (height_top - 1.0, 100.5)
        bars.append((o, height_top, 100.0, c, 1.0))
    bars.append((100.5, 108.0, 100.4, 107.5, break_volume))            # cassure
    bars.append((107.5, 107.7, retest_low, retest_close, 1.0))        # retest
    bars.append((retest_close, max(107.6, entry_close), retest_close - 0.1 if entry_low is None else entry_low, entry_close,
                 1.0))                                                # entrée : repart
    bars += extra or [(entry_close, entry_close + 0.2, entry_close - 0.2, entry_close, 1.0)] * 3
    return bars


def test_base_retest_positive_case_levels():
    bars = D.Bars(hours_from_4h(base_retest_bars()))
    found, refused = D.base_retest(bars, "XUSDT")
    assert refused == [] and len(found) == 1
    c = found[0]
    i = 133                                                           # bougie de cassure
    assert c["detail"]["base_high"] == 106.0 and c["detail"]["base_low"] == 100.0 and c["detail"]["base_bars"] == 12
    assert c["entry"] == 107.2 and c["at"] == T0 + 136 * 4 * HOUR     # clôture de la bougie d'entrée (indice 135)
    assert c["stop"] == pytest.approx(106.0 - 0.25 * bars.atr4[i])
    assert c["objective"] == pytest.approx(112.0) and c["unit"] == "4h"
    assert (c["objective"] - c["entry"]) >= 1.5 * (c["entry"] - c["stop"])
    assert c["detail"]["volume_multiple"] == pytest.approx(3.0 / np.mean(bars.h4.v[i - 20:i]))


def test_base_retest_negative_cases_and_boundaries():
    # Volume de cassure à exactement 1,5 × la moyenne : pas assez (strictement plus).
    weak = base_retest_bars(break_volume=1.0)
    assert D.base_retest(D.Bars(hours_from_4h(weak)), "X")[0] == []
    # Une clôture sous le haut de base avant l'entrée annule la configuration.
    broken = base_retest_bars(retest_close=105.9, entry_close=106.8)
    assert D.base_retest(D.Bars(hours_from_4h(broken)), "X")[0] == []
    # Pas de retest : le prix s'envole.
    away = base_retest_bars(retest_low=107.9, retest_close=108.5, entry_close=109.0,
                            extra=[(109.0 + k, 110.0 + k, 108.9 + k, 110.0 + k, 1.0) for k in range(10)])
    assert D.base_retest(D.Bars(hours_from_4h(away)), "X")[0] == []
    # Base trop haute (> 1,5 ATR journalier) : rien.
    tall = base_retest_bars(height_top=112.0)
    assert D.base_retest(D.Bars(hours_from_4h(tall)), "X")[0] == []
    # Frontière du retest : plus bas = haut + 0,25 ATR 4 h exactement → retest valide.
    bars = D.Bars(hours_from_4h(base_retest_bars()))
    limit = 106.0 + 0.25 * bars.atr4[133]
    edge = base_retest_bars(retest_low=round(limit, 12))
    assert len(D.base_retest(D.Bars(hours_from_4h(edge)), "X")[0]) == 1
    above = base_retest_bars(retest_low=limit + 0.01, retest_close=limit + 0.3, entry_low=limit + 0.2, entry_close=limit + 0.8,
                             extra=[(limit + 0.8, limit + 1.0, limit + 0.6, limit + 0.8, 1.0)] * 12)
    assert D.base_retest(D.Bars(hours_from_4h(above)), "X")[0] == []


def test_base_retest_refuses_a_target_under_one_and_a_half_r():
    close_target = base_retest_bars(height_top=101.5, retest_low=101.8, retest_close=102.0, entry_close=103.5,
                                    extra=[(103.5, 103.6, 103.4, 103.5, 1.0)] * 3)
    close_target[133] = (100.5, 103.0, 100.4, 102.5, 3.0)
    close_target[134] = (102.5, 102.7, 101.8, 102.0, 1.0)
    close_target[135] = (102.0, 103.6, 101.9, 103.5, 1.0)
    found, refused = D.base_retest(D.Bars(hours_from_4h(close_target)), "X")
    assert found == [] and len(refused) == 1 and refused[0]["reason"] == D.TARGET_TOO_CLOSE


# --- 2. SQUEEZE --------------------------------------------------------------------------------------------------------------

def squeeze_bars(*, trend=rising_days, exit_volume: float = 2.5, flat: int = 30, extra: list[tuple] | None = None) -> list[tuple]:
    bars = trend(70)
    p = bars[-1][3]
    for k in range(flat):
        o, c = (p - 0.02, p + 0.02) if k % 2 == 0 else (p + 0.02, p - 0.02)
        bars.append((o, max(o, c) + 0.05, min(o, c) - 0.05, c, 1.0))
    bars.append((p, p + 0.6, p - 0.05, p + 0.5, exit_volume))
    bars += extra or [(p + 0.5, p + 0.6, p + 0.4, p + 0.5, 1.0)] * 2
    return bars


def test_squeeze_positive_case_levels():
    raw = squeeze_bars()
    bars = D.Bars(hours_from_4h(raw))
    found, _ = D.squeeze(bars, "XUSDT")
    assert len(found) == 1
    c = found[0]
    e = 420 + 30                                                      # bougie de sortie
    assert c["at"] == T0 + (e + 1) * 4 * HOUR and c["entry"] == pytest.approx(raw[e][3])
    assert c["stop"] == pytest.approx(min(b[2] for b in raw[e - 6:e]))
    assert c["objective"] == pytest.approx(c["entry"] + 2 * (c["entry"] - c["stop"]))
    assert c["detail"]["squeeze_bars"] >= 6


def test_squeeze_negative_cases():
    assert D.squeeze(D.Bars(hours_from_4h(squeeze_bars(exit_volume=1.4))), "X")[0] == []        # volume insuffisant
    assert D.squeeze(D.Bars(hours_from_4h(squeeze_bars(trend=falling_days))), "X")[0] == []      # pas de tendance haussière
    # Première sortie sans volume, la suivante avec : ce n'est plus la première → rien.
    raw = squeeze_bars(exit_volume=1.0)
    p = raw[-3][3]
    raw[-2] = (p, p + 0.3, p - 0.1, p + 0.2, 3.0)
    assert D.squeeze(D.Bars(hours_from_4h(raw)), "X")[0] == []


def test_squeeze_needs_six_compressed_bars(monkeypatch):
    raw = squeeze_bars()
    bars = D.Bars(hours_from_4h(raw))
    flags, upper = D.squeeze_flags(bars.h4)
    e = 450
    five = flags.copy()
    five[: e - 5] = False                                             # seulement 5 bougies comprimées avant la sortie
    monkeypatch.setattr(D, "squeeze_flags", lambda f: (five, upper))
    assert D.squeeze(bars, "X")[0] == []
    six = flags.copy()
    six[: e - 6] = False
    six[e - 6:e] = True
    monkeypatch.setattr(D, "squeeze_flags", lambda f: (six, upper))
    assert len(D.squeeze(bars, "X")[0]) == 1


# --- 3. FORCE_RELATIVE ---------------------------------------------------------------------------------------------------------

def hourly(closes: list[float], lows: list[float] | None = None, highs: list[float] | None = None,
           start: pd.Timestamp = T0) -> pd.DataFrame:
    closes = np.asarray(closes, float)
    opens = np.r_[closes[0], closes[:-1]]
    lows = np.minimum(opens, closes) - 0.1 if lows is None else np.asarray(lows, float)
    highs = np.maximum(opens, closes) + 0.1 if highs is None else np.asarray(highs, float)
    frame = pd.DataFrame({"open_time": pd.date_range(start, periods=len(closes), freq="h", tz="UTC"), "open": opens,
                          "high": highs, "low": lows, "close": closes, "quote_volume": 1.0})
    frame["available_at"] = frame["open_time"] + HOUR + pd.Timedelta(seconds=2)
    return frame


FLAT_H = 480                                                           # 20 jours plats avant la chute


def btc_fall() -> pd.DataFrame:
    """BTC : 100 pendant 20 jours, chute à 92,8 en 12 h, plat, puis remonte à 96 (stabilisation)."""
    closes = [100.0] * FLAT_H + [100 - 0.6 * (j + 1) for j in range(12)] + [92.8] * 8 + [96.0] * 40
    return hourly(closes)


def pair_fall(dip_low: float, dip_close: float) -> pd.DataFrame:
    """Paire à 50 pendant 20 jours (plus bas 49,9), qui descend pendant la chute (plus bas `dip_low`, clôtures
    `dip_close`), puis revient à 50,5."""
    closes = [50.0] * FLAT_H + [dip_close] * 20 + [50.5] * 40
    lows = [49.9] * FLAT_H + [dip_low] * 20 + [50.4] * 40
    highs = [50.1] * FLAT_H + [max(dip_close, 50.0) + 0.1] * 20 + [50.6] * 40
    return hourly(closes, lows, highs)


def test_force_event_onset_fall_start_and_stabilisation():
    events = D.force_events(D.Bars(btc_fall()))
    assert len(events) == 1
    e = events[0]
    onset = T0 + (FLAT_H + 8) * HOUR + HOUR                           # première clôture ≤ −5 % (94,6 / 100)
    assert D.stamp(e["onset"]) == onset and D.stamp(e["fall_start"]) == onset - 24 * HOUR
    assert e["btc_drop_24h"] == pytest.approx(94.6 / 100 - 1)
    # Bougie 4 h du plus bas : [488 ; 492) (plus bas 92,7, le premier atteint) ; sa haute = 95,3 ; la première clôture 4 h
    # au-dessus est celle de la bougie [500 ; 504), qui clôture à 96.
    assert e["btc_low"] == pytest.approx(92.7) and D.stamp(e["low_bar_close"]) == T0 + 492 * HOUR
    assert D.stamp(e["stabilized"]) == T0 + 504 * HOUR


def test_force_pair_reading_and_selection_of_the_three_weakest_declines():
    event = D.force_events(D.Bars(btc_fall()))[0]
    readings = {"AUSDT": D.force_pair(D.Bars(pair_fall(49.5, 49.95)), event),     # tient, −1 %
                "BUSDT": D.force_pair(D.Bars(pair_fall(47.5, 48.0)), event),      # clôture sous 49,9 : n'a pas tenu
                "CUSDT": D.force_pair(D.Bars(pair_fall(49.0, 49.95)), event),     # tient, −2 %
                "DUSDT": D.force_pair(D.Bars(pair_fall(49.75, 49.95)), event),    # tient, −0,5 %
                "EUSDT": D.force_pair(D.Bars(pair_fall(48.5, 49.95)), event),     # tient, −3 % : 4e
                "BTCUSDT": D.force_pair(D.Bars(pair_fall(49.9, 50.0)), event)}    # jamais candidate
    a = readings["AUSDT"]
    assert a["held"] and not readings["BUSDT"]["held"] and a["pre_low"] == pytest.approx(49.9)
    assert a["drop"] == pytest.approx(49.5 / 50 - 1) and a["fall_low"] == pytest.approx(49.5)
    assert a["entry"] == pytest.approx(50.5) and a["stop"] == pytest.approx(49.5 - 0.25 * a["atr4"])
    assert a["objective"] == pytest.approx(a["entry"] + 2 * (a["entry"] - a["stop"]))
    chosen = D.force_select(event, readings)
    assert [c["symbol"] for c in chosen] == ["DUSDT", "AUSDT", "CUSDT"] and chosen[0]["at_ns"] == event["stabilized"]
    assert [c["detail"]["rank"] for c in chosen] == [1, 2, 3] and chosen[0]["detail"]["held_pairs"] == 4   # BTC exclue


def test_force_pair_needs_complete_data():
    event = D.force_events(D.Bars(btc_fall()))[0]
    short = pair_fall(49.5, 49.95).iloc[300:]                          # moins de 200 bougies sur les 10 jours d'avant
    assert D.force_pair(D.Bars(short), event) is None
    holed = pair_fall(49.5, 49.95).drop(index=FLAT_H + 5)              # une heure manque pendant la chute
    assert D.force_pair(D.Bars(holed), event) is None


def test_no_event_without_a_five_percent_fall():
    closes = [100.0] * FLAT_H + [100 - 0.4 * (j + 1) for j in range(12)] + [95.2] * 8 + [97.0] * 40     # −4,8 %
    assert D.force_events(D.Bars(hourly(closes))) == []


# --- 4. INSIDE_DAY -------------------------------------------------------------------------------------------------------------

def inside_day_bars(*, trend=rising_days, mother_low_offset: float = 1.0, inside_high_offset: float = 1.8,
                    breakout_close_offset: float = 2.3, breakout_bar: int = 1) -> tuple[list[tuple], float]:
    bars = trend(70)
    p = bars[-1][3]
    bars += day4h(p, p + 2.0, p - mother_low_offset, p + 1.5)                       # mère
    bars += day4h(p + 1.5, p + inside_high_offset, p - 0.5, p + 1.2)                 # journée intérieure
    nxt = [(p + 1.2, p + 1.7, p + 1.1, p + 1.6, 1 / 6)] * 12
    nxt[breakout_bar] = (p + 1.6, p + breakout_close_offset + 0.1, p + 1.5, p + breakout_close_offset, 1 / 6)
    return bars + nxt, p


def test_inside_day_positive_case_levels():
    raw, p = inside_day_bars()
    bars = D.Bars(hours_from_4h(raw))
    found, refused = D.inside_day(bars, "XUSDT")
    assert refused == [] and len(found) == 1
    c = found[0]
    first_next = 72 * 6                                                # première bougie 4 h après la journée intérieure
    assert c["at"] == T0 + (first_next + 2) * 4 * HOUR and c["entry"] == pytest.approx(p + 2.3)
    assert c["stop"] == pytest.approx(p - 1.0) and c["unit"] == "1d"
    assert c["objective"] == pytest.approx(c["entry"] + 2 * (c["entry"] - c["stop"]))
    assert c["detail"]["mother_high"] == pytest.approx(p + 2.0)


def test_inside_day_negative_cases_and_boundaries():
    assert D.inside_day(D.Bars(hours_from_4h(inside_day_bars(trend=falling_days)[0])), "X")[0] == []   # pas de tendance
    assert D.inside_day(D.Bars(hours_from_4h(inside_day_bars(inside_high_offset=2.1)[0])), "X")[0] == []  # pas intérieure
    assert len(D.inside_day(D.Bars(hours_from_4h(inside_day_bars(inside_high_offset=2.0)[0])), "X")[0]) == 1  # égalité : intérieure
    # Cassure à la 12e clôture 4 h (48 h) : comptée ; aucune cassure dans les 48 h : rien.
    assert len(D.inside_day(D.Bars(hours_from_4h(inside_day_bars(breakout_bar=11)[0])), "X")[0]) == 1
    late, p = inside_day_bars(breakout_close_offset=1.9)
    late += [(p + 1.6, p + 2.5, p + 1.5, p + 2.4, 1 / 6)]                       # 13e clôture : 52 h après
    intended = (T0 + 71 * 24 * HOUR).isoformat()
    assert [c for c in D.inside_day(D.Bars(hours_from_4h(late)), "X")[0] if c["detail"]["inside_day"] == intended] == []
    # Stop au-delà de 3 ATR journaliers : refus.
    wide = D.Bars(hours_from_4h(inside_day_bars(mother_low_offset=6.0)[0]))
    found, refused = D.inside_day(wide, "X")
    assert found == [] and refused[0]["reason"] == D.STOP_TOO_WIDE


# --- 5. SORTIE_BASE_LONGUE ---------------------------------------------------------------------------------------------------

def long_base_days(*, volume: float = 3.0, base_high: float = 90.0, base_low: float = 82.0, early_high: float | None = None,
                   breakout_close: float = 92.5) -> list[tuple]:
    days = []
    p = 50.0
    for k in range(70):
        high = p + 1.0 if early_high is None or k != 60 else early_high
        days.append((p, high, p - 0.5, p + 0.5, 1.0))
        p += 0.5
    for k in range(50):
        o, c = (84.0, 88.0) if k % 2 == 0 else (88.0, 84.0)
        days.append((o, base_high, base_low, c, 1.0))
    days.append((84.0, breakout_close + 0.5, 83.5, breakout_close, volume))
    days.append((breakout_close, breakout_close + 1, breakout_close - 1, breakout_close, 1.0))
    return [bar for o, h, low, c, v in days for bar in day4h(o, h, low, c, v)]


def test_long_base_positive_case_levels():
    bars = D.Bars(hours_from_4h(long_base_days()))
    found, refused = D.long_base(bars, "XUSDT")
    assert refused == [] and len(found) == 1
    c = found[0]
    d = c["detail"]
    assert c["at"] == T0 + 121 * 24 * HOUR and c["entry"] == pytest.approx(92.5) and c["unit"] == "1d"
    assert d["base_high"] == pytest.approx(90.0) and d["base_days"] >= 50
    assert d["base_high"] - d["base_low"] <= 0.25 * 84.0 + 1e-9                     # clôture de la veille : 84
    assert c["stop"] == pytest.approx((d["base_high"] + d["base_low"]) / 2)
    risk = c["entry"] - c["stop"]
    assert c["objective"] == pytest.approx(max(d["base_high"] + (d["base_high"] - d["base_low"]), c["entry"] + 2 * risk))


def test_long_base_negative_cases():
    assert D.long_base(D.Bars(hours_from_4h(long_base_days(volume=1.2))), "X")[0] == []              # volume faible
    assert D.long_base(D.Bars(hours_from_4h(long_base_days(base_high=110.0, base_low=70.0, breakout_close=112.0))),
                       "X")[0] == []                                                                  # base > 25 %
    assert D.long_base(D.Bars(hours_from_4h(long_base_days(early_high=95.0))), "X")[0] == []         # plus haut de 90 j au-dessus


# --- Causalité ------------------------------------------------------------------------------------------------------------------

def synthetic(index: int, days: int = 540) -> pd.DataFrame:
    frame = H.market(index)
    return frame[frame["open_time"] < H.START + pd.Timedelta(days=days)].reset_index(drop=True)


def falsified(frame: pd.DataFrame, cut: pd.Timestamp, seed: int) -> pd.DataFrame:
    """Bougies postérieures à `cut` remplacées par n'importe quoi (prix et volumes extrêmes)."""
    rng = np.random.default_rng(seed)
    out = frame.copy()
    after = out["open_time"] + HOUR > cut
    n = int(after.sum())
    noise = 100 * rng.lognormal(0, 1, n)
    out.loc[after, ["open", "close"]] = np.c_[noise, noise[::-1]]
    out.loc[after, "high"] = np.maximum(noise, noise[::-1]) * 1.5
    out.loc[after, "low"] = np.minimum(noise, noise[::-1]) * 0.5
    out.loc[after, "quote_volume"] = rng.lognormal(15, 2, n)
    return out


@pytest.mark.parametrize("index", [3, 61])
def test_causality_cut_or_falsified_future_changes_no_candidate(index):
    full = synthetic(index)
    reference = D.scan_pair(D.Bars(full), "XUSDT")[0]
    assert len(reference) > 5
    for cut in (pd.Timestamp("2019-06-01 12:00", tz="UTC"), pd.Timestamp("2019-09-17 05:00", tz="UTC"),
                pd.Timestamp("2019-11-30 00:00", tz="UTC")):
        cut_ns = int(D.to_ns([cut])[0])
        expected = keyed([c for c in reference if c["at_ns"] <= cut_ns])
        truncated = full[full["open_time"] + HOUR <= cut]
        assert keyed(D.scan_pair(D.Bars(truncated), "XUSDT")[0]) == expected
        lied = D.scan_pair(D.Bars(falsified(full, cut, seed=index)), "XUSDT")[0]
        assert keyed([c for c in lied if c["at_ns"] <= cut_ns]) == expected


def test_causality_of_force_events_and_readings():
    btc, pair = synthetic(H.PAIRS), synthetic(7)
    events = D.force_events(D.Bars(btc))
    assert len(events) > 5
    for cut in (pd.Timestamp("2019-05-10 08:00", tz="UTC"), pd.Timestamp("2019-10-02 16:00", tz="UTC")):
        cut_ns = int(D.to_ns([cut])[0])
        early = [e for e in events if e["stabilized"] <= cut_ns]
        assert D.force_events(D.Bars(btc[btc["open_time"] + HOUR <= cut]))[:len(early)] == early
        assert [e for e in D.force_events(D.Bars(falsified(btc, cut, 1))) if e["stabilized"] <= cut_ns] == early
        lied = D.Bars(falsified(pair, cut, 2))
        assert [D.force_pair(lied, e) for e in early] == [D.force_pair(D.Bars(pair), e) for e in early]


# --- Gestion (calculée à la main) --------------------------------------------------------------------------------------------

FEE, MARKET = 0.00075, 0.0005                                        # scénario central, paire hors BTC/ETH
ENTRY_AT = pd.Timestamp("2026-03-02 08:00", tz="UTC")


def sell(p: float) -> float:
    return p * (1 - MARKET) * (1 - FEE)


COST_IN = 100 * (1 + MARKET) * (1 + FEE)


def path(rows: list[tuple], start: pd.Timestamp = ENTRY_AT) -> pd.DataFrame:
    """Bougies 1 h (o, h, l, c) à partir de `start` ; complétées par des bougies plates jusqu'à 31 jours."""
    full = list(rows) + [(rows[-1][3],) * 4] * (31 * 24 - len(rows))
    frame = pd.DataFrame(full, columns=["open", "high", "low", "close"])
    frame.insert(0, "open_time", pd.date_range(start, periods=len(full), freq="h", tz="UTC"))
    return frame


def sim(rows, *, unit="4h", objective=108.0, complete=False, bars=None):
    return M.simulate(path(rows) if bars is None else bars, entry_at=ENTRY_AT, entry=100.0, stop=96.0, objective=objective,
                      symbol="XUSDT", scenario="central", unit=unit, complete=complete)


def test_management_tp1_then_objective_by_hand():
    out = sim([(100, 104.5, 99.5, 104), (104, 108.2, 103.5, 107)])
    assert out["outcome"] == M.TARGET and out["hits"] == 1
    assert out["r"] == pytest.approx((0.5 * sell(104) + 0.5 * sell(108) - COST_IN) / 4, abs=1e-6)
    assert out["exit_at"] == ENTRY_AT + 2 * HOUR


def test_management_hard_stop_at_level_or_open_and_tie_counts_the_stop():
    out = sim([(100, 100.5, 95, 97), (97, 97, 93.5, 94)])                  # secours = 100 − 1,5 × 4 = 94
    assert out["outcome"] == M.STOP_HARD and out["r"] == pytest.approx((sell(94) - COST_IN) / 4, abs=1e-6)
    gap = sim([(100, 100.5, 98, 99), (93, 93.5, 92, 93)])                 # ouverture déjà sous le secours
    assert gap["r"] == pytest.approx((sell(93) - COST_IN) / 4, abs=1e-6)
    tie = sim([(100, 104.5, 99.5, 104), (104, 108.5, 93.0, 100)])         # objectif et secours dans la même bougie
    assert tie["outcome"] == f"{M.STOP_HARD}_APRES_TP1"
    assert tie["r"] == pytest.approx((0.5 * sell(104) + 0.5 * sell(94) - COST_IN) / 4, abs=1e-6)


def test_management_close_stop_only_at_unit_closes():
    # Entrée 08:00 : clôtures 4 h à 12:00 (4e bougie). Une clôture ≤ stop à 09:00 ne compte pas ; à 12:00, oui.
    rows = [(100, 100, 95.5, 95.8), (95.8, 99, 95.5, 98), (98, 98, 97, 97), (97, 97, 95.5, 95.9)]
    out = sim(rows)
    assert out["outcome"] == M.STOP_CLOSE and out["exit_at"] == ENTRY_AT + 4 * HOUR
    assert out["r"] == pytest.approx((sell(95.9) - COST_IN) / 4, abs=1e-6)
    # Unité journalière : la même clôture de 12:00 ne compte pas ; seule celle de 00:00 (16e bougie) compte.
    daily = sim(rows + [(95.9, 96.5, 95.5, 96.2)] * 11 + [(96.2, 96.3, 95.6, 95.7)], unit="1d")
    assert daily["outcome"] == M.STOP_CLOSE and daily["exit_at"] == pd.Timestamp("2026-03-03 00:00", tz="UTC")


def test_management_stop_raised_to_entry_after_tp1_and_time_exit():
    rows = [(100, 104.2, 99.8, 103), (103, 103, 101, 101.5), (101.5, 101.6, 99.5, 101), (101, 101, 99.7, 99.9)]
    out = sim(rows)                                                        # clôture 4 h à 99,9 ≤ entrée après TP1
    assert out["outcome"] == f"{M.STOP_CLOSE}_APRES_TP1"
    assert out["r"] == pytest.approx((0.5 * sell(104) + 0.5 * sell(99.9) - COST_IN) / 4, abs=1e-6)
    flat = sim([(100, 101, 99, 100.5)])                                    # rien ne se passe en 10 jours
    assert flat["outcome"] == M.TIME and flat["exit_at"] == ENTRY_AT + pd.Timedelta(days=10)
    assert flat["r"] == pytest.approx((sell(100.5) - COST_IN) / 4, abs=1e-6)
    month = sim([(100, 101, 99, 100.5)], unit="1d")
    assert month["exit_at"] == ENTRY_AT + pd.Timedelta(days=30)


def test_management_gaps_live_versus_history_and_delisting():
    bars = path([(100, 101, 99, 100.5)]).drop(index=[5]).reset_index(drop=True)
    assert sim(None, bars=bars)["status"] == M.RUNNING                     # direct : on attend la bougie
    done = sim(None, bars=bars, complete=True)                             # historique : heure sautée
    assert done["status"] == M.RESOLVED and done["outcome"] == M.TIME
    dead = path([(100, 101, 99, 100.5)]).iloc[:30]                         # paire retirée après 30 heures
    out = sim(None, bars=dead, complete=True)
    assert out["outcome"] == M.DELISTED and out["exit_at"] == ENTRY_AT + 30 * HOUR
    assert sim(None, bars=dead)["status"] == M.RUNNING


def test_management_matches_the_assistant_rule_in_four_hours():
    rng = np.random.default_rng(5)
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.006, 400))
    opens = np.r_[100.0, closes[:-1]]
    rows = list(zip(opens, np.maximum(opens, closes) * 1.002, np.minimum(opens, closes) * 0.998, closes, strict=True))
    bars = path(rows)
    for stop, objective in ((97.0, 106.0), (98.5, 103.5), (95.0, 112.0)):
        mine = M.simulate(bars, entry_at=ENTRY_AT, entry=100.0, stop=stop, objective=objective, symbol="XUSDT",
                          scenario="defavorable", unit="4h", complete=False)
        theirs = assistant_rules.simulate(bars, entry_at=ENTRY_AT, entry=100.0, stop=stop, tp1=100 + (100 - stop),
                                          tp2=objective, symbol="XUSDT", scenario="defavorable")
        assert mine["r"] == pytest.approx(theirs["r"], abs=1e-9) and mine["exit_at"] == theirs["exit_at"]


# --- Placebos ------------------------------------------------------------------------------------------------------------------

def test_placebo_offsets_are_reproducible_and_in_their_windows():
    ident = M.signal_id(D.BASE_RETEST, "XUSDT", ENTRY_AT)
    assert ident == hashlib.sha256(f"PRICE_ACTION:BASE_RETEST:XUSDT:{ENTRY_AT.isoformat()}".encode()).hexdigest()[:16]
    hours = M.placebo_offsets(ident, "4h")
    assert hours == M.placebo_offsets(ident, "4h") and len(set(hours)) == 20
    assert all(5 <= abs(o) <= 84 for o in hours) and hours != M.placebo_offsets("autre", "4h")
    days = M.placebo_offsets(ident, "1d")
    assert len(set(days)) == 20 and all(o % 24 == 0 and 2 <= abs(o) // 24 <= 15 for o in days)
    rng = __import__("random").Random(int(hashlib.sha256(f"PRICE_ACTION:{ident}".encode()).hexdigest()[:16], 16))
    assert hours == sorted(rng.sample([*range(-84, -4), *range(5, 85)], 20))


def test_placebo_uses_the_same_geometry_and_management():
    bars = path([(100, 101, 99, 100)] * 10 + [(100, 100, 99, 99)] + [(99, 99.5, 98.5, 99)] * 300, start=ENTRY_AT - 20 * HOUR)
    p = M.placebo(bars, at=ENTRY_AT, offset_h=-9, entry=100.0, stop=96.0, objective=108.0, symbol="XUSDT",
                  scenario="central", unit="4h", complete=False)
    q = 99.0                                                              # clôture 1 h de t − 9 h
    direct = M.simulate(bars, entry_at=ENTRY_AT - 9 * HOUR, entry=q, stop=q * 0.96, objective=q * 1.08, symbol="XUSDT",
                        scenario="central", unit="4h", complete=False)
    assert p == direct
    assert M.placebo(bars, at=ENTRY_AT, offset_h=-40, entry=100.0, stop=96.0, objective=108.0, symbol="XUSDT",
                     scenario="central", unit="4h", complete=False)["status"] == M.GAP


# --- Pipeline de l'étude ---------------------------------------------------------------------------------------------------------

def test_window_membership_and_discipline():
    end = int(D.to_ns([pd.Timestamp("2025-07-01", tz="UTC")])[0])
    first = int(D.to_ns([S.FIRST_SIGNAL])[0])
    late4 = {"at_ns": end - S.horizon_ns("4h") + 1, "unit": "4h"}
    ok4 = {"at_ns": end - S.horizon_ns("4h"), "unit": "4h"}
    assert not S.in_window(late4, first, end) and S.in_window(ok4, first, end)
    assert S.horizon_ns("1d") == (15 + 30) * D.DAY_NS and S.horizon_ns("4h") == 84 * D.HOUR_NS + 10 * D.DAY_NS
    by_month = {"2021-02": {"AUSDT"}, "2021-03": {"BUSDT"}}
    march_first = int(D.to_ns([pd.Timestamp("2021-03-01 20:00", tz="UTC")])[0])
    assert S.member_at(by_month, "AUSDT", march_first) and not S.member_at(by_month, "BUSDT", march_first)
    assert S.member_at(by_month, "BUSDT", march_first + D.DAY_NS)


def test_pair_rows_apply_discipline_and_measure_both_scenarios():
    frame = synthetic(12, days=900)
    bars = D.Bars(frame)
    first, end = int(D.to_ns([H.FIRST_SIGNAL])[0]), int(D.to_ns([frame["open_time"].iloc[-1]])[0])
    rows, counts = S.pair_rows(bars, "H12USDT", eligible=H.always, first_ns=first, end_ns=end, scenarios=S.SCENARIOS)
    assert rows and set(rows[0]["results"]) == {"central", "defavorable"}
    for config in {r["config"] for r in rows}:
        mine = sorted((r for r in rows if r["config"] == config), key=lambda r: r["at_ns"])
        for a, b in zip(mine, mine[1:], strict=False):
            assert b["at_ns"] >= a["results"]["central"]["exit_ns"] + 48 * D.HOUR_NS
    assert all(r["at_ns"] + S.horizon_ns(r["unit"]) <= end for r in rows)
    assert all(len(r["results"]["central"]["placebos"]) == 20 for r in rows)
    none, _ = S.pair_rows(bars, "H12USDT", eligible=lambda s, t: False, first_ns=first, end_ns=end, scenarios=S.SCENARIOS)
    assert none == []
    assert sum(c.get("SIGNAL", 0) for c in counts.values()) == len(rows)


def fake_rows(values: list[tuple[str, float, float]]) -> list[dict]:
    """Lignes de signal minimales : (date, R central, excès central) ; défavorable = central − 0,05."""
    rows = []
    for date, r, excess in values:
        at = pd.Timestamp(date, tz="UTC")
        res = {"r": r, "excess": excess, "excess_back": excess, "excess_forward": excess, "hits": 1, "outcome": M.TARGET}
        rows.append({"config": D.BASE_RETEST, "symbol": "X", "at_ns": int(D.to_ns([at])[0]),
                     "results": {"central": res, "defavorable": res | {"r": r - 0.05, "excess": excess - 0.05}}})
    return rows


def test_decision_rules_and_guards():
    days = pd.date_range("2019-01-03", "2025-05-30", freq="5D", tz="UTC")
    rng = np.random.default_rng(3)
    good = fake_rows([(str(d), 0.6 + rng.normal(0, 0.3), 0.6 + rng.normal(0, 0.3)) for d in days])
    out = S.decide(good, samples=500)
    assert out["decision"] == S.PISTE and out["guards"]["ok"] and out["guards"]["positive_years"] >= 4
    bad = fake_rows([(str(d), -0.6 + rng.normal(0, 0.3), rng.normal(0, 0.3)) for d in days])
    assert S.decide(bad, samples=500)["decision"] == S.PERTE
    few = fake_rows([(str(d), 1.0, 1.0) for d in days[:99]])
    assert S.decide(few, samples=500)["decision"] == S.INSUFFISANT
    # Tout le gain dans une seule année : les garde-fous refusent la piste.
    lucky = fake_rows([(str(d), (3.0 if d.year == 2021 else -0.05) + rng.normal(0, 0.05),
                        (3.0 if d.year == 2021 else -0.05) + rng.normal(0, 0.05)) for d in days])
    decided = S.decide(lucky, samples=500)
    assert decided["decision"] == S.RIEN and decided["guards"]["best_year"] == "2021" and not decided["guards"]["ok"]


def test_load_development_never_reads_after_june_2025(settings):
    from crypto_signal_intelligence.data.store import CandleStore
    from crypto_signal_intelligence.research.long_history import long_settings
    frame = hourly([100.0] * 200, start=pd.Timestamp("2025-06-25", tz="UTC"))
    store = CandleStore(long_settings(settings).data_dir)
    target = store.path("XUSDT", "1h")
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target, index=False)
    end = S.development_end_exclusive(settings)
    assert end == pd.Timestamp("2025-07-01", tz="UTC")
    loaded = S.load_development(settings, "XUSDT", end)
    assert loaded["open_time"].max() == pd.Timestamp("2025-06-30 23:00", tz="UTC") and len(loaded) == 144


# --- Garde d'exécution --------------------------------------------------------------------------------------------------------

def test_review_guards(tmp_path, settings):
    with pytest.raises(S.DirtyCode):
        S.require_clean_and_reviewed("abc+DIRTY", review="abc")
    with pytest.raises(S.NotReady):
        S.require_clean_and_reviewed("abc", review="")
    assert S.review_file_is_inert(Path(S.__file__).with_name("price_action_review.py"))
    bad = tmp_path / "review.py"
    bad.write_text('"""doc"""\nfrom __future__ import annotations\nCODE_REVIEW = "x"\nimport os\n', encoding="utf-8")
    assert not S.review_file_is_inert(bad)
    with pytest.raises(S.NotReady):
        S.require_clean_and_reviewed("HEAD", review="HEAD", settings=settings, fingerprint="0" * 64)
    S.require_clean_and_reviewed("HEAD", review="HEAD", settings=settings, fingerprint=S.config_fingerprint(settings))


def test_control_inscription_is_checked(tmp_path):
    with pytest.raises(S.NotReady):
        S.control_of(None)
    path = tmp_path / "criteres.json"
    path.write_text(json.dumps({"study": S.STUDY, "configs": {c: {"passes": True} for c in D.CONFIGS}}), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert S.control_of(f"{path}#{digest}")["study"] == S.STUDY
    with pytest.raises(S.NotReady):
        S.control_of(f"{path}#{'0' * 64}")
    path.write_text(json.dumps({"study": "AUTRE", "configs": {}}), encoding="utf-8")
    with pytest.raises(S.NotReady):
        S.control_of(f"{path}#{hashlib.sha256(path.read_bytes()).hexdigest()}")


def test_cli_refuses_real_execution_without_executer_or_review(settings):
    from typer.testing import CliRunner

    from crypto_signal_intelligence.cli import app
    runner = CliRunner()
    refused = runner.invoke(app, ["price-action", "executer"])
    assert refused.exit_code == 2 and "--executer" in refused.output
    blocked = runner.invoke(app, ["price-action", "executer", "--executer"])
    assert blocked.exit_code == 3 and "Aucun calcul" in blocked.output
    assert not settings.experiments_db.exists() or S.ExperimentRegistry(settings.experiments_db).count_runs(S.STUDY) == 0


def test_study_refuses_a_second_execution(settings, monkeypatch):
    registry = S.ExperimentRegistry(settings.experiments_db)
    registry.record(run_id="PRICEACTION-x", created_at="2026-10-10T00:00:00+00:00", kind=S.KIND, hypothesis="h",
                    strategy=S.STUDY, strategy_version=1, variant="v", params={}, period_label="DEVELOPMENT",
                    period_start="2019", period_end="2025", universe=[], data_hashes={}, git_commit="c", dependencies={},
                    seed=1, cost_scenario="c", simulation_rules={}, metrics={"n_trials": 5}, status="COMPLETED", report_dir=None)
    monkeypatch.setattr(S, "require_clean_and_reviewed", lambda *a, **k: None)
    monkeypatch.setattr(S, "control_of", lambda inscription: {"configs": {c: {"passes": True} for c in D.CONFIGS}})
    with pytest.raises(S.AlreadyRun):
        S.run(settings, now=pd.Timestamp("2026-10-10", tz="UTC"))


def test_study_end_to_end_on_a_tiny_synthetic_universe(settings, monkeypatch):
    """Le pipeline réel (chargement coupé, appartenance à date, FORCE_RELATIVE, registre, rapport) sur 3 paires
    synthétiques écrites dans un magasin long temporaire ; ne dit rien du marché."""
    from crypto_signal_intelligence.data.store import CandleStore
    from crypto_signal_intelligence.research.long_history import long_settings
    store = CandleStore(long_settings(settings).data_dir)
    end = pd.Timestamp("2025-07-01", tz="UTC")
    for index, symbol in ((H.PAIRS, "BTCUSDT"), (4, "AUSDT"), (55, "BUSDT")):
        frame = H.market(index, start=pd.Timestamp("2023-01-01", tz="UTC"), end=pd.Timestamp("2025-09-01", tz="UTC"))
        target = store.path(symbol, "1h")
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(target, index=False)
    months = pd.date_range("2023-01-01", "2025-06-01", freq="MS", tz="UTC")
    members = pd.DataFrame([{"month": m, "symbol": s, "rank": 1, "median_quote_volume": 1.0}
                            for m in months for s in ("BTCUSDT", "AUSDT", "BUSDT")])
    monkeypatch.setattr("crypto_signal_intelligence.research.pit_universe.load_membership", lambda s: members)
    monkeypatch.setattr("crypto_signal_intelligence.research.universe.RESEARCH_UNIVERSE", ("AUSDT",))
    monkeypatch.setattr(S, "FIRST_SIGNAL", pd.Timestamp("2023-06-01", tz="UTC"))
    monkeypatch.setattr(S, "require_clean_and_reviewed", lambda *a, **k: None)
    monkeypatch.setattr(S, "control_of", lambda inscription: {"configs": {c: {"passes": c != D.SQUEEZE} for c in D.CONFIGS}})
    summary = S.run(settings, now=pd.Timestamp("2026-10-10", tz="UTC"), workers=1)
    assert summary["n_trials"] == 4 and summary["configs_removed_h0"] == [D.SQUEEZE]
    assert summary["decisions"][D.SQUEEZE] == S.RETIREE and set(summary["decisions"]) == set(D.CONFIGS)
    registry = S.ExperimentRegistry(settings.experiments_db)
    assert registry.count_runs(S.STUDY) == 1 and registry.program_trials("DEVELOPMENT") == 4
    signals = json.loads((Path(registry.get(summary["run_id"])["report_dir"]) / "signals.json").read_text(encoding="utf-8"))
    last = max((r["at_ns"] + S.horizon_ns(r["unit"]) for rows in signals.values() for r in rows), default=0)
    assert last <= int(D.to_ns([end])[0])
