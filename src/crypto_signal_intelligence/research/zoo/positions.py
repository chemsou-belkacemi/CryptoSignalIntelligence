"""Positions d'une règle de forme (a) (docs/ZOO.md § 3 et § 4.1) : AUCUN rendement ici.

Ce module ne lit que des heures, des appartenances et des indicateurs booléens ; il sert au comptage à blanc (nombre
de signaux, de transactions, exposition) comme au calcul des rendements (`measure.py`). Règles :
- décision à la clôture `t` (instant `available_at`), dans la fenêtre de décision de l'étape ;
- **entrée à la clôture suivante** (`t + 1`, une unité plus tard) ; aucune bougie 1 h close à cet instant (trou) :
  signal écarté (« entrée dans un trou », compté) ; entrée après la dernière clôture de l'étape : écartée (comptée) ;
- **une position par paire et par test** : un signal pendant une position est ignoré (compté) ;
- appartenance au top 40 du mois de la décision exigée pour ouvrir ; une position ouverte va jusqu'à sa sortie ;
- **sortie** : condition évaluée à chaque clôture `u ≥ t`, exécutée à la clôture `u + 1` ; ou au plafond `N` (clôture de
  la N-ième bougie après l'entrée) ; ou à la dernière clôture de l'étape. Une sortie qui tombe dans un trou s'exécute
  à la première clôture 1 h disponible ; une paire dont les bougies s'arrêtent avant (retrait de la cote) est vendue à
  sa dernière clôture connue (`COTATION_ARRETEE`) ;
- transaction de durée nulle (entrée et sortie à la même clôture) : écartée (comptée).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ._jit import njit
from .data import Bougies, Etape, Grille
from .spec import Signaux

REGLE, PLAFOND, FIN_ETAPE, COTATION_ARRETEE = 0, 1, 2, 3
RAISONS = ("REGLE", "PLAFOND", "FIN_ETAPE", "COTATION_ARRETEE")
COMPTEURS = ("signaux", "ignores_position_ouverte", "non_membre", "entree_apres_fin", "entree_dans_un_trou",
             "duree_nulle", "transactions")


@dataclass
class Positions:
    symbole: str
    signal: np.ndarray           # indice de la bougie du signal
    entree_h: np.ndarray         # heure de la clôture d'entrée
    sortie_h: np.ndarray         # heure de la clôture de sortie
    raison: np.ndarray
    compteurs: dict[str, int] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.entree_h)

    @classmethod
    def vide(cls, symbole: str) -> Positions:
        empty = np.zeros(0, dtype=np.int64)
        return cls(symbole, empty, empty, empty, np.zeros(0, dtype=np.int8), dict.fromkeys(COMPTEURS, 0))


def voisins(grille: Grille) -> tuple[np.ndarray, np.ndarray]:
    """Pour chaque heure de la grille : indice de la première clôture existante ≥ (ou −1), de la dernière ≤ (ou −1)."""
    exists = grille.existe
    idx = np.arange(len(exists))
    prev = np.where(exists, idx, -1)
    np.maximum.accumulate(prev, out=prev)
    nxt = np.where(exists, idx, len(exists))
    nxt = np.minimum.accumulate(nxt[::-1])[::-1]
    nxt = np.where(nxt == len(exists), -1, nxt)
    return nxt.astype(np.int64), prev.astype(np.int64)


@njit
def _simuler(entree: np.ndarray, sortie: np.ndarray, has_exit: bool, close_h: np.ndarray, avail: np.ndarray,
             member: np.ndarray, start_ns: int, end_ns: int, end_h: int, unit: int, n_max: int, g0: int,
             nxt: np.ndarray, prv: np.ndarray):
    n = len(entree)
    size = len(nxt)
    sig = np.empty(n, dtype=np.int64)
    ins = np.empty(n, dtype=np.int64)
    outs = np.empty(n, dtype=np.int64)
    why = np.empty(n, dtype=np.int8)
    counts = np.zeros(7, dtype=np.int64)
    k = 0
    busy_until = -1
    last_close = g0 + size - 1
    big = np.int64(1) << 60
    for t in range(n):
        if avail[t] < start_ns:
            continue
        if avail[t] > end_ns:
            break
        if not entree[t]:
            continue
        counts[0] += 1
        if close_h[t] < busy_until:
            counts[1] += 1
            continue
        if not member[t]:
            counts[2] += 1
            continue
        te = close_h[t] + unit
        if te > end_h:
            counts[3] += 1
            continue
        if te < g0 or te - g0 >= size or nxt[te - g0] != te - g0:
            counts[4] += 1
            continue
        sched = big
        reason = FIN_ETAPE
        if n_max > 0:
            sched = te + n_max * unit
            reason = PLAFOND
        if has_exit:
            for u in range(t, n):
                tx = close_h[u] + unit
                if tx >= sched or tx > end_h:
                    break
                if sortie[u]:
                    sched = tx
                    reason = REGLE
                    break
        if sched > end_h:
            stop = min(end_h, last_close)
            x = g0 + prv[stop - g0]
            reason = COTATION_ARRETEE if last_close < end_h else FIN_ETAPE
        elif sched > last_close:
            x = last_close
            reason = COTATION_ARRETEE
        else:
            x = g0 + nxt[sched - g0]
        busy_until = x
        if x <= te:
            counts[5] += 1
            continue
        sig[k] = t
        ins[k] = te
        outs[k] = x
        why[k] = reason
        k += 1
    counts[6] = k
    return sig[:k], ins[:k], outs[:k], why[:k], counts


def simuler(symbole: str, signaux: Signaux, bougies: Bougies, grille: Grille, membre: np.ndarray, etape: Etape,
            n_max: int | None, voisinage: tuple[np.ndarray, np.ndarray] | None = None) -> Positions:
    """Positions d'un test sur une paire dans une étape (indices et heures seulement, aucun prix)."""
    if len(bougies) == 0 or len(grille.existe) == 0:
        return Positions.vide(symbole)
    if len(signaux.entree) != len(bougies) or len(membre) != len(bougies):
        raise ValueError("signaux, appartenance et bougies de longueurs différentes")
    nxt, prv = voisinage or voisins(grille)
    exit_flags = signaux.sortie if signaux.sortie is not None else np.zeros(len(bougies), dtype=bool)
    sig, ins, outs, why, counts = _simuler(
        signaux.entree, exit_flags, signaux.sortie is not None, bougies.cloture_h.astype(np.int64),
        bougies.disponible.astype(np.int64), membre, etape.debut_ns, etape.fin_ns, etape.fin_h, int(bougies.unite),
        int(n_max or 0), int(grille.debut_h), nxt, prv)
    return Positions(symbole, sig, ins, outs, why, dict(zip(COMPTEURS, (int(c) for c in counts), strict=True)))


def exposition_jours(positions: list[Positions], etape: Etape) -> np.ndarray:
    """Exposition totale par journée de l'étape (somme des fractions de journée détenues) : comptage à blanc."""
    days = etape.jours()
    out = np.zeros(len(days))
    first = days[0]
    for pos in positions:
        for a, b in zip(pos.entree_h, pos.sortie_h, strict=True):
            d0, d1 = a // 24, (b - 1) // 24
            for d in range(int(d0), int(d1) + 1):
                lo, hi = max(a, 24 * d), min(b, 24 * (d + 1))
                if 0 <= d - first < len(out):
                    out[d - first] += (hi - lo) / 24.0
    return out
