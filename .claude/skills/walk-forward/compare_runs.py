"""Compare les deux derniers walk-forwards d'une stratégie (lecture seule du registre d'expériences).

Usage : .venv/Scripts/python.exe .claude/skills/walk-forward/compare_runs.py [STRATEGIE ...]
Sans argument : toutes les stratégies ayant au moins un walk-forward.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DB = Path(__file__).resolve().parents[3] / "experiments" / "experiments.sqlite3"
VARIANTS = ("base/central", "base/adverse", "base/stress", "profil_BSM/central")


def fmt(summary: dict | None) -> str:
    if not summary or summary.get("expectancy_r") is None:
        return "—"
    ci = summary.get("expectancy_r_ci95_block_bootstrap")
    ci_text = f"[{ci[0]:+.3f} ; {ci[1]:+.3f}]" if ci else "[pas d'IC]"
    return f"{summary['expectancy_r']:+.4f} {ci_text} n={summary.get('trades_closed')}"


def main(strategies: list[str]) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    if not strategies:
        strategies = [r[0] for r in db.execute("SELECT DISTINCT strategy FROM runs WHERE kind='WALK_FORWARD'")]
    for strategy in strategies:
        rows = db.execute("SELECT run_id, created_at, universe, metrics FROM runs WHERE kind='WALK_FORWARD' "
                          "AND strategy=? ORDER BY created_at DESC LIMIT 2", (strategy,)).fetchall()
        if not rows:
            print(f"{strategy} : aucun walk-forward")
            continue
        print(f"\n== {strategy}")
        runs = [(r[0], len(json.loads(r[2])), json.loads(r[3])) for r in rows]
        for run_id, pairs, metrics in runs:
            failed = [c["number"] for c in metrics.get("criteria", []) if not c.get("passed")]
            print(f"  {run_id} ({pairs} paires) verdict {metrics.get('verdict')}, critères en échec {failed}, "
                  f"essais du programme {metrics.get('program_trials', 'non enregistré')}")
        for variant in VARIANTS:
            values = [fmt(m.get("oos", {}).get(variant)) for _, _, m in runs]
            print(f"  {variant:<20} " + "   <- avant : ".join(values))


if __name__ == "__main__":
    main(sys.argv[1:])
