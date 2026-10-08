"""Programme « combinaisons » (docs/COMBINAISONS.md) : briques, causalité et mutations, votes, placebos, modèle
logistique, intervalles, décisions et étape 3. Données SYNTHÉTIQUES seulement : ces tests vérifient le code, jamais une
performance de marché."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.patterns import volume as pv
from crypto_signal_intelligence.patterns.primitives import ZIGZAG_M, atr, zigzag
from crypto_signal_intelligence.research import combinations as cb
from crypto_signal_intelligence.research import combinations_study as cs
from crypto_signal_intelligence.research import figures_history as fh
from crypto_signal_intelligence.research import trendline_confirmation as tc

START = pd.Timestamp("2021-01-01", tz="UTC")


def hourly(days: int, seed: int, *, gaps: bool = True, start: pd.Timestamp = START) -> pd.DataFrame:
    """Bougies 1 h synthétiques (marche aléatoire, flux tirés indépendamment), avec trous si demandé."""
    rng = np.random.default_rng(seed)
    n = days * 24
    times = start + pd.to_timedelta(np.arange(n), unit="h")
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.r_[100.0, c[:-1]]
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 0.004, n)))
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 0.004, n)))
    v = rng.lognormal(7, 0.5, n)
    share = np.clip(rng.normal(0.48, 0.08, n), 0.01, 0.99)
    frame = pd.DataFrame({"open_time": times, "open": o, "high": h, "low": lo, "close": c, "base_volume": v,
                          "taker_buy_base_volume": v * share,
                          "number_of_trades": rng.poisson(np.exp(rng.normal(5.5, 0.8, n))).astype(float)})
    frame["available_at"] = frame["open_time"] + pd.Timedelta(hours=1, seconds=2)
    if gaps:
        frame = frame.drop(index=rng.choice(n, n // 200, replace=False))
        frame = frame[~frame["open_time"].between(times[n // 2], times[n // 2 + 30])]
    return frame.reset_index(drop=True)


def btc_frame(days: int, seed: int) -> pd.DataFrame:
    return hourly(days, seed, gaps=False)[["open_time", "close", "available_at"]]


def _equal(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    a, b = a.reset_index(drop=True), b.reset_index(drop=True)
    if len(a) != len(b):
        return False
    for column in a.columns:
        x, y = a[column].to_numpy(), b[column].to_numpy()
        if x.dtype.kind == "f" or y.dtype.kind == "f":
            x, y = x.astype(float), y.astype(float)
            if not np.array_equal(x, y, equal_nan=True):
                return False
        elif not np.array_equal(x, y):
            return False
    return True


def _falsify(frame: pd.DataFrame, after: pd.Timestamp, seed: int, *, only: pd.Timestamp | None = None) -> pd.DataFrame:
    """Futur (ou une seule bougie) falsifié : prix, volumes et nombre de transactions remplacés."""
    out = frame.copy()
    rng = np.random.default_rng(seed)
    mask = (out["open_time"] == only) if only is not None else (out["open_time"] > after)
    k = int(mask.sum())
    if not k:
        return out
    level = rng.uniform(50, 200, k)
    out.loc[mask, "close"] = level * (1.04 if only is not None else 1.0)     # bougie seule : reste une candidate
    for column in ("open", "high", "low", "base_volume", "taker_buy_base_volume", "number_of_trades"):
        if column not in out:
            continue
        if column == "high":
            out.loc[mask, column] = level * 1.05
        elif column == "low":
            out.loc[mask, column] = level * 0.95
        elif column == "open":
            out.loc[mask, column] = level * rng.uniform(0.96, 1.04, k)
        elif column == "taker_buy_base_volume":
            out.loc[mask, column] = out.loc[mask, "base_volume"] * rng.uniform(0, 1, k)
        else:
            out.loc[mask, column] = rng.uniform(200 if only is not None else 10, 5000, k)
    return out


def _same_reference(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    """Colonnes de référence de la dernière ligne ; centile et médiane d'absorption comparés là où ils existent des
    deux côtés (ils ne sont calculés qu'aux bougies candidates)."""
    for column in cb.REFERENCE_COLUMNS:
        x, y = float(a[column].iloc[-1]), float(b[column].iloc[-1])
        if column in ("abs_q10", "abs_median") and not (np.isfinite(x) and np.isfinite(y)):
            continue
        if not (x == y or (np.isnan(x) and np.isnan(y))):
            return False
    return True


def violations(h1: pd.DataFrame, btc: pd.DataFrame, cutoffs, *, mutation: str | None = None, stop_at_first: bool = False) -> int:
    """Nombre de coupures où ce qui est connu avant la coupure change : données tronquées, futur falsifié, bougie de
    la coupure falsifiée (colonnes de référence seulement)."""
    full = cb.brick_table(h1, btc, mutation=mutation)
    bad = 0
    for k, cut in enumerate(cutoffs):
        known = full[full["open_time"] <= cut]
        part = cb.brick_table(h1[h1["open_time"] <= cut], btc[btc["open_time"] <= cut], mutation=mutation)
        fake = cb.brick_table(_falsify(h1, cut, k), _falsify(btc, cut, k + 1), mutation=mutation)
        present = _falsify(h1, cut, k + 2, only=cut)
        now = cb.brick_table(present[present["open_time"] <= cut], btc[btc["open_time"] <= cut], mutation=mutation)
        ok = (_equal(known, part) and _equal(known, fake[fake["open_time"] <= cut]) and _same_reference(known, now))
        bad += not ok
        if bad and stop_at_first:
            break
    return bad


@pytest.fixture(scope="module")
def walk():
    return hourly(100, 11), btc_frame(100, 12)


@pytest.fixture(scope="module")
def full_table(walk):
    h1, btc = walk
    return cb.brick_table(h1, btc)


def _cutoffs(h1: pd.DataFrame, table: pd.DataFrame, seed: int = 0, count: int = 6) -> list[pd.Timestamp]:
    """Coupures au hasard, juste après la confirmation d'un pivot ZigZag, juste après 00:00 UTC, à des événements."""
    rng = np.random.default_rng(seed)
    times = table["open_time"]
    late = times[times > START + pd.Timedelta(days=35)].reset_index(drop=True)
    out = list(late.iloc[rng.choice(len(late), count, replace=False)])
    frame = cb.prepare(h1)
    a = atr(*(frame[k].to_numpy(float) for k in ("high", "low", "close")))
    pivots = [p for p in zigzag(frame["high"], frame["low"], a, ZIGZAG_M["1h"]) if p.known_at > 800]
    out += [frame["open_time"].iloc[p.known_at] for p in pivots[:2]]
    midnight = late[late.dt.hour == 0]
    out += [midnight.iloc[3], midnight.iloc[len(midnight) // 2] + pd.Timedelta(hours=1)]
    for brick in cb.EVENT_BRICKS:
        hits = table.loc[table[brick] & (table["open_time"] > START + pd.Timedelta(days=35)), "open_time"]
        out += list(hits.iloc[:1])
    return sorted(set(out))


# --- Briques : exemples vérifiables à la main ----------------------------------------------------------------------

def test_profile_compiled_copy_equals_volume_profile():
    rng = np.random.default_rng(0)
    for case in range(150):
        n = int(rng.integers(5, 300))
        lo = np.round(rng.uniform(90, 110, n), 1 if case % 3 == 0 else 6)       # prix arrondis : égalités fréquentes
        h = lo + np.where(rng.random(n) < 0.1, 0.0, np.round(rng.uniform(0, 5, n), 1))
        v = np.round(rng.uniform(0, 100, n), 0 if case % 2 else 3)
        a, b = pv.volume_profile(h, lo, v), cb.volume_profile_fast(h, lo, v)
        assert np.array_equal(a.volume, b.volume) and a.poc == b.poc and a.val == b.val and a.vah == b.vah


def test_profile_example_of_indicateurs():
    p = cb.volume_profile_fast([10, 10], [0, 5], [10, 10])
    assert p.poc == pytest.approx((5.0, 5.2)) and p.val == pytest.approx(5.0) and p.vah == pytest.approx(9.8)


def _flat(n: int, *, start: pd.Timestamp = START) -> pd.DataFrame:
    times = start + pd.to_timedelta(np.arange(n), unit="h")
    frame = pd.DataFrame({"open_time": times, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
                          "base_volume": 10.0, "taker_buy_base_volume": 5.0, "number_of_trades": 200.0})
    frame["available_at"] = frame["open_time"] + pd.Timedelta(hours=1, seconds=2)
    return frame


def test_centred_delta_is_volume_weighted_shifted_and_needs_684_bars():
    rng = np.random.default_rng(1)
    frame = _flat(800)
    frame["base_volume"] = rng.uniform(1, 50, 800)
    frame["taker_buy_base_volume"] = frame["base_volume"] * rng.uniform(0.3, 0.6, 800)
    frame.loc[5, "base_volume"] = 0.0                       # V = 0 : non valide
    frame.loc[5, "taker_buy_base_volume"] = 0.0
    grid = cb.Grid.of(cb.prepare(frame))
    s, delta = cb.centred_delta(cb.prepare(frame), grid)
    t = 760
    tb, v = frame["taker_buy_base_volume"].to_numpy(), frame["base_volume"].to_numpy()
    expected = tb[t - 720:t].sum() / v[t - 720:t].sum()
    assert s[t] == pytest.approx(expected, rel=1e-12)
    assert delta[t] == pytest.approx(tb[t] - expected * v[t], rel=1e-9)
    assert (tb[t - 720:t] - s[t] * v[t - 720:t]).sum() == pytest.approx(0.0, abs=1e-8)   # somme nulle sur la fenêtre
    # 684 bougies valides au moins : 36 heures absentes passent, 37 non (la bougie t n'entre jamais dans s̃_t)
    for missing, finite in ((36, True), (37, False)):
        cut = frame.drop(index=range(t - 100, t - 100 + missing)).reset_index(drop=True)
        prepared = cb.prepare(cut)
        s2, _ = cb.centred_delta(prepared, cb.Grid.of(prepared))
        row = int(np.flatnonzero(prepared["open_time"] == frame["open_time"].iloc[t])[0])
        assert bool(np.isfinite(s2[row])) == finite
    # changer la bougie t ne change pas s̃_t ; la mutation `centrage_t` le change
    other = frame.copy()
    other.loc[t, "taker_buy_base_volume"] = other.loc[t, "base_volume"]
    p2 = cb.prepare(other)
    assert cb.centred_delta(p2, cb.Grid.of(p2))[0][t] == s[t]
    assert cb.centred_delta(p2, cb.Grid.of(p2), mutation="centrage_t")[0][t] != \
        cb.centred_delta(cb.prepare(frame), grid, mutation="centrage_t")[0][t]


def test_absorption_reference_needs_100_trades_and_360_bars():
    frame = _flat(800)
    shares = 0.40 + 0.001 * (np.arange(800) % 100)
    frame["taker_buy_base_volume"] = frame["base_volume"] * shares
    frame.loc[np.arange(800) % 4 == 0, "number_of_trades"] = 50.0         # hors référence (N < 100)…
    frame.loc[np.arange(800) % 4 == 0, "taker_buy_base_volume"] = 0.5     # … avec une part très basse
    t = 790
    frame.loc[t, ["low", "high", "close", "number_of_trades"]] = [99.0, 101.0, 100.9, 150.0]
    frame.loc[t, "taker_buy_base_volume"] = 10.0 * 0.405                  # sous le 10e centile des seules bougies valides
    prepared = cb.prepare(frame)
    out = cb.absorption(prepared, cb.Grid.of(prepared))
    assert out["ABSORPTION"][t] and out["abs_reference"][t] == 540
    ref = shares[t - 720:t][np.arange(t - 720, t) % 4 != 0]
    assert out["abs_q10"][t] == np.quantile(ref, 0.1)
    for change in ({"number_of_trades": 99.0}, {"close": 100.0}, {"base_volume": 9.0}):
        other = frame.copy()
        for k, value in change.items():
            other.loc[t, k] = value
        p = cb.prepare(other)
        assert not cb.absorption(p, cb.Grid.of(p))["ABSORPTION"][t]
    sparse = frame.copy()
    sparse.loc[np.arange(800) % 2 == 1, "number_of_trades"] = 50.0        # 180 bougies valides sur 720 : absente
    sparse.loc[t, "number_of_trades"] = 150.0
    p = cb.prepare(sparse)
    got = cb.absorption(p, cb.Grid.of(p))
    assert not got["calc_ABSORPTION"][t] and not got["ABSORPTION"][t]


def _avwap_case() -> pd.DataFrame:
    closes = [100.0] * 20 + [99, 98, 97, 96, 95, 94.3] + [95.5, 96.5, 97.5, 98.5, 99.5, 100, 100.2, 100, 99.8]
    closes += [96.0, 98.0, 99.5, 100, 99.8, 96.2, 98.5, 99.5, 100, 100.1, 100.2]
    n = len(closes)
    frame = _flat(n)
    c = np.array(closes)
    frame["close"] = c
    frame["open"] = np.r_[100.0, c[:-1]]
    frame["high"] = np.maximum(frame["open"], c) + 0.5
    frame["low"] = np.minimum(frame["open"], c) - 0.5
    frame.loc[25, "low"] = 94.0
    frame.loc[26, "low"] = 94.5
    return frame


def test_avwap_reclaim_waits_for_the_anchor_confirmation():
    frame = cb.prepare(_avwap_case())
    a = atr(frame["high"], frame["low"], frame["close"])
    lows = [p for p in zigzag(frame["high"], frame["low"], a, ZIGZAG_M["1h"]) if p.kind == "low"]
    assert lows[0].index == 25 and lows[0].known_at == 28                 # construction : ancre en 25, connue en 28
    out = cb.avwap_reclaims(frame, a)
    events = list(np.flatnonzero(out["AVWAP_RECLAIM"]))
    vwap = pv.anchored_vwap(frame["high"], frame["low"], frame["close"], frame["base_volume"], 25)
    c = frame["close"].to_numpy()
    first = next(t for t in range(29, len(c)) if c[t - 1] <= vwap[t - 1] and c[t] > vwap[t])
    assert events == [first]                                              # une seule reprise par ancre, après k_a
    assert c[25] <= vwap[25] and c[26] > vwap[26]                         # la reprise « avant l'ancre » existe…
    leaky = list(np.flatnonzero(cb.avwap_reclaims(frame, a, mutation="reprise_avant_ancre")["AVWAP_RECLAIM"]))
    assert leaky[0] == 26 and 26 not in events                            # … et seule la mutation la compte
    assert not out["calc_AVWAP_RECLAIM"][27] and out["calc_AVWAP_RECLAIM"][28]


def test_cvd_divergence_is_known_two_bars_after_the_second_low():
    n = 30
    frame = _flat(n)
    frame.loc[10, "low"], frame.loc[20, "low"] = 90.0, 85.0
    prepared = cb.prepare(frame)
    delta = np.zeros(n)
    delta[11:21] = 1.0
    assert list(np.flatnonzero(cb.cvd_divergences(prepared, delta))) == [22]
    assert list(np.flatnonzero(cb.cvd_divergences(prepared, delta, mutation="cvd_j2"))) == [20]
    assert not cb.cvd_divergences(prepared, -delta).any()                 # flux centré ≤ 0 : rien
    holes = delta.copy()
    holes[15] = np.nan
    assert not cb.cvd_divergences(prepared, holes).any()                  # deltaC manquant : rien
    higher = frame.copy()
    higher.loc[20, "low"] = 91.0
    assert not cb.cvd_divergences(cb.prepare(higher), delta).any()
    for second, size in ((14, 30), (71, 80)):                         # écarts de 4 et 61 bougies : hors de 5 à 60
        far = _flat(size)
        far.loc[10, "low"], far.loc[second, "low"] = 90.0, 85.0
        assert not cb.cvd_divergences(cb.prepare(far), np.ones(size)).any()


def test_bounce_needs_previous_close_above_and_is_once_a_day():
    close = np.array([101, 99.5, 100.5, 101, 100.2, 100.4])
    low = np.array([100.5, 99, 99.5, 100.5, 100.1, 99.7])
    level = np.full(6, 100.0)
    day = np.array([0, 0, 0, 0, 0, 1])
    # t = 1 : clôture sous le niveau ; t = 2 : C_1 ≤ niveau ; t = 3, 4 : plus bas au-dessus ; t = 5 : rebond (jour 1)
    assert list(np.flatnonzero(cb._bounce(close, low, level, day))) == [5]
    close2 = np.array([101, 100.6, 100.5, 101, 100.8, 100.4])
    low2 = np.array([100.5, 99, 99.5, 100.5, 99.5, 99.7])
    assert list(np.flatnonzero(cb._bounce(close2, low2, level, np.zeros(6)))) == [1]    # un seul par jour


def test_btc_state_uses_complete_days_known_at_23h_available_at():
    btc = btc_frame(80, 3)
    daily = cb.btc_daily(btc)
    assert (daily["known_at"] == daily["day"] + pd.Timedelta(days=1, seconds=2)).all()
    at = pd.Series([daily["known_at"].iloc[60] - pd.Timedelta(seconds=1), daily["known_at"].iloc[60]])
    got = cb.join_btc(at, daily)
    assert got[0] == daily["bull"].iloc[59] and got[1] == daily["bull"].iloc[60]
    holed = btc.drop(index=btc.index[btc["open_time"] == btc["open_time"].iloc[24 * 70 + 5]])
    assert pd.Timestamp(btc["open_time"].iloc[24 * 70]).floor("D") not in set(cb.btc_daily(holed)["day"])


def test_period_mask_equals_figures_history_in_period(full_table):
    first = pd.Timestamp(full_table["open_time"].iloc[0])
    end = pd.Timestamp(full_table["open_time"].iloc[-1]) - pd.Timedelta(days=3)
    # période synthétique : 2021 ; FIRST_DAY (2019) ne limite pas, l'avance de 90 jours si
    mask = cb.period_mask(full_table, first_bar=first, end=end)
    expected = [cb.in_period(pd.Timestamp(t), first_bar=first, end=end) for t in full_table["at"]]
    assert list(mask) == expected


# --- Causalité et mutations ----------------------------------------------------------------------------------------

def test_brick_table_is_causal_at_random_pivot_midnight_and_event_cutoffs(walk, full_table):
    h1, btc = walk
    cutoffs = _cutoffs(h1, full_table)
    assert len(cutoffs) >= 12
    assert violations(h1, btc, cutoffs) == 0


@pytest.mark.parametrize("mutation", cb.MUTATIONS)
def test_each_leak_mutation_is_detected(walk, full_table, mutation):
    h1, btc = walk
    leaky = cb.brick_table(h1, btc, mutation=mutation)                   # coupures aux événements de la version fautive
    cutoffs = _cutoffs(h1, leaky, seed=3, count=10)
    if mutation in ("centrage_t", "absorption_t"):
        cand = full_table[np.isfinite(full_table["abs_q10"]) & (full_table["open_time"] > START + pd.Timedelta(days=40))]
        cutoffs = list(cand["open_time"].iloc[:3]) + cutoffs
    if mutation == "profil_jour":
        cutoffs = [t for t in cutoffs if t.hour == 0] + cutoffs
    assert violations(h1, btc, cutoffs, mutation=mutation, stop_at_first=True) > 0


def test_votes_are_active_for_24_hours_and_absent_when_not_computable():
    times = START + pd.to_timedelta(np.arange(60), unit="h")
    table = pd.DataFrame({"open_time": times})
    for brick in cb.EVENT_BRICKS:
        table[brick] = False
        table[f"calc_{brick}"] = True
    for brick in cb.STATE_BRICKS:
        table[brick] = 1.0
    table.loc[10, "CVD_DIV"] = True
    table.loc[:5, "calc_ABSORPTION"] = False
    table.loc[3, "TENDANCE"] = np.nan
    v = cb.votes(table)
    assert v.loc[10:33, "CVD_DIV"].eq(1).all() and v.loc[34, "CVD_DIV"] == 0 and v.loc[9, "CVD_DIV"] == 0
    assert np.isnan(v.loc[2, "ABSORPTION"]) and v.loc[6, "ABSORPTION"] == 0
    assert np.isnan(v.loc[3, "TENDANCE"]) and v.loc[3, "n_votes"] == 2
    assert v["trigger"].sum() == 1 and v.loc[10, "n_votes"] == 4


def test_votes_and_triggers_are_causal(walk, full_table):
    h1, btc = walk
    cut = full_table["open_time"].iloc[1800]
    part = cb.brick_table(h1[h1["open_time"] <= cut], btc[btc["open_time"] <= cut])
    assert _equal(cb.votes(full_table).iloc[:len(part)], cb.votes(part))


def test_one_position_reads_only_exits_known_at_the_decision():
    hour = 3600e9
    at = np.array([0, 5, 10, 30, 34, 40]) * hour
    exits = np.array([20, 8, np.nan, 40.5, 36, 50]) * hour
    assert list(cs.one_position(at, exits)) == [True, False, False, True, False, False]
    # coupure à 35 h : les sorties après 35 h sont inconnues (+inf) ; décisions avant 35 h identiques
    cut = 35 * hour
    known = np.where(exits <= cut, exits, np.where(np.isnan(exits), np.nan, np.inf))
    before = at <= cut
    assert list(cs.one_position(at[before], known[before])) == list(cs.one_position(at, exits)[before])
    leaky_full = cs.one_position(at, exits, mutation="exit_at")
    leaky_cut = cs.one_position(at[before], known[before], mutation="exit_at")
    assert list(leaky_full[before]) != list(leaky_cut)                    # la mutation lit une sortie future


# --- Placebos ---------------------------------------------------------------------------------------------------------

def test_uniform_placebo_minutes_reimplementation_is_identical_with_the_trendline_identifier():
    lo, hi = pd.Timestamp("2020-01-01", tz="UTC").value, pd.Timestamp("2021-06-01", tz="UTC").value
    for key in ("BTCUSDT:1h:TRENDLINE:bull:20200501T1000", "X:1h:COMBO:bull:20200601T0000"):
        assert np.array_equal(cs.placebo_minutes(key, lo, hi, test_id=tc.TEST_ID), tc.placebo_minutes(key, lo, hi))
        assert not np.array_equal(cs.placebo_minutes(key, lo, hi), tc.placebo_minutes(key, lo, hi))


def _minutes(days: int, seed: int) -> tuple[fh.Minutes, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    n = days * 1440
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.0012, n)))
    o = np.r_[100.0, c[:-1]]
    h = np.maximum(o, c) * np.exp(np.abs(rng.normal(0, 0.0006, n)))
    lo = np.minimum(o, c) * np.exp(-np.abs(rng.normal(0, 0.0006, n)))
    bars = pd.DataFrame({"open_time": START + pd.to_timedelta(np.arange(n), unit="min"), "open": o, "high": h, "low": lo,
                         "close": c})
    return fh.Minutes.from_frame(bars), bars


def test_uniform_placebos_equal_trendline_confirmation_with_its_identifier():
    m, _ = _minutes(40, 5)
    setup = fh.Setup("SOLUSDT:1h:COMBO:bull:20210110T0500", "COMBO", "1h", START + pd.Timedelta(days=9, hours=6), 98.0)
    lo, hi = START.value, (START + pd.Timedelta(days=37)).value
    a = fh.play(setup, m, "SOLUSDT", pd.Timedelta(seconds=2))
    a["stop"] = a["entry"] * 0.98
    a["tp1"] = a["entry"] + (a["entry"] - a["stop"])
    b = dict(a)
    tc.with_uniform_placebos(a, m, lo_ns=lo, hi_ns=hi)
    cs.with_uniform_placebos(b, m, lo_ns=lo, hi_ns=hi, test_id=tc.TEST_ID)
    assert a == b


def test_matched_draw_takes_20_all_from_5_none_under_5():
    big = np.arange(100, 200)
    drawn = cs.matched_draw("K", big)
    assert len(drawn) == 20 and len(set(drawn)) == 20 and set(drawn) <= set(big)
    assert np.array_equal(drawn, cs.matched_draw("K", big))               # graine déduite de l'identifiant
    assert list(cs.matched_draw("K", np.arange(7))) == list(range(7))
    assert len(cs.matched_draw("K", np.arange(4))) == 0


def test_play_rows_matched_placebos_share_pair_year_and_states_and_never_the_trigger():
    from crypto_signal_intelligence.research import combinations_controls as cc
    walk = cc.synthetic_walk("constante", 4, years=1)
    end = pd.Timestamp(int(walk.minutes.ns[-1]), tz="UTC")
    data = cs.pair_data(walk.symbol, walk.h1, cb.btc_daily(walk.btc), end=end)
    rows = cs.trigger_rows(data)[:25]
    out = cs.play_rows(data, rows, walk.minutes, cc.LATENCY)
    states = cb.state_key(data.table)
    years = pd.DatetimeIndex(data.table["at"]).year
    checked = 0
    for i, row in zip(rows, out, strict=True):
        if row["status"] != fh.EXECUTED:
            continue
        drawn = row["matched_rows"]
        assert i not in drawn and all(states[j] == states[i] and years[j] == years[i] and data.in_period[j] for j in drawn)
        assert len(drawn) == min(20, row["matched_candidates"]) or (row["matched_candidates"] < 5 and not drawn)
        assert row["uplacebo_n_central"] <= 20 and np.isfinite(row["r_central"])
        checked += 1
    assert checked > 10


# --- Modèle logistique ----------------------------------------------------------------------------------------------

def test_logit_minimizes_the_scikit_learn_default_objective():
    from scipy.optimize import minimize
    rng = np.random.default_rng(2)
    X = (rng.random((3000, 9)) < 0.4).astype(float)
    y = (rng.random(3000) < 1 / (1 + np.exp(-(X @ np.linspace(-0.5, 0.5, 9) - 0.2)))).astype(float)
    model = cs.fit_logit(X, y, tuple(cb.VOTERS))
    ref = minimize(cs.logit_objective, np.zeros(10), args=(X, y), method="L-BFGS-B", options={"maxiter": 1000, "gtol": 1e-10})
    assert np.allclose(np.r_[model.intercept, model.coef], ref.x, atol=1e-4)
    w = np.r_[model.intercept, model.coef]
    assert cs.logit_objective(w, X, y) <= ref.fun + 1e-8
    assert model.base_rate == pytest.approx(y.mean())


def _logit_trades(n: int = 9000, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    at = pd.Timestamp("2019-03-01", tz="UTC") + pd.to_timedelta(np.sort(rng.uniform(0, 6 * 365 * 24, n)), unit="h")
    frame = pd.DataFrame({"at": at, "status": fh.EXECUTED, "symbol": "AUSDT"})
    frame["exit_at"] = frame["at"] + pd.to_timedelta(rng.uniform(1, 60, n), unit="h")
    for brick in cb.VOTERS:
        frame[f"vote_{brick}"] = (rng.random(n) < 0.5).astype(float)
    frame.loc[rng.random(n) < 0.05, "vote_BTC_HAUSSIER"] = np.nan
    frame["r_central"] = rng.normal(0, 1, n)
    return frame


def test_logit_purge_trains_only_on_exits_before_january_and_ignores_later_outcomes():
    trades = _logit_trades()
    mask = cs.train_rows(trades, 2022)
    exits = pd.to_datetime(trades["exit_at"], utc=True) + pd.Timedelta(minutes=1)
    assert (exits[mask] <= pd.Timestamp("2022-01-01", tz="UTC")).all()
    crossing = (trades["at"] < pd.Timestamp("2022-01-01", tz="UTC")) & ~mask
    assert crossing.any()                                                 # déclenchés en 2021, sortis en 2022 : purgés
    models, _ = cs.logit_models(trades)
    buy, prob = cs.logit_select(trades, models)
    later = trades.copy()
    late = pd.to_datetime(later["exit_at"], utc=True) + pd.Timedelta(minutes=1) > pd.Timestamp("2022-01-01", tz="UTC")
    later.loc[late, "r_central"] = -later.loc[late, "r_central"] + 3      # issues futures falsifiées
    buy2, prob2 = cs.logit_select(later, cs.logit_models(later)[0])
    in_2022 = (pd.to_datetime(trades["at"], utc=True).dt.year == 2022).to_numpy()
    assert np.array_equal(prob[in_2022], prob2[in_2022]) and np.array_equal(buy[in_2022], buy2[in_2022])
    assert not np.array_equal(prob[~in_2022 & (pd.to_datetime(trades["at"], utc=True).dt.year >= 2023).to_numpy()],
                              prob2[~in_2022 & (pd.to_datetime(trades["at"], utc=True).dt.year >= 2023).to_numpy()])


def test_logit_absent_indicator_is_decided_per_fold_and_small_folds_have_no_model():
    trades = _logit_trades()
    model, absent, info = cs.fit_fold(trades, 2023)
    assert absent == ("BTC_HAUSSIER",) and model is not None and "absente_BTC_HAUSSIER" in model.features
    trades["vote_BTC_HAUSSIER"] = trades["vote_BTC_HAUSSIER"].fillna(1.0)
    assert cs.fit_fold(trades, 2023)[1] == ()
    small = trades.iloc[:1500]
    assert cs.fit_fold(small, 2025)[0] is None                            # moins de 2 000 lignes : pli sans modèle
    buy, _ = cs.logit_select(small, {2025: (None, ())})
    assert not buy.any()


# --- Intervalles et décisions ------------------------------------------------------------------------------------------

def test_declared_levels_blocks_samples_and_bonferroni():
    assert cs.N_TRIALS_STEP == 10 and pytest.approx(0.995) == cs.LEVEL_R
    assert (cs.BLOCKS_EVENT, cs.BLOCKS_STATE, cs.SAMPLES_R, cs.SAMPLES_C, cs.SEED) == (28, 91, 10_000, 100_000, 20261007)
    assert cs.confirmation_plan(["VP_POC"], ["VOTE_3", "LOGIT"]) == (6, pytest.approx(1 - 0.05 / 6))
    with pytest.raises(cs.AlreadyRun):
        cs.confirmation_plan([], [])
    assert set(cs.STATE_RULES) == {"VOTE_2", "VOTE_3", "VOTE_4", "LOGIT"}


def _series(n: int, seed: int, shift: float = 0.0):
    rng = np.random.default_rng(seed)
    times = (pd.Timestamp("2019-01-01", tz="UTC") + pd.to_timedelta(np.sort(rng.uniform(0, 6 * 365, n)), unit="D")).to_numpy()
    return rng.normal(shift, 1, n), times


def test_nested_difference_uses_the_same_blocks_for_both_sets():
    values, times = _series(4000, 1)
    kept = np.random.default_rng(2).random(4000) < 0.4
    ci, blocks, diff = cs.nested_diff_ci(values, kept, times, block_days=91, samples=2000, level=0.995)
    assert blocks >= 10 and ci[0] < diff < ci[1] and diff == pytest.approx(values[kept].mean() - values.mean(), abs=1e-4)
    ci0, _, diff0 = cs.nested_diff_ci(values, np.ones(4000, bool), times, block_days=91, samples=500, level=0.995)
    assert ci0 == (0.0, 0.0) and diff0 == 0.0
    boosted = values + kept * 0.5
    assert cs.nested_diff_ci(boosted, kept, times, block_days=91, samples=2000, level=0.995)[0][0] > 0
    few = cs.nested_diff_ci(values[:30], kept[:30], times[:30], block_days=91, samples=100, level=0.995)
    assert few[0] is None


def test_block_and_cross_intervals():
    values, times = _series(3000, 3)
    pairs = np.random.default_rng(4).choice(["A", "B", "C", "D"], 3000)
    ci28, b28 = cs.block_ci(values, times, block_days=28, samples=2000, level=0.995)
    ci91, b91 = cs.block_ci(values, times, block_days=91, samples=2000, level=0.995)
    assert b28 > b91 >= 10 and ci28[0] < values.mean() < ci28[1]
    cross = cs.cross_ci(values, times, pairs, block_days=28, samples=2000, level=0.995)
    assert cross[0] < values.mean() < cross[1] and cross == cs.cross_ci(values, times, pairs, block_days=28,
                                                                          samples=2000, level=0.995)
    assert cs.cross_ci(values[:20], times[:20], pairs[:20], block_days=91, samples=100, level=0.995) is None
    # 100 000 tirages sur C : même méthode, niveau plus exigeant
    ci_c, _ = cs.block_ci(values, times, block_days=28, samples=cs.SAMPLES_C, level=1 - 0.05 / 6)
    assert ci_c[0] <= ci28[0] + 0.05


def _m(u, t, g=(0.1, 0.0, 0.3), n=500, cross=None):
    stat = lambda ci: {"n": n, "mean": (ci[0] + ci[1]) / 2 if ci else None, "ci": ci, "blocks": 30,  # noqa: E731
                       "cross_ci": cross}
    return {s: {"uniform": stat(u), "timing": stat(t), "gain": stat(g)} for s in ("central", "defavorable")}


def _g(ok=True, logit=False):
    row = {"top": ok, "pair": True, "year": True, "regular": True, "folds_ok": True}
    return {s: dict(row) for s in ("central", "defavorable")}


def test_excess_decisions_are_written_exactly_as_in_the_protocol(monkeypatch):
    monkeypatch.setattr(cs, "NULL_BIAS", {})
    assert cs.decide_excess("VP_POC", _m((0.05, 0.2), (0.02, 0.1)), _g())["decision"] == "PISTE"
    assert cs.decide_excess("VP_POC", _m((0.05, 0.2), (0.02, 0.1)), _g(False))["decision"] == "PISTE_FRAGILE"
    assert cs.decide_excess("VP_POC", _m((-0.1, 0.2), (-0.2, -0.01)), _g())["decision"] == "INVERSE"
    assert cs.decide_excess("VP_POC", _m((-0.1, 0.2), (-0.2, 0.1)), _g())["decision"] == "RIEN"
    assert cs.decide_excess("VP_POC", _m((0.05, 0.2), (0.02, 0.1), n=299), _g())["decision"] == "INSUFFISANT"
    assert cs.decide_excess("VP_POC", _m(None, (0.02, 0.1)), _g())["decision"] == "INSUFFISANT"
    assert cs.decide_excess("VOTE_3", _m((0.05, 0.2), (0.02, 0.1)), _g(),
                            extra={"difference_avec_REF_TOUS": False})["decision"] == "RIEN"
    assert cs.decide_excess("VOTE_3", _m((0.05, 0.2), (0.02, 0.1)), _g(),
                            extra={"difference_avec_REF_TOUS": None})["decision"] == "INSUFFISANT"
    assert cs.decide_excess("VP_POC", _m((0.05, 0.2), (0.02, 0.1)), _g(), confirmation=True)["decision"] == "CONFIRMEE"
    assert cs.decide_excess("VP_POC", _m((0.05, 0.2), (0.02, 0.1)), _g(False), confirmation=True)["decision"] == "NON_CONFIRMEE"
    assert cs.decide_excess("VOTE_2", _m((0.05, 0.2), (0.02, 0.1)), _g(), confirmation=True,
                            extra={"sans_TRENDLINE": False})["decision"] == "NON_CONFIRMEE"
    out = cs.decide_excess("VP_POC", _m((0.05, 0.2), (0.02, 0.1), cross=(-0.01, 0.3)), _g())
    assert out["decision"] == "PISTE" and out["mention"] == "fragile au tirage par paires"
    # règle de repli : la borne basse doit dépasser le biais maximal mesuré
    monkeypatch.setattr(cs, "NULL_BIAS", {"VP_POC": {"timing": 0.03}})
    assert cs.decide_excess("VP_POC", _m((0.05, 0.2), (0.02, 0.1)), _g())["decision"] == "RIEN"
    logit = _g()
    logit["central"]["folds_ok"] = False
    assert cs.decide_excess("LOGIT", _m((0.05, 0.2), (0.02, 0.1)), logit)["decision"] == "INSUFFISANT"


def test_gain_decisions():
    assert cs.decide_gain(_m((0, 1), (0, 1), g=(0.01, 0.2))) == "GAIN_DEMONTRE"
    assert cs.decide_gain(_m((0, 1), (0, 1), g=(-0.3, -0.01))) == "PERTE_DEMONTREE"
    assert cs.decide_gain(_m((0, 1), (0, 1), g=(-0.3, 0.01))) == "GAIN_NON_DEMONTRE"
    assert cs.decide_gain(_m((0, 1), (0, 1), g=(0.1, 0.2), n=29)) == "INSUFFISANT"


def test_guards_top_share_concentration_and_regularity():
    rng = np.random.default_rng(5)
    n = 700
    years = rng.choice(range(2019, 2026), n)
    part = pd.DataFrame({"texcess_central": rng.normal(0.1, 0.5, n), "year": years,
                         "symbol": rng.choice([f"P{k}" for k in range(10)], n)})
    g = cs.guards(part, "central", logit=False)
    assert g["top"] and g["pair"] and g["year"] and g["regular"]
    spike = part.copy()
    spike.loc[:6, "texcess_central"] = 1000.0                             # le meilleur 1 % porte tout
    spike.loc[7:, "texcess_central"] = -0.1
    assert not cs.guards(spike, "central", logit=False)["top"]
    one = part.copy()
    one.loc[one["symbol"] == "P0", "texcess_central"] += 5
    assert not cs.guards(one, "central", logit=False)["pair"]
    weak = part.copy()
    weak.loc[weak["year"] >= 2022, "texcess_central"] = -0.2
    assert cs.guards(weak, "central", logit=False)["positive_years"] == 3 and not cs.guards(weak, "central", logit=False)["regular"]
    folds = part[part["year"] >= 2021].copy()
    folds.loc[folds["year"] == 2021, "year"] = 2021
    gl = cs.guards(folds.iloc[:350], "central", logit=True)
    assert gl["folds_ok"] == (len(gl["folds_counted"]) >= 4)


def test_one_position_mask_by_pair_and_rule():
    trades = pd.DataFrame({"symbol": ["A", "A", "B", "A"], "status": fh.EXECUTED,
                           "at": pd.to_datetime(["2021-01-01 00:00", "2021-01-01 05:00", "2021-01-01 05:00",
                                                 "2021-01-02 00:00"], utc=True),
                           "exit_at": pd.to_datetime(["2021-01-01 10:00", "2021-01-01 06:00", "2021-01-01 06:00",
                                                      "2021-01-02 01:00"], utc=True)})
    assert list(cs.one_position_mask(trades, np.ones(4, bool))) == [True, False, True, True]
    assert list(cs.one_position_mask(trades, np.array([False, True, True, True]))) == [False, True, True, True]


def test_runs_refuse_without_controls_and_twice(settings, monkeypatch):
    monkeypatch.setattr(cs, "code_state", lambda: "abc")
    monkeypatch.setattr(cs, "CONTROLS_DATE", None)
    with pytest.raises(cs.NotReady):
        cs.run_bricks(settings, now=pd.Timestamp("2026-10-07", tz="UTC").to_pydatetime())
    monkeypatch.setattr(cs, "CONTROLS_DATE", "2026-10-07")
    with pytest.raises(cs.NotReady):                                      # relecture leak-auditor non inscrite
        cs.run_bricks(settings, now=pd.Timestamp("2026-10-07", tz="UTC").to_pydatetime())
    monkeypatch.setattr(cs, "CODE_REVIEW", "test")
    monkeypatch.setattr(cs, "code_state", lambda: "abc+DIRTY")
    from crypto_signal_intelligence.research.factors import DirtyCode
    with pytest.raises(DirtyCode):
        cs.run_bricks(settings, now=pd.Timestamp("2026-10-07", tz="UTC").to_pydatetime())
    monkeypatch.setattr(cs, "code_state", lambda: "abc")
    with pytest.raises(cs.NotReady):                                      # « test » n'est pas un commit relu
        cs.run_bricks(settings, now=pd.Timestamp("2026-10-07", tz="UTC").to_pydatetime())
    monkeypatch.setattr(cs, "require_clean_and_reviewed", lambda state, **k: None)
    with pytest.raises(cs.AlreadyRun):                                    # étape 2 avant l'étape 1 : refusée
        cs.run_votes(settings, now=pd.Timestamp("2026-10-07", tz="UTC").to_pydatetime())


def test_counts_simulate_nothing_and_cover_rules_years_and_cells(settings, monkeypatch):
    frames = {"AUSDT": hourly(130, 21, start=pd.Timestamp("2019-01-01", tz="UTC")),
              "BTCUSDT": hourly(130, 22, gaps=False, start=pd.Timestamp("2019-01-01", tz="UTC"))}
    monkeypatch.setattr(cs, "_load_pair", lambda s, symbol, end: frames.get(symbol))
    monkeypatch.setattr(cs.fh, "play", lambda *a, **k: pytest.fail("aucune simulation dans les comptages"))
    out = cs.run_counts(settings, symbols=["AUSDT", "ZZZUSDT"], workers=1, check_minutes=False)
    total = out["total"]
    assert out["missing"] == ["ZZZUSDT"]
    assert set(total["rules_by_year"]) == {"REF_TOUS", "VOTE_2", "VOTE_3", "VOTE_4", *cb.NEW_BRICKS}
    assert sum(total["rules_by_year"]["REF_TOUS"].values()) == total["matched"]["REF_TOUS"]["triggers"] > 0
    assert sum(total["rules_by_year"]["VOTE_4"].values()) <= sum(total["rules_by_year"]["VOTE_2"].values())
    assert sum(total["candidates"].values()) == total["hours"]
    assert "r_central" not in str(out)


# --- Étape 3 (voie A) : sans aucune donnée réelle -----------------------------------------------------------------

from crypto_signal_intelligence.research import combinations_telegram as ct  # noqa: E402


def _sig(at: str, group: str = "A", symbol: str = "ABCUSDT", entry: float = 100.0) -> ct.Signal:
    return ct.Signal(pd.Timestamp(at, tz="UTC"), group, ct.OK, symbol=symbol, entries=(entry,), stop=95.0,
                     targets=(104.0, 108.0), message_id=at)


def test_telegram_duplicates_by_group_first_then_across_groups():
    signals = [_sig("2026-05-01 10:00"), _sig("2026-05-02 09:00"), _sig("2026-05-02 11:00", group="B", entry=101.0),
               _sig("2026-05-02 12:00", group="C", entry=99.0), _sig("2026-05-03 12:00", group="C", entry=98.0)]
    ct.dedupe(signals)
    assert [s.status for s in signals] == [ct.OK, ct.DUP_GROUP, ct.OK, ct.DUP_CROSS, ct.OK]
    # l'ordre déclaré compte : le doublon du groupe A (retiré d'abord) ne masque pas le signal du groupe B


def test_telegram_exclusion_is_by_date_with_the_fixed_cutoff():
    signals = [_sig("2026-09-06 00:00"), _sig("2026-09-06 00:00:01", symbol="XYZUSDT"), _sig("2026-04-01")]
    ct.apply_cutoff(signals)
    assert [s.status for s in signals] == [ct.OK, ct.LATE, ct.OK]
    assert pd.Timestamp("2026-09-06", tz="UTC") == ct.LAST_RECEPTION


def test_telegram_split_keeps_30_percent_most_recent_per_group():
    signals = [_sig(f"2026-05-{d:02d}", group="A") for d in range(1, 11)] + \
              [_sig(f"2026-05-{d:02d}", group="B") for d in range(1, 4)] + [_sig(f"2026-06-{d:02d}", group="C") for d in range(1, 5)]
    ct.split(signals)
    parts = {g: [s.part for s in signals if s.group == g] for g in "ABC"}
    assert parts["A"] == ["etude"] * 7 + ["ecart"] * 3
    assert parts["B"] == ["etude"] * 3 and parts["C"] == ["etude"] * 3 + ["ecart"]


def test_telegram_useful_cells_and_placebo():
    groups = np.array(["A"] * 4 + ["B"] * 3 + ["C"] + ["D"] * 2, dtype=object)
    months = np.array(["2026-05"] * 10, dtype=object)
    keep = np.array([1, 0, 1, 0, 1, 1, 1, 1, 0, 0], dtype=bool)
    useful = ct.useful_cells(groups, months, keep)
    assert list(useful) == [True] * 4 + [False] * 6          # B : tout gardé ; C : un seul signal ; D : rien gardé
    rng = np.random.default_rng(0)
    r = rng.normal(0, 1, 400)
    cells = np.repeat(np.arange(40), 10).astype(str)
    best = np.zeros(400, dtype=bool)
    for c in range(40):
        idx = np.arange(c * 10, c * 10 + 10)
        best[idx[np.argsort(r[idx])[-4:]]] = True                # le filtre garde les meilleurs de chaque case
    stat, p = ct.placebo_p(r, best, cells)
    assert p == pytest.approx(1 / 10_001) and stat == pytest.approx(r[best].mean(), abs=1e-6)
    random_keep = rng.random(400) < 0.4
    p_random = ct.placebo_p(r, random_keep, cells)[1]
    assert 0.02 < p_random < 0.98 and p_random == ct.placebo_p(r, random_keep, cells)[1]


def test_telegram_decisions_and_minimums():
    study = {"useful_signals": 150, "useful_kept": 50, "kept_days": 20, "p": 0.01}
    holdout = {"useful_signals": 60, "useful_kept": 25, "p": 0.04}
    assert ct.decide(study, holdout) == "FILTRE_CONFIRME"
    assert ct.decide(study, holdout | {"p": 0.2}) == "NON_CONFIRME"
    assert ct.decide(study, holdout | {"useful_signals": 39}) == "INSUFFISANT"
    assert ct.decide(study, holdout | {"useful_kept": 19}) == "INSUFFISANT"
    assert ct.decide(study | {"useful_signals": 99}, holdout) == "INSUFFISANT"
    assert ct.decide(study | {"useful_kept": 29}, holdout) == "INSUFFISANT"
    assert ct.decide(study | {"kept_days": 9}, holdout) == "INSUFFISANT"
    assert ct.decide(study | {"p": 0.98}, holdout) == "FILTRE_NEFASTE"
    assert ct.decide(study | {"p": 0.3}, holdout) == "RIEN"
    assert ct.decide(study | {"p": 0.025}, holdout) == "FILTRE_CONFIRME"          # p ≤ 0,05/2 inclus


def test_telegram_refusals_use_available_at():
    times = pd.Timestamp("2026-05-01", tz="UTC") + pd.to_timedelta(np.arange(20) * 15, unit="min")
    bars = pd.DataFrame({"open_time": times, "close": 100.0})
    bars["available_at"] = bars["open_time"] + pd.Timedelta(minutes=15, seconds=2)
    bars.loc[9, "close"] = 104.5                                         # bougie close à 02:30, disponible à 02:30:02
    signal = _sig("2026-05-01 02:30:01")
    assert ct.refuse(signal, bars, 3.0) == (ct.OK, "")                    # la bougie de 02:15 n'est pas encore disponible
    assert ct.refuse(_sig("2026-05-01 02:30:03"), bars, 3.0)[0] == ct.PLAYED
    assert ct.refuse(_sig("2026-05-01 02:30:03", entry=110.0), bars.assign(close=100.0), 3.0)[0] == ct.STALE
    assert ct.refuse(_sig("2026-05-01 09:00"), bars, 3.0)[0] == ct.NO_DATA


def test_telegram_bricks_use_only_available_bars_and_the_last_available_profile():
    h1 = hourly(60, 31, start=pd.Timestamp("2026-01-01", tz="UTC"))
    btc = cb.btc_daily(btc_frame(60, 32))
    latency = pd.Timedelta(seconds=2)
    for at in ("2026-02-10 00:00:01", "2026-02-10 00:00:03", "2026-02-11 13:30", "2026-02-20 07:00:02"):
        signal = _sig(at, entry=float(h1["close"].iloc[-1]))
        signal.stop = signal.entry * 0.97
        received = signal.received
        full = ct.bricks_at(signal, h1, btc, latency=latency)
        known = ct.bricks_at(signal, h1[h1["available_at"] <= received], btc, latency=latency)
        fake = ct.bricks_at(signal, _falsify(h1, received.floor("h"), 3), btc, latency=latency)
        assert full == known == fake and not any(math.isnan(v) for v in full.values())
    signal = _sig("2026-02-10 00:00:01")
    day = pd.Timestamp("2026-02-10", tz="UTC")
    prepared = cb.prepare(h1)
    grid = cb.Grid.of(prepared)
    _, val_d, _, _ = cb.profile_window(prepared, grid, day)
    _, val_prev, _, _ = cb.profile_window(prepared, grid, day - pd.Timedelta(days=1))
    signal.entries = ((val_d + val_prev) / 2,)
    signal.stop = signal.entry * 0.97
    good = ct.bricks_at(signal, h1, btc, latency=latency)["PROFIL"]
    leak = ct.bricks_at(signal, h1, btc, latency=latency, mutation="profil_courant")["PROFIL"]
    assert good == float(signal.entry >= val_prev) and leak == float(signal.entry >= val_d) and good != leak


def test_telegram_unfilled_signal_is_zero_r(settings):
    times = pd.Timestamp("2026-05-01", tz="UTC") + pd.to_timedelta(np.arange(3000) * 15, unit="min")
    m15 = pd.DataFrame({"open_time": times, "open": 120.0, "high": 121.0, "low": 119.0, "close": 120.0})
    signal = _sig("2026-05-01 00:00")
    ct.measure_signal(signal, m15, settings)
    assert signal.r == {"central": 0.0, "defavorable": 0.0} and signal.outcome == "UNFILLED"


def test_telegram_message_counts_read_no_prices(settings, monkeypatch, tmp_path):
    import json as _json
    root = tmp_path / "imports" / "telegram"
    text = "#ABC/USDT\nEntry1: 100\nTP1: 104\nStop: 95"
    for k, name in enumerate(ct.EXPORTS):
        folder = root / name
        folder.mkdir(parents=True)
        messages = [{"id": i + 1, "type": "message", "date_unixtime": str(int(pd.Timestamp(f"2026-0{4 + k}-0{i + 1}",
                     tz="UTC").timestamp())), "text": text.replace("ABC", f"AB{k}{i}")} for i in range(3)]
        (folder / "result.json").write_text(_json.dumps({"name": f"G{k}", "messages": messages}), encoding="utf-8")
    monkeypatch.setattr(settings, "root", tmp_path)
    monkeypatch.setattr(ct, "load_bars", lambda *a, **k: pytest.fail("aucun prix lu pour les comptages"))
    out = ct.message_counts(settings)
    assert out["messages"] == 9 and out["status"] == {"OK": 9} and out["groups"] == 3
    assert out["parts_before_refusals"]["ecart"]["signals"] == 0           # groupes de 3 signaux : tout en étude


def test_telegram_run_requires_the_final_test_flag(settings, monkeypatch):
    from crypto_signal_intelligence.research.factors import DirtyCode
    from crypto_signal_intelligence.research.protocol import FinalTestLocked
    monkeypatch.setattr(ct, "code_state", lambda: "abc+DIRTY")
    with pytest.raises(DirtyCode):                                        # jamais de code non commité, aucune option
        ct.run(settings, now=pd.Timestamp("2026-10-07", tz="UTC").to_pydatetime(), allow_final_test=True)
    monkeypatch.setattr(ct, "code_state", lambda: "abc")
    with pytest.raises(cs.NotReady):
        ct.run(settings, now=pd.Timestamp("2026-10-07", tz="UTC").to_pydatetime(), allow_final_test=True)
    monkeypatch.setattr(cs, "require_clean_and_reviewed", lambda state, **k: None)
    with pytest.raises(FinalTestLocked):
        ct.run(settings, now=pd.Timestamp("2026-10-07", tz="UTC").to_pydatetime(), allow_final_test=False)
    with pytest.raises(FinalTestLocked):
        ct.download_bars(settings, ["ABCUSDT"], allow_final_test=False)


# --- Contrôles synthétiques du § 1.8 : le même code de bout en bout ------------------------------------------------

def test_null_control_pipeline_and_judgement_on_a_small_sample():
    from crypto_signal_intelligence.research import combinations_controls as cc
    trades = cc.walk_trades(cc.synthetic_walk("facteur_commun", 5, years=1), witnesses=True).assign(walk=5)
    assert trades["witness_1"].any() and trades["witness_2"].any() and trades["trigger"].any()
    stats = cc.null_statistics(trades)
    assert {"TEMOIN_1", "TEMOIN_2", *cc.NULL_RULES} <= set(stats)
    verdict = cc.judge("facteur_commun", stats)
    assert verdict["VOTE_3"]["uniforme"] == "NON_JUGE" and verdict["VP_POC"]["uniforme"] in ("PASSE", "ECHEC")
    results = {"facteur_commun": {"stats": stats, "verdict": verdict}}
    bias = cc.fallback_bias(results)
    for name, entry in bias.items():
        assert all(v >= 0 for v in entry.values()) and name in cc.NULL_RULES


def test_single_runs_record_their_trials_and_refuse_a_second_run(settings, monkeypatch):
    from crypto_signal_intelligence.research import combinations_controls as cc
    from crypto_signal_intelligence.research.experiments import ExperimentRegistry
    trades = cc.walk_trades(cc.synthetic_walk("constante", 6, years=1))
    trades = trades[trades["trigger"]].reset_index(drop=True)
    monkeypatch.setattr(cs, "collect_trades", lambda *a, **k: (trades.copy(), {"1h/S6USDT": "x"}, []))
    monkeypatch.setattr(cs, "code_state", lambda: "abc")
    monkeypatch.setattr(cs, "CONTROLS_DATE", "2026-10-07")
    monkeypatch.setattr(cs, "require_clean_and_reviewed", lambda state, **k: None)
    registry = ExperimentRegistry(settings.experiments_db)
    before = registry.program_trials()
    now = pd.Timestamp("2026-10-07", tz="UTC").to_pydatetime()
    one = cs.run_bricks(settings, now=now, workers=1)
    assert one["n_trials"] == 10 and registry.program_trials() == before + 10
    assert set(one["rows"]) == set(cb.NEW_BRICKS)
    assert all(r["excess"]["decision"] in ("PISTE", "PISTE_FRAGILE", "INVERSE", "INSUFFISANT", "RIEN")
               for r in one["rows"].values())
    with pytest.raises(cs.AlreadyRun):
        cs.run_bricks(settings, now=now, workers=1)
    desc = one["rows"]["VP_POC"]["conditions"]["descriptif"]
    assert {"sans_meilleurs", "objectifs", "issues", "par_paire", "par_case", "par_tranche_horaire"} <= set(desc)
    assert sum(r["part_declencheurs"] for r in desc["par_tranche_horaire"].values()) == pytest.approx(1, abs=1e-3)
    assert "overview" in one and one["overview"]["declencheurs"] == len(trades)
    two = cs.run_votes(settings, now=now, workers=1)
    assert set(two["rows"]) == set(cs.STEP2_RULES) and registry.program_trials() == before + 20
    assert two["rows"]["LOGIT"]["excess"]["decision"] == "INSUFFISANT"     # une seule année : aucun pli
    saved = pd.read_parquet(settings.reports_dir / two["run_id"] / "trades.parquet")
    assert len(saved) == len(trades) and "logit_buy" in saved
    with pytest.raises(cs.AlreadyRun):                                    # aucune PISTE : pas d'exécution sur C
        cs.run_confirmation(settings, now=now, workers=1)


# --- Corrections de la relecture leak-auditor (F1 à F5) ---------------------------------------------------------------

def test_dirty_code_is_always_refused_without_any_override(settings, monkeypatch):
    import inspect

    from crypto_signal_intelligence.research.factors import DirtyCode
    for runner in (cs.run_bricks, cs.run_votes, cs.run_confirmation, ct.run):
        assert "allow_dirty" not in inspect.signature(runner).parameters
    monkeypatch.setattr(cs, "code_state", lambda: "x+DIRTY")
    monkeypatch.setattr(cs, "CODE_REVIEW", "0" * 40)
    with pytest.raises(DirtyCode):
        cs.run_bricks(settings, now=pd.Timestamp("2026-10-07", tz="UTC").to_pydatetime())


def _git(root, *args):
    import subprocess
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"})
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout.strip()


def test_execution_requires_the_reviewed_commit_unchanged(tmp_path):
    study = tmp_path / "src" / "crypto_signal_intelligence" / "research" / "combinations.py"
    imported = tmp_path / "src" / "crypto_signal_intelligence" / "patterns" / "volume.py"
    other = tmp_path / "README.md"
    for path in (study, imported, other):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    reviewed = _git(tmp_path, "commit", "-qm", "relu")
    cs.require_clean_and_reviewed("abc", root=tmp_path, review=reviewed)          # identique : accepté
    other.write_text("autre\n")
    _git(tmp_path, "commit", "-qam", "doc")
    cs.require_clean_and_reviewed("abc", root=tmp_path, review=reviewed)          # fichier hors étude : accepté
    for path in (imported, study):
        path.write_text("x = 2\n")
        _git(tmp_path, "commit", "-qam", "modif")
        with pytest.raises(cs.NotReady):                                          # source modifiée après la relecture
            cs.require_clean_and_reviewed("abc", root=tmp_path, review=reviewed)
        path.write_text("x = 1\n")
        _git(tmp_path, "commit", "-qam", "retour")
    with pytest.raises(cs.NotReady):
        cs.require_clean_and_reviewed("abc", root=tmp_path, review="f" * 40)      # commit inconnu
    with pytest.raises(cs.NotReady):
        cs.require_clean_and_reviewed("abc", root=tmp_path, review=None)          # CODE_REVIEW vide (défaut)
    assert cs.CODE_REVIEW is None


def test_cross_group_duplicates_only_between_different_groups():
    same = [_sig("2026-05-02 10:00", group="A"), _sig("2026-05-02 15:00", group="A", entry=97.0)]
    ct.dedupe(same)
    assert [s.status for s in same] == [ct.OK, ct.OK]                    # même groupe, autres niveaux : gardés
    aba = [_sig("2026-05-02 10:00", group="A"), _sig("2026-05-02 11:00", group="B", entry=101.0),
           _sig("2026-05-02 12:00", group="A", entry=97.0)]
    ct.dedupe(aba)
    assert [s.status for s in aba] == [ct.OK, ct.DUP_CROSS, ct.OK]


def _telegram_items():
    from datetime import UTC, datetime

    from crypto_signal_intelligence.external.audit import HistoryItem
    return [HistoryItem(text=f"#AB{k}/USDT\nEntry1: 100\nTP1: 104\nStop: 95", group="G",
                        received_at=datetime(2026, 5, 1 + k, tzinfo=UTC), message_id=str(k)) for k in range(5)]


def _telegram_fakes(monkeypatch, *, fail_in: str):
    monkeypatch.setattr(ct, "code_state", lambda: "abc")
    monkeypatch.setattr(cs, "require_clean_and_reviewed", lambda state, **k: None)
    monkeypatch.setattr(ct, "read_exports", lambda root: _telegram_items())
    monkeypatch.setattr(ct, "load_bars", lambda *a, **k: btc_frame(10, 1))
    monkeypatch.setattr(ct, "completeness", lambda signals, bars: {s.symbol: {"ok": True} for s in signals})
    monkeypatch.setattr(ct, "refuse", lambda *a, **k: (ct.OK, ""))

    def bricks(*a, **k):
        if fail_in == "briques":
            raise RuntimeError("panne simulée avant les comptages")
        return dict.fromkeys(ct.BRICKS, 1.0)

    def measure(signal, *a, **k):
        if fail_in == "R":
            raise RuntimeError("panne simulée dans measure_signal")
        signal.r = {"central": 0.0, "defavorable": 0.0}
    monkeypatch.setattr(ct, "bricks_at", bricks)
    monkeypatch.setattr(ct, "measure_signal", measure)


def test_voie_a_failure_after_counts_cannot_be_retried(settings, monkeypatch):
    from crypto_signal_intelligence.research.experiments import ExperimentRegistry
    now = pd.Timestamp("2026-10-07", tz="UTC").to_pydatetime()
    _telegram_fakes(monkeypatch, fail_in="R")
    with pytest.raises(RuntimeError, match="measure_signal"):
        ct.run(settings, now=now, allow_final_test=True)
    registry = ExperimentRegistry(settings.experiments_db)
    with registry.connect() as db:
        status, metrics = db.execute("SELECT status, metrics FROM runs WHERE kind=?", (ct.KIND,)).fetchone()
    import json as _json
    metrics = _json.loads(metrics)
    assert status == "FAILED" and metrics["stage"] == "comptages écrits, calcul des R" and metrics["counts_written"]
    assert registry.program_trials("FINAL_TEST") == 2
    _telegram_fakes(monkeypatch, fail_in="")
    with pytest.raises(ct.AlreadyConsulted):
        ct.run(settings, now=now, allow_final_test=True, retry=True)


def test_voie_a_failure_before_counts_allows_one_retry_without_new_trials(settings, monkeypatch):
    from crypto_signal_intelligence.research.experiments import ExperimentRegistry
    now = pd.Timestamp("2026-10-07", tz="UTC").to_pydatetime()
    _telegram_fakes(monkeypatch, fail_in="briques")
    with pytest.raises(RuntimeError, match="avant les comptages"):
        ct.run(settings, now=now, allow_final_test=True)
    registry = ExperimentRegistry(settings.experiments_db)
    _telegram_fakes(monkeypatch, fail_in="")
    with pytest.raises(ct.AlreadyConsulted):                              # pas de reprise sans --reprise
        ct.run(settings, now=now, allow_final_test=True)
    payload = ct.run(settings, now=now, allow_final_test=True, retry=True)
    assert payload["n_trials"] == 0 and payload["retry"]
    assert registry.program_trials("FINAL_TEST") == 2                    # 8 → 10 dans le vrai registre, jamais plus
    with pytest.raises(ct.AlreadyConsulted):
        ct.run(settings, now=now, allow_final_test=True, retry=True)      # une seule reprise


def test_newton_must_converge():
    rng = np.random.default_rng(3)
    X = (rng.random((500, 3)) < 0.5).astype(float)
    y = (rng.random(500) < 0.4).astype(float)
    with pytest.raises(cs.LogitNotConverged):
        cs.fit_logit(X, y, ("a", "b", "c"), max_iter=1)
    assert cs.fit_logit(X, y, ("a", "b", "c")).iterations < cs.LOGIT_MAX_ITER


def test_pit_flags_for_confirmation_pairs():
    rows = [{"at": pd.Timestamp("2021-03-05", tz="UTC")}, {"at": pd.Timestamp("2022-07-01", tz="UTC")}]
    cs.add_pit_flags(rows, {"2022-07"}, last_hour=pd.Timestamp("2023-01-01", tz="UTC"),
                     end=pd.Timestamp("2025-06-30", tz="UTC"))
    assert [(r["top40"], r["after_first_top40"], r["delisted_pair"]) for r in rows] == [(False, False, True), (True, True, True)]



def test_inscribing_the_review_touches_no_watched_file(tmp_path):
    """Relecture du 2026-10-08 : l'inscription du commit relu (combinations_review.py) ne doit pas modifier un fichier
    surveillé ; un module de mesure ajouté à la liste (data/seconds.py) est bien surveillé."""
    base = tmp_path / "src" / "crypto_signal_intelligence"
    study = base / "research" / "combinations_study.py"
    review = base / "research" / "combinations_review.py"
    seconds = base / "data" / "seconds.py"
    toml = tmp_path / "config" / "default.toml"
    for path in (study, review, seconds, toml):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    reviewed = _git(tmp_path, "commit", "-qm", "relu")
    review.write_text(f'CODE_REVIEW = "{reviewed}"\n')
    _git(tmp_path, "commit", "-qam", "inscription")
    cs.require_clean_and_reviewed("abc", root=tmp_path, review=reviewed)          # inscription seule : acceptée
    for path in (seconds, toml):
        path.write_text("x = 2\n")
        _git(tmp_path, "commit", "-qam", "modif")
        with pytest.raises(cs.NotReady):
            cs.require_clean_and_reviewed("abc", root=tmp_path, review=reviewed)
        path.write_text("x = 1\n")
        _git(tmp_path, "commit", "-qam", "retour")


def test_the_effective_configuration_must_match_the_review(settings, monkeypatch, tmp_path):
    reviewed = _git_repo_with_one_commit(tmp_path)
    monkeypatch.setattr(cs, "CONFIG_FINGERPRINT", None)
    with pytest.raises(cs.NotReady, match="CONFIG_FINGERPRINT"):
        cs.require_clean_and_reviewed("abc", root=tmp_path, review=reviewed, settings=settings)
    monkeypatch.setattr(cs, "CONFIG_FINGERPRINT", cs.config_fingerprint(settings))
    cs.require_clean_and_reviewed("abc", root=tmp_path, review=reviewed, settings=settings)
    changed = settings.model_copy(update={"data": settings.data.model_copy(update={"assumed_availability_latency_seconds": 99.0})})
    with pytest.raises(cs.NotReady, match="configuration effective"):
        cs.require_clean_and_reviewed("abc", root=tmp_path, review=reviewed, settings=changed)


def _git_repo_with_one_commit(root):
    (root / "README.md").write_text("x\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    return _git(root, "commit", "-qm", "relu")
