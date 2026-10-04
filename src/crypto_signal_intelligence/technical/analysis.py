"""Analyse technique mécanique d'une paire (docs/ANALYSE_TECHNIQUE.md), à partir des définitions de
docs/INDICATEURS.md : supports et résistances (pivots ZigZag regroupés), structure (dernière cassure BOS/CHoCH),
figures récentes du détecteur de F15, zones FVG et order blocks encore actives, niveaux de la veille et de la
semaine, chiffres ronds, RSI et moyennes ; plan d'achat INDICATIF (long seulement) : stop sous le support le plus
proche, objectifs aux résistances suivantes, prix arrondis au pas de cotation.

Information seulement : bougies CLÔTURÉES, rien de postérieur ; aucune de ces lectures n'a d'avantage démontré
(F15 mesure les figures en direct) ; aucun signal n'est publié à partir de cette analyse.
"""
from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

import numpy as np
import pandas as pd

from ..patterns import figures as fg
from ..patterns import indicators as ind
from ..patterns import levels as lv
from ..patterns import smc
from ..patterns.primitives import ZIGZAG_M, atr, zigzag

TIMEFRAMES = {"1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4), "1d": pd.Timedelta(days=1)}
DISPLAY_BARS = 120                 # bougies montrées
LEVEL_LOOKBACK = 300               # pivots retenus pour les niveaux
MERGE_ATR = 0.5                    # deux pivots à moins de 0,5 ATR forment un même niveau
RECENT_BARS = 20                   # figure « récente » : détectée dans les 20 dernières bougies
BREAK_RECENT_BARS = 3              # cassure « en cours » : dans les 3 dernières bougies
STOP_ATR = 0.25                    # marge du stop sous le support
MIN_TP_GAP = 0.002                 # un objectif à moins de 0,2 % de l'entrée est ignoré
MAX_TARGETS = 3
WARNING = ("Lecture mécanique du graphique (définitions fixes de docs/INDICATEURS.md) : information seulement, aucun "
           "avantage démontré, aucun signal publié. Les figures sont mesurées en direct par F15 (verdict vers mars 2027).")


def aggregate(h1: pd.DataFrame, timeframe: str, now: pd.Timestamp) -> pd.DataFrame:
    """Bougies de l'unité de temps à partir des bougies 1 h (alignées sur 00:00 UTC, complètes et clôturées)."""
    frame = h1[["open_time", "open", "high", "low", "close"]].copy()
    frame["open_time"] = pd.to_datetime(frame["open_time"], utc=True)
    frame = frame[frame["open_time"] + TIMEFRAMES["1h"] <= now].sort_values("open_time")
    if timeframe != "1h":
        step = TIMEFRAMES[timeframe]
        hours = int(step / TIMEFRAMES["1h"])
        frame["bucket"] = frame["open_time"].dt.floor(step)
        grouped = frame.groupby("bucket")
        frame = grouped.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                            count=("open", "size")).reset_index().rename(columns={"bucket": "open_time"})
        frame = frame[(frame["count"] == hours) & (frame["open_time"] + step <= now)]
    return frame[["open_time", "open", "high", "low", "close"]].reset_index(drop=True)


def cluster_levels(prices: list[tuple[float, int]], tolerance: float) -> list[dict]:
    """Regroupe des prix de pivots (prix, indice) proches de moins de `tolerance` : niveau = moyenne, touches =
    nombre de pivots, dernière touche = indice le plus récent."""
    out: list[dict] = []
    for price, index in sorted(prices):
        if out and price - out[-1]["_last_price"] <= tolerance:
            level = out[-1]
            level["_sum"] += price
            level["touches"] += 1
            level["last_index"] = max(level["last_index"], index)
            level["_last_price"] = price
            level["price"] = level["_sum"] / level["touches"]
        else:
            out.append({"price": price, "touches": 1, "last_index": index, "_sum": price, "_last_price": price})
    return [{k: v for k, v in level.items() if not k.startswith("_")} for level in out]


def round_tick(value: float, tick: Decimal | None, rounding: str) -> float:
    if tick is None or tick <= 0:
        return float(f"{value:.8g}")
    return float((Decimal(str(value)) / tick).to_integral_value(rounding=rounding) * tick)


def buy_plan(close: float, supports: list[dict], resistances: list[dict], atr_last: float, *, bearish: bool,
             tick: Decimal | None) -> dict:
    """Plan INDICATIF (long seulement) : entrée à la dernière clôture, stop sous le support le plus proche moins
    0,25 ATR, objectifs aux résistances suivantes (au plus 3, au moins 0,2 % au-dessus de l'entrée) ; R de chaque
    objectif = (objectif − entrée) / (entrée − stop). Contexte baissier : pas d'achat."""
    if bearish:
        return {"state": "NO_TRADE", "reason": "contexte baissier (dernière cassure de structure vers le bas) : pas d'achat"}
    if not supports:
        return {"state": "NO_TRADE", "reason": "aucun support sous le prix : stop impossible à placer"}
    entry = round_tick(close, tick, ROUND_FLOOR)
    stop = round_tick(supports[0]["price"] - STOP_ATR * atr_last, tick, ROUND_FLOOR)
    if not stop < entry:
        return {"state": "NO_TRADE", "reason": "support collé au prix : stop impossible à placer"}
    risk = entry - stop
    targets: list[dict] = []
    for level in resistances:
        target = round_tick(level["price"], tick, ROUND_CEILING)
        if target > entry * (1 + MIN_TP_GAP) and (not targets or target > targets[-1]["price"]):
            targets.append({"price": target, "pct": (target - entry) / entry * 100, "r": (target - entry) / risk})
        if len(targets) == MAX_TARGETS:
            break
    if not targets:
        return {"state": "NO_TRADE", "reason": "aucune résistance au-dessus : pas d'objectif mécanique",
                "entry": entry, "stop": stop}
    return {"state": "PLAN", "entry": entry, "stop": stop, "stop_pct": (entry - stop) / entry * 100,
            "targets": targets, "first_r": targets[0]["r"],
            "note": "premier objectif à moins de 1 R : rapport gain/risque faible" if targets[0]["r"] < 1 else ""}


def analyze(h1: pd.DataFrame, timeframe: str, *, now: pd.Timestamp, symbol: str, tick: Decimal | None = None) -> dict:
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"unité de temps inconnue : {timeframe} (choix : {', '.join(TIMEFRAMES)})")
    now = pd.Timestamp(now)
    frame = aggregate(h1, timeframe, now)
    if len(frame) < 60:
        raise ValueError(f"historique trop court pour {symbol} en {timeframe} ({len(frame)} bougies)")
    o, h, lo, c = (frame[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    times = frame["open_time"]
    n = len(c)
    a = atr(h, lo, c)
    atr_last = float(a[-1])
    close = float(c[-1])
    pivots = zigzag(h, lo, a, ZIGZAG_M[timeframe])

    # Supports et résistances : pivots récents regroupés, puis séparés par rapport à la dernière clôture.
    recent = [p for p in pivots if p.index >= n - LEVEL_LOOKBACK]
    levels = cluster_levels([(p.price, p.index) for p in recent], MERGE_ATR * atr_last)
    for level in levels:
        level["last_touch"] = str(times.iloc[level["last_index"]])
        level["price"] = round_tick(level["price"], tick, "ROUND_HALF_EVEN")      # affiché au pas de cotation
    resistances = sorted([lv_ for lv_ in levels if lv_["price"] > close], key=lambda x: x["price"])
    supports = sorted([lv_ for lv_ in levels if lv_["price"] < close], key=lambda x: -x["price"])

    # Structure : dernière cassure (BOS / CHoCH), sur pivots fractals.
    breaks = smc.structure(h, lo, c, a)
    last_break = breaks[-1] if breaks else None
    structure = None
    if last_break is not None:
        structure = {"side": last_break.side, "kind": last_break.kind, "level": last_break.level,
                     "at": str(times.iloc[last_break.index]), "bars_ago": n - 1 - last_break.index,
                     "recent": n - 1 - last_break.index < BREAK_RECENT_BARS, "mss": last_break.mss}
    bearish = last_break is not None and last_break.side == "bear"

    # Figures récentes (détecteur de F15) et zones encore actives.
    figures: list[dict] = []
    pivot_price = {p.index: p.price for p in pivots}
    for f in fg.detect(o, h, lo, c, m=ZIGZAG_M[timeframe]):
        if f.detected_at < n - RECENT_BARS:
            continue
        points = [{"time": str(times.iloc[i]), "price": pivot_price.get(i, float(c[i]))} for i in f.anchors if i < n]
        figures.append({"family": f.family, "side": f.side, "detected_at": str(times.iloc[f.detected_at]),
                        "valid": f.valid, "entry": f.entry, "stop": f.stop, "targets": list(f.targets),
                        "points": points, "notes": {k: v for k, v in f.notes.items() if isinstance(v, (int, float, str))}})
    start = max(0, n - DISPLAY_BARS)
    gaps = [g for g in smc.fair_value_gaps(h, lo, a) if g.index >= start and g.filled_at is None]
    blocks = smc.order_blocks(o, h, lo, c, a)
    broken = {e.block.index for e in ind.block_events(blocks, h, lo, c) if e.kind == "BREAKER"}
    live_blocks = [b for b in blocks if b.index >= start and b.index not in broken]
    zones = ([{"kind": "FVG", "side": g.side, "bottom": g.bottom, "top": g.top, "from": str(times.iloc[g.index])}
              for g in gaps[-4:]]
             + [{"kind": "OB", "side": b.side, "bottom": b.bottom, "top": b.top, "from": str(times.iloc[b.index])}
                for b in live_blocks[-4:]])

    rsi = ind.rsi(c)
    ema50, ema200 = ind.ema(c, 50), ind.ema(c, 200)
    previous = lv.previous_levels(h1, now)
    rounds = lv.round_levels(close)
    plan = buy_plan(close, supports, resistances, atr_last, bearish=bearish, tick=tick)

    summary = []
    if structure and last_break is not None:
        trend = "haussière" if structure["side"] == "bull" else "baissière"
        summary.append(f"Tendance de structure : {trend} (dernière cassure {structure['kind']} du niveau "
                       f"{round_tick(float(last_break.level), tick, 'ROUND_HALF_EVEN'):.8g}, il y a {structure['bars_ago']} bougie(s)).")
        if structure["recent"]:
            summary.append("Cassure EN COURS : " + ("résistance cassée à la hausse." if structure["side"] == "bull"
                                                    else "support cassé à la baisse."))
    if resistances:
        r0 = resistances[0]
        summary.append(f"Résistance la plus proche : {r0['price']:.8g} (+{(r0['price'] / close - 1) * 100:.2f} %, "
                       f"{r0['touches']} touche(s)).")
    if supports:
        s0 = supports[0]
        summary.append(f"Support le plus proche : {s0['price']:.8g} ({(s0['price'] / close - 1) * 100:.2f} %, "
                       f"{s0['touches']} touche(s)).")
    if np.isfinite(rsi[-1]):
        summary.append(f"RSI 14 : {rsi[-1]:.0f}" + (" (suracheté)" if rsi[-1] > 70 else " (survendu)" if rsi[-1] < 30 else "") + ".")
    if np.isfinite(ema200[-1]):
        summary.append(f"Prix {'au-dessus' if close > ema200[-1] else 'en dessous'} de la moyenne 200.")
    for item in figures:
        summary.append(f"Figure récente : {item['family']} {'haussière' if item['side'] == 'bull' else 'baissière'} "
                       f"(détectée le {item['detected_at'][:16]}).")

    shown = frame.iloc[start:]
    return {
        "symbol": symbol, "timeframe": timeframe, "close": close, "last_open": str(times.iloc[-1]),
        "atr": atr_last, "tick": str(tick) if tick is not None else None,
        "bars": [[str(t), float(x1), float(x2), float(x3), float(x4)]
                 for t, x1, x2, x3, x4 in shown[["open_time", "open", "high", "low", "close"]].itertuples(index=False)],
        "resistances": resistances[:5], "supports": supports[:5], "structure": structure, "figures": figures,
        "zones": zones, "previous": previous, "round": rounds,
        "indicators": {"rsi14": float(rsi[-1]) if np.isfinite(rsi[-1]) else None,
                       "ema50": float(ema50[-1]) if np.isfinite(ema50[-1]) else None,
                       "ema200": float(ema200[-1]) if np.isfinite(ema200[-1]) else None},
        "plan": plan, "summary": summary, "warning": WARNING,
    }
