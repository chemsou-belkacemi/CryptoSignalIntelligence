"""Règles PURES de l'assistant de marché (docs/ASSISTANT.md), figées par le test en direct F18_ASSISTANT.

Tout ici est une fonction sans entrée-sortie : bougies clôturées en entrée, lecture en sortie. Les paramètres sont
des constantes déclarées A PRIORI (aucune n'a été réglée sur un résultat) ; en changer une change l'empreinte du
code et ARRÊTE le test F18. Aucune de ces règles n'a d'avantage démontré : F18 les mesure contre des placebos.

Lecture, à chaque clôture 4 h UTC, par paire :
1. régime journalier (EMA20 / EMA50 journalières, départ par moyenne simple comme `risk/market_light.ema`) ;
2. configuration : « repli puis reprise » en HAUSSE, « rejet du support » en RANGE ; jamais de cassure ;
3. niveaux : entrée à la clôture, stop sous le repli ou la mèche − 0,1 ATR(4 h), TP1 = +1 R, TP2 = résistance ;
4. gestion identique pour l'appel et ses placebos : stop à la clôture 4 h, stop de secours dur à −1,5 R touché en
   1 h, moitié à TP1 puis stop remonté à l'entrée, l'autre moitié à TP2, 10 jours au plus ; prudence : une bougie
   1 h qui touche à la fois un objectif et le stop de secours compte le stop.
"""
from __future__ import annotations

import hashlib
import random
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import numpy as np
import pandas as pd

from ..forward.costs import CENTRAL, costs_for
from ..patterns.indicators import ema as ema_series
from ..patterns.primitives import ZIGZAG_M, atr, zigzag
from ..patterns.volume import volume_profile
from ..technical.analysis import cluster_levels, round_tick

TEST_ID = "F18_ASSISTANT"
HOUR, H4, DAY = pd.Timedelta(hours=1), pd.Timedelta(hours=4), pd.Timedelta(days=1)
STEPS = {"1h": HOUR, "4h": H4, "1d": DAY}

# --- Paramètres déclarés (tableau de docs/ASSISTANT.md § 3) -------------------------------------------------------
HISTORY_DAYS = 200                 # bougies 1 h lues avant la clôture évaluée (EMA50 journalière stabilisée)
MIN_DAYS = 60                      # journées complètes exigées, sinon paire non évaluable
EMA_FAST, EMA_SLOW, SLOPE_DAYS = 20, 50, 3
RANGE_DAYS = 45                    # couloir et niveaux 4 h : pivots ZigZag des 45 derniers jours (ajusté le 2026-10-09)
RANGE_BARS = RANGE_DAYS * 6
PROFILE_DAYS = 30                  # VAL : profil de volume des 30 derniers jours
PROFILE_BARS = PROFILE_DAYS * 6
MERGE_ATR = 0.5                    # deux pivots à moins de 0,5 ATR(4 h) forment un même niveau
MIN_TOUCHES = 2                    # support et résistance du couloir : touchés ≥ 2 fois chacun
RANGE_HEIGHT_ATR = 3.0             # hauteur du couloir ≥ 3 ATR journaliers (ATR14 ; ajusté le 2026-10-09)
ATR_PERIOD = 14
PULLBACK_BARS = 5                  # repli : plus bas des 5 dernières bougies 4 h
TOUCH_BARS = 3                     # rejet : une des 3 dernières bougies 4 h touche le support
TOUCH_ATR = 0.25                   # « touche » : plus bas ≤ support + 0,25 ATR(4 h)
STOP_ATR = 0.10                    # stop = plus bas − 0,1 ATR(4 h)
VOLUME_BARS = 20                   # volume quote de la bougie de confirmation > moyenne des 20 précédentes
MIN_RESISTANCE_R = 1.0             # résistance à moins de 1 R au-dessus de l'entrée : refus
MIN_TP2_R_NET = 1.5                # TP2 net des frais taker aller-retour ≥ 1,5 R (ajusté le 2026-10-09)
SPREAD_MAX_PCT = 0.3               # carnet : écart < 0,3 %
SLIPPAGE_MAX_PCT = 0.2             # glissement d'un achat de 500 USDT < 0,2 %
BUY_SIZE_USDT = 500.0
IMBALANCE_BAND = "1"               # déséquilibre à ±1 % ≥ 0 (achats ≥ ventes)
STOP_VOL_MIN, STOP_VOL_MAX = 0.75, 3.0  # distance au stop entre 0,75 et 3 × le mouvement attendu sur 24 h (0,75 ajusté le 2026-10-09)
REALIZED_DAYS = 7                  # sans prévision : écart-type des rendements 1 h des 7 derniers jours × √24
MACRO_MARGIN = pd.Timedelta(hours=2)    # rien dans les 2 h avant/après un événement macro (jour entier : prudence)
NEWS_WINDOW = pd.Timedelta(hours=24)
MAX_CALLS_PER_DAY = 3
REST_HOURS = 48
HARD_STOP_FACTOR = 1.5             # stop de secours dur = entrée − 1,5 × (entrée − stop)
TP1_SHARE = 0.5
MAX_HOLD = pd.Timedelta(days=10)
MAX_DELAY = pd.Timedelta(minutes=30)   # évaluation plus de 30 min après la clôture : inscrite « late », aucun appel
PLACEBOS = 20
PLACEBO_MIN_H, PLACEBO_MAX_H = 5, 84    # clôtures 1 h dans [t − 84 h ; t + 84 h] hors [t − 4 h ; t + 4 h]

# --- Régimes, configurations, raisons ----------------------------------------------------------------------------
UP, DOWN, RANGE, UNDECIDED, UNREADABLE = "HAUSSE", "BAISSE", "RANGE", "INDECIS", "NON_EVALUABLE"
REGIMES = (UP, DOWN, RANGE, UNDECIDED, UNREADABLE)
PULLBACK, REJECTION = "REPLI_REPRISE", "REJET_SUPPORT"
NO_SETUP = "PAS_DE_CONFIGURATION"
RESISTANCE_NEAR = "RESISTANCE_PROCHE"
MACRO = "TIMING_MACRO"
NEWS = "NEWS_RISQUE"
NEWS_UNREACHABLE = "NEWS_INJOIGNABLES"
STOP_TIGHT, STOP_WIDE, VOL_UNKNOWN = "STOP_TROP_SERRE", "STOP_TROP_LARGE", "VOLATILITE_INCONNUE"
GAIN_RISK = "GAIN_RISQUE"
ACTIVE, REST, QUOTA = "APPEL_ACTIF", "REPOS_48H", "QUOTA_JOUR"
LATE = "EVALUATION_TARDIVE"
BOOK_UNREACHABLE, BOOK_SPREAD, BOOK_SLIPPAGE, BOOK_IMBALANCE = ("CARNET_INJOIGNABLE", "CARNET_ECART",
                                                                 "CARNET_GLISSEMENT", "CARNET_DESEQUILIBRE")
# Issues de la gestion
STOP_HARD, STOP_CLOSE, TP2, TIME, GAP, RUNNING = "STOP_SECOURS", "STOP_CLOTURE", "TP2", "TEMPS", "TROU", "EN_COURS"


# --- Bougies -----------------------------------------------------------------------------------------------------

def aggregate(h1: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Bougies 1 h, 4 h ou 1 jour (alignées sur 00:00 UTC) avec leur volume quote ; une bougie n'est gardée que si
    elle contient toutes ses heures (modèle `forward/f15.aggregate`, le volume en plus)."""
    columns = ["open_time", "open", "high", "low", "close", "quote_volume"]
    frame = h1[columns].copy()
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame = frame.sort_values("open_time").drop_duplicates("open_time")
    if timeframe == "1h":
        return frame.reset_index(drop=True)
    step = STEPS[timeframe]
    hours = int(step / HOUR)
    frame["bucket"] = frame["open_time"].dt.floor(step)
    out = frame.groupby("bucket").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"),
                                      close=("close", "last"), quote_volume=("quote_volume", "sum"),
                                      count=("open", "size")).reset_index()
    out = out[out["count"] == hours].rename(columns={"bucket": "open_time"})
    return out[columns].reset_index(drop=True)


def closed(frame: pd.DataFrame, timeframe: str, at: pd.Timestamp) -> pd.DataFrame:
    """Bougies clôturées au plus tard à `at` (clôture = ouverture + pas)."""
    return frame[frame["open_time"] + STEPS[timeframe] <= at].reset_index(drop=True)


# --- 1. Régime journalier ----------------------------------------------------------------------------------------

def daily_regime(daily: pd.DataFrame) -> dict:
    """Régime de la dernière journée complète : HAUSSE (clôture > EMA50, EMA20 > EMA50, EMA50 > EMA50 d'il y a 3
    jours), BAISSE (clôture < EMA50, EMA20 < EMA50), sinon INDECIS (le couloir, § RANGE, est jugé à part). INDECIS
    aussi sous MIN_DAYS journées. Rend les valeurs lues (clôture, EMA20, EMA50, ATR14 journalier)."""
    c = daily["close"].to_numpy(float)
    if len(c) < max(MIN_DAYS, EMA_SLOW + SLOPE_DAYS):
        return {"regime": UNDECIDED, "reason": f"{len(c)} journée(s) complète(s) (au moins {MIN_DAYS})"}
    e20, e50 = ema_series(c, EMA_FAST), ema_series(c, EMA_SLOW)
    a = atr(daily["high"].to_numpy(float), daily["low"].to_numpy(float), c, ATR_PERIOD)
    close, ema20, ema50, ema50_past, atr_d = float(c[-1]), float(e20[-1]), float(e50[-1]), float(e50[-1 - SLOPE_DAYS]), float(a[-1])
    if not all(np.isfinite(v) for v in (ema20, ema50, ema50_past, atr_d)):
        return {"regime": UNDECIDED, "reason": "moyennes non définies"}
    if close > ema50 and ema20 > ema50 and ema50 > ema50_past:
        regime, reason = UP, "clôture > EMA50, EMA20 > EMA50, EMA50 montante sur 3 jours"
    elif close < ema50 and ema20 < ema50:
        regime, reason = DOWN, "clôture < EMA50 et EMA20 < EMA50"
    else:
        regime, reason = UNDECIDED, "ni hausse ni baisse"
    return {"regime": regime, "reason": reason, "close": close, "ema20": ema20, "ema50": ema50,
            "ema50_past": ema50_past, "atr_d": atr_d, "day": daily["open_time"].iloc[-1]}


# --- Niveaux 4 h, couloir, zone de valeur ----------------------------------------------------------------------------

def levels_4h(h4: pd.DataFrame) -> dict:
    """Supports (pivots bas) et résistances (pivots hauts) des 45 derniers jours : ZigZag 4 h (m = 2,5 ATR), pivots
    regroupés à 0,5 ATR(4 h), avec leurs touches ; triés du plus proche de la clôture au plus loin."""
    h, lo, c = (h4[k].to_numpy(float) for k in ("high", "low", "close"))
    a = atr(h, lo, c, ATR_PERIOD)
    atr4 = float(a[-1]) if len(a) and np.isfinite(a[-1]) else float("nan")
    close = float(c[-1])
    if not np.isfinite(atr4) or atr4 <= 0:
        return {"atr4": atr4, "close": close, "supports": [], "resistances": []}
    n = len(c)
    recent = [p for p in zigzag(h, lo, a, ZIGZAG_M["4h"]) if p.index >= n - RANGE_BARS]
    lows = cluster_levels([(p.price, p.index) for p in recent if p.kind == "low"], MERGE_ATR * atr4)
    highs = cluster_levels([(p.price, p.index) for p in recent if p.kind == "high"], MERGE_ATR * atr4)
    supports = sorted([x for x in lows if x["price"] < close], key=lambda x: -x["price"])
    resistances = sorted([x for x in highs if x["price"] > close], key=lambda x: x["price"])
    return {"atr4": atr4, "close": close, "supports": supports, "resistances": resistances}


def corridor(levels: dict, atr_d: float) -> dict | None:
    """Couloir clair : support et résistance les plus proches touchés ≥ 2 fois chacun, hauteur ≥ 3 ATR journaliers."""
    support = next((s for s in levels["supports"] if s["touches"] >= MIN_TOUCHES), None)
    resistance = next((r for r in levels["resistances"] if r["touches"] >= MIN_TOUCHES), None)
    if support is None or resistance is None or not np.isfinite(atr_d) or atr_d <= 0:
        return None
    if resistance["price"] - support["price"] < RANGE_HEIGHT_ATR * atr_d:
        return None
    return {"support": support, "resistance": resistance, "height_atr_d": (resistance["price"] - support["price"]) / atr_d}


def value_area_low(h4: pd.DataFrame) -> float:
    """VAL (bas de la zone de valeur à 70 %) du profil de volume quote des 30 derniers jours de bougies 4 h."""
    recent = h4.iloc[-PROFILE_BARS:]
    return float(volume_profile(recent["high"], recent["low"], recent["quote_volume"]).val)


def value_zone(ema20_d: float, support: dict | None, val: float) -> tuple[float, float]:
    """Zone de valeur = [min(EMA20 journalière, dernier support 4 h) ; max(EMA20 journalière, VAL)]."""
    low = min(ema20_d, support["price"]) if support is not None else ema20_d
    return low, max(ema20_d, val)


def volume_confirms(h4: pd.DataFrame) -> tuple[bool, float]:
    """Volume quote de la dernière bougie > moyenne des 20 précédentes ; rend aussi le multiple."""
    v = h4["quote_volume"].to_numpy(float)
    if len(v) < VOLUME_BARS + 1:
        return False, float("nan")
    mean = float(v[-VOLUME_BARS - 1:-1].mean())
    if not mean > 0:
        return False, float("nan")
    return bool(v[-1] > mean), float(v[-1] / mean)


# --- 2. Configurations ----------------------------------------------------------------------------------------------

def pullback_setup(h4: pd.DataFrame, regime: dict, levels: dict) -> dict:
    """HAUSSE, « repli puis reprise » : le plus bas des 5 dernières bougies 4 h est entré dans la zone de valeur ET la
    bougie qui vient de clôturer clôture au-dessus de la zone avec un volume > moyenne des 20 précédentes."""
    if len(h4) < PROFILE_BARS:
        return {"ok": False, "reason": "moins de 30 jours de bougies 4 h"}
    support = levels["supports"][0] if levels["supports"] else None
    zone_low, zone_high = value_zone(regime["ema20"], support, value_area_low(h4))
    pull_low = float(h4["low"].iloc[-PULLBACK_BARS:].min())
    close = float(h4["close"].iloc[-1])
    detail = {"zone": [zone_low, zone_high], "pullback_low": pull_low, "support_touches": support["touches"] if support else 0}
    if not zone_low <= pull_low <= zone_high:
        return {"ok": False, "reason": "aucun repli dans la zone de valeur", **detail}
    if not close > zone_high:
        return {"ok": False, "reason": "clôture pas encore au-dessus de la zone de valeur", **detail}
    confirmed, multiple = volume_confirms(h4)
    detail["volume_multiple"] = multiple
    if not confirmed:
        return {"ok": False, "reason": "volume de confirmation sous la moyenne des 20 bougies", **detail}
    stop = pull_low - STOP_ATR * levels["atr4"]
    return {"ok": True, "setup": PULLBACK, "entry": close, "stop": stop, "touches": detail["support_touches"], **detail}


def rejection_setup(h4: pd.DataFrame, corridor_: dict, levels: dict) -> dict:
    """RANGE, « rejet du support » : une des 3 dernières bougies 4 h a touché le support (plus bas ≤ support + 0,25
    ATR) ET la bougie qui vient de clôturer clôture au-dessus du support avec un volume > moyenne des 20 précédentes."""
    support = corridor_["support"]["price"]
    last = h4.iloc[-TOUCH_BARS:]
    touching = last[last["low"] <= support + TOUCH_ATR * levels["atr4"]]
    close = float(h4["close"].iloc[-1])
    detail = {"support": support, "resistance": corridor_["resistance"]["price"], "support_touches": corridor_["support"]["touches"]}
    if touching.empty:
        return {"ok": False, "reason": "support non touché dans les 3 dernières bougies", **detail}
    if not close > support:
        return {"ok": False, "reason": "clôture pas au-dessus du support", **detail}
    confirmed, multiple = volume_confirms(h4)
    detail["volume_multiple"] = multiple
    if not confirmed:
        return {"ok": False, "reason": "volume de confirmation sous la moyenne des 20 bougies", **detail}
    wick_low = float(touching["low"].min())
    stop = wick_low - STOP_ATR * levels["atr4"]
    return {"ok": True, "setup": REJECTION, "entry": close, "stop": stop, "wick_low": wick_low,
            "touches": corridor_["support"]["touches"], **detail}


def _clean(value: float) -> float:
    """Efface le bruit binaire (100,12 + 4,8 = 104,92000000000002) avant l'arrondi au pas de cotation."""
    return float(f"{value:.12g}")


def targets(entry: float, stop: float, resistance: float | None, tick: Decimal | None) -> dict:
    """Niveaux arrondis au pas de cotation : entrée (achat) et objectifs vers le haut, stop vers le bas ; TP1 = +1 R ;
    TP2 = résistance, ou +2 R sans résistance. Refus si une résistance est à moins de 1 R au-dessus de l'entrée."""
    entry = round_tick(_clean(entry), tick, ROUND_CEILING)
    stop = round_tick(stop, tick, ROUND_FLOOR)
    if not stop < entry:
        return {"ok": False, "reason": NO_SETUP, "detail": "stop collé au prix"}
    risk = entry - stop
    if resistance is not None and resistance < entry + MIN_RESISTANCE_R * risk:
        return {"ok": False, "reason": RESISTANCE_NEAR,
                "detail": f"résistance à {(resistance - entry) / risk:.2f} R au-dessus de l'entrée (< 1 R)"}
    tp1 = round_tick(_clean(entry + risk), tick, ROUND_CEILING)
    tp2 = round_tick(_clean(resistance if resistance is not None else entry + 2 * risk), tick, ROUND_CEILING)
    hard = round_tick(_clean(entry - HARD_STOP_FACTOR * risk), tick, ROUND_FLOOR)
    return {"ok": True, "entry": entry, "stop": stop, "hard_stop": hard, "tp1": tp1, "tp2": tp2, "risk": risk,
            "r_tp2": (tp2 - entry) / risk, "tp2_source": "résistance 4 h" if resistance is not None else "entrée + 2 R"}


# --- 3. Filtres ---------------------------------------------------------------------------------------------------------

def tp2_net_r(entry: float, stop: float, tp2: float, symbol: str, scenario: str = CENTRAL) -> float:
    """R net de TP2 : vente à TP2 moins l'achat, frais et glissement taker aller-retour du scénario, en R."""
    c = costs_for(symbol, scenario)
    cost_in = entry * (1 + c.market) * (1 + c.fee)
    return (tp2 * (1 - c.market) * (1 - c.fee) - cost_in) / (entry - stop)


def book_check(book: dict | None, metrics) -> dict:
    """Liquidité à l'instant : carnet injoignable → refus ; écart < 0,3 % ; glissement d'un achat de 500 USDT < 0,2 % ;
    déséquilibre à ±1 % ≥ 0 (achats ≥ ventes). `metrics` = `forward.liquidity_log.book_metrics` (lecture)."""
    if book is None:
        return {"ok": False, "reason": BOOK_UNREACHABLE, "detail": "carnet injoignable"}
    try:
        m = metrics(book, sizes=(BUY_SIZE_USDT,))
    except ValueError as exc:
        return {"ok": False, "reason": BOOK_UNREACHABLE, "detail": f"carnet illisible : {exc}"}
    spread = m["spread_pct"]
    slip = m["slippage"]["buy"][f"{BUY_SIZE_USDT:g}"]
    band = m["depth"].get(IMBALANCE_BAND) or {}
    imbalance = band.get("imbalance")
    summary = {"spread_pct": spread, "slippage_pct": slip, "imbalance": imbalance,
               "bid_usdt": band.get("bid_usdt"), "ask_usdt": band.get("ask_usdt")}
    if not spread < SPREAD_MAX_PCT:
        return {"ok": False, "reason": BOOK_SPREAD, "detail": f"écart {spread:.3f} % (≥ {SPREAD_MAX_PCT} %)", **summary}
    if slip is None or not slip < SLIPPAGE_MAX_PCT:
        return {"ok": False, "reason": BOOK_SLIPPAGE, "detail": f"glissement de 500 USDT {slip} % (≥ {SLIPPAGE_MAX_PCT} %)", **summary}
    if imbalance is None or imbalance < 0:
        return {"ok": False, "reason": BOOK_IMBALANCE, "detail": f"déséquilibre à ±1 % {imbalance} (ventes > achats)", **summary}
    return {"ok": True, **summary}


def realized_move_24h_pct(h1: pd.DataFrame) -> float | None:
    """Volatilité réalisée des 7 derniers jours : écart-type des rendements 1 h × √24, en %."""
    c = h1["close"].to_numpy(float)[-REALIZED_DAYS * 24 - 1:]
    if len(c) < 48:
        return None
    r = np.diff(np.log(c))
    return float(r.std(ddof=1) * np.sqrt(24) * 100)


def stop_versus_volatility(entry: float, stop: float, move_24h_pct: float | None) -> dict:
    """Distance au stop entre 0,75 et 3 × le mouvement attendu sur 24 h."""
    stop_pct = (entry - stop) / entry * 100
    if move_24h_pct is None or not np.isfinite(move_24h_pct) or move_24h_pct <= 0:
        return {"ok": False, "reason": VOL_UNKNOWN, "stop_pct": stop_pct, "detail": "mouvement attendu inconnu"}
    ratio = stop_pct / move_24h_pct
    if ratio < STOP_VOL_MIN:
        return {"ok": False, "reason": STOP_TIGHT, "stop_pct": stop_pct, "ratio": ratio,
                "detail": f"stop à {stop_pct:.2f} % pour un mouvement attendu de {move_24h_pct:.2f} % (ratio {ratio:.2f} < {STOP_VOL_MIN})"}
    if ratio > STOP_VOL_MAX:
        return {"ok": False, "reason": STOP_WIDE, "stop_pct": stop_pct, "ratio": ratio,
                "detail": f"stop à {stop_pct:.2f} % pour un mouvement attendu de {move_24h_pct:.2f} % (ratio {ratio:.2f} > 3)"}
    return {"ok": True, "stop_pct": stop_pct, "ratio": ratio}


def macro_blocked(at: pd.Timestamp, events_of_day) -> list[str]:
    """Événements macro dont le JOUR UTC recouvre [t − 2 h ; t + 2 h] (l'heure de l'événement n'est pas connue :
    tout le jour est tenu pour sensible, lecture prudente)."""
    days = {f"{(at + d):%Y-%m-%d}" for d in (-MACRO_MARGIN, pd.Timedelta(0), MACRO_MARGIN)}
    return sorted({f"{day} {name}" for day in days for name in events_of_day(day)})


def news_hits(symbol: str, items: list[dict], at: pd.Timestamp) -> list[str]:
    """Titres de news de risque des 24 h précédant `at` (connues au plus tard à `at`) qui visent la paire : base dans
    les actifs étiquetés, ou base / symbole en mot entier du titre."""
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    since = at - NEWS_WINDOW
    out = []
    for item in items:
        seen = pd.Timestamp(item.get("risk_seen_at") or item.get("first_seen_at"))
        if seen.tzinfo is None:
            seen = seen.tz_localize("UTC")
        if not since <= seen <= at:
            continue
        words = {w.strip(".,:;!?()[]'\"") for w in str(item.get("title", "")).upper().split()}
        if base in {a.upper() for a in (item.get("assets") or [])} or base in words or symbol in words:
            out.append(str(item.get("title", ""))[:120])
    return out


# --- 4. Score (affiché, ne décide pas) ----------------------------------------------------------------------------

def score(*, touches: int, imbalance: float | None, volume_multiple: float, stop_ratio: float, btc_above_ema20: bool) -> dict:
    """0-100 : touches du niveau (0-25 : 5 touches = 25), déséquilibre du carnet à ±1 % (0-25 : +0,5 = 25), volume de
    la bougie de confirmation (0-20 : 3 × la moyenne = 20), distance au stop en multiple du mouvement H24 (0-15 :
    1 × = 0, 3 × = 15), BTC au-dessus de son EMA20 journalière (15). Le score classe les appels d'un même jour ; il ne
    décide d'aucun refus."""
    def clamp(x: float) -> float:
        return float(min(1.0, max(0.0, x)))
    parts = {"touches": round(25 * clamp(touches / 5), 1),
             "imbalance": round(25 * clamp((imbalance or 0.0) / 0.5), 1),
             "volume": round(20 * clamp((volume_multiple - 1) / 2), 1) if np.isfinite(volume_multiple) else 0.0,
             "stop": round(15 * clamp((stop_ratio - 1) / 2), 1),
             "btc": 15.0 if btc_above_ema20 else 0.0}
    return {"total": round(sum(parts.values()), 1), "parts": parts}


# --- 5. Gestion (appel et placebos) --------------------------------------------------------------------------------

def simulate(bars: pd.DataFrame, *, entry_at: pd.Timestamp, entry: float, stop: float, tp1: float, tp2: float,
             symbol: str, scenario: str) -> dict:
    """Une position achetée à `entry` à la clôture 1 h `entry_at` (frais et glissement taker), gérée sur les bougies
    1 h clôturées qui suivent (contiguës, sans trou) :
    - stop de secours dur à entrée − 1,5 × (entrée − stop), touché si plus bas ≤ niveau : sortie au niveau (ou à
      l'ouverture si elle est déjà dessous) ; une bougie qui touche aussi un objectif compte le stop (prudence) ;
    - TP1 : moitié à +1 R (plus haut ≥ TP1), puis stop de clôture remonté à l'entrée ; TP2 : l'autre moitié ;
    - stop à la clôture : à chaque clôture 4 h UTC, si la clôture ≤ stop de clôture, sortie à cette clôture ;
    - 10 jours au plus : sortie à la clôture de la dernière bougie 1 h.
    Sorties comptées au marché (taker) partout (prudence). R = résultat net / (entrée − stop).
    EN_COURS si les bougies manquent avant la résolution."""
    c = costs_for(symbol, scenario)
    risk = entry - stop
    hard = entry - HARD_STOP_FACTOR * risk
    cost_in = entry * (1 + c.market) * (1 + c.fee)

    def sell(price: float) -> float:
        return price * (1 - c.market) * (1 - c.fee)

    horizon = entry_at + MAX_HOLD
    frame = bars[(bars["open_time"] >= entry_at) & (bars["open_time"] < horizon)]
    remaining, proceeds, hits, close_stop = 1.0, 0.0, 0, stop
    expected = entry_at
    outcome, exit_at = None, None
    for row in frame.itertuples(index=False):
        if row.open_time != expected:
            return {"status": RUNNING}
        expected = row.open_time + HOUR
        o, h, lo, cl = float(row.open), float(row.high), float(row.low), float(row.close)
        if lo <= hard:
            proceeds += remaining * sell(min(hard, o))
            remaining, outcome, exit_at = 0.0, STOP_HARD, expected
            break
        if hits == 0 and h >= tp1:
            proceeds += TP1_SHARE * sell(tp1)
            remaining, hits, close_stop = remaining - TP1_SHARE, 1, entry
        if hits == 1 and h >= tp2:
            proceeds += remaining * sell(tp2)
            remaining, outcome, exit_at = 0.0, TP2, expected
            break
        if (expected - expected.floor("D")) % H4 == pd.Timedelta(0) and cl <= close_stop:
            proceeds += remaining * sell(cl)
            remaining, outcome, exit_at = 0.0, STOP_CLOSE, expected
            break
    if remaining > 1e-12:
        if expected < horizon:
            return {"status": RUNNING}
        last = frame.iloc[-1]
        proceeds += remaining * sell(float(last["close"]))
        outcome, exit_at = TIME, horizon
    label = outcome if hits == 0 or outcome == TP2 else f"{outcome}_APRES_TP1"
    r = (proceeds - cost_in) / risk
    return {"status": "RESOLU", "outcome": label, "hits": hits, "r": round(float(r), 6), "exit_at": exit_at,
            "entry_at": entry_at, "entry": entry}


def placebo_offsets(call_id: str) -> list[int]:
    """20 décalages en heures tirés sans remise dans [−84 ; −5] ∪ [5 ; 84], graine sha256("F18_ASSISTANT:" + id)."""
    rng = random.Random(int(hashlib.sha256(f"{TEST_ID}:{call_id}".encode()).hexdigest()[:16], 16))
    candidates = [*range(-PLACEBO_MAX_H, -PLACEBO_MIN_H + 1), *range(PLACEBO_MIN_H, PLACEBO_MAX_H + 1)]
    return sorted(rng.sample(candidates, PLACEBOS))


def placebo(bars: pd.DataFrame, *, at: pd.Timestamp, offset_h: int, entry: float, stop: float, tp1: float, tp2: float,
            symbol: str, scenario: str) -> dict:
    """Entrée au marché à la clôture 1 h `at + offset`, même géométrie en % que l'appel, même gestion, mêmes frais."""
    when = at + offset_h * HOUR
    ref = bars[bars["open_time"] == when - HOUR]
    if ref.empty:
        return {"status": GAP, "entry_at": when}
    q = float(ref["close"].iloc[0])
    return simulate(bars, entry_at=when, entry=q, stop=q * stop / entry, tp1=q * tp1 / entry, tp2=q * tp2 / entry,
                    symbol=symbol, scenario=scenario)


def latent_r(call: dict, last_close: float, hits: int) -> float:
    """R latent approché d'un appel en cours à la dernière clôture (brut de frais, affichage seulement)."""
    risk = call["entry"] - call["stop"]
    realized = TP1_SHARE * (call["tp1"] - call["entry"]) / risk if hits else 0.0
    share = 1 - TP1_SHARE if hits else 1.0
    return round(realized + share * (last_close - call["entry"]) / risk, 2)
