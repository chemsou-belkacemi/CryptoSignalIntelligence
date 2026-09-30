"""Diagnostic : environnement, dépendances, configuration, droits, réseau public, stockage."""
from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from dataclasses import dataclass
from importlib import metadata

from .config import Settings
from .data.http import PublicHttpClient
from .data.rest import fetch_tick_size


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    blocking: bool = True


def run(settings: Settings, *, network: bool = True) -> list[Check]:
    checks = [Check("Python", sys.version_info >= (3, 12), sys.version.split()[0])]
    for package in ("numpy", "pandas", "pyarrow", "httpx", "pydantic", "pydantic-settings", "typer", "rich"):
        try:
            checks.append(Check(f"dépendance {package}", True, metadata.version(package)))
        except metadata.PackageNotFoundError:
            checks.append(Check(f"dépendance {package}", False, "absente"))
    checks.append(Check("configuration", True, f"racine {settings.root} ; paires {', '.join(settings.data.symbols)}"))
    checks.append(Check("publication", True,
                        f"mode {settings.publication.mode} → {settings.publication_dir()} ; INTEGRATION_UNVERIFIED",
                        blocking=False))
    for directory in (settings.data_dir, settings.reports_dir, settings.experiments_db.parent,
                      settings.publication_dir()):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            checks.append(Check(f"écriture {directory.name}", True, str(directory)))
        except OSError as exc:
            checks.append(Check(f"écriture {directory.name}", False, str(exc)))
    for database in (settings.experiments_db, settings.signals_db, settings.data_dir / "raw" / "archives.sqlite3"):
        if database.exists():
            with closing(sqlite3.connect(database)) as db:
                result = db.execute("PRAGMA integrity_check").fetchone()[0]
            checks.append(Check(f"intégrité {database.name}", result == "ok", result))
    checks.append(Check("clé LLM", True, "non requise en mode quantitatif", blocking=False))
    if network:
        rest = PublicHttpClient.rest(settings.data.rest_base_url, retries=2)
        try:
            rest.get_json("/api/v3/ping")
            checks.append(Check("REST public Binance", True, settings.data.rest_base_url))
            for symbol in settings.data.symbols:
                live = fetch_tick_size(rest, symbol)
                configured = settings.data.tick_size.get(symbol)
                checks.append(Check(f"tickSize {symbol}", configured is not None and configured.normalize() == live,
                                    f"configuré {configured}, marché de données {live} "
                                    "(les filtres de la démo peuvent différer)"))
        except Exception as exc:  # noqa: BLE001 - diagnostic
            checks.append(Check("REST public Binance", False, str(exc)))
        finally:
            rest.close()
        archives = PublicHttpClient.archives(settings.data.archive_base_url, retries=2)
        try:
            symbol = settings.data.symbols[0]
            archives.get(f"/data/spot/monthly/klines/{symbol}/1h/{symbol}-1h-2024-01.zip.CHECKSUM")
            checks.append(Check("archives Binance Public Data", True, settings.data.archive_base_url))
        except Exception as exc:  # noqa: BLE001
            checks.append(Check("archives Binance Public Data", False, str(exc)))
        finally:
            archives.close()
    return checks
