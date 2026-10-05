"""IA locale contre la règle des moyennes : tirage des moments, rien après la décision dans ce que voit l'IA, rendement à
72 h, règle EMA, lecture des réponses, client local seulement, intervalle par semaines, décision, exécution de bout en
bout (fausse IA, consultation inscrite avant la première question, cache). Données SYNTHÉTIQUES, aucun réseau."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research import ia_bias as ib
from crypto_signal_intelligence.research.experiments import ExperimentRegistry

START = pd.Timestamp("2024-01-01", tz="UTC")


def hours(seed: int, n: int = 24 * 260, drift: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
    o = np.r_[100.0, c[:-1]]
    vol = rng.uniform(1e5, 2e5, n)
    return pd.DataFrame({"open_time": START + pd.to_timedelta(np.arange(n), unit="h"), "open": o,
                         "high": np.maximum(o, c) * (1 + rng.uniform(0, 0.006, n)),
                         "low": np.minimum(o, c) * (1 - rng.uniform(0, 0.006, n)), "close": c,
                         "quote_volume": vol, "taker_buy_quote_volume": vol * rng.uniform(0.3, 0.7, n)})


def test_moments_are_reproducible_inside_the_window_with_enough_history():
    frames = {"AUSDT": hours(1), "BUSDT": hours(2)}
    start, end = pd.Timestamp("2024-03-15", tz="UTC"), pd.Timestamp("2024-08-15", tz="UTC")
    a = ib.sample_moments(frames, n=50, start=start, end=end)
    assert a == ib.sample_moments(frames, n=50, start=start, end=end) and len(set(a)) == 50
    for symbol, t0 in a:
        assert start <= t0 <= end and t0.hour % 4 == 0
        assert len(ib.four_hours(ib.known(frames[symbol], t0))) >= ib.MIN_BARS


def test_nothing_after_the_decision_is_seen():
    h1 = hours(3)
    t0 = pd.Timestamp("2024-07-01 08:00", tz="UTC")
    fake = h1.copy()
    after = pd.to_datetime(fake["open_time"], utc=True) >= t0
    fake.loc[after, ["open", "high", "low", "close", "quote_volume"]] *= 3.0
    true_ctx, true_a, true_f4 = ib.context_of(ib.known(h1, t0), "AUSDT", t0)
    fake_ctx, fake_a, fake_f4 = ib.context_of(ib.known(fake, t0), "AUSDT", t0)
    assert true_ctx == fake_ctx and ib.ema_rule(true_f4) == ib.ema_rule(fake_f4)
    assert ib.chart_png(true_a, true_f4) == ib.chart_png(fake_a, fake_f4)        # image identique, octet par octet
    assert pd.Timestamp(true_a["bars"][-1][0]) + ib.STEP <= t0


def test_price_at_uses_the_close_of_the_hour_before():
    h1 = hours(4, n=200)
    t = START + pd.Timedelta(hours=10)
    assert ib.price_at(h1, t) == h1["close"].iloc[9]
    assert ib.price_at(h1.drop(index=9), t) is None


def test_ema_rule_follows_the_averages():
    up = pd.DataFrame({"close": np.linspace(10, 50, 400)})
    down = pd.DataFrame({"close": np.linspace(50, 10, 400)})
    assert ib.ema_rule(up) == 1 and ib.ema_rule(down) == -1


def test_answers_are_read_strictly():
    assert ib.parse_bias({"response": json.dumps({"analyse": "x", "biais": "HAUSSIER"})}) == ("HAUSSIER", "x")
    assert ib.parse_bias({"response": "pas du json"})[0] == ib.UNREADABLE
    assert ib.parse_bias({"response": json.dumps({"biais": "ACHAT"})})[0] == ib.UNREADABLE
    assert ib.parse_bias({})[0] == ib.UNREADABLE


def test_the_ai_client_only_calls_the_local_server():
    with pytest.raises(ib.LocalOnly):
        ib.ask("qwen2.5vl:7b", "x", b"png", url="http://example.com:11434")
    with pytest.raises(ib.LocalOnly):
        ib.model_digest("qwen2.5vl:7b", url="https://api.example.org")


def test_week_interval_and_verdict():
    weeks = np.repeat([f"2025-{k:02d}" for k in range(1, 31)], 5)
    rng = np.random.default_rng(1)
    assert ib.verdict(ib.week_ci(np.abs(rng.normal(0.02, 0.01, 150)), weeks, samples=500)) == ib.BETTER
    assert ib.verdict(ib.week_ci(-np.abs(rng.normal(0.02, 0.01, 150)), weeks, samples=500)) == ib.WORSE
    assert ib.verdict(ib.week_ci(rng.normal(0, 0.05, 150), weeks, samples=500)) == ib.NOT_BETTER
    assert ib.week_ci(np.ones(9), np.arange(9), samples=100) is None and ib.verdict(None) == ib.INSUFFICIENT


def test_evaluate_rewards_an_oracle_and_compares_with_the_rule():
    rng = np.random.default_rng(2)
    n = 300
    r = rng.normal(0, 0.05, n)
    rows = pd.DataFrame({"decision_at": pd.date_range("2025-04-01", periods=n, freq="1D", tz="UTC"),
                         "bias": np.where(r > 0, "HAUSSIER", "BAISSIER"), "rule": rng.choice([-1, 1], n), "r72": r,
                         "seconds": 10.0})
    out = ib.evaluate(rows, samples=500)
    assert out["ia_right_share"] == 1.0 and out["decision"]["verdict"] == ib.BETTER
    assert out["ia_signed_mean_pct"] == pytest.approx(100 * np.abs(r).mean(), abs=1e-3)
    neutral = rows.assign(bias="NEUTRE")
    assert ib.evaluate(neutral, samples=500)["ia_signed_mean_pct"] == 0.0


def test_run_end_to_end_with_a_fake_ai(settings, monkeypatch):
    from crypto_signal_intelligence.research import long_history
    frames = {"AUSDT": hours(5, drift=0.0005), "BUSDT": hours(6, drift=-0.0005)}
    monkeypatch.setattr(long_history, "load_long", lambda s, symbol: frames[symbol])
    monkeypatch.setattr(ib, "FROM", pd.Timestamp("2024-03-15", tz="UTC"))
    monkeypatch.setattr(ib, "TO", pd.Timestamp("2024-08-20", tz="UTC"))
    monkeypatch.setattr(ib, "code_state", lambda: "abc123")
    registry = ExperimentRegistry(settings.experiments_db)
    calls = []

    def fake_ask(model, prompt, image):
        assert registry.final_test_consulted(ib.STRATEGY) == 1            # consultation inscrite avant la 1re question
        assert image[:8] == b"\x89PNG\r\n\x1a\n" and "derniere_cloture" in prompt and "AUSDT" not in prompt
        calls.append(1)
        return {"response": json.dumps({"analyse": "tendance", "biais": "HAUSSIER" if len(calls) % 3 else "NEUTRE"})}

    payload = ib.run(settings, model="qwen2.5vl:7b", now=datetime(2026, 10, 5, tzinfo=UTC), ask_fn=fake_ask,
                     digest_fn=lambda m: "sha256:abc", symbols=["AUSDT", "BUSDT"], n=40)
    assert len(calls) == 40 and payload["result"]["n"] == 40 and payload["consultation"] == 1
    assert payload["result"]["biases"] == {"HAUSSIER": 27, "NEUTRE": 13}
    entry = registry.get(payload["run_id"])
    assert entry is not None and entry["period_label"] == "FINAL_TEST" and entry["metrics"]["n_trials"] == 1
    again = ib.run(settings, model="qwen2.5vl:7b", now=datetime(2026, 10, 5, tzinfo=UTC), ask_fn=fake_ask,
                   digest_fn=lambda m: "sha256:abc", symbols=["AUSDT", "BUSDT"], n=40)
    assert len(calls) == 40 and again["consultation"] is None                 # réponses en cache, pas de 2e consultation
    assert registry.final_test_consultations_total() == 1
    with pytest.raises(ValueError):
        ib.run(settings, model="autre:1b", now=datetime(2026, 10, 5, tzinfo=UTC), ask_fn=fake_ask,
               digest_fn=lambda m: "x", symbols=["AUSDT"], n=5)
