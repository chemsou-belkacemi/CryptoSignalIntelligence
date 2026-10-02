"""Liste des tests en direct pré-inscrits (docs/FORWARD_TESTS.md) : (définition figée, module qui l'exécute).

Un module de test expose `record_decisions(settings, journal, start, now=)`, `resolve(settings, journal, now=)`,
`finalize(journal, start, now=)` et `stats(journal, start, now=)` ; en option `poll(settings, journal, start, now=)`,
appelé à chaque cycle de la surveillance (toutes les 15 min) pour les tests qui détectent des événements en direct.
"""
from __future__ import annotations

from . import f1, f2, f3, f4, f5, f6, f7, f8, f9, f10, f11

TESTS = ((f1.TEST, f1), (f2.TEST, f2), (f3.TEST, f3), (f4.TEST, f4), (f5.TEST, f5), (f6.TEST, f6), (f7.TEST, f7),
         (f8.TEST, f8), (f9.TEST, f9), (f10.TEST, f10), (f11.TEST, f11))
BY_ID = {test.test_id: (test, module) for test, module in TESTS}
