"""Marché synthétique du contrôle sous H0 (docs/ZOO.md § 8.1 et § 8.2). Paramètres fixés A PRIORI par le protocole,
aucune estimation sur données réelles.

- 100 paires, bougies 1 h du 2017-08-17 au 2025-06-30 : BTC et ETH dès le début, 98 altcoins cotées à des dates
  tirées (10 avant 2018, les autres uniformément de 2018-01 à 2024-12) ;
- rendements simples martingales `r = σ(i, t) s(h) z(i, t)` : facteur commun `z = λ u + √(1 − λ²) e` (BTC λ = 1,
  ETH 0,85, altcoins U[0,55 ; 0,85]), `u` et `e` Student à 4 degrés de variance 1, TRONQUÉS symétriquement à
  ± min(20 ; 0,9 / (σ s)) (moyenne nulle, |r| ≤ 0,9, prix > 0) ; σ : GJR-GARCH(1, 1) (α 0,04, γ 0,04 sur les baisses,
  β 0,93) de volatilité horaire moyenne BTC 0,6 %, ETH 0,75 %, altcoins log-uniforme [0,8 % ; 1,5 %] ; s(h) : profil
  heure × jour de moyenne 1 ;
- bougies : ouverture = clôture précédente, mèches 0,5 σ s |w| ; volume lié à l'ampleur (jamais au sens futur) ; part
  acheteuse liée au sens de la même bougie ; retraits de la cote décidés sur le passé seul ;
- H0-a : sans dérive ; H0-b : dérive constante (BTC +0,10 %/jour, autres +0,20 %/jour) ; H0-c : deux régimes communs
  (durée moyenne 180 jours ; haussier +0,25 %/jour, BTC +0,15 %, volatilité × 0,8 ; baissier −0,25 %/jour, BTC
  −0,15 %, volatilité × 1,25). H0-a et H0-b partagent les mêmes tirages (mêmes répliques).

Graines : `SeedSequence(20261012, spawn_key=(réplique, …))`, une sous-graine par réplique et par paire : chaque paire
se régénère à l'identique, seule, ce qui garde la mémoire basse (les bougies 1 h ne sont jamais toutes en mémoire).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ._jit import njit
from .data import DAY_H, HOUR_NS, LATENCE_NS, Bougies, Paire, Univers, agreger

GRAINE = 20261012
DEBUT = pd.Timestamp("2017-08-17", tz="UTC")
FIN = pd.Timestamp("2025-07-01", tz="UTC")           # dernière clôture (exclusive pour les ouvertures)
N_PAIRES = 100
ALPHA, GAMMA, BETA = 0.04, 0.04, 0.93
VOL_BTC, VOL_ETH, VOL_ALT = 0.006, 0.0075, (0.008, 0.015)
LAMBDA_ETH, LAMBDA_ALT = 0.85, (0.55, 0.85)
T_DEGRES = 4
DERIVE_H0B: dict[str, float] = {"BTCUSDT": 0.0010, "autres": 0.0020}                   # par jour
#: Régimes de H0-c : durée moyenne d'un état (jours) ; (dérive par jour des altcoins, de BTC, multiplicateur de volatilité)
REGIME_DUREE_JOURS = 180
REGIME_HAUSSIER: tuple[float, float, float] = (0.0025, 0.0015, 0.8)
REGIME_BAISSIER: tuple[float, float, float] = (-0.0025, -0.0015, 1.25)
RETRAIT: dict[str, float] = {"jours": 90, "seuil": -0.75, "probabilite": 0.3}
VOLUME: dict[str, float] = {"phi": 0.995, "innovation": 0.05, "niveau_ecart": 1.0, "facteur_sigma": 0.5, "base": 2.0e5}
MARCHES = ("H0a", "H0b", "H0c")


def heure(ts: pd.Timestamp) -> int:
    return int(pd.Timestamp(ts).value // HOUR_NS)


DEBUT_H, FIN_H = heure(DEBUT), heure(FIN)


def symbole(j: int) -> str:
    return "BTCUSDT" if j == 0 else "ETHUSDT" if j == 1 else f"S{j - 1:03d}USDT"


SYMBOLES = tuple(symbole(j) for j in range(N_PAIRES))


def profil_horaire(heures: np.ndarray) -> np.ndarray:
    """s(h) déterministe, moyenne 1 sur la semaine : plus agité vers 14:00 UTC les jours ouvrés, plus calme le
    week-end (× 0,7)."""
    hour_of_day = np.asarray(heures) % 24
    weekday = ((np.asarray(heures) // 24) + 3) % 7
    shape = 1.0 + 0.35 * np.exp(-((hour_of_day - 14.0) ** 2) / (2 * 3.0 ** 2))
    shape = shape * np.where(weekday >= 5, 0.7, 1.0)
    week = np.arange(168)
    ref = (1.0 + 0.35 * np.exp(-((week % 24 - 14.0) ** 2) / 18.0)) * np.where((week // 24 + 3) % 7 >= 5, 0.7, 1.0)
    return shape / ref.mean()


def _student(rng: np.random.Generator, size: int) -> np.ndarray:
    """Student à 4 degrés de variance 1."""
    return rng.standard_t(T_DEGRES, size=size) / np.sqrt(T_DEGRES / (T_DEGRES - 2.0))


def graine(replique: int, *cle: int) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence(GRAINE, spawn_key=(int(replique), *cle)))


@dataclass
class Commun:
    """Tirages communs d'une réplique : choc de marché `u`, profil, régimes (H0-c)."""
    replique: int
    u: np.ndarray
    s: np.ndarray
    regime: np.ndarray      # 1 haussier, 0 baissier (H0-c)

    @classmethod
    def tirer(cls, replique: int) -> Commun:
        n = FIN_H - DEBUT_H
        u = _student(graine(replique, 0, 0), n)
        rng = graine(replique, 0, 1)
        switch = rng.random(n) < 1.0 / (REGIME_DUREE_JOURS * 24)
        switch[0] = False
        current = int(rng.random() < 0.5)
        state = ((current + np.cumsum(switch)) % 2).astype(np.int8)
        return cls(replique, u, profil_horaire(np.arange(DEBUT_H, FIN_H)), state)


def derive_et_echelle(commun: Commun, marche: str, sym: str) -> tuple[np.ndarray, np.ndarray]:
    """Dérive par heure et multiplicateur de volatilité de chaque heure, selon le marché."""
    n = len(commun.u)
    if marche == "H0a":
        return np.zeros(n), np.ones(n)
    if marche == "H0b":
        daily = DERIVE_H0B["BTCUSDT"] if sym == "BTCUSDT" else DERIVE_H0B["autres"]
        return np.full(n, daily / 24.0), np.ones(n)
    if marche == "H0c":
        up, down = REGIME_HAUSSIER, REGIME_BAISSIER
        btc = sym == "BTCUSDT"
        drift = np.where(commun.regime == 1, up[1] if btc else up[0], down[1] if btc else down[0]) / 24.0
        scale = np.where(commun.regime == 1, up[2], down[2]).astype(np.float64)
        return drift, scale
    raise ValueError(f"marché inconnu : {marche}")


@njit
def _trajectoire(u: np.ndarray, e: np.ndarray, lam: float, sigma_bar: float, s: np.ndarray, k: np.ndarray,
                 mu: np.ndarray, start: int, w1: np.ndarray, w2: np.ndarray, p0: float, alpha: float, gamma: float,
                 beta: float):
    n = len(u)
    close = np.full(n, np.nan)
    open_ = np.full(n, np.nan)
    high = np.full(n, np.nan)
    low = np.full(n, np.nan)
    zt = np.zeros(n)
    scale_out = np.zeros(n)
    omega = sigma_bar * sigma_bar * (1.0 - alpha - gamma / 2.0 - beta)
    sig2 = sigma_bar * sigma_bar
    eps = 0.0
    price = p0
    mix = np.sqrt(1.0 - lam * lam)
    for t in range(start, n):
        if t > start:
            lev = gamma if eps < 0.0 else 0.0
            sig2 = omega + (alpha + lev) * eps * eps + beta * sig2
        sigma = np.sqrt(sig2)
        scale = sigma * s[t] * k[t]
        z = lam * u[t] + mix * e[t]
        bound = min(20.0, 0.9 / scale)
        if z > bound:
            z = bound
        elif z < -bound:
            z = -bound
        eps = sigma * z
        r = scale * z + mu[t]
        o = price
        price = price * (1.0 + r)
        open_[t] = o
        close[t] = price
        hi_wick = min(0.5 * scale * abs(w1[t]), 0.9)
        lo_wick = min(0.5 * scale * abs(w2[t]), 0.9)
        high[t] = max(o, price) * (1.0 + hi_wick)
        low[t] = min(o, price) * (1.0 - lo_wick)
        zt[t] = z
        scale_out[t] = scale
    return open_, high, low, close, zt, scale_out


@dataclass(frozen=True)
class ParamsPaire:
    symbole: str
    debut: int              # indice de l'heure de cotation
    lam: float
    sigma: float


def params_paire(replique: int, j: int) -> ParamsPaire:
    rng = graine(replique, 1, j, 0)
    sym = symbole(j)
    if j == 0:
        return ParamsPaire(sym, 0, 1.0, VOL_BTC)
    if j == 1:
        return ParamsPaire(sym, 0, LAMBDA_ETH, VOL_ETH)
    if j - 2 < 10:
        lo, hi = 0, heure(pd.Timestamp("2018-01-01", tz="UTC")) - DEBUT_H
    else:
        lo = heure(pd.Timestamp("2018-01-01", tz="UTC")) - DEBUT_H
        hi = heure(pd.Timestamp("2025-01-01", tz="UTC")) - DEBUT_H
    day = int(rng.integers(lo // 24, hi // 24))
    lam = float(rng.uniform(*LAMBDA_ALT))
    sigma = float(np.exp(rng.uniform(np.log(VOL_ALT[0]), np.log(VOL_ALT[1]))))
    return ParamsPaire(sym, day * 24, lam, sigma)


def _retrait(close: np.ndarray, start: int, rng: np.random.Generator, alt: bool) -> int:
    """Indice de fin (exclusif) : retrait de la cote décidé au 1er de chaque mois sur le passé seul (rendement 90 j
    < −75 % : retrait avec probabilité 0,3, à une heure tirée dans le mois)."""
    n = len(close)
    if not alt:
        return n
    months = pd.date_range(DEBUT.floor("D") + pd.offsets.MonthBegin(1), FIN, freq="MS")
    hours = ((months.as_unit("ns").asi8 // HOUR_NS) - DEBUT_H).astype(np.int64)
    draws = rng.random(len(hours))
    offsets = rng.random(len(hours))
    lag = int(RETRAIT["jours"]) * 24
    for k, h in enumerate(hours):
        if h - lag < start or h >= n:
            continue
        past, now = close[h - lag - 1], close[h - 1]          # clôtures connues au 1er du mois, 00:00
        if now / past - 1.0 < RETRAIT["seuil"] and draws[k] < RETRAIT["probabilite"]:
            month_end = hours[k + 1] if k + 1 < len(hours) else n
            return int(min(n, h + 1 + int(offsets[k] * max(1, month_end - h - 1))))
    return n


def generer_paire(commun: Commun, marche: str, j: int) -> Paire | None:
    """Bougies 1 h synthétiques de la paire `j` (régénérées à l'identique à partir de leur graine)."""
    p = params_paire(commun.replique, j)
    n = len(commun.u)
    rng = graine(commun.replique, 1, j, 1)
    e = _student(rng, n)
    w1, w2 = rng.standard_normal(n), rng.standard_normal(n)
    mu, k = derive_et_echelle(commun, marche, p.symbole)
    o, h, lo, c, z, scale = _trajectoire(commun.u, e, p.lam, p.sigma, commun.s, k, mu, p.debut, w1, w2, 100.0,
                                         ALPHA, GAMMA, BETA)
    end = _retrait(c, p.debut, graine(commun.replique, 1, j, 2), j >= 2)
    sl = slice(p.debut, end)
    if end - p.debut < 2:
        return None
    vol_rng = graine(commun.replique, 1, j, 3)
    days = (np.arange(n) + DEBUT_H) // 24
    first_day = int(days[p.debut])
    ndays = int(days[-1]) - first_day + 1
    level = np.empty(ndays)
    level[0] = vol_rng.normal(0.0, VOLUME["niveau_ecart"])
    shocks = vol_rng.normal(0.0, VOLUME["innovation"], ndays)
    for d in range(1, ndays):
        level[d] = VOLUME["phi"] * level[d - 1] + shocks[d]
    factor = np.exp(vol_rng.normal(-VOLUME["facteur_sigma"] ** 2 / 2, VOLUME["facteur_sigma"], ndays))
    taker_noise = vol_rng.normal(0.0, 0.03, n)
    size = np.exp(vol_rng.normal(np.log(200.0), 0.5, n))
    di = days[sl] - first_day
    zs = z[sl]
    volume = VOLUME["base"] * np.exp(level[di]) * commun.s[sl] * (0.5 + np.abs(zs)) * factor[di]
    share = np.clip(0.5 + 0.08 * np.tanh(zs) + taker_noise[sl], 0.05, 0.95)
    open_h = np.arange(n, dtype=np.int64)[sl] + DEBUT_H
    bars = Bougies(1, open_h, (open_h + 1) * HOUR_NS + LATENCE_NS, o[sl], h[sl], lo[sl], c[sl], volume,
                   volume * share, volume / size[sl])
    return Paire(p.symbole, bars)


def quotidien_membres(paires_quotidiennes: dict[str, Bougies]) -> pd.DataFrame:
    """Lignes (day, symbol, close, quote_volume) pour `pit_universe.membership` (journées complètes)."""
    rows = []
    for sym, daily in paires_quotidiennes.items():
        rows.append(pd.DataFrame({"day": pd.to_datetime(daily.ouverture_h * HOUR_NS, utc=True), "symbol": sym,
                                  "close": daily.cloture, "quote_volume": daily.volume}))
    return pd.concat(rows, ignore_index=True)


def univers_synthetique(paires_quotidiennes: dict[str, Bougies]) -> Univers:
    """Top 40 à date : `pit_universe.membership`, inchangée, sur les journées synthétiques."""
    from ..pit_universe import membership
    return Univers.depuis(membership(quotidien_membres(paires_quotidiennes)))


# --- EV02 : cotations synthétiques aux dates réelles -------------------------------------------------------------

def generer_ev02(commun: Commun, marche: str, cotations: dict[str, pd.Timestamp]) -> dict[str, Bougies]:
    """Une paire synthétique (paramètres d'altcoin) par paire USDT du recensement, cotée à la date réelle de sa
    première bougie ; seules les bougies journalières sont gardées (EV02 lit des clôtures journalières)."""
    out = {}
    n = len(commun.u)
    for k, (sym, first) in enumerate(sorted(cotations.items())):
        rng = graine(commun.replique, 2, k)
        start = max(0, heure(pd.Timestamp(first).floor("D")) - DEBUT_H)
        if start >= n - 48:
            continue
        lam = float(rng.uniform(*LAMBDA_ALT))
        sigma = float(np.exp(rng.uniform(np.log(VOL_ALT[0]), np.log(VOL_ALT[1]))))
        e = _student(rng, n)
        w1, w2 = rng.standard_normal(n), rng.standard_normal(n)
        mu, scale = derive_et_echelle(commun, marche, sym)
        o, h, lo, c, z, _ = _trajectoire(commun.u, e, lam, sigma, commun.s, scale, mu, start, w1, w2, 1.0,
                                         ALPHA, GAMMA, BETA)
        end = _retrait(c, start, rng, True)
        sl = slice(start, end)
        open_h = np.arange(n, dtype=np.int64)[sl] + DEBUT_H
        volume = np.ones(end - start)
        bars = Bougies(1, open_h, (open_h + 1) * HOUR_NS + LATENCE_NS, o[sl], h[sl], lo[sl], c[sl], volume,
                       volume / 2, volume)
        out[sym] = agreger(bars, DAY_H)
    return out


def annonces_ev02(annonces: pd.DataFrame, cotations: dict[str, pd.Timestamp]) -> pd.DataFrame:
    """Calendrier réel des annonces `COTATION_SPOT` (même correspondance annonce → paire que la stratégie EV02,
    `strategies.events.annonces_de`) ; une paire sans annonce reçoit une annonce 24 h avant sa cotation (§ 8.1).
    Colonnes : symbol, event_time, available_at (publication + 10 min), reelle."""
    from .strategies.events import annonces_de
    rows = []
    for sym, first in cotations.items():
        known = annonces_de(annonces, sym, "COTATION_SPOT") if len(annonces) else np.zeros(0, dtype=np.int64)
        if len(known):
            available = pd.Timestamp(int(known[0]), tz="UTC")
            rows.append({"symbol": sym, "event_time": available - pd.Timedelta(minutes=10), "available_at": available,
                         "reelle": True})
        else:
            when = pd.Timestamp(first).floor("D") - pd.Timedelta(hours=24)
            rows.append({"symbol": sym, "event_time": when, "available_at": when + pd.Timedelta(minutes=10),
                         "reelle": False})
    return pd.DataFrame(rows, columns=["symbol", "event_time", "available_at", "reelle"])
