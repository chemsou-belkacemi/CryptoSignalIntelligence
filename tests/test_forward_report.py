"""Rapport quotidien des tests en direct (forward/report.py) : il doit s'écrire quelles que soient les formes de
statistiques des tests démarrés (contrôle quotidien avec placebos F9-F11, prévisions de volatilité F12, tests non
démarrés), sans lever d'exception. SYNTHÉTIQUE."""
from __future__ import annotations

from datetime import UTC, datetime

from crypto_signal_intelligence.forward import f9, f10, f12, registry, report
from crypto_signal_intelligence.forward.halal import HalalList

HALAL = HalalList(("BTCUSDT", "ETHUSDT", "SOLUSDT"), {}, "a" * 64, "b" * 64)


def test_daily_report_handles_every_running_test_shape(settings):
    now = datetime(2026, 10, 5, 9, tzinfo=UTC)
    for test in (f9.TEST, f10.TEST, f12.TEST):
        registry.start(settings, test, now=now, allow_dirty=True, halal=HALAL)
    path = report.write(settings, now=datetime(2026, 10, 6, 1, tzinfo=UTC))
    text = path.read_text(encoding="utf-8")
    for test_id in ("F9_OI_FLUSH", "F10_PIVOT_BREAK_1D", "F12_VOL_FORWARD"):
        assert f"## {test_id}" in text
    assert "Prévisions journalisées : 0" in text and "| Horizon | Candidat | Référence |" in text
    assert "Contrôles : 0 ; événements : 0" in text and "Pas encore démarré." in text
    assert "Traceback" not in text
