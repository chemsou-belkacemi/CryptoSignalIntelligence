"""Météo du marché : gardes des exécutions réelles (refus sans --executer, relecture, contrôles inscrits), sceau,
coupure des lecteurs à la fin de DEVELOPMENT, `n_descriptive` au registre. Données SYNTHÉTIQUES, aucun réseau."""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research import meteo as mt
from crypto_signal_intelligence.research import meteo_execution as me
from crypto_signal_intelligence.research.experiments import ExperimentRegistry

UTC = "UTC"


def _run(**metrics) -> dict:
    return dict(created_at="2026-10-08T00:00:00+00:00", kind="TEST", hypothesis="h", strategy="S", strategy_version=1,
                variant="v", params={}, period_label="DEVELOPMENT", period_start="", period_end="", universe=[],
                data_hashes={}, git_commit="x", dependencies={}, seed=0, cost_scenario="", simulation_rules={},
                metrics=metrics, status="COMPLETED", report_dir=None)


def test_n_descriptive_is_recorded_without_counting_as_trials(tmp_path):
    """§ 5.9 : `n_descriptive` est inscrit avec `n_trials` ; il n'entre jamais dans `program_trials`."""
    registry = ExperimentRegistry(tmp_path / "registry.sqlite")
    registry.record(run_id="A", **_run(n_trials=2))
    before = registry.program_trials()
    registry.record(run_id="B", **_run(n_trials=1, n_descriptive=37))
    assert registry.program_trials() == before + 1
    assert registry.descriptive_total() == 37
    assert registry.get("B")["metrics"]["n_descriptive"] == 37


def test_seal_fingerprint_detects_any_change(tmp_path):
    days = mt.day_index("2024-01-01", "2024-01-10")
    states = pd.DataFrame({"color": np.array([0, 2, 1, -1, 2, 2, 0, 0, 1, 2], np.int8), "m": np.linspace(-1, 1, 10)},
                          index=days)
    digest = me.write_seal(tmp_path, states)
    path = tmp_path / me.SEAL_FILE
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    back = me.read_seal(path, digest)
    assert back["color"].tolist() == states["color"].tolist()
    states.iloc[3, 0] = 2
    states.reset_index(names="day").to_parquet(path, index=False)
    with pytest.raises(me.NotReady):
        me.read_seal(path, digest)


def test_cli_refuses_real_execution_without_executer(settings):
    from typer.testing import CliRunner

    from crypto_signal_intelligence import cli
    for step in ("principale", "variante", "confirmation"):
        done = CliRunner().invoke(cli.app, ["meteo", "executer", step])
        assert done.exit_code == 2 and "--executer" in done.output
    assert CliRunner().invoke(cli.app, ["meteo", "fng-historique"]).exit_code == 2


def test_cli_refuses_while_review_is_not_inscribed(settings):
    """Avec --executer, la commande refuse tant que la relecture du code n'est pas inscrite (aucun calcul)."""
    from typer.testing import CliRunner

    from crypto_signal_intelligence import cli
    assert me.CODE_REVIEW is None
    done = CliRunner().invoke(cli.app, ["meteo", "executer", "principale", "--executer"])
    assert done.exit_code == 3 and "Aucun calcul" in done.output


def test_review_guards(tmp_path, monkeypatch):
    with pytest.raises(me.DirtyCode):
        me.require_clean_and_reviewed("abc+DIRTY", review="abc")
    with pytest.raises(me.NotReady):
        me.require_clean_and_reviewed("abc", review="")
    inert = tmp_path / "ok.py"
    inert.write_text('"""doc"""\nfrom __future__ import annotations\n\nCODE_REVIEW: str | None = "abc"\n'
                     'CONTROLES_VARIANTE: str | None = None\n', encoding="utf-8")
    assert me.review_file_is_inert(inert)
    inert.write_text('CODE_REVIEW = "abc"\nimport os\n', encoding="utf-8")
    assert not me.review_file_is_inert(inert)


def test_controls_inscription_is_checked(tmp_path):
    report = {"question": "principale", "sims": 500, "samples": 10_000, "all_null_criteria_passed": True,
              "rule": {"superiority": 0.8, "equivalence": 0.7, "false_equivalence_max": 0.03}}
    path = tmp_path / "principale_criteres.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert me.controls_of(f"{path}#{digest}", question="principale")["rule"]["superiority"] == 0.8
    with pytest.raises(me.NotReady):
        me.controls_of(None, question="principale")
    with pytest.raises(me.NotReady):
        me.controls_of(f"{path}#{digest}", question="variante")
    path.write_text(json.dumps(report | {"all_null_criteria_passed": False}), encoding="utf-8")
    with pytest.raises(me.NotReady):                                  # fichier modifié
        me.controls_of(f"{path}#{digest}", question="principale")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(me.NotReady):                                  # un critère a échoué : ni exécutée ni comptée
        me.controls_of(f"{path}#{digest}", question="principale")


def test_readers_ignore_everything_after_development_end(settings):
    """Mutation « lecture après le 2025-06-30 » : falsifier tout ce qui suit la coupure ne change rien."""
    from crypto_signal_intelligence.context import store
    from crypto_signal_intelligence.derivatives.history import DerivativesStore
    from crypto_signal_intelligence.research.pit_universe import pit_dir
    end = pd.Timestamp(me.development_end(settings)).tz_convert(UTC)
    days = mt.day_index("2025-06-01", "2025-07-15")
    times = pd.date_range("2025-06-01", "2025-07-15", freq="8h", tz=UTC)

    def write(scale: float) -> None:
        after = days > end
        fng = [{"key": me.FNG_KEY, "date": d, "field": me.FNG_FIELD, "value": 40.0 * (scale if a else 1.0)}
               for d, a in zip(days, after, strict=True)]
        frame = store.rows(fng, kind=store.HISTORY, source="test", now=pd.Timestamp("2026-10-08", tz=UTC))
        store.path_for(settings, me.FNG_SERIES).unlink(missing_ok=True)
        store.upsert(settings, me.FNG_SERIES, frame)
        rates = np.where(times > end, 0.001 * scale, 0.0001)
        deriv = DerivativesStore(settings.data_dir)
        deriv.path("funding", "BTCUSDT").parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"time": times, "rate": rates, "available_at": times + pd.Timedelta(minutes=1)}).to_parquet(
            deriv.path("funding", "BTCUSDT"))
        folder = pit_dir(settings)
        folder.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"day": days, "symbol": "AUSDT", "close": np.where(days >= end.floor("D"), 9.0 * scale, 1.0),
                      "quote_volume": 1e6}).to_parquet(folder / "daily.parquet")
        months = pd.date_range("2025-05-01", "2025-07-01", freq="MS", tz=UTC)
        pd.DataFrame({"month": months, "symbol": np.where((months > end) & (scale != 1), "BUSDT", "AUSDT"), "rank": 1,
                      "median_quote_volume": 1.0}).to_parquet(folder / "membership.parquet")

    write(1.0)
    first = (me.load_fng(settings, end), me.load_funding(settings, end), me.load_pit(settings, end))
    write(7.0)
    second = (me.load_fng(settings, end), me.load_funding(settings, end), me.load_pit(settings, end))
    pd.testing.assert_series_equal(first[0], second[0])
    pd.testing.assert_frame_equal(first[1], second[1])
    pd.testing.assert_frame_equal(first[2][0], second[2][0])
    pd.testing.assert_frame_equal(first[2][1], second[2][1])
    assert first[0].index.max() <= end and first[2][0]["day"].max() < end.floor("D")


def test_real_pipeline_runs_end_to_end_on_synthetic_frames(settings):
    """Chemin réel (feu, prévisions `fit_at`, panier, descriptif) sur des bougies SYNTHÉTIQUES au format du magasin :
    vérifie que le code s'exécute et que la suite des couleurs reste dans le fichier scellé."""
    from crypto_signal_intelligence.research import meteo_controls as mc
    from crypto_signal_intelligence.research import meteo_study as ms
    period = ms.Period("COURT", pd.Timestamp("2020-02-03", tz=UTC), pd.Timestamp("2020-05-03", tz=UTC), "F6", (2020,))
    world = mc.make_world(mc.Spec("N2", "principale", 3), period=period)
    names = {"P00USDT": "ETHUSDT", "P01USDT": "SOLUSDT", "P02USDT": "ZZZUSDT"}
    pairs = {new: world.pairs[old].assign(quote_volume=1e7) for old, new in names.items()}
    btc = world.btc.assign(quote_volume=1e8)
    members = mc.all_members(list(pairs), world.start, world.n_days)
    data = me.RealData(btc=btc, pit_daily=mc.pit_daily_of(pairs), members=members, fng=world.fng,
                       funding=world.funding, pairs=pairs | {"BTCUSDT": btc}, missing_hourly=[], hashes={})
    rng = np.random.default_rng(0)
    s1_days = mt.day_index(world.start + pd.Timedelta(days=40), period.end)
    s1 = pd.DataFrame({"day": np.repeat(s1_days, 20), "r_gross": rng.normal(0, 1, 20 * len(s1_days))})
    s1["risk_pct"], s1["r_central"], s1["r_defavorable"] = 0.026, s1["r_gross"] - 0.1, s1["r_gross"] - 0.15
    forecaster = me.forecaster_of(data, settings)
    states = me.real_states(settings, data, period, s1, forecaster)
    assert set(mt.COMPONENTS) <= set(states.columns) and states["VOL_HAUTE"].notna().any()
    table, info = me.basket_table(data, period, forecaster, symbols=list(pairs))
    assert table["y"].notna().mean() > 0.9 and set(info) >= {"sorties", "migrations"}
    analysis = ms.analyse(states["color"].to_numpy(), table["y"].to_numpy(float), period, samples=200)
    out, n = me.describe(states, table, s1, period, analysis)
    assert n >= 10 and out["fausses_alertes_attendues"] == pytest.approx(0.05 * n)
    seal = me.write_seal(settings.reports_dir / "x", states)
    assert me.read_seal(settings.reports_dir / "x" / me.SEAL_FILE, seal)["color"].tolist() == states["color"].tolist()
