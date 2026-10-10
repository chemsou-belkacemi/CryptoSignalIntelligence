"""Régimes et modèles (RG02 forme b, RG03 à RG07 forme a) : définitions déclaratives au registre (`research/zoo/spec.py`), À CODER.

Chaque fonction ci-dessous lève `NotImplementedError` tant que le chantier de la famille RG ne l'a pas codée
sur l'interface du zoo (`research/zoo/README.md` : fonction pure `signal(frame, ctx)`, bougies closes, aucune
I/O). Une fonction codée est ajoutée à `IMPLEMENTEES`, avec les tests exigés par le README (définitions calculées
à la main, causalité et mutations, comptage). Le moteur refuse l'exécution réelle tant qu'un test retenu n'est pas
codé."""
from __future__ import annotations

from ..spec import Contexte

#: Fonctions codées et testées (le moteur ne les appelle qu'à cette condition).
IMPLEMENTEES: frozenset[str] = frozenset()
DEPENDENT_ETAPE: frozenset[str] = frozenset()


def entropie(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("regimes.entropie : à coder par le chantier de la famille RG "
                              "(interface : research/zoo/README.md)")


def haar(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("regimes.haar : à coder par le chantier de la famille RG "
                              "(interface : research/zoo/README.md)")


def hmm3(frame, ctx: Contexte):
    """Forme (b) : `frame` = Panneau, rend `Poids`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("regimes.hmm3 : à coder par le chantier de la famille RG "
                              "(interface : research/zoo/README.md)")


def hurst(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("regimes.hurst : à coder par le chantier de la famille RG "
                              "(interface : research/zoo/README.md)")


def kalman(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("regimes.kalman : à coder par le chantier de la famille RG "
                              "(interface : research/zoo/README.md)")


def sinusoide_ehlers(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("regimes.sinusoide_ehlers : à coder par le chantier de la famille RG "
                              "(interface : research/zoo/README.md)")
