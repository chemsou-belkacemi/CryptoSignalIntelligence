"""Volatilité et range (VO) : définitions déclaratives au registre (`research/zoo/spec.py`), À CODER.

Chaque fonction ci-dessous lève `NotImplementedError` tant que le chantier de la famille VO ne l'a pas codée
sur l'interface du zoo (`research/zoo/README.md` : fonction pure `signal(frame, ctx)`, bougies closes, aucune
I/O). Une fonction codée est ajoutée à `IMPLEMENTEES`, avec les tests exigés par le README (définitions calculées
à la main, causalité et mutations, comptage). Le moteur refuse l'exécution réelle tant qu'un test retenu n'est pas
codé."""
from __future__ import annotations

from ..spec import Contexte

#: Fonctions codées et testées (le moteur ne les appelle qu'à cette condition).
IMPLEMENTEES: frozenset[str] = frozenset()
DEPENDENT_ETAPE: frozenset[str] = frozenset()


def cassure_atr(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("volatility.cassure_atr : à coder par le chantier de la famille VO "
                              "(interface : research/zoo/README.md)")


def dual_thrust(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("volatility.dual_thrust : à coder par le chantier de la famille VO "
                              "(interface : research/zoo/README.md)")


def momentum_intrajournalier(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("volatility.momentum_intrajournalier : à coder par le chantier de la famille VO "
                              "(interface : research/zoo/README.md)")


def nr(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("volatility.nr : à coder par le chantier de la famille VO "
                              "(interface : research/zoo/README.md)")


def ouverture_seance(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("volatility.ouverture_seance : à coder par le chantier de la famille VO "
                              "(interface : research/zoo/README.md)")


def squeeze(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("volatility.squeeze : à coder par le chantier de la famille VO "
                              "(interface : research/zoo/README.md)")


def williams_cassure(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("volatility.williams_cassure : à coder par le chantier de la famille VO "
                              "(interface : research/zoo/README.md)")
