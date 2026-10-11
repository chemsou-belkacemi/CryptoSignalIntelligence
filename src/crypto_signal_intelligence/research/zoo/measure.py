"""Mesure du zoo, exactement comme docs/ZOO.md § 4 (forme (a) et forme (b)).

Forme (a), règle de transaction. Chaque position `i` est valorisée à chaque clôture 1 h ; pour chaque journée UTC `d` :
- `e(i, d)` = fraction de la journée détenue (heures / 24) ;
- `r(i, d)` = rendement simple net de la position sur la partie détenue de la journée, rapporté à sa valeur au début
  de cette partie (prix d'entrée le jour de l'entrée, dernière clôture connue de la veille sinon). Frais d'entrée
  (`c`) rapportés à la valeur d'entrée, frais de sortie à la valeur de sortie (`c × V_sortie / V_début`). Ainsi
  `Σ r / Σ e` est un rendement par jour d'exposition, dans l'unité de `m(p)`. (Lecture déclarée de « multiplié par
  `e(i, d)` pour une journée partielle » : la référence d'une journée partielle est `e × m` ; le rendement de la partie
  détenue n'est pas multiplié une seconde fois, ce qui changerait son unité. Sous une dérive constante sans bruit,
  `μ* = 0` exactement quand les journées sont entières (unité 1j) ; pour une entrée en cours de journée, l'écart est
  du second ordre de la dérive horaire, < 1e-8 par jour d'exposition.)
- `μ* = Σ_{i,d} [r(i, d) − e(i, d) × m(p(i))] / Σ e`, `m(p)` = moyenne des rendements quotidiens BRUTS de la paire sur
  ses journées de membre de l'étape (moins de 90 : moyenne de l'univers à poids égaux de l'étape) ; EV02 : `R180(d)` ;
  VO11 : 24 × rendement moyen de la bougie 23:00-24:00 (référence d'une position d'une heure = ce rendement moyen) ;
- intervalle : blocs de 7 jours calendaires depuis le début de l'étape, variance du ratio de
  `research/intervals.calendar_mean_ci` (exposition à la place du nombre d'événements), Student à B − 1 degrés ;
- condition jointe : `μ = Σ r / Σ e > 0` (rendement net absolu) dans chaque scénario.

Forme (b), portefeuille : valeur suivie à chaque échange et chaque lundi (clôture du lundi = mardi 00:00 UTC), excès
hebdomadaire net sur la référence (exposition égale, même composition moyenne, univers éligible, top 40, panier
flottant), blocs de 28 jours. Frais à chaque rééquilibrage : Σ |variation de poids| × (frais + écart et glissement).

Scénarios : « nul » (frais nuls, contrôle sous H0), « central », « defavorable » (forward/costs.py, ordres au marché des
deux côtés).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from ...forward.costs import ADVERSE, CENTRAL, costs_for
from ._jit import njit
from .data import DAY_H, Etape, Grille, Univers, annee_du_jour
from .positions import Positions
from .spec import (
    EXPOSITION_EGALE,
    MEME_COMPOSITION,
    MEME_HEURE,
    PANIER_FLOTTANT,
    R180,
    UNIVERS_ELIGIBLE,
    UNIVERS_TOP40,
    Poids,
    Test,
)

NUL = "nul"
SCENARIOS = (NUL, CENTRAL, ADVERSE)
DECISION = (CENTRAL, ADVERSE)
BLOCS_A, BLOCS_B = 7, 28
MIN_TRANSACTIONS, MIN_BLOCS = 100, 20
MIN_JOURS_MEMBRE = 90


def cout_par_cote(symbole: str, scenario: str) -> float:
    """Frais + écart et glissement d'un ordre au marché, en fraction du montant (0 en scénario « nul »)."""
    if scenario == NUL:
        return 0.0
    c = costs_for(symbole, scenario)
    return c.fee + c.market


# --- Statistique de ratio par blocs calendaires ------------------------------------------------------------------------

@dataclass(frozen=True)
class Stat:
    stat: float          # statistique de décision (μ* ou excès hebdomadaire moyen)
    se: float
    blocs: int           # blocs avec exposition
    t: float
    p: float             # p bilatérale de Student à B − 1 degrés
    mu: float            # rendement net absolu par jour d'exposition (forme a) ; rendement de la stratégie (forme b)
    ic: tuple[float, float]
    niveau: float

    @property
    def positive(self) -> bool:
        return self.stat > 0


def ratio_blocs(num: np.ndarray, den: np.ndarray, jours: np.ndarray, origine: int, bloc: int,
                niveau: float = 0.95) -> tuple[float, float, int, tuple[float, float]] | None:
    """Ratio Σ num / Σ den et son erreur type par blocs de `bloc` jours calendaires depuis `origine` : covariance entre
    blocs voisins ajoutée si positive, jamais retranchée ; facteur B / (B − 1) ; Student à B − 1 degrés.
    Rend (ratio, erreur type, B, intervalle) ou None (moins de 2 blocs avec exposition)."""
    from scipy.stats import t as student_t
    num = np.asarray(num, dtype=np.float64)
    den = np.asarray(den, dtype=np.float64)
    block = (np.asarray(jours, dtype=np.int64) - origine) // bloc
    if len(block) == 0 or (block < 0).any():
        return None
    sums = np.bincount(block, weights=num)
    expo = np.bincount(block, weights=den)
    filled = int((expo > 0).sum())
    total = float(expo.sum())
    if filled < 2 or total <= 0:
        return None
    mean = float(sums.sum() / total)
    u = sums - mean * expo
    variance = (u @ u + 2 * max(0.0, float(u[1:] @ u[:-1]))) * filled / (filled - 1) / total ** 2
    se = math.sqrt(max(float(variance), 0.0))
    half = float(student_t.ppf(0.5 + niveau / 2, filled - 1)) * se
    return mean, se, filled, (mean - half, mean + half)


def p_bilaterale(stat: float, se: float, blocs: int) -> float:
    from scipy.stats import t as student_t
    if blocs < 2:
        return 1.0
    if se <= 0:
        return 0.0 if stat != 0 else 1.0
    return float(2 * student_t.sf(abs(stat / se), blocs - 1))


# --- Données journalières des paires (références) ---------------------------------------------------------------------

@njit
def _jour_prix(exists: np.ndarray, prix: np.ndarray, g0: int, day0: int, ndays: int) -> tuple[np.ndarray, np.ndarray]:
    """Prix au début de chaque journée (dernière clôture ≤ 24 d) et rendement brut de la journée (défini si la paire a
    un prix au début ET au moins une bougie close dans la journée)."""
    start = np.full(ndays, np.nan)
    ret = np.full(ndays, np.nan)
    size = len(exists)
    for j in range(ndays):
        d = day0 + j
        h0 = 24 * d - g0
        h1 = h0 + 24
        if h1 < 0 or h0 >= size:
            continue
        if h0 >= 0:
            start[j] = prix[h0]
        any_close = False
        for k in range(max(h0 + 1, 0), min(h1, size - 1) + 1):
            if exists[k]:
                any_close = True
                break
        if any_close and h0 >= 0 and h1 < size:
            ret[j] = prix[h1] / prix[h0] - 1.0
        elif any_close and h0 >= 0:
            ret[j] = prix[size - 1] / prix[h0] - 1.0
    return start, ret


@njit
def _heure(exists: np.ndarray, prix: np.ndarray, g0: int, day0: int, ndays: int, heure: int) -> np.ndarray:
    """Rendement brut de la bougie qui s'ouvre à `heure` (UTC) chaque journée, si elle et la précédente existent."""
    out = np.full(ndays, np.nan)
    size = len(exists)
    for j in range(ndays):
        close = 24 * (day0 + j) + heure + 1 - g0
        if 1 <= close < size and exists[close] and exists[close - 1]:
            out[j] = prix[close] / prix[close - 1] - 1.0
    return out


@dataclass
class JoursEtape:
    """Références d'une étape, calculées APRÈS COUP sur l'étape (elles n'entrent dans aucun signal)."""
    etape: Etape
    jours: np.ndarray
    rendement: dict[str, np.ndarray]           # rendement brut quotidien de chaque paire (NaN : indéfini)
    membre: dict[str, np.ndarray]              # membre du top 40 le jour d
    u: np.ndarray                              # rendement brut de l'univers à poids égaux (membres) le jour d
    u_moyen: float
    m: dict[str, float]                        # dérive de chaque paire, m(p)
    jours_membre: dict[str, int]
    r180: np.ndarray | None = None
    m_heure: dict[str, float] = field(default_factory=dict)

    @property
    def origine(self) -> int:
        return int(self.jours[0])


def jours_etape(etape: Etape, grilles: dict[str, Grille], univers: Univers) -> JoursEtape:
    days = etape.jours()
    day0, ndays = int(days[0]), len(days)
    returns, members = {}, {}
    for symbol, grid in grilles.items():
        if len(grid.existe) == 0:
            continue
        _, ret = _jour_prix(grid.existe, grid.prix, grid.debut_h, day0, ndays)
        returns[symbol] = ret
        members[symbol] = univers.membre_jours(symbol, days)
    if returns:
        matrix = np.vstack([np.where(members[s], returns[s], np.nan) for s in returns])
        counts = (~np.isnan(matrix)).sum(axis=0)
        u = np.where(counts > 0, np.nansum(matrix, axis=0) / np.maximum(counts, 1), np.nan)
    else:
        u = np.full(ndays, np.nan)
    u_mean = float(np.nanmean(u)) if np.isfinite(u).any() else 0.0
    m, member_days = {}, {}
    for symbol, ret in returns.items():
        ok = members[symbol] & ~np.isnan(ret)
        member_days[symbol] = int(ok.sum())
        m[symbol] = float(ret[ok].mean()) if ok.sum() >= MIN_JOURS_MEMBRE else u_mean
    return JoursEtape(etape, days, returns, members, np.nan_to_num(u, nan=0.0), u_mean, m, member_days)


def m_derniere_heure(je: JoursEtape, grilles: dict[str, Grille], heure: int = 23) -> dict[str, float]:
    """VO11 : moyenne du rendement brut de la bougie `heure`:00 de la paire sur ses journées de membre de l'étape
    (moins de 90 : moyenne de l'univers), exprimée PAR JOUR D'EXPOSITION (× 24) pour une position d'une heure."""
    day0, ndays = je.origine, len(je.jours)
    values: dict[str, np.ndarray] = {}
    for symbol, grid in grilles.items():
        if symbol in je.membre:
            r = _heure(grid.existe, grid.prix, grid.debut_h, day0, ndays, heure)
            values[symbol] = np.where(je.membre[symbol], r, np.nan)
    pooled = np.concatenate([v[~np.isnan(v)] for v in values.values()]) if values else np.zeros(0)
    universe = float(pooled.mean()) if len(pooled) else 0.0
    out = {}
    for symbol, v in values.items():
        ok = ~np.isnan(v)
        out[symbol] = 24.0 * (float(v[ok].mean()) if ok.sum() >= MIN_JOURS_MEMBRE else universe)
    return out


def r180(jours: np.ndarray, symboles: list[str], premier_jour: np.ndarray, rendement: np.ndarray) -> np.ndarray:
    """EV02 : le jour `d`, rendement brut moyen à poids égaux des paires cotées depuis plus de 180 jours
    (`premier_jour` ≤ d − 181), d'après `rendement` (jours × paires, NaN si indéfini)."""
    eligible = premier_jour[None, :] <= (np.asarray(jours)[:, None] - 181)
    values = np.where(eligible, rendement, np.nan)
    counts = (~np.isnan(values)).sum(axis=1)
    return np.where(counts > 0, np.nansum(values, axis=1) / np.maximum(counts, 1), 0.0)


# --- Forme (a) -------------------------------------------------------------------------------------------------

@njit
def _decomposer(ins: np.ndarray, outs: np.ndarray, prix: np.ndarray, g0: int, day0: int, ref_d: np.ndarray,
                u_d: np.ndarray, cin_c: float, cout_c: float, cin_a: float, cout_a: float, g: np.ndarray,
                e: np.ndarray, ref: np.ndarray, eu: np.ndarray, cc: np.ndarray, ca: np.ndarray,
                trades: np.ndarray) -> None:
    ndays = len(g)
    for i in range(len(ins)):
        a = ins[i]
        b = outs[i]
        pe = prix[a - g0]
        px = prix[b - g0]
        ratio = px / pe
        trades[i, 0] = ratio - 1.0
        trades[i, 1] = ratio - 1.0 - cin_c - cout_c * ratio
        trades[i, 2] = ratio - 1.0 - cin_a - cout_a * ratio
        trades[i, 3] = (b - a) / 24.0
        d_first = a // 24
        d_last = (b - 1) // 24
        for d in range(d_first, d_last + 1):
            j = d - day0
            if j < 0 or j >= ndays:
                continue
            lo = max(a, 24 * d)
            hi = min(b, 24 * d + 24)
            vs = prix[lo - g0]
            vf = prix[hi - g0]
            frac = (hi - lo) / 24.0
            g[j] += vf / vs - 1.0
            e[j] += frac
            ref[j] += frac * ref_d[j]
            eu[j] += frac * u_d[j]
            if lo == a:
                cc[j] += cin_c
                ca[j] += cin_a
            if hi == b:
                cc[j] += cout_c * vf / vs
                ca[j] += cout_a * vf / vs


@dataclass
class MesureA:
    """Accumulateurs d'un test de forme (a) sur une étape : séries quotidiennes et sommes par paire. Les rendements
    nets d'un scénario sont `g − c[scénario]` ; la référence est `ref` (Σ e × m) ; `eu` = Σ e × U(d)."""
    test_id: str
    etape: Etape
    jours: np.ndarray
    g: np.ndarray
    e: np.ndarray
    ref: np.ndarray
    eu: np.ndarray
    c: dict[str, np.ndarray]
    paires: dict[str, dict[str, float]]                 # par paire : g, e, ref, c_central, c_defavorable
    transactions: dict[str, np.ndarray]                 # par paire : (brut, net central, net défavorable, durée en j)
    compteurs: dict[str, int]
    u_moyen: float
    meilleure_paire: bool = True
    forme: str = "a"

    @property
    def n_transactions(self) -> int:
        return int(sum(len(t) for t in self.transactions.values()))

    def exposition_moyenne(self) -> float:
        """L'effet planté est déjà par jour d'exposition (`δ × e`) : facteur 1."""
        return 1.0

    def numerateur(self, scenario: str, delta: float = 0.0) -> np.ndarray:
        cost = self.c[scenario] if scenario != NUL else 0.0
        return self.g + delta * self.e - cost - self.ref

    def net(self, scenario: str, delta: float = 0.0) -> np.ndarray:
        cost = self.c[scenario] if scenario != NUL else 0.0
        return self.g + delta * self.e - cost

    def stat(self, scenario: str, *, delta: float = 0.0, garder: np.ndarray | None = None,
             sans_paire: str | None = None, niveau: float = 0.95, min_blocs: int = MIN_BLOCS,
             min_transactions: int = MIN_TRANSACTIONS, contre_zero: bool = False) -> Stat | None:
        """Statistique de décision. `garder` : masque des journées gardées (garde-fou « sans la meilleure année ») ;
        `sans_paire` : estimation ponctuelle sans la paire (garde-fou « sans la meilleure paire »). None si
        insuffisant (moins de `min_transactions` transactions ou de `min_blocs` blocs avec exposition).
        `contre_zero` : ancienne statistique du 2026-10-10 (μ contre 0, sans retirer la dérive), descriptive."""
        if self.n_transactions < min_transactions:
            return None
        num, net, den = self.numerateur(scenario, delta), self.net(scenario, delta), self.e
        if contre_zero:
            num = net
        if garder is not None:
            num, net, den = num * garder, net * garder, den * garder
        if sans_paire is not None:
            pair = self.paires[sans_paire]
            cost = pair[f"c_{scenario}"] if scenario != NUL else 0.0
            total_e = float(den.sum()) - pair["e"]
            if total_e <= 0:
                return None
            x = float(num.sum()) - (pair["g"] + delta * pair["e"] - cost - pair["ref"])
            mu = (float(net.sum()) - (pair["g"] + delta * pair["e"] - cost)) / total_e
            value = x / total_e
            return Stat(value, math.nan, 0, math.nan, math.nan, mu, (math.nan, math.nan), niveau)
        out = ratio_blocs(num, den, self.jours, int(self.etape.debut_jour), BLOCS_A, niveau)
        if out is None or out[2] < min_blocs:
            return None
        value, se, blocs, ci = out
        mu = float(net.sum() / den.sum())
        return Stat(value, se, blocs, value / se if se > 0 else math.inf, p_bilaterale(value, se, blocs), mu, ci, niveau)

    def contributions_annees(self, scenario: str = CENTRAL, delta: float = 0.0) -> dict[int, float]:
        years = annee_du_jour(self.jours)
        num = self.numerateur(scenario, delta)
        return {int(y): float(num[years == y].sum()) for y in np.unique(years) if self.e[years == y].sum() > 0}

    def masque_sans_annee(self, annee: int) -> np.ndarray:
        return (annee_du_jour(self.jours) != annee).astype(np.float64)

    def contributions_paires(self, scenario: str = CENTRAL, delta: float = 0.0) -> dict[str, float]:
        out = {}
        for symbol, p in self.paires.items():
            cost = p[f"c_{scenario}"] if scenario != NUL else 0.0
            out[symbol] = p["g"] + delta * p["e"] - cost - p["ref"]
        return out

    def stat_annee(self, annee: int, scenario: str = CENTRAL) -> float | None:
        years = annee_du_jour(self.jours)
        keep = years == annee
        den = float(self.e[keep].sum())
        return float(self.numerateur(scenario)[keep].sum()) / den if den > 0 else None

    def decomposition(self, scenario: str = CENTRAL) -> dict[str, float]:
        """Descriptif : choix des paires Σ (r − e U) et choix des moments Σ e (U − Ū), par jour d'exposition ; leur
        somme plus Ū est le rendement brut moyen par jour d'exposition."""
        den = float(self.e.sum())
        if den <= 0:
            return {}
        return {"choix_des_paires": float((self.g - self.eu).sum() / den),
                "choix_des_moments": float((self.eu - self.e * self.u_moyen).sum() / den),
                "univers_moyen": self.u_moyen, "brut_par_jour": float(self.g.sum() / den)}


def mesurer_a(test: Test, positions: dict[str, Positions], grilles: dict[str, Grille], je: JoursEtape, *,
              garder_transactions: bool = True) -> MesureA:
    """Rendements quotidiens de toutes les positions d'un test sur une étape (frais des trois scénarios).
    `garder_transactions=False` (contrôle sous H0) : seul le nombre de transactions est gardé."""
    days = je.jours
    n = len(days)
    day0 = int(days[0])
    total = {k: np.zeros(n) for k in ("g", "e", "ref", "eu", "cc", "ca")}
    pairs, trades = {}, {}
    counts: dict[str, int] = {}
    for symbol, pos in positions.items():
        for key, value in pos.compteurs.items():
            counts[key] = counts.get(key, 0) + int(value)
        if len(pos) == 0:
            continue
        grid = grilles[symbol]
        if test.reference == R180:
            if je.r180 is None:
                raise ValueError("référence R180 absente pour EV02")
            ref_d = je.r180
        elif test.reference == MEME_HEURE:
            ref_d = np.full(n, je.m_heure.get(symbol, 0.0))
        else:
            ref_d = np.full(n, je.m.get(symbol, je.u_moyen))
        own = {k: np.zeros(n) for k in total}
        tr = np.zeros((len(pos), 4))
        cin_c, cin_a = cout_par_cote(symbol, CENTRAL), cout_par_cote(symbol, ADVERSE)
        _decomposer(pos.entree_h.astype(np.int64), pos.sortie_h.astype(np.int64), grid.prix, int(grid.debut_h), day0,
                    ref_d, je.u, cin_c, cin_c, cin_a, cin_a, own["g"], own["e"], own["ref"], own["eu"], own["cc"],
                    own["ca"], tr)
        for key in total:
            total[key] += own[key]
        pairs[symbol] = {"g": float(own["g"].sum()), "e": float(own["e"].sum()), "ref": float(own["ref"].sum()),
                         f"c_{CENTRAL}": float(own["cc"].sum()), f"c_{ADVERSE}": float(own["ca"].sum())}
        trades[symbol] = tr if garder_transactions else np.zeros((len(pos), 0))
    return MesureA(test.id, je.etape, days, total["g"], total["e"], total["ref"], total["eu"],
                   {CENTRAL: total["cc"], ADVERSE: total["ca"]}, pairs, trades, counts, je.u_moyen,
                   test.meilleure_paire)


def descriptif_a(mesure: MesureA, eligibles: frozenset[str] | None = None) -> dict:
    """Descriptif (jamais décisif) : décomposition, rendement par transaction, part gagnante, durée, par année, par
    paire, compteurs ; restreint aux paires éligibles à F31 si demandé."""
    allt = [t for t in mesure.transactions.values() if len(t)]
    trades = np.vstack(allt) if allt else np.zeros((0, 4))
    out: dict = {"transactions": int(len(trades)), "compteurs": mesure.compteurs,
                 "exposition_jours": round(float(mesure.e.sum()), 2), "decomposition": mesure.decomposition()}
    if len(trades):
        out |= {"duree_moyenne_jours": round(float(trades[:, 3].mean()), 3),
                "part_gagnante_centrale": round(float((trades[:, 1] > 0).mean()), 4),
                "net_par_transaction": {CENTRAL: float(trades[:, 1].mean()), ADVERSE: float(trades[:, 2].mean())},
                "plus_longue_tenue_jours": round(float(trades[:, 3].max()), 2)}
    out["par_annee"] = {str(y): mesure.stat_annee(y) for y in sorted(mesure.contributions_annees())}
    contributions = mesure.contributions_paires()
    out["par_paire"] = {s: {"contribution_centrale": round(v, 6), "exposition": round(mesure.paires[s]["e"], 3)}
                        for s, v in sorted(contributions.items(), key=lambda kv: -kv[1])}
    if eligibles is not None:
        kept = [s for s in mesure.paires if s in eligibles]
        den = sum(mesure.paires[s]["e"] for s in kept)
        out["eligibles_f31"] = {"paires": len(kept), "exposition": round(den, 3),
                                "mu_star_central": (sum(contributions[s] for s in kept) / den) if den > 0 else None}
    return out


# --- Forme (b) -------------------------------------------------------------------------------------------------

def _lundis(debut_h: int, fin_h: int) -> np.ndarray:
    """Clôtures du lundi (mardi 00:00 UTC, en heures) dans [debut_h ; fin_h]."""
    first_day = -(-debut_h // DAY_H)
    days = np.arange(first_day, fin_h // DAY_H + 1)
    tuesdays = days[(days + 3) % 7 == 1]
    return tuesdays * DAY_H


def prix_aux_heures(grilles: dict[str, Grille], symboles: tuple[str, ...], heures: np.ndarray) -> np.ndarray:
    """Dernière clôture connue de chaque paire à chaque heure (NaN avant la cotation)."""
    out = np.full((len(heures), len(symboles)), np.nan)
    for k, symbol in enumerate(symboles):
        grid = grilles.get(symbol)
        if grid is None or len(grid.existe) == 0:
            continue
        idx = heures - grid.debut_h
        ok = (idx >= 0)
        idx = np.clip(idx, 0, len(grid.prix) - 1)
        out[ok, k] = grid.prix[idx[ok]]
    return out


@dataclass
class Simulation:
    valeur_avant: np.ndarray       # valeur avant l'échange, à chaque instant
    poids_apres: np.ndarray        # poids en valeur après l'échange (instants × paires)
    rotation: float
    exposition: float              # exposition moyenne pondérée par le temps (poids cibles)


def simuler_valeur(heures: np.ndarray, prix: np.ndarray, echange: np.ndarray, cibles: np.ndarray,
                   cout: np.ndarray) -> Simulation:
    """Portefeuille acheteur seul suivi aux `heures` : à chaque instant d'échange, poids cibles atteints en payant
    Σ |Δ valeur| × coût ; entre deux échanges, les avoirs flottent ; à la dernière heure, liquidation (frais compris).
    Une paire sans prix ne peut être achetée (son poids cible reste en USDT)."""
    n, s = prix.shape
    units = np.zeros(s)
    cash = 1.0
    before = np.zeros(n)
    after_w = np.zeros((n, s))
    turnover = 0.0
    exposure = 0.0
    current_target = 0.0
    for k in range(n):
        p = prix[k]
        safe = np.where(np.isnan(p), 0.0, p)
        holding = units * safe
        value = cash + holding.sum()
        if k > 0:
            exposure += current_target * (heures[k] - heures[k - 1])
        before[k] = value
        if k == n - 1:
            value -= float((holding * cout).sum())
            before[k] = value
            break
        if echange[k]:
            target = np.where(np.isnan(p), 0.0, cibles[k]) * value
            delta = np.abs(target - holding)
            turnover += float(delta.sum() / value) if value > 0 else 0.0
            value -= float((delta * cout).sum())
            weights = np.where(np.isnan(p), 0.0, cibles[k])
            units = np.where(safe > 0, value * weights / np.where(safe > 0, safe, 1.0), 0.0)
            cash = value * (1.0 - weights.sum())
            current_target = float(weights.sum())
            holding = units * safe
        after_w[k] = holding / value if value > 0 else 0.0
    span = float(heures[-1] - heures[0]) if n > 1 else 0.0
    return Simulation(before, after_w, turnover, exposure / span if span > 0 else 0.0)


@dataclass
class MesureB:
    """Excès hebdomadaire net d'un test de forme (b) sur une étape, par scénario."""
    test_id: str
    etape: Etape
    semaines: np.ndarray                    # journée (mardi) de début de chaque semaine
    exces: dict[str, np.ndarray]
    strategie: dict[str, np.ndarray]
    reference: dict[str, np.ndarray]
    contributions: dict[str, np.ndarray]    # par paire : contribution brute à l'excès, par semaine
    exposition: float
    rotation: float
    meilleure_paire: bool = False
    forme: str = "b"
    descriptif: dict = field(default_factory=dict)
    #: exposition moyenne de la stratégie pendant chaque semaine (Σ des poids cibles, pondérée par la durée)
    exposition_hebdo: np.ndarray | None = None

    def exposition_moyenne(self) -> float:
        return float(self.exposition_hebdo.mean()) if self.exposition_hebdo is not None and \
            len(self.exposition_hebdo) else self.exposition

    def _effet(self, delta: float) -> np.ndarray | float:
        """Effet planté (contrôle de puissance) : `δ` par semaine d'exposition PLEINE, proportionnel à l'exposition de
        la semaine (une stratégie jamais investie ne reçoit rien)."""
        if delta == 0:
            return 0.0
        return delta * (self.exposition_hebdo if self.exposition_hebdo is not None else 1.0)

    @property
    def n_transactions(self) -> int:
        return len(self.semaines)

    def stat(self, scenario: str, *, delta: float = 0.0, garder: np.ndarray | None = None,
             sans_paire: str | None = None, niveau: float = 0.95, min_blocs: int = MIN_BLOCS,
             min_transactions: int = 0, contre_zero: bool = False) -> Stat | None:
        """Excès hebdomadaire moyen sur la référence (`contre_zero` sans effet : la forme (b) n'a pas changé)."""
        x = self.exces[scenario] + self._effet(delta)
        ones = np.ones(len(x))
        if garder is not None:
            x, ones = x * garder, ones * garder
        if sans_paire is not None:
            contrib = self.contributions.get(sans_paire)
            total = float(ones.sum())
            if total <= 0:
                return None
            removed = float((contrib * (ones > 0)).sum()) if contrib is not None else 0.0
            value = (float(x.sum()) - removed) / total
            return Stat(value, math.nan, 0, math.nan, math.nan, value, (math.nan, math.nan), niveau)
        out = ratio_blocs(x, ones, self.semaines, int(self.etape.debut_jour), BLOCS_B, niveau)
        if out is None or out[2] < min_blocs:
            return None
        value, se, blocs, ci = out
        mu = float((self.strategie[scenario] * (ones > 0)).sum() / ones.sum())
        return Stat(value, se, blocs, value / se if se > 0 else math.inf, p_bilaterale(value, se, blocs), mu, ci, niveau)

    def contributions_annees(self, scenario: str = CENTRAL, delta: float = 0.0) -> dict[int, float]:
        years = annee_du_jour(self.semaines)
        x = self.exces[scenario] + self._effet(delta)
        return {int(y): float(x[years == y].sum()) for y in np.unique(years)}

    def masque_sans_annee(self, annee: int) -> np.ndarray:
        return (annee_du_jour(self.semaines) != annee).astype(np.float64)

    def contributions_paires(self, scenario: str = CENTRAL, delta: float = 0.0) -> dict[str, float]:
        return {s: float(v.sum()) for s, v in self.contributions.items()}

    def stat_annee(self, annee: int, scenario: str = CENTRAL) -> float | None:
        keep = annee_du_jour(self.semaines) == annee
        return float(self.exces[scenario][keep].mean()) if keep.any() else None


def poids_reference(test: Test, poids: Poids, exposition: float, dans_a: float | None = None) -> np.ndarray:
    """Poids de la référence à chaque décision (§ 4.2)."""
    univers = poids.univers if poids.univers is not None else (poids.poids > 0)
    counts = univers.sum(axis=1, keepdims=True)
    equal = np.where(counts > 0, univers / np.maximum(counts, 1), 0.0)
    if test.reference == EXPOSITION_EGALE:
        return exposition * equal
    if test.reference in (UNIVERS_ELIGIBLE, UNIVERS_TOP40, PANIER_FLOTTANT):
        return equal
    if test.reference == MEME_COMPOSITION:
        if poids.composante_a is None or poids.composante_b is None or dans_a is None:
            raise ValueError(f"{test.id} : composantes A et B exigées pour la référence de même composition")
        return dans_a * poids.composante_a + (1 - dans_a) * poids.composante_b
    raise ValueError(f"{test.id} : référence de forme (b) inconnue {test.reference}")


def _trimestre_nouveau(heures: np.ndarray) -> np.ndarray:
    months = (heures * 3_600_000_000_000).astype("datetime64[ns]").astype("datetime64[M]").astype(np.int64)
    quarters = months // 3
    out = np.ones(len(heures), dtype=bool)
    out[1:] = quarters[1:] != quarters[:-1]
    return out


def mesurer_b(test: Test, poids: Poids, grilles: dict[str, Grille], etape: Etape) -> MesureB:
    """Excès hebdomadaire net (lundi au lundi) du portefeuille sur sa référence, trois scénarios de frais."""
    keep = (poids.decisions >= etape.debut_ns) & (poids.decisions <= etape.fin_ns) & (poids.execution_h <= etape.fin_h)
    exec_h = poids.execution_h[keep]
    targets = poids.poids[keep]
    if len(exec_h) == 0:
        raise ValueError(f"{test.id} : aucune décision dans l'étape")
    sub = Poids(poids.decisions[keep], exec_h, poids.symboles, targets,
                None if poids.univers is None else poids.univers[keep],
                None if poids.composante_a is None else poids.composante_a[keep],
                None if poids.composante_b is None else poids.composante_b[keep],
                None if poids.dans_a is None else poids.dans_a[keep])
    checkpoints = _lundis(int(exec_h[0]), etape.fin_h)
    times = np.unique(np.r_[exec_h, checkpoints, etape.fin_h])
    times = times[(times >= exec_h[0]) & (times <= etape.fin_h)]
    prices = prix_aux_heures(grilles, poids.symboles, times)
    pos = np.searchsorted(times, exec_h)
    is_exec = np.zeros(len(times), dtype=bool)
    is_exec[pos] = True
    full_targets = np.zeros((len(times), len(poids.symboles)))
    full_targets[pos] = targets
    duration = np.diff(np.r_[exec_h, etape.fin_h]).astype(float)
    exposure = float((targets.sum(axis=1) * duration).sum() / duration.sum()) if duration.sum() > 0 else 0.0
    share_a = None
    if sub.dans_a is not None:
        share_a = float((sub.dans_a.astype(float) * duration).sum() / duration.sum())
    ref_targets = np.zeros_like(full_targets)
    ref_targets[pos] = poids_reference(test, sub, exposure, share_a)
    ref_exec = is_exec.copy()
    if test.reference == PANIER_FLOTTANT:
        quarter_start = np.zeros(len(times), dtype=bool)
        quarter_start[pos] = _trimestre_nouveau(exec_h)
        ref_exec = is_exec & quarter_start
    marks = np.isin(times, np.r_[checkpoints, etape.fin_h])
    week_index = np.flatnonzero(marks)
    weeks = times[week_index][:-1] // DAY_H
    strategy, reference, excess = {}, {}, {}
    sims = {}
    for scenario in SCENARIOS:
        cost = np.array([cout_par_cote(s, scenario) for s in poids.symboles])
        a = simuler_valeur(times, prices, is_exec, full_targets, cost)
        b = simuler_valeur(times, prices, ref_exec, ref_targets, cost)
        va, vb = a.valeur_avant[week_index], b.valeur_avant[week_index]
        strategy[scenario] = va[1:] / va[:-1] - 1
        reference[scenario] = vb[1:] / vb[:-1] - 1
        excess[scenario] = strategy[scenario] - reference[scenario]
        sims[scenario] = (a, b)
    a, b = sims[NUL]
    with np.errstate(invalid="ignore", divide="ignore"):
        step = np.where(np.isnan(prices[1:] / prices[:-1]), 0.0, prices[1:] / prices[:-1] - 1)
    diff = (a.poids_apres[:-1] - b.poids_apres[:-1]) * step
    diff[np.arange(len(times) - 1) < week_index[0]] = 0.0
    week_of = np.searchsorted(week_index, np.arange(len(times) - 1), side="right") - 1
    current = np.zeros(len(times))
    last_target = 0.0
    sums = full_targets.sum(axis=1)
    for k in range(len(times)):
        if is_exec[k]:
            last_target = float(sums[k])
        current[k] = last_target
    span = np.diff(times).astype(float)
    step_week = np.clip(week_of, 0, len(weeks) - 1)
    valid = np.arange(len(times) - 1) >= week_index[0]
    weekly_time = np.bincount(step_week, weights=span * valid, minlength=len(weeks))[:len(weeks)]
    weekly_expo = np.bincount(step_week, weights=current[:-1] * span * valid, minlength=len(weeks))[:len(weeks)]
    exposition_hebdo = np.where(weekly_time > 0, weekly_expo / np.maximum(weekly_time, 1e-12), 0.0)
    contributions = {}
    for k, symbol in enumerate(poids.symboles):
        per_week = np.bincount(np.clip(week_of, 0, len(weeks) - 1), weights=diff[:, k], minlength=len(weeks))
        if np.abs(per_week).sum() > 0:
            contributions[symbol] = per_week[:len(weeks)]
    return MesureB(test.id, etape, weeks, excess, strategy, reference, contributions, exposure, sims[CENTRAL][0].rotation,
                   test.meilleure_paire, exposition_hebdo=exposition_hebdo,
                   descriptif={"exposition_moyenne": round(exposure, 4),
                                                     "part_a": share_a,
                                                     "rotation_par_echange": round(sims[CENTRAL][0].rotation /
                                                                                   max(len(exec_h), 1), 4)})


def descriptif_b(mesure: MesureB) -> dict:
    out = dict(mesure.descriptif)
    for scenario in DECISION:
        values = mesure.strategie[scenario]
        nav = np.cumprod(1 + values)
        out[scenario] = {"rendement_hebdo_moyen": float(values.mean()) if len(values) else None,
                         "volatilite_hebdo": float(values.std(ddof=1)) if len(values) > 1 else None,
                         "perte_maximale": float((nav / np.maximum.accumulate(nav) - 1).min()) if len(nav) else None,
                         "exces_sur_reference": float(mesure.exces[scenario].mean()) if len(values) else None}
    out["par_annee"] = {str(y): mesure.stat_annee(y) for y in sorted(mesure.contributions_annees())}
    return out


# --- Placebos (descriptifs, § 6) ---------------------------------------------------------------------------------

def placebos_a(mesure: MesureA, positions: dict[str, Positions], grilles: dict[str, Grille], je: JoursEtape,
               *, tirages: int = 20, graine: int = 20261012) -> dict:
    """Pour chaque transaction, `tirages` entrées placebo sur la même paire, de même durée, tirées uniformément parmi
    les heures de l'étape où la paire est membre et hors de toute position du test ; mêmes frais. Rapporté : rendement
    net moyen par transaction des placebos et écart signal − placebos (descriptif seulement)."""
    rng = np.random.default_rng([graine, sum(map(ord, mesure.test_id))])
    signal_net: list[np.ndarray] = []
    placebo_net: list[np.ndarray] = []
    for symbol, pos in positions.items():
        if len(pos) == 0 or symbol not in grilles or symbol not in je.membre:
            continue                      # univers d'exception (EV02) : pas de placebo de membre, déclaré
        grid = grilles[symbol]
        member_days = je.jours[je.membre.get(symbol, np.zeros(len(je.jours), bool))]
        if len(member_days) == 0:
            continue
        hours = (member_days[:, None] * DAY_H + np.arange(DAY_H)[None, :]).ravel()
        # heures occupées par une position du test (tableau de différences, aucune boucle sur les heures)
        lo, hi = int(hours.min()), int(hours.max()) + 1
        marks = np.zeros(hi - lo + 2, dtype=np.int64)
        np.add.at(marks, np.clip(pos.entree_h - lo, 0, hi - lo + 1), 1)
        np.add.at(marks, np.clip(pos.sortie_h - lo, 0, hi - lo + 1), -1)
        busy = np.cumsum(marks)[hours - lo] > 0
        free = hours[~busy]
        free = free[(free >= grid.debut_h) & (free <= grid.fin_h)]
        free = free[grid.existe[free - grid.debut_h]]
        cost = cout_par_cote(symbol, CENTRAL)
        ratio = grid.prix[pos.sortie_h - grid.debut_h] / grid.prix[pos.entree_h - grid.debut_h]
        signal_net.append(ratio - 1 - cost - cost * ratio)
        limit = min(grid.fin_h, je.etape.fin_h)
        for length in np.unique(pos.sortie_h - pos.entree_h):
            count = int(((pos.sortie_h - pos.entree_h) == length).sum())
            starts = free[free + length <= limit]
            if len(starts) == 0:
                continue
            picks = rng.choice(starts, size=count * tirages)
            r = grid.prix[picks + length - grid.debut_h] / grid.prix[picks - grid.debut_h]
            placebo_net.append(r - 1 - cost - cost * r)
    if not signal_net or not placebo_net:
        return {"tirages": tirages, "transactions": int(sum(len(x) for x in signal_net)), "placebos": 0}
    signals, placebos = np.concatenate(signal_net), np.concatenate(placebo_net)
    return {"tirages": tirages, "transactions": len(signals), "placebos": len(placebos),
            "net_signal_central": float(signals.mean()), "net_placebo_central": float(placebos.mean()),
            "ecart_signal_moins_placebos": float(signals.mean() - placebos.mean())}
