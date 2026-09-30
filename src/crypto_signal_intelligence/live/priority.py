"""Travaux lourds en priorité basse (point 4) : backtests et walk-forward ne doivent pas ralentir `run`.

Priorité CPU « inférieure à la normale » (Windows) ou `nice +10` (Linux, macOS). La mémoire n'est
pas plafonnée ici : sur une machine limitée, lancer un seul travail lourd à la fois.
"""
from __future__ import annotations

import os
import sys

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


def lower_priority() -> bool:
    """Abaisse la priorité du processus courant ; retourne False si le système l'a refusé."""
    try:
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            # HANDLE est un pointeur : sans ces déclarations, ctypes tronque le pseudo-handle en 32 bits.
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            kernel32.SetPriorityClass.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
            return bool(kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS))
        os.nice(10)
        return True
    except (OSError, AttributeError):
        return False
