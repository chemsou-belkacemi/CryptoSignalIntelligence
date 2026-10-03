"""Magasin des données de contexte : un fichier Parquet par série, au format long
(`key`, `date`, `field`, `value`, `kind`, `source`, `retrieved_at`).

Règle d'écriture : une ligne `RELEVE` déjà présente n'est JAMAIS réécrite (c'est la valeur vue ce jour-là) ; une ligne
`HISTORIQUE` est remplacée par le dernier téléchargement (valeurs telles que publiées aujourd'hui)."""
from __future__ import annotations

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import pandas as pd

from ..config import Settings

HISTORY, OBSERVED = "HISTORIQUE", "RELEVE"
COLUMNS = ["key", "date", "field", "value", "kind", "source", "retrieved_at"]
IDENTITY = ["key", "date", "field", "kind"]


def context_dir(settings: Settings) -> Path:
    return settings.data_dir / "context"


def path_for(settings: Settings, series: str) -> Path:
    if not series.replace("_", "").isalnum():
        raise ValueError(f"nom de série invalide : {series}")
    return context_dir(settings) / f"{series}.parquet"


@contextmanager
def locked(settings: Settings) -> Iterator[None]:
    """Un seul écrivain à la fois (relevé quotidien de la surveillance et téléchargement de l'historique)."""
    folder = context_dir(settings)
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / ".lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def rows(records: list[dict], *, kind: str, source: str, now: datetime) -> pd.DataFrame:
    """Lignes du magasin à partir de dictionnaires {key, date, field, value} ; valeurs non finies écartées."""
    frame = pd.DataFrame(records, columns=["key", "date", "field", "value"])
    if frame.empty:
        return pd.DataFrame(columns=COLUMNS)
    frame["date"] = pd.to_datetime(frame["date"], utc=True)
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame[frame["value"].notna() & (frame["value"].abs() != float("inf"))]
    frame = frame.assign(kind=kind, source=source, retrieved_at=pd.Timestamp(now))
    return frame[COLUMNS].reset_index(drop=True)


def load(settings: Settings, series: str) -> pd.DataFrame:
    path = path_for(settings, series)
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_parquet(path)


def upsert(settings: Settings, series: str, new: pd.DataFrame) -> int:
    """Ajoute des lignes ; renvoie le nombre de lignes nouvelles ou remplacées."""
    if new.empty:
        return 0
    with locked(settings):
        return _upsert(settings, series, new)


def _upsert(settings: Settings, series: str, new: pd.DataFrame) -> int:
    path = path_for(settings, series)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = load(settings, series)
    observed_old = old[old["kind"] == OBSERVED]
    history_old = old[old["kind"] == HISTORY]
    observed_new = new[new["kind"] == OBSERVED]
    history_new = new[new["kind"] == HISTORY]
    # RELEVE : la première valeur vue gagne. HISTORIQUE : le dernier téléchargement gagne.
    observed = pd.concat([observed_old, observed_new], ignore_index=True).drop_duplicates(IDENTITY, keep="first")
    history = pd.concat([history_old, history_new], ignore_index=True).drop_duplicates(IDENTITY, keep="last")
    merged = pd.concat([observed, history], ignore_index=True).sort_values(["key", "field", "date", "kind"])
    changed = (len(observed) - len(observed_old)) + len(history_new)
    tmp = path.with_suffix(".tmp")
    merged.reset_index(drop=True).to_parquet(tmp, index=False)
    tmp.replace(path)
    return max(changed, 0)


def series_names(settings: Settings) -> list[str]:
    folder = context_dir(settings)
    return sorted(p.stem for p in folder.glob("*.parquet")) if folder.exists() else []


def summary(settings: Settings) -> list[dict]:
    """Par série et par sorte : nombre de lignes, de clés, première et dernière date."""
    out = []
    for series in series_names(settings):
        frame = load(settings, series)
        for kind, part in frame.groupby("kind"):
            out.append({"series": series, "kind": kind, "rows": int(len(part)), "keys": int(part["key"].nunique()),
                        "first": str(part["date"].min())[:10], "last": str(part["date"].max())[:10]})
    return out
