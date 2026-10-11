"""Événements (EV02), forme (a), 1 j : annonces de cotation spot de Binance (`annonces_binance`, catégorie
`COTATION_SPOT`, connues à la publication + 10 min).

Règle du registre : signal à la clôture de la **1re journée complète de cotation** d'une paire annoncée, l'annonce
étant connue AVANT la clôture de cette journée ; le moteur achète à la clôture suivante (catalogue : « achat de la paire
USDT annoncée à la clôture suivant sa 1re journée complète de cotation ») ; aucune sortie, tenue 30 jours.

Lectures déclarées :
- 1re journée complète = 1re bougie journalière de la paire (le moteur n'agrège que des journées complètes de 24
  bougies 1 h) ;
- « annonce connue avant la clôture » : `available_at` STRICTEMENT antérieur à l'instant de décision de cette
  journée (même règle que `data.joindre`) ;
- correspondance annonce → paire : base de la paire (symbole sans `USDT`) parmi les tickers entre parenthèses du titre
  (`tickers`) ; sans ticker explicite, parmi les mots en capitales du titre (même règle que le contrôle d'annonces du
  comptage, `reel.controle_annonces`) ; aucune fenêtre de temps n'est ajoutée entre l'annonce et la cotation (aucune
  n'est écrite au protocole) ;
- table synthétique du contrôle H0 (`synth.annonces_ev02` : colonnes `symbol`, `available_at`) : correspondance par
  symbole.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ..data import Bougies
from ..spec import Contexte, Signaux

IMPLEMENTEES: frozenset[str] = frozenset({"cotation_binance"})
DEPENDENT_ETAPE: frozenset[str] = frozenset()

ANNONCES = "annonces_binance"
_MOT = re.compile(r"\b[A-Z0-9]{2,12}\b")


def annonces_de(annonces: pd.DataFrame | None, symbole: str, categorie: str) -> np.ndarray:
    """Instants `available_at` (ns, triés) des annonces de la catégorie qui concernent la paire."""
    if annonces is None or len(annonces) == 0:
        return np.zeros(0, dtype=np.int64)
    if "symbol" in annonces.columns:                       # table synthétique du contrôle H0
        part = annonces[annonces["symbol"].astype(str) == symbole]
    else:
        base = symbole.removesuffix("USDT")
        part = annonces[annonces["category"] == categorie] if "category" in annonces else annonces.iloc[:0]

        def concerne(row) -> bool:
            explicit = [t for t in str(row.get("tickers") or "").split(",") if t]
            return base in explicit if explicit else base in _MOT.findall(str(row.get("detail") or ""))

        part = part[[concerne(row) for _, row in part.iterrows()]] if len(part) else part
    if len(part) == 0:
        return np.zeros(0, dtype=np.int64)
    return np.sort(pd.to_datetime(part["available_at"], utc=True).dt.as_unit("ns").astype("int64").to_numpy())


def cotation_binance(frame: Bougies, ctx: Contexte) -> Signaux:
    """EV02 : 1re journée complète de cotation d'une paire annoncée (annonce connue avant sa clôture) ; tenue 30 j."""
    n = len(frame)
    entry = np.zeros(n, dtype=bool)
    if n == 0:
        return Signaux(entry, None)
    known = annonces_de(ctx.evenements.get(ANNONCES), ctx.symbole, ctx.params["categorie"])
    entry[0] = bool(len(known)) and bool(known[0] < frame.disponible[0])
    return Signaux(entry, None)
