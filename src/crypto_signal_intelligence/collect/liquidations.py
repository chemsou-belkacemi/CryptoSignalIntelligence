"""Source LIQUIDATIONS : flux public `wss://fstream.binance.com/ws/!forceOrder@arr` (ordres de liquidation du marché
à terme USDⓈ-M, toutes les paires). Lecture seule : CSI ne négocie aucun contrat à terme.

Message Binance : `{"e": "forceOrder", "E": ms, "o": {"s": "BTCUSDT", "S": "SELL", "q": "0.014", "p": "9910",
"ap": "9910", "z": "0.014", "T": ms, ...}}`. Côté `SELL` = une position longue est liquidée (vendue de force),
`BUY` = une position courte est liquidée. Notionnel = prix moyen `ap` × quantité exécutée `z` (en USDT pour les
paires en USDT ; les autres cotations sont ignorées). Binance publie au plus un ordre par seconde et par paire
dans ce flux (le « plus gros » de la seconde) : le compte est donc un MINIMUM, dit dans la doc.

Entrées (journal `C_LIQUIDATIONS-AAAA-MM.jsonl`) :
- `LIQ_MINUTE` : par minute où le notionnel > 0 ; `pairs` = les `TOP_PAIRS` paires les plus liquidées de la minute
  (`[n, notionnel, longs liquidés, courts liquidés]` = `FIELDS`, non répété dans l'entrée), le reste dans `others` ;
- `LIQ_GROS` : chaque ordre d'un notionnel ≥ `LARGE_USDT` (brut : paire, côté, prix, quantité, notionnel, heure) ;
- `LIQ_RESUME` : par heure : totaux, nombre, part des longs, top 5 paires, minutes avec liquidation.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from .base import LIQUIDATIONS, Context, iso_ms, minute_floor

log = logging.getLogger("csi.collect.liquidations")

URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
MINUTE, LARGE, RESUME = "LIQ_MINUTE", "LIQ_GROS", "LIQ_RESUME"
LARGE_USDT = 100_000.0
TOP_PAIRS = 10
TOP_RESUME = 5
FIELDS = ["n", "notional_usdt", "long_liq_usdt", "short_liq_usdt"]
FLUSH_GRACE_MS = 3_000          # une minute est écrite 3 s après sa fin (messages en retard)


@dataclass(frozen=True)
class Liquidation:
    symbol: str
    side: str                   # SELL = long liquidé, BUY = court liquidé
    price: float
    quantity: float
    notional: float
    time_ms: int


def parse(message: dict) -> Liquidation | None:
    """Ordre de liquidation d'un message du flux ; None si le message n'en est pas un ou n'est pas coté en USDT."""
    order = message.get("o") if message.get("e") == "forceOrder" else None
    if not isinstance(order, dict):
        return None
    symbol = str(order.get("s", ""))
    if not symbol.endswith("USDT"):
        return None
    try:
        price = float(order.get("ap") or order.get("p") or 0)
        quantity = float(order.get("z") or order.get("q") or 0)
        time_ms = int(order.get("T") or message.get("E") or 0)
    except (TypeError, ValueError):
        return None
    side = str(order.get("S", ""))
    if price <= 0 or quantity <= 0 or time_ms <= 0 or side not in ("BUY", "SELL"):
        return None
    return Liquidation(symbol, side, price, quantity, price * quantity, time_ms)


class MinuteAggregator:
    """Agrège les liquidations par minute et par paire ; `flush` rend les entrées des minutes terminées."""

    def __init__(self, *, top_pairs: int = TOP_PAIRS, large_usdt: float = LARGE_USDT):
        self.top_pairs, self.large_usdt = top_pairs, large_usdt
        self.minutes: dict[int, dict[str, list[float]]] = {}       # minute → paire → [n, notionnel, longs, courts]
        self.hour: dict[str, list[float]] = {}
        self.hour_start: int | None = None
        self.hour_minutes = 0

    def add(self, liq: Liquidation) -> dict | None:
        """Ajoute un ordre ; rend l'entrée LIQ_GROS à écrire tout de suite s'il dépasse `large_usdt`."""
        minute = minute_floor(liq.time_ms)
        row = self.minutes.setdefault(minute, {}).setdefault(liq.symbol, [0, 0.0, 0.0, 0.0])
        row[0] += 1
        row[1] += liq.notional
        row[2 if liq.side == "SELL" else 3] += liq.notional
        if liq.notional >= self.large_usdt:
            return {"symbol": liq.symbol, "side": liq.side, "long_liquidated": liq.side == "SELL",
                    "price": liq.price, "quantity": liq.quantity, "notional_usdt": round(liq.notional, 2),
                    "time": iso_ms(liq.time_ms)}
        return None

    def _minute_entry(self, minute: int, pairs: dict[str, list[float]]) -> dict | None:
        total = sum(r[1] for r in pairs.values())
        if total <= 0:
            return None
        ranked = sorted(pairs.items(), key=lambda kv: -kv[1][1])
        top = {s: [int(r[0]), round(r[1]), round(r[2]), round(r[3])] for s, r in ranked[:self.top_pairs]}
        rest = ranked[self.top_pairs:]
        others = {"pairs": len(rest), "n": int(sum(r[0] for _, r in rest)),
                  "notional_usdt": round(sum(r[1] for _, r in rest), 2),
                  "long_liq_usdt": round(sum(r[2] for _, r in rest), 2),
                  "short_liq_usdt": round(sum(r[3] for _, r in rest), 2)}
        return {"minute": iso_ms(minute), "n": int(sum(r[0] for r in pairs.values())),
                "notional_usdt": round(total, 2), "long_liq_usdt": round(sum(r[2] for r in pairs.values()), 2),
                "short_liq_usdt": round(sum(r[3] for r in pairs.values()), 2), "pairs_count": len(pairs),
                "pairs": top, "others": others}

    def _accumulate_hour(self, pairs: dict[str, list[float]]) -> None:
        for symbol, row in pairs.items():
            acc = self.hour.setdefault(symbol, [0, 0.0, 0.0, 0.0])
            for i in range(4):
                acc[i] += row[i]
        self.hour_minutes += 1

    def _hour_entry(self, hour_start: int) -> dict:
        total = sum(r[1] for r in self.hour.values())
        longs = sum(r[2] for r in self.hour.values())
        ranked = sorted(self.hour.items(), key=lambda kv: -kv[1][1])[:TOP_RESUME]
        return {"hour": iso_ms(hour_start), "n": int(sum(r[0] for r in self.hour.values())),
                "notional_usdt": round(total, 2), "long_liq_usdt": round(longs, 2),
                "short_liq_usdt": round(total - longs, 2),
                "long_share": round(longs / total, 4) if total > 0 else None,
                "minutes_with_liquidations": self.hour_minutes, "pairs": len(self.hour),
                "top": [{"symbol": s, "n": int(r[0]), "notional_usdt": round(r[1], 2)} for s, r in ranked]}

    def flush(self, now_ms: int) -> list[tuple[str, dict]]:
        """Entrées (nature, données) des minutes closes depuis plus de `FLUSH_GRACE_MS`, puis du résumé horaire
        quand l'heure est passée. Les minutes sans notionnel ne produisent rien."""
        out: list[tuple[str, dict]] = []
        for minute in sorted(self.minutes):
            if minute + 60_000 + FLUSH_GRACE_MS > now_ms:
                break
            pairs = self.minutes.pop(minute)
            hour = minute - minute % 3_600_000
            if self.hour_start is not None and hour != self.hour_start:
                out.append((RESUME, self._hour_entry(self.hour_start)))
                self.hour, self.hour_minutes = {}, 0
            self.hour_start = hour
            entry = self._minute_entry(minute, pairs)
            if entry is not None:
                out.append((MINUTE, entry))
                self._accumulate_hour(pairs)
        current_hour = now_ms - now_ms % 3_600_000
        if self.hour_start is not None and current_hour > self.hour_start and not self.minutes \
                and now_ms >= current_hour + 60_000 + FLUSH_GRACE_MS:
            out.append((RESUME, self._hour_entry(self.hour_start)))
            self.hour, self.hour_minutes, self.hour_start = {}, 0, None
        return out


def aggregate(messages: Iterable[dict], *, now_ms: int) -> list[tuple[str, dict]]:
    """Fonction pure pour les tests : toutes les entrées produites par une liste de messages à l'instant `now_ms`."""
    agg = MinuteAggregator()
    out: list[tuple[str, dict]] = []
    for message in messages:
        liq = parse(message)
        if liq is None:
            continue
        large = agg.add(liq)
        if large:
            out.append((LARGE, large))
    return out + agg.flush(now_ms)


async def run(ctx: Context) -> None:
    """Une connexion : lit le flux jusqu'à sa coupure (le superviseur reconnecte) ; vide l'agrégat au fil des minutes."""
    agg = MinuteAggregator()
    assert ctx.stream is not None
    async with ctx.stream(URL) as messages:
        ctx.state.touch(LIQUIDATIONS, detail=f"flux {URL}")
        async for message in messages:
            if ctx.stop.is_set():
                break
            liq = parse(message)
            now = ctx.clock()
            ctx.state.message(LIQUIDATIONS, now=now)
            if liq is not None:
                large = agg.add(liq)
                if large:
                    ctx.recorder.append(LIQUIDATIONS, LARGE, large, now=now)
            for kind, data in agg.flush(int(now.timestamp() * 1000)):
                ctx.recorder.append(LIQUIDATIONS, kind, data, now=ctx.clock())
            ctx.state.write()
