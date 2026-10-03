"""Bougies de 1 SECONDE pour simuler l'exécution avec précision (mission du 2026-10-03, phase 1.5).

Usage limité à l'exécution simulée autour d'un événement (entrée, ordre limite, stop, objectif) : jamais un signal
de direction. Source : l'API publique Binance Spot (`/api/v3/klines`, intervalle `1s`, client en liste blanche),
qui sert aussi le passé ; aucune connexion permanente. Seules des fenêtres sont conservées (par défaut 15 minutes
avant et 2 heures après l'événement), en Parquet compressé, sous `<racine>/data/seconds/<PAIRE>/`.

Une seconde sans transaction n'a pas de bougie chez Binance : le prix n'a pas bougé. Les règles d'exécution :
- achat au marché : ouverture de la première bougie qui commence à l'heure demandée ou après, plus le glissement
  du modèle de coûts (appliqué par l'appelant) ;
- ordre limite d'achat : exécuté seulement si le prix TRAVERSE la limite (plus bas strictement sous la limite),
  au prix limite ; toucher la limite ne suffit pas (prudence) ;
- vente à un objectif : plus haut strictement au-dessus de l'objectif, au prix de l'objectif ;
- stop : plus bas au stop ou dessous ; vente au stop, ou à l'ouverture si la bougie ouvre déjà dessous (trou).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ..config import Settings
from .http import PublicHttpClient

PAGE = 1000
BEFORE, AFTER = pd.Timedelta(minutes=15), pd.Timedelta(hours=2)
COLUMNS = ("open_time", "open", "high", "low", "close", "volume")


def seconds_dir(settings: Settings) -> Path:
    return settings.root / "data" / "seconds"


def fetch(client: PublicHttpClient, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Bougies 1 s de [start, end[ (heures d'ouverture), paginées par 1 000."""
    rows: list[list] = []
    cursor = int(start.timestamp() * 1000)
    stop = int(end.timestamp() * 1000)
    while cursor < stop:
        page = client.get_json("/api/v3/klines", {"symbol": symbol, "interval": "1s", "startTime": cursor,
                                                  "endTime": stop - 1, "limit": PAGE})
        if not isinstance(page, list) or not page:
            break
        rows.extend(page)
        cursor = int(page[-1][0]) + 1000
        if len(page) < PAGE:
            break
    return to_frame(rows)


def to_frame(rows: list[list]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=list(COLUMNS))
    frame = pd.DataFrame([r[:6] for r in rows], columns=list(COLUMNS))
    frame["open_time"] = pd.to_datetime(frame["open_time"].astype("int64"), unit="ms", utc=True)
    for column in COLUMNS[1:]:
        frame[column] = frame[column].astype(float)
    return frame.drop_duplicates("open_time").sort_values("open_time").reset_index(drop=True)


def window(settings: Settings, symbol: str, event: pd.Timestamp, *, before: pd.Timedelta = BEFORE,
           after: pd.Timedelta = AFTER, client: PublicHttpClient | None = None) -> pd.DataFrame:
    """Fenêtre 1 s autour d'un événement, lue dans le cache si elle y est complète, sinon téléchargée puis gardée
    (une fenêtre pas encore entièrement écoulée n'est pas gardée)."""
    event = pd.Timestamp(event).tz_convert("UTC") if pd.Timestamp(event).tzinfo else pd.Timestamp(event, tz="UTC")
    start, end = (event - before).floor("s"), (event + after).ceil("s")
    path = seconds_dir(settings) / symbol / f"{start:%Y%m%dT%H%M%S}_{end:%Y%m%dT%H%M%S}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    client = client or PublicHttpClient.rest(settings.data.rest_base_url)
    frame = fetch(client, symbol, start, end)
    if end <= pd.Timestamp.now(tz="UTC") and not frame.empty:
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(path, index=False, compression="zstd")
    return frame


@dataclass(frozen=True)
class Fill:
    at: pd.Timestamp
    price: float


def market_fill(frame: pd.DataFrame, at: pd.Timestamp) -> Fill | None:
    """Ouverture de la première bougie 1 s qui commence à `at` ou après (glissement ajouté par l'appelant)."""
    after = frame[frame["open_time"] >= at]
    if after.empty:
        return None
    row = after.iloc[0]
    return Fill(row["open_time"], float(row["open"]))


def limit_buy_fill(frame: pd.DataFrame, limit: float, start: pd.Timestamp, end: pd.Timestamp) -> Fill | None:
    """Achat limite exécuté au prix limite seulement si un plus bas passe STRICTEMENT sous la limite dans [start, end[ ;
    si une bougie OUVRE déjà sous la limite, l'exécution se fait à cette ouverture (meilleure que la limite)."""
    part = frame[(frame["open_time"] >= start) & (frame["open_time"] < end)]
    hit = part[part["low"] < limit]
    if hit.empty:
        return None
    row = hit.iloc[0]
    return Fill(row["open_time"], min(limit, float(row["open"])))


def target_fill(frame: pd.DataFrame, target: float, start: pd.Timestamp, end: pd.Timestamp | None = None) -> Fill | None:
    """Vente à un objectif : plus haut STRICTEMENT au-dessus de l'objectif ; au prix de l'objectif, ou à l'ouverture
    si la bougie ouvre déjà au-dessus."""
    part = frame[frame["open_time"] >= start] if end is None else frame[(frame["open_time"] >= start) & (frame["open_time"] < end)]
    hit = part[part["high"] > target]
    if hit.empty:
        return None
    row = hit.iloc[0]
    return Fill(row["open_time"], max(target, float(row["open"])))


def stop_fill(frame: pd.DataFrame, stop: float, start: pd.Timestamp, end: pd.Timestamp | None = None) -> Fill | None:
    """Stop : plus bas au stop ou dessous ; vente au stop, ou à l'ouverture si la bougie ouvre déjà dessous."""
    part = frame[frame["open_time"] >= start] if end is None else frame[(frame["open_time"] >= start) & (frame["open_time"] < end)]
    hit = part[part["low"] <= stop]
    if hit.empty:
        return None
    row = hit.iloc[0]
    return Fill(row["open_time"], min(stop, float(row["open"])))
