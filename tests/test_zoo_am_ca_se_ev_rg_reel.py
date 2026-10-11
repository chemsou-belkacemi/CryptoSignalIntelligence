"""Causalité des familles AM, CA, SE, EV et RG sur les DONNÉES RÉELLES de la découverte (signaux et poids seulement,
AUCUN rendement) : séries externes, calendriers et annonces réels du magasin de contexte, bougies 1 h du magasin long,
le tout coupé au 2022-12-31. Mutation « série externe jointe sur sa date » détectée sur les vraies séries.

Lancé seulement quand `CSI_ZOO_DONNEES_REELLES` désigne une racine CSI qui contient `data/context` et `long_history`
(sinon ignoré : la CI n'a pas ces données)."""
from __future__ import annotations

import os
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research.zoo import causality as C
from crypto_signal_intelligence.research.zoo.data import (
    DAY_H,
    DECOUVERTE,
    HOUR_NS,
    agreger,
    charger_evenements,
    charger_paire,
    charger_serie,
    charger_univers,
)
from crypto_signal_intelligence.research.zoo.reel import externes_pour
from crypto_signal_intelligence.research.zoo.run import panneau
from crypto_signal_intelligence.research.zoo.spec import FORME_A, FORME_B, PAR_ID, Contexte
from crypto_signal_intelligence.research.zoo.spec import tests_de as choisir
from crypto_signal_intelligence.research.zoo.strategies import calendrier as CA
from crypto_signal_intelligence.research.zoo.strategies import events as EV
from crypto_signal_intelligence.research.zoo.strategies.commun import (
    decisions_hebdomadaires,
    decisions_quotidiennes,
)

from .test_zoo_am_ca_se_ev_rg_causalite import redater, verifier_annonces, verifier_poids

RACINE = os.environ.get("CSI_ZOO_DONNEES_REELLES", "")
pytestmark = pytest.mark.skipif(not RACINE or not (Path(RACINE) / "data" / "context" / "zoo_marches.parquet").exists()
                                or not (Path(RACINE) / "long_history").exists(),
                                reason="données réelles absentes (CSI_ZOO_DONNEES_REELLES)")
FAMILLES = ["AM", "CA", "SE", "EV", "RG"]
FIN = DECOUVERTE.fin_exclusive


@pytest.fixture(scope="module")
def reel():
    previous = {k: os.environ.get(k) for k in ("CSI_ROOT", "CSI_CONFIG_FILE")}
    os.environ["CSI_ROOT"] = RACINE
    os.environ["CSI_CONFIG_FILE"] = str(Path(RACINE) / "config" / "default.toml")
    try:
        from crypto_signal_intelligence.config import load_settings
        settings = load_settings()
        series, events = externes_pour(settings, choisir(FAMILLES), FIN)
        # GLD est rangé sous le champ « close » dans `zoo_marches` (et non « value ») : lu ici avec le bon champ
        series["zoo_marches:GLD"] = charger_serie(settings, "zoo_marches", "GLD", FIN, champ="close")
        pairs = {s: charger_paire(settings, s, FIN) for s in ("BTCUSDT", "ETHUSDT", "BNBUSDT")}
        yield {"settings": settings, "series": series, "events": events, "pairs": pairs,
               "univers": charger_univers(settings, FIN)}
    finally:
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_series_et_calendriers_reels_coupes(reel):
    end = int(FIN.value)
    for key, s in reel["series"].items():
        assert len(s) > 400, key
        assert (s.disponible < end).all() and (s.date < end).all(), key
        assert (np.diff(s.date) > 0).all(), f"{key} : ordre de disponibilité ≠ ordre des dates"
    macro = reel["events"]["calendrier_macro"]
    for event, hour in (("FOMC", "14:00"), ("CPI", "08:30"), ("NFP", "08:30")):
        mine = CA.publications_ns(macro, event, hour)
        part = macro[(macro["event"] == event) & macro["scheduled"].astype(bool)]
        stored = np.sort(pd.to_datetime(part["event_time"], utc=True).dt.as_unit("ns").astype("int64").to_numpy())
        assert (mine == stored).all(), event                  # zoneinfo = heures du magasin (été et hiver)


@pytest.mark.parametrize("test_id", [t.id for t in choisir(FAMILLES) if t.forme == FORME_A])
def test_forme_a_causale_sur_donnees_reelles(reel, test_id):
    test = PAR_ID[test_id]
    pairs = [reel["pairs"]["BTCUSDT"], reel["pairs"]["ETHUSDT"]]

    def ctx_of(pair):
        return Contexte(pair.symbole, test.execution_heures, test.params, DECOUVERTE.debut_ns,
                        externes=MappingProxyType(reel["series"]), evenements=MappingProxyType(reel["events"]))

    report = C.verifier_test(test, pairs, DECOUVERTE, ctx_of, n_aleatoires=20, echantillon=1)
    assert report.ok, report.resume()


def test_se01_mutation_sur_la_vraie_serie_detectee(reel):
    test = PAR_ID["SE01_1j"]
    pair = reel["pairs"]["BTCUSDT"]
    ctx = Contexte(pair.symbole, DAY_H, test.params, DECOUVERTE.debut_ns, externes=MappingProxyType(reel["series"]))
    bars = agreger(pair.h1, DAY_H)
    taus = C.instants_aleatoires(bars, DECOUVERTE, 200, np.random.default_rng(5))
    fn = test.strategie_fonction()
    assert C.verifier(fn, pair.h1, DAY_H, ctx, taus).ok
    assert not C.verifier(lambda f, c: fn(f, redater(c)), pair.h1, DAY_H, ctx, taus).ok


def _board(reel):
    return panneau({s: p.unite(DAY_H) for s, p in reel["pairs"].items()}, reel["univers"],
                   jusqu_a_jour=DECOUVERTE.fin_jour)


@pytest.mark.parametrize("test_id", [t.id for t in choisir(FAMILLES) if t.forme == FORME_B])
def test_forme_b_causale_et_mutation_sur_donnees_reelles(reel, test_id):
    test = PAR_ID[test_id]
    board = _board(reel)
    ctx = Contexte("", DAY_H, test.params, DECOUVERTE.debut_ns, externes=MappingProxyType(reel["series"]))
    calendar = decisions_quotidiennes if test.frequence == "quotidienne" else decisions_hebdomadaires
    _, decisions, _ = calendar(board)
    pool = decisions[(decisions >= DECOUVERTE.debut_ns) & (decisions <= DECOUVERTE.fin_ns)]
    taus = np.sort(np.random.default_rng(sum(map(ord, test_id))).choice(pool, size=50, replace=False))
    fn = test.strategie_fonction()
    assert verifier_poids(fn, board, ctx, taus) == []
    if test.famille in ("AM", "SE"):
        assert verifier_poids(lambda b, c: fn(b, redater(c)), board, ctx, taus) != []
    full = fn(board, ctx)
    inside = (full.decisions >= DECOUVERTE.debut_ns) & (full.decisions <= DECOUVERTE.fin_ns)
    assert 0 < (full.poids[inside].sum(axis=1) > 0).sum() < inside.sum()      # ni jamais, ni toujours investi


def test_ev02_causal_sur_les_vraies_annonces(reel):
    """Paires cotées pendant la découverte, avec leurs vraies annonces : signal de la 1re journée complète identique
    avec les annonces coupées à τ (τ = la décision, une seconde avant, une heure après)."""
    test = PAR_ID["EV02_1j"]
    annonces = charger_evenements(reel["settings"], "annonces_binance", FIN)
    spot = annonces[annonces["category"] == "COTATION_SPOT"]
    bases = sorted({t for x in spot["tickers"].fillna("") for t in x.split(",") if t})
    checked = signals = 0
    for base in bases:
        if checked >= 12:
            break
        pair = charger_paire(reel["settings"], f"{base}USDT", FIN)
        if pair is None or pair.h1.disponible[0] < DECOUVERTE.debut_ns:
            continue
        bars = agreger(pair.h1, DAY_H)
        if len(bars) < 3:
            continue
        ctx = Contexte(pair.symbole, DAY_H, test.params, DECOUVERTE.debut_ns, evenements={EV.ANNONCES: annonces})
        signals += int(EV.cotation_binance(bars, ctx).entree[0])
        d0 = int(bars.disponible[0])
        taus = np.array([d0 - 1_000_000_000, d0, d0 + HOUR_NS, int(bars.disponible[2])])
        assert verifier_annonces(EV.cotation_binance, pair, DAY_H, ctx, taus) == [], pair.symbole
        checked += 1
    assert checked >= 5 and signals >= 1
