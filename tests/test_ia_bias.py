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
    assert pd.Timestamp(true_a["bars"][-1][0]) + ib.STEP == t0                    # dernière bougie = celle qui clôt à t0
    assert true_f4["open_time"].iloc[-1] + ib.STEP == t0
    # Même sans la coupure de `known` (tout l'historique, futur falsifié compris) : rien après t0 n'entre.
    full_ctx, full_a, full_f4 = ib.context_of(fake, "AUSDT", t0)
    assert full_ctx == true_ctx and ib.chart_png(full_a, full_f4) == ib.chart_png(true_a, true_f4)


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


def test_block_interval_and_verdict():
    times = pd.Series(pd.date_range("2025-04-01", periods=150, freq="3D", tz="UTC"))
    blocks = ib.blocks_of(times)
    assert blocks[0] == 0 and blocks[5] == 1 and len(set(blocks)) == 32                # 447 jours en blocs de 14
    rng = np.random.default_rng(1)
    up = ib.block_ci(np.abs(rng.normal(0.02, 0.01, 150)), blocks, samples=500)
    down = ib.block_ci(-np.abs(rng.normal(0.02, 0.01, 150)), blocks, samples=500)
    noise = ib.block_ci(rng.normal(0, 0.05, 150), blocks, samples=500)
    assert ib.verdict(up, up) == ib.BETTER and ib.verdict(down, up) == ib.WORSE and ib.verdict(noise, up) == ib.NOT_BETTER
    assert ib.verdict(up, noise) == ib.NOT_BETTER                       # l'IA seule doit aussi porter une information
    assert ib.verdict(up, up, unreadable_share=0.03) == ib.INSUFFICIENT
    assert ib.block_ci(np.ones(9), np.arange(9), samples=100) is None and ib.verdict(None, up) == ib.INSUFFICIENT


def test_an_always_neutral_ai_does_not_beat_a_losing_rule():
    """Relecture : quand la règle perd, une IA toujours NEUTRE avait un écart positif sans rien prédire."""
    rng = np.random.default_rng(3)
    n = 300
    r = rng.normal(-0.01, 0.03, n)
    rows = pd.DataFrame({"symbol": "AUSDT", "decision_at": pd.date_range("2025-04-01", periods=n, freq="1D", tz="UTC"),
                         "bias": "NEUTRE", "rule": np.where(r > 0, -1, 1), "r72": r, "seconds": 1.0})
    out = ib.evaluate(rows, samples=500)
    assert out["decision"]["ci_pct"][0] > 0 and out["decision"]["verdict"] == ib.NOT_BETTER


def test_evaluate_rewards_an_oracle_and_compares_with_the_rule():
    rng = np.random.default_rng(2)
    n = 300
    r = rng.normal(0, 0.05, n)
    rows = pd.DataFrame({"symbol": "AUSDT", "decision_at": pd.date_range("2025-04-01", periods=n, freq="1D", tz="UTC"),
                         "bias": np.where(r > 0, "HAUSSIER", "BAISSIER"), "rule": rng.choice([-1, 1], n), "r72": r,
                         "seconds": 10.0})
    out = ib.evaluate(rows, samples=500)
    assert out["ia_right_share"] == 1.0 and out["decision"]["verdict"] == ib.BETTER
    assert out["ia_signed_mean_pct"] == pytest.approx(100 * np.abs(r).mean(), abs=1e-3)
    neutral = rows.assign(bias="NEUTRE")
    assert ib.evaluate(neutral, samples=500)["ia_signed_mean_pct"] == 0.0


@pytest.fixture
def fake_world(settings, monkeypatch):
    from crypto_signal_intelligence.research import long_history
    frames = {"AUSDT": hours(5, drift=0.0005), "BUSDT": hours(6, drift=-0.0005)}
    monkeypatch.setattr(long_history, "load_long", lambda s, symbol: frames[symbol])
    monkeypatch.setattr(ib, "FROM", pd.Timestamp("2024-03-15", tz="UTC"))
    monkeypatch.setattr(ib, "TO", pd.Timestamp("2024-08-20", tz="UTC"))
    monkeypatch.setattr(ib, "REHEARSAL_FROM", pd.Timestamp("2024-03-15", tz="UTC"))
    monkeypatch.setattr(ib, "REHEARSAL_TO", pd.Timestamp("2024-04-15", tz="UTC"))
    monkeypatch.setattr(ib, "code_state", lambda: "abc123")
    return frames


def kwargs(**extra):
    return {"now": datetime(2026, 10, 5, tzinfo=UTC), "digest_fn": lambda m: "sha256:abc", "version_fn": lambda: "0.35.1",
            "sleep_fn": lambda s: None, "symbols": ["AUSDT", "BUSDT"], "n": 40} | extra


def answering(calls, registry):
    def fake_ask(model, prompt, image):
        assert registry.final_test_consulted(ib.STRATEGY) == 1            # consultation inscrite avant la 1re question
        assert image[:8] == b"\x89PNG\r\n\x1a\n" and "derniere_cloture" in prompt and "AUSDT" not in prompt
        calls.append(1)
        return {"response": json.dumps({"analyse": "tendance", "biais": "HAUSSIER" if len(calls) % 3 else "NEUTRE"})}
    return fake_ask


def test_run_end_to_end_with_a_fake_ai(settings, fake_world):
    registry = ExperimentRegistry(settings.experiments_db)
    calls: list[int] = []
    with pytest.raises(ib.FinalTestLocked):
        ib.run(settings, model="qwen2.5vl:7b", ask_fn=answering(calls, registry), **kwargs())
    payload = ib.run(settings, model="qwen2.5vl:7b", allow_final_test=True, ask_fn=answering(calls, registry), **kwargs())
    assert len(calls) == 40 and payload["result"]["n"] == 40 and payload["consultation"] == 1
    assert payload["result"]["biases"] == {"HAUSSIER": 27, "NEUTRE": 13}
    entry = registry.get(payload["run_id"])
    assert entry is not None and entry["period_label"] == "FINAL_TEST" and entry["metrics"]["n_trials"] == 1
    assert entry["params"]["ollama"] == "0.35.1" and entry["params"]["options"]["num_predict"] == ib.OPTIONS["num_predict"]
    with pytest.raises(ib.AlreadyConsulted):                              # une seule lecture par modèle
        ib.run(settings, model="qwen2.5vl:7b", allow_final_test=True, ask_fn=answering(calls, registry), **kwargs())
    other = ib.run(settings, model="gemma3:12b", allow_final_test=True, ask_fn=answering(calls, registry), **kwargs())
    assert other["consultation"] is None and registry.final_test_consultations_total() == 1     # une consultation en tout
    with pytest.raises(ValueError):
        ib.run(settings, model="autre:1b", allow_final_test=True, ask_fn=answering(calls, registry), **kwargs(n=5))


def test_a_server_failure_stops_the_run_records_failed_and_caches_nothing(settings, fake_world):
    registry = ExperimentRegistry(settings.experiments_db)
    attempts = []

    def down(model, prompt, image):
        attempts.append(1)
        raise TimeoutError("délai dépassé")

    with pytest.raises(ib.ServerFailure):
        ib.run(settings, model="qwen2.5vl:7b", allow_final_test=True, ask_fn=down, **kwargs())
    assert len(attempts) == ib.RETRIES
    assert not ib.cache_path_for(settings, "qwen2.5vl:7b").exists() or not ib.load_cache(ib.cache_path_for(settings, "qwen2.5vl:7b"))
    with registry.connect() as db:
        statuses = [row[0] for row in db.execute("SELECT status FROM runs WHERE kind=?", (ib.KIND,)).fetchall()]
    assert statuses == ["FAILED"]
    calls: list[int] = []
    payload = ib.run(settings, model="qwen2.5vl:7b", allow_final_test=True, ask_fn=answering(calls, registry), **kwargs())
    assert len(calls) == 40 and payload["result"]["n"] == 40                   # reprise : toutes les questions posées


def test_a_truncated_cache_line_is_ignored(tmp_path):
    path = tmp_path / "c.jsonl"
    path.write_text(json.dumps({"key": "a", "bias": "NEUTRE"}) + "\n" + '{"key": "b", "bi', encoding="utf-8")
    assert list(ib.load_cache(path)) == ["a"]


def test_rehearsal_measures_nothing_and_records_nothing(settings, fake_world):
    registry = ExperimentRegistry(settings.experiments_db)
    calls: list[int] = []

    def fake_ask(model, prompt, image):
        calls.append(1)
        return {"response": "pas du json" if len(calls) == 1 else json.dumps({"analyse": "x", "biais": "BAISSIER"})}

    out = ib.run(settings, model="qwen2.5vl:7b", rehearsal=True, ask_fn=fake_ask, **kwargs())
    assert out["rehearsal"] and out["n"] == ib.REHEARSAL_N and out["unreadable_share"] == 0.1 and "result" not in out
    assert registry.final_test_consultations_total() == 0 and registry.count_runs() == 0


def test_the_answer_cache_is_ignored_by_git():
    import subprocess
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    target = "state/ia_cache/qwen2.5vl_7b.jsonl"
    done = subprocess.run(["git", "check-ignore", "-q", target], cwd=repo, check=False)
    assert done.returncode == 0
