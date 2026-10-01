"""Registre des expériences (SQLite) : tout est conservé, échecs compris.

Chaque exécution enregistre hypothèse, période, univers, empreinte des
données, commit, versions des dépendances, graine, paramètres, coûts, règles
de simulation, métriques et statut. Les consultations du test final sont
comptées : après la première, ce test n'est plus un test « vierge ».
"""
from __future__ import annotations

import json
import platform
import sqlite3
import subprocess
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

DEPENDENCIES = ["numpy", "pandas", "pyarrow", "httpx", "pydantic", "pydantic-settings", "typer", "rich"]


def new_run_id(prefix: str = "RUN") -> str:
    return f"{prefix}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"


def code_state() -> str:
    """Commit du code EXÉCUTÉ (dépôt qui contient ce paquet), « +DIRTY » s'il a des modifications non commitées.
    À distinguer de `CSI_ROOT` (données, registre) : depuis un worktree propre, c'est ce code qui tourne."""
    return git_state(Path(__file__).resolve().parents[3])


def git_state(root: Path) -> str:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
                                timeout=5, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True,
                               timeout=5, check=True).stdout.strip()
        return commit + ("+DIRTY" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "NO_GIT_COMMIT"


def dependency_versions() -> dict[str, str]:
    versions = {"python": platform.python_version()}
    for name in DEPENDENCIES:
        try:
            versions[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            versions[name] = "absent"
    return versions


class ExperimentRegistry:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    @contextmanager
    def connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, kind TEXT NOT NULL,
                hypothesis TEXT NOT NULL, strategy TEXT NOT NULL, strategy_version INTEGER NOT NULL,
                variant TEXT NOT NULL, params TEXT NOT NULL, period_label TEXT NOT NULL,
                period_start TEXT NOT NULL, period_end TEXT NOT NULL, universe TEXT NOT NULL,
                data_hashes TEXT NOT NULL, git_commit TEXT NOT NULL, dependencies TEXT NOT NULL,
                seed INTEGER NOT NULL, cost_scenario TEXT NOT NULL, simulation_rules TEXT NOT NULL,
                metrics TEXT NOT NULL, status TEXT NOT NULL, report_dir TEXT)""")
            db.execute("""CREATE TABLE IF NOT EXISTS final_test_consultations (
                consulted_at TEXT NOT NULL, run_id TEXT NOT NULL, strategy TEXT NOT NULL)""")
            yield db
            db.commit()
        finally:
            db.close()

    def record(self, **run) -> None:
        run = {k: (json.dumps(v, default=str, ensure_ascii=False) if isinstance(v, dict | list) else v)
               for k, v in run.items()}
        columns = ", ".join(run)
        with self.connect() as db:
            db.execute(f"INSERT INTO runs ({columns}) VALUES ({', '.join('?' for _ in run)})", tuple(run.values()))

    def count_runs(self, strategy: str | None = None) -> int:
        with self.connect() as db:
            if strategy:
                return db.execute("SELECT COUNT(*) FROM runs WHERE strategy=?", (strategy,)).fetchone()[0]
            return db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

    def consult_final_test(self, run_id: str, strategy: str) -> int:
        """Enregistre une consultation et renvoie le total TOUTES stratégies confondues : dès qu'une
        stratégie a regardé le test final, ce qu'on y a vu peut orienter les suivantes."""
        with self.connect() as db:
            db.execute("INSERT INTO final_test_consultations VALUES (?, ?, ?)",
                       (datetime.now(UTC).isoformat(), run_id, strategy))
            return db.execute("SELECT COUNT(*) FROM final_test_consultations").fetchone()[0]

    def final_test_consulted(self, strategy: str) -> int:
        """Consultations déjà enregistrées du test final pour cette stratégie (ou ce programme)."""
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM final_test_consultations WHERE strategy=?",
                              (strategy,)).fetchone()[0]

    def final_test_consultations_total(self) -> int:
        """Consultations du test final, toutes stratégies confondues (le compteur est global)."""
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM final_test_consultations").fetchone()[0]

    def program_trials(self, period_label: str = "DEVELOPMENT") -> int:
        """Nombre d'essais déjà faits sur la période par TOUT le programme de recherche : somme des
        `n_trials` enregistrés (combinaisons de grille, conditions × horizons), 1 pour une exécution
        sans ce champ (chaque variante de backtest est un regard de plus sur les mêmes données)."""
        with self.connect() as db:
            row = db.execute("""SELECT COALESCE(SUM(COALESCE(json_extract(metrics, '$.n_trials'), 1)), 0)
                                FROM runs WHERE period_label=?""", (period_label,)).fetchone()
        return int(row[0])

    def get(self, run_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            return None
        return {k: (json.loads(row[k]) if k in {"params", "universe", "data_hashes", "dependencies",
                                                  "simulation_rules", "metrics"} else row[k]) for k in row.keys()}  # noqa: SIM118 - sqlite3.Row n'est pas un dict

    def recent(self, limit: int = 20) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""SELECT run_id, created_at, kind, strategy, variant, period_label, cost_scenario,
                                        status, json_extract(metrics, '$.verdict') AS verdict
                                 FROM runs ORDER BY created_at DESC LIMIT ?""", (limit,)).fetchall()
        return [dict(row) for row in rows]
