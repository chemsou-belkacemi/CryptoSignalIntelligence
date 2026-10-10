"""Portefeuille (PF), forme (b) : définitions déclaratives au registre (`research/zoo/spec.py`), À CODER.

Chaque fonction ci-dessous lève `NotImplementedError` tant que le chantier de la famille PF ne l'a pas codée
sur l'interface du zoo (`research/zoo/README.md` : fonction pure `signal(frame, ctx)`, bougies closes, aucune
I/O). Une fonction codée est ajoutée à `IMPLEMENTEES`, avec les tests exigés par le README (définitions calculées
à la main, causalité et mutations, comptage). Le moteur refuse l'exécution réelle tant qu'un test retenu n'est pas
codé."""
from __future__ import annotations

from ..spec import Contexte

#: Fonctions codées et testées (le moteur ne les appelle qu'à cette condition).
IMPLEMENTEES: frozenset[str] = frozenset()
DEPENDENT_ETAPE: frozenset[str] = frozenset()


def btc_eth(frame, ctx: Contexte):
    """Forme (b) : `frame` = Panneau, rend `Poids`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("portfolio.btc_eth : à coder par le chantier de la famille PF "
                              "(interface : research/zoo/README.md)")


def inverse_volatilite(frame, ctx: Contexte):
    """Forme (b) : `frame` = Panneau, rend `Poids`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("portfolio.inverse_volatilite : à coder par le chantier de la famille PF "
                              "(interface : research/zoo/README.md)")


def poids_egaux(frame, ctx: Contexte):
    """Forme (b) : `frame` = Panneau, rend `Poids`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("portfolio.poids_egaux : à coder par le chantier de la famille PF "
                              "(interface : research/zoo/README.md)")


def variance_minimale(frame, ctx: Contexte):
    """Forme (b) : `frame` = Panneau, rend `Poids`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("portfolio.variance_minimale : à coder par le chantier de la famille PF "
                              "(interface : research/zoo/README.md)")
