"""CVD, profil de volume (POC, zone de valeur à 70 %) et VWAP ancré (docs/INDICATEURS.md § 7 et 8)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

PROFILE_BINS = 50
VALUE_AREA = 0.70


def volume_delta(volume, taker_buy) -> np.ndarray:
    """delta = achats au marché − ventes au marché = 2 · TB − V."""
    return 2 * np.asarray(taker_buy, dtype=float) - np.asarray(volume, dtype=float)


def cvd(volume, taker_buy, anchor: int = 0) -> np.ndarray:
    """Somme des delta depuis `anchor` (NaN avant l'ancre)."""
    delta = volume_delta(volume, taker_buy)
    out = np.full(len(delta), np.nan)
    out[anchor:] = np.cumsum(delta[anchor:])
    return out


@dataclass(frozen=True)
class Profile:
    edges: np.ndarray       # bornes des tranches (bins + 1)
    volume: np.ndarray      # volume par tranche
    poc: tuple[float, float]
    val: float              # bas de la zone de valeur
    vah: float              # haut de la zone de valeur


def volume_profile(high, low, volume, *, bins: int = PROFILE_BINS, area: float = VALUE_AREA) -> Profile:
    """Volume de chaque bougie réparti uniformément sur les tranches couvertes par [L ; H] (au prorata)."""
    h, lo, v = (np.asarray(a, dtype=float) for a in (high, low, volume))
    edges = np.linspace(lo.min(), h.max(), bins + 1)
    profile = np.zeros(bins)
    for bar_low, bar_high, bar_volume in zip(lo, h, v, strict=True):
        if bar_high <= bar_low:                                      # bougie sans étendue : tout dans sa tranche
            k = min(bins - 1, int(np.searchsorted(edges, bar_low, side="right") - 1))
            profile[max(k, 0)] += bar_volume
            continue
        overlap = np.clip(np.minimum(edges[1:], bar_high) - np.maximum(edges[:-1], bar_low), 0, None)
        profile += bar_volume * overlap / (bar_high - bar_low)
    tol = 1e-9 * max(float(profile.max()), 1.0)                      # égalités à l'arrondi près
    poc = int(np.nonzero(profile >= profile.max() - tol)[0][0])      # égalité : la plus basse (premier indice)
    low_i = high_i = poc
    total, covered = profile.sum(), profile[poc]
    while covered < area * total - 1e-12 and (low_i > 0 or high_i < bins - 1):
        above = profile[high_i + 1] if high_i < bins - 1 else -1.0
        below = profile[low_i - 1] if low_i > 0 else -1.0
        if above >= below - tol:                                     # égalité : celle du dessus
            high_i += 1
            covered += above
        else:
            low_i -= 1
            covered += below
    return Profile(edges, profile, (float(edges[poc]), float(edges[poc + 1])), float(edges[low_i]), float(edges[high_i + 1]))


def anchored_vwap(high, low, close, volume, anchor: int) -> np.ndarray:
    """VWAP depuis la bougie `anchor` (incluse), prix typique (H + L + C) / 3 ; NaN avant l'ancre."""
    h, lo, c, v = (np.asarray(a, dtype=float) for a in (high, low, close, volume))
    typical = (h + lo + c) / 3
    out = np.full(len(c), np.nan)
    cum_v = np.cumsum(v[anchor:])
    with np.errstate(divide="ignore", invalid="ignore"):
        out[anchor:] = np.cumsum(typical[anchor:] * v[anchor:]) / cum_v
    return out
