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
ONE_LETTER = "👑 WHALE HUNTING\nPAIR : G/USDT\nENTRY 1 : 0.00456\nTP 1 : 0.00463\nSL : 0.00438 (15m)"
HASH_T = "#T/USDT\nEntry: 0.02\nTP1: 0.022\nSL: 0.018"
JOINED_D = "Coin: DUSDT\nEntry: 0.02\nTP1: 0.022\nSL: 0.018"
# Jamais une crypto à une lettre : « w/ » (with) avant une devise, ou un trait d'union (relecture du 2026-10-06).
WITH_USDT = "Buy XRP w/ USDT\nEntry 0.5\nTP 0.55\nSL 0.45"
WITH_BTC = "#SOL/USDT\nEntry: 150\nTP1: 160\nSL: 140\ncorrelation w/ BTC"
DASH_W = "#W-USDT\nEntry: 0.1\nTP1: 0.12\nSL: 0.09"
CASES = [AL_MAHWASHI, INSTANT_STOP, HARMONIC, FOREIGN, ONE_LETTER, HASH_T, JOINED_D, WITH_USDT, WITH_BTC, DASH_W]


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


def test_one_letter_coins_are_read_and_with_is_never_a_coin():
    """2026-10-06 : G/USDT (WHALE HUNTING) n'était lu ni par CSI ni par BSM ; la comparaison avec BSM est faite sur
    CASES (test précédent). Une lettre seule n'est une crypto qu'avec « / » collé ou collée à USDT."""
    for text, symbol in ((ONE_LETTER, "GUSDT"), (HASH_T, "TUSDT"), (JOINED_D, "DUSDT"), (WITH_BTC, "SOLUSDT")):
        result = parse(text)
        assert result.symbol == symbol and not result.errors, text
    assert parse(WITH_USDT).errors and parse(DASH_W).errors
    assert parse("PAIR: 1/USDT\nENTRY 1: 2\nT1: 3\nSL: 1").errors


def test_history_reports_carry_the_parser_fingerprint(settings):
    from datetime import UTC, datetime

    from crypto_signal_intelligence.external.audit import audit, parser_fingerprint
    report = audit(settings, [], now=datetime(2026, 10, 6, tzinfo=UTC))
    assert any(parser_fingerprint()[:16] in note for note in report.notes)
