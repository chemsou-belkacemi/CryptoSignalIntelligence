"""Reprise après interruption et publication sans doublon (audit du 2026-10-01, lacunes L5 à L7)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from typer.testing import CliRunner

from crypto_signal_intelligence.cli import app
from crypto_signal_intelligence.live.backup import SUSPENSION_FILE
from crypto_signal_intelligence.live.lock import InstanceLock
from crypto_signal_intelligence.signals.outbox import SignalRegistry, sha256_text
from crypto_signal_intelligence.signals.txt import serialize

from .test_signals import one_tp_signal

CREATED = datetime(2026, 9, 29, 12, 0, 5, tzinfo=UTC)


def pending(registry: SignalRegistry, signal) -> None:
    """Crash simulé : enregistrement PENDING sans fichier."""
    text = serialize(signal)
    with registry.connect() as db:
        db.execute("INSERT INTO signals VALUES (?, ?, ?, ?, 'c', 'e', 'PENDING', ?, ?, ?, ?, NULL)",
                   (signal.signal_id, signal.idempotency_key, signal.symbol, signal.strategy,
                    str(registry.directory), f"{signal.signal_id}.txt", sha256_text(text), text))


def test_an_expired_pending_signal_is_never_written(tmp_path):
    registry = SignalRegistry(tmp_path / "db.sqlite3", tmp_path / "shadow")
    pending(registry, one_tp_signal())
    counts = registry.reconcile(now=CREATED + timedelta(minutes=16))            # EXPIRES_AT = création + 15 min
    assert counts["expired"] == 1 and counts["published"] == 0
    assert not (tmp_path / "shadow" / "CSI-TEST-1.txt").exists()
    assert registry.reconcile(now=CREATED + timedelta(minutes=1))["published"] == 1


def test_suspended_publication_holds_pending_signals(tmp_path):
    registry = SignalRegistry(tmp_path / "db.sqlite3", tmp_path / "shadow")
    pending(registry, one_tp_signal())
    counts = registry.reconcile(publish=False, now=CREATED)
    assert counts["held"] == 1 and not (tmp_path / "shadow" / "CSI-TEST-1.txt").exists()


def test_publication_resume_without_confirmation_writes_nothing(settings, monkeypatch):
    monkeypatch.chdir(settings.root)
    registry = SignalRegistry(settings.signals_db, settings.publication_dir())
    soon = datetime.now(UTC).replace(microsecond=0)
    pending(registry, one_tp_signal(expires_at=soon + timedelta(hours=1), entry_expires_at=soon + timedelta(hours=2)))
    (settings.root / SUSPENSION_FILE).parent.mkdir(parents=True, exist_ok=True)
    (settings.root / SUSPENSION_FILE).write_text("restauration de test", encoding="utf-8")
    result = CliRunner().invoke(app, ["publication-resume"])
    assert result.exit_code == 1
    assert not list(settings.publication_dir().glob("*.txt"))
    result = CliRunner().invoke(app, ["publication-resume", "--yes"])
    assert result.exit_code == 0 and len(list(settings.publication_dir().glob("*.txt"))) == 1


def test_publishing_commands_refuse_to_run_beside_the_monitor(settings):
    with InstanceLock(settings.root / settings.live.lock_file):
        for command in (["scan", "--no-refresh"], ["signals-reconcile"], ["analyze", "--no-refresh"]):
            result = CliRunner().invoke(app, command)
            assert result.exit_code == 3, (command, result.output)
    assert not list(settings.publication_dir().glob("*.txt"))
