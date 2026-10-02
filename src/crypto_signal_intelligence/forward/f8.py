"""Test en direct F8_NEWS : filtre de news à mots-clés (gratuit, sans modèle de langage) appliqué au modèle A en
direct (docs/FORWARD_TESTS.md, section F8_NEWS ; règles figées au démarrage). Phase 4 de la mission du 2026-10-02.

Les actualités sont collectées en continu par CSI (flux RSS publics et annonces Binance, texte brut conservé,
`news/`). Chaque jour après 01:10 UTC, les titres vus depuis le dernier contrôle sont classés : gravité 3 négative
si le TITRE contient un terme de la liste figée ET cite un actif admis AVANT ce terme ; les termes de vol ne
s'appliquent jamais à BTC ni à ETH (cités comme unités dans presque tout article de piratage) ; tout autre cas
avec un terme est journalisé comme AMBIGU, sans effet. Effet : exposition à zéro sur l'actif pendant 7 jours dans
le portefeuille A + news, qui rejoue les décisions et les prix journalisés par F5_MODELE_A. Comparaison en direct
seulement : A contre A + news. Aucun backtest.
"""
from __future__ import annotations

import re
from datetime import datetime

import pandas as pd

from ..config import Settings
from ..news import assets as news_assets
from ..news.store import NewsStore
from . import f5
from .costs import CENTRAL, SCENARIOS
from .journal import Journal, utc_iso
from .registry import ForwardTest, code_fingerprint, journal_for

TEST_ID = "F8_NEWS"
BLOCK_DAYS_EFFECT = 7
VALUATION_AFTER = pd.Timedelta(hours=1, minutes=20)   # après la valorisation de F5 (01:10)
PROTECTED = frozenset({"BTC", "ETH"})
#: Termes de gravité 3 négative (liste FIGÉE au démarrage, anglais et français) ; `THEFT` : jamais pour BTC ni ETH.
THEFT_TERMS = ("hack", "hacked", "hacker", "hackers", "exploit", "exploited", "drained", "drain", "stolen", "theft",
               "piratage", "piraté", "piratée", "piratés", "exploité", "vol de fonds", "fonds volés", "volé", "volés", "siphonné")
OTHER_TERMS = ("delisting", "delist", "delists", "delisted", "sec lawsuit", "lawsuit", "sues", "sued", "charged", "charges",
               "indicted", "indictment", "insolvency", "insolvent", "bankruptcy", "bankrupt", "withdrawals suspended",
               "withdrawals halted", "withdrawals paused", "halts withdrawals", "suspends withdrawals", "depeg", "depegs",
               "depegged", "rug pull", "rugpull", "exit scam",
               "radiation", "radié", "retrait de la cote", "poursuite", "poursuites", "poursuit", "inculpé", "inculpation",
               "mis en examen", "insolvabilité", "insolvable", "faillite", "retraits suspendus", "retrait suspendu",
               "retraits gelés", "décrochage de la parité", "perd sa parité", "arnaque de sortie")
TERMS = THEFT_TERMS + OTHER_TERMS
NEWS, VALUATION, SKIPPED = "NEWS", "VALORISATION", "SANS_F5"
APPLIED, AMBIGUOUS = "APPLIQUE", "AMBIGU"
DONE, RUNNING = "SUIVI_TERMINE", "EN_COURS"
VARIANTS = ("A", "A_NEWS")


def _pattern(term: str) -> re.Pattern:
    return re.compile(rf"(?<![a-zà-ÿ0-9]){re.escape(term)}(?![a-zà-ÿ0-9])", re.IGNORECASE)


PATTERNS = {term: _pattern(term) for term in TERMS}


def classify(title: str, universe: list[str]) -> dict:
    """Classement d'un titre : APPLIQUE (actif cité avant le terme, règle stricte), AMBIGU (terme présent sans
    actif applicable ou plusieurs actifs) ou rien."""
    hits = [(m.start(), term) for term, pattern in PATTERNS.items() for m in [pattern.search(title)] if m]
    if not hits:
        return {"status": None}
    position, term = min(hits)
    before = title[:position]
    cited = news_assets.detect(before, universe)
    theft = term in THEFT_TERMS
    if theft:
        cited = [a for a in cited if a not in PROTECTED]
    if len(cited) == 1:
        return {"status": APPLIED, "asset": cited[0], "term": term, "theft": theft}
    all_cited = news_assets.detect(title, universe)
    reason = ("plusieurs actifs cités" if len(cited) > 1 else
              "actif cité après le terme" if all_cited else "aucun actif admis cité")
    if theft and any(a in PROTECTED for a in news_assets.detect(before, universe)) and not cited:
        reason = "terme de vol sur BTC ou ETH : jamais appliqué"
    return {"status": AMBIGUOUS, "assets": all_cited, "term": term, "theft": theft, "reason": reason}


def masked_until(news: list[dict], day: pd.Timestamp) -> dict[str, str]:
    """Actifs dont l'exposition est à zéro le jour `day` : news appliquées datées de moins de 7 jours."""
    out = {}
    for item in news:
        if item.get("status") != APPLIED:
            continue
        since = pd.Timestamp(item["day"], tz="UTC")
        until = since + pd.Timedelta(days=BLOCK_DAYS_EFFECT)
        if since <= day < until:
            out[item["asset"]] = str(until)[:10]
    return out


def _by(journal: Journal, kind: str, field: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for entry in journal.entries({kind}):
        out.setdefault(entry["data"][field], entry["data"])
    return out


def poll(settings: Settings, journal: Journal, start: dict, *, now: datetime, store: NewsStore | None = None,
         f5_journal: Journal | None = None) -> dict:
    moment = pd.Timestamp(now)
    day = moment.floor("D")
    key = str(day)[:10]
    if moment < day + VALUATION_AFTER or day < pd.Timestamp(start["started_at"]).floor("D") or day > pd.Timestamp(start["final_at"]).floor("D"):
        return {"valued": 0}
    valuations = _by(journal, VALUATION, "day")
    if key in valuations:
        return {"valued": 0}
    # --- news du jour : titres vus depuis le dernier contrôle ---
    universe = news_assets.base_assets(list(start["halal"]["symbols"]))
    news_log = _by(journal, NEWS, "item_id")
    seen_since = pd.Timestamp(max([v["day"] for v in valuations.values()], default=str(pd.Timestamp(start["started_at"]).floor("D"))[:10]),
                              tz="UTC")
    items = (store or NewsStore(settings.news_db)).recent(seen_since.to_pydatetime(), limit=2000) if (store or settings.news_db.exists()) else []
    counts = {"valued": 0, "scanned": 0, "applied": 0, "ambiguous": 0}
    for item in sorted(items, key=lambda i: i.get("first_seen_at") or ""):
        counts["scanned"] += 1
        if item["item_id"] in news_log:
            continue
        verdict = classify(item.get("title", ""), universe)
        if verdict["status"] is None:
            continue
        seen = pd.Timestamp(item.get("first_seen_at"))
        data = {"item_id": item["item_id"], "day": str(seen.floor("D"))[:10], "seen_at": utc_iso(seen), "source": item.get("source_id"),
                "title": str(item.get("title", ""))[:300], "published_at": item.get("published_at"), **verdict,
                "detector_code": code_fingerprint((news_assets.detect,))}
        journal.append(NEWS, data, now=moment)
        news_log[item["item_id"]] = data
        counts["applied" if verdict["status"] == APPLIED else "ambiguous"] += 1
    # --- valorisation : rejeu des décisions et prix de F5 ---
    source = f5_journal or journal_for(settings, f5.TEST_ID)
    f5_values = _by(source, f5.VALUATION, "day")
    if key not in f5_values:
        if journal.first(SKIPPED) is None or _by(journal, SKIPPED, "day").get(key) is None:
            journal.append(SKIPPED, {"day": key, "reason": "valorisation de F5 absente ce jour"}, now=moment)
        return counts
    today = f5_values[key]
    prices = today["prices"]
    week = today.get("week_decision")
    decision = _by(source, f5.DECISION, "week").get(str(week)) if week else None
    mask = masked_until(list(news_log.values()), day)
    previous_day = max(valuations) if valuations else None
    previous_prices = valuations[previous_day]["prices"] if previous_day else prices
    ledgers = valuations[previous_day]["ledgers"] if previous_day else {s: {v: f5.new_ledger() for v in VARIANTS} for s in SCENARIOS}
    out_ledgers: dict = {}
    for scenario in SCENARIOS:
        out_ledgers[scenario] = {}
        for variant in VARIANTS:
            ledger = f5.revalue(ledgers[scenario][variant], prices, previous_prices)
            targets = dict(decision["weights"]) if decision else None
            if variant == "A_NEWS" and mask:
                blocked = {f"{a}USDT" for a in mask}
                if targets is not None:
                    targets = {s: (0.0 if s in blocked else w) for s, w in targets.items()}
                exits = {s: 0.0 for s in ledger["holdings"] if s in blocked}
                if exits:
                    ledger = f5.rebalance(ledger, {**{s: v / ledger["value"] for s, v in ledger["holdings"].items()}, **exits},
                                          scenario=scenario, band=None, allow_buys=False, prices=prices)
            if targets is not None:
                ledger = f5.rebalance(ledger, targets, scenario=scenario, prices=prices)
            out_ledgers[scenario][variant] = ledger
    journal.append(VALUATION, {"day": key, "prices": prices, "week_decision": today.get("week_decision"), "masked": mask,
                               "ledgers": out_ledgers,
                               "values": {s: {v: round(led["value"], 6) for v, led in per.items()} for s, per in out_ledgers.items()},
                               "f5_a_value": (today.get("values") or {}).get(CENTRAL, {}).get("A")}, now=moment)
    counts["valued"] = 1
    return counts


def record_decisions(settings: Settings, journal: Journal, start: dict, *, now: datetime) -> dict:
    return poll(settings, journal, start, now=now)


def resolve(settings: Settings, journal: Journal, *, now: datetime) -> dict:
    return {}


def stats(journal: Journal, start: dict, *, now: datetime) -> dict:
    news = list(_by(journal, NEWS, "item_id").values())
    valuations = sorted(_by(journal, VALUATION, "day").values(), key=lambda v: v["day"])
    applied = [n for n in news if n["status"] == APPLIED]
    out: dict = {"news_items": len(news), "applied": len(applied), "ambiguous": sum(1 for n in news if n["status"] == AMBIGUOUS),
                 "by_asset": {a: sum(1 for n in applied if n["asset"] == a) for a in sorted({n["asset"] for n in applied})},
                 "terms": sorted({n["term"] for n in news})[:30], "days": len(valuations),
                 "masked_days": sum(len(v.get("masked") or {}) for v in valuations),
                 "skipped_days": sum(1 for _ in journal.entries({SKIPPED})), "scenarios": {}}
    for scenario in SCENARIOS:
        per: dict = {}
        for variant in VARIANTS:
            series = pd.Series({v["day"]: v["values"][scenario][variant] for v in valuations}, dtype=float).sort_index()
            last = valuations[-1]["ledgers"][scenario][variant] if valuations else f5.new_ledger()
            per[variant] = f5.describe(series) | {"trades": last["trades"], "fees_pct": round(last["fees"] * 100, 4)}
        a, news_r = per["A"].get("return"), per["A_NEWS"].get("return")
        per["A_NEWS_minus_A"] = round(news_r - a, 6) if a is not None and news_r is not None else None
        out["scenarios"][scenario] = per
    mismatch = [v["day"] for v in valuations if v.get("f5_a_value") is not None and abs(v["f5_a_value"] - v["values"][CENTRAL]["A"]) > 1e-6]
    out["f5_consistency"] = {"days": len(valuations), "mismatches": mismatch[:10]}
    ended = pd.Timestamp(now) >= pd.Timestamp(start["final_at"]) + pd.Timedelta(days=1)
    out["ended"] = bool(ended)
    out["verdict"] = f"{DONE}: {out['applied']} coupure(s), écart A+news − A {out['scenarios'][CENTRAL]['A_NEWS_minus_A']}" if ended else RUNNING
    return out


def finalize(journal: Journal, start: dict, *, now: datetime) -> str | None:
    from .registry import CLOSURE, VERDICT
    if journal.first(CLOSURE) is not None:
        return None
    result = stats(journal, start, now=now)
    if not result["ended"]:
        return None
    if journal.first(VERDICT) is None:
        journal.append(VERDICT, {"verdict": result["verdict"], "applied": result["applied"], "ambiguous": result["ambiguous"],
                                 "central": result["scenarios"][CENTRAL]}, now=now)
        return VERDICT
    journal.append(CLOSURE, {"pending": 0}, now=now)
    return CLOSURE


TEST = ForwardTest(
    test_id=TEST_ID, title="Filtre de news à mots-clés appliqué au modèle A en direct (A contre A + news)",
    hypothesis=("Mettre à zéro pendant 7 jours l'exposition d'un actif visé par une news de gravité 3 (termes figés, règle "
                "stricte d'association) change le résultat du modèle A en direct ; mesure descriptive, aucun seuil, "
                "aucun backtest."),
    params={"terms_theft": list(THEFT_TERMS), "terms_other": list(OTHER_TERMS), "protected": sorted(PROTECTED),
            "rule": "terme dans le titre, actif admis cité avant le terme, un seul actif ; vol jamais sur BTC/ETH",
            "effect_days": BLOCK_DAYS_EFFECT, "variants": list(VARIANTS), "replays": f5.TEST_ID,
            "detector": "news/assets.detect, non gelé : empreinte inscrite dans chaque news"},
    rule_objects=(), config_keys=(),
    frozen_modules=("crypto_signal_intelligence.forward.f8", "crypto_signal_intelligence.forward.costs",
                    "crypto_signal_intelligence.forward.registry", "crypto_signal_intelligence.forward.journal"),
    frozen_functions=(("crypto_signal_intelligence.forward.f5", "rebalance"), ("crypto_signal_intelligence.forward.f5", "revalue"),
                      ("crypto_signal_intelligence.forward.f5", "new_ledger"), ("crypto_signal_intelligence.forward.f5", "describe")),
)
