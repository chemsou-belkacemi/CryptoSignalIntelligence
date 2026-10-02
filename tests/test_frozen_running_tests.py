"""Garde-fou : le code GELÉ d'un test en direct qui tourne ne doit pas changer (sinon le test s'arrête pour de bon).

Les empreintes relevées au démarrage sont copiées ici (32 premiers caractères). Si ce test échoue, NE PAS mettre la valeur à jour : la
modification arrêterait le test en service. Annuler la modification, ou la faire dans un NOUVEAU module (nouveau
test pré-inscrit). Mettre à jour une ligne seulement quand le test correspondant est clos (entrée CLOTURE)."""
from __future__ import annotations

import pytest

from crypto_signal_intelligence.config import load_settings
from crypto_signal_intelligence.forward import registry
from crypto_signal_intelligence.forward.tests import BY_ID

from .conftest import PROJECT

STARTED = {
    "F1_MAKER_TAKER": {"doc": "a6a08771608ebc006fc3d6c6025934c2",
                       "params": "08db849104a10f7d901b53497235a485",
                       "code": "a85393ad1076368fd48e2cafcc385b72"},
    "F2_ECHELLES": {"doc": "4b2751fff4b03e7719b0cb17c9c5e58a",
                    "params": "70b894cc0934d89baf8a589f7614e7a8",
                    "code": "9afb3dec7d18e9bec5a05d765ca1670a"},
}


@pytest.mark.parametrize("test_id", sorted(STARTED))
def test_frozen_fingerprints_of_running_tests_are_unchanged(test_id, monkeypatch):
    monkeypatch.setenv("CSI_CONFIG_FILE", str(PROJECT / "config" / "default.toml"))
    doc = PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8")
    # 32 premiers caractères (128 bits) de chaque empreinte SHA-256 : assez pour voir toute modification.
    current = registry.fingerprints(BY_ID[test_id][0], doc, load_settings())
    assert {k: v[:32] for k, v in current.items()} == STARTED[test_id]
