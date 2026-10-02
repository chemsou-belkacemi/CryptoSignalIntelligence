"""Tests en direct (forward/) : journal chaîné, frais, filtre halal, simulation maker/taker calculée à la main, gel des
règles, test F1 de bout en bout, relevé des dérivés, rapport. SYNTHÉTIQUE : rien ici ne dit ce que donnera le marché."""
from __future__ import annotations

import json
import multiprocessing
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from crypto_signal_intelligence.data.http import HttpError
from crypto_signal_intelligence.data.store import CandleStore
from crypto_signal_intelligence.external.admission import DOUTEUX, FAVORABLE, Screening
from crypto_signal_intelligence.forward import derivlog, f1, registry, report, runner
from crypto_signal_intelligence.forward.costs import ADVERSE, CENTRAL, Costs, costs_for
from crypto_signal_intelligence.forward.halal import (
    HalalList,
    HalalNotValidated,
    admitted,
    load_screen,
    structural_reason,
)
from crypto_signal_intelligence.forward.journal import TRUNCATED, Journal, canonical, entry_hash
from crypto_signal_intelligence.forward.maker import NOT_FILLED, maker_path, net_return, taker_path
from crypto_signal_intelligence.outlook import tracking as tk
from crypto_signal_intelligence.research.experiments import ExperimentRegistry

from .conftest import PROJECT
from .conftest import canonical as candles_fixture

STEP = pd.Timedelta(minutes=15)
T0 = pd.Timestamp("2026-03-02", tz="UTC")
NOW = datetime(2026, 3, 2, 12, tzinfo=UTC)
HALAL = HalalList(("BTCUSDT", "ETHUSDT"), {}, "a" * 64, "b" * 64)
ETH_ONLY = HalalList(("ETHUSDT",), {}, "a" * 64, "b" * 64)


# --- Journal ------------------------------------------------------------------------------------------------------

def test_journal_chain_detects_any_change(tmp_path):
    path = tmp_path / "j.jsonl"
    journal = Journal(path)
    for k in range(4):
        journal.append("X", {"k": k, "v": float("nan") if k == 2 else 1.5}, now=NOW + timedelta(minutes=k))
    assert journal.verify() == {"ok": True, "entries": 4, "broken_at": None, "reason": None, "truncated": 0}
    assert [e["data"]["v"] for e in journal.entries()] == [1.5, 1.5, None, 1.5]          # NaN → null
    lines = path.read_text().splitlines()
    tampered = json.loads(lines[1])
    tampered["data"]["k"] = 99
    path.write_text("\n".join([lines[0], json.dumps(tampered), *lines[2:]]) + "\n")
    assert journal.verify()["broken_at"] == 1 and "modifié" in journal.verify()["reason"]
    path.write_text("\n".join([lines[0], *lines[2:]]) + "\n")                             # ligne supprimée
    assert not journal.verify()["ok"]
    path.write_text("\n".join([lines[1], lines[0], *lines[2:]]) + "\n")                   # lignes inversées
    assert journal.verify()["broken_at"] == 0
    path.write_text("\n".join(lines) + "\n")
    assert journal.verify()["ok"]


def test_journal_detects_a_splice_and_a_wrong_number(tmp_path):
    """Deux défauts que seuls le lien et le numéro voient : chaque entrée reste cohérente avec elle-même."""
    first, other = Journal(tmp_path / "a.jsonl"), Journal(tmp_path / "b.jsonl")
    for k in range(3):
        first.append("X", {"k": k}, now=NOW)
        other.append("X", {"k": 10 + k}, now=NOW)
    lines_a, lines_b = first.path.read_text().splitlines(), other.path.read_text().splitlines()
    spliced = Journal(tmp_path / "c.jsonl")
    spliced.path.write_text("\n".join([*lines_a[:2], lines_b[2]]) + "\n")         # numéro 2, mais autre chaîne
    assert spliced.verify()["broken_at"] == 2 and "lien" in spliced.verify()["reason"]
    wrong = json.loads(lines_a[2])
    wrong["seq"] = 7
    wrong["hash"] = entry_hash(7, wrong["at"], wrong["kind"], wrong["data"], wrong["prev"])
    spliced.path.write_text("\n".join([*lines_a[:2], canonical(wrong)]) + "\n")    # bien chaînée, mauvais numéro
    assert spliced.verify()["broken_at"] == 2 and "numéro" in spliced.verify()["reason"]


def test_journal_refuses_naive_or_backward_time(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    with pytest.raises(ValueError, match="fuseau"):
        journal.append("X", {}, now=datetime(2026, 3, 2, 12))
    journal.append("X", {}, now=NOW)
    with pytest.raises(ValueError, match="antérieur"):
        journal.append("X", {}, now=NOW - timedelta(seconds=1))
    entry = journal.append("Y", {"a": 1}, now=NOW)
    assert entry["seq"] == 1 and entry["at"] == "2026-03-02T12:00:00+00:00"


def test_journal_survives_a_line_cut_by_a_crash(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    journal.append("X", {"k": 0}, now=NOW)
    journal.append("X", {"k": 1}, now=NOW)
    with journal.path.open("a") as handle:                       # arrêt brutal au milieu d'une écriture
        handle.write('{"seq":2,"at":"2026-03-02T12:00:00+00:00","kind":"X","da')
    assert [e["data"]["k"] for e in journal.entries()] == [0, 1]
    assert journal.verify()["ok"] and "coupée" in journal.verify()["reason"]
    entry = journal.append("X", {"k": 2}, now=NOW)
    assert entry["seq"] == 3
    kinds = [e["kind"] for e in journal.entries()]
    assert kinds == ["X", "X", TRUNCATED, "X"]
    check = journal.verify()
    assert check["ok"] and check["truncated"] == 1 and check["entries"] == 4
    # Une ligne illisible que rien ne signale casse la chaîne.
    lines = journal.path.read_text().splitlines()
    journal.path.write_text("\n".join([lines[0], lines[1], "illisible", lines[4]]) + "\n")
    assert not journal.verify()["ok"]
    journal.path.write_text("\n".join([lines[0], "illisible", lines[1]]) + "\n")   # glissée entre deux entrées valides
    assert not journal.verify()["ok"] and "LIGNE_TRONQUEE" in journal.verify()["reason"]


def test_journal_complete_line_without_newline_is_kept(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    journal.append("X", {"k": 0}, now=NOW)
    journal.path.write_text(journal.path.read_text().rstrip("\n"))      # saut final perdu, ligne complète
    entry = journal.append("X", {"k": 1}, now=NOW)
    assert entry["seq"] == 1 and journal.verify() == {"ok": True, "entries": 2, "broken_at": None, "reason": None,
                                                       "truncated": 0}


# --- Frais --------------------------------------------------------------------------------------------------------

def test_single_cost_model():
    assert costs_for("BTCUSDT", CENTRAL) == Costs(fee=0.00075, market=0.0002)
    assert costs_for("ethusdt", CENTRAL) == Costs(fee=0.00075, market=0.0002)
    assert costs_for("SOLUSDT", CENTRAL) == Costs(fee=0.00075, market=0.0005)
    assert costs_for("SOLUSDT", ADVERSE) == Costs(fee=0.001, market=0.001)
    assert costs_for("BTCUSDT", ADVERSE) == Costs(fee=0.001, market=0.0004)
    with pytest.raises(ValueError):
        costs_for("BTCUSDT", "stress")


# --- Filtre halal -------------------------------------------------------------------------------------------------

SCREEN = """statut: {statut}
regles: {{admis: [AJOUTEE], exclus: [REFUSEE, A_DECIDER, INDISPONIBLE], absent: exclu}}
structurelles:
  stables: {{bases: [USDC, FDUSD]}}
  wrapped: {{bases: [PAXG], motif: "^(W|ST)(BTC|ETH)$"}}
  levier: {{motif: "^(BTC|ETH)(UP|DOWN)$"}}
"""


def decision(symbol, state, by="propriétaire"):
    return {"symbol": symbol, "decision": state, "decided_by": by}


def screenings(**status):
    return {base: Screening(base, value) for base, value in status.items()}


def test_halal_list_follows_owner_decisions(settings, tmp_path):
    path = tmp_path / "screen.yaml"
    path.write_text(SCREEN.format(statut="PROPOSITION"))
    with pytest.raises(HalalNotValidated):
        admitted(settings, path=path, decisions=[])
    path.write_text(SCREEN.format(statut="VALIDE"))
    decisions = [decision("DOGEUSDT", "AJOUTEE"), decision("BNBUSDT", "REFUSEE"), decision("GUNUSDT", "A_DECIDER", "règle"),
                 decision("XMRUSDT", "INDISPONIBLE", "règle"), decision("SOLUSDT", "REFUSEE"),
                 decision("USDCUSDT", "AJOUTEE"), decision("WBTCUSDT", "AJOUTEE"), decision("BTCUPUSDT", "AJOUTEE"),
                 decision("PAXGUSDT", "AJOUTEE"), decision("JUPUSDT", "AJOUTEE")]
    favorable = screenings(BTC=FAVORABLE, ETH=FAVORABLE, SOL=FAVORABLE, XRP=DOUTEUX)
    halal = admitted(settings, path=path, decisions=decisions, screenings=favorable)
    assert {"DOGEUSDT", "BTCUSDT", "ETHUSDT", "JUPUSDT"} <= set(halal.symbols)    # ajoutées ; configurées favorables
    assert "SOLUSDT" not in halal.symbols                                      # configurée favorable mais refusée
    assert "XRPUSDT" not in halal.symbols and "défaut" in halal.excluded["XRPUSDT"]   # configurée, douteuse, sans décision
    assert "NEARUSDT" not in halal.symbols                                     # configurée, absente du relevé
    for symbol in ("BNBUSDT", "GUNUSDT", "XMRUSDT", "USDCUSDT", "WBTCUSDT", "BTCUPUSDT", "PAXGUSDT", "PEPEUSDT"):
        assert not halal.allows(symbol), symbol
    assert halal.excluded["WBTCUSDT"].startswith("exclusion structurelle")
    assert halal.symbols == tuple(sorted(halal.symbols))
    again = admitted(settings, path=path, decisions=list(reversed(decisions)), screenings=favorable)
    assert again.sha256 == halal.sha256 and len(halal.sha256) == 64


def test_project_screen_file_is_validated_and_spares_ordinary_names():
    screen = load_screen(PROJECT / "config" / "halal_screen.yaml")
    assert screen["valide_par"] == "propriétaire" and screen["regles"]["absent"] == "exclu"
    for base in ("JUP", "SYRUP", "SUP", "BTC", "SOL"):
        assert structural_reason(base, screen) is None, base
    for base in ("BTCUP", "ETHDOWN", "BNBBULL", "USDC", "WBTC", "STETH", "PAXG"):
        assert structural_reason(base, screen) is not None, base


# --- Maker contre taker, à la main --------------------------------------------------------------------------------

def bars(rows, start=T0):
    return pd.DataFrame([(start + k * STEP, *r) for k, r in enumerate(rows)], columns=["open_time", "open", "high", "low", "close"])


def maker(rows, limit=100.0, horizon=6, through=0.0):
    return maker_path(bars(rows), limit=limit, stop_pct=-2.0, target_pct=3.0, horizon_bars=horizon, valid_bars=4,
                      step=STEP, through=through)


FLAT = (100.2, 100.4, 100.1, 100.3)
FILL = (100.2, 100.3, 99.9, 100.0)


def test_maker_fill_needs_a_strict_cross_within_the_validity():
    assert maker([(100.2, 100.5, 100.0, 100.3)] + [FLAT] * 5)["outcome"] == NOT_FILLED     # contact seulement
    assert maker([FLAT] * 4 + [(100.2, 100.3, 99.0, 99.5)] + [FLAT])["outcome"] == NOT_FILLED   # trop tard (5e bougie)
    out = maker([FLAT, FILL] + [FLAT] * 4)
    assert out["fill_bar"] == 1 and out["outcome"] == "TEMPS" and out["gross"] == pytest.approx(1.003)
    assert maker([FLAT] * 6)["gross"] is None
    # Robuste : il faut traverser de 0,1 % (99,9) ; 99,95 ne suffit pas, 99,85 oui.
    assert maker([(100.2, 100.3, 99.95, 100.0)] + [FLAT] * 5, through=0.001)["outcome"] == NOT_FILLED
    assert maker([(100.2, 100.3, 99.85, 100.0)] + [FLAT] * 5, through=0.001)["fill_bar"] == 0


def test_maker_exits_by_hand():
    # Rempli à 100 ; objectif 103 dépassé ensuite.
    assert maker([FILL, (100.0, 103.5, 99.9, 103.2)] + [FLAT] * 4)["gross"] == pytest.approx(1.03)
    # Pire cas dans la bougie du remplissage : plus bas sous le stop (98), même si l'objectif y est dépassé.
    out = maker([(100.5, 104.0, 97.9, 103.0)] + [FLAT] * 5)
    assert (out["outcome"], out["gross"]) == ("SL", pytest.approx(0.98))
    # Objectif touché sans être dépassé : pas de sortie ; ouverture sous le stop : sortie à l'ouverture.
    assert maker([FILL, (100.0, 103.0, 99.9, 102.0)] + [FLAT] * 4)["outcome"] == "TEMPS"
    out = maker([FILL, (97.0, 97.5, 96.5, 97.2)] + [FLAT] * 4)
    assert (out["outcome"], out["gross"]) == ("SL", pytest.approx(0.97))


def test_net_return_by_hand():
    assert net_return(1.03, fee=0.00075, entry_cost=0.0, exit_cost=0.0005) == pytest.approx(
        1.03 * (1 - 0.0005) * (1 - 0.00075) / (1 + 0.00075) - 1)
    assert net_return(1.03, fee=0.0, entry_cost=0.01, exit_cost=0.0) == pytest.approx(1.03 / 1.01 - 1)


def test_maker_gaps_and_pending():
    assert maker([FLAT] * 3)["outcome"] == "PENDING"
    holey = bars([FLAT] * 6).drop(index=2).reset_index(drop=True)
    assert maker_path(holey, limit=100, stop_pct=-2, target_pct=3, horizon_bars=5, valid_bars=4, step=STEP)["outcome"] == "TROU"


@pytest.mark.parametrize("rows", [
    [(100, 101, 99, 100.5), (100.5, 103.5, 100, 103)] + [FLAT] * 4,
    [(100, 101, 97.5, 98.5)] + [FLAT] * 5,
    [(100, 101, 99.5, 100.7)] * 6,
])
def test_taker_is_exactly_the_plan_rule(rows):
    """Chemin sans coût puis coûts par formule = `tracking.replay_plan` avec les mêmes coûts."""
    frame = bars(rows)
    path = taker_path(frame, stop_pct=-2, target_pct=3, horizon_bars=6, step=STEP)
    outcome, r = tk.replay_plan(frame, stop_pct=-2, target_pct=3, horizon_bars=6, step=STEP, fee=0.00075, market=0.0005)
    assert path["outcome"] == outcome
    assert net_return(path["gross"], fee=0.00075, entry_cost=0.0005, exit_cost=0.0005) / 0.02 == pytest.approx(r, abs=2e-4)


# --- Lectures, écart d'équilibre, verdict -------------------------------------------------------------------------

def paths_of(rows, symbol="SOLUSDT"):
    decision = {"symbol": symbol, "limit": 100.0, "stop_pct": -2.0, "target_pct": 3.0, "bars": 6}
    return f1.resolve_one(bars(rows), decision)


def test_views_differ_only_by_the_declared_costs():
    paths = paths_of([FILL, (100.0, 103.5, 99.9, 103.2)] + [FLAT] * 4)
    taker_o, maker_o, filled = f1.view_r("SOLUSDT", 0.02, paths, "observe")
    taker_m, maker_m, _ = f1.view_r("SOLUSDT", 0.02, paths, "modele_central")
    assert filled and maker_o == pytest.approx(maker_m)                         # le maker ne paie jamais l'écart d'entrée
    assert taker_m < taker_o                                                     # le modèle fait payer l'écart au taker
    assert taker_o == pytest.approx(net_return(paths["taker"]["gross"], fee=0.00075, entry_cost=0, exit_cost=0.0005) / 0.02)


def test_robust_reading_is_harder_for_the_maker():
    """Remplissage limite (99,95) puis objectif : rempli en central, pas en robuste. Le scénario de robustesse ne doit
    jamais favoriser le maker (défaut de la première version)."""
    paths = paths_of([(100.2, 100.3, 99.95, 100.0), (100.0, 103.5, 99.9, 103.2)] + [FLAT] * 4)
    assert paths["maker"]["central"]["fill_bar"] == 0 and paths["maker"]["robuste"]["outcome"] == NOT_FILLED
    t_o, m_o, _ = f1.view_r("SOLUSDT", 0.02, paths, "observe")
    t_r, m_r, _ = f1.view_r("SOLUSDT", 0.02, paths, "robuste")
    assert m_r - t_r < m_o - t_o
    assert f1.through_for("central", "SOLUSDT") == 0.0
    assert f1.through_for("robuste", "SOLUSDT") == pytest.approx(0.001)
    assert f1.through_for("robuste", "BTCUSDT") == pytest.approx(0.0004)


def test_break_even_cost_cancels_the_mean_gap():
    rally = [(100.2, 100.5, 100.1, 100.4), (100.4, 102.5, 100.3, 102.0)] + [(102.0, 102.2, 101.9, 102.1)] * 4
    worse = paths_of(rally)                                            # maker non rempli, le taker gagne
    better = paths_of([FILL, (100.0, 103.5, 99.9, 103.2)] + [FLAT] * 4)
    rows = [("SOLUSDT", 0.02, worse)] * 3 + [("SOLUSDT", 0.02, better)]
    bps = f1.break_even_bps(rows)
    assert bps is not None and bps > 0
    taker = [net_return(p["taker"]["gross"], fee=0.00075, entry_cost=bps / 1e4, exit_cost=0.0005) / 0.02 for _, _, p in rows]
    maker_r = [f1.view_r(s, r, p, "observe")[1] for s, r, p in rows]
    assert sum(maker_r) / 4 - sum(taker) / 4 == pytest.approx(0, abs=1e-3)     # arrondi au centième de pb
    assert f1.break_even_bps([("SOLUSDT", 0.02, better)]) == 0.0       # maker déjà meilleur sans écart
    wide = {"symbol": "SOLUSDT", "limit": 100.0, "stop_pct": -2.0, "target_pct": 10.0, "bars": 6}
    big = f1.resolve_one(bars([(100.2, 108.0, 100.1, 107.0)] + [(107.0, 107.2, 106.9, 107.0)] * 5), wide)
    assert big["taker"]["gross"] == pytest.approx(107.0 / 100.2)
    assert f1.break_even_bps([("SOLUSDT", 0.02, big)]) is None          # il faudrait plus de 500 pb


@pytest.mark.parametrize(("observed", "robust", "ended", "expected"), [
    ({"n": 600, "days": 40, "diff_ci": (0.01, 0.05)}, {"diff_ci": (0.002, 0.04)}, True, f1.MAKER_BETTER),
    ({"n": 600, "days": 40, "diff_ci": (0.01, 0.05)}, {"diff_ci": (-0.01, 0.04)}, True, f1.NO_DIFFERENCE),
    ({"n": 600, "days": 40, "diff_ci": (-0.05, -0.01)}, {"diff_ci": (-0.06, -0.02)}, True, f1.TAKER_BETTER),
    ({"n": 499, "days": 40, "diff_ci": (0.01, 0.05)}, {"diff_ci": (0.01, 0.04)}, True, f1.INSUFFICIENT),
    ({"n": 600, "days": 29, "diff_ci": (0.01, 0.05)}, {"diff_ci": (0.01, 0.04)}, True, f1.INSUFFICIENT),
    ({"n": 600, "days": 40, "diff_ci": (0.01, 0.05)}, {"diff_ci": None}, True, f1.INSUFFICIENT),
    ({"n": 600, "days": 40, "diff_ci": (0.01, 0.05)}, {"diff_ci": (0.01, 0.04)}, False, f1.RUNNING),
])
def test_f1_decision_threshold(observed, robust, ended, expected):
    assert f1.verdict({"observe": observed, "robuste": robust}, ended=ended) == expected


def test_f1_entry_starts_when_the_plan_exists():
    assert f1.entry_time("2026-03-02T23:12:30+00:00") == pd.Timestamp("2026-03-02T23:15", tz="UTC")
    assert f1.entry_time("2026-03-02T23:15:00+00:00") == pd.Timestamp("2026-03-02T23:15", tz="UTC")


# --- Pré-inscription et gel ---------------------------------------------------------------------------------------

def rule_a(x):
    return x + 1


def rule_b(x):
    return x + 2


DOC = """# Titre

## Cadre commun
texte commun

## T_TEST : essai
Hypothèse : h. Règles : r. Paramètres : p. Métrique : m. Seuil de décision : s. Date d'évaluation : d.

## Démarrages
- rien
"""


def a_test(**changes):
    base = {"test_id": "T_TEST", "title": "essai", "hypothesis": "h", "params": {"k": 1}, "rule_objects": (rule_a,)}
    return registry.ForwardTest(**(base | changes))


def test_section_and_required_fields():
    assert registry.section(DOC, "T_TEST").startswith("## T_TEST : essai") and "Démarrages" not in registry.section(DOC, "T_TEST")
    assert registry.section(DOC, "T_AUTRE") is None
    with pytest.raises(registry.NotPreregistered, match="plusieurs"):
        registry.section(DOC + "\n## T_TEST : doublon\nautre\n", "T_TEST")
    assert registry.missing_fields("Hypothèse, Règles") == ["Paramètres", "Métrique", "Seuil de décision", "Date d'évaluation"]
    assert registry.missing_fields(registry.section(PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(), f1.TEST_ID)) == []


def test_start_once_records_a_forward_trial(settings, monkeypatch):
    with pytest.raises(registry.NotPreregistered):
        registry.start(settings, a_test(test_id="T_AUTRE"), now=NOW, allow_dirty=True, halal=HALAL, doc_text=DOC)
    with pytest.raises(registry.NotPreregistered, match="champs manquants"):
        registry.start(settings, a_test(), now=NOW, allow_dirty=True, halal=HALAL, doc_text=DOC.replace("Métrique", "M"))
    monkeypatch.setattr(registry, "current_commit", lambda: "abc+DIRTY")
    with pytest.raises(registry.DirtyCode):
        registry.start(settings, a_test(), now=NOW, halal=HALAL, doc_text=DOC)
    monkeypatch.setattr(registry, "current_commit", lambda: "abc123")
    data = registry.start(settings, a_test(), now=NOW, halal=HALAL, doc_text=DOC)
    assert data["final_at"] == "2026-05-25T12:00:00+00:00" and data["interim_at"] == "2026-04-13T12:00:00+00:00"
    assert data["halal"]["symbols"] == ["BTCUSDT", "ETHUSDT"] and data["commit"] == "abc123"
    run = ExperimentRegistry(settings.experiments_db).get(data["run_id"])
    assert run["period_label"] == registry.PERIOD_LABEL and run["kind"] == "FORWARD_TEST" and run["metrics"]["n_trials"] == 1
    assert ExperimentRegistry(settings.experiments_db).program_trials() == 0          # hors DEVELOPMENT
    assert report.forward_trials(settings) == 1
    with pytest.raises(registry.AlreadyStarted):
        registry.start(settings, a_test(), now=NOW, halal=HALAL, doc_text=DOC)
    assert registry.status(settings, a_test(), now=NOW)["state"] == registry.RUNNING
    assert registry.status(settings, a_test(), now=NOW + timedelta(days=84))["state"] == registry.ENDED


@pytest.mark.parametrize(("change", "what"), [
    ({"doc_text": DOC.replace("Seuil de décision : s.", "Seuil de décision : s2.")}, "doc"),
    ({"doc_text": DOC.replace("texte commun", "frais changés")}, "doc"),
    ({"test": {"params": {"k": 2}}}, "params"),
    ({"test": {"rule_objects": (rule_b,)}}, "code"),
])
def test_any_change_after_start_stops_the_test_for_good(settings, change, what):
    registry.start(settings, a_test(), now=NOW, allow_dirty=True, halal=HALAL, doc_text=DOC)
    assert registry.check_frozen(settings, a_test(), now=NOW, doc_text=DOC) is None
    assert registry.check_frozen(settings, a_test(), now=NOW, doc_text=DOC.replace("- rien", "- démarré")) is None  # hors section
    changed = a_test(**change.get("test", {}))
    reason = registry.check_frozen(settings, changed, now=NOW + timedelta(hours=1), doc_text=change.get("doc_text", DOC))
    assert reason is not None and what in reason
    assert registry.status(settings, a_test(), now=NOW)["state"] == registry.STOPPED
    assert registry.check_frozen(settings, a_test(), now=NOW + timedelta(hours=2), doc_text=DOC) is None   # ne reprend pas


def test_whole_frozen_modules_and_config_values_are_covered(settings, tmp_path, monkeypatch):
    """Une constante de module (non lue par une fonction gelée) ou une valeur de configuration qui change : arrêt."""
    import importlib
    module = tmp_path / "regle_gelee.py"
    module.write_text("SEUIL = 1\n\n\ndef regle(x):\n    return x\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    test = a_test(rule_objects=(), frozen_modules=("regle_gelee",), config_keys=("data.setup_timeframe",))
    registry.start(settings, test, now=NOW, allow_dirty=True, halal=HALAL, doc_text=DOC)
    assert registry.check_frozen(settings, test, now=NOW, doc_text=DOC) is None
    module.write_text("SEUIL = 22\n\n\ndef regle(x):\n    return x\n")
    importlib.invalidate_caches()
    importlib.reload(importlib.import_module("regle_gelee"))
    assert "code" in registry.check_frozen(settings, test, now=NOW, doc_text=DOC)


def test_config_value_change_stops_the_test(settings, monkeypatch):
    test = a_test(config_keys=("data.setup_timeframe",))
    registry.start(settings, test, now=NOW, allow_dirty=True, halal=HALAL, doc_text=DOC)
    monkeypatch.setenv("CSI_DATA__SETUP_TIMEFRAME", "1h")
    from crypto_signal_intelligence.config import load_settings
    other = load_settings()
    assert other.data.setup_timeframe == "1h"
    assert "params" in registry.check_frozen(other, test, now=NOW, doc_text=DOC)


def test_frozen_check_continues_after_the_evaluation_date_and_spares_a_missing_document(settings, monkeypatch, tmp_path):
    registry.start(settings, a_test(), now=NOW, allow_dirty=True, halal=HALAL, doc_text=DOC)
    monkeypatch.setattr(registry, "doc_path", lambda: tmp_path / "absent.md")
    alert = registry.check_frozen(settings, a_test(), now=NOW + timedelta(days=85))
    assert alert.startswith("ALERTE") and registry.status(settings, a_test(), now=NOW)["state"] == registry.RUNNING
    reason = registry.check_frozen(settings, a_test(), now=NOW + timedelta(days=85),
                                   doc_text=DOC.replace("Règles : r.", "Règles : r2."))
    assert "doc" in reason and registry.status(settings, a_test(), now=NOW)["state"] == registry.STOPPED


def test_f1_freezes_the_measurement_code():
    names = {getattr(o, "__name__", "") for o in registry.frozen_objects(f1.TEST)}
    for name in ("crypto_signal_intelligence.forward.f1", "crypto_signal_intelligence.forward.maker",
                 "crypto_signal_intelligence.forward.costs", "day_block_ci95", "replay_plan"):
        assert name in names, name
    assert f1.TEST.config_keys == ("data.setup_timeframe",)


def test_f1_preregistration_is_complete_and_matches_the_code():
    text = registry.section(PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8"), f1.TEST_ID)
    assert registry.missing_fields(text) == []
    for value in ("15 minutes", "4 bougies", "30 jours et 500 décisions", "10 000 tirages, graine 20261002", "84 jours",
                  "observe", "robuste", "écart d'équilibre"):
        assert value in text, value


# --- F1 de bout en bout -------------------------------------------------------------------------------------------

def add_plan(settings, symbol, decision_time, *, horizon="24h", bars_count=96, close=100.0, recorded_at=None):
    with tk.connect(settings) as db:
        cursor = db.execute(
            """INSERT INTO plans (day, symbol, horizon, bars, recorded_at, decision_time, close, stop_pct, target_pct, state)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (f"{decision_time:%Y-%m-%d}", symbol, horizon, bars_count, (recorded_at or decision_time).isoformat(),
             decision_time.isoformat(), close, -2.0, 3.0, "HISTORIQUE_NEUTRE"))
        return cursor.lastrowid


def test_f1_end_to_end(settings):
    candles = candles_fixture(2500, "15m", symbol="ETHUSDT", start="2026-03-01", seed=11)
    CandleStore(settings.data_dir).save(candles, "ETHUSDT", "15m")
    begin = candles["open_time"].iloc[300]
    start = registry.start(settings, f1.TEST, now=begin.to_pydatetime(), allow_dirty=True, halal=ETH_ONLY)
    journal = registry.journal_for(settings, f1.TEST_ID)
    decision_time = candles["open_time"].iloc[400]
    close = float(candles["close"].iloc[399])
    recorded = decision_time + pd.Timedelta(minutes=20)                    # plan calculé 20 min après la décision
    plan_id = add_plan(settings, "ETHUSDT", decision_time, close=close, recorded_at=recorded)
    add_plan(settings, "PEPEUSDT", decision_time, recorded_at=recorded)    # hors liste halal figée
    add_plan(settings, "ETHUSDT", candles["open_time"].iloc[200], recorded_at=begin - STEP)   # avant le démarrage
    now = (recorded + STEP).to_pydatetime()
    assert f1.record_decisions(settings, journal, start, now=now) == {"decisions": 1, "skipped": 1}
    assert f1.record_decisions(settings, journal, start, now=now) == {"decisions": 0, "skipped": 0}   # une seule fois
    decision_entry = next(journal.entries({f1.DECISION}))["data"]
    entry = decision_time + 2 * STEP                                       # première ouverture après l'enregistrement
    assert decision_entry["entry_time"] == entry.isoformat() and decision_entry["delay_minutes"] == 30.0
    assert decision_entry["limit"] == close and len(decision_entry["plan_sha256"]) == 64
    assert f1.resolve(settings, journal, now=(entry + 50 * STEP).to_pydatetime()) == {}       # horizon pas écoulé
    later = (entry + 200 * STEP).to_pydatetime()
    counts = f1.resolve(settings, journal, now=later)
    assert sum(counts.values()) == 1 and f1.resolve(settings, journal, now=later) == {}
    result = next(journal.entries({f1.RESOLUTION}))["data"]
    after = candles[candles["open_time"] >= entry].reset_index(drop=True)
    assert result["paths"]["taker"] == taker_path(after, stop_pct=-2, target_pct=3, horizon_bars=96, step=STEP)
    for fill in f1.FILLS:
        assert result["paths"]["maker"][fill] == maker_path(after, limit=close, stop_pct=-2, target_pct=3, horizon_bars=96,
                                                            valid_bars=4, step=STEP,
                                                            through=f1.through_for(fill, "ETHUSDT"))
    assert result["plan_id"] == plan_id and result["n_bars"] == 96 and len(result["first_bars"]) == 4
    assert journal.verify()["ok"]
    out = f1.stats(journal, start, now=later)
    assert out["pending"] == 0 and out["skipped"] == 1 and out["verdict"] == f1.RUNNING
    assert out["horizons"]["24h"]["observe"]["n"] == 1 and out["plan_codes"] == 1


def test_f1_waits_for_missing_candles_then_marks_a_gap(settings):
    start = registry.start(settings, f1.TEST, now=NOW, allow_dirty=True, halal=ETH_ONLY)
    journal = registry.journal_for(settings, f1.TEST_ID)
    decision_time = pd.Timestamp(NOW) + 4 * STEP
    add_plan(settings, "ETHUSDT", decision_time)
    f1.record_decisions(settings, journal, start, now=decision_time.to_pydatetime())
    assert f1.resolve(settings, journal, now=(decision_time + timedelta(days=2)).to_pydatetime()) == {}
    assert f1.resolve(settings, journal, now=(decision_time + timedelta(days=3, hours=1)).to_pydatetime()) == {"TROU": 1}
    out = f1.stats(journal, start, now=(decision_time + timedelta(days=3, hours=2)).to_pydatetime())
    assert out["pending"] == 0 and out["horizons"]["24h"]["observe"]["missing_entry"] == 1


def test_f1_stats_count_a_duplicated_entry_once(settings):
    start = registry.start(settings, f1.TEST, now=NOW, allow_dirty=True, halal=ETH_ONLY)
    journal = registry.journal_for(settings, f1.TEST_ID)
    decision = {"plan_id": 7, "symbol": "ETHUSDT", "horizon": "24h", "bars": 6, "entry_time": T0.isoformat(),
                "decision_time": T0.isoformat(), "limit": 100.0, "stop_pct": -2.0, "target_pct": 3.0}
    resolution = {"plan_id": 7, "symbol": "ETHUSDT", "horizon": "24h", "risk": 0.02,
                  "paths": paths_of([FILL, (100.0, 103.5, 99.9, 103.2)] + [FLAT] * 4, "ETHUSDT")}
    for _ in range(2):
        journal.append(f1.DECISION, decision, now=NOW)
    out = f1.stats(journal, start, now=NOW)
    assert out["pending"] == 1 and out["pending_primary"] == 1
    for _ in range(2):
        journal.append(f1.RESOLUTION, resolution, now=NOW)
    out = f1.stats(journal, start, now=NOW + timedelta(days=90))
    assert out["pending"] == 0 and out["horizons"]["24h"]["observe"]["n"] == 1 and out["ended"]
    assert out["verdict"] == f1.INSUFFICIENT


# --- Un seul passage à la fois ------------------------------------------------------------------------------------

def _hold_lock(root, ready, release):
    import os
    os.environ["CSI_ROOT"] = root
    from crypto_signal_intelligence.config import load_settings
    with runner.process_lock(load_settings()) as acquired:
        ready.put(acquired)
        release.get(timeout=20)


def test_a_second_process_skips_its_pass(settings):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Queue(), context.Queue()
    worker = context.Process(target=_hold_lock, args=(str(settings.root), ready, release))
    worker.start()
    try:
        assert ready.get(timeout=30) is True
        assert runner.daily(settings, now=NOW + timedelta(hours=1), force=True) == {"skipped": "un autre passage est en cours"}
    finally:
        release.put(True)
        worker.join(timeout=30)
    with runner.process_lock(settings) as acquired:
        assert acquired


# --- Relevé des dérivés -------------------------------------------------------------------------------------------

class FakeFutures:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def get_json(self, path, params=None):
        self.calls.append((path, params))
        if self.fail and self.fail(path, params):
            raise HttpError("refusé")
        if path == "/fapi/v1/premiumIndex":
            return [{"symbol": "BTCUSDT", "markPrice": "60000", "lastFundingRate": "0.0001"},
                    {"symbol": "1000BONKUSDT", "markPrice": "0.02", "lastFundingRate": "0.0002"}]
        if path == "/fapi/v1/fundingRate":
            return [{"symbol": params["symbol"], "fundingTime": 1, "fundingRate": "0.0001", "markPrice": "1"}]
        return [{"timestamp": 2, "sumOpenInterest": "10", "sumOpenInterestValue": "600000"}]

    def close(self):
        pass


DERIV_HALAL = HalalList(("BTCUSDT", "BONKUSDT", "DOGEUSDT"), {}, "c" * 64, "d" * 64)


def test_derivatives_log_once_a_day(settings):
    now = datetime(2026, 3, 3, 0, 20, tzinfo=UTC)
    client = FakeFutures()
    out = derivlog.record_day(settings, now=now, client=client, halal=DERIV_HALAL, sleep=lambda s: None)
    assert out == {"day": "2026-03-03", "pairs": 2, "no_perpetual": 1, "errors": 0}
    log = derivlog.journal(settings)
    pairs = [e["data"] for e in log.entries({derivlog.PAIR})]
    assert [p["perp"] for p in pairs] == ["BTCUSDT", "1000BONKUSDT"] and pairs[0]["funding"] == [[1, "0.0001", "1"]]
    assert client.calls[2][1] == {"symbol": "BTCUSDT", "period": "1h", "limit": 100}
    assert next(log.entries({derivlog.DAY}))["data"]["without_perpetual"] == ["DOGEUSDT"]
    assert derivlog.record_day(settings, now=now + timedelta(hours=5), client=client, halal=DERIV_HALAL)["already"]
    calls = len(client.calls)
    failing = FakeFutures(fail=lambda path, params: path == "/futures/data/openInterestHist")
    out = derivlog.record_day(settings, now=now + timedelta(days=1), client=failing, halal=DERIV_HALAL, sleep=lambda s: None)
    assert out["errors"] == 2 and out["pairs"] == 0 and len(client.calls) == calls
    assert derivlog.summary(settings)["days"] == 2 and log.verify()["ok"]


def test_derivatives_log_retries_the_day_when_the_common_call_fails(settings):
    now = datetime(2026, 3, 3, 0, 20, tzinfo=UTC)
    down = FakeFutures(fail=lambda path, params: path == "/fapi/v1/premiumIndex")
    assert derivlog.record_day(settings, now=now, client=down, halal=DERIV_HALAL, sleep=lambda s: None)["errors"] == 1
    assert derivlog.recorded_days(derivlog.journal(settings)) == set()                 # jour non clos
    out = derivlog.record_day(settings, now=now + timedelta(hours=1), client=FakeFutures(), halal=DERIV_HALAL,
                              sleep=lambda s: None)
    assert out["pairs"] == 2 and derivlog.recorded_days(derivlog.journal(settings)) == {"2026-03-03"}


def test_derivatives_log_day_is_the_previous_utc_day_before_00_10():
    assert derivlog.day_of(datetime(2026, 3, 3, 0, 5, tzinfo=UTC)) == "2026-03-02"
    assert derivlog.day_of(datetime(2026, 3, 3, 0, 15, tzinfo=UTC)) == "2026-03-03"


# --- Passage et rapport -------------------------------------------------------------------------------------------

def test_runner_and_daily_report(settings, monkeypatch):
    monkeypatch.setattr(derivlog, "record_day", lambda settings, now: {"day": "x", "pairs": 0})
    registry.start(settings, f1.TEST, now=NOW, allow_dirty=True, halal=ETH_ONLY)
    out = runner.daily(settings, now=NOW + timedelta(hours=1), force=True)
    assert f1.TEST_ID in out["tests"] and out["report"].endswith("2026-03-02.md")
    text = (settings.reports_dir / "forward" / "2026-03-02.md").read_text(encoding="utf-8")
    assert "F1_MAKER_TAKER" in text and "EN_COURS" in text and "unlocks : NON FAIT" in text and "FORWARD" in text
    assert report.latest(settings)["tests"][0]["state"] == registry.RUNNING
    assert runner.daily(settings, now=NOW + timedelta(hours=1, minutes=10)) is None    # au plus un passage par heure


def test_runner_survives_a_derivatives_failure_and_reports_a_stopped_test(settings, monkeypatch):
    def boom(settings, now):
        raise RuntimeError("panne")
    monkeypatch.setattr(derivlog, "record_day", boom)
    registry.start(settings, f1.TEST, now=NOW, allow_dirty=True, halal=ETH_ONLY)
    registry.journal_for(settings, f1.TEST_ID).append(registry.STOP, {"reason": "test"}, now=NOW)
    out = runner.daily(settings, now=NOW + timedelta(hours=2), force=True)
    assert "panne" in out["derivatives"]["error"] and out["report"]
    assert report.latest(settings)["tests"][0]["stats"]["verdict"] == registry.STOPPED
