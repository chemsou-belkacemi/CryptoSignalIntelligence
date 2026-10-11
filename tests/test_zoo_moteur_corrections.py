"""Corrections du moteur du zoo après le codage des familles (2026-10-11) : EV02 de bout en bout sur synthétique
(univers d'exception, R180), effet de puissance planté pendant l'exposition seulement, série GLD lue sous « close »,
contexte journalier coupé, blocs exposés de la forme (b), TR37/TR40 sur la dernière journée cotée, causalité qui coupe
et falsifie séries, événements et tous les champs du panneau. Données SYNTHÉTIQUES (aucun rendement réel)."""
from __future__ import annotations

from datetime import UTC, datetime
from types import MappingProxyType

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.research.zoo import causality as C
from crypto_signal_intelligence.research.zoo import h0
from crypto_signal_intelligence.research.zoo import measure as M
from crypto_signal_intelligence.research.zoo.data import DAY_H, DECOUVERTE, HOUR_NS, VALIDATION, SerieExterne
from crypto_signal_intelligence.research.zoo.run import (
    blocs_exposes,
    compter,
    contexte,
    mesurer_tout,
    panneau,
)
from crypto_signal_intelligence.research.zoo.spec import PAR_ID, Contexte, Poids, Signaux
from crypto_signal_intelligence.research.zoo.strategies.commun import decisions_hebdomadaires
from crypto_signal_intelligence.research.zoo.synth import annonces_ev02

from .test_zoo_measure import _grilles_hebdo, _poids
from .zoo_outils import etape, paire, univers_toujours

COTATIONS = {f"N{k:02d}USDT": pd.Timestamp("2019-02-01", tz="UTC") + pd.Timedelta(days=9 * k) for k in range(40)} | \
            {f"V{k:02d}USDT": pd.Timestamp("2017-09-01", tz="UTC") + pd.Timedelta(days=5 * k) for k in range(20)}


# --- EV02 : univers d'exception de bout en bout ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def source_ev02():
    real = pd.DataFrame({"category": ["COTATION_SPOT"], "tickers": ["N03"], "detail": ["Binance Will List N (N03)"],
                         "available_at": [pd.Timestamp("2019-02-20", tz="UTC")]})
    events = {"annonces_binance": annonces_ev02(real, COTATIONS)}
    return h0.SourceSynthetique(2, "H0a", n_paires=4, evenements=events, cotations=COTATIONS)


def test_ev02_bout_en_bout_sur_synthetique(source_ev02):
    test = PAR_ID["EV02_1j"]
    mesures, refs = mesurer_tout([test], source_ev02, [DECOUVERTE, VALIDATION])
    m = mesures[("EV02_1j", "decouverte")]
    # 40 paires cotées pendant la découverte, annonce connue avant la clôture de leur 1re journée : une position
    # chacune, sans aucune condition d'appartenance au top 40 (les paires N.. n'y sont jamais)
    assert m.n_transactions == 40 and set(m.paires) == set(c for c in COTATIONS if c.startswith("N"))
    assert m.e.sum() == pytest.approx(40 * 30)
    je = refs.jours["decouverte"]
    assert je.r180 is not None and np.abs(je.r180).sum() > 0
    assert m.ref.sum() == pytest.approx(float((m.e * je.r180).sum()))
    assert mesures[("EV02_1j", "validation")].n_transactions == 0
    counts = compter([test], source_ev02, DECOUVERTE)
    assert counts["EV02_1j"]["compteurs"]["transactions"] == 40


def test_ev02_annonce_reelle_prise_pour_la_paire_correspondante():
    table = annonces_ev02(pd.DataFrame({"category": ["COTATION_SPOT"], "tickers": ["N03"], "detail": ["x (N03)"],
                                        "available_at": [pd.Timestamp("2019-02-20", tz="UTC")]}), COTATIONS)
    row = table.set_index("symbol").loc["N03USDT"]
    assert row["reelle"] and row["available_at"] == pd.Timestamp("2019-02-20", tz="UTC")
    other = table.set_index("symbol").loc["N04USDT"]
    assert not other["reelle"] and other["event_time"] == COTATIONS["N04USDT"] - pd.Timedelta(hours=24)


def test_r180_de_l_etape_a_la_main():
    from crypto_signal_intelligence.research.zoo.data import LATENCE_NS, Bougies
    from crypto_signal_intelligence.research.zoo.run import r180_etape
    stage = etape("2020-01-01", "2020-01-04")
    d0 = int(stage.debut_jour)

    def bars(first_day: int, closes: list[float]) -> Bougies:
        open_h = (first_day + np.arange(len(closes))) * DAY_H
        c = np.asarray(closes, dtype=float)
        return Bougies(DAY_H, open_h.astype(np.int64), (open_h + DAY_H) * HOUR_NS + LATENCE_NS, c, c, c, c, c, c, c)

    old = bars(d0 - 300, [100.0] * 299 + [100.0, 110.0, 99.0, 99.0])
    young = bars(d0 - 50, [10.0] * 49 + [10.0, 20.0, 20.0, 20.0])
    out = r180_etape(stage, {"OLD": old, "YOUNG": young})
    assert out[:3] == pytest.approx([0.10, -0.10, 0.0])          # seule la paire cotée depuis plus de 180 jours


# --- Puissance : effet planté pendant l'exposition seulement ---------------------------------------------------------

def test_effet_plante_proportionnel_a_l_exposition():
    grids, first = _grilles_hebdo({"AAAUSDT": [100, 110, 99, 120, 120], "BBBUSDT": [50, 50, 60, 54, 54]})
    stage = etape("2018-12-31", "2019-01-22")
    exec_h = [first + 168 * k for k in (0, 1, 2)]
    weights = [[0.5, 0.0], [0.0, 0.0], [0.5, 0.5]]
    mesure = M.mesurer_b(PAR_ID["TR37_1j"], _poids(exec_h, weights, ["AAAUSDT", "BBBUSDT"],
                                                   univers=np.ones((3, 2), bool)), grids, stage)
    assert mesure.exposition_hebdo == pytest.approx([0.5, 0.0, 1.0])
    base = sum(mesure.contributions_annees(M.NUL).values())
    planted = sum(mesure.contributions_annees(M.NUL, 0.01).values())
    assert planted - base == pytest.approx(0.01 * (0.5 + 0.0 + 1.0))
    never = M.mesurer_b(PAR_ID["AM03_1j"], _poids(exec_h, np.zeros((3, 2)), ["AAAUSDT", "BBBUSDT"],
                                                  univers=np.ones((3, 2), bool)), grids, stage)
    assert never.exposition_moyenne() == 0.0
    assert h0._cout_moyen(never) is None                       # jamais investie : aucune puissance possible
    assert sum(never.contributions_annees(M.NUL, 0.02).values()) == pytest.approx(
        sum(never.contributions_annees(M.NUL).values()))


# --- GLD lu sous « close » -------------------------------------------------------------------------------------------

def test_series_am_lues_avec_leur_champ(settings):
    from crypto_signal_intelligence.context import zoo
    from crypto_signal_intelligence.research.zoo.reel import SERIES_AM, externes_pour
    now = datetime(2026, 10, 10, tzinfo=UTC)
    days = pd.date_range("2019-01-01", periods=40, freq="D", tz="UTC")
    for store, key, field in SERIES_AM.values():
        records = [{"key": key, "date": d, "field": field, "value": 100.0 + i,
                    "available_at": d + pd.Timedelta(hours=22)} for i, d in enumerate(days)]
        zoo.write_series(settings, store, records, source="test", now=now)
    series, _ = externes_pour(settings, [PAR_ID["AM03_1j"]], DECOUVERTE.fin_exclusive)
    assert SERIES_AM["zoo_marches:GLD"][2] == "close"
    assert len(series["zoo_marches:GLD"]) == 40 and all(len(s) == 40 for s in series.values())


# --- Contexte journalier coupé, blocs exposés, TR37 sur la dernière journée cotée -----------------------------------

def test_contexte_quotidien_coupe_et_journee_connue():
    rng = np.random.default_rng(4)
    p = paire(100 * np.exp(np.cumsum(rng.normal(0, 0.01, 24 * 10 + 5))), start="2019-03-01")

    class Src:
        externes: dict = {}
        evenements: dict = {}
        univers = univers_toujours([p.symbole])

    ctx = contexte(PAR_ID["MR19_1j"], p, DECOUVERTE, Src())     # exécution 1 h
    frame = p.unite(1)
    assert (ctx.quotidien.disponible <= frame.disponible[-1]).all()
    known = ctx.quotidien_connu
    ok = known >= 0
    assert (ctx.quotidien.disponible[known[ok]] <= frame.disponible[ok]).all()
    nxt = known + 1
    later = nxt < len(ctx.quotidien)
    assert (ctx.quotidien.disponible[nxt[later]] > frame.disponible[later]).all()


def test_blocs_exposes_et_non_echanges():
    stage = etape("2019-01-01", "2019-07-01")
    start = stage.debut_jour * DAY_H
    exec_h = np.array([start + 24 * 7 * k for k in range(25)])
    exposure = np.where(np.arange(25) < 4, 1.0, 0.0)            # investi les 4 premières semaines seulement
    assert blocs_exposes(exec_h, exposure, stage) == 1
    assert blocs_exposes(exec_h, np.ones(25), stage) == len(range(0, (stage.fin_jour - stage.debut_jour) // 28 + 1))


def test_tr37_decide_sur_la_derniere_journee_cotee():
    pairs = [paire(np.linspace(100, 130 + 10 * s, 24 * 60), start="2019-01-01", symbole=f"P{s}USDT") for s in range(3)]
    daily = {p.symbole: p.unite(DAY_H) for p in pairs}
    board = panneau(daily, univers_toujours(list(daily)))
    idx, _, _ = decisions_hebdomadaires(board)
    sunday = int(idx[5])
    holed = Panneau_sans(board, sunday)
    ctx = Contexte("", DAY_H, PAR_ID["TR37_1j"].params, 0)
    out = PAR_ID["TR37_1j"].strategie_fonction()(holed, ctx)
    assert out.poids[5].sum() == pytest.approx(1.0)             # tous en hausse : investi malgré le dimanche manquant


def Panneau_sans(board, j: int):
    from dataclasses import replace
    fields = {f: getattr(board, f).copy() for f in ("ouverture", "haut", "bas", "cloture", "volume", "achat")}
    for f in fields.values():
        f[j] = np.nan
    return replace(board, **fields)


# --- Causalité : séries, événements, tous les champs du panneau ---------------------------------------------------

def test_causalite_detecte_une_annonce_lue_avant_sa_publication():
    p = paire(np.linspace(100, 120, 24 * 40), start="2019-03-01", mecha=0.0)
    daily = p.unite(DAY_H)
    when = int(daily.disponible[3]) + 3 * DAY_H * HOUR_NS                     # publiée 3 jours après la 4e journée
    table = pd.DataFrame({"symbol": [p.symbole], "available_at": [pd.Timestamp(when, tz="UTC")]})
    ctx = Contexte(p.symbole, DAY_H, {"categorie": "COTATION_SPOT"}, 0, evenements=MappingProxyType(
        {"annonces_binance": table}))

    def leaky(frame, ctx):                     # lit l'annonce sans regarder son available_at
        entry = np.zeros(len(frame), dtype=bool)
        if len(frame) > 3:
            entry[3] = len(ctx.evenements["annonces_binance"]) > 0
        return Signaux(entry, None)

    good = PAR_ID["EV02_1j"].strategie_fonction()
    taus = daily.disponible[2:8]
    assert C.verifier(good, p.h1, DAY_H, ctx, taus).ok
    assert not C.verifier(leaky, p.h1, DAY_H, ctx, taus).ok


def test_causalite_portefeuille_coupe_series_et_tous_les_champs():
    pairs = [paire(100 * np.exp(np.cumsum(np.random.default_rng(s).normal(0, 0.01, 24 * 120))), start="2019-01-01",
                   symbole=f"P{s}USDT") for s in range(4)]
    board = panneau({p.symbole: p.unite(DAY_H) for p in pairs}, univers_toujours([p.symbole for p in pairs]))
    days = np.arange(int(board.jours[0]), int(board.jours[-1]) + 1)
    serie = SerieExterne("x", (days * DAY_H + 18) * HOUR_NS, days * DAY_H * HOUR_NS,
                         np.random.default_rng(1).normal(0, 1, len(days)))
    ctx = Contexte("", DAY_H, {}, 0, externes=MappingProxyType({"x": serie}))
    _, decisions, _ = decisions_hebdomadaires(board)
    taus = decisions[3:12]

    def weights(frame, ctx, *, leak: str = ""):
        idx, dec, exe = decisions_hebdomadaires(frame)
        n = len(frame.symboles)
        w = np.zeros((len(idx), n))
        univ = np.ones((len(idx), n), dtype=bool)
        s = ctx.externes["x"]
        for k, j in enumerate(idx):
            when = dec[k] + (7 * DAY_H * HOUR_NS if leak == "serie" else 0)
            pos = np.searchsorted(s.disponible, when, side="left") - 1
            if pos >= 0 and s.valeur[pos] > 0:
                w[k] = 1.0 / n
            if leak == "volume" and j + 1 < len(frame.volume):
                univ[k] = frame.volume[j + 1] > frame.volume[j]     # lit le volume du lendemain
        return Poids(dec, exe, frame.symboles, w, univers=univ)

    assert C.verifier_portefeuille(weights, board, ctx, taus).ok
    assert not C.verifier_portefeuille(lambda f, c: weights(f, c, leak="serie"), board, ctx, taus).ok
    assert not C.verifier_portefeuille(lambda f, c: weights(f, c, leak="volume"), board, ctx, taus).ok
