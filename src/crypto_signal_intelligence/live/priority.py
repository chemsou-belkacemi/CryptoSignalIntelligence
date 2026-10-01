"""Travaux lourds bridés (point 4) : backtests, walk-forward et criblage ne doivent pas gêner `run`.

- CPU : priorité « inférieure à la normale » (Windows) ou `nice +10` (Linux, macOS).
- Mémoire : plafond du processus. Windows : objet de tâche (Job Object) avec
  JOB_OBJECT_LIMIT_PROCESS_MEMORY, le système refuse proprement toute allocation au-delà (MemoryError
  côté Python) au lieu de faire tomber la machine. Linux : la limite vient du conteneur (Docker
  `tools`, `mem_limit`) ; RLIMIT_AS compterait la mémoire virtuelle réservée par numpy/pyarrow et
  bloquerait des travaux sains, il n'est donc pas utilisé.
"""
from __future__ import annotations

import os
import sys

BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100


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


def limit_memory(max_mb: int) -> bool:
    """Plafonne la mémoire du processus courant (Windows) ; False si non appliqué (autre système, refus)."""
    if max_mb <= 0 or sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
        kernel32.SetInformationJobObject.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        kernel32.AssignProcessToJobObject.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return False
        info = ExtendedLimits()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = max_mb * 1024 * 1024
        if not kernel32.SetInformationJobObject(job, JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                                                ctypes.byref(info), ctypes.sizeof(info)):
            return False
        # Le handle du job reste ouvert jusqu'à la fin du processus : le plafond vaut pour toute sa vie.
        return bool(kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()))
    except (OSError, AttributeError):
        return False


def peak_memory_mb() -> dict[str, int] | None:
    """Pics de mémoire du processus courant (Windows) : engagée (ce que compte le plafond) et en RAM."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi = ctypes.windll.psapi  # type: ignore[attr-defined]
        psapi.GetProcessMemoryInfo.argtypes = (ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD)
        if not psapi.GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return None
        return {"committed_peak_mb": counters.PeakPagefileUsage // 2**20,
                "ram_peak_mb": counters.PeakWorkingSetSize // 2**20}
    except (OSError, AttributeError):
        return None


def heavy_job(max_mb: int) -> dict[str, bool]:
    """Bride le processus courant pour un travail lourd : priorité basse et plafond mémoire."""
    return {"priority_lowered": lower_priority(), "memory_limited": limit_memory(max_mb)}
