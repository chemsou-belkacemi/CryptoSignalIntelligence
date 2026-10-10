"""Volume et flux (VF) : définitions déclaratives au registre (`research/zoo/spec.py`), À CODER.

Chaque fonction ci-dessous lève `NotImplementedError` tant que le chantier de la famille VF ne l'a pas codée
sur l'interface du zoo (`research/zoo/README.md` : fonction pure `signal(frame, ctx)`, bougies closes, aucune
I/O). Une fonction codée est ajoutée à `IMPLEMENTEES`, avec les tests exigés par le README (définitions calculées
à la main, causalité et mutations, comptage). Le moteur refuse l'exécution réelle tant qu'un test retenu n'est pas
codé."""
from __future__ import annotations

from ..spec import Contexte

#: Fonctions codées et testées (le moteur ne les appelle qu'à cette condition).
IMPLEMENTEES: frozenset[str] = frozenset()
DEPENDENT_ETAPE: frozenset[str] = frozenset()


def chaikin(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("volume_flow.chaikin : à coder par le chantier de la famille VF "
                              "(interface : research/zoo/README.md)")


def choc_volume(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("volume_flow.choc_volume : à coder par le chantier de la famille VF "
                              "(interface : research/zoo/README.md)")


def force_elder(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("volume_flow.force_elder : à coder par le chantier de la famille VF "
                              "(interface : research/zoo/README.md)")


def klinger(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("volume_flow.klinger : à coder par le chantier de la famille VF "
                              "(interface : research/zoo/README.md)")


def ligne_ad(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("volume_flow.ligne_ad : à coder par le chantier de la famille VF "
                              "(interface : research/zoo/README.md)")


def mfi(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("volume_flow.mfi : à coder par le chantier de la famille VF "
                              "(interface : research/zoo/README.md)")


def obv(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("volume_flow.obv : à coder par le chantier de la famille VF "
                              "(interface : research/zoo/README.md)")
