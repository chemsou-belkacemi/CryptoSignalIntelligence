"""Causalité des familles MR, VO et VF du zoo (docs/ZOO.md § 7) : chaque test recalculé sur données tronquées et sous
un futur falsifié (`causality.verifier`) à des instants tirés au hasard ET à des instants de signal ; des fuites
construites (journée en cours lue par un signal journalier exécuté en 1 h, VWAP de toute la journée, journée datée
par son ouverture) sont détectées. Données SYNTHÉTIQUES (trous compris), aucun rendement."""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from crypto_signal_intelligence.research.zoo import causality as C
from crypto_signal_intelligence.research.zoo import indicators as ind
from crypto_signal_intelligence.research.zoo.data import DAY_H, HOUR_NS, Bougies, Paire
from crypto_signal_intelligence.research.zoo.spec import PAR_ID, Contexte, Signaux
from crypto_signal_intelligence.research.zoo.spec import tests_de as choisir
from crypto_signal_intelligence.research.zoo.strategies import mean_reversion as MR
from crypto_signal_intelligence.research.zoo.strategies import volatility as VO

from .zoo_outils import etape, paire

STAGE = etape("2019-03-01", "2020-01-01")
FAMILLES = choisir(["MR", "VO", "VF"])


def marche(n: int = 24 * 330, seed: int = 41) -> Paire:
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0001, 0.012, n)))
    holes = tuple(int(x) for x in rng.choice(np.arange(24 * 70, n - 48), size=12, replace=False))
    p = paire(closes, start="2019-01-01", mecha=0.004, manquantes=holes)
    k = len(p.h1)
    vol = np.exp(rng.normal(13, 1, k))
    vol[rng.random(k) < 0.02] *= 10
    return Paire(p.symbole, replace(p.h1, haut=p.h1.haut * (1 + np.abs(rng.normal(0, 0.003, k))), volume=vol,
                                    achat=vol / 2))


_PAIR = marche()


def _instants(fn, test, pair: Paire, rng: np.random.Generator, n_signal: int = 6) -> tuple[np.ndarray, int]:
    unit = test.execution_heures
    bars = pair.unite(unit)
    ctx = Contexte(pair.symbole, unit, test.params, STAGE.debut_ns, quotidien=pair.unite(DAY_H))
    taus = C.instants_aleatoires(bars, STAGE, 6, rng)
    signals = C.instants_de_signal(fn, bars, ctx, STAGE.debut_ns, STAGE.fin_ns)
    if len(signals) > n_signal:
        signals = np.sort(rng.choice(signals, size=n_signal, replace=False))
    return np.unique(np.r_[taus, signals]), len(signals)


@pytest.mark.parametrize("test", FAMILLES, ids=[t.id for t in FAMILLES])
def test_strategies_mr_vo_vf_causales(test):
    rng = np.random.default_rng(sum(map(ord, test.id)))
    fn = test.strategie_fonction()
    taus, n_signal = _instants(fn, test, _PAIR, rng)
    unit = test.execution_heures
    ctx = Contexte(_PAIR.symbole, unit, test.params, STAGE.debut_ns)
    report = C.verifier(fn, _PAIR.h1, unit, ctx, taus, quotidien=unit != DAY_H, test_id=test.id,
                        signal_instants=n_signal)
    assert len(taus) >= 6 and report.ok, report.resume()


def test_verifier_test_du_moteur_sur_les_trois_familles():
    """`causality.verifier_test` (commande `csi zoo causalite`) : instants au hasard + tous les instants de signal."""
    pairs = [_PAIR, marche(seed=42)]
    for test_id in ("MR19_1j", "VO01_1j", "VF17_4h"):
        test = PAR_ID[test_id]

        def ctx_of(pair: Paire, test=test) -> Contexte:
            return Contexte(pair.symbole, test.execution_heures, test.params, STAGE.debut_ns,
                            quotidien=pair.unite(DAY_H))

        report = C.verifier_test(test, pairs, STAGE, ctx_of, n_aleatoires=8, echantillon=1)
        assert report.ok and report.instants >= 8 and report.instants_signal > 0, report.resume()


# --- Fuites construites : doivent être détectées --------------------------------------------------------------------

def _fuite_vo01_journee_en_cours(frame: Bougies, ctx: Contexte) -> Signaux:
    """VO01 qui lit l'amplitude de la journée EN COURS (journée d au lieu de d − 1)."""
    daily = VO.quotidien(frame, ctx)
    days = daily.ouverture_h // DAY_H
    pos = np.searchsorted(days, VO.jour(frame))
    ok = (pos < len(days)) & (days[np.minimum(pos, len(days) - 1)] == VO.jour(frame))
    amp = np.where(ok, (daily.haut - daily.bas)[np.minimum(pos, len(days) - 1)], np.nan)
    level = VO.ouverture_du_jour(frame) + 0.5 * amp * 0.3
    return Signaux(VO.premier_du_groupe(ind.superieur(frame.cloture, level), VO.jour(frame)),
                   VO.fin_de_periode(frame, DAY_H))


def _fuite_mr09_vwap_de_toute_la_journee(frame: Bougies, ctx: Contexte) -> Signaux:
    """MR09 dont la VWAP est celle de TOUTE la journée (bougies postérieures à t comprises)."""
    day = VO.jour(frame)
    tp = (frame.haut + frame.bas + frame.cloture) / 3
    _, inverse = np.unique(day, return_inverse=True)
    vwap = (np.bincount(inverse, tp * frame.volume) / np.bincount(inverse, frame.volume))[inverse]
    gap = frame.cloture / vwap - 1
    sd = MR.ecart_type_glissant(gap, 168, ddof=1, inclure=False)
    with np.errstate(invalid="ignore", divide="ignore"):
        z = gap / sd
    return Signaux(ind.inferieur(z, np.full(len(z), -1.0)), ind.superieur(frame.cloture, vwap))


def _fuite_mr19_journee_datee_ouverture(frame: Bougies, ctx: Contexte) -> Signaux:
    """MR19 dont la journée est datée par son OUVERTURE : à 01:00, l'IBS lu est celui de la journée en cours."""
    daily = VO.quotidien(frame, ctx)
    start_ns = daily.ouverture_h * HOUR_NS
    j = np.searchsorted(start_ns, frame.disponible, side="right") - 1
    span = daily.haut - daily.bas
    with np.errstate(invalid="ignore", divide="ignore"):
        value = np.where(span > 0, (daily.cloture - daily.bas) / span, np.nan)
    level = np.where((j >= 0) & (VO.heure(frame) == 0), value[np.maximum(j, 0)], np.nan)
    return Signaux(ind.inferieur(level, np.full(len(frame), 0.5)), None)


@pytest.mark.parametrize("fuite", [_fuite_vo01_journee_en_cours, _fuite_mr09_vwap_de_toute_la_journee,
                                   _fuite_mr19_journee_datee_ouverture], ids=["VO01", "MR09", "MR19"])
def test_fuites_detectees(fuite):
    bars = _PAIR.h1
    rng = np.random.default_rng(5)
    ctx = Contexte(_PAIR.symbole, 1, {}, STAGE.debut_ns, quotidien=_PAIR.unite(DAY_H))
    signals = C.instants_de_signal(fuite, bars, ctx, STAGE.debut_ns, STAGE.fin_ns)
    assert len(signals) > 0
    taus = np.sort(rng.choice(signals, size=min(30, len(signals)), replace=False))
    report = C.verifier(fuite, bars, 1, Contexte(_PAIR.symbole, 1, {}, STAGE.debut_ns), taus, quotidien=True,
                        test_id=fuite.__name__)
    assert not report.ok
