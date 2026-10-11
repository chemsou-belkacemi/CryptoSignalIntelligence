"""Causalité des familles AM, CA, SE, EV et RG du zoo (docs/ZOO.md § 7) sur données SYNTHÉTIQUES : signaux et poids
identiques sur données tronquées (bougies ET séries externes ET annonces coupées à τ) et sous un futur falsifié ; les
mutations propres à ces familles sont détectées (série externe jointe sur sa date, annonce lue sans son
`available_at`, modèle estimé sur tout l'échantillon). Aucun rendement."""
from __future__ import annotations

from dataclasses import replace
from functools import partial
from types import MappingProxyType

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.context.calendrier import moon_phases
from crypto_signal_intelligence.context.zoo import available_at_ecb, available_at_fng, available_at_fred
from crypto_signal_intelligence.research.zoo import causality as C
from crypto_signal_intelligence.research.zoo.data import DAY_H, HOUR_NS, SerieExterne, agreger
from crypto_signal_intelligence.research.zoo.run import panneau
from crypto_signal_intelligence.research.zoo.spec import FORME_A, FORME_B, PAR_ID, Contexte
from crypto_signal_intelligence.research.zoo.spec import tests_de as choisir
from crypto_signal_intelligence.research.zoo.strategies import events as EV
from crypto_signal_intelligence.research.zoo.strategies import regimes as RG
from crypto_signal_intelligence.research.zoo.strategies import sentiment as SE
from crypto_signal_intelligence.research.zoo.strategies.commun import (
    decisions_hebdomadaires,
    decisions_quotidiennes,
)

from .zoo_outils import etape, paire, univers_toujours

FAMILLES = ["AM", "CA", "SE", "EV", "RG"]
STAGE = etape("2019-03-01", "2020-03-01")
TESTS_A = [t for t in choisir(FAMILLES) if t.forme == FORME_A]
TESTS_B = [t for t in choisir(FAMILLES) if t.forme == FORME_B]


def marche(jours: int = 820, seed: int = 11, symbole: str = "AAAUSDT", debut: str = "2017-12-01", vol: float = 0.01):
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, vol, 24 * jours)))
    return paire(closes, start=debut, symbole=symbole, mecha=0.004)


def serie_synthetique(nom: str, regle, debut: str = "2017-01-01", fin: str = "2020-06-30", ouvres: bool = True,
                      seed: int = 3, niveau: float = 100.0) -> SerieExterne:
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(debut, fin) if ouvres else pd.date_range(debut, fin, freq="D")
    days = days.tz_localize("UTC")
    values = niveau * np.exp(np.cumsum(rng.normal(0, 0.02, len(days))))
    avail = np.array([int(regle(d).value) for d in days], dtype=np.int64)
    order = np.argsort(avail, kind="stable")
    return SerieExterne(nom, avail[order], days.as_unit("ns").asi8[order], values[order])


def externes() -> dict[str, SerieExterne]:
    out = {f"zoo_marches:{name}": serie_synthetique(name, partial(available_at_fred, name), seed=k)
           for k, name in enumerate(("SP500", "NASDAQ100", "DGS10", "VIXCLS"))}
    out["zoo_marches:GLD"] = serie_synthetique("zoo_marches:GLD", lambda d: available_at_fred("SP500", d), seed=9)
    out["zoo_dollar_bce:DXY_BCE"] = serie_synthetique("zoo_dollar_bce:DXY_BCE", available_at_ecb, seed=10)
    rng = np.random.default_rng(12)
    days = pd.date_range("2018-02-01", "2020-06-30", freq="D", tz="UTC")
    avail = np.array([int(available_at_fng(d).value) for d in days], dtype=np.int64)
    out[SE.CLE] = SerieExterne(SE.CLE, avail, days.as_unit("ns").asi8, rng.integers(5, 95, len(days)).astype(float))
    return out


def calendriers() -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(13)
    rows = []
    for month in pd.date_range("2018-01-01", "2020-06-01", freq="MS"):
        for event, day in (("CPI", 10 + rng.integers(0, 5)), ("NFP", 1 + rng.integers(0, 7))):
            rows.append({"event": event, "key": str((month + pd.Timedelta(days=int(day))).date()), "scheduled": True})
        if month.month % 2:
            rows.append({"event": "FOMC", "key": str((month + pd.Timedelta(days=int(20 + rng.integers(0, 8)))).date()),
                         "scheduled": True})
    moons = pd.DataFrame(moon_phases(pd.Timestamp("2017-12-01", tz="UTC"), pd.Timestamp("2020-06-30", tz="UTC")))
    return {"calendrier_macro": pd.DataFrame(rows), "calendrier_crypto": moons}


def annonces(symboles_heures: dict[str, str]) -> pd.DataFrame:
    rows = [{"category": "COTATION_SPOT", "detail": f"Binance Will List X ({s.removesuffix('USDT')})",
             "tickers": s.removesuffix("USDT"), "event_time": pd.Timestamp(h, tz="UTC") - pd.Timedelta(minutes=10),
             "available_at": pd.Timestamp(h, tz="UTC")} for s, h in symboles_heures.items()]
    return pd.DataFrame(rows)


EXTERNES = externes()
EVENEMENTS = {**calendriers(), EV.ANNONCES: annonces({"NEWUSDT": "2019-04-02 08:00", "LATEUSDT": "2019-04-04 00:00:05"})}


def contexte(test, symbole: str = "AAAUSDT") -> Contexte:
    return Contexte(symbole, test.execution_heures, test.params, STAGE.debut_ns,
                    externes=MappingProxyType(EXTERNES), evenements=MappingProxyType(EVENEMENTS))


# --- Forme (a) -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("test", TESTS_A, ids=[t.id for t in TESTS_A])
def test_forme_a_causale_instants_au_hasard_et_de_signal(test):
    pairs = [marche(seed=21), marche(seed=22, symbole="BBBUSDT")]
    report = C.verifier_test(test, pairs, STAGE, lambda pair: contexte(test, pair.symbole), n_aleatoires=6,
                             echantillon=1)
    assert report.ok, report.resume()
    assert report.instants >= 6


def tronquer_evenements(evenements, tau: int) -> dict[str, pd.DataFrame]:
    """Annonces (et toute table munie d'`available_at`) coupées à τ ; calendriers déclarés connus d'avance gardés."""
    out = {}
    for name, table in evenements.items():
        if name == EV.ANNONCES:
            table = table[pd.to_datetime(table["available_at"], utc=True).dt.as_unit("ns").astype("int64") <= tau]
        out[name] = table
    return out


def verifier_annonces(strategie, pair, unit: int, ctx: Contexte, taus) -> list[str]:
    """Recalcul tronqué (bougies 1 h ET annonces coupées à τ) : décisions ≤ τ identiques au calcul complet."""
    full = strategie(agreger(pair.h1, unit), ctx)
    bars = agreger(pair.h1, unit)
    errors = []
    for tau in taus:
        cut = agreger(C.tronquer_h1(pair.h1, int(tau)), unit)
        got = strategie(cut, replace(ctx, evenements=tronquer_evenements(ctx.evenements, int(tau))))
        keep = bars.disponible <= tau
        if len(cut) != keep.sum() or (got.entree != full.entree[keep]).any():
            errors.append(f"τ={int(tau)}")
    return errors


def test_ev02_causale_avec_annonces_coupees_et_mutation_detectee():
    test = PAR_ID["EV02_1j"]
    new = marche(jours=60, seed=31, symbole="NEWUSDT", debut="2019-04-02 10:00")
    late = marche(jours=60, seed=32, symbole="LATEUSDT", debut="2019-04-02 10:00")
    for pair, expected in ((new, True), (late, False)):
        bars = agreger(pair.h1, DAY_H)
        ctx = contexte(test, pair.symbole)
        sig = EV.cotation_binance(bars, ctx)
        assert bool(sig.entree[0]) is expected                    # LATE : annonce connue 3 s APRÈS la décision
        taus = bars.disponible[:8] + np.array([0, 1, 5_000_000_000, 0, 0, 0, 0, 0])
        assert verifier_annonces(EV.cotation_binance, pair, DAY_H, ctx, taus) == []

    def mutation(frame, ctx):                                     # MUTATION : annonce lue sans son available_at
        # (antidatée à la veille de la cotation : dans la fenêtre de 30 jours, mais avant sa publication réelle)
        table = ctx.evenements[EV.ANNONCES].assign(available_at=pd.Timestamp("2019-04-01", tz="UTC"))
        return EV.cotation_binance(frame, replace(ctx, evenements={**ctx.evenements, EV.ANNONCES: table}))

    bars = agreger(late.h1, DAY_H)
    assert mutation(bars, contexte(test, "LATEUSDT")).entree[0]
    assert verifier_annonces(mutation, late, DAY_H, contexte(test, "LATEUSDT"), bars.disponible[:3]) != []


def redater(ctx: Contexte) -> Contexte:
    """MUTATION « série externe jointe sur sa date » : chaque série rendue disponible à sa date."""
    out = {}
    for key, s in ctx.externes.items():
        order = np.argsort(s.date, kind="stable")
        out[key] = SerieExterne(s.nom, s.date[order], s.date[order], s.valeur[order])
    return replace(ctx, externes=out)


def test_se01_mutation_jointure_sur_la_date_detectee():
    test = PAR_ID["SE01_1j"]
    p = marche(seed=41)
    bars = agreger(p.h1, DAY_H)
    taus = C.instants_aleatoires(bars, STAGE, 40, np.random.default_rng(1))
    good = C.verifier(test.strategie_fonction(), p.h1, DAY_H, contexte(test), taus, test_id=test.id)
    bad = C.verifier(lambda f, c: SE.peur_extreme(f, redater(c)), p.h1, DAY_H, contexte(test), taus, test_id="mut")
    assert good.ok and not bad.ok


# --- Forme (b) -------------------------------------------------------------------------------------------------------

def falsifier_serie(s: SerieExterne, tau: int, rng: np.random.Generator) -> SerieExterne:
    after = s.disponible > tau
    values = s.valeur.copy()
    values[after] = values[after] * np.exp(rng.normal(0, 0.3, after.sum()))
    return SerieExterne(s.nom, s.disponible, s.date, values)


def verifier_poids(strategie, board, ctx: Contexte, taus) -> list[str]:
    """Forme (b) : poids recalculés sur le panneau tronqué aux journées connues à τ ET les séries externes coupées à τ
    (`available_at` ≤ τ), puis avec un futur falsifié (panneau et séries) ; décisions ≤ τ identiques."""
    rng = np.random.default_rng(C.GRAINE)
    full = strategie(board, ctx)
    errors = []

    def same(out, tau) -> bool:
        exp = {int(d): w for d, w in zip(full.decisions, full.poids, strict=True) if d <= tau}
        got = {int(d): w for d, w in zip(out.decisions, out.poids, strict=True) if d <= tau}
        return set(exp) == set(got) and all(np.allclose(exp[d], got[d]) for d in exp)

    for tau in np.asarray(taus, dtype=np.int64):
        n = int((board.disponible <= tau).sum())
        cut = replace(ctx, externes={k: C.tronquer_serie(v, int(tau)) for k, v in ctx.externes.items()})
        if not same(strategie(board.tete(n), cut), tau):
            errors.append(f"tronqué τ={int(tau)}")
        noise = np.exp(rng.normal(0, 0.05, board.cloture.shape))
        future = (np.arange(len(board.jours)) >= n)[:, None]
        fake_board = replace(board, cloture=np.where(future, board.cloture * noise, board.cloture))
        fake = replace(ctx, externes={k: falsifier_serie(v, int(tau), rng) for k, v in ctx.externes.items()})
        if not same(strategie(fake_board, fake), tau):
            errors.append(f"falsifié τ={int(tau)}")
    return errors


def panneau_synthetique():
    pairs = [marche(seed=50, symbole="BTCUSDT", debut="2017-08-17", jours=1000)] + \
        [marche(seed=50 + k, symbole=f"P{k}USDT", jours=800) for k in range(1, 4)]
    return panneau({p.symbole: p.unite(DAY_H) for p in pairs}, univers_toujours([p.symbole for p in pairs]))


BOARD = panneau_synthetique()


def instants_b(test, n: int = 8) -> np.ndarray:
    calendar = decisions_quotidiennes if test.frequence == "quotidienne" else decisions_hebdomadaires
    _, decisions, _ = calendar(BOARD)
    pool = decisions[(decisions >= STAGE.debut_ns) & (decisions <= STAGE.fin_ns)]
    taus = np.random.default_rng(sum(map(ord, test.id))).choice(pool, size=n, replace=False)
    return np.sort(np.r_[taus, taus[:2] + HOUR_NS * 20])       # instants de décision et instants entre deux décisions


@pytest.mark.parametrize("test", TESTS_B, ids=[t.id for t in TESTS_B])
def test_forme_b_causale_series_coupees(test):
    ctx = Contexte("", DAY_H, test.params, STAGE.debut_ns, externes=MappingProxyType(EXTERNES))
    assert verifier_poids(test.strategie_fonction(), BOARD, ctx, instants_b(test)) == []
    # le moteur (sans coupure des séries) passe aussi
    assert C.verifier_portefeuille(test.strategie_fonction(), BOARD, ctx, instants_b(test), test.id).ok


@pytest.mark.parametrize("test", [t for t in TESTS_B if t.famille in ("AM", "SE")],
                         ids=[t.id for t in TESTS_B if t.famille in ("AM", "SE")])
def test_forme_b_mutation_jointure_sur_la_date_detectee(test):
    ctx = Contexte("", DAY_H, test.params, STAGE.debut_ns, externes=MappingProxyType(EXTERNES))
    fn = test.strategie_fonction()
    taus = instants_b(test, 30)
    assert verifier_poids(lambda b, c: fn(b, redater(c)), BOARD, ctx, taus) != []


def test_rg02_mutation_hmm_ajuste_sur_tout_l_echantillon_detectee():
    test = PAR_ID["RG02_1j"]
    ctx = Contexte("", DAY_H, test.params, STAGE.debut_ns)
    original = RG.probabilite_etat_volatil

    def leaky(log_rendements, fins, refits, etats=3, minimum=RG.HMM_MIN_RENDEMENTS):
        params = RG._baum_welch(log_rendements, *RG.hmm_depart(log_rendements, etats), 100, 1e-6)  # tout le passé ET le futur
        out = np.full(len(fins), np.nan)
        for d, end in enumerate(fins):
            if end >= minimum:
                f, _ = RG._avant(RG._emissions(log_rendements[:end], params[2], params[3]), params[0], params[1])
                out[d] = f[-1, int(np.argmax(params[3]))]
        return out

    def mutation(board, c):
        RG.probabilite_etat_volatil = leaky
        try:
            return RG.hmm3(board, c)
        finally:
            RG.probabilite_etat_volatil = original

    rng = np.random.default_rng(2)
    sigma = np.where((np.arange(1000) // 120) % 2, 0.04, 0.01)
    btc = paire(100 * np.exp(np.cumsum(np.repeat(rng.normal(0, 1, 1000) * sigma, 24) / np.sqrt(24))),
                start="2017-08-17", symbole="BTCUSDT")
    board = panneau({"BTCUSDT": btc.unite(DAY_H)}, univers_toujours(["BTCUSDT"]))
    _, decisions, _ = decisions_hebdomadaires(board)
    taus = decisions[(decisions >= STAGE.debut_ns) & (decisions <= STAGE.fin_ns)][::4]
    assert verifier_poids(RG.hmm3, board, ctx, taus) == []
    assert verifier_poids(mutation, board, ctx, taus) != []


def test_rg03_mutation_variances_sur_tout_l_echantillon_detectee():
    test = PAR_ID["RG03_1j"]
    p = marche(seed=61, vol=0.02)
    bars = agreger(p.h1, DAY_H)

    def leaky(frame, ctx):                       # variances estimées une fois sur TOUTE la série (futur compris)
        y = np.log(frame.cloture)
        e0, e1 = RG._nelder_mead_kalman(y, *RG.KALMAN_DEPART, *RG.KALMAN_BORNES)
        slope = np.full(len(y), np.nan)
        a0, a1, p00, p01, p11 = y[0], 0.0, RG.KALMAN_DIFFUS, 0.0, RG.KALMAN_DIFFUS
        for t in range(len(y)):
            _, _, slope[t], a0, a1, p00, p01, p11 = RG._kalman_pas(y[t], a0, a1, p00, p01, p11, np.exp(e0),
                                                                    np.exp(e1))
        return RG._etat(slope > 0, np.ones(len(y), dtype=bool))

    taus = C.instants_aleatoires(bars, STAGE, 12, np.random.default_rng(4))
    ctx = Contexte(p.symbole, DAY_H, test.params, STAGE.debut_ns)
    assert C.verifier(test.strategie_fonction(), p.h1, DAY_H, ctx, taus, test_id=test.id).ok
    assert not C.verifier(leaky, p.h1, DAY_H, ctx, taus, test_id="mutation").ok
