"""Sauvegarde, restauration et suspension de publication (fixtures SYNTHÉTIQUES)."""
from __future__ import annotations

import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from crypto_signal_intelligence.domain.enums import Action, EntryMode
from crypto_signal_intelligence.domain.market import EntryIntent, StrategyResult
from crypto_signal_intelligence.live.backup import (
    BackupError,
    create_backup,
    publication_suspended,
    restore_backup,
    resume_publication,
    verify_backup,
)
from crypto_signal_intelligence.live.lock import InstanceAlreadyRunning, InstanceLock
from crypto_signal_intelligence.live.scanner import scan_cycle
from crypto_signal_intelligence.signals.outbox import SignalRegistry
from crypto_signal_intelligence.strategies.donchian import DonchianVolumeBreakout
from tests.test_live import DECISION, FakeClock, no_download, store_candles
from tests.test_signals import one_tp_signal

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)


def published_state(settings) -> SignalRegistry:
    registry = SignalRegistry(settings.signals_db, settings.publication_dir())
    registry.publish(one_tp_signal(), NOW)
    return registry


def test_backup_is_consistent_verified_and_restorable(settings, tmp_path):
    registry = published_state(settings)
    archive = create_backup(settings, tmp_path / "b", now=NOW)
    manifest = verify_backup(archive)
    assert "signals/registry.sqlite3" in manifest["files"] and "signals/shadow/CSI-TEST-1.txt" in manifest["files"]
    assert not any(name.startswith("data/") and not name.endswith("archives.sqlite3") for name in manifest["files"])
    settings.signals_db.unlink()                                   # perte de l'état…
    (settings.publication_dir() / "CSI-TEST-1.txt").unlink()
    result = restore_backup(settings, archive, now=NOW + timedelta(hours=1))   # …puis restauration
    assert [r["signal_id"] for r in registry.rows()] == ["CSI-TEST-1"]
    assert (settings.publication_dir() / "CSI-TEST-1.txt").exists() and result.safety_backup.exists()
    assert (publication_suspended(settings) or "").startswith("restauration de")
    assert resume_publication(settings) and publication_suspended(settings) is None


def test_tampered_or_foreign_archive_is_refused_before_any_write(settings, tmp_path):
    published_state(settings)
    archive = create_backup(settings, tmp_path / "b", now=NOW)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive) as src, zipfile.ZipFile(tampered, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            dst.writestr(name, data + b"x" if name.endswith(".txt") else data)
    with pytest.raises(BackupError, match="empreinte"):
        restore_backup(settings, tampered, now=NOW)
    foreign = tmp_path / "foreign.zip"
    with zipfile.ZipFile(foreign, "w") as zf:
        zf.writestr("signals/registry.sqlite3", b"x")
    with pytest.raises(BackupError, match="manifeste"):
        verify_backup(foreign)
    assert publication_suspended(settings) is None


def test_restore_is_refused_while_a_monitor_runs(settings, tmp_path):
    published_state(settings)
    archive = create_backup(settings, tmp_path / "b", now=NOW)
    with InstanceLock(settings.root / settings.live.lock_file), pytest.raises(InstanceAlreadyRunning):
        restore_backup(settings, archive, now=NOW)


def test_suspension_blocks_publication_until_resumed(settings, monkeypatch):
    def always_buy(self, context):
        close = float(context.setup["close"])
        return StrategyResult(strategy_id=self.strategy_id, strategy_version=1, action=Action.BUY,
                              setup_time=context.decision_time, regime=context.regime,
                              entry_intent=EntryIntent(EntryMode.LIMIT, close, 10, 2),
                              invalidation_reference=close * 0.98, exit_policy_id="FIXED_SL_ONE_TP_V1", target_r=2.0)

    monkeypatch.setattr(DonchianVolumeBreakout, "evaluate", always_buy)
    store_candles(settings)
    (settings.root / "state").mkdir(parents=True, exist_ok=True)
    (settings.root / "state" / "PUBLICATION_SUSPENDED").write_text("restauration de test", encoding="utf-8")
    clock = FakeClock(DECISION + timedelta(seconds=20))
    kwargs = dict(symbols=["ETHUSDT"], strategies=["DONCHIAN_VOLUME_BREAKOUT"], downloader=no_download, clock=clock,
                  sleep=clock.sleep)
    blocked = scan_cycle(settings, now=clock(), **kwargs)
    assert [(o.reason_code, o.signal_id) for o in blocked.outcomes] == [("PUBLICATION_SUSPENDED", None)]
    resume_publication(settings)
    assert scan_cycle(settings, now=clock(), **kwargs).outcomes[0].publication_status == "PUBLISHED"
