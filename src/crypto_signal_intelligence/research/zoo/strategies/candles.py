"""Chandeliers japonais (CH) : définitions déclaratives au registre (`research/zoo/spec.py`), À CODER.

Chaque fonction ci-dessous lève `NotImplementedError` tant que le chantier de la famille CH ne l'a pas codée
sur l'interface du zoo (`research/zoo/README.md` : fonction pure `signal(frame, ctx)`, bougies closes, aucune
I/O). Une fonction codée est ajoutée à `IMPLEMENTEES`, avec les tests exigés par le README (définitions calculées
à la main, causalité et mutations, comptage). Le moteur refuse l'exécution réelle tant qu'un test retenu n'est pas
codé."""
from __future__ import annotations

from ..spec import Contexte

#: Fonctions codées et testées (le moteur ne les appelle qu'à cette condition).
IMPLEMENTEES: frozenset[str] = frozenset()
DEPENDENT_ETAPE: frozenset[str] = frozenset()


def figure(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("candles.figure : à coder par le chantier de la famille CH "
                              "(interface : research/zoo/README.md)")


def heikin_ashi(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("candles.heikin_ashi : à coder par le chantier de la famille CH "
                              "(interface : research/zoo/README.md)")


def renko(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("candles.renko : à coder par le chantier de la famille CH "
                              "(interface : research/zoo/README.md)")
