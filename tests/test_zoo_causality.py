"""Causalité du zoo (docs/ZOO.md § 7) : chaque stratégie TR codée est identique sur données tronquées et sous un futur
falsifié ; les quatre mutations exigées sont détectées et leurs témoins corrects passent. Données SYNTHÉTIQUES."""
from __future__ import annotations

from types import MappingProxyType

import numpy as np
import pytest

from crypto_signal_intelligence.research.zoo import causality as C
from crypto_signal_intelligence.research.zoo.data import DAY_H, HOUR_NS, SerieExterne
from crypto_signal_intelligence.research.zoo.run import panneau
from crypto_signal_intelligence.research.zoo.spec import FORME_A, Contexte
from crypto_signal_intelligence.research.zoo.spec import tests_de as choisir
from crypto_signal_intelligence.research.zoo.strategies.commun import decisions_hebdomadaires

from .zoo_outils import etape, paire, univers_toujours

STAGE = etape("2019-03-01", "2020-01-01")


def marche(n: int = 24 * 330, seed: int = 11, symbole: str = "AAAUSDT", vol: float = 0.012):
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    return paire(closes, start="2019-01-01", symbole=symbole, mecha=0.004)


TR_A = [t for t in choisir(["TR"]) if t.forme == FORME_A]


@pytest.mark.parametrize("test", TR_A, ids=[t.id for t in TR_A])
def test_strategies_tr_causales(test):
    p = marche()
    unit = test.execution_heures
    bars = p.unite(unit)
    rng = np.random.default_rng(sum(map(ord, test.id)))
    taus = C.instants_aleatoires(bars, STAGE, 6, rng)
    ctx = Contexte(p.symbole, unit, test.params, STAGE.debut_ns)
    report = C.verifier(test.strategie_fonction(), p.h1, unit, ctx, taus, quotidien=unit != DAY_H, test_id=test.id)
    assert report.ok, report.resume()


def test_instants_de_signal_d_un_echantillon():
    p = marche(seed=12)
    test = next(t for t in TR_A if t.id == "TR01_4h")
    ctx = Contexte(p.symbole, 4, test.params, STAGE.debut_ns)
    report = C.verifier_test(test, [p, marche(seed=13, symbole="BBBUSDT")], STAGE, lambda pair: ctx,
                             n_aleatoires=10, echantillon=1)
    assert report.ok and report.instants >= 10 and report.instants_signal > 0


def test_mutations_detectees_et_temoins_corrects():
    p = marche(n=24 * 200, seed=21)
    days = np.arange(int(p.h1.ouverture_h[0]) // DAY_H, int(p.h1.ouverture_h[-1]) // DAY_H + 1)
    rng = np.random.default_rng(3)
    dates = days * DAY_H * HOUR_NS
    serie = SerieExterne("x", dates + 18 * HOUR_NS, dates, rng.normal(0, 1, len(days)))
    stage = etape("2019-02-01", "2019-07-01")
    out = C.controler_mutations(p.h1, serie, stage, instants=40)
    assert set(out) == {"cloture_suivante", "bougie_datee_ouverture", "externe_sur_date", "pivot_extreme"}
    for name, result in out.items():
        assert result["temoin_ok"], name
        assert result["mutation_detectee"], name


def test_portefeuille_tr37_causal():
    pairs = [marche(n=24 * 300, seed=s, symbole=f"P{s}USDT") for s in range(30, 34)]
    board = panneau({p.symbole: p.unite(DAY_H) for p in pairs}, univers_toujours([p.symbole for p in pairs]))
    test = next(t for t in choisir(["TR"]) if t.id == "TR37_1j")
    ctx = Contexte("", DAY_H, test.params, STAGE.debut_ns, externes=MappingProxyType({}))
    _, decisions, _ = decisions_hebdomadaires(board)
    report = C.verifier_portefeuille(test.strategie_fonction(), board, ctx, decisions[5:15], test.id)
    assert report.ok, report.resume()

    def leaky(frame, ctx):                     # mutation : poids de la semaine lus sur la clôture SUIVANTE
        out = test.strategie_fonction()(frame, ctx)
        shifted = np.vstack([out.poids[1:], out.poids[-1:]])
        return type(out)(out.decisions, out.execution_h, out.symboles, shifted, out.univers)

    assert not C.verifier_portefeuille(leaky, board, ctx, decisions[5:15], "fuite").ok
