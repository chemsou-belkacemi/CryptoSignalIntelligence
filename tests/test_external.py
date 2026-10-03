"""Signaux externes : formats Telegram, taux de base, évaluation et résolution (valeurs synthétiques)."""
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.config import CostScenario
from crypto_signal_intelligence.data.schema import RAW_COLUMNS, normalize
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.external import base_rate as br
from crypto_signal_intelligence.external.evaluate import evaluate
from crypto_signal_intelligence.external.parser import parse
from crypto_signal_intelligence.external.registry import ExternalSignalRegistry, replay, resolve_pending
from crypto_signal_intelligence.features import indicators as ind

from .conftest import canonical

FREE = CostScenario(fee_bps=0, slippage_bps=0, half_spread_bps=0)


def structured(pair, entries, targets, stop, platform="Binance"):
    """Message réel du modèle « structured » (même fixture que BinanceSpotManager)."""
    return (f"📈 Trader/ Suhaib AlMashhadani\n✨ بسم الله توكلت على الله ✨\n"
            f"💎 PAIR: {pair}\n🔶 ENTRY ZONE:\n"
            + "\n".join(f"✨ ENTRY {i}: {p}" for i, p in enumerate(entries, 1))
            + "\n🎯 TARGETS:\n"
            + "\n───────────────────\n".join(f"{i}️⃣ T{i}: {p} (+2.08%)" for i, p in enumerate(targets, 1))
            + f"\n🛑 SL: {stop} (1h) (-0.81%)\n📅 Date: Monday - 2026-09-28\n"
              "⏰IndicatorTime :- 06:25 GMT+3\n"
            + f"🟠️ Platform: {platform}\n☪️ الحكم الشرعي: مباح ✅")


BICO = structured("BICO/USDT", [.02162, .02143], [.02207, .02225, .02244, .02264, .02292, .02327], .02135)
ABK = """🚨 ABK SIGNAL ALERT 🚨
🔰 Coin: LSKUSDT 🔰
▫️ Entry Zone: 0.3689 – 0.3673
🎯 Target 1 → 0.3765 [+2.06%]
🎯 Target 2 → 0.3835 [+3.96%]
🎯 Target 3 → 0.3961 [+7.37%]
🔴 Stop Loss: 0.3654 (15min) [-0.73%]
💡 Tip: إدارة رأس المال تسبق البحث عن الربح"""
SIMPLE = "PAIR: BTC/USDT\nENTRY 1: 84000\nT1: 90000\nSL: 80000"
GALA = """👑AL-MAHWASHI VIP👑
───────────────────
#GALA/USDT
───────────────────
📍 Entry1: 0.002116
───────────────────
🎯 TP1: 0.002210 (4.44%)
🎯 TP2: 0.002330 (10.11%)
───────────────────
🛑 Stop: 0.001990"""

# Message réel du 2026-10-02 (INCRYPTO) : gras Telegram « *PAIR:* » et « SELL » derrière chaque objectif = vendre
# à l'objectif (prise de bénéfice), pas une vente à découvert. BinanceSpotManager le lisait, CSI le refusait.
INCRYPTO = """📈 *INCRYPTO TIME ANALYSIS INDICATOR*
YASMINA BOUZID INDICATOR
──────────────────────
✨ بسم الله توكلت على الله ✨
──────────────────────
💎 *PAIR:* MOVR/USDT
🔶 *ENTRY ZONE:*
✨ENTRY 1: 2.86
✨ENTRY 2: 2.74
──────────────────────
🎯 *TARGETS:*
1️⃣ T1: 2.9  📉 SELL (1.40%)
──────────────────────
2️⃣ T2: 2.94  📉 SELL (2.80%)
──────────────────────
3️⃣ T3: 3.02  📉 SELL (5.59%)
──────────────────────
4️⃣ T4: 3.2  📉 SELL(11.89%)
──────────────────────
5️⃣ T5: 3.45  📉 SELL(20.63%)
──────────────────────
6️⃣ T6: 3.83  📉 SELL (33.92%)
──────────────────────
7️⃣ T7: 4.28  📉 SELL(49.65%)
──────────────────────
🛑 *SL:* 2.7  (4h) (-3.57%)
──────────────────────
📅Date: Friday - 2026-10-02
⏰IndicatorTime :- 23:59 GMT+3
──────────────────────
👤IndicatorCeo:YASMINA BOUZID
──────────────────────
🟠 Platform: Binance
──────────────────────
☪️ *الحكم الشرعي:* مباح ✅"""

def test_parser_reads_the_four_group_formats():
    bico = parse(BICO)
    assert bico.ok and bico.template == "structured" and bico.symbol == "BICOUSDT"
    assert bico.entries == [.02162, .02143] and len(bico.targets) == 6 and bico.stop == .02135
    assert bico.published_at == "2026-09-28T03:25:00+00:00" and bico.stop_timeframe == "1h"
    abk = parse(ABK)
    assert abk.ok and abk.template == "abk" and abk.symbol == "LSKUSDT" and abk.entries == [0.3689, 0.3673]
    assert abk.targets == [0.3765, 0.3835, 0.3961] and abk.stop == 0.3654
    simple = parse(SIMPLE)
    assert simple.ok and simple.symbol == "BTCUSDT" and simple.entries == [84000] and simple.stop == 80000
    gala = parse(GALA)
    assert gala.ok and gala.template == "numbered" and gala.symbol == "GALAUSDT" and gala.stop == 0.00199
    plain = parse("BUY BTCUSDT\nENTRY PRICE: 84000\nTP: 90000\nSL: 80000")
    assert plain.ok and plain.template == "simple" and plain.direction == "BUY" and plain.symbol == "BTCUSDT"
    assert parse(SIMPLE).content_hash == parse(SIMPLE.lower()).content_hash


@pytest.mark.parametrize("text", [
    SIMPLE + "\nPAIR: ETHUSDT", SIMPLE.replace("84000", "84,000"), SIMPLE.replace("90000", "5%"),
    SIMPLE.replace("80000", "85000"), SIMPLE.replace("T1: 90000", "T1: 90000\nT2: 89000"),
    "SHORT BTC/USDT\nENTRY 1: 84000\nT1: 80000\nSL: 90000", SIMPLE + "\nLEVERAGE: 10x",
    SIMPLE + "\nPLATFORM: Bitget", "", "a" * 20001,
])
def test_parser_refuses_ambiguous_short_leveraged_or_foreign(text):
    assert parse(text).errors


def bars(rows, trend="BULL"):
    n = len(rows)
    o, h, low, c = map(list, zip(*rows, strict=True))
    times = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"open_time": times, "decision_time": times + pd.Timedelta(minutes=15),
                         "open": o, "high": h, "low": low, "close": c, "atr14": 1.0,
                         "ctx_trend": trend, "ctx_volatility": "NORMAL"})


FLAT = (100, 100.5, 99.5, 100)


def test_blind_limit_orders_follow_hand_built_scenarios():
    # Limite au prix (écart 0) : remplie à l'ouverture suivante (100), stop 98, cible 102.
    frame = bars([FLAT, (100, 103, 99.8, 102), (102, 102.5, 97, 98), (98, 98.5, 97.5, 98), (98, 98.5, 97.5, 98),
                  (98, 98.5, 97.5, 98)])
    blind = br.blind_limit_outcomes(frame, entry_offset=0.0, stop_atr=2, target_r=1, entry_window=1, horizon=2,
                                    costs=FREE)
    assert blind.outcome.tolist()[:2] == [br.TP, br.SL] and blind.r.tolist()[:2] == pytest.approx([1.0, -1.0])
    # Limite 1 % SOUS le prix : jamais atteinte sur des bougies plates → non remplie, hors statistiques.
    flat = bars([FLAT] * 6)
    unfilled = br.blind_limit_outcomes(flat, entry_offset=-0.01, stop_atr=2, target_r=1, entry_window=2, horizon=2,
                                       costs=FREE)
    assert len(unfilled.r) == 0 and unfilled.filled == 0 and unfilled.emitted == 4   # ordres 0 à 3 : fenêtre complète
    # Stop et cible dans la même bougie → stop (pessimiste), compté ambigu.
    same_bar = br.blind_limit_outcomes(bars([FLAT, FLAT, (100, 105, 97, 100), FLAT]), entry_offset=0.0, stop_atr=2,
                                       target_r=1, entry_window=1, horizon=2, costs=FREE)
    assert same_bar.outcome[0] == br.SL and same_bar.ambiguous[0]


def test_blind_limit_orders_are_resolved_exactly_like_replay():
    """Alignement taux de base ↔ résolution : même issue, même R, pour des ordres tirés au hasard."""
    candles = canonical(900, seed=11, vol=0.006)
    frame = candles[["open_time", "open", "high", "low", "close"]].assign(
        decision_time=candles["open_time"] + pd.Timedelta(minutes=15),
        atr14=ind.atr(candles["high"], candles["low"], candles["close"]))
    costs = settings_costs = FREE
    rng = np.random.default_rng(4)
    checked = 0
    for i in rng.choice(np.arange(20, 700), size=60, replace=False):
        offset, stop_atr, target_r = float(rng.uniform(-0.01, 0.004)), float(rng.uniform(0.6, 3)), float(rng.uniform(0.5, 3))
        mask = np.zeros(len(frame), dtype=bool)
        mask[i] = True
        blind = br.blind_limit_outcomes(frame, entry_offset=offset, stop_atr=stop_atr, target_r=target_r,
                                        entry_window=8, horizon=40, costs=costs, mask=mask)
        limit = float(frame["close"].iat[i]) * (1 + offset)
        stop = limit - stop_atr * float(frame["atr14"].iat[i])
        target = limit + target_r * (limit - stop)
        outcome, r, _ = replay(frame.iloc[i + 1:].reset_index(drop=True), entry=limit, stop=stop, target=target,
                               entry_window=8, max_hold=40, costs=settings_costs)
        names = {br.TP: "TP1_FIRST", br.SL: "SL_FIRST", br.TIMEOUT: "TIMEOUT"}
        if outcome in {"UNFILLED", "PENDING"}:
            assert len(blind.r) == 0, (i, outcome)
        else:
            assert names[int(blind.outcome[0])] == outcome and blind.r[0] == pytest.approx(r, abs=1e-4), (i, outcome)
            checked += 1
    assert checked >= 30


def test_base_rate_conditions_on_regime_then_falls_back():
    candles = canonical(3000, vol=0.006)
    frame = candles[["open_time", "open", "high", "low", "close"]].assign(
        decision_time=candles["open_time"] + pd.Timedelta(minutes=15),
        atr14=ind.atr(candles["high"], candles["low"], candles["close"]),
        ctx_trend=np.where(np.arange(3000) % 2 == 0, "BULL", "RANGE"), ctx_volatility="NORMAL")
    rate = br.base_rate(frame, stop_atr=1.5, target_r=2, horizon=8, costs=FREE, trend="BULL", volatility="NORMAL",
                        min_samples=50, seed=1, bootstrap_samples=200, entry_offset=0.0, entry_window=4)
    assert rate.regime_conditioned and rate.regime == "BULL/NORMAL" and 0 < rate.samples <= 1500
    assert rate.tp_first + rate.sl_first + rate.timeout == pytest.approx(1.0)
    assert rate.expectancy_r_ci95 is not None and rate.blocks >= 10 and rate.method == br.METHOD
    low, high = rate.expectancy_r_ci95
    assert low <= rate.expectancy_r <= high                 # l'intervalle encadre la moyenne AFFICHÉE
    assert rate.tp_first_ci95 is not None and 0 < rate.fill_rate <= 1
    fallback = br.base_rate(frame, stop_atr=1.5, target_r=2, horizon=8, costs=FREE, trend="BULL",
                            volatility="NORMAL", min_samples=100_000, seed=1, bootstrap_samples=200)
    assert not fallback.regime_conditioned and fallback.regime == "tous régimes" and fallback.samples > rate.samples


def test_day_block_interval_needs_ten_blocks_not_ten_horizons():
    """Avant : 10 blocs de 672 ENTRÉES (6 720) exigés → avis indéterminé par construction. Maintenant :
    10 blocs de jours calendaires."""
    times = pd.date_range("2024-01-01", periods=70 * 96, freq="15min", tz="UTC").to_numpy()
    values = np.random.default_rng(2).normal(0.05, 1, len(times))
    ci, blocks = br.day_block_ci95(values, times, block_days=7, samples=500, seed=1)
    assert blocks == 10 and ci is not None and ci[0] < values.mean() < ci[1]
    short_ci, short_blocks = br.day_block_ci95(values[: 60 * 96], times[: 60 * 96], block_days=7, samples=500, seed=1)
    assert short_ci is None and short_blocks == 9


def bars15(rows):
    return bars(rows)[["open_time", "open", "high", "low", "close"]]


def test_replay_follows_the_simulator_fill_rules():
    outcome, r, filled = replay(bars15([FLAT, (100, 104.5, 99.8, 104)]), entry=100, stop=98, target=104,
                                entry_window=2, max_hold=10, costs=FREE)
    assert (outcome, r) == ("TP1_FIRST", 2.0) and filled == pd.Timestamp("2024-01-01", tz="UTC")
    touching = bars15([(101, 102, 100, 101)] * 2)
    assert replay(touching, entry=100, stop=98, target=104, entry_window=3, max_hold=10, costs=FREE)[0] == "PENDING"
    assert replay(touching, entry=100, stop=98, target=104, entry_window=2, max_hold=10, costs=FREE)[0] == "UNFILLED"
    outcome, r, _ = replay(bars15([(101, 105, 99.9, 101), (100, 100.2, 97, 97)]), entry=100, stop=98, target=104,
                           entry_window=2, max_hold=10, costs=FREE)
    assert (outcome, r) == ("SL_FIRST", -1.0)   # TP dans la bougie de contact ignoré, stop ensuite
    flat = bars15([FLAT] * 5)
    assert replay(flat, entry=100, stop=98, target=104, entry_window=1, max_hold=3, costs=FREE)[:2] == ("TIMEOUT", 0.0)
    assert replay(flat, entry=100, stop=98, target=104, entry_window=1, max_hold=10, costs=FREE)[0] == "PENDING"
    # Stop au-dessus du prix de remplissage : déclenché aussitôt, au marché (pas « au prix du stop »).
    outcome, r, _ = replay(bars15([(99, 99.5, 98.5, 99), (99, 104.5, 98.9, 104)]), entry=100, stop=99.5, target=104,
                           entry_window=1, max_hold=10, costs=FREE)
    assert outcome == "SL_FIRST" and r == pytest.approx(0.0)       # acheté 99, stoppé aussitôt à 99, avant frais


def future_bars(after: pd.Timestamp, price: float, n: int, step_pct: float) -> pd.DataFrame:
    """Bougies 15m canoniques qui prolongent une série : hausse régulière de step_pct par bougie."""
    open_ms = int(after.timestamp() * 1000) + 900_000 * np.arange(1, n + 1, dtype="int64")
    closes = price * np.cumprod(np.full(n, 1 + step_pct))
    opens = np.r_[price, closes[:-1]]
    raw = pd.DataFrame({"open_time": open_ms, "open": opens, "high": closes * 1.0001, "low": opens * 0.9999,
                        "close": closes, "volume": 10.0, "close_time": open_ms + 899_999, "quote_volume": 1000.0,
                        "count": 10, "taker_buy_base": 5.0, "taker_buy_quote": 500.0, "ignore": 0})[RAW_COLUMNS]
    return normalize(raw, unit="ms", symbol="ETHUSDT", timeframe="15m", source="SYNTHETIC_FIXTURE",
                     source_version="test", now=pd.Timestamp("2030-01-01", tz="UTC").to_pydatetime(),
                     latency_seconds=2)


def test_evaluate_end_to_end_then_resolve_and_score_the_source(settings):
    store = CandleStore(settings.data_dir)
    setup = canonical(4000, symbol="ETHUSDT", seed=5)
    store.save(setup, "ETHUSDT", "15m")
    store.save(canonical(1000, "1h", symbol="ETHUSDT", seed=6), "ETHUSDT", "1h")
    store.save(canonical(1000, "1h", symbol="BTCUSDT", seed=7), "BTCUSDT", "1h")
    last = setup.iloc[-1]
    close = float(last["close"])
    atr = float(ind.atr(setup["high"], setup["low"], setup["close"]).iloc[-1])
    now = last["available_at"].to_pydatetime() + timedelta(minutes=1)

    def text(entry, stop, tp1, pair="ETH/USDT"):
        return f"PAIR: {pair}\nENTRY 1: {entry:.2f}\nT1: {tp1:.2f}\nT2: {tp1 * 1.01:.2f}\nSL: {stop:.2f}\nPLATFORM: Binance"

    entry = close * 1.001
    good = text(entry, entry - 1.5 * atr, entry + 3 * atr)
    ev = evaluate(settings, good, source="groupe test", now=now)
    assert ev.verdict in {"DEFAVORABLE", "INDETERMINE", "FAVORABLE"} and not ev.failed and ev.record_id
    assert ev.base_rate is not None and ev.base_rate.samples > 0 and ev.context["trend_1h"] in {"BULL", "BEAR", "RANGE"}
    stop, tp1 = entry - 1.5 * atr, entry + 3 * atr          # RR recalculé sur l'entrée obtenue (le close)
    assert ev.geometry["rr_gross"][0] == pytest.approx((tp1 - close) / (close - stop), abs=0.02)
    assert ev.source_stats is None
    again = evaluate(settings, good, source="groupe test", now=now, record=False)
    assert any("déjà évalué" in w for w in again.warnings) and again.source_stats["evaluated"] == 1
    late = evaluate(settings, text(close * 1.05, entry - 1.5 * atr, close * 1.10), source="groupe test", now=now)
    assert late.verdict == "DEFAVORABLE" and late.failed[0].label == "entrée par rapport au dernier prix"
    below = close * 0.999                                   # limite sous le prix : entrée obtenue = limite
    tight = evaluate(settings, text(below, below - 0.1 * atr, below + 0.4 * atr), source="groupe test", now=now)
    assert tight.verdict == "DEFAVORABLE" and tight.failed[0].label == "distance du stop"
    dead = evaluate(settings, text(entry, close * 1.0005, entry + 3 * atr), source="groupe test", now=now)
    assert dead.verdict == "REFUSE" and dead.failed[0].label == "signal encore valable"    # stop au-dessus du prix
    played = evaluate(settings, text(close * 0.98, close * 0.96, close * 0.995), source="groupe test", now=now)
    assert played.verdict == "REFUSE" and "déjà joué" in played.failed[0].detail           # TP1 déjà dépassé
    assert ev.geometry["entry_effective"] == pytest.approx(close) and ev.base_rate.method == br.METHOD
    stale = evaluate(settings, good, source="groupe test", now=now + timedelta(hours=3))
    assert stale.verdict == "REFUSE" and stale.failed[0].label == "données fraîches"
    # POL : favorable mais hors univers, sans ajout automatique (réglage par défaut) : refusée
    foreign = evaluate(settings, text(entry, entry - atr, entry + 2 * atr, pair="POL/USDT"), source="autre", now=now)
    assert foreign.verdict == "REFUSE" and foreign.failed[0].label == "paire dans l'univers"

    registry = ExternalSignalRegistry(settings.external_db)
    rows = registry.recent()
    # Refusés (données périmées, hors univers, signal mort, signal joué) : jamais comptés dans un bilan.
    assert len(rows) == 7 and sorted(r["outcome"] for r in rows) == ["INVALID"] * 4 + ["PENDING"] * 3
    assert resolve_pending(settings, registry, now=now) == {"PENDING": 3}       # aucune bougie postérieure
    store.save(pd.concat([setup, future_bars(last["open_time"], close, 300, 0.005)], ignore_index=True),
               "ETHUSDT", "15m")
    counts = resolve_pending(settings, registry, now=now + timedelta(days=4))
    # Suivi à partir de la bougie qui s'ouvre APRÈS la réception : le prix monte de 0,5 % par bougie, il a déjà
    # dépassé l'entrée « good » (close + 0,1 %) et reste au-dessus de la limite « stop serré » : deux non remplis.
    # Seul « late » (entrée à +5 %) est rempli, puis atteint TP1.
    assert counts == {"TP1_FIRST": 1, "UNFILLED": 2}
    stats = registry.source_stats("groupe test")[0]
    assert stats["resolved"] == 1 and stats["realized_tp1_rate"] == pytest.approx(1.0) and stats["unfilled"] == 2
    assert stats["refuse"] == 3 and stats["defavorable"] >= 2 and stats["realized_r"] is not None
    assert all(r["outcome_r"] > 0 for r in registry.recent() if r["outcome"] == "TP1_FIRST")
    assert registry.recent()[0]["received_at"] >= registry.recent()[-1]["received_at"]
    assert now.utcoffset() == timedelta(0)


def test_same_text_is_counted_once_per_source_and_copies_are_marked(settings):
    registry = ExternalSignalRegistry(settings.external_db)
    base = dict(content_hash="h1", template="structured", symbol="ETHUSDT", entry=100.0, stop=98.0, tp1=104.0,
                targets=[104.0], decision_time=None, close=100.0, verdict="INDETERMINE", p_tp1=0.4,
                base_expectancy_r=-0.1, evaluation={}, raw_text="x", resolvable=True)
    t0 = pd.Timestamp("2026-09-30 10:00", tz="UTC").to_pydatetime()
    first = registry.record(received_at=t0, source="A", **base)
    again = registry.record(received_at=t0 + timedelta(hours=1), source="A", **base)       # re-collé
    copied = registry.record(received_at=t0 + timedelta(hours=2), source="B", **base)      # autre groupe
    later = registry.record(received_at=t0 + timedelta(days=8), source="A", **base)        # hors fenêtre
    rows = {r["id"]: r for r in registry.pending()}
    assert set(rows) == {first, copied, later}                                  # le doublon n'est jamais résolu
    stats = {s["source"]: s for s in registry.source_stats()}
    assert stats["A"]["evaluated"] == 2 and stats["A"]["duplicates"] == 1 and stats["B"]["copies"] == 1
    with registry.connect() as db:
        dup = db.execute("SELECT outcome, duplicate_of FROM external_signals WHERE id=?", (again,)).fetchone()
        cop = db.execute("SELECT copy_of FROM external_signals WHERE id=?", (copied,)).fetchone()
    assert (dup["outcome"], dup["duplicate_of"]) == ("DUPLICATE", first) and cop["copy_of"] == first


def test_resolution_starts_at_the_first_bar_opened_after_reception(settings):
    """La bougie en cours à la réception a commencé avant : ses extrêmes ne comptent pas."""
    store = CandleStore(settings.data_dir)
    candles = canonical(150, symbol="ETHUSDT", start="2026-09-30", seed=3)
    prices = ["open", "high", "low", "close"]
    candles.loc[candles.index >= 10, prices] = [100.0, 100.2, 99.9, 100.0]       # prix stable à ~100
    candles.loc[10, prices] = [100.0, 100.2, 90.0, 100.0]                        # plongée à 90 dans la bougie 10
    candles.loc[candles.index >= 10, ["taker_buy_base_volume", "taker_buy_quote_volume"]] = 0.0
    store.save(candles, "ETHUSDT", "15m")
    registry = ExternalSignalRegistry(settings.external_db)
    received = candles["open_time"].iat[10].to_pydatetime() + timedelta(minutes=5)   # après la plongée
    registry.record(received_at=received, source="A", content_hash="h2", template="structured", symbol="ETHUSDT",
                    entry=95.0, stop=91.0, tp1=110.0, targets=[110.0], decision_time=received - timedelta(minutes=5),
                    close=100.0, verdict="INDETERMINE", p_tp1=None, base_expectancy_r=None, evaluation={},
                    raw_text="y", resolvable=True)
    counts = resolve_pending(settings, registry, now=received + timedelta(days=30))
    assert counts == {"UNFILLED": 1}          # la plongée à 90 précède la réception : pas de remplissage à 95


def test_source_record_draws_no_conclusion_before_twenty_resolved_signals(settings):
    from crypto_signal_intelligence.external.record import MIN_RESOLVED, source_records
    registry = ExternalSignalRegistry(settings.external_db)
    t0 = pd.Timestamp("2026-01-01", tz="UTC").to_pydatetime()
    for k in range(MIN_RESOLVED + 5):
        sid = registry.record(received_at=t0 + timedelta(days=k), source="Bon", content_hash=f"g{k}",
                              template="structured", symbol="ETHUSDT", entry=100.0, stop=98.0, tp1=104.0,
                              targets=[104.0], decision_time=None, close=100.0, verdict="INDETERMINE", p_tp1=0.35,
                              base_expectancy_r=-0.1, evaluation={}, raw_text="x", resolvable=True)
        registry.mark(sid, "TP1_FIRST", 1.9, t0, t0 + timedelta(days=k, hours=5))
        if k < 3:
            other = registry.record(received_at=t0 + timedelta(days=k), source="Nouveau", content_hash=f"n{k}",
                                    template="structured", symbol="ETHUSDT", entry=100.0, stop=98.0, tp1=104.0,
                                    targets=[104.0], decision_time=None, close=100.0, verdict="INDETERMINE",
                                    p_tp1=0.35, base_expectancy_r=-0.1, evaluation={}, raw_text="x", resolvable=True)
            registry.mark(other, "TP1_FIRST", 1.9, t0, t0 + timedelta(days=k, hours=5))
    records = {r.source: r for r in source_records(registry)}
    assert "aucune conclusion" in records["Nouveau"].conclusion and records["Nouveau"].edge_ci95 is None
    good = records["Bon"]
    assert good.resolved == MIN_RESOLVED + 5 and good.edge_r == pytest.approx(2.0)
    assert good.edge_ci95 is not None and good.edge_ci95[0] > 0 and "au-dessus" in good.conclusion


def test_source_record_needs_signals_spread_over_several_days(settings):
    """Vingt-cinq signaux le même jour suivent le même marché : pas vingt-cinq observations indépendantes."""
    from crypto_signal_intelligence.external.record import MIN_RESOLVED, source_records
    registry = ExternalSignalRegistry(settings.external_db)
    t0 = pd.Timestamp("2026-01-01", tz="UTC").to_pydatetime()
    for k in range(MIN_RESOLVED + 5):
        sid = registry.record(received_at=t0 + timedelta(minutes=k), source="Rafale", content_hash=f"r{k}",
                              template="structured", symbol="ETHUSDT", entry=100.0, stop=98.0, tp1=104.0,
                              targets=[104.0], decision_time=None, close=100.0, verdict="INDETERMINE", p_tp1=0.35,
                              base_expectancy_r=-0.1, evaluation={}, raw_text="x", resolvable=True)
        registry.mark(sid, "TP1_FIRST", 1.9, t0, t0 + timedelta(hours=5))
    record = source_records(registry)[0]
    assert record.edge_ci95 is None and "concentrés" in record.conclusion



def test_base_rate_never_uses_orders_resolved_after_the_development_end():
    """Version 3 : le test final de la recherche reste réservé ; falsifier tout ce qui suit la fin de
    l'historique ne change rien au taux de base."""
    candles = canonical(3000, vol=0.006)
    frame = candles[["open_time", "open", "high", "low", "close"]].assign(
        decision_time=candles["open_time"] + pd.Timedelta(minutes=15),
        atr14=ind.atr(candles["high"], candles["low"], candles["close"]), ctx_trend="BULL", ctx_volatility="NORMAL")
    end = frame["decision_time"].iloc[2000]
    kwargs = dict(stop_atr=1.5, target_r=2, horizon=8, costs=FREE, trend="BULL", volatility="NORMAL", min_samples=50,
                  seed=1, bootstrap_samples=200, entry_offset=0.0, entry_window=4, history_end=end)
    first = br.base_rate(frame, **kwargs)
    later = frame["open_time"] > end
    falsified = frame.copy()
    for column in ("open", "high", "low", "close"):
        falsified.loc[later, column] = falsified.loc[later, column] * 1.5
    assert br.base_rate(falsified, **kwargs) == first
    assert first.history_end == f"{end:%Y-%m-%d}" and first.method == "LIMIT_ALIGNED_V4"
    everything = br.base_rate(frame, **{**kwargs, "history_end": None})
    assert everything.emitted > first.emitted


def test_sell_at_a_target_is_a_take_profit_and_telegram_bold_is_ignored():
    signal = parse(INCRYPTO)
    assert signal.ok and signal.direction == "BUY" and signal.symbol == "MOVRUSDT"
    assert signal.entries == [2.86, 2.74] and signal.targets == [2.9, 2.94, 3.02, 3.2, 3.45, 3.83, 4.28]
    assert signal.stop == 2.7 and signal.stop_timeframe == "4h" and signal.published_at == "2026-10-02T20:59:00+00:00"
    # Une vraie vente reste refusée, sous toutes ses formes.
    for short in ("SELL BTC/USDT\nENTRY 1: 84000\nT1: 80000\nSL: 90000",
                  "PAIR: BTC/USDT\nDIRECTION: SELL\nENTRY 1: 84000\nT1: 80000\nSL: 90000",
                  "PAIR: BTC/USDT\nSELL LIMIT 84000\nT1: 80000\nSL: 90000",
                  "*PAIR:* BTC/USDT\n*SHORT*\nENTRY 1: 84000\nT1: 80000\nSL: 90000"):
        assert parse(short).direction == "SELL" and parse(short).errors, short
    assert not parse(INCRYPTO.replace("T1: 2.9  📉 SELL", "T1: 2.9 OR 2.95")).ok      # toujours aucun prix deviné


def test_a_limit_above_the_market_scales_with_atr_and_never_puts_the_stop_above_the_fill():
    """Signal reçu après une baisse : entrée 1 à +2,3 % du prix (achat aussitôt au marché), stop à 1,2 ATR sous
    l'entrée. En % fixe (V3), dans une période calme (ATR 1 %), la limite +2,3 % mettait le stop AU-DESSUS du
    prix d'achat : stop immédiat. En ATR (V4), l'écart suit la volatilité : le stop reste sous le prix d'achat."""
    rng = np.random.default_rng(4)
    n = 3000
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.002, n)))
    opens = np.r_[100.0, close[:-1]]
    frame = pd.DataFrame({"open": opens, "high": np.maximum(opens, close) * 1.001, "low": np.minimum(opens, close) * 0.999,
                          "close": close, "atr14": close * 0.01,
                          "decision_time": pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")})
    common = dict(stop_atr=1.2, target_r=0.25, entry_window=4, horizon=96, costs=FREE)
    percent = br.blind_limit_outcomes(frame, entry_offset=0.023, **common)
    in_atr = br.blind_limit_outcomes(frame, entry_offset=0.0, entry_offset_atr=0.5, **common)
    assert (percent.outcome == br.SL).mean() > 0.95                        # V3 : stop au-dessus de l'achat
    assert (in_atr.outcome == br.SL).mean() < 0.9 and (in_atr.outcome == br.TP).mean() > 0.1
    # Même résolution que `replay` pour un ordre, prix absolus recalculés à la main.
    i = 1500
    limit = close[i] + 0.5 * close[i] * 0.01
    stop = limit - 1.2 * close[i] * 0.01
    after = frame.iloc[i + 1:].assign(open_time=frame["decision_time"].iloc[i + 1:]).reset_index(drop=True)
    outcome, r, _ = replay(after, entry=limit, stop=stop, target=limit + 0.25 * (limit - stop), entry_window=4,
                           max_hold=96, costs=FREE)
    pos = int(np.flatnonzero(in_atr.times == frame["decision_time"].to_numpy()[i])[0])
    assert {br.TP: "TP1_FIRST", br.SL: "SL_FIRST", br.TIMEOUT: "TIMEOUT"}[in_atr.outcome[pos]] == outcome
    assert in_atr.r[pos] == pytest.approx(r, abs=1e-4)


# --- Prix entier suivi d'un point (« Stop: 223. »), même règle que BSM (branche fix/parser-stop-tp) ----------------

QNT_TRAILING_DOT = """👑AL-MAHWASHI VIP👑
#QNT/USDT
📍 Entry1: 234.04
🎯 TARGETS
🎯 TP1: 240.30 (2.67%)
🎯 TP2: 246.90 (5.50%)
🎯 TP3: 253.87 (8.47%)
🎯 TP4: 263.18 (12.45%)
🎯 TP5: 273.36 (16.80%)
🛑 Stop: 223.
📅 Date: Saturday - 2026-10-03"""


def test_integer_price_with_trailing_dot_is_read():
    from crypto_signal_intelligence.external.parser import parse
    signal = parse(QNT_TRAILING_DOT)
    assert signal.errors == [] and signal.symbol == "QNTUSDT" and signal.stop == 223.0
    assert signal.targets[-1] == 273.36


@pytest.mark.parametrize("stop", ["223.5.", "1.442.", "2.2.3", "223.."])
def test_malformed_prices_stay_refused(stop):
    from crypto_signal_intelligence.external.parser import parse
    assert parse(QNT_TRAILING_DOT.replace("Stop: 223.", f"Stop: {stop}")).stop is None
