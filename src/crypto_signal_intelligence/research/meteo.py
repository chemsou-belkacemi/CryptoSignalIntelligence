"""Météo du marché : composantes, feu, prévisions recalculées, séries `S1` et `S2N` (docs/METEO_MARCHE.md, § 3 à 5).

Même code pour les données réelles et pour les marchés synthétiques du § 7.2 : chaque composante est calculée, pour
tous les jours de décision à la fois, à partir de tables d'entrée « génériques » (bougies 1 h de BTC, clôtures
journalières du top 40 à date, Fear & Greed, financement, achats `S1`, prévisions de volatilité). Seules la source des
prévisions (HAR + profil du protocole v3 sur le réel, HAR journalier simple sur le synthétique) et l'échelle des achats
`S1` (minutes sur le réel, bougies 1 h sur le synthétique) diffèrent : approximations déclarées au § 7.2.

Instant de décision du jour `d` : `T_d = d 00:10 UTC`. Une valeur n'entre que si son heure de connaissance est ≤ `T_d`.
Le paramètre `mutation` n'existe que pour les mutations du § 7.1 (elles doivent être détectées) ; il n'est jamais
utilisé par une exécution.
"""
from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numba import njit
from numpy.lib.stride_tricks import sliding_window_view

from ..features.indicators import atr
from . import figures_history as fh
from . import volatility_hourly as vh

DOC = "docs/METEO_MARCHE.md"
SEED = 20261008
DAY, HOUR, MINUTE = pd.Timedelta(days=1), pd.Timedelta(hours=1), pd.Timedelta(minutes=1)
DECISION = pd.Timedelta(minutes=10)                     # T_d = d 00:10 UTC
COMPONENTS = ("BTC_STRUCTURE", "LARGEUR", "VOL_HAUTE", "PEUR_EXTREME", "FINANCEMENT_CHAUD", "PERTES_RECENTES")
COMPONENTS_R4 = ("BTC_STRUCTURE", "LARGEUR", "PEUR_EXTREME", "PERTES_RECENTES")
RED_AT = {"F6": 3, "R4": 2}                              # rouge si au moins 3 dangers sur 6, 2 sur 4 (§ 4, § 5.8)
EMA_DAYS = 50
LARGEUR_SHARE = 1 / 3
LARGEUR_MIN = {"F6": 10, "R4": 5}                       # membres éligibles minimaux (§ 3.1, § 5.8)
RANK_DAYS, RANK_MIN = 365, 300
VOL_P = 0.90
FNG_LEVEL = 25.0
FNG_KNOWN = DAY                                          # valeur horodatée X supposée connue à X + 1 jour (§ 3.3)
FUNDING_LEVEL = 0.0005
FUNDING_WINDOW = pd.Timedelta(days=7)
FUNDING_MIN = 18
FUNDING_LATENCY = MINUTE                                 # règlement connu 1 min après son heure
LOSS_FROM, LOSS_TO = 10, 4                               # achats S1 des jours d − 10 à d − 4
LOSS_MIN = 70
LOSS_P = 0.20
VERT, ORANGE, ROUGE, SANS_FEU = 0, 1, 2, -1
COLOR_NAMES = {VERT: "VERT", ORANGE: "ORANGE", ROUGE: "ROUGE", SANS_FEU: "SANS_FEU"}
EXEC_HOUR = 1                                            # achat et vente à l'ouverture de la bougie 1 h de 01:00
MIN_AGE = pd.Timedelta(days=90)                          # au moins 90 jours depuis la première bougie 1 h (FACTORS)
COTATION_ARRETEE = "COTATION_ARRETEE"
SIGMA_168_HOURS, SIGMA_168_MIN = 168, 160
S1_BUYS = 20
S1_MIN_USABLE = 10
STOP_ATR = 2.0
ATR_PERIOD = 14
HOLD_HOURS = 60
LATENCY = pd.Timedelta(seconds=2)                       # latence supposée des bougies (config : data)
ZERO = pd.Timedelta(0)
MUTATIONS = ("ema_open_time", "largeur_jour_d", "membres_mois_courant", "fng_jour_d", "financement_sans_latence", "financement_t_plus_8h",
             "vol_cible_filtree", "rang_inclut_jour", "base_autre_instance", "pertes_d10_d1",
             "panier_sans_ouverture_future", "rotation_non_multiple_7", "lecture_apres_coupure")


def decision_time(days: pd.DatetimeIndex) -> pd.DatetimeIndex:
    return utc(days) + DECISION


def utc(values) -> pd.DatetimeIndex:
    dtype = getattr(values, "dtype", None)
    if isinstance(dtype, pd.DatetimeTZDtype):
        out = pd.DatetimeIndex(values).tz_convert("UTC")
    else:
        out = pd.DatetimeIndex(pd.to_datetime(values, utc=True))
    return out.as_unit("ns")


def stamp(value) -> pd.Timestamp:
    out = pd.Timestamp(value)
    return (out.tz_localize("UTC") if out.tzinfo is None else out.tz_convert("UTC")).as_unit("ns")


def day_index(first, last) -> pd.DatetimeIndex:
    return pd.date_range(stamp(first), stamp(last), freq="D").as_unit("ns")


# --- Outils numériques -----------------------------------------------------------------------------------------------

@njit(cache=True)
def _ema_seeded(values: np.ndarray, n: int) -> np.ndarray:
    """EMA (α = 2 / (n + 1)) d'une série SANS trou, départ par la moyenne simple des n premières valeurs."""
    out = np.full(len(values), np.nan)
    if len(values) < n:
        return out
    alpha = 2.0 / (n + 1.0)
    state = 0.0
    for i in range(n):
        state += values[i]
    state /= n
    out[n - 1] = state
    for i in range(n, len(values)):
        state = alpha * values[i] + (1.0 - alpha) * state
        out[i] = state
    return out


def ema_seeded(values, n: int = EMA_DAYS) -> np.ndarray:
    return _ema_seeded(np.ascontiguousarray(np.asarray(values, float)), int(n))


def ema_skipping(matrix: np.ndarray, n: int = EMA_DAYS) -> tuple[np.ndarray, np.ndarray]:
    """Par colonne : EMA sur les seules valeurs présentes (un jour absent est sauté), et nombre cumulé de valeurs."""
    ema = np.full(matrix.shape, np.nan)
    present = np.isfinite(matrix)
    for j in range(matrix.shape[1]):
        rows = np.flatnonzero(present[:, j])
        if len(rows):
            ema[rows, j] = ema_seeded(matrix[rows, j], n)
    return ema, np.cumsum(present, axis=0)


def strict_rank(values: np.ndarray, *, window: int = RANK_DAYS, minimum: int = RANK_MIN,
                include_today: bool = False) -> tuple[np.ndarray, np.ndarray]:
    """Rang `p` = part, strictement inférieure, des valeurs des `window` jours précédents (d − window à d − 1) ;
    NaN si moins de `minimum` valeurs ou valeur du jour absente. `include_today` : mutation (fenêtre d − 364 à d)."""
    values = np.asarray(values, float)
    n = len(values)
    p = np.full(n, np.nan)
    count = np.zeros(n, int)
    if n == 0:
        return p, count
    padded = np.concatenate([np.full(window, np.nan), values])
    windows = sliding_window_view(padded, window)          # windows[i] = valeurs i − window .. i − 1 (décalées)
    base = windows[1:] if include_today else windows[:-1]  # base[i] : d − window .. d − 1 (ou d − window + 1 .. d)
    known = np.isfinite(base)
    count = known.sum(axis=1)
    below = (np.where(known, base, np.inf) < values[:, None]).sum(axis=1)
    ok = (count >= minimum) & np.isfinite(values)
    p[ok] = below[ok] / count[ok]
    return p, count


# --- Clôtures journalières -------------------------------------------------------------------------------------------

def daily_from_h1(h1: pd.DataFrame) -> pd.DataFrame:
    """Une ligne par journée UTC X : clôture de la bougie 1 h de 23:00, heure de connaissance (`available_at` de cette
    bougie) et `complete` (les 24 bougies présentes). Une journée incomplète n'a pas de clôture."""
    if h1.empty:
        return pd.DataFrame(columns=["close", "known_at", "complete"])
    t = utc(h1["open_time"])
    frame = pd.DataFrame({"day": t.floor("D"), "hour": t.hour, "close": h1["close"].to_numpy(float),
                          "known_at": utc(h1["available_at"])})
    grouped = frame.groupby("day")
    hours = grouped["hour"].nunique()
    last = frame[frame["hour"] == 23].drop_duplicates("day", keep="last").set_index("day")
    out = pd.DataFrame(index=hours.index)
    out["complete"] = (hours == 24) & out.index.isin(last.index)
    out["close"] = last["close"].reindex(out.index).where(out["complete"])
    out["known_at"] = last["known_at"].reindex(out.index)
    return out


# --- Composantes -----------------------------------------------------------------------------------------------------

def btc_structure(btc_daily: pd.DataFrame, days: pd.DatetimeIndex, *, mutation: str | None = None) -> pd.DataFrame:
    """`BTC_STRUCTURE` : clôture de d − 1 ≤ EMA50 des clôtures journalières (journées complètes seulement)."""
    days = utc(days)
    complete = btc_daily[btc_daily["complete"]].sort_index()
    table = pd.DataFrame({"close": complete["close"].to_numpy(float),
                          "ema": ema_seeded(complete["close"].to_numpy(float)),
                          "known_at": complete["known_at"]}, index=complete.index)
    leaky = mutation == "ema_open_time"
    row = table.reindex(days if leaky else days - DAY)       # mutation : jointure sur l'ouverture (journée d lue)
    known = row["known_at"].notna() & (utc(row["known_at"].fillna(pd.Timestamp(0, tz="UTC"))) <= decision_time(days))
    ok = row["close"].notna().to_numpy() & row["ema"].notna().to_numpy() & (leaky | known.to_numpy())
    danger = (row["close"] <= row["ema"]).to_numpy()
    return pd.DataFrame({"BTC_STRUCTURE": np.where(ok, danger.astype(float), np.nan),
                         "btc_close": row["close"].to_numpy(), "btc_ema50": row["ema"].to_numpy()}, index=days)


def membership_at(pit_daily: pd.DataFrame, *, mutation: str | None = None) -> pd.DataFrame:
    """Appartenance au top 40 à date recalculée (`pit_universe.membership`, importé sans changement). Mutation
    `membres_mois_courant` : la fenêtre des 30 journées commence le 1er du mois M au lieu de finir la veille de M."""
    from .pit_universe import membership
    if mutation != "membres_mois_courant":
        return membership(pit_daily)
    from .pit_universe import WINDOW_DAYS
    shifted = pit_daily.assign(day=pd.to_datetime(pit_daily["day"], utc=True) - pd.Timedelta(days=WINDOW_DAYS))
    return membership(shifted)          # le mois M est jugé sur ses propres 30 premières journées


def largeur(pit_daily: pd.DataFrame, members: pd.DataFrame, days: pd.DatetimeIndex, *, rule: str = "F6",
            latency: pd.Timedelta = LATENCY, mutation: str | None = None) -> pd.DataFrame:
    """`LARGEUR` : membres du mois de d − 1, éligibles (≥ 50 clôtures jusqu'à d − 1, clôture de d − 1 présente), part
    de ceux dont la clôture de d − 1 est > leur EMA50 ; danger si < 1/3 ; absente sous le minimum d'éligibles."""
    days = utc(days)
    out = pd.DataFrame({"LARGEUR": np.nan, "largeur_share": np.nan, "largeur_eligible": 0}, index=days)
    if pit_daily.empty or members.empty:
        return out
    frame = pit_daily.assign(day=utc(pit_daily["day"]))
    closes = frame.pivot_table(index="day", columns="symbol", values="close", aggfunc="last").sort_index()
    calendar = day_index(closes.index.min(), max(closes.index.max(), days.max()))
    closes = closes.reindex(calendar)
    ema, counts = ema_skipping(closes.to_numpy(float))
    symbols = list(closes.columns)
    target = days if mutation == "largeur_jour_d" else days - DAY
    rows = calendar.get_indexer(target)
    inside = rows >= 0
    from .pit_universe import member_mask
    mask = member_mask(members, pd.DatetimeIndex(days - DAY), symbols).to_numpy(bool)
    c = np.full((len(days), len(symbols)), np.nan)
    e = np.full_like(c, np.nan)
    n = np.zeros_like(c)
    c[inside], e[inside], n[inside] = closes.to_numpy(float)[rows[inside]], ema[rows[inside]], counts[rows[inside]]
    known = (pd.DatetimeIndex(target) + DAY + latency <= decision_time(days)) | (mutation == "largeur_jour_d")
    eligible = mask & (n >= EMA_DAYS) & np.isfinite(c) & np.isfinite(e) & np.asarray(known)[:, None]
    count = eligible.sum(axis=1)
    above = (eligible & (c > e)).sum(axis=1)
    share = np.where(count > 0, above / np.maximum(count, 1), np.nan)
    ok = count >= LARGEUR_MIN[rule]
    out["LARGEUR"] = np.where(ok, (share < LARGEUR_SHARE).astype(float), np.nan)
    out["largeur_share"] = share
    out["largeur_eligible"] = count
    return out


def fear(fng: pd.Series, days: pd.DatetimeIndex, *, mutation: str | None = None) -> pd.DataFrame:
    """`PEUR_EXTREME` : Fear & Greed horodaté d − 1 < 25 (historique : supposé connu à `T_d`)."""
    days = utc(days)
    series = fng.copy()
    series.index = utc(series.index).floor("D")
    series = series[~series.index.duplicated(keep="last")]
    value = series.reindex(days if mutation == "fng_jour_d" else days - DAY).to_numpy(float)
    return pd.DataFrame({"PEUR_EXTREME": np.where(np.isfinite(value), (value < FNG_LEVEL).astype(float), np.nan),
                         "fng": value}, index=days)


def funding_known_at(funding: pd.DataFrame, *, mutation: str | None = None) -> pd.DatetimeIndex:
    times = utc(funding["time"])
    return times if mutation == "financement_sans_latence" else times + FUNDING_LATENCY


def funding_mean_at(funding: pd.DataFrame, at, *, mutation: str | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Moyenne et nombre des règlements dont l'heure de connaissance est dans ]at − 7 j ; at]."""
    at = utc(at)
    if funding.empty:
        return np.full(len(at), np.nan), np.zeros(len(at), int)
    known = funding_known_at(funding, mutation=mutation)
    order = np.argsort(known.asi8, kind="stable")
    k = known.asi8[order]
    rate = funding["rate"].to_numpy(float)[order]
    cum = np.concatenate([[0.0], np.cumsum(rate)])
    hi = np.searchsorted(k, at.asi8, side="right")
    lo = np.searchsorted(k, (at - FUNDING_WINDOW).asi8, side="right")
    n = hi - lo
    mean = np.where(n > 0, (cum[hi] - cum[lo]) / np.maximum(n, 1), np.nan)
    return mean, n


def funding_hot(funding: pd.DataFrame, days: pd.DatetimeIndex, *, mutation: str | None = None) -> pd.DataFrame:
    """`FINANCEMENT_CHAUD` : moyenne des règlements connus dans ]T_d − 7 j ; T_d] (au moins 18) > 0,05 % par 8 h."""
    days = utc(days)
    at = decision_time(days) + (pd.Timedelta(hours=8) if mutation == "financement_t_plus_8h" else ZERO)
    mean, n = funding_mean_at(funding, at, mutation=mutation)
    ok = n >= FUNDING_MIN
    return pd.DataFrame({"FINANCEMENT_CHAUD": np.where(ok, (mean > FUNDING_LEVEL).astype(float), np.nan),
                         "funding_mean": mean, "funding_n": n}, index=days)


def recent_losses(s1: pd.DataFrame, days: pd.DatetimeIndex, *, mutation: str | None = None) -> pd.DataFrame:
    """`PERTES_RECENTES` : `m_d` = R brut moyen des achats `S1` des jours d − 10 à d − 4 (au moins 70) ; rang strict
    parmi les `m` des 365 jours précédents (au moins 300) ; danger si `p < 0,20`. `s1` : colonnes `day`, `r_gross`."""
    days = utc(days)
    out = pd.DataFrame({"PERTES_RECENTES": np.nan, "m": np.nan, "m_rank": np.nan}, index=days)
    if s1.empty:
        return out
    usable = s1[np.isfinite(s1["r_gross"].to_numpy(float))]
    first = min(utc(s1["day"]).min(), days.min() - (RANK_DAYS + LOSS_FROM + 1) * DAY)
    calendar = day_index(first, max(utc(s1["day"]).max(), days.max()))
    sums = usable.groupby(utc(usable["day"]))["r_gross"].sum().reindex(calendar, fill_value=0.0).to_numpy(float)
    counts = usable.groupby(utc(usable["day"]))["r_gross"].size().reindex(calendar, fill_value=0).to_numpy(float)
    lo, hi = (LOSS_FROM, 1) if mutation == "pertes_d10_d1" else (LOSS_FROM, LOSS_TO)    # mutation : + d − 3 à d − 1
    cs, cn = np.concatenate([[0.0], np.cumsum(sums)]), np.concatenate([[0.0], np.cumsum(counts)])
    idx = np.arange(len(calendar))
    a, b = np.clip(idx - lo, 0, len(calendar)), np.clip(idx - hi + 1, 0, len(calendar))   # jours [d − lo ; d − hi]
    n = cn[b] - cn[a]
    m = np.where((n >= LOSS_MIN) & (idx - lo >= 0), (cs[b] - cs[a]) / np.maximum(n, 1), np.nan)
    p, _ = strict_rank(m, include_today=mutation == "rang_inclut_jour")
    rows = calendar.get_indexer(days)
    out["m"] = m[rows]
    out["m_rank"] = p[rows]
    out["PERTES_RECENTES"] = np.where(np.isfinite(p[rows]), (p[rows] < LOSS_P).astype(float), np.nan)
    return out


@dataclass
class VolForecasts:
    """Prévisions de BTC à 00:00 par instance du modèle (`by_instance[réajustement]` : série jour → variance prévue),
    et l'instance de chaque jour (dernier réajustement ≤ origine)."""
    by_instance: dict[pd.Timestamp, pd.Series]
    instance_of: pd.Series


def vol_high(forecasts: VolForecasts, days: pd.DatetimeIndex, *, mutation: str | None = None) -> pd.DataFrame:
    """`VOL_HAUTE` : rang strict de la prévision 24 h de BTC du jour parmi celles des 365 jours précédents, calculées
    par la MÊME instance du modèle (au moins 300) ; danger si `p ≥ 0,90`."""
    days = utc(days)
    value = np.full(len(days), np.nan)
    rank = np.full(len(days), np.nan)
    instance = forecasts.instance_of.reindex(days)
    for inst in instance.dropna().unique():
        chosen = np.flatnonzero((instance == inst).to_numpy())
        series = forecasts.by_instance.get(pd.Timestamp(inst))
        if series is None or not len(chosen):
            continue
        if mutation == "base_autre_instance":      # base lue chez l'instance de chaque jour de base (mutation)
            own = pd.concat([forecasts.by_instance[pd.Timestamp(i)].reindex(
                forecasts.instance_of.index[(forecasts.instance_of == i).to_numpy()])
                for i in forecasts.instance_of.dropna().unique() if pd.Timestamp(i) in forecasts.by_instance])
            series = own.where(own.index < days[chosen].min()).dropna().combine_first(series)
        calendar = day_index(days[chosen].min() - RANK_DAYS * DAY, days[chosen].max())
        values = series.reindex(calendar).to_numpy(float)
        p, _ = strict_rank(values, include_today=mutation == "rang_inclut_jour")
        rows = calendar.get_indexer(days[chosen])
        value[chosen], rank[chosen] = values[rows], p[rows]
    return pd.DataFrame({"VOL_HAUTE": np.where(np.isfinite(rank), (rank >= VOL_P).astype(float), np.nan),
                         "btc_forecast": value, "vol_rank": rank}, index=days)


def light(states: pd.DataFrame, *, rule: str = "F6") -> np.ndarray:
    """Couleur du feu : `SANS_FEU` si deux composantes ou plus sont absentes ; sinon rouge à partir de `RED_AT` dangers,
    orange un danger en dessous, vert au-dessous (§ 4 ; `FEU_R4` du § 5.8)."""
    names = COMPONENTS if rule == "F6" else COMPONENTS_R4
    values = states[list(names)].to_numpy(float)
    absent = (~np.isfinite(values)).sum(axis=1)
    danger = (values == 1).sum(axis=1)
    red = RED_AT[rule]
    color = np.where(danger >= red, ROUGE, np.where(danger == red - 1, ORANGE, VERT))
    return np.where(absent >= 2, SANS_FEU, color).astype(np.int8)


@dataclass
class LightInputs:
    """Entrées génériques du feu (réelles ou synthétiques), coupées à la fin de la période lue."""
    btc_h1: pd.DataFrame                       # open_time, close, available_at (BTCUSDT, bougies 1 h)
    pit_daily: pd.DataFrame                    # day, symbol, close, quote_volume (bougies journalières publiques)
    members: pd.DataFrame                      # month, symbol (top 40 à date)
    fng: pd.Series                             # jour → valeur
    funding: pd.DataFrame                      # time, rate
    s1: pd.DataFrame                           # day, r_gross
    forecasts: VolForecasts | None             # None : VOL_HAUTE non calculée (FEU_R4)
    latency: pd.Timedelta = LATENCY
    extra: dict = field(default_factory=dict)


def components(inputs: LightInputs, days: pd.DatetimeIndex, *, rule: str = "F6",
               mutation: str | None = None) -> pd.DataFrame:
    """Toutes les composantes du jour `d` (1 danger, 0 sans danger, NaN absente), leurs valeurs et la couleur."""
    days = utc(days)
    parts = [btc_structure(daily_from_h1(inputs.btc_h1), days, mutation=mutation),
             largeur(inputs.pit_daily, inputs.members, days, rule=rule, latency=inputs.latency, mutation=mutation),
             fear(inputs.fng, days, mutation=mutation),
             funding_hot(inputs.funding, days, mutation=mutation),
             recent_losses(inputs.s1, days, mutation=mutation)]
    if inputs.forecasts is not None:
        parts.append(vol_high(inputs.forecasts, days, mutation=mutation))
    states = pd.concat(parts, axis=1)
    if "VOL_HAUTE" not in states:
        states["VOL_HAUTE"] = np.nan
    states["color"] = light(states, rule=rule)
    return states


def episodes(color: np.ndarray) -> list[tuple[int, int]]:
    """Épisodes rouges : suites maximales de jours ROUGES ou `SANS_FEU` contenant au moins un jour rouge, bordées par
    des jours verts ou orange (ou les bords de la période). Bornes incluses."""
    out = []
    i, n = 0, len(color)
    while i < n:
        if color[i] in (ROUGE, SANS_FEU):
            j = i
            while j + 1 < n and color[j + 1] in (ROUGE, SANS_FEU):
                j += 1
            if (color[i:j + 1] == ROUGE).any():
                out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


# --- Prévisions de volatilité 24 h recalculées (protocole v3, sans filtre sur la cible) --------------------------------

def quarter_starts(first: pd.Timestamp, last: pd.Timestamp) -> list[pd.Timestamp]:
    start = pd.Timestamp(first).tz_convert(None).to_period("Q").start_time.tz_localize("UTC")
    return list(pd.date_range(start, pd.Timestamp(last), freq=vh.REFIT_FREQ))


def origin_rows(frame: pd.DataFrame, *, mutation: str | None = None) -> pd.DataFrame:
    """Lignes d'origine 00:00 aux entrées complètes (`complete_rows`), SANS condition sur la cible. Mutation
    `vol_cible_filtree` : on ne garde que les origines dont la cible des 24 h suivantes est complète (fichier v3)."""
    origins = utc(frame["origin"])
    keep = (origins.hour == 0) & vh.complete_rows(frame).to_numpy()
    if mutation == "vol_cible_filtree":
        keep &= (frame["rv2_24"] > 0).to_numpy()
    rows = frame[keep].copy()
    rows["day"] = utc(rows["origin"]).floor("D")
    return rows


def har_variance(models: vh.Models, rows: pd.DataFrame) -> np.ndarray:
    """Variance prévue sur 24 h : exp(x · β) × facteur de Duan (`Har.predict`), NaN sans modèle."""
    if models.har is None or rows.empty:
        return np.full(len(rows), np.nan)
    rows = vh.with_profile(rows, models.profile, models.profile_default)
    return models.har.predict(rows[list(vh.H1_FEATURES)].to_numpy(float))


@dataclass
class RealForecaster:
    """Instances `fit_at` (HAR + profil, horizon 24 h) réajustées le 1er de chaque trimestre sur les 40 paires de
    recherche et BTC (fenêtre glissante de 3 ans, lignes purgées), comme en v3."""
    training: pd.DataFrame                    # hourly_frame des paires d'entraînement, colonne `symbol`
    seed: int
    models: dict[pd.Timestamp, vh.Models] = field(default_factory=dict)

    def instance(self, refit: pd.Timestamp) -> vh.Models:
        if refit not in self.models:
            self.models[refit] = vh.fit_at(self.training, refit, 24, seed=self.seed)
        return self.models[refit]


def instance_of_days(days: pd.DatetimeIndex, refits: list[pd.Timestamp]) -> pd.Series:
    days = utc(days)
    stamps = pd.DatetimeIndex(refits).as_unit("ns")
    pos = np.searchsorted(stamps.asi8, pd.DatetimeIndex(days).as_unit("ns").asi8, side="right") - 1
    values = [stamps[p] if p >= 0 else pd.NaT for p in pos]
    return pd.Series(values, index=days)


def real_btc_forecasts(forecaster: RealForecaster, btc_frame: pd.DataFrame, days: pd.DatetimeIndex, *,
                       mutation: str | None = None) -> VolForecasts:
    """Prévisions de BTC à 00:00 : pour chaque instance, valeur des jours du trimestre ET de la base (365 jours
    précédents), calculées par cette instance (§ 3.3)."""
    days = utc(days)
    rows = origin_rows(btc_frame, mutation=mutation)
    refits = quarter_starts(days.min(), days.max())
    instance_of = instance_of_days(days, refits)
    by_instance: dict[pd.Timestamp, pd.Series] = {}
    for refit in refits:
        chosen = days[(instance_of == refit).to_numpy()]
        if not len(chosen):
            continue
        part = rows[(rows["day"] >= chosen.min() - RANK_DAYS * DAY) & (rows["day"] <= chosen.max())]
        values = har_variance(forecaster.instance(refit), part)
        by_instance[refit] = pd.Series(values, index=pd.DatetimeIndex(part["day"]))
    return VolForecasts(by_instance, instance_of)


def real_sigma(forecaster: RealForecaster, frame: pd.DataFrame, days: pd.DatetimeIndex) -> pd.Series:
    """σ̂ d'une paire à l'origine d 00:00 = √(variance prévue 24 h), instance du trimestre de d."""
    days = utc(days)
    rows = origin_rows(frame)
    rows = rows[rows["day"].isin(days)]
    out = pd.Series(np.nan, index=days)
    if rows.empty:
        return out
    instance_of = instance_of_days(pd.DatetimeIndex(rows["day"]), quarter_starts(days.min(), days.max()))
    for refit in instance_of.dropna().unique():
        part = rows[(instance_of == refit).to_numpy()]
        var = har_variance(forecaster.instance(pd.Timestamp(refit)), part)
        out.loc[pd.DatetimeIndex(part["day"])] = np.sqrt(np.where(var > 0, var, np.nan))
    return out


def sigma_168(h1: pd.DataFrame, days: pd.DatetimeIndex) -> pd.Series:
    """`σ24_168` (variante 2018) : √(24 × moyenne des carrés des rendements log horaires des 168 dernières heures
    connues à 00:00), au moins 160 présents ; rendement défini si la bougie de l'heure précédente existe."""
    days = utc(days)
    out = pd.Series(np.nan, index=days)
    if h1.empty:
        return out
    t = utc(h1["open_time"])
    grid = pd.date_range(t.min(), max(t.max(), days.max()), freq="h").as_unit("ns")
    close = pd.Series(h1["close"].to_numpy(float), index=t).groupby(level=0).last().reindex(grid).to_numpy(float)
    r2 = np.full(len(grid), np.nan)
    r2[1:] = np.diff(np.log(np.where(close > 0, close, np.nan))) ** 2
    present = np.isfinite(r2)
    cs = np.concatenate([[0.0], np.cumsum(np.where(present, r2, 0.0))])
    cn = np.concatenate([[0], np.cumsum(present)])
    last_bar = grid.get_indexer(pd.DatetimeIndex(days) - HOUR)          # bougie de 23:00 de d − 1 (incluse)
    ok = last_bar >= SIGMA_168_HOURS - 1
    hi = np.where(ok, last_bar + 1, 0)
    lo = np.where(ok, last_bar + 1 - SIGMA_168_HOURS, 0)
    n = cn[hi] - cn[lo]
    mean = np.where(n > 0, (cs[hi] - cs[lo]) / np.maximum(n, 1), np.nan)
    out[:] = np.where(ok & (n >= SIGMA_168_MIN), np.sqrt(24.0 * mean), np.nan)
    return out


# --- Panier S2N ------------------------------------------------------------------------------------------------------

def basket_returns(h1: pd.DataFrame, days: pd.DatetimeIndex, *, mutation: str | None = None) -> pd.DataFrame:
    """Achat à l'ouverture de la bougie 1 h de d 01:00 (présente), vente à l'ouverture de d + 1 01:00 ; si elle
    manque, à la prochaine ouverture disponible ; si la paire n'a plus de bougie, à sa dernière clôture
    (`COTATION_ARRETEE`, gardée). Mutation `panier_sans_ouverture_future` : la paire est retirée si l'ouverture de
    d + 1 01:00 manque (donnée future)."""
    days = utc(days)
    out = pd.DataFrame({"entry": np.nan, "exit": np.nan, "r": np.nan, "exit_kind": None}, index=days)
    if h1.empty:
        return out
    t = utc(h1["open_time"])
    frame = pd.DataFrame({"open": h1["open"].to_numpy(float), "close": h1["close"].to_numpy(float)}, index=t)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    buy_at = pd.DatetimeIndex(days) + EXEC_HOUR * HOUR
    sell_at = buy_at + DAY
    entry = frame["open"].reindex(buy_at).to_numpy(float)
    pos = np.searchsorted(frame.index.asi8, sell_at.asi8, side="left")
    has_next = pos < len(frame)
    exit_price = np.where(has_next, frame["open"].to_numpy(float)[np.minimum(pos, len(frame) - 1)],
                          frame["close"].to_numpy(float)[-1])
    exact = has_next & (frame.index.asi8[np.minimum(pos, len(frame) - 1)] == sell_at.asi8)
    kind = np.where(exact, "OUVERTURE_D1", np.where(has_next, "OUVERTURE_SUIVANTE", COTATION_ARRETEE))
    ok = np.isfinite(entry) & (entry > 0)
    if mutation == "panier_sans_ouverture_future":
        ok &= exact
    out["entry"] = np.where(ok, entry, np.nan)
    out["exit"] = np.where(ok, exit_price, np.nan)
    out["r"] = np.where(ok, exit_price / np.where(ok, entry, 1.0) - 1.0, np.nan)
    out["exit_kind"] = pd.Series(kind, index=days).where(ok, None)
    return out


def first_bar(h1: pd.DataFrame) -> pd.Timestamp | None:
    return None if h1.empty else utc(h1["open_time"]).min()


def s2n(returns: dict[str, pd.DataFrame], sigma: dict[str, pd.Series], members: pd.DataFrame,
        firsts: dict[str, pd.Timestamp | None], days: pd.DatetimeIndex, *, symbols: list[str] | None = None) -> pd.DataFrame:
    """`y_d` = moyenne, sur les paires éligibles du jour (membre du mois de d − 1, 90 jours depuis la première bougie
    1 h, ouverture de d 01:00 présente, σ̂ calculable), du rendement brut divisé par σ̂ ; aussi le % brut moyen."""
    days = utc(days)
    from .pit_universe import member_mask
    names = sorted(symbols if symbols is not None else returns)
    mask = member_mask(members, pd.DatetimeIndex(days - DAY), names).to_numpy(bool)
    r = np.column_stack([returns[s]["r"].reindex(days).to_numpy(float) for s in names]) if names else np.empty((len(days), 0))
    sig = np.column_stack([sigma[s].reindex(days).to_numpy(float) for s in names]) if names else np.empty((len(days), 0))
    buy = (pd.DatetimeIndex(days) + EXEC_HOUR * HOUR).asi8
    def old_enough(first: pd.Timestamp | None) -> np.ndarray:
        return np.zeros(len(days), bool) if first is None else buy - stamp(first).value >= MIN_AGE.value

    age = (np.column_stack([old_enough(firsts.get(s)) for s in names]) if names
           else np.empty((len(days), 0), bool))
    ok = mask & age & np.isfinite(r) & np.isfinite(sig) & (sig > 0)
    n = ok.sum(axis=1)
    z = np.where(ok, r / np.where(ok, sig, 1.0), 0.0)
    pct = np.where(ok, r, 0.0)
    inv = np.where(ok, 1.0 / np.where(ok, sig, 1.0), 0.0)
    return pd.DataFrame({"y": np.where(n > 0, z.sum(axis=1) / np.maximum(n, 1), np.nan),
                         "pct": np.where(n > 0, pct.sum(axis=1) / np.maximum(n, 1), np.nan),
                         "inv_sigma": np.where(n > 0, inv.sum(axis=1) / np.maximum(n, 1), np.nan),
                         "n": n, "sigma_median": [float(np.median(sig[i][ok[i]])) if n[i] else np.nan
                                                   for i in range(len(days))]}, index=days)


# --- Achats génériques S1 (transaction de référence, annexe A) -------------------------------------------------------

def s1_seed(day: pd.Timestamp, k: int) -> int:
    key = f"{SEED}:METEO:HASARD:{pd.Timestamp(day):%Y-%m-%d}:{k}"
    return int(hashlib.sha256(key.encode()).hexdigest()[:16], 16)


def s1_draws(eligible: pd.DataFrame, *, minutes: int = 1440, step: pd.Timedelta = MINUTE) -> pd.DataFrame:
    """20 achats par jour : paire tirée uniformément parmi les éligibles du jour (colonnes booléennes), instant tiré
    uniformément parmi les `minutes` qui ouvrent dans ]T_d ; T_{d+1}] (minutes : 00:11 → 00:10 du lendemain)."""
    rows = []
    names = list(eligible.columns)
    values = eligible.to_numpy(bool)
    first_open = DECISION - DECISION % step + step       # première ouverture strictement après T_d (00:11 ou 01:00)
    for i, day in enumerate(eligible.index):
        chosen = [names[j] for j in np.flatnonzero(values[i])]
        for k in range(S1_BUYS):
            if not chosen:
                rows.append({"day": day, "k": k, "symbol": None, "at": pd.NaT})
                continue
            rng = random.Random(s1_seed(day, k))
            symbol = chosen[rng.randrange(len(chosen))]
            rows.append({"day": day, "k": k, "symbol": symbol, "at": day + first_open + rng.randrange(minutes) * step})
    return pd.DataFrame(rows)


@njit(cache=True)
def _play_batch(o, h, lo, c, ns, starts_ns, stops, hold_ns, weights):
    """R brut (sans frais ni glissement) de chaque achat au marché à `starts_ns`, stop fixe, tiers à 1, 2 et 3 R,
    durée `hold_ns` : la copie compilée de F15 (`figures_history._simulate`, importée sans changement)."""
    m = len(starts_ns)
    out = np.full(m, np.nan)
    status = np.full(m, -1)
    targets = np.empty(3)
    for k in range(m):
        start = np.searchsorted(ns, starts_ns[k])
        if start >= len(ns) or ns[start] - starts_ns[k] > 10 * fh.MINUTE_NS:
            status[k] = 4                               # trou de plus de 10 minutes
            continue
        entry = o[start]
        stop = stops[k]
        if not (np.isfinite(stop) and stop < entry * (1.0 - 0.001)):
            status[k] = 6                               # géométrie invalide
            continue
        for j in range(3):
            targets[j] = entry + (j + 1.0) * (entry - stop)
        st, _, _, _, r, _, _ = fh._simulate(o, h, lo, c, ns, entry, stop, targets, starts_ns[k], -1, hold_ns, True,
                                            0.0, 0.0, weights, True)
        status[k] = st
        if st == 0:
            out[k] = r
    return out, status


def decision_stops(h1: pd.DataFrame, at: pd.DatetimeIndex) -> np.ndarray:
    """Stop `C_t − 2 × ATR_t` (ATR de Wilder 14 en 1 h) de la dernière bougie 1 h dont l'`available_at` précède
    l'instant d'exécution."""
    if h1.empty or not len(at):
        return np.full(len(at), np.nan)
    frame = h1.sort_values("open_time").reset_index(drop=True)
    a = atr(frame["high"].astype(float), frame["low"].astype(float), frame["close"].astype(float), ATR_PERIOD).to_numpy(float)
    known = utc(frame["available_at"]).asi8
    pos = np.searchsorted(known, utc(at).asi8, side="left") - 1      # available_at < instant
    ok = pos >= 0
    close = frame["close"].to_numpy(float)
    out = np.full(len(at), np.nan)
    out[ok] = close[pos[ok]] - STOP_ATR * a[pos[ok]]
    return out


def play_s1(bars: pd.DataFrame, h1: pd.DataFrame, at: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    """R brut des achats d'une paire (bougies d'exécution `bars` : minutes sur le réel, heures sur le synthétique)."""
    m = fh.Minutes.from_frame(bars.sort_values("open_time").reset_index(drop=True))
    stops = decision_stops(h1, at)
    return _play_batch(m.o, m.h, m.lo, m.c, m.ns, utc(at).asi8.astype(np.int64), np.ascontiguousarray(stops, float),
                       np.int64(HOLD_HOURS * 3600 * 10**9), fh.WEIGHTS)


def play_s1_costs(bars: pd.DataFrame, h1: pd.DataFrame, at: pd.DatetimeIndex, symbol: str) -> pd.DataFrame:
    """R net (central et défavorable) des mêmes achats, par `figures_history.simulate_fast` (descriptif)."""
    m = fh.Minutes.from_frame(bars.sort_values("open_time").reset_index(drop=True))
    stops = decision_stops(h1, at)
    rows = []
    for when, stop in zip(utc(at), stops, strict=True):
        row = {}
        first = int(np.searchsorted(m.ns, when.value))
        if first < len(m.ns) and m.ns[first] - when.value <= 10 * fh.MINUTE_NS and math.isfinite(stop) \
                and stop < m.o[first] * (1 - 0.001):
            entry = float(m.o[first])
            for s in ("central", "defavorable"):
                res = fh.simulate_fast(m, entry=entry, stop=float(stop), targets=[entry + k * (entry - stop) for k in (1, 2, 3)],
                                       order_from=when, order_until=None, hold_minutes=HOLD_HOURS * 60, symbol=symbol,
                                       scenario=s, market_entry=True)
                row[f"r_{s}"] = res.get("r", np.nan)
        rows.append(row)
    return pd.DataFrame(rows, columns=["r_central", "r_defavorable"])


def s1_by_day(trades: pd.DataFrame) -> pd.DataFrame:
    """Jours de `S1` : au moins 10 achats utilisables (sinon le jour sort de `S1`)."""
    usable = trades[np.isfinite(trades["r_gross"].to_numpy(float))]
    grouped = usable.groupby("day")["r_gross"]
    out = pd.DataFrame({"n": grouped.size(), "r_gross": grouped.mean()})
    return out[out["n"] >= S1_MIN_USABLE]


# --- Causalité (§ 7.1) -------------------------------------------------------------------------------------------------

def truncate(frame: pd.DataFrame, column: str, at: pd.Timestamp, *, delay: pd.Timedelta = ZERO) -> pd.DataFrame:
    if frame.empty:
        return frame
    return frame[np.asarray(utc(frame[column]) + delay <= at)]


def falsify(frame: pd.DataFrame, column: str, at: pd.Timestamp, rng: np.random.Generator, *,
            delay: pd.Timedelta = ZERO, skip: tuple[str, ...] = ()) -> pd.DataFrame:
    """Valeurs numériques des lignes connues après `at` multipliées par un facteur au hasard dans [0,5 ; 1,5]."""
    if frame.empty:
        return frame
    frame = frame.copy()
    future = np.asarray(utc(frame[column]) + delay > at)
    for name in frame.columns:
        if name in skip or name in ("open_time", "close_time", "available_at", "time", "day", "month", "at"):
            continue
        if pd.api.types.is_numeric_dtype(frame[name]) and not pd.api.types.is_bool_dtype(frame[name]):
            frame[name] = frame[name].astype(float)
            frame.loc[future, name] = frame.loc[future, name] * rng.uniform(0.5, 1.5, int(future.sum()))
    return frame


def compare_at(full: pd.Series, other: pd.Series) -> list[str]:
    """Noms des valeurs qui diffèrent (présence ou valeur)."""
    out = []
    for name in full.index:
        a, b = full[name], other.get(name, np.nan)
        try:
            fa, fb = float(a), float(b)
        except (TypeError, ValueError):
            if a != b:
                out.append(str(name))
            continue
        if not (np.isclose(fa, fb, rtol=1e-9, atol=1e-12) or (math.isnan(fa) and math.isnan(fb))):
            out.append(str(name))
    return out
