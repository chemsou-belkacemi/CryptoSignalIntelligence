"""Rotation et marché (RO), forme (b) : définitions déclaratives au registre (`research/zoo/spec.py`), À CODER.

Chaque fonction ci-dessous lève `NotImplementedError` tant que le chantier de la famille RO ne l'a pas codée
sur l'interface du zoo (`research/zoo/README.md` : fonction pure `signal(frame, ctx)`, bougies closes, aucune
I/O). Une fonction codée est ajoutée à `IMPLEMENTEES`, avec les tests exigés par le README (définitions calculées
à la main, causalité et mutations, comptage). Le moteur refuse l'exécution réelle tant qu'un test retenu n'est pas
codé."""
from __future__ import annotations

from ..spec import Contexte

#: Fonctions codées et testées (le moteur ne les appelle qu'à cette condition).
IMPLEMENTEES: frozenset[str] = frozenset()
DEPENDENT_ETAPE: frozenset[str] = frozenset()


def altseason(frame, ctx: Contexte):
    """Forme (b) : `frame` = Panneau, rend `Poids`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("rotation.altseason : à coder par le chantier de la famille RO "
                              "(interface : research/zoo/README.md)")


def correlation(frame, ctx: Contexte):
    """Forme (b) : `frame` = Panneau, rend `Poids`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("rotation.correlation : à coder par le chantier de la famille RO "
                              "(interface : research/zoo/README.md)")


def eth_btc(frame, ctx: Contexte):
    """Forme (b) : `frame` = Panneau, rend `Poids`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("rotation.eth_btc : à coder par le chantier de la famille RO "
                              "(interface : research/zoo/README.md)")


def largeur(frame, ctx: Contexte):
    """Forme (b) : `frame` = Panneau, rend `Poids`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("rotation.largeur : à coder par le chantier de la famille RO "
                              "(interface : research/zoo/README.md)")
