"""Familles CH, ST et AN du zoo : causalité (recalcul tronqué et futur falsifié, docs/ZOO.md § 7) de chacun des 74
tests, mutations « pivot daté à son extrême » (et équivalents : sommet de swing de Gann, order block, boîte Renko)
DÉTECTÉES avec leur témoin correct, comptage à blanc sur le marché synthétique. Données SYNTHÉTIQUES, signaux et
comptes seulement, aucun rendement."""
from __future__ import annotations

from functools import partial

import numpy as np
import pytest

from crypto_signal_intelligence.research.zoo import causality as C
from crypto_signal_intelligence.research.zoo import h0
from crypto_signal_intelligence.research.zoo import indicators as ind
from crypto_signal_intelligence.research.zoo.data import (
    DAY_H,
    DECOUVERTE,
    HOUR_NS,
    LATENCE_NS,
    Bougies,
    Paire,
    agreger,
)
from crypto_signal_intelligence.research.zoo.run import compter
from crypto_signal_intelligence.research.zoo.spec import PAR_ID, Contexte, Signaux
from crypto_signal_intelligence.research.zoo.spec import tests_de as choisir
from crypto_signal_intelligence.research.zoo.strategies import analysts as A
from crypto_signal_intelligence.research.zoo.strategies import candles as CH
from crypto_signal_intelligence.research.zoo.strategies import structure as S

from .zoo_outils import etape, heure

STAGE = etape("2019-03-01", "2020-01-01")
TESTS = choisir(["CH", "ST", "AN"])


def marche(n: int = 24 * 330, seed: int = 11, vol: float = 0.012, ecart: float = 0.0004) -> Paire:
    """Paire 1 h synthétique dont l'ouverture s'écarte un peu de la clôture précédente (`ecart`), pour que les
    figures à écart de TA-Lib apparaissent aussi."""
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, vol, n)))
    opens = np.r_[closes[0], closes[:-1]] * (1 + rng.normal(0, ecart, n))
    high = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, 0.004, n)))
    low = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, 0.004, n)))
    open_h = heure("2019-01-01") + np.arange(n, dtype=np.int64)
    vol_usdt = np.full(n, 1e6)
    bars = Bougies(1, open_h, (open_h + 1) * HOUR_NS + LATENCE_NS, opens, high, low, closes, vol_usdt, vol_usdt / 2,
                   vol_usdt / 100)
    return Paire("AAAUSDT", bars)


@pytest.fixture(scope="module")
def paire():
    return marche()


def _contexte(test, pair: Paire) -> Contexte:
    unit = test.execution_heures
    return Contexte(pair.symbole, unit, test.params, STAGE.debut_ns,
                    quotidien=pair.unite(DAY_H) if unit != DAY_H else None)


def _instants(fn, test, pair: Paire, n_random: int = 6, n_signal: int = 6) -> np.ndarray:
    unit = test.execution_heures
    bars = pair.unite(unit)
    rng = np.random.default_rng(sum(map(ord, test.id)))
    random = C.instants_aleatoires(bars, STAGE, n_random, rng)
    signals = C.instants_de_signal(fn, bars, _contexte(test, pair), STAGE.debut_ns, STAGE.fin_ns)
    if len(signals) > n_signal:
        signals = np.sort(rng.choice(signals, size=n_signal, replace=False))
    return np.unique(np.r_[random, signals]).astype(np.int64)


@pytest.mark.parametrize("test", TESTS, ids=[t.id for t in TESTS])
def test_causalite_ch_st_an(test, paire):
    fn = test.strategie_fonction()
    unit = test.execution_heures
    taus = _instants(fn, test, paire)
    assert len(taus) >= 6
    report = C.verifier(fn, paire.h1, unit, _contexte(test, paire), taus, quotidien=unit != DAY_H, test_id=test.id)
    assert report.ok, report.resume()


def test_signaux_presents_sur_le_marche_synthetique(paire):
    """Chaque test produit au moins un signal sur la paire synthétique de 330 jours (avec écarts d'ouverture), sauf
    les règles journalières trop lentes pour un an (plus haut annuel ou historique : 365 jours d'historique)."""
    silent = []
    for test in TESTS:
        bars = paire.unite(test.execution_heures)
        if not test.strategie_fonction()(bars, _contexte(test, paire)).entree.any():
            silent.append(test.id)
    assert set(silent) <= {"ST11_1j", "ST12_1j", "CH10_1j"}, silent


# --- Mutations : pivot (ou niveau) daté à son extrême --------------------------------------------------------------

def renko_fin_de_mois(frame: Bougies, ctx: Contexte) -> Signaux:
    """MUTATION : boîte Renko lue sur l'ATR journalier de la FIN du mois (au lieu du 1er)."""
    daily = frame if frame.unite == DAY_H else (ctx.quotidien if ctx.quotidien is not None else agreger(frame, DAY_H))
    atr = ind.atr(daily.haut, daily.bas, daily.cloture, ctx.params["atr"])
    months = (frame.ouverture_h // DAY_H).astype("datetime64[D]").astype("datetime64[M]")
    next_first = (months + np.timedelta64(1, "M")).astype("datetime64[D]").astype(np.int64)
    idx = np.searchsorted(daily.ouverture_h // DAY_H, next_first, side="left") - 1
    entry, down = CH.briques_renko(frame.cloture, atr[idx])
    return Signaux(entry, down)


MUTATIONS = {
    "ST01_4h": (S._dow_impl, partial(S._dow_impl, extreme=True)),
    "ST03_4h": (S._fibonacci_impl, partial(S._fibonacci_impl, extreme=True)),
    "ST05_1h": (S._fibonacci_impl, partial(S._fibonacci_impl, extreme=True)),
    "AN09_4h": (A._elliott_impl, partial(A._elliott_impl, extreme=True)),
    "AN13_1j": (A._gann_impl, partial(A._gann_impl, extreme=True)),
    "CH21_1j": (CH.renko, renko_fin_de_mois),
}


@pytest.mark.parametrize("test_id", sorted(MUTATIONS))
def test_mutation_pivot_extreme_detectee(test_id, paire):
    test = PAR_ID[test_id]
    good, bad = MUTATIONS[test_id]
    unit = test.execution_heures
    ctx = _contexte(test, paire)
    bars = paire.unite(unit)
    full_good, full_bad = good(bars, ctx), bad(bars, ctx)
    # instants où les deux versions diffèrent sur l'historique complet (là où une fuite peut se voir), plus des
    # instants de signal et des instants au hasard de l'étape
    differ = bars.disponible[(full_good.entree != full_bad.entree) | (full_good.sortie != full_bad.sortie)]
    assert len(differ) > 0, f"la mutation ne change rien pour {test_id}"
    rng = np.random.default_rng(5)
    differ = rng.choice(differ, size=min(20, len(differ)), replace=False)
    taus = np.unique(np.r_[differ, _instants(good, test, paire, 10, 15)]).astype(np.int64)
    witness = C.verifier(good, paire.h1, unit, ctx, taus, quotidien=unit != DAY_H, test_id=test_id)
    assert witness.ok, witness.resume()
    mutant = C.verifier(bad, paire.h1, unit, ctx, taus, quotidien=unit != DAY_H, test_id=f"mutation:{test_id}")
    assert not mutant.ok, f"mutation non détectée pour {test_id}"


def test_breaker_order_block_date_a_son_origine_sans_effet(paire):
    """AN06 ne lit pas de pivot ZigZag : l'order block est connu au déplacement ; le dater à sa bougie d'origine ne
    change aucun signal (une clôture au-dessus de la zone avant le déplacement serait une bougie haussière, qui annule
    l'OB) : il n'y a rien à détecter, la causalité ordinaire suffit (test_causalite_ch_st_an)."""
    test = PAR_ID["AN06_4h"]
    bars = paire.unite(4)
    good = A._breaker_impl(bars, _contexte(test, paire))
    moved = A._breaker_impl(bars, _contexte(test, paire), origine=True)
    assert good.entree.sum() > 5
    assert np.array_equal(good.entree, moved.entree) and np.array_equal(good.sortie, moved.sortie)


def test_pivots_dates_a_la_confirmation(paire):
    """Aucun pivot ZigZag n'est connu avant la bougie qui le confirme, et la confirmation est postérieure à
    l'extrême ; le recalcul tronqué à la bougie de confirmation retrouve le pivot, pas avant."""
    bars = paire.unite(4)
    piv = S.pivots_zigzag(bars, 2.5)
    assert len(piv.indice) > 10 and (piv.connu > piv.indice).all()
    for k in range(0, len(piv.indice), max(1, len(piv.indice) // 8)):
        known = int(piv.connu[k])
        before = S.pivots_zigzag(bars.tete(known), 2.5)
        at = S.pivots_zigzag(bars.tete(known + 1), 2.5)
        assert int(piv.indice[k]) not in before.indice.tolist()
        assert int(piv.indice[k]) in at.indice.tolist()


# --- Comptage à blanc sur le marché synthétique ---------------------------------------------------------------------

def test_comptage_ch_st_an_synthetique():
    source = h0.SourceSynthetique(5, "H0a", n_paires=3)
    counts = compter(TESTS, source, DECOUVERTE)
    assert set(counts) == {t.id for t in TESTS}
    for test_id, c in counts.items():
        assert c["forme"] == "a" and {"compteurs", "jours_exposition", "blocs_avec_exposition"} <= set(c), test_id
    # ouverture = clôture précédente sur ce marché : les figures à écart de TA-Lib n'y apparaissent jamais (raison
    # de leur passage en IMPOSSIBLE le 2026-10-11 : CH02, CH11, CH12, CH15, CH16)
    pair = source.paire(source.symboles[0])
    for func, trend in (("CDLINVERTEDHAMMER", "baissiere"), ("CDL3INSIDE", "baissiere"), ("CDL3OUTSIDE", "baissiere"),
                        ("CDLRISEFALL3METHODS", "haussiere"), ("CDLHOMINGPIGEON", "baissiere")):
        for unit in (1, 4, 24):
            ctx = Contexte(pair.symbole, unit, {"talib": func, "tendance": trend, "sma": 10}, 0)
            assert not CH.figure(pair.unite(unit), ctx).entree.any(), (func, unit)
    assert counts["CH03_1h"]["compteurs"]["transactions"] > 0
    assert counts["ST06_1h"]["compteurs"]["transactions"] > 0
