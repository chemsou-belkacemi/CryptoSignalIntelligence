"""Liste des tests en direct pré-inscrits (docs/FORWARD_TESTS.md) : (définition figée, module qui l'exécute).

Un module de test expose `record_decisions(settings, journal, start, now=)`, `resolve(settings, journal, now=)`,
`finalize(journal, start, now=)` et `stats(journal, start, now=)`.
"""
from __future__ import annotations

from . import f1, f2

TESTS = ((f1.TEST, f1), (f2.TEST, f2))
BY_ID = {test.test_id: (test, module) for test, module in TESTS}
