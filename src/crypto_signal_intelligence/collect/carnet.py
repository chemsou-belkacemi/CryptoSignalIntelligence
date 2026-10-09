"""Source CARNET : flux public `wss://stream.binance.com:9443/stream?streams=<sym>@depth20@1000ms` (20 meilleurs
niveaux de chaque côté, toutes les secondes) pour les 16 paires de `data.symbols` plus les paires des appels actifs de
l'assistant (`state/assistant.json`, LECTURE SEULE, relu toutes les 5 min), 40 paires au plus.

Toutes les 5 s par paire, sur le dernier carnet reçu : meilleur achat/vente, écart (%), USDT cumulés à ±0,5 % et
±1 % du milieu, déséquilibre (achats − ventes) / (achats + ventes). LIMITE : depth20 ne couvre que 20 niveaux ; pour
une paire liquide, ±0,5 % dépasse souvent ces 20 niveaux, la profondeur relevée est alors un MINIMUM (`truncated`).

Entrées (journal `C_CARNET-AAAA-MM.jsonl`) :
- `CARNET_5S` : seulement si un champ (écart, profondeur achat/vente à ±0,5 % ou ±1 %) change de plus de 10 % par
  rapport à la dernière entrée ÉCRITE de la paire, ou si le déséquilibre à ±1 % bouge de plus de 0,10 (absolu) ; au
  moins `MIN_WRITE_GAP_SECONDS` (10 min) entre deux entrées d'une même paire et au plus `MAX_WRITES_PER_HOUR` par
  heure CALENDAIRE (sinon le journal explose : sur BTC ou ETH, depth20 est tronqué et ses 20 niveaux changent de
  plus de 10 % en permanence, les écritures partiraient en rafale) ; c'est un échantillon, pas la mesure ;
- `CARNET_RESUME` : par heure et par paire : min / max / moyenne de l'écart, des profondeurs et du déséquilibre sur
  TOUS les échantillons de 5 s (pas seulement ceux écrits), nombre d'échantillons et d'entrées écrites. C'est la
  MESURE DE RÉFÉRENCE du carnet.
"""
from __future__ import annotations

import json
import logging

from ..config import Settings
from .base import CARNET, Context, Reload, iso_ms

log = logging.getLogger("csi.collect.carnet")

BASE_URL = "wss://stream.binance.com:9443/stream?streams="
SAMPLE, RESUME = "CARNET_5S", "CARNET_RESUME"
BANDS = (0.005, 0.01)
SAMPLE_SECONDS = 5.0
STALE_SECONDS = 15.0            # carnet plus vieux : pas d'échantillon (flux figé)
PAIRS_REFRESH_SECONDS = 300.0
MAX_PAIRS = 40
MAX_WRITES_PER_HOUR = 6
MIN_WRITE_GAP_SECONDS = 600.0
CHANGE_RATIO = 0.10
IMBALANCE_DELTA = 0.10
TRACKED = ("spread_pct", "bid_0.5", "ask_0.5", "bid_1", "ask_1")
RESUME_FIELDS = ("spread_pct", "bid_0.5", "ask_0.5", "bid_1", "ask_1", "imb_1")


def _band(band: float) -> str:
    return f"{band * 100:g}"


def book_sample(bids: list, asks: list) -> dict:
    """Mesures d'un carnet à 20 niveaux (`[[prix, qté], …]`) : prix, écart, profondeur en USDT et déséquilibre par
    bande, `truncated` par bande et par côté (vrai = les 20 niveaux ne couvrent pas la bande : minimum)."""
    b = sorted(((float(p), float(q)) for p, q in bids if float(p) > 0 and float(q) > 0), key=lambda x: -x[0])
    a = sorted(((float(p), float(q)) for p, q in asks if float(p) > 0 and float(q) > 0), key=lambda x: x[0])
    if not b or not a or a[0][0] <= b[0][0]:
        raise ValueError("carnet vide ou croisé")
    mid = (b[0][0] + a[0][0]) / 2
    out: dict = {"bid": b[0][0], "ask": a[0][0], "spread_pct": round((a[0][0] - b[0][0]) / mid * 100, 5),
                 "truncated": {}}
    for band in BANDS:
        key = _band(band)
        bid_usdt = sum(p * q for p, q in b if p >= mid * (1 - band))
        ask_usdt = sum(p * q for p, q in a if p <= mid * (1 + band))
        total = bid_usdt + ask_usdt
        out[f"bid_{key}"] = round(bid_usdt)
        out[f"ask_{key}"] = round(ask_usdt)
        out[f"imb_{key}"] = round((bid_usdt - ask_usdt) / total, 4) if total > 0 else None
        out["truncated"][key] = [bool(b[-1][0] > mid * (1 - band)), bool(a[-1][0] < mid * (1 + band))]
    return out


def changed(previous: dict | None, current: dict) -> bool:
    """Vrai si un champ suivi bouge de plus de `CHANGE_RATIO` (relatif) ou le déséquilibre de `IMBALANCE_DELTA`."""
    if previous is None:
        return True
    for key in TRACKED:
        before, after = previous.get(key), current.get(key)
        if before is None or after is None:
            if before != after:
                return True
            continue
        if before == 0:
            if after != 0:
                return True
            continue
        if abs(after - before) / abs(before) > CHANGE_RATIO:
            return True
    before, after = previous.get("imb_1"), current.get("imb_1")
    if before is None or after is None:
        return before != after
    return abs(after - before) > IMBALANCE_DELTA


class PairTracker:
    """Par paire : dernière entrée écrite, plafond horaire, accumulateurs du résumé horaire."""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.last_written: dict | None = None
        self.last_write_ms: int | None = None
        self.hour: int | None = None
        self.samples = 0
        self.written = 0
        self.acc: dict[str, list[float]] = {}         # champ → [min, max, somme, n]

    def _accumulate(self, sample: dict) -> None:
        self.samples += 1
        for key in RESUME_FIELDS:
            value = sample.get(key)
            if value is None:
                continue
            slot = self.acc.get(key)
            if slot is None:
                self.acc[key] = [value, value, value, 1]
            else:
                slot[0], slot[1], slot[2], slot[3] = min(slot[0], value), max(slot[1], value), slot[2] + value, slot[3] + 1

    def resume(self, hour_ms: int) -> dict:
        stats = {k: {"min": v[0], "max": v[1], "mean": round(v[2] / v[3], 5)} for k, v in self.acc.items()}
        return {"hour": iso_ms(hour_ms), "symbol": self.symbol, "samples": self.samples, "written": self.written,
                "max_writes_per_hour": MAX_WRITES_PER_HOUR, "stats": stats}

    def observe(self, sample: dict, *, now_ms: int, mono: float) -> list[tuple[str, dict]]:
        """Un échantillon de 5 s : rend les entrées à écrire (résumé de l'heure close, puis CARNET_5S si retenu)."""
        out: list[tuple[str, dict]] = []
        hour = now_ms - now_ms % 3_600_000
        if self.hour is not None and hour != self.hour and self.samples:
            out.append((RESUME, self.resume(self.hour)))
            self.acc, self.samples, self.written = {}, 0, 0
        self.hour = hour
        self._accumulate(sample)
        spaced = self.last_write_ms is None or now_ms - self.last_write_ms >= MIN_WRITE_GAP_SECONDS * 1000
        if spaced and self.written < MAX_WRITES_PER_HOUR and changed(self.last_written, sample):
            self.last_write_ms = now_ms
            self.last_written = sample
            self.written += 1
            out.append((SAMPLE, {"time": iso_ms(now_ms), "symbol": self.symbol, **sample}))
        return out


def assistant_pairs(settings: Settings) -> list[str]:
    """Paires des appels actifs de l'assistant (lecture seule de `state/assistant.json` ; vide si absent)."""
    path = settings.root / "state" / "assistant.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return [str(c["symbol"]) for c in payload.get("active_calls") or [] if c.get("symbol")]
    except (ValueError, OSError, TypeError, AttributeError):
        return []


def tracked_pairs(settings: Settings) -> list[str]:
    pairs = list(dict.fromkeys([*settings.data.symbols, *assistant_pairs(settings)]))
    return [p for p in pairs if p.endswith("USDT")][:MAX_PAIRS]


def stream_url(pairs: list[str]) -> str:
    return BASE_URL + "/".join(f"{p.lower()}@depth20@1000ms" for p in pairs)


def parse(message: dict) -> tuple[str, dict] | None:
    """(paire, carnet) d'un message du flux combiné `{"stream": "btcusdt@depth20@1000ms", "data": {...}}`."""
    stream, data = message.get("stream"), message.get("data")
    if not isinstance(stream, str) or not isinstance(data, dict) or "@depth" not in stream:
        return None
    if not isinstance(data.get("bids"), list) or not isinstance(data.get("asks"), list):
        return None
    return stream.split("@")[0].upper(), data


class BookCollector:
    def __init__(self, ctx: Context):
        self.ctx = ctx
        self.trackers: dict[str, PairTracker] = {}
        self.latest: dict[str, tuple[float, dict]] = {}
        self.last_sample: float | None = None

    def sample_all(self, *, force: bool = False) -> int:
        mono = self.ctx.monotonic()
        if not force and self.last_sample is not None and mono - self.last_sample < SAMPLE_SECONDS:
            return 0
        self.last_sample = mono
        now = self.ctx.clock()
        now_ms = int(now.timestamp() * 1000)
        written = 0
        for symbol, (seen, book) in sorted(self.latest.items()):
            if mono - seen > STALE_SECONDS:
                continue
            try:
                sample = book_sample(book["bids"], book["asks"])
            except (ValueError, TypeError, KeyError):
                continue
            tracker = self.trackers.setdefault(symbol, PairTracker(symbol))
            for kind, data in tracker.observe(sample, now_ms=now_ms, mono=mono):
                self.ctx.recorder.append(CARNET, kind, data, now=self.ctx.clock())
                written += 1
        return written

    async def run_once(self) -> None:
        """Une connexion sur la liste de paires du moment ; sort (pour reconnexion) quand la liste change."""
        ctx = self.ctx
        pairs = tracked_pairs(ctx.settings)
        if not pairs:
            raise RuntimeError("aucune paire USDT à suivre")
        assert ctx.stream is not None
        refreshed = ctx.monotonic()
        async with ctx.stream(stream_url(pairs)) as messages:
            ctx.state.touch(CARNET, detail=f"{len(pairs)} paires (depth20@1000ms), échantillon toutes les {SAMPLE_SECONDS:g} s")
            async for message in messages:
                if ctx.stop.is_set():
                    break
                parsed = parse(message)
                now = ctx.clock()
                ctx.state.message(CARNET, now=now)
                if parsed is not None:
                    self.latest[parsed[0]] = (ctx.monotonic(), parsed[1])
                self.sample_all()
                ctx.state.write()
                if ctx.monotonic() - refreshed >= PAIRS_REFRESH_SECONDS:
                    refreshed = ctx.monotonic()
                    fresh = tracked_pairs(ctx.settings)
                    if fresh != pairs:
                        log.info("carnet : liste de paires changée, reconnexion")
                        raise Reload(f"liste de paires changée ({len(pairs)} → {len(fresh)})")


async def run(ctx: Context) -> None:
    await BookCollector(ctx).run_once()
