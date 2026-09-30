from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.config import RegimeSection
from crypto_signal_intelligence.features import indicators as ind
from crypto_signal_intelligence.features.builder import (
    SetupFeatureParams,
    build_decision_frame,
    context_features,
    join_past,
    setup_features,
)
from crypto_signal_intelligence.regimes.classifier import classify
from crypto_signal_intelligence.strategies import registry
from crypto_signal_intelligence.validation.causality import check

from .conftest import canonical


def test_rsi_and_atr_conventions():
    rising = pd.Series(np.arange(1, 60, dtype=float))
    assert ind.rsi(rising).iloc[-1] == 100
    frame = canonical(300)
    rsi = ind.rsi(frame["close"]).dropna()
    assert rsi.between(0, 100).all() and ind.rsi(frame["close"]).iloc[:14].isna().all()
    # ATR de Wilder sur une série à vrai range constant : converge vers ce range.
    close = pd.Series(np.full(100, 10.0))
    assert ind.atr(close + 1, close - 1, close).iloc[-1] == pytest.approx(2.0)


def test_recursive_indicators_converge_with_history_depth():
    close = canonical(3000)["close"]
    full, short = ind.ema(close, 50), ind.ema(close.iloc[-1000:].reset_index(drop=True), 50)
    assert abs(full.iloc[-1] - short.iloc[-1]) / full.iloc[-1] < 1e-9


def test_reference_windows_exclude_current_bar():
    high = pd.Series([1.0, 2, 3, 4, 100])
    assert ind.prior_max(high, 3).iloc[-1] == 4
    volume = pd.Series([1.0, 1, 1, 1, 50])
    assert ind.prior_mean(volume, 4).iloc[-1] == 1


def test_multi_timeframe_join_uses_only_available_context():
    """À la décision 10h15, la bougie 1h 10h–11h n'est pas utilisable ; celle de 9h–10h l'est."""
    setup = setup_features(canonical(200, start="2024-01-01"), "15m", SetupFeatureParams())
    ctx = context_features(canonical(60, "1h", start="2024-01-01"), RegimeSection())
    joined = join_past(setup, ctx, "ctx_")
    row = joined[joined["open_time"] == pd.Timestamp("2024-01-02 10:00", tz="UTC")].iloc[0]
    assert row["decision_time"] == pd.Timestamp("2024-01-02 10:15", tz="UTC")
    assert row["ctx_open_time"] == pd.Timestamp("2024-01-02 09:00", tz="UTC")
    at_hour = joined[joined["open_time"] == pd.Timestamp("2024-01-02 10:45", tz="UTC")].iloc[0]
    # Décision à 11h00:02 : la bougie 10h–11h est disponible exactement à ce moment.
    assert at_hour["ctx_open_time"] == pd.Timestamp("2024-01-02 10:00", tz="UTC")
    assert (joined["ctx_available_at"].dropna() <= joined.loc[joined["ctx_available_at"].notna(), "available_at"]).all()


def test_regimes_are_causal_and_separate_axes():
    ctx = context_features(canonical(1500, "1h", drift=0.001), RegimeSection())
    assert ctx["trend"].iloc[:40].eq("UNKNOWN").all()
    assert set(ctx["trend"]) <= {"BULL", "BEAR", "RANGE", "UNKNOWN"}
    assert ctx["trend"].iloc[-300:].eq("BULL").mean() > 0.5
    altered = ctx.copy()
    altered.loc[1000:, "atr_pct"] *= 10
    before = classify(ctx, RegimeSection()).iloc[:1000]
    after = classify(altered, RegimeSection()).iloc[:1000]
    pd.testing.assert_frame_equal(before, after)


def test_decisions_and_features_do_not_change_when_future_changes(settings):
    """Test central : couper à t, falsifier après t, recalculer ; rien ne doit bouger avant t."""
    inputs = {"setup": canonical(4000, seed=1), "context": canonical(1000, "1h", seed=2),
              "btc": canonical(1000, "1h", seed=3, symbol="BTCUSDT")}
    strategy = registry.build("DONCHIAN_VOLUME_BREAKOUT", settings.strategies)
    cuts = [inputs["setup"]["open_time"].iloc[i] for i in (1500, 2600, 3800)]
    results = check(settings, strategy, inputs, "ETHUSDT", cuts)
    assert all(r.ok for r in results), [(r.cut, r.feature_mismatches, r.decision_mismatches) for r in results]
    assert all(r.rows_compared == 400 for r in results)


def test_gap_blocks_decisions_downstream(settings):
    setup = canonical(600).drop(index=range(300, 305)).reset_index(drop=True)
    frame = build_decision_frame(setup, canonical(200, "1h"), canonical(200, "1h", symbol="BTCUSDT"),
                                 setup_timeframe="15m", params=SetupFeatureParams(gap_block_bars=50),
                                 regimes=RegimeSection())
    flags = frame["data_gap_recent"].to_numpy()
    assert not flags[:300].any() and flags[300:350].all() and not flags[360:].any()
    assert datetime(2024, 1, 1, tzinfo=UTC) <= frame["decision_time"].iloc[0].to_pydatetime()
