"""Hook PostToolUse : `ruff check --fix` sur le fichier Python que Claude vient de modifier.

Lit l'événement JSON sur l'entrée standard. Code 0 : rien à signaler (ou fichier non Python).
Code 2 : erreurs ruff restantes, renvoyées à Claude (stderr) pour qu'il les corrige aussitôt.
Aucun reformatage : seulement les règles de lint du projet (pyproject.toml, [tool.ruff]).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[2]
RUFF = PROJECT / ".venv" / "Scripts" / "ruff.exe"


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    raw = (event.get("tool_input") or {}).get("file_path") or ""
    path = Path(raw)
    if path.suffix != ".py" or not path.is_file():
        return 0
    try:
        path.resolve().relative_to(PROJECT)
    except ValueError:
        return 0                                   # hors du projet : pas nos règles
    ruff = str(RUFF) if RUFF.exists() else "ruff"
    try:
        result = subprocess.run([ruff, "check", "--fix", "--quiet", str(path)], cwd=PROJECT,
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return 0                                   # ruff absent ou bloqué : ne jamais bloquer le travail
    if result.returncode != 0:
        sys.stderr.write(f"ruff : erreurs restantes dans {path.name}\n{result.stdout}{result.stderr}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
