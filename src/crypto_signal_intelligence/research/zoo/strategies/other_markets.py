"""Autres marchés (AM01 à AM04, AM06 à AM08 ; AM05 retiré), forme (b), décision QUOTIDIENNE : 100 % BTC si le signal
du recensement (docs/ZOO_CATALOGUE.md § 9) est vrai, sinon USDT. Fonction PURE, séries externes déjà coupées.

Lectures déclarées :
- **valeur connue** à la décision `D` (00:00 UTC + latence, clôture journalière de la veille) : dernière ligne dont
  l'`available_at` est STRICTEMENT antérieur à `D` (`data.joindre`) ; le week-end et les jours fériés, la dernière
  séance connue est reprise (aucune ligne n'est créée) ;
- **« n séances »** (« n jours ouvrés » pour DXY_BCE) : n lignes de la série plus tôt, dans l'ordre de
  disponibilité, parmi les lignes connues à `D` (pour ces séries, cet ordre est celui des dates, vérifié sur le
  magasin) ; rendement = valeur / valeur n séances plus tôt − 1 ; variation = valeur − valeur n séances plus tôt ;
- **AM04** : DGS10 avec son propre `available_at` (`d` + 1, 16:15 New York + 2 h, plus tardif que `d` + 1 12:00 UTC) ;
- **AM08** : « sous sa médiane des 365 jours précédents » : dernière valeur connue strictement sous la médiane des
  valeurs dont la DATE tombe dans les 365 jours calendaires qui précèdent la date de cette valeur (exclue), connues à
  `D` ; indéfinie tant que la série ne couvre pas ces 365 jours ;
- un signal indéfini (série trop courte, absente) n'est pas vrai : USDT ;
- univers de la référence : BTC seul (colonne BTCUSDT, s'il a une clôture le jour de la décision).
"""
from __future__ import annotations

import numpy as np

from ..data import DAY_H, HOUR_NS, SerieExterne
from ..spec import Contexte, Panneau, Poids
from .commun import decisions_quotidiennes

IMPLEMENTEES: frozenset[str] = frozenset({"btc_si"})
DEPENDENT_ETAPE: frozenset[str] = frozenset()

BTC = "BTCUSDT"
#: Série du registre → clé du magasin de contexte (`h0.externes_reels`, `reel.externes_pour`).
SOURCES = {"SP500": "zoo_marches:SP500", "NASDAQ100": "zoo_marches:NASDAQ100", "GLD": "zoo_marches:GLD",
           "DGS10": "zoo_marches:DGS10", "VIXCLS": "zoo_marches:VIXCLS", "DXY_BCE": "zoo_dollar_bce:DXY_BCE"}
#: Texte de la règle au registre → (calcul, nombre de séances ou de jours).
REGLES = {"rendement 20 séances > 0": ("rendement", 20), "dernière séance > 0": ("rendement", 1),
          "variation 20 séances < 0": ("baisse", 20), "variation 20 jours ouvrés < 0": ("baisse", 20),
          "sous sa médiane des 365 jours précédents": ("mediane", 365)}
DAY_NS = DAY_H * HOUR_NS


def serie_de(ctx: Contexte, nom: str) -> SerieExterne | None:
    key = SOURCES.get(nom, nom)
    if key in ctx.externes:
        return ctx.externes[key]
    for k, serie in ctx.externes.items():
        if k.split(":")[-1] == nom:
            return serie
    return None


def indices_connus(serie: SerieExterne, decisions_ns: np.ndarray) -> np.ndarray:
    """Indice de la dernière ligne dont l'`available_at` est strictement antérieur à chaque décision (−1 : aucune)."""
    return np.searchsorted(serie.disponible, np.asarray(decisions_ns, dtype=np.int64), side="left") - 1


def condition(serie: SerieExterne | None, decisions_ns: np.ndarray, regle: str) -> tuple[np.ndarray, np.ndarray]:
    """(condition vraie, condition définie) à chaque décision."""
    if regle not in REGLES:
        raise KeyError(f"règle AM inconnue : {regle!r}")
    n_dec = len(decisions_ns)
    true, defined = np.zeros(n_dec, dtype=bool), np.zeros(n_dec, dtype=bool)
    if serie is None or len(serie) == 0:
        return true, defined
    kind, n = REGLES[regle]
    idx = indices_connus(serie, decisions_ns)
    value = serie.valeur
    if kind in ("rendement", "baisse"):
        ok = idx - n >= 0
        cur, past = value[np.where(ok, idx, 0)], value[np.where(ok, idx - n, 0)]
        with np.errstate(invalid="ignore", divide="ignore"):
            hit = (cur / past - 1.0 > 0) if kind == "rendement" else (cur - past < 0)
        defined = ok & ~np.isnan(cur) & ~np.isnan(past)
        return hit & defined, defined
    # médiane des 365 jours calendaires précédents (dates), parmi les lignes connues
    cache: dict[int, tuple[bool, bool]] = {}
    first_date = serie.date.min()
    for d, i in enumerate(idx):
        i = int(i)
        if i < 0:
            continue
        if i not in cache:
            date_i = serie.date[i]
            lo = date_i - n * DAY_NS
            if first_date > lo:
                cache[i] = (False, False)
            else:
                known = slice(0, i)                      # lignes connues avant la ligne i (ordre de disponibilité)
                dates, values = serie.date[known], value[known]
                window = values[(dates >= lo) & (dates < date_i)]
                window = window[~np.isnan(window)]
                cache[i] = (bool(len(window)) and bool(value[i] < np.median(window)), bool(len(window)))
        true[d], defined[d] = cache[i]
    return true & defined, defined


def btc_si(frame: Panneau, ctx: Contexte) -> Poids:
    """AM01-AM04, AM06-AM08 : 100 % BTC si la règle de la série est vraie à la décision, sinon USDT."""
    p = ctx.params
    idx, decisions, execution = decisions_quotidiennes(frame)
    weights = np.zeros((len(idx), len(frame.symboles)))
    universe = np.zeros_like(weights, dtype=bool)
    if BTC in frame.symboles:
        k = frame.symboles.index(BTC)
        universe[:, k] = ~np.isnan(frame.cloture[idx, k])
        hit, _ = condition(serie_de(ctx, p["serie"]), decisions, p["regle"])
        weights[:, k] = np.where(hit & universe[:, k], 1.0, 0.0)
    return Poids(decisions, execution, frame.symboles, weights, univers=universe)
