"""CSI lit les signaux Telegram EXACTEMENT comme BinanceSpotManager (lecture par étiquettes de
`binance_spot_manager/signal_parser.py`) : mêmes signaux acceptés, mêmes prix. Formats tirés des canaux réels du
propriétaire (2026-10-02), prix d'exemple. La comparaison directe avec le code de BSM est sautée s'il est absent."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from crypto_signal_intelligence.external.parser import parse

AL_MAHWASHI = """👑 AL-MAHWASHI CRYPTO 👑
━━━━━━━━━━━━━━
#DUSK/USDT
━━━━━━━━━━━━━━
📍 Entry1: 0.0646
📍 Entry2: 0.0635
━━━━━━━━━━━━━━
🎯 TARGETS
🎯 TP1: 0.0655 (1.55%)(35%)
🎯 TP2: 0.0678 (5.12%)(20%)
🎯 TP3: 0.071 (10.08%)(15%)
━━━━━━━━━━━━━━
🛑 Stop: 0.0625 (1h)
🕌 الحكم الشرعي: مباح ✅
📅 Date: Sunday - 2026-07-26"""
INSTANT_STOP = """🕒🕒 AL-MAHWASHI CRYPTO 🕒🕒
RENDER/USDT
🔵 الدخول🔵 :
📍Entry : 1.292
🎯 TARGETS
📊 TP1: 1.340 🚀.
📊 TP2: 1.398 🚀.
📊 TP3: 1.450 🚀.
🛡Stop: 1.26(لحظي)🛡"""
HARMONIC = """📈 *Harmonic Pattern Detected*
💎 *PAIR:* ENJ/USDT
🔶 *ENTRY ZONE:*
✨ENTRY 1: 0.045
✨ENTRY 2: 0.043
🎯 *TARGETS:*
1️⃣ T1: 0.0457  📉 SELL(45%) (2.0%)
2️⃣ T2: 0.04695  📉 SELL(10%) (4.33%)
🛑 SL: 0.0418 (4h)"""
FOREIGN = """📈 Trader/ QUEEN CLEO
💎 PAIR: CGPT/USDT
🔶 ENTRY ZONE:
✨ ENTRY 1: 0.02105
🎯 TARGETS:
1️⃣ T1: 0.02160 (2.61%)
🛑 SL: 0.02060 (15m) (-2.14%)
🏦 PLATFORM: MEXC"""
CASES = [AL_MAHWASHI, INSTANT_STOP, HARMONIC, FOREIGN]


def test_real_channel_formats_are_read():
    a = parse(AL_MAHWASHI)
    assert not a.errors and a.symbol == "DUSKUSDT" and a.entries == [0.0646, 0.0635]
    assert a.targets == [0.0655, 0.0678, 0.071] and a.stop == 0.0625 and a.stop_timeframe == "1h"
    b = parse(INSTANT_STOP)                                     # « (لحظي) » = stop instantané : au toucher
    assert not b.errors and b.stop == 1.26 and b.stop_timeframe == "" and b.targets == [1.34, 1.398, 1.45]
    c = parse(HARMONIC)                                         # « SELL(45%) » = part vendue à l'objectif
    assert not c.errors and c.direction == "BUY" and c.targets == [0.0457, 0.04695] and c.stop_timeframe == "4h"
    assert parse(FOREIGN).errors == ["Plateforme autre que Binance : prix et liquidité non comparables."]


def _bsm():
    root = Path(os.environ.get("BSM_PATH", Path(__file__).resolve().parents[2] / "BinanceSpotManager"))
    path = root / "binance_spot_manager" / "signal_parser.py"
    if not path.exists():
        pytest.skip("BinanceSpotManager introuvable : définir BSM_PATH")
    spec = importlib.util.spec_from_file_location("bsm_signal_parser_alignment", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "LABELLED_LINE"):
        pytest.skip("cette version de BSM ne lit pas encore par étiquettes")
    return module


@pytest.mark.parametrize("text", CASES)
def test_csi_and_bsm_accept_the_same_signals_with_the_same_prices(text):
    b, c = _bsm().parse_signal(text), parse(text)
    assert bool(b.errors) == bool(c.errors)
    assert (b.symbol, b.entries, b.targets, b.stop, b.stop_timeframe) == \
        (c.symbol, c.entries, c.targets, c.stop, c.stop_timeframe)
