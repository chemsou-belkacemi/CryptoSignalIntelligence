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
    "F3_STABLECOINS": {"doc": "02d1dc70654fb83cb08b08df10363b6e",
                       "params": "68770138b575681d47a661ce40b762b5",
                       "code": "ff516d5b976709c821981e9392431bf2"},
    "F4_TELEGRAM": {"doc": "37153713c1eb418643bd11d91029fa54",
                    "params": "d7783cc8ddb8cbd20afae1c8dbfeab9f",
                    "code": "9306290ff2439f22adf4e76a8735701c"},
    "F5_MODELE_A": {"doc": "dfc6760a5212e5e035de160fa936606d",
                    "params": "36afd8799936e4662c5f0375b966f092",
                    "code": "a8c63f22ab2ae88918f377a3f380a7f1"},
    "F6_CAPITULATION": {"doc": "45de7c4edb326324f71a09542cf2c5d3",
                        "params": "df9d1109fcda2c214e7830ae3dc51540",
                        "code": "ecf3bacc5053d3698afbb87b91c7a6d1"},
    "F7_LISTINGS": {"doc": "e48b6d89fe0f96780eb34dde6d35e226",
                    "params": "70ffd25f68524f044bba65f51f2aada3",
                    "code": "f2ba122f0ce3151146484617aaa9b428"},
    "F8_NEWS": {"doc": "a9ad4d8b16ed457f56dd1f506a43b56e",
                "params": "822ceef77e4931ef33d21decb864c926",
                "code": "67669f372d4361ccb2a89658de6dcc01"},
    "F9_OI_FLUSH": {"doc": "8a462b3b900ed85b08cc8a9f465915f7",
                    "params": "87afbf5d5501766b72f4dd1676a50b60",
                    "code": "8b40c57f2c955edb2bbcfa11bb2b90dc"},
    "F10_PIVOT_BREAK_1D": {"doc": "210df143838b10e6af3eeb0835548d1b",
                           "params": "ac67404be011c613c7ee359701a20635",
                           "code": "adfd33331b18d6c5aa765030449454a3"},
    "F11_SELL_PRESSURE_VETO": {"doc": "556da31af0804602ea3913778566e630",
                               "params": "eee117b71102ec804edd45a331361cf8",
                               "code": "6a4bf7bbd2d12e296e5d1a721221b9d5"},
    "F12_VOL_FORWARD": {"doc": "66db0d0ca744a8922accde416af6130d",
                        "params": "4832f3d7ee2ba366a1147f1e7726a8fd",
                        "code": "e25ca34972a0b487d9e087ffb805b0ad"},
    "F13_PIVOT_BREAK_1D_24": {"doc": "7f453a6358aca0ea4b0fae60156074d5",
                              "params": "c4a6717cc3341b31bcf0e8c0b51abc96",
                              "code": "ae87e63584ea4c27ee0ff29165bc9731"},
    "F14_PIVOT_BREAK_VOL_LEVELS": {"doc": "c62f16f1d21fbfe432e9a201411280f1",
                                   "params": "a18fdecd93b54a18645a21b4c3e69c65",
                                   "code": "aed755011889bbc043733c1030f921a3"},
    "F15_FIGURES": {"doc": "91001d7efed3ae5a567b5fba2bfbb406",
                    "params": "c177e0f2e0a99cd648c0c49604b84ab3",
                    "code": "49ebe8f0ca2c4cc90a7b4525e4b72cec"},
    "F16_TELEGRAM_IMAGES": {"doc": "3566bfdd683b831cb50ccc9536ac85a5",
                            "params": "3970b69908b5c99257779251d67a9638",
                            "code": "1c6e2e8fdc5b9fcf2ef3146739651437"},
    "F18_ASSISTANT": {"doc": "90ee4a3b41e3739346bfd9640a7e8e34",
                      "params": "dbd26ce915d32d1d08ab5e174b5fc675",
                      "code": "bab922fe1708ad816f18ce769db23cbb"},
}


@pytest.mark.parametrize("test_id", sorted(STARTED))
def test_frozen_fingerprints_of_running_tests_are_unchanged(test_id, monkeypatch):
    monkeypatch.setenv("CSI_CONFIG_FILE", str(PROJECT / "config" / "default.toml"))
    doc = PROJECT.joinpath("docs", "FORWARD_TESTS.md").read_text(encoding="utf-8")
    # 32 premiers caractères (128 bits) de chaque empreinte SHA-256 : assez pour voir toute modification.
    current = registry.fingerprints(BY_ID[test_id][0], doc, load_settings())
    assert {k: v[:32] for k, v in current.items()} == STARTED[test_id]
