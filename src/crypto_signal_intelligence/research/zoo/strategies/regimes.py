"""Régimes et modèles (RG) : RG02 (forme b), RG03 à RG07 (forme a), paramètres standard du registre (`spec.py`).

Chaque fonction est PURE (aucune I/O, aucun hasard, aucune horloge) et CAUSALE : la valeur en `t` ne lit que les
bougies `≤ t`. Les modèles estimés (HMM, Kalman) ne sont ajustés que sur le passé, à fréquence fixe (le mois), avec un
départ déterministe (aucune graine n'est nécessaire : aucun tirage). Lectures déclarées (docs/ZOO_CATALOGUE.md § 15,
docs/ZOO.md § 12) :

- **RG02** (HMM à 3 états, Ang et Bekaert 2002 ; « réestimation mensuelle comme FACTORS.md ») : rendements
  logarithmiques journaliers de BTC (clôtures du panneau, jours sans clôture sautés) ; Baum-Welch sur TOUT le passé
  connu à la première décision hebdomadaire de chaque mois (au moins 300 rendements, comme `research/factors.py`),
  départ de l'ajustement = paramètres du mois précédent (le premier : départ fixe, terciles des écarts absolus à la
  médiane, persistance 0,9) ; probabilité FILTRÉE (récurrence avant seule) de l'état de plus forte variance à chaque
  décision, avec les paramètres du mois ; top 40 à poids égaux sauf si elle dépasse 0,5. Indéfinie (moins de 300
  rendements, BTC absent) : la comparaison « dépasse 0,5 » est fausse, l'univers est investi.
- **RG03** (Kalman, tendance locale linéaire du log-prix, Harvey 1989) : `y = μ + ε`, `μ' = μ + β + η`, `β' = β + ζ` ;
  variances estimées par maximum de vraisemblance (vraisemblance concentrée en σ²_ε, Nelder-Mead déterministe sur
  log(σ²_η/σ²_ε), log(σ²_ζ/σ²_ε), départ fixe) à la première décision de chaque mois, sur la FENÊTRE GLISSANTE des
  365 dernières clôtures journalières connues ; filtre lancé sur cette première fenêtre puis poursuivi bougie par
  bougie, les variances changeant chaque mois ; état : pente filtrée `β(t|t)` > 0, sortie ≤ 0. Aucun signal avant la
  première estimation (365 clôtures).
- **RG04** (Hurst R/S, Hurst 1951, Peters 1994) : 128 derniers rendements logarithmiques journaliers ; pour
  n ∈ {8, 16, 32, 64, 128}, moyenne de R/S sur les 128 / n sous-périodes disjointes (R : étendue des écarts cumulés à
  la moyenne ; S : écart-type sans correction) ; H = pente des moindres carrés de log(R/S) sur log(n), sans
  correction d'Anis-Lloyd (R/S classique : biais connu vers le haut sur de petits n, déclaré, non corrigé) ; état :
  H > 0,5 et rendement 20 j > 0.
- **RG05** (entropie de permutation, Bandt et Pompe 2002) : ordre 3, délai 1, sur les 64 dernières clôtures
  (62 motifs), normalisée par log(3!) ; égalités départagées par l'ordre d'apparition ; « dans le tiers bas de ses
  365 j » : strictement sous le quantile 1/3 (interpolation linéaire) des 365 valeurs PRÉCÉDENTES (comme « les 365 j
  précédents » de AM08 et RO05) ; état : cette condition et rendement 20 j > 0.
- **RG06** (tendance de Haar niveau 3, recalculée à chaque décision sur le passé seul) : sur le log de la clôture, la
  transformée de Haar alignée sur la DERNIÈRE bougie donne au niveau 3 des blocs de 8 bougies ; la tendance
  (approximation de niveau 3, détails retirés) vaut la moyenne de chaque bloc ; pente = moyenne des 8 dernières moins
  moyenne des 8 précédentes (> 0 : état vrai ; ≤ 0 : sortie).
- **RG07** (sinusoïde d'Ehlers, « Rocket Science for Traders », 2001, réglages de l'auteur) : prix médian (H + L) / 2,
  lissage pondéré 4-3-2-1, discriminateur homodyne (période bornée à [6 ; 50], lissages 0,2 / 0,33), phase du cycle
  dominant sur `int(période lissée + 0,5)` bougies ; Sine = sin(phase), LeadSine = sin(phase + 45°) ; entrée quand
  Sine croise au-dessus de LeadSine (texte du registre), sortie au croisement inverse ; indéfinie sur les 50
  premières bougies (période maximale de l'auteur).
"""
from __future__ import annotations

import math

import numpy as np

from .. import indicators as ind
from .._jit import njit
from ..data import Bougies, mois_de
from ..spec import Contexte, Panneau, Poids, Signaux
from .commun import decisions_hebdomadaires

IMPLEMENTEES: frozenset[str] = frozenset({"entropie", "haar", "hmm3", "hurst", "kalman", "sinusoide_ehlers"})
DEPENDENT_ETAPE: frozenset[str] = frozenset()

BTC = "BTCUSDT"
HMM_MIN_RENDEMENTS = 300          # comme research/factors.py (HMM_MIN_RETURNS)
HMM_ITERATIONS, HMM_TOLERANCE = 100, 1e-6
KALMAN_FENETRE = 365
KALMAN_DIFFUS = 1e7               # variance initiale « diffuse » (unités de σ²_ε)
KALMAN_DEPART = (-5.0, -10.0)     # log(q_η), log(q_ζ) de départ (fixe, déclaré)
KALMAN_BORNES = (-25.0, 5.0)
HURST_TAILLES = (8, 16, 32, 64, 128)
EHLERS_CHAUFFE = 50


def _rendement(cloture: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(cloture), np.nan)
    if len(cloture) > n:
        out[n:] = cloture[n:] / cloture[:-n] - 1.0
    return out


def _etat(condition: np.ndarray, definie: np.ndarray) -> Signaux:
    """Règle d'état : entrée = condition vraie (et définie), sortie = condition fausse ET définie."""
    return Signaux(condition & definie, ~condition & definie)


# --- RG04 : Hurst R/S ------------------------------------------------------------------------------------------------

@njit
def _rs_moyen(x: np.ndarray, n: int) -> float:
    """Moyenne de R/S sur les sous-périodes disjointes de longueur n de x (NaN si aucune n'a S > 0)."""
    total, count = 0.0, 0
    for start in range(0, len(x) - n + 1, n):
        seg = x[start:start + n]
        mean = seg.mean()
        cum, hi, lo, ss = 0.0, 0.0, 0.0, 0.0
        for v in seg:
            cum += v - mean
            hi = max(hi, cum)
            lo = min(lo, cum)
            ss += (v - mean) ** 2
        s = math.sqrt(ss / n)
        if s > 0:
            total += (hi - lo) / s
            count += 1
    return total / count if count else np.nan


@njit
def _hurst_glissant(r: np.ndarray, fenetre: int, tailles: np.ndarray) -> np.ndarray:
    """Exposant de Hurst R/S sur les `fenetre` derniers rendements à chaque t (NaN si l'un manque)."""
    out = np.full(len(r), np.nan)
    lx = np.log(tailles.astype(np.float64))
    for t in range(fenetre - 1, len(r)):
        x = r[t - fenetre + 1:t + 1]
        if np.isnan(x).any():
            continue
        ly = np.empty(len(tailles))
        ok = True
        for k in range(len(tailles)):
            v = _rs_moyen(x, tailles[k])
            if not v > 0:
                ok = False
                break
            ly[k] = math.log(v)
        if not ok:
            continue
        mx, my = lx.mean(), ly.mean()
        out[t] = ((lx - mx) * (ly - my)).sum() / ((lx - mx) ** 2).sum()
    return out


def exposant_hurst(cloture: np.ndarray, fenetre: int = 128, tailles: tuple[int, ...] = HURST_TAILLES) -> np.ndarray:
    """H de R/S sur les `fenetre` derniers rendements logarithmiques (fenetre + 1 clôtures)."""
    close = np.asarray(cloture, dtype=np.float64)
    r = np.full(len(close), np.nan)
    if len(close) > 1:
        r[1:] = np.diff(np.log(close))
    return _hurst_glissant(r, fenetre, np.asarray(tailles, dtype=np.int64))


def hurst(frame: Bougies, ctx: Contexte) -> Signaux:
    """RG04 (état) : Hurst R/S (128 rendements) > 0,5 et rendement 20 j > 0 ; sortie : condition fausse."""
    p = ctx.params
    h = exposant_hurst(frame.cloture, p["n"])
    ret = _rendement(frame.cloture, p["rendement"])
    defined = ~np.isnan(h) & ~np.isnan(ret)
    return _etat(ind.superieur(h, np.full(len(h), 0.5)) & ind.superieur(ret, np.zeros(len(ret))), defined)


# --- RG05 : entropie de permutation ----------------------------------------------------------------------------------

@njit
def _motifs_ordre3(x: np.ndarray) -> np.ndarray:
    """Code (0 à 5) du motif ordinal de (x[i-2], x[i-1], x[i]) en i ; −1 si indéfini. Rangs « stables » : à valeurs
    égales, la plus ancienne est la plus petite."""
    out = np.full(len(x), -1, dtype=np.int64)
    for i in range(2, len(x)):
        a, b, c = x[i - 2], x[i - 1], x[i]
        if np.isnan(a) or np.isnan(b) or np.isnan(c):
            continue
        # rang de chaque élément (0 = plus petit), départage par position
        ra = (b < a) + (c < a)
        rb = (a <= b) + (c < b)
        rc = (a <= c) + (b <= c)
        out[i] = ra * 9 + rb * 3 + rc
    return out


_CODES3 = np.array([0 * 9 + 1 * 3 + 2, 0 * 9 + 2 * 3 + 1, 1 * 9 + 0 * 3 + 2, 1 * 9 + 2 * 3 + 0, 2 * 9 + 0 * 3 + 1,
                    2 * 9 + 1 * 3 + 0], dtype=np.int64)


@njit
def _entropie_glissante(codes: np.ndarray, fenetre: int, valeurs_codes: np.ndarray) -> np.ndarray:
    n_motifs = fenetre - 2
    out = np.full(len(codes), np.nan)
    norm = math.log(6.0)
    for t in range(fenetre - 1, len(codes)):
        seg = codes[t - n_motifs + 1:t + 1]
        if (seg < 0).any():
            continue
        h = 0.0
        for v in valeurs_codes:
            c = (seg == v).sum()
            if c:
                p = c / n_motifs
                h -= p * math.log(p)
        out[t] = h / norm
    return out


def entropie_permutation(cloture: np.ndarray, ordre: int = 3, fenetre: int = 64) -> np.ndarray:
    """Entropie de permutation normalisée (ordre 3, délai 1) des `fenetre` dernières clôtures."""
    if ordre != 3:
        raise ValueError("entropie de permutation : ordre 3 seulement (registre)")
    codes = _motifs_ordre3(np.asarray(cloture, dtype=np.float64))
    return _entropie_glissante(codes, fenetre, _CODES3)


@njit
def _sous_tiers_bas(x: np.ndarray, historique: int, q: float) -> np.ndarray:
    """x[t] < quantile q (interpolation linéaire) des `historique` valeurs précédentes ; NaN si l'une manque."""
    out = np.full(len(x), np.nan)
    for t in range(historique, len(x)):
        window = x[t - historique:t]
        if np.isnan(window).any() or np.isnan(x[t]):
            continue
        s = np.sort(window)
        pos = q * (historique - 1)
        lo = int(math.floor(pos))
        hi = min(lo + 1, historique - 1)
        level = s[lo] + (pos - lo) * (s[hi] - s[lo])
        out[t] = 1.0 if x[t] < level else 0.0
    return out


def entropie(frame: Bougies, ctx: Contexte) -> Signaux:
    """RG05 (état) : entropie de permutation (ordre 3, 64 j) dans le tiers bas de ses 365 j et rendement 20 j > 0."""
    p = ctx.params
    pe = entropie_permutation(frame.cloture, p["ordre"], p["fenetre"])
    low = _sous_tiers_bas(pe, p["historique"], 1.0 / 3.0)
    ret = _rendement(frame.cloture, p["rendement"])
    defined = ~np.isnan(low) & ~np.isnan(ret)
    return _etat((low == 1.0) & ind.superieur(ret, np.zeros(len(ret))), defined)


# --- RG06 : tendance de Haar -------------------------------------------------------------------------------------------

def pente_haar(cloture: np.ndarray, niveau: int = 3) -> np.ndarray:
    """Pente de la tendance de Haar de niveau `niveau` (blocs de 2^niveau bougies alignés sur la dernière) :
    moyenne du dernier bloc − moyenne du bloc précédent, sur le log de la clôture (NaN sans 2 blocs complets)."""
    block = 2 ** niveau
    y = np.log(np.asarray(cloture, dtype=np.float64))
    last = ind.sma(y, block) if len(y) else y
    out = np.full(len(y), np.nan)
    if len(y) >= 2 * block:
        out[2 * block - 1:] = last[2 * block - 1:] - last[block - 1:-block]
    return out


def haar(frame: Bougies, ctx: Contexte) -> Signaux:
    """RG06 (état) : pente de la tendance de Haar (niveau 3, passé seul) > 0 ; sortie : pente ≤ 0."""
    slope = pente_haar(frame.cloture, ctx.params["niveau"])
    return _etat(ind.superieur(slope, np.zeros(len(slope))), ~np.isnan(slope))


# --- RG07 : sinusoïde d'Ehlers --------------------------------------------------------------------------------------

@njit
def _ehlers(price: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Transcription de « SineWave Indicator » (Ehlers, Rocket Science for Traders, 2001) ; angles en degrés."""
    n = len(price)
    smooth = np.zeros(n)
    detr = np.zeros(n)
    q1 = np.zeros(n)
    i1 = np.zeros(n)
    ji = np.zeros(n)
    jq = np.zeros(n)
    i2 = np.zeros(n)
    q2 = np.zeros(n)
    re = np.zeros(n)
    im = np.zeros(n)
    period = np.zeros(n)
    sper = np.zeros(n)
    phase = np.zeros(n)
    rad = math.pi / 180.0
    for t in range(6, n):
        smooth[t] = (4 * price[t] + 3 * price[t - 1] + 2 * price[t - 2] + price[t - 3]) / 10.0
        k = 0.075 * period[t - 1] + 0.54
        detr[t] = (0.0962 * smooth[t] + 0.5769 * smooth[t - 2] - 0.5769 * smooth[t - 4] - 0.0962 * smooth[t - 6]) * k
        q1[t] = (0.0962 * detr[t] + 0.5769 * detr[t - 2] - 0.5769 * detr[t - 4] - 0.0962 * detr[t - 6]) * k
        i1[t] = detr[t - 3]
        ji[t] = (0.0962 * i1[t] + 0.5769 * i1[t - 2] - 0.5769 * i1[t - 4] - 0.0962 * i1[t - 6]) * k
        jq[t] = (0.0962 * q1[t] + 0.5769 * q1[t - 2] - 0.5769 * q1[t - 4] - 0.0962 * q1[t - 6]) * k
        i2[t] = i1[t] - jq[t]
        q2[t] = q1[t] + ji[t]
        i2[t] = 0.2 * i2[t] + 0.8 * i2[t - 1]
        q2[t] = 0.2 * q2[t] + 0.8 * q2[t - 1]
        re[t] = i2[t] * i2[t - 1] + q2[t] * q2[t - 1]
        im[t] = i2[t] * q2[t - 1] - q2[t] * i2[t - 1]
        re[t] = 0.2 * re[t] + 0.8 * re[t - 1]
        im[t] = 0.2 * im[t] + 0.8 * im[t - 1]
        per = period[t - 1]
        if im[t] != 0.0 and re[t] != 0.0:
            per = 360.0 / (math.atan(im[t] / re[t]) / rad)
        if per > 1.5 * period[t - 1]:
            per = 1.5 * period[t - 1]
        if per < 0.67 * period[t - 1]:
            per = 0.67 * period[t - 1]
        if per < 6.0:
            per = 6.0
        if per > 50.0:
            per = 50.0
        period[t] = 0.2 * per + 0.8 * period[t - 1]
        sper[t] = 0.33 * period[t] + 0.67 * sper[t - 1]
        dc = int(sper[t] + 0.5)
        real, imag = 0.0, 0.0
        for c in range(dc):
            if t - c >= 0:
                real += math.sin(360.0 * c / dc * rad) * smooth[t - c]
                imag += math.cos(360.0 * c / dc * rad) * smooth[t - c]
        ph = phase[t - 1]
        if abs(imag) > 0.0:
            ph = math.atan(real / imag) / rad
        if abs(imag) <= 0.001:
            ph = ph + 90.0 * (1.0 if real > 0 else (-1.0 if real < 0 else 0.0))
        ph += 90.0
        ph += 360.0 / sper[t]
        if imag < 0.0:
            ph += 180.0
        if ph > 315.0:
            ph -= 360.0
        phase[t] = ph
    sine = np.sin(phase * rad)
    lead = np.sin((phase + 45.0) * rad)
    return sine, lead


def sinusoide(haut: np.ndarray, bas: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(Sine, LeadSine) d'Ehlers sur le prix médian ; NaN sur les 50 premières bougies."""
    price = (np.asarray(haut, dtype=np.float64) + np.asarray(bas, dtype=np.float64)) / 2.0
    sine, lead = _ehlers(price)
    sine[:EHLERS_CHAUFFE] = np.nan
    lead[:EHLERS_CHAUFFE] = np.nan
    return sine, lead


def sinusoide_ehlers(frame: Bougies, ctx: Contexte) -> Signaux:
    """RG07 : la sinusoïde d'Ehlers croise au-dessus de sa version avancée (LeadSine) ; sortie au croisement inverse."""
    sine, lead = sinusoide(frame.haut, frame.bas)
    return Signaux(ind.croise_dessus(sine, lead), ind.croise_dessous(sine, lead))


# --- RG03 : filtre de Kalman (tendance locale linéaire) -------------------------------------------------------------

@njit
def _kalman_pas(y: float, a0: float, a1: float, p00: float, p01: float, p11: float, q_eta: float, q_zeta: float):
    """Une mise à jour (σ²_ε = 1) puis la prédiction : (v, F, pente filtrée, état et covariance prédits)."""
    v = y - a0
    f = p00 + 1.0
    k0, k1 = p00 / f, p01 / f
    u0, u1 = a0 + k0 * v, a1 + k1 * v
    s00 = p00 - k0 * k0 * f
    s01 = p01 - k0 * k1 * f
    s11 = p11 - k1 * k1 * f
    # prédiction : T = [[1, 1], [0, 1]]
    n00 = s00 + 2 * s01 + s11 + q_eta
    n01 = s01 + s11
    n11 = s11 + q_zeta
    return v, f, u1, u0 + u1, u1, n00, n01, n11


@njit
def _kalman_log_vraisemblance(y: np.ndarray, log_q_eta: float, log_q_zeta: float) -> float:
    """Log-vraisemblance concentrée en σ²_ε (deux premières observations exclues : départ diffus)."""
    q_eta, q_zeta = math.exp(log_q_eta), math.exp(log_q_zeta)
    a0, a1 = y[0], 0.0
    p00, p01, p11 = KALMAN_DIFFUS, 0.0, KALMAN_DIFFUS
    s_v2f, s_logf, count = 0.0, 0.0, 0
    for t in range(len(y)):
        v, f, _, a0, a1, p00, p01, p11 = _kalman_pas(y[t], a0, a1, p00, p01, p11, q_eta, q_zeta)
        if t >= 2:
            s_v2f += v * v / f
            s_logf += math.log(f)
            count += 1
    if count == 0 or s_v2f <= 0:
        return -np.inf
    return -0.5 * (count * math.log(s_v2f / count) + s_logf)


@njit
def _nelder_mead_kalman(y: np.ndarray, x0: float, x1: float, lo: float, hi: float) -> tuple[float, float]:
    """Nelder-Mead déterministe (2 paramètres bornés) qui MAXIMISE la vraisemblance concentrée."""
    pts = np.array([[x0, x1], [x0 + 1.0, x1], [x0, x1 + 1.0]])
    vals = np.empty(3)
    for i in range(3):
        pts[i, 0] = min(max(pts[i, 0], lo), hi)
        pts[i, 1] = min(max(pts[i, 1], lo), hi)
        vals[i] = -_kalman_log_vraisemblance(y, pts[i, 0], pts[i, 1])
    for _ in range(200):
        order = np.argsort(vals)
        pts, vals = pts[order], vals[order]
        if abs(vals[2] - vals[0]) <= 1e-8 * (1.0 + abs(vals[0])) and \
                max(abs(pts[2, 0] - pts[0, 0]), abs(pts[2, 1] - pts[0, 1])) < 1e-4:
            break
        c0, c1 = (pts[0, 0] + pts[1, 0]) / 2, (pts[0, 1] + pts[1, 1]) / 2
        r0 = min(max(c0 + (c0 - pts[2, 0]), lo), hi)
        r1 = min(max(c1 + (c1 - pts[2, 1]), lo), hi)
        fr = -_kalman_log_vraisemblance(y, r0, r1)
        if fr < vals[0]:
            e0 = min(max(c0 + 2 * (c0 - pts[2, 0]), lo), hi)
            e1 = min(max(c1 + 2 * (c1 - pts[2, 1]), lo), hi)
            fe = -_kalman_log_vraisemblance(y, e0, e1)
            if fe < fr:
                pts[2, 0], pts[2, 1], vals[2] = e0, e1, fe
            else:
                pts[2, 0], pts[2, 1], vals[2] = r0, r1, fr
        elif fr < vals[1]:
            pts[2, 0], pts[2, 1], vals[2] = r0, r1, fr
        else:
            k0 = c0 + 0.5 * (pts[2, 0] - c0)
            k1 = c1 + 0.5 * (pts[2, 1] - c1)
            fk = -_kalman_log_vraisemblance(y, k0, k1)
            if fk < vals[2]:
                pts[2, 0], pts[2, 1], vals[2] = k0, k1, fk
            else:
                for i in (1, 2):
                    pts[i, 0] = pts[0, 0] + 0.5 * (pts[i, 0] - pts[0, 0])
                    pts[i, 1] = pts[0, 1] + 0.5 * (pts[i, 1] - pts[0, 1])
                    vals[i] = -_kalman_log_vraisemblance(y, pts[i, 0], pts[i, 1])
    best = np.argmin(vals)
    return pts[best, 0], pts[best, 1]


@njit
def _kalman_pente(y: np.ndarray, refits: np.ndarray, fenetre: int, x0: float, x1: float, lo: float,
                  hi: float) -> np.ndarray:
    """Pente filtrée à chaque t ≥ première réestimation ; variances réestimées aux indices `refits` (fenêtre des
    `fenetre` dernières valeurs, t inclus), filtre lancé sur la première fenêtre puis poursuivi."""
    out = np.full(len(y), np.nan)
    if len(refits) == 0:
        return out
    first = refits[0]
    q_eta, q_zeta = 0.0, 0.0
    a0, a1 = 0.0, 0.0
    p00, p01, p11 = KALMAN_DIFFUS, 0.0, KALMAN_DIFFUS
    k = 0
    for t in range(first - fenetre + 1, len(y)):
        if k < len(refits) and t == refits[k]:
            e0, e1 = _nelder_mead_kalman(y[t - fenetre + 1:t + 1], x0, x1, lo, hi)
            q_eta, q_zeta = math.exp(e0), math.exp(e1)
            if k == 0:                       # filtre relancé sur la 1re fenêtre (données ≤ t), mêmes variances
                a0, a1 = y[t - fenetre + 1], 0.0
                p00, p01, p11 = KALMAN_DIFFUS, 0.0, KALMAN_DIFFUS
                for s in range(t - fenetre + 1, t):
                    _, _, _, a0, a1, p00, p01, p11 = _kalman_pas(y[s], a0, a1, p00, p01, p11, q_eta, q_zeta)
            k += 1
        if t < first:
            continue
        _, _, slope, a0, a1, p00, p01, p11 = _kalman_pas(y[t], a0, a1, p00, p01, p11, q_eta, q_zeta)
        out[t] = slope
    return out


def indices_debut_de_mois(disponible: np.ndarray) -> np.ndarray:
    """Indices des premières décisions de chaque mois (mois de l'instant de décision)."""
    months = mois_de(disponible)
    first = np.ones(len(months), dtype=bool)
    first[1:] = months[1:] != months[:-1]
    return np.flatnonzero(first)


def pente_kalman(cloture: np.ndarray, disponible: np.ndarray, fenetre: int = KALMAN_FENETRE) -> np.ndarray:
    """Pente filtrée de la tendance locale linéaire du log-prix (variances réestimées chaque mois, passé seul)."""
    y = np.log(np.asarray(cloture, dtype=np.float64))
    starts = indices_debut_de_mois(np.asarray(disponible, dtype=np.int64))
    refits = starts[starts >= fenetre - 1].astype(np.int64)
    return _kalman_pente(y, refits, fenetre, KALMAN_DEPART[0], KALMAN_DEPART[1], *KALMAN_BORNES)


def kalman(frame: Bougies, ctx: Contexte) -> Signaux:
    """RG03 (état) : pente filtrée (Kalman, tendance locale linéaire du log-prix) > 0 ; sortie : pente ≤ 0."""
    slope = pente_kalman(frame.cloture, frame.disponible)
    return _etat(ind.superieur(slope, np.zeros(len(slope))), ~np.isnan(slope))


# --- RG02 : HMM gaussien à 3 états -----------------------------------------------------------------------------------

@njit
def _emissions(x: np.ndarray, mean: np.ndarray, var: np.ndarray) -> np.ndarray:
    k = len(mean)
    out = np.empty((len(x), k))
    for j in range(k):
        out[:, j] = np.exp(-0.5 * (x - mean[j]) ** 2 / var[j]) / math.sqrt(2 * math.pi * var[j]) + 1e-300
    return out


@njit
def _avant(b: np.ndarray, start: np.ndarray, trans: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Récurrence avant normalisée : (P(état_t | x_1..t), facteurs d'échelle)."""
    n, k = b.shape
    f = np.empty((n, k))
    scale = np.empty(n)
    prior = start.copy()
    for t in range(n):
        if t:
            prior = f[t - 1] @ trans
        u = prior * b[t]
        total = u.sum()
        f[t] = u / total
        scale[t] = total
    return f, scale


@njit
def _baum_welch(x: np.ndarray, start: np.ndarray, trans: np.ndarray, mean: np.ndarray, var: np.ndarray,
                iterations: int, tol: float):
    """Maximum de vraisemblance (Baum-Welch) sur toute la série fournie ; déterministe."""
    n, k = len(x), len(mean)
    start, trans, mean, var = start.copy(), trans.copy(), mean.copy(), var.copy()
    previous = -np.inf
    for _ in range(iterations):
        b = _emissions(x, mean, var)
        f, scale = _avant(b, start, trans)
        g = np.empty((n, k))
        g[n - 1] = f[n - 1]
        r = np.ones(k)
        xi = np.zeros((k, k))
        for t in range(n - 2, -1, -1):
            m = b[t + 1] * r / scale[t + 1]
            for i in range(k):
                for j in range(k):
                    xi[i, j] += f[t, i] * trans[i, j] * m[j]
            r = trans @ m
            g[t] = f[t] * r
        weights = g.sum(axis=0)
        for j in range(k):
            w = g[:, j]
            s = weights[j]
            if s > 0:
                mean[j] = (w * x).sum() / s
                var[j] = max((w * (x - mean[j]) ** 2).sum() / s, 1e-10)
        for i in range(k):
            row = xi[i].sum()
            if row > 0:
                trans[i] = xi[i] / row
        start = g[0].copy()
        likelihood = np.log(scale).sum()
        if abs(likelihood - previous) <= tol * max(1.0, abs(likelihood)):
            break
        previous = likelihood
    return start, trans, mean, var


def hmm_depart(x: np.ndarray, etats: int = 3) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Départ fixe et déclaré : états séparés par les quantiles des écarts absolus à la médiane (terciles pour 3
    états), moyenne commune, persistance 0,9, probabilités initiales égales."""
    x = np.asarray(x, dtype=np.float64)
    deviation = np.abs(x - np.median(x))
    edges = np.quantile(deviation, np.linspace(0, 1, etats + 1))
    var = np.empty(etats)
    for j in range(etats):
        part = x[(deviation >= edges[j]) & (deviation <= edges[j + 1])]
        var[j] = max(float(np.var(part)) if len(part) > 1 else 1e-10, 1e-10)
    trans = np.full((etats, etats), 0.1 / (etats - 1))
    np.fill_diagonal(trans, 0.9)
    return np.full(etats, 1.0 / etats), trans, np.full(etats, float(np.mean(x))), var


def probabilite_etat_volatil(log_rendements: np.ndarray, fins: np.ndarray, refits: np.ndarray, etats: int = 3,
                             minimum: int = HMM_MIN_RENDEMENTS) -> np.ndarray:
    """Probabilité filtrée de l'état de plus forte variance à chaque décision `d`, calculée sur les rendements
    `log_rendements[:fins[d]]` (ceux connus à la décision) ; Baum-Welch réajusté aux décisions `refits` (booléens)
    sur tout ce passé, départ = ajustement précédent ; NaN avant `minimum` rendements."""
    out = np.full(len(fins), np.nan)
    params = None
    for d, end in enumerate(fins):
        if end < minimum:
            continue
        x = log_rendements[:end]
        if params is None or refits[d]:
            init = params if params is not None else hmm_depart(x, etats)
            params = _baum_welch(x, *init, HMM_ITERATIONS, HMM_TOLERANCE)
        start, trans, mean, var = params
        filtered, _ = _avant(_emissions(x, mean, var), start, trans)
        out[d] = filtered[-1, int(np.argmax(var))]
    return out


def hmm3(frame: Panneau, ctx: Contexte) -> Poids:
    """RG02 (forme b, hebdomadaire) : top 40 à poids égaux sauf si la probabilité filtrée de l'état le plus volatil
    (HMM gaussien à 3 états sur les rendements journaliers de BTC, réestimé chaque mois sur le passé) dépasse 0,5."""
    p = ctx.params
    idx, decisions, execution = decisions_hebdomadaires(frame)
    eligible = frame.membre[idx] & ~np.isnan(frame.cloture[idx])
    volatile = np.full(len(idx), np.nan)
    if BTC in frame.symboles and len(idx):
        close = frame.cloture[:, frame.symboles.index(BTC)]
        rows = np.flatnonzero(~np.isnan(close))
        returns = np.diff(np.log(close[rows]))              # rendement k : de rows[k] à rows[k + 1]
        ends = np.searchsorted(rows[1:], idx, side="right")  # rendements dont la journée de fin ≤ décision
        months = mois_de(decisions)
        refit = np.ones(len(idx), dtype=bool)
        refit[1:] = months[1:] != months[:-1]
        volatile = probabilite_etat_volatil(returns, ends, refit, p["etats"])
    calm = ~ind.superieur(volatile, np.full(len(volatile), p["seuil"]))
    count = eligible.sum(axis=1, keepdims=True)
    weights = np.where(eligible & calm[:, None], 1.0 / np.maximum(count, 1), 0.0)
    return Poids(decisions, execution, frame.symboles, weights, univers=eligible)
