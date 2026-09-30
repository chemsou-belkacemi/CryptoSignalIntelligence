"""Hygiène du dépôt : tout le code source est versionné.

Le 2026-09-30, la règle « data/ » du .gitignore masquait le paquet src/crypto_signal_intelligence/data
(et « news/ » le paquet news) : le code tournait en local, mais tout clone était cassé.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parents[1]


def test_every_source_and_test_file_is_versioned():
    try:
        ignored = subprocess.run(
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard", "src", "tests", "config", "scripts"],
            cwd=PROJECT, capture_output=True, text=True, timeout=10, check=True).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git indisponible ou pas de dépôt")
    hidden = [f for f in ignored if f.endswith((".py", ".toml", ".json", ".ps1", ".md"))
              and "__pycache__" not in f and ".egg-info" not in f]
    assert not hidden, f"fichiers du projet masqués par le .gitignore : {hidden}"
