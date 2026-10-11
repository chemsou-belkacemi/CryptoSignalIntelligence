"""Calendrier (CA09, CA10, CA11 en 1 h ; CA13 en 1 j), forme (a). Calendriers DÉCLARÉS CONNUS À L'AVANCE (docs/ZOO.md
§ 2 : FOMC, CPI et NFP publiés l'année précédente, hypothèse déclarée ; phases lunaires calculables d'avance) : la
stratégie lit `ctx.evenements` sans filtre d'`available_at`, et ne lit jamais l'issue d'un événement.

**CA09 à CA11** (texte exact du registre) : achat de chaque membre à la clôture de la DERNIÈRE bougie 1 h qui se
termine au plus tard 24 h avant la publication, vente à la clôture de la DERNIÈRE bougie 1 h qui se termine au plus
tard à l'heure de publication.
- Heure de publication `P` : date de l'événement (clé du calendrier, jour de New York) + heure du registre (FOMC 14:00,
  CPI et NFP 08:30), fuseau `America/New_York` converti en UTC avec `zoneinfo` (heure d'été comprise) ; FOMC : réunions
  programmées seulement (`scheduled`).
- Entrée voulue à `E` = ⌊P − 24 h⌋ (heure entière) ; le moteur entre à la clôture qui SUIT la bougie du signal
  (README, « calendriers connus d'avance ») : le signal est marqué sur la bougie qui se clôt à `E` − 1 h. Si cette
  bougie manque, aucun signal (déclaré) ; si la bougie de `E` manque, le moteur écarte l'entrée (« entrée dans un
  trou »).
- Sortie voulue à `X` = ⌊P⌋ : la condition de sortie est marquée sur la PREMIÈRE bougie qui se clôt à `X` − 1 h ou
  après (la sortie suit d'une heure : `X` quand la bougie existe ; dans un trou, à la première clôture disponible,
  règle du moteur). Ce choix reste causal : il ne dépend que des bougies déjà closes.
- 14:00 New York = 18:00 UTC (heure d'été) ou 19:00 UTC (hiver) : entrée à 18:00 (19:00) la veille, sortie à 18:00
  (19:00). 08:30 New York = 12:30 ou 13:30 UTC : entrée à 12:00 (13:00) la veille, sortie à 12:00 (13:00).

**CA13** (1 j) : signal d'entrée à la clôture de la journée UTC qui CONTIENT la nouvelle lune (1re clôture journalière
après elle ; journée absente : aucun signal) ; condition de sortie à la 1re clôture journalière après chaque pleine
lune ; le moteur exécute l'une et l'autre à la clôture suivante (aucune exception déclarée pour CA13).
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..data import HOUR_NS, Bougies
from ..spec import Contexte, Signaux

IMPLEMENTEES: frozenset[str] = frozenset({"cycle_lunaire", "veille_publication"})
DEPENDENT_ETAPE: frozenset[str] = frozenset()

NEW_YORK = ZoneInfo("America/New_York")
MACRO, CRYPTO = "calendrier_macro", "calendrier_crypto"


def publications_ns(evenements: pd.DataFrame, evenement: str, heure: str) -> np.ndarray:
    """Instants de publication (ns UTC, triés) : jour de l'événement à `heure` de New York (zoneinfo)."""
    if evenements is None or len(evenements) == 0:
        return np.zeros(0, dtype=np.int64)
    part = evenements[evenements["event"] == evenement]
    if "scheduled" in part:
        part = part[part["scheduled"].astype(bool)]
    hh, mm = (int(x) for x in heure.split(":"))
    out = []
    for key in part["key"].astype(str):
        local = datetime.combine(date.fromisoformat(key[:10]), time(hh, mm), tzinfo=NEW_YORK)
        out.append(int(pd.Timestamp(local).tz_convert("UTC").value))
    return np.unique(np.asarray(out, dtype=np.int64))


def veille_publication(frame: Bougies, ctx: Contexte) -> Signaux:
    """CA09, CA10, CA11 (1 h) : achat à la clôture de la dernière bougie finie au plus tard 24 h avant la publication,
    vente à la clôture de la dernière bougie finie au plus tard à la publication."""
    p = ctx.params
    n = len(frame)
    entry, leave = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    published = publications_ns(ctx.evenements.get(MACRO), p["evenement"], p["heure"])
    if n == 0 or len(published) == 0:
        return Signaux(entry, leave)
    unit = frame.unite
    close_h = frame.cloture_h.astype(np.int64)
    e_h = (published - 24 * HOUR_NS) // HOUR_NS       # ⌊P − 24 h⌋, heure de l'entrée voulue
    x_h = published // HOUR_NS                         # ⌊P⌋, heure de la sortie voulue
    entry[np.isin(close_h, e_h - unit)] = True
    pos = np.searchsorted(close_h, x_h - unit, side="left")
    leave[pos[pos < n]] = True
    return Signaux(entry, leave)


def cycle_lunaire(frame: Bougies, ctx: Contexte) -> Signaux:
    """CA13 (1 j) : 1re clôture journalière après une nouvelle lune ; sortie : 1re clôture après la pleine lune."""
    n = len(frame)
    entry, leave = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
    events = ctx.evenements.get(CRYPTO)
    if n == 0 or events is None or len(events) == 0:
        return Signaux(entry, leave)
    times = pd.to_datetime(events["event_time"], utc=True).dt.as_unit("ns").astype("int64").to_numpy()
    new = np.sort(times[(events["event"] == "NOUVELLE_LUNE").to_numpy()])
    full = np.sort(times[(events["event"] == "PLEINE_LUNE").to_numpy()])
    open_ns = frame.ouverture_h.astype(np.int64) * HOUR_NS
    close_ns = frame.cloture_h.astype(np.int64) * HOUR_NS
    pos = np.searchsorted(close_ns, new, side="right")      # 1re clôture strictement après la nouvelle lune
    ok = pos < n
    hit = pos[ok][open_ns[pos[ok]] <= new[ok]]              # … de la journée qui la contient
    entry[hit] = True
    pos = np.searchsorted(close_ns, full, side="right")
    leave[pos[pos < n]] = True
    return Signaux(entry, leave)
