"""Famille Volume et flux (VF) : 21 tests de forme (a) (docs/ZOO.md § 12.1, docs/ZOO_CATALOGUE.md § 6).

Paramètres standard du recensement, lus dans `ctx.params` (registre `spec.py`) ; fonctions PURES, bougies closes, la
valeur en `t` ne lit que les bougies `≤ t` (vérifié par `causality.py`).

Conventions déclarées :
- **V = volume en devise de cotation (USDT)**, seul volume des bougies du zoo (docs/ZOO.md § 2, docs/INDICATEURS.md
  § 10.5) ; les définitions publiées emploient le volume de base : l'écart est une pondération par le prix, de second
  ordre sur les fenêtres courtes (déclaré) ;
- **OBV** (Granville) : `OBV_0 = 0`, `OBV_t = OBV_{t−1} + signe(C_t − C_{t−1}) × V_t` ; **ligne A/D** (Chaikin) :
  `CLV = ((C − L) − (H − C)) / (H − L)` (0 si H = L), cumul de `CLV × V` depuis la 1re bougie ; ces deux cumuls
  partent de la cotation, mais leur écart à leur propre moyenne (VF01, VF02) et l'oscillateur de Chaikin (VF03)
  ne dépendent pas du point de départ ;
- **oscillateur de Chaikin** : EMA 3 − EMA 10 de la ligne A/D (EMA amorcées par la SMA, comme TA-Lib) ;
- **MFI** (Quong et Soudack) : TP = (H + L + C) / 3, flux = TP × V, positif si TP_t > TP_{t−1}, négatif si
  TP_t < TP_{t−1} ; sommes sur les 14 dernières bougies ; MFI = 100 − 100 / (1 + positif / négatif), 100 si le flux
  négatif est nul ;
- **choc de volume** : V_t > 5 × moyenne des 20 bougies PRÉCÉDENTES (`t` exclue, INDICATEURS.md § 10.5) et C > O ;
- **Elder Force Index** : EMA 13 de (C_t − C_{t−1}) × V_t ;
- **Klinger** (1997, formule MetaStock) : tendance = +1 si H + L + C > celle de la veille, sinon −1 ; dm = H − L ;
  cm = cm_{t−1} + dm si la tendance continue, sinon dm_{t−1} + dm (1re valeur : dm_0 + dm_1) ;
  VF = V × |2 × (dm / cm − 1)| × tendance × 100 (0 si cm = 0) ; KVO = EMA 34 − EMA 55 de VF, signal = EMA 13 de KVO.
"""
from __future__ import annotations

import numpy as np

from .. import indicators as ind
from .._jit import njit
from ..data import Bougies
from ..spec import Contexte, Signaux

IMPLEMENTEES: frozenset[str] = frozenset({
    "obv", "ligne_ad", "chaikin", "mfi", "choc_volume", "force_elder", "klinger"})
DEPENDENT_ETAPE: frozenset[str] = frozenset()


def _zeros(n: int) -> np.ndarray:
    return np.zeros(n, dtype=np.float64)


def _le(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(invalid="ignore"):
        return np.where(np.isnan(a) | np.isnan(b), False, a <= b)


def obv_valeurs(frame: Bougies) -> np.ndarray:
    """On-balance volume, 0 à la première bougie."""
    step = np.sign(np.diff(frame.cloture, prepend=frame.cloture[:1])) * frame.volume
    return np.cumsum(step)


def ligne_ad_valeurs(frame: Bougies) -> np.ndarray:
    """Ligne d'accumulation / distribution de Chaikin (cumul de CLV × V)."""
    span = frame.haut - frame.bas
    with np.errstate(invalid="ignore", divide="ignore"):
        clv = np.where(span > 0, ((frame.cloture - frame.bas) - (frame.haut - frame.cloture)) / span, 0.0)
    return np.cumsum(clv * frame.volume)


def _croisement_moyenne(values: np.ndarray, n: int) -> Signaux:
    average = ind.sma(values, n)
    return Signaux(ind.croise_dessus(values, average), ind.croise_dessous(values, average))


def obv(frame: Bougies, ctx: Contexte) -> Signaux:
    """VF01 : OBV croise au-dessus de sa SMA 20 ; sortie au croisement inverse."""
    return _croisement_moyenne(obv_valeurs(frame), int(ctx.params["sma"]))


def ligne_ad(frame: Bougies, ctx: Contexte) -> Signaux:
    """VF02 : ligne A/D croise au-dessus de sa SMA 20 ; sortie au croisement inverse."""
    return _croisement_moyenne(ligne_ad_valeurs(frame), int(ctx.params["sma"]))


def chaikin_valeurs(frame: Bougies, fast: int, slow: int) -> np.ndarray:
    line = ligne_ad_valeurs(frame)
    return ind.ema(line, fast) - ind.ema(line, slow)


def chaikin(frame: Bougies, ctx: Contexte) -> Signaux:
    """VF03 (état) : oscillateur de Chaikin (3, 10) > 0 ; sortie ≤ 0."""
    value = chaikin_valeurs(frame, int(ctx.params["rapide"]), int(ctx.params["lente"]))
    zero = _zeros(len(frame))
    return Signaux(ind.superieur(value, zero), _le(value, zero))


def mfi_valeurs(frame: Bougies, n: int) -> np.ndarray:
    """Money Flow Index (première valeur en `n`)."""
    tp = (frame.haut + frame.bas + frame.cloture) / 3.0
    flow = tp * frame.volume
    prev = ind.decale(tp, 1)
    with np.errstate(invalid="ignore"):
        pos = np.where(tp > prev, flow, 0.0)
        neg = np.where(tp < prev, flow, 0.0)
    pos[np.isnan(prev)] = np.nan
    neg[np.isnan(prev)] = np.nan
    sp, sn = ind.sma(pos, n) * n, ind.sma(neg, n) * n
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(sn == 0, 100.0, 100.0 - 100.0 / (1.0 + sp / sn))
    return np.where(np.isnan(sp) | np.isnan(sn), np.nan, out)


def mfi(frame: Bougies, ctx: Contexte) -> Signaux:
    """VF04 : MFI 14 croise au-dessus de 20 ; sortie MFI > 80 (plafond 20)."""
    p = ctx.params
    value = mfi_valeurs(frame, int(p["n"]))
    n = len(frame)
    return Signaux(ind.croise_dessus(value, np.full(n, float(p["bas"]))),
                   ind.superieur(value, np.full(n, float(p["haut"]))))


def choc_volume(frame: Bougies, ctx: Contexte) -> Signaux:
    """VF05 : volume > 5 × moyenne des 20 bougies précédentes et clôture > ouverture ; aucune sortie, tenue de 10."""
    p = ctx.params
    previous = ind.decale(ind.sma(frame.volume, int(p["n"])), 1)
    entry = ind.superieur(frame.volume, float(p["multiple"]) * previous) & ind.superieur(frame.cloture,
                                                                                         frame.ouverture)
    return Signaux(entry, None)


def force_elder_valeurs(frame: Bougies, n: int) -> np.ndarray:
    return ind.ema((frame.cloture - ind.decale(frame.cloture, 1)) * frame.volume, n)


def force_elder(frame: Bougies, ctx: Contexte) -> Signaux:
    """VF16 (état) : Elder Force Index (EMA 13) > 0 ; sortie ≤ 0."""
    value = force_elder_valeurs(frame, int(ctx.params["ema"]))
    zero = _zeros(len(frame))
    return Signaux(ind.superieur(value, zero), _le(value, zero))


@njit
def _force_volume(high: np.ndarray, low: np.ndarray, close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    n = len(close)
    out = np.full(n, np.nan)
    if n < 2:
        return out
    trend_prev = 0
    cm = 0.0
    for t in range(1, n):
        hlc, hlc_prev = high[t] + low[t] + close[t], high[t - 1] + low[t - 1] + close[t - 1]
        trend = 1 if hlc > hlc_prev else -1
        dm = high[t] - low[t]
        cm = cm + dm if t > 1 and trend == trend_prev else (high[t - 1] - low[t - 1]) + dm
        out[t] = 0.0 if cm == 0.0 else volume[t] * abs(2.0 * (dm / cm - 1.0)) * trend * 100.0
        trend_prev = trend
    return out


def klinger_valeurs(frame: Bougies, fast: int, slow: int, signal: int) -> tuple[np.ndarray, np.ndarray]:
    """(KVO, signal) de Klinger."""
    force = _force_volume(frame.haut.astype(np.float64), frame.bas.astype(np.float64),
                          frame.cloture.astype(np.float64), frame.volume.astype(np.float64))
    kvo = ind.ema(force, fast) - ind.ema(force, slow)
    return kvo, ind.ema(kvo, signal)


def klinger(frame: Bougies, ctx: Contexte) -> Signaux:
    """VF17 : Klinger (34, 55) croise au-dessus de son signal 13 ; sortie au croisement inverse."""
    p = ctx.params
    kvo, signal = klinger_valeurs(frame, int(p["rapide"]), int(p["lente"]), int(p["signal"]))
    return Signaux(ind.croise_dessus(kvo, signal), ind.croise_dessous(kvo, signal))
