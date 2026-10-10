"""Retour à la moyenne (MR) : définitions déclaratives au registre (`research/zoo/spec.py`), À CODER.

Chaque fonction ci-dessous lève `NotImplementedError` tant que le chantier de la famille MR ne l'a pas codée
sur l'interface du zoo (`research/zoo/README.md` : fonction pure `signal(frame, ctx)`, bougies closes, aucune
I/O). Une fonction codée est ajoutée à `IMPLEMENTEES`, avec les tests exigés par le README (définitions calculées
à la main, causalité et mutations, comptage). Le moteur refuse l'exécution réelle tant qu'un test retenu n'est pas
codé."""
from __future__ import annotations

from ..spec import Contexte

#: Fonctions codées et testées (le moteur ne les appelle qu'à cette condition).
IMPLEMENTEES: frozenset[str] = frozenset()
DEPENDENT_ETAPE: frozenset[str] = frozenset()


def bollinger_reintegration(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("mean_reversion.bollinger_reintegration : à coder par le chantier de la famille MR "
                              "(interface : research/zoo/README.md)")


def cci(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("mean_reversion.cci : à coder par le chantier de la famille MR "
                              "(interface : research/zoo/README.md)")


def choc_rendement(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("mean_reversion.choc_rendement : à coder par le chantier de la famille MR "
                              "(interface : research/zoo/README.md)")


def double_sept(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("mean_reversion.double_sept : à coder par le chantier de la famille MR "
                              "(interface : research/zoo/README.md)")


def ibs(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("mean_reversion.ibs : à coder par le chantier de la famille MR "
                              "(interface : research/zoo/README.md)")


def rsi2_connors(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("mean_reversion.rsi2_connors : à coder par le chantier de la famille MR "
                              "(interface : research/zoo/README.md)")


def rsi_30_70(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("mean_reversion.rsi_30_70 : à coder par le chantier de la famille MR "
                              "(interface : research/zoo/README.md)")


def stochastique(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("mean_reversion.stochastique : à coder par le chantier de la famille MR "
                              "(interface : research/zoo/README.md)")


def trois_baisses(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("mean_reversion.trois_baisses : à coder par le chantier de la famille MR "
                              "(interface : research/zoo/README.md)")


def vwap_ecart(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("mean_reversion.vwap_ecart : à coder par le chantier de la famille MR "
                              "(interface : research/zoo/README.md)")


def williams_r(frame, ctx: Contexte):
    """Forme (a) : `frame` = Bougies, rend `Signaux`. Paramètres : `ctx.params` (registre)."""
    raise NotImplementedError("mean_reversion.williams_r : à coder par le chantier de la famille MR "
                              "(interface : research/zoo/README.md)")
