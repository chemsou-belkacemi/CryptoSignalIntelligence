"""Outils des tests du zoo : paires et univers SYNTHÉTIQUES construits à la main (aucune donnée réelle)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_signal_intelligence.research.zoo.data import (
    HOUR_NS,
    LATENCE_NS,
    Bougies,
    Etape,
    Paire,
    Univers,
    mois_de,
)


def heure(text: str) -> int:
    return int(pd.Timestamp(text, tz="UTC").value // HOUR_NS)


def paire(closes, start: str = "2019-01-01", symbole: str = "AAAUSDT", manquantes=(), mecha: float = 0.001,
          volume: float = 1e6) -> Paire:
    """Bougies 1 h : ouverture = clôture précédente, mèches de ± `mecha`, heures `manquantes` retirées."""
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    open_h = heure(start) + np.arange(n, dtype=np.int64)
    opens = np.r_[closes[0], closes[:-1]]
    keep = np.ones(n, dtype=bool)
    keep[list(manquantes)] = False
    vol = np.full(n, volume)
    bars = Bougies(1, open_h[keep], (open_h[keep] + 1) * HOUR_NS + LATENCE_NS, opens[keep],
                   np.maximum(opens, closes)[keep] * (1 + mecha), np.minimum(opens, closes)[keep] * (1 - mecha),
                   closes[keep], vol[keep], vol[keep] / 2, vol[keep] / 100)
    return Paire(symbole, bars)


def univers_toujours(symboles, debut: str = "2017-01-01", fin: str = "2026-01-01") -> Univers:
    months = pd.date_range(debut, fin, freq="MS", tz="UTC")
    ids = mois_de(months.as_unit("ns").asi8)
    return Univers({int(m): frozenset(symboles) for m in ids})


def univers_mois(table: dict[str, list[str]]) -> Univers:
    """{« AAAA-MM » : paires}."""
    out = {}
    for month, symbols in table.items():
        out[int(mois_de(np.array([pd.Timestamp(month + "-01", tz="UTC").value]))[0])] = frozenset(symbols)
    return Univers(out)


def etape(debut: str, fin_exclusive: str, nom: str = "test") -> Etape:
    start = pd.Timestamp(debut, tz="UTC")
    end = pd.Timestamp(fin_exclusive, tz="UTC") - pd.Timedelta(seconds=1)
    years = tuple(str(y) for y in range(start.year, end.year + 1))
    return Etape(nom, start, end, years)
