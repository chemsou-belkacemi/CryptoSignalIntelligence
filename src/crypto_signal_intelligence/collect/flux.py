"""Source FLUX : flux public `<sym>@aggTrade` (transactions agrégées Spot) pour les 16 paires de `data.symbols`.

Message Binance : `{"e": "aggTrade", "s": "BNBUSDT", "p": "0.001", "q": "100", "T": ms, "m": true, ...}` ;
`m` vrai = l'acheteur est le teneur de marché, donc le TAKER a vendu ; faux = le taker a acheté.

Entrées (journal `C_FLUX-AAAA-MM.jsonl`) :
- `FLUX_MINUTE` : UNE entrée par minute pour toutes les paires (`pairs` = `{paire: [achats taker USDT, ventes taker
  USDT, nombre, déséquilibre 1 min, déséquilibre 5 min, nombre de gros ordres, USDT des gros ordres]}`, ordre des
  champs = `FIELDS`, non répété dans chaque entrée pour tenir le budget), déséquilibre = (achats − ventes) /
  (achats + ventes), celui à 5 min sur les 5 dernières minutes closes (None tant qu'il n'y en a pas 5) ;
- `FLUX_GROS` : transaction agrégée d'un notionnel ≥ `LARGE_USDT` (paire, prix, quantité, notionnel, côté du taker,
  heure), au plus `MAX_LARGE_PER_MINUTE` par paire et par minute (les suivantes ne sont que comptées dans
  FLUX_MINUTE). Une transaction agrégée regroupe les exécutions d'un même ordre au même prix : une « grosse » ligne
  est bien un gros ordre au marché (ou une grosse limite consommée d'un coup).
"""
from __future__ import annotations

import logging
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

from .base import FLUX, Context, iso_ms, minute_floor

log = logging.getLogger("csi.collect.flux")

BASE_URL = "wss://stream.binance.com:9443/stream?streams="
MINUTE, LARGE = "FLUX_MINUTE", "FLUX_GROS"
LARGE_USDT = 50_000.0
FIELDS = ["taker_buy_usdt", "taker_sell_usdt", "n", "imbalance_1m", "imbalance_5m", "large_n", "large_usdt"]
MAX_LARGE_PER_MINUTE = 3
FLUSH_GRACE_MS = 3_000
WINDOW_5M = 5


@dataclass(frozen=True)
class Trade:
    symbol: str
    price: float
    quantity: float
    notional: float
    taker_buy: bool
    time_ms: int


def parse(message: dict) -> Trade | None:
    data = message.get("data") if "stream" in message else message
    if not isinstance(data, dict) or data.get("e") != "aggTrade":
        return None
    try:
        price, quantity = float(data["p"]), float(data["q"])
        time_ms = int(data["T"])
    except (KeyError, TypeError, ValueError):
        return None
    if price <= 0 or quantity <= 0 or time_ms <= 0:
        return None
    return Trade(str(data.get("s", "")), price, quantity, price * quantity, not bool(data.get("m")), time_ms)


def stream_url(pairs: list[str]) -> str:
    return BASE_URL + "/".join(f"{p.lower()}@aggTrade" for p in pairs)


def imbalance(buy: float, sell: float) -> float | None:
    total = buy + sell
    return round((buy - sell) / total, 3) if total > 0 else None


class MinuteAggregator:
    def __init__(self, *, large_usdt: float = LARGE_USDT):
        self.large_usdt = large_usdt
        self.minutes: dict[int, dict[str, list[float]]] = {}            # minute → paire → [achats, ventes, n]
        self.history: dict[str, deque[tuple[float, float]]] = {}       # paire → 5 dernières minutes (achats, ventes)

    def add(self, trade: Trade) -> dict | None:
        row = self.minutes.setdefault(minute_floor(trade.time_ms), {}).setdefault(trade.symbol, [0.0, 0.0, 0, 0, 0.0])
        row[0 if trade.taker_buy else 1] += trade.notional
        row[2] += 1
        if trade.notional >= self.large_usdt:
            row[3] += 1
            row[4] += trade.notional
            if row[3] > MAX_LARGE_PER_MINUTE:
                return None
            return {"symbol": trade.symbol, "taker_side": "BUY" if trade.taker_buy else "SELL", "price": trade.price,
                    "quantity": trade.quantity, "notional_usdt": round(trade.notional, 2), "time": iso_ms(trade.time_ms)}
        return None

    def flush(self, now_ms: int) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        for minute in sorted(self.minutes):
            if minute + 60_000 + FLUSH_GRACE_MS > now_ms:
                break
            pairs = self.minutes.pop(minute)
            compact = {}
            for symbol in sorted(pairs):
                buy, sell, n, large_n, large_usdt = pairs[symbol]
                history = self.history.setdefault(symbol, deque(maxlen=WINDOW_5M))
                history.append((buy, sell))
                imb5 = imbalance(sum(b for b, _ in history), sum(s for _, s in history)) if len(history) == WINDOW_5M else None
                compact[symbol] = [round(buy), round(sell), int(n), imbalance(buy, sell), imb5, int(large_n), round(large_usdt)]
            out.append((MINUTE, {"minute": iso_ms(minute), "pairs": compact,
                                 "taker_buy_usdt": round(sum(r[0] for r in pairs.values()), 2),
                                 "taker_sell_usdt": round(sum(r[1] for r in pairs.values()), 2)}))
        return out


def aggregate(messages: Iterable[dict], *, now_ms: int) -> list[tuple[str, dict]]:
    agg = MinuteAggregator()
    out: list[tuple[str, dict]] = []
    for message in messages:
        trade = parse(message)
        if trade is None:
            continue
        large = agg.add(trade)
        if large:
            out.append((LARGE, large))
    return out + agg.flush(now_ms)


async def run(ctx: Context) -> None:
    pairs = [p for p in ctx.settings.data.symbols if p.endswith("USDT")]
    if not pairs:
        raise RuntimeError("aucune paire USDT configurée")
    agg = MinuteAggregator()
    assert ctx.stream is not None
    async with ctx.stream(stream_url(pairs)) as messages:
        ctx.state.touch(FLUX, detail=f"{len(pairs)} paires (aggTrade)")
        async for message in messages:
            if ctx.stop.is_set():
                break
            trade = parse(message)
            now = ctx.clock()
            ctx.state.message(FLUX, now=now)
            if trade is not None:
                large = agg.add(trade)
                if large:
                    ctx.recorder.append(FLUX, LARGE, large, now=now)
            for kind, data in agg.flush(int(now.timestamp() * 1000)):
                ctx.recorder.append(FLUX, kind, data, now=ctx.clock())
            ctx.state.write()
