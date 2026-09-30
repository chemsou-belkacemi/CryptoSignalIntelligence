"""Garde-fou : aucun secret écrit en dur, aucun fichier .env versionné.

CSI n'utilise aucune clé API (données publiques Binance seulement) : toute clé ou tout secret
apparaissant dans le code, la configuration ou les scripts est une erreur, pas un réglage.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
SCANNED = ("src", "scripts", "config", "tests", "docker-compose.yml", "Dockerfile")
SUFFIXES = {".py", ".ps1", ".toml", ".json", ".yml", ".yaml", ".sh", ".cfg", ".ini", ""}
# Nom évoquant un secret, affecté à une valeur littérale longue (≥ 16 caractères sans espace).
ASSIGNED_SECRET = re.compile(
    r"(?i)(api[_-]?key|api[_-]?secret|secret[_-]?key|access[_-]?token|bot[_-]?token|password|passwd)"
    r"\s*[:=]\s*['\"][^'\"\s]{16,}['\"]")
# Formes reconnaissables : clé Binance (64 alphanumériques), jeton de bot Telegram, clé privée PEM.
KNOWN_FORMATS = {
    "clé de type Binance": re.compile(r"['\"][A-Za-z0-9]{64}['\"]"),
    "jeton de bot Telegram": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "clé privée": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def _files() -> list[Path]:
    out = []
    for name in SCANNED:
        root = PROJECT / name
        candidates = [root] if root.is_file() else root.rglob("*") if root.is_dir() else []
        out += [p for p in candidates if p.is_file() and p.suffix in SUFFIXES
                and "__pycache__" not in p.parts and p.name != Path(__file__).name]
    return out


def test_no_hardcoded_secret():
    findings = []
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), 1):
            where = f"{path.relative_to(PROJECT)}:{number}"
            if ASSIGNED_SECRET.search(line):
                findings.append(f"{where} : secret affecté en dur")
            findings += [f"{where} : {label}" for label, rx in KNOWN_FORMATS.items() if rx.search(line)]
    assert not findings, "Secrets à déplacer vers des variables d'environnement :\n" + "\n".join(findings)


def test_no_env_file_is_tracked():
    try:
        tracked = subprocess.run(["git", "ls-files"], cwd=PROJECT, capture_output=True, text=True,
                                 timeout=10, check=True).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        import pytest
        pytest.skip("git indisponible")
    env_files = [f for f in tracked if re.fullmatch(r"(.*/)?\.env(\..+)?", f) and not f.endswith(".env.example")]
    assert not env_files, f"fichiers d'environnement versionnés : {env_files}"
