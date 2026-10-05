"""Nom du trader écrit en tête d'un signal (`external.parser.group_of`) : en-têtes tirés des exports Telegram du
propriétaire (LEGEND TRADING, AL-MAHWASHI CRYPTO, IN CRYPTO, fichiers de son robot), raccourcis.

La forme canonique réunit les variantes d'écriture d'un même trader : F4 et F16 regroupent leurs fournisseurs par
nom exact. Un dernier test vérifie que CSI lit le même trader que BinanceSpotManager (`trader_name.trader_of`),
chargé par chemin comme le test de contrat ; il est sauté si le dépôt voisin est absent.
"""
from __future__ import annotations

import importlib.util
import sys
from types import ModuleType

import pytest

from crypto_signal_intelligence.external.parser import canonical_name, group_of
from tests.test_bsm_contract import BSM_ROOT

BODY = "\n#SOL/USDT\n📍 Entry1: 150\n🎯 TP1: 160\n🛑 Stop: 140"

HEADERS = [
    ("👑AL-MAHWASHI VIP👑\n───────────────────\n", "ALMAHWASHI VIP"),
    ("👑 AL-MAHWASHI CRYPTO 👑\n✨ بسم الله توكلت على الله ✨\n", "ALMAHWASHI CRYPTO"),
    ("👑 AL-MAHWASHI CRYPTO TRADING 👑\n", "ALMAHWASHI CRYPTO"),                    # variante déclarée
    ("👑 💎 HAMZAWY 💎 👑\n━━━━━━━━━━━━━━\n", "HAMZAWY"),
    ("👑 ام البنين 👑\n", "ام البنين"),
    ("📈 Trader/ Suhaib AlMashhadani\n✨ بسم الله توكلت على الله ✨\n", "SUHAIB ALMASHHADANI"),
    ("📈 Trader/Suhaib Al-Mashhadani\n", "SUHAIB ALMASHHADANI"),
    ("📈 HARMONIC TRADE DETECTED\nSuhaib AlMashhadani Harmonic Indicator\n──────\n✨ بسم الله توكلت على الله ✨\n",
     "SUHAIB ALMASHHADANI"),
    ("SUHAIB ALMASHHADANI SIGNAL - Bat Pattern Detected\n", "SUHAIB ALMASHHADANI"),
    ("Ph. Suhaib AlMashhadani\n───────────────────\n", "SUHAIB ALMASHHADANI"),
    ("*Harmonic Pattern Detected*\nAl-Afify Harmonic Indicator Ultra\nبسم الله توكلت على الله\n", "ALAFIFY"),
    ("🚨 ALAFIFY SIGNAL ALERT 🚨\n", "ALAFIFY"),
    ("Harmonic Pattern Detected\nApex Harmonic Indicator Ultra\n", "APEX"),
    ("TIME-BASED TRADE DETECTED\nABD ELOUADOUD TIME CYCLE INDICATOR\nبسم الله توكلت على الله\n", "ABDELOUADOUD"),
    ("👑 ABDELOUADOUD 👑\n", "ABDELOUADOUD"),
    ("*INCRYPTO TIME ANALYSIS INDICATOR*\nYASMINA BOUZID INDICATOR\nبسم الله الرحمن الرحيم\n", "YASMINA BOUZID"),
    ("🚨 ABK SIGNAL ALERT 🚨\n🌟 SPECIAL TRADE 🌟\n", "ABK"),
    ("معاينة الصفقة:\n👑 Abo yaseein 👑\n", "ABOYASEEIN"),
    ("👑 Aboyaseein 👑\n", "ABOYASEEIN"),
    ("👑 LEGEND TRADING 👑\nLEGEND TRADING INDICATOR\n", "LEGEND TRADING"),
    # Relecture du 2026-10-05 : donnée, date ou mot-dièse entre le nom et la paire, particule détachée, prénom.
    ("👑 HAMZAWY 👑\nType: Spot\nMarket: Spot\n", "HAMZAWY"),
    ("👑 HAMZAWY 👑\n05/10/2026 14:00\n", "HAMZAWY"),
    ("👑 HAMZAWY 👑\n#SOL\n", "HAMZAWY"),
    ("👑 HAMZAWY 👑\nإشارة شراء\n", "HAMZAWY"),
    ("👑 AL - MAHWASHI VIP 👑\n", "ALMAHWASHI VIP"),
    ("👑 عبد الرحمن 👑\nبسم الله الرحمن الرحيم\n", "عبد الرحمن"),
    ("Trader: Abdallah Al-Abyed\n", "ABDALLAH ALABYED"),
    # Deuxième relecture : formule vocalisée, « NOM : événement », autres séparateurs, dates, $SOL, descriptions.
    ("👑 HAMZAWY 👑\nبسم اللّه\n", "HAMZAWY"),
    ("👑 HAMZAWY 👑\nتوكّلت على الله\n", "HAMZAWY"),
    ("👑 HAMZAWY 👑\nبسـم الله\n", "HAMZAWY"),
    ("LEGEND TRADING: NEW SIGNAL\n", "LEGEND TRADING"),
    ("ALAFIFY : Spot Trade\n", "ALAFIFY"),
    ("👑 HAMZAWY 👑\nType = Spot\nType | Spot\nType → Spot\nRisk Level - High\n", "HAMZAWY"),
    ("👑 HAMZAWY 👑\n05-10\n5/10\n14.00\n14h00\n5 Oct 2026\n", "HAMZAWY"),
    ("👑 HAMZAWY 👑\n$SOL\n*#SOL*\n• #SOL\n", "HAMZAWY"),
    ("MOHAMED BEN - New Signal\n", "MOHAMED BEN"),
    ("AL-MAHWASHI CRYPTO - VIP\n", "ALMAHWASHI CRYPTO VIP"),                       # VIP n'est pas une description
    ("Suhaib AlMashhadani - Shark Pattern\n", "SUHAIB ALMASHHADANI"),
    # Troisième vérification : durcissements facultatifs.
    ("2 Main Traders\n", "2 MAIN TRADERS"),                                        # « Main » n'est pas « mai »
    ("👑 HAMZAWY 👑\n(#SOL)\n1. #SOL\nt.me/legend\n", "HAMZAWY"),
    ("👑 HAMZAWY 👑\nMonday\nOctober 5\n2PM UTC\n", "HAMZAWY"),
    ("👑 HAMZAWY 👑\nSOL\nBTC Analysis\nMid-Term\nLong Term Hold\n", "HAMZAWY"),
    ("HAMZAWY - Daily Chart\n", "HAMZAWY"),
    ("SUHAIB ALMASHHADANI SIGNAL - Gartley\n", "SUHAIB ALMASHHADANI"),
    ("Suhaib AlMashhadani: Bat Pattern\n", "SUHAIB ALMASHHADANI"),
    ("المحلل: حمزاوي\n", "حمزاوي"),
]


@pytest.mark.parametrize("header, expected", HEADERS)
def test_the_trader_is_read_from_the_header_in_canonical_form(header, expected):
    assert group_of(header + BODY) == expected


@pytest.mark.parametrize("text", [
    "┏━━━━━━━━━━━━━━━━━━┓\n┃     #RIF/USDT    ┃\n┗━━━━━━━━━━━━━━━━━━┛\n📍 Entry 1: 0.0614\n🎯 TP1: 0.065\n🛑 SL: 0.058",
    "👑 ⚡ 👑\n━━━━━━━━━━━━━━" + BODY,
    "#BTC/USDT\n👑 HAMZAWY 👑\nEntry1: 84000\nTP1: 90000\nStop: 80000",            # nom après la paire : non lu
    "✨ بسم الله توكلت على الله ✨" + BODY,
    "Nous avons une très belle opportunité aujourd'hui sur le marché, regardez bien ce graphique" + BODY,
    "👑 HAMZAWY 👑\nbonjour à tous, le marché est calme aujourd'hui",          # pas un signal : aucun nom
    "🚨 VIP SIGNAL 🚨" + BODY,                                                    # mots banals seuls : personne
    "BINANCE SPOT SIGNAL" + BODY,
    "CRYPTO VIP" + BODY,
    "IN CRYPTO" + BODY,
    "توصيات كريبتو" + BODY,
    "Note: New Signal" + BODY,
    "Good morning traders\nVIP SIGNAL" + BODY,                                   # salutation : pas un nom
])
def test_no_name_is_invented(text):
    assert group_of(text) == ""


def test_canonical_names_are_strict():
    assert canonical_name("AL-MAHWASHI VIP") != canonical_name("AL-MAHWASHI CRYPTO")
    # aucun mot retiré : deux canaux qui partagent un mot restent distincts
    assert len({canonical_name(n) for n in ("CRYPTO LEGEND", "LEGEND TRADING", "Legend Trader")}) == 3
    assert canonical_name("CRYPTO KING") != canonical_name("KING TRADING")
    assert canonical_name("Abdallah Al-Abyed") == canonical_name("Abdallah Al-abyed") == "ABDALLAH ALABYED"
    assert canonical_name("Légende") == "LEGENDE" and canonical_name("Queen cleo") == canonical_name("QUEEN CLEO")


def load_bsm_trader_name() -> ModuleType:
    """`trader_name.py` de BSM et son `signal_parser.py`, chargés par chemin sous un paquet fictif (import relatif)."""
    folder = BSM_ROOT / "binance_spot_manager"
    if not (folder / "trader_name.py").exists():
        pytest.skip("trader_name.py de BinanceSpotManager introuvable : définir BSM_PATH")
    package = ModuleType("bsm_names")
    package.__path__ = [str(folder)]
    sys.modules["bsm_names"] = package
    for name in ("signal_parser", "trader_name"):
        spec = importlib.util.spec_from_file_location(f"bsm_names.{name}", folder / f"{name}.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules["bsm_names.trader_name"]


def test_csi_reads_and_groups_like_bsm():
    """Même lecture (`trader_of`) et même forme canonique (`name_key`) que BSM, en-tête par en-tête."""
    bsm = load_bsm_trader_name()
    for header, expected in HEADERS:
        assert bsm.name_key(bsm.trader_of(header + BODY)) == expected == group_of(header + BODY)
        assert canonical_name(bsm.trader_of(header + BODY)) == expected
