"""Structure (ST) : définitions déclaratives au registre (`research/zoo/spec.py`), À CODER.

Chaque fonction ci-dessous lève `NotImplementedError` tant que le chantier de la famille ST ne l'a pas codée
sur l'interface du zoo (`research/zoo/README.md` : fonction pure `signal(frame, ctx)`, bougies closes, aucune
I/O). Une fonction codée est ajoutée à `IMPLEMENTEES`, avec les tests exigés par le README (définitions calculées
à la main, causalité et mutations, comptage). Le moteur refuse l'exécution réelle tant qu'un test retenu n'est pas
codé."""
from __future__ import annotations

from ..spec import Contexte

#: Fonctions codées et testées (le moteur ne les appelle qu'à cette condition).
IMPLEMENTEES: frozenset[str] = frozenset()
DEPENDENT_ETAPE: frozenset[str] = frozenset()


def dow(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("structure.dow : à coder par le chantier de la famille ST "
                              "(interface : research/zoo/README.md)")


def fibonacci(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("structure.fibonacci : à coder par le chantier de la famille ST "
                              "(interface : research/zoo/README.md)")


def niveau_rond(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("structure.niveau_rond : à coder par le chantier de la famille ST "
                              "(interface : research/zoo/README.md)")


def pivots(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("structure.pivots : à coder par le chantier de la famille ST "
                              "(interface : research/zoo/README.md)")


def plus_haut_annuel(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("structure.plus_haut_annuel : à coder par le chantier de la famille ST "
                              "(interface : research/zoo/README.md)")


def plus_haut_historique(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("structure.plus_haut_historique : à coder par le chantier de la famille ST "
                              "(interface : research/zoo/README.md)")


def plus_haut_semaine(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("structure.plus_haut_semaine : à coder par le chantier de la famille ST "
                              "(interface : research/zoo/README.md)")


def plus_haut_veille(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("structure.plus_haut_veille : à coder par le chantier de la famille ST "
                              "(interface : research/zoo/README.md)")
