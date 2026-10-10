"""Registre déclaratif des 257 tests du zoo (docs/ZOO.md § 12) et interface UNIQUE des stratégies.

Interface (détaillée dans `research/zoo/README.md`) : une fonction PURE `signal(frame, ctx)`, sur des bougies
CLOSES, sans I/O, sans réseau, sans état global :
- forme (a) : `frame` = `Bougies` de l'unité d'exécution du test ; rend `Signaux(entree, sortie)`, deux tableaux
  booléens alignés sur les bougies (état vrai ou déclencheur ; condition de sortie évaluée à chaque clôture) ;
- forme (b) : `frame` = `Panneau` journalier de l'univers ; rend `Poids` (poids cibles à chaque décision de
  rééquilibrage, somme ≤ 1, le reste en USDT).

La valeur rendue à la bougie `t` ne doit dépendre que des bougies `≤ t` (et des séries externes dont l'`available_at`
précède la décision) : `causality.py` le vérifie par recalcul tronqué et futur falsifié.
"""
from __future__ import annotations

import importlib
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import numpy as np
import pandas as pd

from .data import Bougies, SerieExterne, Univers

FORME_A, FORME_B = "a", "b"
ETAT, EVENEMENT, PORTEFEUILLE = "etat", "evenement", "portefeuille"
# Références de la statistique de décision (§ 4.1 et § 4.2)
DERIVE_PAIRE = "derive_paire"           # m(p) : moyenne quotidienne brute de la paire sur ses journées de membre
R180 = "r180"                           # EV02 : univers des paires cotées depuis plus de 180 jours, le même jour
MEME_HEURE = "meme_heure"               # VO11 : rendement moyen de la bougie 23:00-24:00 de la paire
EXPOSITION_EGALE = "exposition_egale"   # f̄ × U + (1 − f̄) × USDT
MEME_COMPOSITION = "meme_composition"   # f̄ × A + (1 − f̄) × B (RO02, RO03)
UNIVERS_ELIGIBLE = "univers_eligible"   # membres éligibles à poids égaux (facteurs XS)
UNIVERS_TOP40 = "univers_top40"         # top 40 du moment à poids égaux (PF06, PF07, PF09)
PANIER_FLOTTANT = "panier_flottant"     # PF05 : même panier laissé flotter, renouvelé chaque trimestre
HEBDOMADAIRE, QUOTIDIENNE, MENSUELLE = "hebdomadaire", "quotidienne", "mensuelle"
CODE, A_CODER = "code", "a_coder"


# --- Interface des stratégies --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Signaux:
    """Forme (a). `entree[t]` : état vrai ou déclencheur à la clôture `t` ; `sortie[t]` : condition de sortie vraie à
    la clôture `t` (None : aucune règle de sortie, tenue jusqu'au plafond `N` ou à la fin de l'étape)."""
    entree: np.ndarray
    sortie: np.ndarray | None = None

    def __post_init__(self) -> None:
        if self.entree.dtype != bool or (self.sortie is not None and self.sortie.dtype != bool):
            raise TypeError("Signaux : tableaux booléens attendus")
        if self.sortie is not None and len(self.sortie) != len(self.entree):
            raise ValueError("Signaux : entrée et sortie de longueurs différentes")


@dataclass(frozen=True)
class Panneau:
    """Forme (b) : bougies journalières de l'univers (jours × paires), NaN quand la paire n'a pas de bougie complète.
    `membre[j, k]` : la paire `k` est membre du top 40 du mois de la décision prise à la clôture du jour `j`."""
    jours: np.ndarray                  # numéros de journée UTC (depuis 1970)
    symboles: tuple[str, ...]
    ouverture: np.ndarray
    haut: np.ndarray
    bas: np.ndarray
    cloture: np.ndarray
    volume: np.ndarray
    achat: np.ndarray
    membre: np.ndarray

    @property
    def disponible(self) -> np.ndarray:
        """Instant de décision à la clôture de chaque journée (minuit suivant, plus la latence)."""
        from .data import HOUR_NS, LATENCE_NS
        return (self.jours + 1) * 24 * HOUR_NS + LATENCE_NS

    def tete(self, n: int) -> Panneau:
        return Panneau(self.jours[:n], self.symboles, *(getattr(self, f)[:n] for f in
                                                       ("ouverture", "haut", "bas", "cloture", "volume", "achat",
                                                        "membre")))


@dataclass(frozen=True)
class Poids:
    """Forme (b) : poids cibles. `decisions` : instants de décision (ns) ; `execution_h` : heure de la clôture à
    laquelle l'échange se fait ; `poids[k, s]` ≥ 0, somme par ligne ≤ 1 (le reste en USDT)."""
    decisions: np.ndarray
    execution_h: np.ndarray
    symboles: tuple[str, ...]
    poids: np.ndarray
    #: univers de la référence à chaque décision (membres éligibles, top 40 ou BTC), booléens (décisions × paires)
    univers: np.ndarray | None = None
    #: rotations (RO02, RO03) : poids des actifs A et B à chaque décision, et « la stratégie est dans A »
    composante_a: np.ndarray | None = None
    composante_b: np.ndarray | None = None
    dans_a: np.ndarray | None = None

    def __post_init__(self) -> None:
        if self.poids.shape != (len(self.decisions), len(self.symboles)):
            raise ValueError("Poids : dimensions incohérentes")
        if (self.poids < -1e-12).any() or (self.poids.sum(axis=1) > 1 + 1e-9).any():
            raise ValueError("Poids : achat seul, somme ≤ 1")
        if (self.execution_h * 3_600_000_000_000 < self.decisions).any():
            raise ValueError("Poids : exécution avant la décision")


@dataclass(frozen=True)
class Contexte:
    """Ce qu'une stratégie peut lire en plus de ses bougies : paramètres, début de l'étape, bougies journalières de la
    même paire (tests à exécution 1 h), séries et événements externes (déjà coupés à la borne), univers à date."""
    symbole: str
    unite: int
    params: Mapping[str, Any]
    debut_etape_ns: int
    quotidien: Bougies | None = None
    externes: Mapping[str, SerieExterne] = field(default_factory=dict)
    evenements: Mapping[str, pd.DataFrame] = field(default_factory=dict)
    univers: Univers | None = None


Strategie = Callable[[Any, Contexte], Any]


# --- Registre ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Test:
    id: str                    # identifiant du test : stratégie + unité (« TR01_1h »)
    strategie: str             # identifiant du recensement (« TR01 »)
    famille: str               # TR, MR, VO, CH, ST, VF, XS, RO, AM, CA, SE, EV, RG, PF, AN
    unite: str                 # unité du signal (« 1h », « 4h », « 1j »)
    forme: str                 # « a » ou « b »
    type: str                  # état, événement ou portefeuille
    entree: str                # règle d'entrée (texte du § 12)
    sortie: str                # règle de sortie (texte du § 12)
    n_max: int | None          # plafond de tenue, en bougies de l'unité d'exécution (None : aucun)
    reference: str
    fonction: str              # « module:fonction » sous research/zoo/strategies
    params: Mapping[str, Any]
    execution: str             # unité d'exécution du moteur (« 1h » pour MR19, VO01, VO02, XS24 : signal journalier)
    frequence: str | None = None
    univers: str = "top40"     # « top40 » ; « recensement » (EV02) ; « btc » (AM)
    meilleure_paire: bool = True
    deja_vu: bool = False

    @property
    def execution_heures(self) -> int:
        from .data import UNITES
        return UNITES[self.execution]

    def strategie_fonction(self) -> Strategie:
        module, name = self.fonction.split(":")
        mod = importlib.import_module(f"{__package__}.strategies.{module}")
        return getattr(mod, name)

    @property
    def etat_code(self) -> str:
        """« code » si la fonction est implémentée (ensemble `IMPLEMENTEES` de son module), « a_coder » sinon."""
        module, name = self.fonction.split(":")
        mod = importlib.import_module(f"{__package__}.strategies.{module}")
        return CODE if name in getattr(mod, "IMPLEMENTEES", frozenset()) else A_CODER


def _p(**values: Any) -> Mapping[str, Any]:
    return MappingProxyType(dict(values))


H, Q, J = "1h", "4h", "1j"
TROIS = (H, Q, J)
_TESTS: list[Test] = []


def _a(sid: str, units: tuple[str, ...], kind: str, entree: str, sortie: str, n_max: int | None, fonction: str,
       params: Mapping[str, Any], *, execution: str | None = None, reference: str = DERIVE_PAIRE,
       univers: str = "top40", deja_vu: bool = False) -> None:
    for unit in units:
        _TESTS.append(Test(f"{sid}_{unit}", sid, sid[:2], unit, FORME_A, kind, entree, sortie, n_max, reference,
                           fonction, params, execution or unit, None, univers, True, deja_vu))


def _b(sid: str, frequence: str, entree: str, reference: str, fonction: str, params: Mapping[str, Any], *,
       execution: str = J, univers: str = "top40", meilleure_paire: bool = False, deja_vu: bool = False) -> None:
    _TESTS.append(Test(f"{sid}_{J}", sid, sid[:2], J, FORME_B, PORTEFEUILLE, entree, "rééquilibrage", None, reference,
                       fonction, params, execution, frequence, univers, meilleure_paire, deja_vu))


# 12.1 Forme (a) — Tendance (TR)
for _sid, _kind, _fast, _slow in (("TR01", "SMA", 5, 20), ("TR02", "EMA", 5, 20), ("TR03", "SMA", 10, 50),
                                  ("TR04", "EMA", 10, 50), ("TR05", "SMA", 20, 100), ("TR06", "EMA", 20, 100),
                                  ("TR07", "SMA", 50, 200), ("TR08", "EMA", 50, 200)):
    _a(_sid, TROIS, EVENEMENT, f"{_kind} {_fast} croise au-dessus de {_kind} {_slow}", "croisement inverse", None,
       "trend:croisement_moyennes", _p(moyenne=_kind, rapide=_fast, lente=_slow))
_a("TR09", TROIS, ETAT, "clôture > SMA 50", "clôture ≤ SMA 50", None, "trend:au_dessus_sma", _p(periode=50))
_a("TR10", TROIS, ETAT, "clôture > SMA 200", "clôture ≤ SMA 200", None, "trend:au_dessus_sma", _p(periode=200))
_a("TR12", TROIS, EVENEMENT, "clôture > plus haut des 20 bougies précédentes",
   "clôture < plus bas des 10 bougies précédentes", None, "trend:donchian", _p(entree=20, sortie=10), deja_vu=True)
_a("TR13", (Q, J), EVENEMENT, "clôture > plus haut des 55 précédentes", "clôture < plus bas des 20 précédentes", None,
   "trend:donchian", _p(entree=55, sortie=20), deja_vu=True)
_a("TR18", TROIS, EVENEMENT, "clôture croise au-dessus de EMA 20 + 2 ATR 10", "clôture < EMA 20", None,
   "trend:keltner", _p(ema=20, atr=10, k=2.0))
_a("TR19", TROIS, ETAT, "SuperTrend (10, 3) haussier", "bascule baissière", None, "trend:supertrend",
   _p(atr=10, k=3.0))
_a("TR20", TROIS, ETAT, "SAR (0,02 ; 0,20) sous le prix", "SAR au-dessus", None, "trend:sar", _p(pas=0.02, maximum=0.20))
_a("TR21", TROIS, ETAT, "ADX 14 > 25 et +DI > −DI", "+DI < −DI", None, "trend:adx_dmi", _p(n=14, seuil=25.0))
_a("TR22", TROIS, ETAT, "clôture > haut du nuage applicable (Senkou de t − 26)", "clôture ≤ haut du nuage", None,
   "trend:ichimoku_nuage", _p(tenkan=9, kijun=26, senkou=52, decalage=26))
_a("TR23", TROIS, EVENEMENT, "Tenkan croise au-dessus de Kijun, clôture > haut du nuage applicable", "Tenkan < Kijun",
   None, "trend:ichimoku_croisement", _p(tenkan=9, kijun=26, senkou=52, decalage=26))
_a("TR24", TROIS, EVENEMENT, "MACD croise au-dessus de son signal", "croisement inverse", None, "trend:macd_signal",
   _p(rapide=12, lente=26, signal=9))
_a("TR25", TROIS, ETAT, "MACD > 0", "MACD ≤ 0", None, "trend:macd_zero", _p(rapide=12, lente=26))
_a("TR26", TROIS, EVENEMENT, "Aroon haut croise au-dessus d'Aroon bas (25)", "croisement inverse", None, "trend:aroon",
   _p(n=25))
_a("TR27", TROIS, EVENEMENT, "VI+ croise au-dessus de VI− (14)", "croisement inverse", None, "trend:vortex", _p(n=14))
_a("TR28", TROIS, EVENEMENT, "TRIX 15 croise au-dessus de son signal 9", "croisement inverse", None, "trend:trix",
   _p(n=15, signal=9))
_a("TR29", TROIS, EVENEMENT, "clôture croise au-dessus de KAMA (10, 2, 30)", "clôture < KAMA", None, "trend:kama",
   _p(n=10, rapide=2, lente=30))
_a("TR30", TROIS, ETAT, "HMA 20 en hausse (HMA_t > HMA_{t−1})", "HMA en baisse", None, "trend:hull", _p(n=20))
_a("TR31", (J,), ETAT, "clôture ≥ 1,05 × plus bas des clôtures depuis la dernière sortie (ou le début de l'étape)",
   "clôture ≤ 0,95 × plus haut atteint depuis l'entrée", None, "trend:filtre_alexander", _p(x=0.05))
_a("TR32", (J,), ETAT, "clôture ≥ 1,10 × plus bas des clôtures depuis la dernière sortie (ou le début de l'étape)",
   "clôture ≤ 0,90 × plus haut atteint depuis l'entrée", None, "trend:filtre_alexander", _p(x=0.10))
for _sid, _n in (("TR33", 50), ("TR34", 150), ("TR35", 200)):
    _a(_sid, (J,), EVENEMENT, f"clôture > plus haut des {_n} jours précédents", "aucune (tenue fixe)", 10,
       "trend:cassure_plus_haut", _p(n=_n), deja_vu=True)
_a("TR36", (J,), EVENEMENT, "(max − min) des 20 jours précédents ≤ 10 % du min, et clôture > ce max", "aucune", 10,
   "trend:canal_etroit", _p(n=20, largeur=0.10))
_a("TR48", (J,), EVENEMENT, "point et figure (boîte 1 % log, retournement 3, clôtures) : colonne de X dépasse la "
   "précédente colonne de X", "colonne de O passe sous la précédente colonne de O", None, "trend:point_figure",
   _p(boite=0.01, retournement=3))

# Retour à la moyenne (MR)
_a("MR01", (J,), EVENEMENT, "RSI 2 < 10 et clôture > SMA 200", "clôture > SMA 5", 20, "mean_reversion:rsi2_connors",
   _p(rsi=2, seuil=10.0, sma=200, sortie_sma=5))
_a("MR02", TROIS, EVENEMENT, "RSI 14 croise au-dessus de 30", "RSI 14 > 70", 20, "mean_reversion:rsi_30_70",
   _p(n=14, bas=30.0, haut=70.0))
_a("MR03", TROIS, EVENEMENT, "clôture t−1 < bande basse, clôture t > bande basse (BB 20, 2)", "clôture ≥ bande médiane",
   20, "mean_reversion:bollinger_reintegration", _p(n=20, k=2.0))
_a("MR05", TROIS, EVENEMENT, "%K croise au-dessus de %D avec %K < 20 (14, 3, 3)", "%K > 80", 20,
   "mean_reversion:stochastique", _p(k=14, lissage=3, d=3, bas=20.0, haut=80.0))
_a("MR06", TROIS, EVENEMENT, "%R 14 croise au-dessus de −80", "%R > −20", 20, "mean_reversion:williams_r",
   _p(n=14, bas=-80.0, haut=-20.0))
_a("MR07", TROIS, EVENEMENT, "CCI 20 croise au-dessus de −100", "CCI > +100", 20, "mean_reversion:cci",
   _p(n=20, bas=-100.0, haut=100.0))
_a("MR09", (H,), EVENEMENT, "écart relatif à la VWAP du jour < −2 écarts-types (168 bougies précédentes)",
   "clôture ≥ VWAP du jour, ou dernière bougie de la journée UTC", 24, "mean_reversion:vwap_ecart",
   _p(fenetre=168, z=-2.0))
_a("MR11", TROIS, EVENEMENT, "rendement de t < −2 × écart-type des 20 rendements précédents", "aucune", 10,
   "mean_reversion:choc_rendement", _p(n=20, z=-2.0))
_a("MR12", (J,), EVENEMENT, "3 clôtures consécutives en baisse et clôture > SMA 200", "clôture > SMA 5", 20,
   "mean_reversion:trois_baisses", _p(baisses=3, sma=200, sortie_sma=5))
_a("MR19", (J,), EVENEMENT, "IBS = (C − L) / (H − L) < 0,2 ; entrée à la clôture de la 1re bougie 1 h qui s'ouvre "
   "après l'available_at du signal", "24 h après l'entrée", 24, "mean_reversion:ibs", _p(seuil=0.2), execution=H)
_a("MR20", (J,), EVENEMENT, "clôture > SMA 200 et clôture = plus bas des 7 dernières",
   "clôture = plus haut des 7 dernières", 20, "mean_reversion:double_sept", _p(sma=200, n=7))

# Volatilité et range (VO)
_a("VO01", (J,), EVENEMENT, "1re clôture 1 h du jour > ouverture du jour + 0,5 × (plus haut − plus bas de la veille)",
   "clôture de la dernière bougie 1 h de la journée UTC", 24, "volatility:williams_cassure", _p(k=0.5), execution=H)
_a("VO02", (J,), EVENEMENT, "1re clôture 1 h > ouverture du jour + 0,5 × amplitude Dual Thrust (N = 4)", "idem", 24,
   "volatility:dual_thrust", _p(n=4, k=0.5), execution=H)
for _sid, _name, _start, _end in (("VO03", "asie", 0, 8), ("VO04", "londres", 7, 16), ("VO05", "new_york", 13, 22)):
    _a(_sid, (H,), EVENEMENT, f"1re clôture 1 h de la séance ({_name}, {_start:02d}:00-{_end:02d}:00 UTC) au-dessus du "
       "plus haut de la 1re heure", "dernière bougie 1 h de la séance", None, "volatility:ouverture_seance",
       _p(seance=_name, debut=_start, fin=_end))
_a("VO06", (J,), EVENEMENT, "clôture > plus haut d'un jour NR4 dans les 2 jours qui le suivent", "aucune", 10,
   "volatility:nr", _p(n=4, fenetre=2))
_a("VO07", (J,), EVENEMENT, "clôture > plus haut d'un jour NR7 dans les 2 jours qui le suivent", "aucune", 10,
   "volatility:nr", _p(n=7, fenetre=2))
_a("VO08", TROIS, EVENEMENT, "fin de la compression (BB 20/2 sort de KC 20/1,5 après au moins une bougie dedans) et "
   "momentum de Carter > 0", "2 baisses consécutives du momentum", 20, "volatility:squeeze",
   _p(bb=20, k_bb=2.0, kc=20, k_kc=1.5), deja_vu=True)
_a("VO09", TROIS, EVENEMENT, "clôture > clôture précédente + 1 × ATR 14", "aucune", 10, "volatility:cassure_atr",
   _p(n=14, k=1.0))
_a("VO11", (H,), EVENEMENT, "rendement de 00:00 à 22:00 UTC > 0 (signal à la clôture de 21:00-22:00)",
   "entrée à 23:00, sortie à la clôture de 23:00-24:00", 1, "volatility:momentum_intrajournalier",
   _p(heure_signal=22), reference=MEME_HEURE)

# Chandeliers (CH) : TA-Lib, tendance préalable du recensement
for _sid, _func, _trend in (("CH01", "CDLHAMMER", "baissiere"), ("CH02", "CDLINVERTEDHAMMER", "baissiere"),
                            ("CH03", "CDLENGULFING", "baissiere"), ("CH04", "CDLHARAMI", "baissiere"),
                            ("CH05", "CDLHARAMICROSS", "baissiere"), ("CH08", "CDLDRAGONFLYDOJI", "baissiere"),
                            ("CH10", "CDL3WHITESOLDIERS", "haussiere"), ("CH11", "CDL3INSIDE", "baissiere"),
                            ("CH12", "CDL3OUTSIDE", "baissiere"), ("CH13", "CDLBELTHOLD", "baissiere"),
                            ("CH15", "CDLRISEFALL3METHODS", "haussiere"), ("CH16", "CDLHOMINGPIGEON", "baissiere"),
                            ("CH17", "CDLMATCHINGLOW", "baissiere")):
    _a(_sid, TROIS, EVENEMENT, f"figure TA-Lib {_func} haussière à t, tendance préalable {_trend} (SMA 10)", "aucune",
       10, "candles:figure", _p(talib=_func, tendance=_trend, sma=10))
_a("CH20", TROIS, EVENEMENT, "1re bougie Heikin Ashi haussière sans mèche basse après une baissière",
   "1re HA baissière", None, "candles:heikin_ashi", _p())
_a("CH21", (H, J), EVENEMENT, "2e brique Renko haussière après une baissière (boîte ATR 14 journalier connue au 1er du "
   "mois, briques sur le passé seul)", "1re brique baissière", None, "candles:renko", _p(atr=14))

# Structure (ST)
_a("ST01", (Q, J), ETAT, "dernier pivot haut ZigZag confirmé > le précédent et dernier pivot bas confirmé > le "
   "précédent", "nouveau pivot bas confirmé plus bas que le précédent", None, "structure:dow",
   _p(m_haut=2.5, m_bas=2.0))
for _sid, _level in (("ST03", 0.382), ("ST04", 0.5), ("ST05", 0.618)):
    _a(_sid, TROIS, EVENEMENT, f"dernière jambe haussière ZigZag confirmée : 1re clôture > niveau {_level:.1%} après "
       "contact, avant tout nouveau pivot", "clôture ≥ H, ou clôture < B", 20, "structure:fibonacci",
       _p(niveau=_level))
_a("ST06", (H,), EVENEMENT, "plus bas ≤ S1 et clôture > S1 (pivots classiques), première fois du jour",
   "clôture ≥ P, sinon fin de journée UTC", 24, "structure:pivots", _p(type="classique", niveau="S1", mode="rebond"))
_a("ST07", (H,), EVENEMENT, "1re clôture du jour > R1", "clôture ≥ R2, sinon fin de journée", 24, "structure:pivots",
   _p(type="classique", niveau="R1", mode="cassure"))
_a("ST08", (H,), EVENEMENT, "plus bas ≤ L3 et clôture > L3 (Camarilla), première fois du jour",
   "clôture ≥ H3, sinon fin de journée", 24, "structure:pivots", _p(type="camarilla", niveau="L3", mode="rebond"))
_a("ST09", (H,), EVENEMENT, "1re clôture du jour > H4", "fin de journée", 24, "structure:pivots",
   _p(type="camarilla", niveau="H4", mode="cassure"))
_a("ST10", (H,), EVENEMENT, "plus bas ≤ S1 de Woodie et clôture > S1, première fois du jour",
   "clôture ≥ P, sinon fin de journée", 24, "structure:pivots", _p(type="woodie", niveau="S1", mode="rebond"))
_a("ST11", (J,), EVENEMENT, "clôture > plus haut des 365 jours précédents", "aucune", 20, "structure:plus_haut_annuel",
   _p(n=365), deja_vu=True)
_a("ST12", (J,), EVENEMENT, "clôture > plus haut de tout l'historique (≥ 365 jours)", "aucune", 20,
   "structure:plus_haut_historique", _p(minimum=365))
_a("ST13", (H, Q), EVENEMENT, "clôture croise au-dessus d'un niveau rond majeur", "aucune", 10,
   "structure:niveau_rond", _p())
_a("ST14", (H,), EVENEMENT, "1re clôture du jour > plus haut de la veille", "fin de journée UTC", 24,
   "structure:plus_haut_veille", _p())
_a("ST15", (Q,), EVENEMENT, "1re clôture de la semaine > plus haut de la semaine précédente",
   "fin de la semaine (dimanche 24:00 UTC)", 42, "structure:plus_haut_semaine", _p())

# Volume et flux (VF)
_a("VF01", TROIS, EVENEMENT, "OBV croise au-dessus de sa SMA 20", "croisement inverse", None, "volume_flow:obv",
   _p(sma=20))
_a("VF02", TROIS, EVENEMENT, "ligne A/D croise au-dessus de sa SMA 20", "croisement inverse", None,
   "volume_flow:ligne_ad", _p(sma=20))
_a("VF03", TROIS, ETAT, "oscillateur de Chaikin (3, 10) > 0", "≤ 0", None, "volume_flow:chaikin", _p(rapide=3, lente=10))
_a("VF04", TROIS, EVENEMENT, "MFI 14 croise au-dessus de 20", "MFI > 80", 20, "volume_flow:mfi",
   _p(n=14, bas=20.0, haut=80.0))
_a("VF05", TROIS, EVENEMENT, "volume > 5 × moyenne des 20 précédentes et clôture > ouverture", "aucune", 10,
   "volume_flow:choc_volume", _p(multiple=5.0, n=20))
_a("VF16", TROIS, ETAT, "Elder Force Index (EMA 13) > 0", "≤ 0", None, "volume_flow:force_elder", _p(ema=13))
_a("VF17", TROIS, EVENEMENT, "Klinger (34, 55) croise au-dessus de son signal 13", "croisement inverse", None,
   "volume_flow:klinger", _p(rapide=34, lente=55, signal=13))

# Sentiment, calendrier, événements, régimes, analystes
_a("SE01", (J,), EVENEMENT, "dernière valeur du Fear & Greed connue avant la décision passe sous 25 : achat de chaque "
   "membre", "aucune", 30, "sentiment:peur_extreme", _p(seuil=25.0))
for _sid, _event, _hour in (("CA09", "FOMC", "14:00"), ("CA10", "CPI", "08:30"), ("CA11", "NFP", "08:30")):
    _a(_sid, (H,), EVENEMENT, f"date connue à l'avance : achat de chaque membre à la clôture de la dernière bougie 1 h "
       f"qui se termine au plus tard 24 h avant la publication ({_event}, {_hour} New York)",
       "clôture de la dernière bougie 1 h qui se termine au plus tard à l'heure de publication", None,
       "calendrier:veille_publication", _p(evenement=_event, heure=_hour))
_a("CA13", (J,), EVENEMENT, "1re clôture journalière après une nouvelle lune", "1re clôture après la pleine lune suivante",
   None, "calendrier:cycle_lunaire", _p())
_a("EV02", (J,), EVENEMENT, "1re journée complète de cotation d'une paire annoncée (COTATION_SPOT, annonce connue avant "
   "la clôture de cette journée)", "aucune", 30, "events:cotation_binance", _p(categorie="COTATION_SPOT"),
   reference=R180, univers="recensement")
_a("RG03", (J,), ETAT, "pente filtrée (Kalman, tendance locale linéaire du log-prix) > 0", "pente ≤ 0", None,
   "regimes:kalman", _p())
_a("RG04", (J,), ETAT, "Hurst R/S (128 rendements) > 0,5 et rendement 20 j > 0", "condition fausse", None,
   "regimes:hurst", _p(n=128, rendement=20))
_a("RG05", (J,), ETAT, "entropie de permutation (ordre 3, 64 j) dans le tiers bas de ses 365 j et rendement 20 j > 0",
   "condition fausse", None, "regimes:entropie", _p(ordre=3, fenetre=64, historique=365, rendement=20))
_a("RG06", (J,), ETAT, "pente de la tendance de Haar (niveau 3, passé seul) > 0", "pente ≤ 0", None, "regimes:haar",
   _p(niveau=3))
_a("RG07", (Q, J), EVENEMENT, "sinusoïde d'Ehlers croise au-dessus de sa version avancée", "croisement inverse", None,
   "regimes:sinusoide_ehlers", _p())
_a("AN05", TROIS, EVENEMENT, "dernière jambe haussière ZigZag confirmée : contact de la zone 62-79 % puis 1re clôture "
   "> 62 %", "clôture ≥ sommet de la jambe, ou < son creux", 20, "analysts:ote", _p(bas=0.62, haut=0.79))
_a("AN06", (H, Q), EVENEMENT, "1er retour dans un breaker haussier, clôture au-dessus de la zone",
   "clôture sous la zone", 10, "analysts:breaker", _p())
_a("AN09", (Q, J), EVENEMENT, "pivots confirmés : clôture > sommet de la vague 1 après une vague 2 de 38,2 à 78,6 %",
   "clôture ≥ départ + 1,618 × vague 1, ou < creux de la vague 2", 20, "analysts:elliott_vague3",
   _p(bas=0.382, haut=0.786, objectif=1.618))
_a("AN13", (J,), ETAT, "swing de Gann haussier (dernier sommet de swing de 2 bougies dépassé)",
   "clôture sous le dernier creux de swing", None, "analysts:gann_swing", _p(n=2))

# 12.2 Forme (b)
_b("TR37", HEBDOMADAIRE, "chaque membre à 1/n si son rendement sur 7 j > 0, sinon sa part en USDT", EXPOSITION_EGALE,
   "trend:momentum_temporel", _p(jours=7))
_b("TR40", HEBDOMADAIRE, "chaque membre à 1/n si son rendement sur 84 j > 0, sinon sa part en USDT", EXPOSITION_EGALE,
   "trend:momentum_temporel", _p(jours=84))
for _sid, _var, _side, _extra in (("XS01", "rendement_7j", "haut", {}), ("XS09", "volume_28j", "bas", {}),
                                  ("XS10", "amihud_28j", "haut", {}), ("XS11", "max_28j", "bas", {}),
                                  ("XS12", "vol_idio_28j", "bas", {}), ("XS14", "beta_90j", "bas", {}),
                                  ("XS15", "asymetrie_28j", "bas", {}),
                                  ("XS17", "proximite_ath", "haut", {"cotation_min_jours": 365}),
                                  ("XS18", "proximite_52s", "haut", {}), ("XS19", "choc_volume_hebdo", "haut", {}),
                                  ("XS20", "flux_acheteur_7j", "haut", {}), ("XS23", "mad_21_200", "haut", {})):
    _b(_sid, HEBDOMADAIRE, f"quintile {_side} de {_var}", UNIVERS_ELIGIBLE, "factors:quintile",
       _p(variable=_var, cote=_side, **_extra), meilleure_paire=True, deja_vu=_sid == "XS20")
_b("XS24", QUOTIDIENNE, "quintile des plus fortes baisses de la veille ; achat à la clôture de la 1re bougie 1 h qui "
   "s'ouvre après l'available_at du signal, vente 24 h plus tard", UNIVERS_ELIGIBLE, "factors:retournement_jour",
   _p(), execution=H, meilleure_paire=True)
_b("RO02", HEBDOMADAIRE, "100 % ETH si ETHBTC > SMA 50 journalière, sinon 100 % BTC", MEME_COMPOSITION,
   "rotation:eth_btc", _p(sma=50))
_b("RO03", HEBDOMADAIRE, "altcoins membres à poids égaux si ≥ 75 % battent BTC sur 90 j, sinon 100 % BTC",
   MEME_COMPOSITION, "rotation:altseason", _p(part=0.75, jours=90))
_b("RO04", HEBDOMADAIRE, "top 40 à poids égaux si plus de la moitié clôture au-dessus de sa SMA 50, sinon USDT",
   EXPOSITION_EGALE, "rotation:largeur", _p(sma=50, part=0.5))
_b("RO05", HEBDOMADAIRE, "top 40 à poids égaux si la corrélation moyenne sur 30 j est sous sa médiane des 365 j "
   "précédents, sinon USDT", EXPOSITION_EGALE, "rotation:correlation", _p(fenetre=30, historique=365))
_b("RG02", HEBDOMADAIRE, "top 40 à poids égaux sauf si la probabilité filtrée de l'état le plus volatil (3 états, BTC) "
   "dépasse 0,5", EXPOSITION_EGALE, "regimes:hmm3", _p(etats=3, seuil=0.5))
_b("SE02", HEBDOMADAIRE, "top 40 à poids égaux sauf si la dernière valeur connue du Fear & Greed dépasse 75",
   EXPOSITION_EGALE, "sentiment:avidite", _p(seuil=75.0))
for _sid, _series, _rule in (("AM01", "SP500", "rendement 20 séances > 0"), ("AM02", "SP500", "dernière séance > 0"),
                             ("AM03", "GLD", "rendement 20 séances > 0"), ("AM04", "DGS10", "variation 20 séances < 0"),
                             ("AM06", "NASDAQ100", "rendement 20 séances > 0"),
                             ("AM07", "DXY_BCE", "variation 20 jours ouvrés < 0"),
                             ("AM08", "VIXCLS", "sous sa médiane des 365 jours précédents")):
    _b(_sid, QUOTIDIENNE, f"100 % BTC si {_series} : {_rule}, sinon USDT", EXPOSITION_EGALE, "other_markets:btc_si",
       _p(serie=_series, regle=_rule), univers="btc")
_b("PF05", MENSUELLE, "top 40 à poids égaux rééquilibré chaque semaine", PANIER_FLOTTANT, "portfolio:poids_egaux", _p())
_b("PF06", MENSUELLE, "poids ∝ 1 / volatilité journalière sur 30 j", UNIVERS_TOP40, "portfolio:inverse_volatilite",
   _p(jours=30))
_b("PF07", MENSUELLE, "minimum de variance long seul (covariance 90 j rétrécie de Ledoit-Wolf)", UNIVERS_TOP40,
   "portfolio:variance_minimale", _p(jours=90))
_b("PF09", MENSUELLE, "60 % BTC, 40 % ETH", UNIVERS_TOP40, "portfolio:btc_eth", _p(btc=0.6, eth=0.4))

TESTS: tuple[Test, ...] = tuple(_TESTS)
PAR_ID: Mapping[str, Test] = MappingProxyType({t.id: t for t in TESTS})
#: Nombre de tests par famille (docs/ZOO_CATALOGUE.md, « Comptes »).
ATTENDU_PAR_FAMILLE = MappingProxyType({"TR": 83, "MR": 23, "VO": 14, "CH": 44, "ST": 22, "VF": 21, "XS": 13, "RO": 4,
                                        "AM": 7, "CA": 4, "SE": 2, "EV": 1, "RG": 7, "PF": 4, "AN": 8})


def verifier_registre() -> dict[str, int]:
    """257 tests, 225 de forme (a) et 32 de forme (b), comptes par famille du recensement, identifiants uniques."""
    counts = Counter(t.famille for t in TESTS)
    problems = []
    if len(TESTS) != 257 or len(PAR_ID) != 257:
        problems.append(f"{len(TESTS)} tests ({len(PAR_ID)} identifiants uniques) au lieu de 257")
    forms = Counter(t.forme for t in TESTS)
    if forms != Counter({FORME_A: 225, FORME_B: 32}):
        problems.append(f"formes {dict(forms)} au lieu de 225 / 32")
    if dict(counts) != dict(ATTENDU_PAR_FAMILLE):
        problems.append(f"familles {dict(counts)} au lieu de {dict(ATTENDU_PAR_FAMILLE)}")
    if problems:
        raise AssertionError(" ; ".join(problems))
    return dict(counts)


def tests_de(familles: list[str] | None = None, ids: list[str] | None = None) -> list[Test]:
    out = [t for t in TESTS if (not familles or t.famille in familles) and (not ids or t.id in ids or t.strategie in ids)]
    if ids:
        unknown = sorted(set(ids) - {t.id for t in TESTS} - {t.strategie for t in TESTS})
        if unknown:
            raise KeyError(f"tests inconnus : {', '.join(unknown)}")
    return out
