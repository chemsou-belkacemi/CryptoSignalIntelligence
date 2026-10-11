"""Causalité des familles XS, RO et PF (docs/ZOO.md § 7) : poids, univers de la référence et composantes A / B
recalculés sur le panneau tronqué aux journées connues, puis sous un futur falsifié (TOUS les champs, volumes et achats
compris) ; contrôle du moteur (`causality.verifier_portefeuille`) ; mutations détectées. Données SYNTHÉTIQUES."""
from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import numpy as np
import pytest

from crypto_signal_intelligence.research.zoo import causality as C
from crypto_signal_intelligence.research.zoo.data import DAY_H
from crypto_signal_intelligence.research.zoo.run import panneau
from crypto_signal_intelligence.research.zoo.spec import Contexte, Panneau, Poids
from crypto_signal_intelligence.research.zoo.spec import tests_de as choisir
from crypto_signal_intelligence.research.zoo.strategies import factors as F
from crypto_signal_intelligence.research.zoo.strategies import rotation as R
from crypto_signal_intelligence.research.zoo.strategies.commun import decisions_hebdomadaires

from .zoo_outils import paire, univers_mois

TESTS = choisir(["XS", "RO", "PF"])
SYMBOLES = ["BTCUSDT", "ETHUSDT"] + [f"A{k:02d}USDT" for k in range(10)]
CHAMPS = ("ouverture", "haut", "bas", "cloture", "volume", "achat")


def _paire(k: int, symbole: str, jours: int = 560):
    rng = np.random.default_rng(100 + k)
    common = np.random.default_rng(99).normal(0, 0.006, 24 * jours)
    closes = 100 * np.exp(np.cumsum(common + rng.normal(0, 0.008, 24 * jours)))
    start = "2018-01-01" if k < 8 else "2018-09-01"          # deux paires cotées plus tard
    n = 24 * jours if k < 8 else 24 * (jours - 243)
    return paire(closes[:n], start=start, symbole=symbole, mecha=0.003, volume=1e6 * (1 + k / 10))


@pytest.fixture(scope="module")
def board() -> Panneau:
    pairs = [_paire(k, s) for k, s in enumerate(SYMBOLES)]
    months = {f"{y}-{m:02d}": [s for i, s in enumerate(SYMBOLES) if not (m % 2 and i in (5, 6))]
              for y in (2018, 2019, 2020) for m in range(1, 13)}
    return panneau({p.symbole: p.unite(DAY_H) for p in pairs}, univers_mois(months))


def _ctx(test) -> Contexte:
    return Contexte("", DAY_H, test.params, 0, externes=MappingProxyType({}))


def _instants(board: Panneau, n: int = 8) -> np.ndarray:
    _, decisions, _ = decisions_hebdomadaires(board)
    late = decisions[decisions >= board.disponible[430]]
    return late[np.linspace(0, len(late) - 5, n).astype(int)]


def _champs(p: Poids, k: int) -> list[np.ndarray]:
    out = [p.poids[k], p.univers[k] if p.univers is not None else np.zeros(0)]
    for extra in (p.composante_a, p.composante_b, p.dans_a):
        if extra is not None:
            out.append(np.asarray(extra[k], dtype=float))
    return out


def _avant(p: Poids, tau: int) -> dict[int, list[np.ndarray]]:
    return {int(d): _champs(p, k) for k, d in enumerate(p.decisions) if d <= tau}


def _egaux(a: dict, b: dict) -> bool:
    return set(a) == set(b) and all(len(a[d]) == len(b[d]) and all(np.allclose(x, y, equal_nan=True)
                                                                      for x, y in zip(a[d], b[d], strict=True))
                                    for d in a)


def controle_strict(strategie, board: Panneau, ctx: Contexte, instants: np.ndarray) -> list[str]:
    """Écarts (décisions ≤ τ) entre le calcul complet, le panneau tronqué et un futur falsifié sur TOUS les champs."""
    rng = np.random.default_rng(4)
    full = strategie(board, ctx)
    problems = []
    for tau in instants:
        n = int((board.disponible <= tau).sum())
        expected = _avant(full, int(tau))
        if not _egaux(expected, _avant(strategie(board.tete(n), ctx), int(tau))):
            problems.append(f"tronqué τ={tau}")
        future = (np.arange(len(board.jours)) >= n)[:, None]
        noise = np.exp(rng.normal(0, 0.08, board.cloture.shape))
        fake = replace(board, **{f: np.where(future, getattr(board, f) * noise * rng.uniform(0.5, 2.0), getattr(board, f))
                                 for f in CHAMPS})
        if not _egaux(expected, _avant(strategie(fake, ctx), int(tau))):
            problems.append(f"falsifié τ={tau}")
    return problems


@pytest.mark.parametrize("test", TESTS, ids=[t.id for t in TESTS])
def test_poids_et_reference_causaux(test, board):
    taus = _instants(board)
    strategie = test.strategie_fonction()
    assert controle_strict(strategie, board, _ctx(test), taus) == []
    report = C.verifier_portefeuille(strategie, board, _ctx(test), taus, test.id)
    assert report.ok and report.instants >= 6, report.resume()
    out = strategie(board, _ctx(test))
    window = (out.decisions >= taus.min()) & (out.decisions <= taus.max())
    assert out.poids[window].sum() > 0                 # contrôle non vide : des positions entre les instants


def test_mutation_variable_lue_le_lendemain_detectee(board, monkeypatch):
    test = next(t for t in TESTS if t.id == "XS01_1j")
    taus = _instants(board)
    honest = F.VARIABLES["rendement_7j"]

    def leaky(b, j):                                   # MUTATION : rendement 7 j lu sur la clôture de j + 1
        return honest(b, min(j + 1, len(b.frame.jours) - 1))

    monkeypatch.setitem(F.VARIABLES, "rendement_7j", leaky)
    assert controle_strict(test.strategie_fonction(), board, _ctx(test), taus)
    assert not C.verifier_portefeuille(test.strategie_fonction(), board, _ctx(test), taus, "fuite").ok


def test_mutation_volume_futur_detectee_seulement_par_le_controle_strict(board, monkeypatch):
    """Le futur falsifié du moteur ne touche pas les volumes (signalé) : le recalcul tronqué suffit à détecter cette
    fuite-ci, et le contrôle strict la voit des deux façons."""
    test = next(t for t in TESTS if t.id == "XS09_1j")
    taus = _instants(board)

    def leaky(b, j):                                   # MUTATION : volume des 28 journées SUIVANTES
        return F.moyenne_valide(b.frame.volume[j + 1:j + 29]) if j + 2 < len(b.frame.jours) else \
            np.full(len(b.frame.symboles), np.nan)

    monkeypatch.setitem(F.VARIABLES, "volume_28j", leaky)
    assert controle_strict(test.strategie_fonction(), board, _ctx(test), taus)
    report = C.verifier_portefeuille(test.strategie_fonction(), board, _ctx(test), taus, "fuite")
    assert report.ecarts_tronque and not report.ok


def test_mutation_mediane_avec_le_jour_meme_ou_le_futur_detectee(board, monkeypatch):
    test = next(t for t in TESTS if t.id == "RO05_1j")
    taus = _instants(board)
    honest = R.correlations_moyennes

    def leaky(frame, window):                          # MUTATION : série lue 7 jours en avance
        series = honest(frame, window)
        return np.r_[series[7:], np.full(7, np.nan)]

    monkeypatch.setattr(R, "correlations_moyennes", leaky)
    assert controle_strict(test.strategie_fonction(), board, _ctx(test), taus)
