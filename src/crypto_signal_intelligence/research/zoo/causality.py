"""Causalité du zoo (docs/ZOO.md § 7) : signaux et poids recalculés avec les seules données antérieures, puis avec
un futur falsifié, à au moins 50 instants tirés au hasard et à TOUS les instants de signal d'un échantillon de 3 paires
× 1 an (graine fixe) : ils doivent être identiques au calcul complet.

Recalcul tronqué à l'instant τ : bougies 1 h dont l'`available_at` ≤ τ (puis agrégation : un bloc 4 h ou journalier
incomplet n'existe pas), séries externes dont l'`available_at` ≤ τ. Comparaison : les indicateurs d'entrée et de sortie
de TOUTES les bougies de décision ≤ τ (indexées par leur instant de décision) doivent être identiques, et l'ensemble de
ces bougies aussi. Futur falsifié : les bougies 1 h postérieures à τ sont remplacées par une marche aléatoire.

Mutations exigées (chacune doit être DÉTECTÉE par ce contrôle, tests/test_zoo_causality.py et `csi zoo causalite`) :
1. un signal qui lit la clôture de la bougie suivante ;
2. une bougie 4 h ou journalière datée par son ouverture au lieu de l'`available_at` de sa dernière bougie 1 h ;
3. une série externe jointe sur sa date au lieu de son `available_at` ;
4. un pivot daté à son extrême au lieu de sa confirmation.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np
import pandas as pd

from . import indicators as ind
from .data import DAY_H, HOUR_NS, LATENCE_NS, Bougies, Etape, Paire, SerieExterne, agreger, en_ns, joindre
from .spec import Contexte, Panneau, Poids, Signaux, Test

Agregation = Callable[[Bougies, int], Bougies]
Strategie = Callable[[Bougies, Contexte], Signaux]
INSTANTS_MIN = 50
GRAINE = 20261012


@dataclass
class Rapport:
    test_id: str
    instants: int = 0
    instants_signal: int = 0
    ecarts_tronque: list[str] = field(default_factory=list)
    ecarts_falsifie: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.ecarts_tronque and not self.ecarts_falsifie

    def resume(self) -> dict:
        return {"test": self.test_id, "instants": self.instants, "instants_signal": self.instants_signal,
                "ecarts_tronque": len(self.ecarts_tronque), "ecarts_falsifie": len(self.ecarts_falsifie),
                "exemples": (self.ecarts_tronque + self.ecarts_falsifie)[:5], "ok": self.ok}


def tronquer_h1(h1: Bougies, tau: int) -> Bougies:
    return h1.filtre(h1.disponible <= tau)


def falsifier_h1(h1: Bougies, tau: int, rng: np.random.Generator) -> Bougies:
    """Bougies postérieures à τ remplacées par une marche aléatoire (prix positifs, mêmes heures)."""
    after = h1.disponible > tau
    if not after.any():
        return h1
    k = int(after.sum())
    last = h1.cloture[~after][-1] if (~after).any() else 100.0
    path = last * np.exp(np.cumsum(rng.normal(0.0, 0.03, k)))
    opens = np.r_[last, path[:-1]]
    high = np.maximum(opens, path) * (1 + np.abs(rng.normal(0, 0.01, k)))
    low = np.minimum(opens, path) * (1 - np.minimum(np.abs(rng.normal(0, 0.01, k)), 0.5))
    vol = np.abs(rng.normal(1e6, 5e5, k)) + 1.0

    def mix(old: np.ndarray, new: np.ndarray) -> np.ndarray:
        out = old.copy()
        out[after] = new
        return out

    return replace(h1, ouverture=mix(h1.ouverture, opens), haut=mix(h1.haut, high), bas=mix(h1.bas, low),
                   cloture=mix(h1.cloture, path), volume=mix(h1.volume, vol), achat=mix(h1.achat, vol * 0.5),
                   transactions=mix(h1.transactions, vol / 100))


def tronquer_serie(serie: SerieExterne, tau: int) -> SerieExterne:
    keep = serie.disponible <= tau
    return SerieExterne(serie.nom, serie.disponible[keep], serie.date[keep], serie.valeur[keep])


def falsifier_serie(serie: SerieExterne, tau: int, rng: np.random.Generator) -> SerieExterne:
    """Valeurs publiées après τ remplacées par du bruit (mêmes dates, mêmes instants de publication)."""
    after = serie.disponible > tau
    if not after.any():
        return serie
    scale = float(np.nanstd(serie.valeur)) or 1.0
    values = serie.valeur.copy()
    values[after] = rng.normal(float(np.nanmean(serie.valeur)), 3 * scale, int(after.sum()))
    return SerieExterne(serie.nom, serie.disponible, serie.date, values)


#: Calendriers DÉCLARÉS connus à l'avance (docs/ZOO.md § 2 : FOMC, CPI, NFP publiés l'année précédente ; phases
#: lunaires calculables) : pour la causalité, un événement est réputé connu `HORIZON_CALENDRIERS` avant son heure
#: (horizon prudent, bien plus court que l'année déclarée ; les règles CA lisent 24 h d'avance au plus).
CALENDRIERS_CONNUS = frozenset({"calendrier_macro", "calendrier_crypto"})
HORIZON_CALENDRIERS_NS = 7 * DAY_H * HOUR_NS


def _connu_le(name: str, table: pd.DataFrame) -> np.ndarray | None:
    """Instant (ns) où chaque ligne d'une table d'événements est connue ; None : table sans horodatage."""
    if len(table) == 0:
        return None
    if name in CALENDRIERS_CONNUS and "event_time" in table:
        return en_ns(table["event_time"]) - HORIZON_CALENDRIERS_NS
    if "available_at" in table:
        return en_ns(table["available_at"])
    if "event_time" in table:
        return en_ns(table["event_time"])
    return None


def tronquer_evenements(evenements, tau: int) -> dict[str, pd.DataFrame]:
    """Lignes connues à τ seulement (annonces : `available_at` ≤ τ ; calendriers : heure − horizon ≤ τ)."""
    out = {}
    for name, table in evenements.items():
        known = _connu_le(name, table)
        out[name] = table if known is None else table[known <= tau].reset_index(drop=True)
    return out


def falsifier_evenements(evenements, tau: int, rng: np.random.Generator) -> dict[str, pd.DataFrame]:
    """Futur falsifié : les lignes connues après τ sont décalées d'un nombre de jours tiré au hasard (1 à 30)."""
    out = {}
    for name, table in evenements.items():
        known = _connu_le(name, table)
        if known is None or not (known > tau).any():
            out[name] = table
            continue
        future = known > tau
        days = np.zeros(len(table), dtype=np.int64)
        days[future] = rng.integers(1, 31, int(future.sum()))
        shift = pd.to_timedelta(days, unit="D")
        fake = table.copy().reset_index(drop=True)
        for column in ("event_time", "available_at"):
            if column in fake:
                fake[column] = pd.to_datetime(fake[column], utc=True) + shift
        if "key" in fake and name in CALENDRIERS_CONNUS:
            fake.loc[future, "key"] = pd.to_datetime(fake.loc[future, "event_time"], utc=True).dt.strftime("%Y-%m-%d")
        out[name] = fake
    return out


def quotidien_coupe(frame_h1: Bougies, frame: Bougies, agregation: Agregation) -> tuple[Bougies, np.ndarray]:
    """Bougies journalières coupées à la dernière décision du cadre et dernière journée connue à chaque décision
    (même règle que `run.contexte`)."""
    daily = _cadre(frame_h1, DAY_H, agregation)
    if len(frame):
        daily = daily.filtre(daily.disponible <= frame.disponible[-1])
    known = np.searchsorted(daily.disponible, frame.disponible, side="right").astype(np.int64) - 1
    return daily, known


def _cadre(h1: Bougies, unit: int, agregation: Agregation) -> Bougies:
    return h1 if unit == 1 else agregation(h1, unit)


def _flags(strategie: Strategie, bars: Bougies, ctx: Contexte) -> dict[int, tuple[bool, bool]]:
    out = strategie(bars, ctx)
    exit_flags = out.sortie if out.sortie is not None else np.zeros(len(bars), dtype=bool)
    return {int(t): (bool(a), bool(b)) for t, a, b in zip(bars.disponible, out.entree, exit_flags, strict=True)}


def verifier(strategie: Strategie, h1: Bougies, unit: int, ctx: Contexte, instants: np.ndarray, *,
             agregation: Agregation = agreger, quotidien: bool = False, rng: np.random.Generator | None = None,
             test_id: str = "?", signal_instants: int = 0) -> Rapport:
    """Recalcul tronqué et futur falsifié aux `instants` ; écarts rapportés."""
    rng = rng or np.random.default_rng(GRAINE)
    report = Rapport(test_id, len(instants), signal_instants)

    def context(frame_h1: Bougies, tau: int | None, fake: bool = False) -> Contexte:
        if tau is None:
            externes, events = dict(ctx.externes), dict(ctx.evenements)
        elif fake:
            externes = {k: falsifier_serie(v, tau, rng) for k, v in ctx.externes.items()}
            events = falsifier_evenements(ctx.evenements, tau, rng)
        else:
            externes = {k: tronquer_serie(v, tau) for k, v in ctx.externes.items()}
            events = tronquer_evenements(ctx.evenements, tau)
        daily, known = (quotidien_coupe(frame_h1, _cadre(frame_h1, unit, agregation), agregation) if quotidien
                        else (None, None))
        return replace(ctx, externes=externes, evenements=events, quotidien=daily, quotidien_connu=known)

    full = _flags(strategie, _cadre(h1, unit, agregation), context(h1, None))
    for tau in np.asarray(instants, dtype=np.int64):
        tau = int(tau)
        expected = {t: v for t, v in full.items() if t <= tau}
        cut = tronquer_h1(h1, tau)
        got = _flags(strategie, _cadre(cut, unit, agregation), context(cut, tau)) if len(cut) else {}
        diff = _difference(expected, got)
        if diff:
            report.ecarts_tronque.append(f"τ={tau} : {diff}")
        fake = falsifier_h1(h1, tau, rng)
        got_fake = _flags(strategie, _cadre(fake, unit, agregation), context(fake, tau, fake=True))
        diff = _difference(expected, {t: v for t, v in got_fake.items() if t <= tau})
        if diff:
            report.ecarts_falsifie.append(f"τ={tau} : {diff}")
    return report


def _difference(expected: dict[int, tuple[bool, bool]], got: dict[int, tuple[bool, bool]]) -> str:
    missing = set(expected) ^ set(got)
    if missing:
        return f"bougies de décision différentes ({len(missing)})"
    wrong = [t for t in expected if expected[t] != got[t]]
    return f"{len(wrong)} décisions différentes" if wrong else ""


def instants_aleatoires(bars: Bougies, etape: Etape, n: int, rng: np.random.Generator) -> np.ndarray:
    pool = bars.disponible[(bars.disponible >= etape.debut_ns) & (bars.disponible <= etape.fin_ns)]
    if len(pool) == 0:
        return np.zeros(0, dtype=np.int64)
    return np.sort(rng.choice(pool, size=min(n, len(pool)), replace=False))


def _fronts(flags: np.ndarray) -> np.ndarray:
    """Instants où l'indicateur PASSE à vrai (déclencheur d'un événement ; bascule d'un état)."""
    out = flags.copy()
    out[1:] &= ~flags[:-1]
    return out


def instants_de_signal(strategie: Strategie, bars: Bougies, ctx: Contexte, debut_ns: int, fin_ns: int) -> np.ndarray:
    """Instants de signal : passages à vrai de l'entrée ou de la sortie (pour une règle d'état, ses bascules ; une
    décision d'entrée prise pendant un état déjà vrai ne fait que répéter la bascule, contrôlée elle aussi)."""
    out = strategie(bars, ctx)
    flags = _fronts(out.entree)
    if out.sortie is not None:
        flags |= _fronts(out.sortie)
    keep = flags & (bars.disponible >= debut_ns) & (bars.disponible <= fin_ns)
    return bars.disponible[keep]


def verifier_test(test: Test, paires: list[Paire], etape: Etape, ctx_de: Callable[[Paire], Contexte], *,
                  n_aleatoires: int = INSTANTS_MIN, echantillon: int = 3, graine: int = GRAINE,
                  strategie: Strategie | None = None, agregation: Agregation = agreger) -> Rapport:
    """Contrôle d'un test : `n_aleatoires` instants au hasard (paires tirées), plus tous les instants de signal d'un
    échantillon de `echantillon` paires sur la 1re année complète de l'étape."""
    rng = np.random.default_rng([graine, sum(map(ord, test.id))])
    fn = strategie or test.strategie_fonction()
    unit = test.execution_heures
    usable = [p for p in paires if len(p.h1) > 0]
    total = Rapport(test.id)
    if not usable:
        return total
    year_start = etape.debut_ns
    year_end = year_start + 365 * DAY_H * HOUR_NS
    # échantillon : paires dont l'historique couvre toute l'année contrôlée (sinon, les plus anciennes)
    covering = [i for i, p in enumerate(usable) if p.h1.disponible[0] <= year_start and p.h1.disponible[-1] >= year_end]
    pool = covering if len(covering) >= echantillon else sorted(range(len(usable)),
                                                                key=lambda i: usable[i].h1.disponible[0])[:echantillon]
    picks = rng.choice(pool, size=min(echantillon, len(pool)), replace=False)
    per_pair: dict[int, list[int]] = {}
    for _ in range(n_aleatoires):
        per_pair.setdefault(int(rng.integers(len(usable))), []).append(0)
    jobs: list[tuple[Paire, np.ndarray, int]] = []
    for idx, draws in per_pair.items():
        pair = usable[idx]
        bars = _cadre(pair.h1, unit, agregation)
        jobs.append((pair, instants_aleatoires(bars, etape, len(draws), rng), 0))
    for idx in picks:
        pair = usable[int(idx)]
        bars = _cadre(pair.h1, unit, agregation)
        signals = instants_de_signal(fn, bars, ctx_de(pair), year_start, year_end)
        jobs.append((pair, signals, len(signals)))
    for pair, instants, n_signal in jobs:
        rep = verifier(fn, pair.h1, unit, ctx_de(pair), instants, agregation=agregation,
                       quotidien=unit != DAY_H, rng=rng, test_id=test.id, signal_instants=n_signal)
        total.instants += rep.instants
        total.instants_signal += n_signal
        total.ecarts_tronque += [f"{pair.symbole} {e}" for e in rep.ecarts_tronque]
        total.ecarts_falsifie += [f"{pair.symbole} {e}" for e in rep.ecarts_falsifie]
    return total


def _lignes_poids(out: Poids, tau: int) -> dict[int, tuple]:
    """Tout ce qu'une décision ≤ τ produit : poids, univers de la référence, composantes A et B, « dans A »."""
    rows = {}
    for k, d in enumerate(out.decisions):
        if int(d) > tau:
            continue
        rows[int(d)] = tuple(None if arr is None else np.asarray(arr[k], dtype=float)
                             for arr in (out.poids, out.univers, out.composante_a, out.composante_b, out.dans_a))
    return rows


def _memes(a: dict[int, tuple], b: dict[int, tuple]) -> bool:
    if set(a) != set(b):
        return False
    for d in a:
        for x, y in zip(a[d], b[d], strict=True):
            if (x is None) != (y is None) or (x is not None and not np.allclose(x, y, equal_nan=True)):
                return False
    return True


def verifier_portefeuille(strategie: Callable[[Panneau, Contexte], Poids], board: Panneau, ctx: Contexte,
                          instants: np.ndarray, test_id: str = "?") -> Rapport:
    """Forme (b) : poids (et univers, composantes, « dans A ») recalculés sur le panneau tronqué aux journées connues
    à τ avec séries et événements coupés, puis avec un futur falsifié (tous les champs, séries, événements)."""
    rng = np.random.default_rng(GRAINE)
    report = Rapport(test_id, len(instants))
    full = strategie(board, ctx)
    fields = ("ouverture", "haut", "bas", "cloture", "volume", "achat")
    for tau in np.asarray(instants, dtype=np.int64):
        tau = int(tau)
        n = int((board.disponible <= tau).sum())
        expected = _lignes_poids(full, tau)
        cut_ctx = replace(ctx, externes={k: tronquer_serie(v, tau) for k, v in ctx.externes.items()},
                          evenements=tronquer_evenements(ctx.evenements, tau))
        if not _memes(expected, _lignes_poids(strategie(board.tete(n), cut_ctx), tau)):
            report.ecarts_tronque.append(f"τ={tau}")
        future = (np.arange(len(board.jours)) >= n)[:, None]
        noise = {f: np.exp(rng.normal(0, 0.05, board.cloture.shape)) for f in fields}
        fake = replace(board, **{f: np.where(future, getattr(board, f) * noise[f], getattr(board, f)) for f in fields})
        fake_ctx = replace(ctx, externes={k: falsifier_serie(v, tau, rng) for k, v in ctx.externes.items()},
                           evenements=falsifier_evenements(ctx.evenements, tau, rng))
        if not _memes(expected, _lignes_poids(strategie(fake, fake_ctx), tau)):
            report.ecarts_falsifie.append(f"τ={tau}")
    return report


# --- Mutations exigées ---------------------------------------------------------------------------------------------

def agreger_date_ouverture(h1: Bougies, unite: int) -> Bougies:
    """MUTATION 2 : bougie agrégée datée par son OUVERTURE (et blocs incomplets gardés)."""
    if unite == 1:
        return h1
    block = h1.ouverture_h // unite
    starts = np.flatnonzero(np.r_[True, block[1:] != block[:-1]])
    ends = np.r_[starts[1:], len(block)] - 1
    return Bougies(unite, block[starts] * unite, block[starts] * unite * HOUR_NS + LATENCE_NS, h1.ouverture[starts],
                   np.maximum.reduceat(h1.haut, starts), np.minimum.reduceat(h1.bas, starts), h1.cloture[ends],
                   np.add.reduceat(h1.volume, starts), np.add.reduceat(h1.achat, starts),
                   np.add.reduceat(h1.transactions, starts))


def strategie_cassure(frame: Bougies, ctx: Contexte) -> Signaux:
    """Stratégie témoin (correcte) : clôture > plus haut des 20 bougies précédentes, sortie sous le plus bas des 10."""
    high = ind.max_glissant(frame.haut, 20, inclure=False)
    low = ind.min_glissant(frame.bas, 10, inclure=False)
    return Signaux(ind.superieur(frame.cloture, high), ind.inferieur(frame.cloture, low))


def mutation_cloture_suivante(frame: Bougies, ctx: Contexte) -> Signaux:
    """MUTATION 1 : le signal en t lit la clôture de t + 1."""
    nxt = np.r_[frame.cloture[1:], np.nan]
    high = ind.max_glissant(frame.haut, 20, inclure=False)
    return Signaux(ind.superieur(nxt, high), None)


def strategie_externe(frame: Bougies, ctx: Contexte) -> Signaux:
    """Stratégie témoin (correcte) : série externe jointe sur son `available_at` (strictement antérieur)."""
    serie = next(iter(ctx.externes.values()))
    value = joindre(frame.disponible, serie)
    return Signaux(ind.superieur(value, np.zeros(len(value))), None)


def mutation_externe_sur_date(frame: Bougies, ctx: Contexte) -> Signaux:
    """MUTATION 3 : série externe jointe sur sa DATE (valeur utilisée avant sa publication)."""
    serie = next(iter(ctx.externes.values()))
    order = np.argsort(serie.date, kind="stable")
    by_date = SerieExterne(serie.nom, serie.date[order], serie.date[order], serie.valeur[order])
    value = joindre(frame.disponible, by_date)
    return Signaux(ind.superieur(value, np.zeros(len(value))), None)


def _pivots_hauts(high: np.ndarray, k: int) -> np.ndarray:
    """Indices des pivots hauts fractals (plus haut strict des k bougies de part et d'autre)."""
    n = len(high)
    out = []
    for t in range(k, n - k):
        window = high[t - k:t + k + 1]
        if high[t] == window.max() and (window == high[t]).sum() == 1:
            out.append(t)
    return np.asarray(out, dtype=np.int64)


def strategie_pivot(frame: Bougies, ctx: Contexte, *, k: int = 3) -> Signaux:
    """Stratégie témoin (correcte) : clôture > dernier pivot haut CONFIRMÉ (connu k bougies après l'extrême)."""
    pivots = _pivots_hauts(frame.haut, k)
    level = np.full(len(frame), np.nan)
    for p in pivots:
        level[p + k:] = frame.haut[p]          # connu à la confirmation p + k
    return Signaux(ind.superieur(frame.cloture, level), None)


def mutation_pivot_extreme(frame: Bougies, ctx: Contexte, *, k: int = 3) -> Signaux:
    """MUTATION 4 : pivot daté à son EXTRÊME (connu dès la bougie p, avant sa confirmation)."""
    pivots = _pivots_hauts(frame.haut, k)
    level = np.full(len(frame), np.nan)
    for p in pivots:
        level[p + 1:] = frame.haut[p]
    return Signaux(ind.superieur(frame.cloture, level) | _touche_pivot(frame, pivots), None)


def _touche_pivot(frame: Bougies, pivots: np.ndarray) -> np.ndarray:
    out = np.zeros(len(frame), dtype=bool)
    out[pivots] = True
    return out


MUTATIONS: dict[str, dict[str, Any]] = {
    "cloture_suivante": {"strategie": mutation_cloture_suivante, "temoin": strategie_cassure, "agregation": agreger},
    "bougie_datee_ouverture": {"strategie": strategie_cassure, "temoin": strategie_cassure,
                               "agregation": agreger_date_ouverture},
    "externe_sur_date": {"strategie": mutation_externe_sur_date, "temoin": strategie_externe, "agregation": agreger},
    "pivot_extreme": {"strategie": mutation_pivot_extreme, "temoin": strategie_pivot, "agregation": agreger},
}


def controler_mutations(h1: Bougies, serie: SerieExterne, etape: Etape, *, unite: int = 4,
                        instants: int = INSTANTS_MIN) -> dict[str, dict]:
    """Chaque mutation doit être détectée, chaque témoin correct doit passer (mêmes données, mêmes instants)."""
    rng = np.random.default_rng(GRAINE)
    ctx = Contexte("TEMOIN", unite, {}, etape.debut_ns, externes={serie.nom: serie})
    out = {}
    for name, spec in MUTATIONS.items():
        unit = 1 if name == "externe_sur_date" else unite
        frame = _cadre(h1, unit, agreger)
        taus = instants_aleatoires(frame, etape, instants, rng)
        good = verifier(spec["temoin"], h1, unit, ctx, taus, test_id=f"temoin:{name}")
        bad = verifier(spec["strategie"], h1, unit, ctx, taus, agregation=spec["agregation"], test_id=name)
        out[name] = {"temoin_ok": good.ok, "mutation_detectee": not bad.ok,
                     "ecarts_mutation": len(bad.ecarts_tronque) + len(bad.ecarts_falsifie)}
    return out
