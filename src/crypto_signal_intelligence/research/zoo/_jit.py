"""numba si présent (extra `research`), sinon les mêmes fonctions en Python pur (CI, tests sur petites séries)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

_njit: Any
try:  # pragma: no cover - dépend de l'environnement
    from numba import njit as _njit

    HAS_NUMBA = True
except ImportError:  # pragma: no cover
    _njit = None
    HAS_NUMBA = False


def njit(function: Callable[..., Any]) -> Callable[..., Any]:
    """Compile avec numba (cache disque) quand il est installé ; sinon rend la fonction telle quelle."""
    if _njit is None:
        return function
    return _njit(cache=True, nogil=True)(function)
