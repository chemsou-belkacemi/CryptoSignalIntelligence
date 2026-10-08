"""Question décisive de la météo du marché : contraste par déviations orthogonales avant à jour de semaine égal,
placebo `P_T` causal (décalages de 7 à 84 jours, sans rotation circulaire), intervalle par tirage de fenêtres de
4 semaines, garde-fous et décisions (docs/METEO_MARCHE.md, § 4, 5.1 à 5.8, instrument corrigé le 2026-10-08 après
l'échec de N1 sur synthétique ; l'ancien instrument circulaire n'est gardé que pour sa mutation).

Données d'entrée d'une analyse : la couleur du feu de chaque jour de l'index calendaire (ROUGE / non rouge / trou) et le
résultat `y_d` du jour (NaN : trou). Un trou est un jour `SANS_FEU` ou sans résultat (B1d), ou un jour retiré par G2.
Rien ici ne lit de données : le même code sert aux contrôles synthétiques (§ 7.2) et aux exécutions réelles.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from numba import njit

from . import meteo as mt

SAMPLES = 10_000                     # tirages du placebo et de l'intervalle (§ 5.4, § 5.6)
DELTA_MIN = 0.15                     # marge d'équivalence en σ, candidat C validé le 2026-10-08 (§ 5.6)
ALPHA_SIDE = 0.025                   # niveau 0,05 bilatéral
CI_LEVEL = 0.90                      # intervalle de Δ_exc (deux tests unilatéraux à 0,05)
ALPHA_SIDE_C, CI_LEVEL_C = 0.0125, 0.95   # confirmation C : niveau 0,025 (§ 8)
WEEK = 7
BLOCK_DAYS = 91                      # blocs de 13 semaines commençant un lundi (§ 5.1)
MIN_LAST_DAYS = 28                   # un dernier bloc de moins de 4 semaines est fusionné avec le précédent
WINDOW_DAYS = 28                     # fenêtres du tirage stratifié (§ 5.6)
G1_RED_DAYS, G1_EPISODES, G1_BLOCKS = 60, 8, 6
G3_YEARS = 6
TIE = 1e-12                          # égalité numérique dans les p-valeurs (comptée du côté prudent)

PERSISTANCE = "PERSISTANCE_INFRA_TRIMESTRIELLE"
PERSISTANCE_FRAGILE = "PERSISTANCE_FRAGILE"
INVERSE = "INVERSE"
EQUIVALENT_NUL = "EQUIVALENT_NUL"
NON_CONCLUANT = "NON_CONCLUANT"
INSUFFISANT = "INSUFFISANT"
INSTRUMENT_TROP_FAIBLE = "INSTRUMENT_TROP_FAIBLE"
EQUIVALENCE_NON_JUGEABLE = "EQUIVALENCE_NON_JUGEABLE"
NON_IDENTIFIABLE = "NON_IDENTIFIABLE"
CONCLUSIVE = (PERSISTANCE, EQUIVALENT_NUL, INVERSE)


@dataclass(frozen=True)
class Period:
    name: str
    start: pd.Timestamp              # un lundi
    end: pd.Timestamp                # un dimanche (inclus)
    rule: str                        # "F6" (feu à 6) ou "R4" (FEU_R4)
    g4_years: tuple[int, ...]

    @property
    def days(self) -> pd.DatetimeIndex:
        return mt.day_index(self.start, self.end)


MAIN = Period("PRINCIPALE", pd.Timestamp("2020-02-03", tz="UTC"), pd.Timestamp("2025-06-22", tz="UTC"), "F6", (2022,))
VARIANT = Period("VARIANTE_2018", pd.Timestamp("2018-03-05", tz="UTC"), pd.Timestamp("2025-06-22", tz="UTC"), "R4",
                 (2018, 2022))
VARIANT_ONLY = Period("SOUS_PERIODE_2018", pd.Timestamp("2018-03-05", tz="UTC"), pd.Timestamp("2020-02-02", tz="UTC"),
                      "R4", (2018,))


def blocks_of(n_days: int) -> list[tuple[int, int]]:
    """Blocs de 91 jours depuis le premier lundi ; un dernier bloc de moins de 28 jours est fusionné au précédent."""
    if n_days % WEEK:
        raise ValueError("la période doit compter des semaines entières")
    out = [(s, min(BLOCK_DAYS, n_days - s)) for s in range(0, n_days, BLOCK_DAYS)]
    if len(out) > 1 and out[-1][1] < MIN_LAST_DAYS:
        start, length = out[-2]
        out[-2:] = [(start, length + out[-1][1])]
    return out


def shifts(n: int, *, mutation: str | None = None) -> np.ndarray:
    """Rotations permises d'un bloc de n jours : 0 (feu observé), puis 7, 14, …, n − 7 (multiples de 7).
    Mutation `rotation_non_multiple_7` : pas d'un jour."""
    step = 1 if mutation == "rotation_non_multiple_7" else WEEK
    return np.arange(0, n, step)


@njit(cache=True)
def _block_kernel(red, hole, y, step):
    b_count, n = red.shape
    n_rot = (n + step - 1) // step
    c = np.full((b_count, n_rot), np.nan)
    n_r = np.zeros((b_count, n_rot), np.int64)
    n_nr = np.zeros((b_count, n_rot), np.int64)
    for b in range(b_count):
        for j in range(n_rot):
            u = j * step
            s_r, s_nr, k_r, k_nr = 0.0, 0.0, 0, 0
            for t in range(n):
                if hole[b, t]:
                    continue
                src = t - u
                if src < 0:
                    src += n
                if hole[b, src]:
                    continue
                if red[b, src]:
                    s_r += y[b, t]
                    k_r += 1
                else:
                    s_nr += y[b, t]
                    k_nr += 1
            n_r[b, j], n_nr[b, j] = k_r, k_nr
            if k_r > 0 and k_nr > 0:
                c[b, j] = s_nr / k_nr - s_r / k_r
    return c, n_r, n_nr


def block_matrix(red: np.ndarray, hole: np.ndarray, y: np.ndarray, *, mutation: str | None = None
                 ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Pour des séquences (B, n) d'un bloc : contraste `c(u)` = moyenne de y des non rouges − moyenne de y des rouges,
    pour chaque rotation u (colonnes, dans l'ordre de `shifts`), avec les nombres de jours rouges et non rouges
    mesurés. Couleur placebo du jour t = couleur du jour t − u (circulaire dans le bloc) ; une cible ou une source trou
    sort de la rotation."""
    red = np.ascontiguousarray(np.atleast_2d(red), dtype=np.bool_)
    hole = np.ascontiguousarray(np.atleast_2d(hole), dtype=np.bool_)
    y0 = np.ascontiguousarray(np.where(hole, 0.0, np.atleast_2d(y).astype(float)))
    step = int(shifts(red.shape[1], mutation=mutation)[1]) if red.shape[1] > 1 else WEEK
    return _block_kernel(red, hole, y0, step)


# --- Ancien instrument (rotation circulaire), gardé pour la mutation « placebo_circulaire » ------------------------
# Il lit le futur : un jour du début du bloc reçoit la couleur d'un jour plus tardif, qui dépend de son propre y
# (échec de N1 le 2026-10-08, synthétique seulement). Ne sert plus à aucune décision.

@dataclass
class CircularPrepared:
    """Contrastes de chaque bloc (rotation 0 = feu observé), blocs utiles (B1a) et poids fixes `ω_b`."""
    blocks: list[tuple[int, int]]
    contrasts: list[np.ndarray]
    useful: np.ndarray
    weights: np.ndarray
    n_red: np.ndarray
    n_nonred: np.ndarray

    @property
    def total_weight(self) -> float:
        return float(self.weights.sum())

    def observed(self) -> np.ndarray:
        return np.array([c[0] if u else np.nan for c, u in zip(self.contrasts, self.useful, strict=True)])

    def rotation_mean(self) -> np.ndarray:
        return np.array([c[1:].mean() if u else np.nan for c, u in zip(self.contrasts, self.useful, strict=True)])

    def rotation_var(self) -> np.ndarray:
        return np.array([c[1:].var() if u else np.nan for c, u in zip(self.contrasts, self.useful, strict=True)])


def circular_prepare(red: np.ndarray, hole: np.ndarray, y: np.ndarray, blocks: list[tuple[int, int]], *,
            mutation: str | None = None) -> CircularPrepared:
    contrasts, useful, weights, n_red, n_nonred = [], [], [], [], []
    for start, length in blocks:
        sl = slice(start, start + length)
        c, n_r, n_nr = block_matrix(red[sl], hole[sl], y[sl], mutation=mutation)
        ok = bool((n_r[0] > 0).all() and (n_nr[0] > 0).all())
        contrasts.append(c[0])
        useful.append(ok)
        r0, nr0 = int(n_r[0, 0]), int(n_nr[0, 0])
        n_red.append(r0)
        n_nonred.append(nr0)
        weights.append(r0 * nr0 / (r0 + nr0) if ok else 0.0)
    return CircularPrepared(blocks, contrasts, np.array(useful, bool), np.array(weights, float), np.array(n_red),
                    np.array(n_nonred))


def circular_excess(prep: CircularPrepared, chosen: np.ndarray | None = None) -> tuple[float, float, float]:
    """(Δ̂, moyenne exacte du placebo, Δ_exc) sur les blocs utiles (ou sur `chosen`, sous-ensemble des utiles)."""
    keep = prep.useful if chosen is None else prep.useful & chosen
    w = np.where(keep, prep.weights, 0.0)
    if w.sum() <= 0:
        return np.nan, np.nan, np.nan
    observed = np.nansum(w * np.nan_to_num(prep.observed())) / w.sum()
    placebo = np.nansum(w * np.nan_to_num(prep.rotation_mean())) / w.sum()
    return float(observed), float(placebo), float(observed - placebo)


def circular_sd(prep: CircularPrepared) -> float:
    w = np.where(prep.useful, prep.weights, 0.0)
    if w.sum() <= 0:
        return np.nan
    return float(np.sqrt(np.nansum(w ** 2 * np.nan_to_num(prep.rotation_var()))) / w.sum())


def circular_draws(prep: CircularPrepared, rng: np.random.Generator, samples: int = SAMPLES) -> np.ndarray:
    """Δ̂ des tirages `P_T` : une rotation non nulle par bloc utile, tirée uniformément et indépendamment."""
    w = np.where(prep.useful, prep.weights, 0.0)
    total = np.zeros(samples)
    for c, weight, ok in zip(prep.contrasts, w, prep.useful, strict=True):
        if ok:
            total += weight * c[1:][rng.integers(0, len(c) - 1, samples)]
    return total / w.sum() if w.sum() > 0 else np.full(samples, np.nan)


def circular_positions(n: int, rng: np.random.Generator, samples: int) -> np.ndarray:
    """Positions des jours d'une réplique d'un bloc : fenêtres de 28 jours consécutifs (circulaires dans le bloc)
    commençant un lundi, mises bout à bout jusqu'à la longueur du bloc."""
    k = -(-n // WINDOW_DAYS)
    starts = rng.integers(0, n // WEEK, size=(samples, k)) * WEEK
    pos = (starts[:, :, None] + np.arange(WINDOW_DAYS)[None, None, :]) % n
    return pos.reshape(samples, k * WINDOW_DAYS)[:, :n]


@dataclass
class CircularBootstrap:
    excess: np.ndarray               # Δ_exc de chaque réplique (NaN : aucun bloc défini)
    dropped: np.ndarray              # par bloc : répliques où il perd une couleur (retiré de la réplique)
    undefined: int


def circular_bootstrap(red: np.ndarray, hole: np.ndarray, y: np.ndarray, prep: CircularPrepared, rng: np.random.Generator,
              samples: int = SAMPLES, *, chunk: int = 2500) -> CircularBootstrap:
    """Intervalle de Δ_exc (§ 5.6) : tirage stratifié dans chaque bloc utile, mêmes poids ; un bloc qui perd une
    couleur dans la réplique ou dans l'une de ses rotations est retiré de la réplique (B1b) ; la moyenne du placebo
    est recalculée dans chaque réplique (m2)."""
    num = np.zeros(samples)
    den = np.zeros(samples)
    dropped = np.zeros(len(prep.blocks), int)
    for b, ((start, length), ok, weight) in enumerate(zip(prep.blocks, prep.useful, prep.weights, strict=True)):
        if not ok:
            continue
        sl = slice(start, start + length)
        r_b, h_b, y_b = red[sl], hole[sl], y[sl]
        for lo in range(0, samples, chunk):
            hi = min(samples, lo + chunk)
            pos = circular_positions(length, rng, hi - lo)
            c, n_r, n_nr = block_matrix(r_b[pos], h_b[pos], y_b[pos])
            defined = (n_r > 0).all(axis=1) & (n_nr > 0).all(axis=1)
            dropped[b] += int((~defined).sum())
            value = np.where(defined, c[:, 0] - np.nanmean(np.where(defined[:, None], c[:, 1:], 0.0), axis=1), 0.0)
            num[lo:hi] += weight * value
            den[lo:hi] += np.where(defined, weight, 0.0)
    excess = np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan)
    return CircularBootstrap(excess, dropped, int((den <= 0).sum()))


def holes_of(color: np.ndarray, y: np.ndarray) -> np.ndarray:
    return (np.asarray(color) == mt.SANS_FEU) | ~np.isfinite(np.asarray(y, float))


def block_years(period: Period, blocks: list[tuple[int, int]]) -> np.ndarray:
    return np.array([(period.start + start * mt.DAY).year for start, _ in blocks])


@dataclass
class Analysis:
    delta_hat: float
    placebo_mean: float
    delta_exc: float
    placebo_sd: float
    z: float
    p_high: float
    p_low: float
    noise: tuple[float, float]
    ci: tuple[float, float]
    ci_level: float
    useful_blocks: int
    red_days: int
    episodes: int
    dropped: list[int]
    undefined_replicas: int
    guards: dict = field(default_factory=dict)
    by_year: dict = field(default_factory=dict)
    verdict: str = ""
    equivalence: bool = False
    note: str = ""


# --- Instrument corrigé (2026-10-08) : déviations orthogonales avant, placebo causal ---------------------------------

LAGS = tuple(range(WEEK, 85, WEEK))          # u ∈ {7, 14, …, 84} : décalages du placebo causal


def placebo_lags(*, mutation: str | None = None) -> tuple[int, ...]:
    """Décalages du placebo `P_T` causal ; mutation `rotation_non_multiple_7` : pas d'un jour."""
    return tuple(range(1, 85)) if mutation == "rotation_non_multiple_7" else LAGS


@njit(cache=True)
def _forward_dev(values, valid, starts, lengths):
    """Déviation orthogonale avant (Arellano-Bover) de chaque jour valide `t` d'un bloc : √(k/(k+1)) · (v_t − moyenne
    de v sur F_t), F_t = jours s > t du même bloc et du même jour de semaine, valides ; k = |F_t|. Un jour sans F_t
    n'est pas un terme."""
    n = len(values)
    dev = np.zeros(n)
    term = np.zeros(n, np.bool_)
    for b in range(len(starts)):
        s0, length = starts[b], lengths[b]
        sums = np.zeros(7)
        counts = np.zeros(7)
        for t in range(s0 + length - 1, s0 - 1, -1):
            if not valid[t]:
                continue
            wd = (t - s0) % 7
            k = counts[wd]
            if k > 0:
                dev[t] = np.sqrt(k / (k + 1.0)) * (values[t] - sums[wd] / k)
                term[t] = True
            sums[wd] += values[t]
            counts[wd] += 1.0
    return dev, term


@dataclass
class Prepared:
    """Termes du contraste par bloc : numérateur Σ r·ỹ et dénominateur Σ r·r̃ du feu observé (`num0`, `den0`) et de
    chaque décalage du placebo causal (`num`, `den` : blocs × décalages), blocs utiles, termes pour l'intervalle."""
    blocks: list[tuple[int, int]]
    num0: np.ndarray
    den0: np.ndarray
    num: np.ndarray
    den: np.ndarray
    useful: np.ndarray
    n_red: np.ndarray
    a_terms: np.ndarray                       # r_t · ỹ_t (0 hors terme)
    b_terms: np.ndarray                       # r_t · r̃_t


def prepare(red: np.ndarray, hole: np.ndarray, y: np.ndarray, blocks: list[tuple[int, int]], *,
            mutation: str | None = None) -> Prepared:
    red = np.asarray(red, bool)
    hole = np.asarray(hole, bool)
    y0 = np.where(hole, 0.0, np.asarray(y, float))
    starts = np.array([s for s, _ in blocks], np.int64)
    lengths = np.array([n for _, n in blocks], np.int64)
    valid = ~hole
    y_dev, term = _forward_dev(y0, valid, starts, lengths)
    r = red.astype(float)
    r_dev, _ = _forward_dev(r, valid, starts, lengths)
    a_terms = np.where(term, r * y_dev, 0.0)
    b_terms = np.where(term, r * r_dev, 0.0)
    lags = placebo_lags(mutation=mutation)
    n = len(red)
    num = np.zeros((len(blocks), len(lags)))
    den = np.zeros_like(num)
    idx = np.arange(n)
    for j, u in enumerate(lags):
        src = idx - u
        ok = (src >= 0) & ~hole[np.maximum(src, 0)]          # source avant la période ou sur un trou : cible = trou
        rp = np.where(ok, red[np.maximum(src, 0)], False).astype(float)
        valid_p = valid & ok
        rp_dev, term_p = _forward_dev(rp, valid_p, starts, lengths)
        use = term & term_p
        for b, (s0, length) in enumerate(blocks):
            sl = slice(s0, s0 + length)
            num[b, j] = float((rp[sl] * y_dev[sl])[use[sl]].sum())
            den[b, j] = float((rp[sl] * rp_dev[sl])[use[sl]].sum())
    num0 = np.array([a_terms[s:s + n_].sum() for s, n_ in blocks])
    den0 = np.array([b_terms[s:s + n_].sum() for s, n_ in blocks])
    useful = np.array([bool((term[s:s + n_] & red[s:s + n_]).any() and (term[s:s + n_] & ~red[s:s + n_]).any())
                       for s, n_ in blocks])
    n_red = np.array([int((term[s:s + n_] & red[s:s + n_]).sum()) for s, n_ in blocks])
    return Prepared(blocks, num0, den0, num, den, useful, n_red, a_terms, b_terms)


def estimate(prep: Prepared, chosen: np.ndarray | None = None) -> float:
    """Δ̂ = −Σ r·ỹ / Σ r·r̃ sur les blocs utiles (ou sur `chosen` parmi eux) ; NaN si Σ r·r̃ = 0 (non identifiable)."""
    keep = prep.useful if chosen is None else prep.useful & chosen
    den = float(prep.den0[keep].sum())
    return -float(prep.num0[keep].sum()) / den if den > 0 else float("nan")


def placebo_draws(prep: Prepared, rng: np.random.Generator, samples: int = SAMPLES) -> np.ndarray:
    """Δ̂ des tirages `P_T` causaux : un décalage par bloc utile, tiré uniformément et indépendamment, mêmes ỹ."""
    num = np.zeros(samples)
    den = np.zeros(samples)
    for b in np.flatnonzero(prep.useful):
        pick = rng.integers(0, prep.num.shape[1], samples)
        num += prep.num[b, pick]
        den += prep.den[b, pick]
    return np.where(den > 0, -num / np.where(den > 0, den, 1.0), np.nan)


def replica_positions(n: int, rng: np.random.Generator, samples: int) -> np.ndarray:
    """Jours d'une réplique d'un bloc : fenêtres de 28 jours consécutifs commençant un lundi, SANS rotation
    circulaire (départs 0, 7, …, n − 28), mises bout à bout jusqu'à la longueur du bloc."""
    k = -(-n // WINDOW_DAYS)
    starts = rng.integers(0, (n - WINDOW_DAYS) // WEEK + 1, size=(samples, k)) * WEEK
    pos = starts[:, :, None] + np.arange(WINDOW_DAYS)[None, None, :]
    return pos.reshape(samples, k * WINDOW_DAYS)[:, :n]


def bootstrap(prep: Prepared, rng: np.random.Generator, samples: int = SAMPLES) -> np.ndarray:
    """Δ̂ de chaque réplique : termes (r, r̃, ỹ) calculés une fois sur la série d'origine, tirés par fenêtres de
    4 semaines dans chaque bloc utile, rapport recalculé."""
    num = np.zeros(samples)
    den = np.zeros(samples)
    for b in np.flatnonzero(prep.useful):
        start, length = prep.blocks[b]
        pos = start + replica_positions(length, rng, samples)
        num += prep.a_terms[pos].sum(axis=1)
        den += prep.b_terms[pos].sum(axis=1)
    return np.where(den > 0, -num / np.where(den > 0, den, 1.0), np.nan)


def guards(color: np.ndarray, y: np.ndarray, period: Period, prep: Prepared, *, excess: float, with_g2: bool = True,
           mutation: str | None = None) -> tuple[dict, dict]:
    """G1 (minimums : toute issue), G2 (sans le meilleur épisode rouge, ses jours en trous, F_t recalculés), G3
    (régularité : au moins 4 années sur 6, ou ⌈2/3⌉ des années utiles quand il y en a plus de 6), G4 (années
    baissières). G2 à G4 ne servent qu'à la persistance ; G2 n'est calculé que si la supériorité est atteinte."""
    blocks = prep.blocks
    eps = mt.episodes(color)
    red_days = int(prep.n_red[prep.useful].sum())
    g1 = red_days >= G1_RED_DAYS and len(eps) >= G1_EPISODES and int(prep.useful.sum()) >= G1_BLOCKS
    red = color == mt.ROUGE
    worst, worst_episode = np.inf, None
    for start, end in (eps if with_g2 else []):
        hole = holes_of(color, y)
        hole[start:end + 1] = True
        value = estimate(prepare(red, hole, y, blocks, mutation=mutation))
        value = -np.inf if not np.isfinite(value) else value
        if value < worst:
            worst, worst_episode = value, (start, end)
    g2 = (bool(eps) and worst > 0) if with_g2 else None
    years = block_years(period, blocks)
    by_year = {}
    for year in sorted(set(years.tolist())):
        if ((years == year) & prep.useful).any():
            by_year[int(year)] = estimate(prep, years == year)
    positive = sum(1 for v in by_year.values() if v > 0)
    count = len(by_year)
    needed = -(-2 * count // 3) if count >= G3_YEARS else count // 2 + 1      # 4 sur 6, 6 sur 8
    g3 = positive >= needed
    g4 = all(by_year.get(year, np.nan) > 0 for year in period.g4_years)
    return ({"G1": bool(g1), "G2": g2, "G3": bool(g3), "G4": bool(g4), "red_days": red_days,
             "episodes": len(eps), "useful_blocks": int(prep.useful.sum()),
             "g2_excess": None if not np.isfinite(worst) else float(worst),
             "g2_episode": worst_episode, "g3_positive_years": positive, "g3_needed": needed,
             "excess": excess}, by_year)


def _circular_analyse(color: np.ndarray, y: np.ndarray, *, seed: int, samples: int) -> Analysis:
    """Ancien instrument (mutation « placebo_circulaire ») : Δ_exc contre la rotation circulaire, z et p-valeurs."""
    red = color == mt.ROUGE
    hole = holes_of(color, y)
    prep = circular_prepare(red, hole, y, blocks_of(len(color)))
    observed, placebo, excess = circular_excess(prep)
    sd = circular_sd(prep)
    draws = circular_draws(prep, np.random.default_rng([seed, 0]), samples)
    p_high = (1 + int((draws >= observed - TIE).sum())) / (samples + 1)
    p_low = (1 + int((draws <= observed + TIE).sum())) / (samples + 1)
    return Analysis(observed, placebo, excess, sd, excess / sd if sd > 0 else np.nan, p_high, p_low,
                    (np.nan, np.nan), (np.nan, np.nan), CI_LEVEL, int(prep.useful.sum()),
                    int(prep.n_red[prep.useful].sum()), len(mt.episodes(color)), [], 0)


def analyse(color: np.ndarray, y: np.ndarray, period: Period, *, seed: int = mt.SEED, samples: int = SAMPLES,
            boot_samples: int | None = None, ci_level: float = CI_LEVEL, with_guards: bool = True,
            mutation: str | None = None) -> Analysis:
    """Δ̂ par déviations orthogonales avant (Δ_exc := Δ̂), placebo causal `P_T` (rangs, moyenne rapportée comme
    contrôle), intervalle par tirage de fenêtres de 4 semaines dans chaque bloc."""
    color = np.asarray(color)
    y = np.asarray(y, float)
    if mutation == "placebo_circulaire":
        return _circular_analyse(color, y, seed=seed, samples=samples)
    blocks = blocks_of(len(color))
    red = color == mt.ROUGE
    hole = holes_of(color, y)
    prep = prepare(red, hole, y, blocks, mutation=mutation)
    observed = estimate(prep)
    rng_draw, rng_boot = (np.random.default_rng([seed, k]) for k in (0, 1))
    draws = placebo_draws(prep, rng_draw, samples)
    finite_draws = draws[np.isfinite(draws)]
    placebo = float(finite_draws.mean()) if len(finite_draws) else np.nan
    sd = float(finite_draws.std()) if len(finite_draws) > 1 else np.nan
    if np.isfinite(observed) and len(finite_draws):
        p_high = (1 + int((finite_draws >= observed - TIE).sum())) / (len(finite_draws) + 1)
        p_low = (1 + int((finite_draws <= observed + TIE).sum())) / (len(finite_draws) + 1)
        centered = finite_draws - placebo
        noise = (float(np.percentile(centered, 2.5)), float(np.percentile(centered, 97.5)))
    else:
        p_high = p_low = np.nan
        noise = (np.nan, np.nan)
    replicas = bootstrap(prep, rng_boot, boot_samples or samples)
    tail = (1 - ci_level) / 2
    finite = replicas[np.isfinite(replicas)]
    ci = ((float(np.quantile(finite, tail)), float(np.quantile(finite, 1 - tail))) if len(finite) and np.isfinite(observed)
          else (np.nan, np.nan))
    out = Analysis(observed, placebo, observed, sd, observed / sd if np.isfinite(sd) and sd > 0 else np.nan,
                   p_high, p_low, noise, ci, ci_level, int(prep.useful.sum()), int(prep.n_red[prep.useful].sum()),
                   len(mt.episodes(color)), [], int((~np.isfinite(replicas)).sum()))
    if with_guards:
        out.guards, out.by_year = guards(color, y, period, prep, excess=observed, mutation=mutation,
                                         with_g2=bool(np.isfinite(p_high) and p_high <= ALPHA_SIDE))
    return out


def decide(a: Analysis, *, delta_min: float = DELTA_MIN, alpha_side: float = ALPHA_SIDE,
           equivalence_judgeable: bool = True) -> Analysis:
    """Résultat de la question (§ 5.6). G1 manquant : `INSUFFISANT`. Supériorité (`p_haut` ≤ α) avec G1 à G4 :
    `PERSISTANCE_INFRA_TRIMESTRIELLE`, sinon `PERSISTANCE_FRAGILE` ; infériorité : `INVERSE` ; intervalle entièrement
    dans [−Δ_min ; +Δ_min] (si l'équivalence est jugeable) : `EQUIVALENT_NUL` ; sinon `NON_CONCLUANT`."""
    g = a.guards
    lo, hi = a.ci
    if not np.isfinite(a.delta_hat):
        a.verdict, a.equivalence = NON_IDENTIFIABLE, False          # Σ r·r̃ = 0 : aucun verdict
        return a
    a.equivalence = bool(equivalence_judgeable and np.isfinite(lo) and np.isfinite(hi)
                         and lo >= -delta_min and hi <= delta_min)
    if not g.get("G1", False):
        a.verdict = INSUFFISANT
    elif a.p_high <= alpha_side:
        a.verdict = PERSISTANCE if all(g.get(k, False) for k in ("G2", "G3", "G4")) else PERSISTANCE_FRAGILE
        if a.equivalence and a.verdict == PERSISTANCE:
            a.note = "effet présent mais inférieur à Δ_min, sans usage pratique"
    elif a.p_low <= alpha_side:
        a.verdict = INVERSE
    elif a.equivalence:
        a.verdict = EQUIVALENT_NUL
    else:
        a.verdict = NON_CONCLUANT
    if not equivalence_judgeable and a.verdict == NON_CONCLUANT:
        a.note = EQUIVALENCE_NON_JUGEABLE
    return a


def variant_hypothesis(main_verdict: str) -> str | None:
    """Hypothèse testée par la variante (procédure séquentielle, § 5.8) : celle de l'issue de la principale."""
    return {PERSISTANCE: "superiorite", EQUIVALENT_NUL: "equivalence", INVERSE: "inferiorite"}.get(main_verdict)


def judge_variant(a: Analysis, main_verdict: str, *, delta_min: float = DELTA_MIN,
                  equivalence_judgeable: bool = True) -> dict:
    """Issue de la variante pour l'hypothèse fixée par la principale ; décrite seulement sinon."""
    hypothesis = variant_hypothesis(main_verdict)
    if hypothesis is None:
        return {"hypothesis": None, "reached": None, "described": True}
    decide(a, delta_min=delta_min, equivalence_judgeable=equivalence_judgeable)
    reached = {"superiorite": a.verdict == PERSISTANCE, "inferiorite": a.verdict == INVERSE,
               "equivalence": a.equivalence and equivalence_judgeable}[hypothesis]
    return {"hypothesis": hypothesis, "reached": bool(reached), "described": False}


GLOBAL_TEXT = {
    (PERSISTANCE, True): "Piste ; la version réduite tient aussi avec 2018. Confirmation en direct.",
    (PERSISTANCE, False): "Piste fragile : la version réduite ne la retrouve pas avec 2018",
    (EQUIVALENT_NUL, True): "Les jours rouges ne sont pas pires de plus de Δ_min, et la version réduite non plus avec 2018",
    (EQUIVALENT_NUL, False): "Pas pires de plus de Δ_min sur 2020-2025 ; pas établi pour la version réduite avec 2018",
    (INVERSE, True): "Nuisible ; la version réduite aussi avec 2018",
    (INVERSE, False): "Nuisible sur 2020-2025 ; pas retrouvé pour la version réduite avec 2018",
}
CONFIRMATION_TEXT = {
    (PERSISTANCE, True): "Piste, retrouvée hors des 40 survivantes",
    (PERSISTANCE, False): "Piste non retrouvée hors des 40 survivantes (puissance plus faible)",
    (EQUIVALENT_NUL, True): "pas pires de plus de Δ_min, aussi hors des 40 survivantes",
    (EQUIVALENT_NUL, False): "équivalence non retrouvée hors des 40 survivantes (puissance plus faible)",
    (INVERSE, True): "nuisible, aussi hors des 40 survivantes",
    (INVERSE, False): "nuisible, non retrouvé hors des 40 survivantes (puissance plus faible)",
}


def global_verdict(main: str, variant: dict | None, *, equivalence_judgeable: bool = True) -> str:
    """Verdict global (tableau du § 5.7)."""
    if main == INSTRUMENT_TROP_FAIBLE:
        return "Rien n'est exécuté ni compté (ni la variante, ni la confirmation C)"
    if main in CONCLUSIVE:
        if variant is None or variant.get("status") == INSTRUMENT_TROP_FAIBLE:
            return f"{main} (verdict de la principale seule ; variante ni exécutée ni comptée)"
        return GLOBAL_TEXT[(main, bool(variant.get("reached")))]
    if not equivalence_judgeable:
        return ("Ni démontré ni exclu, avec la borne haute (« pas pires de plus de X % par jour ») ; on ne peut pas "
                "dire « inutile »")
    return "Ni démontré ni exclu"


def confirmation_mention(main: str, confirmation: str | None) -> str | None:
    if confirmation is None or main not in CONCLUSIVE:
        return None
    return CONFIRMATION_TEXT[(main, confirmation == main)]


def upper_bound_text(a: Analysis, *, sigma_median: float | None, pct_ci: tuple[float, float] | None = None,
                     r_unit_pct: float | None = None) -> dict:
    """Borne haute (et basse) de l'intervalle de Δ_exc, toujours rapportée : en σ, en % par jour pour l'altcoin
    médiane réelle du panier, en % brut non normalisé et en R (§ 5.6)."""
    lo, hi = a.ci
    out: dict = {"sigma": {"haute": hi, "basse": lo}}
    if sigma_median is not None and np.isfinite(sigma_median):
        out["pct_par_jour"] = {"haute": hi * sigma_median * 100, "basse": lo * sigma_median * 100}
        if r_unit_pct:
            out["R"] = {"haute": hi * sigma_median * 100 / r_unit_pct, "basse": lo * sigma_median * 100 / r_unit_pct}
    if pct_ci is not None:
        out["pct_brut"] = {"haute": pct_ci[1] * 100, "basse": pct_ci[0] * 100}
    out["phrase"] = (f"les jours rouges ne sont pas pires de plus de {out['pct_par_jour']['haute']:.2f} % par jour"
                     if "pct_par_jour" in out and np.isfinite(hi) else None)
    return out
