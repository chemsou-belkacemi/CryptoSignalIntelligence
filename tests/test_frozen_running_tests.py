"""Garde-fou : le code GELÉ d'un test en direct qui tourne ne doit pas changer (sinon le test s'arrête pour de bon).

Les empreintes relevées au démarrage sont copiées ici. Si ce test échoue, NE PAS mettre la valeur à jour : la
modification arrêterait le test en service. Annuler la modification, ou la faire dans un NOUVEAU module (nouveau
test pré-inscrit). Mettre à jour une ligne seulement quand le test correspondant est clos (entrée CLOTURE)."""
from __future__ import annotations

import pytest

from crypto_signal_intelligence.config import load_settings
from crypto_signal_intelligence.forward import registry
from crypto_signal_intelligence.forward.tests import BY_ID

from .conftest import PROJECT

STARTED = {
    "F1_MAKER_TAKER": {"doc": "a6a08771608ebc006fc3d6c6025934c23cda8b1481707153b7ce0df7f191f0d2",
                       "params": "08db849104a10f7d901b53497235a4859a0b5a74200d4434cbeb4b2028979f92",
                       "code": "a85393ad1076368fd48e2cafcc385b7276492a292862aa2a517093d9f7965ddb"},
}


@pytest.mark.parametrize("test_id", sorted(STARTED))
def test_frozen_fingerprints_of_running_tests_are_unchanged(test_id, monkeypatch):
    monkeypatch.setenv("CSI_CONFIG_FILE", str(PROJECT / "config" / "default.toml"))
    doc = PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8")
    assert registry.fingerprints(BY_ID[test_id][0], doc, load_settings()) == STARTED[test_id]
