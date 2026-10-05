"""IA locale qui lit les graphiques contre une règle de moyennes (docs/IA_GRAPHES.md, déclaré le 2026-10-05 avant code et
exécution) : 400 moments de 2025-04 à 2026-09 sur les 40 paires de recherche ; graphique 4 h anonyme et chiffres de la
carte d'analyse de CSI (connus au moment de la décision seulement) ; biais à 72 h demandé à un modèle Ollama local
(127.0.0.1) ; comparaison du rendement signé avec la règle « EMA 50 au-dessus de la 200 ». 1 essai par modèle."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import time
import urllib.request
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd

from ..config import Settings
from ..patterns.indicators import rsi as rsi_of
from ..technical.analysis import analyze
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode
from .universe import RESEARCH_UNIVERSE

KIND = "IA_BIAS"
STRATEGY = "IA_LOCALE"
MODELS = ("qwen2.5vl:7b", "gemma3:12b")
FROM = pd.Timestamp("2025-04-01", tz="UTC")
TO = pd.Timestamp("2026-09-27", tz="UTC")
N_MOMENTS = 400
SEED = 20261005
HORIZON = pd.Timedelta(hours=72)
STEP = pd.Timedelta(hours=4)
MIN_BARS = 250
SHOWN_BARS = 120
LEVEL = 1 - 0.05 / len(MODELS)
SAMPLES = 10_000
OLLAMA = "http://127.0.0.1:11434"
BIASES = ("HAUSSIER", "BAISSIER", "NEUTRE")
UNREADABLE = "ILLISIBLE"
SCORE = {"HAUSSIER": 1, "BAISSIER": -1, "NEUTRE": 0, UNREADABLE: 0}
BETTER, WORSE, NOT_BETTER, INSUFFICIENT = "MIEUX_QUE_LA_REGLE", "MOINS_BIEN_QUE_LA_REGLE", "PAS_MIEUX", "INSUFFISANT"
PROMPT = ("Tu es un analyste technique crypto expérimenté. Le graphique montre des bougies 4 h (paire et dates masquées) "
          "avec EMA 50/200, supports (S) et résistances (R), zones FVG / order blocks, figures détectées (en violet), "
          "volume et RSI. Données exactes calculées par un programme : {context}\n"
          "Analyse tout (tendance, structure, zones, figures, volume, flux, RSI, niveaux) en 4 phrases au plus, en "
          "français, puis donne ton biais pour les 72 prochaines heures : HAUSSIER (le prix sera plus haut dans 72 h), "
          "BAISSIER (plus bas) ou NEUTRE (pas d'avis). Réponds UNIQUEMENT en JSON.")
SCHEMA = {"type": "object", "properties": {"analyse": {"type": "string"},
                                           "biais": {"type": "string", "enum": list(BIASES)}},
          "required": ["analyse", "biais"]}


class LocalOnly(ValueError):
    """Le client IA n'appelle que le serveur local (aucune donnée envoyée dehors)."""


# --- Données ----------------------------------------------------------------------------------------------------

def four_hours(h1: pd.DataFrame) -> pd.DataFrame:
    """Bougies 4 h complètes (alignées sur 00:00 UTC) avec volume en USDT et achats agressifs."""
    f = h1[["open_time", "open", "high", "low", "close", "quote_volume", "taker_buy_quote_volume"]].copy()
    f["open_time"] = pd.to_datetime(f["open_time"], utc=True)
    f["b"] = f["open_time"].dt.floor(STEP)
    g = f.groupby("b").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                           vol=("quote_volume", "sum"), buy=("taker_buy_quote_volume", "sum"), n=("open", "size"))
    return g[g["n"] == 4].reset_index().rename(columns={"b": "open_time"}).drop(columns="n")


def known(h1: pd.DataFrame, t0: pd.Timestamp) -> pd.DataFrame:
    """Bougies 1 h clôturées au moment de la décision `t0` (rien après)."""
    times = pd.to_datetime(h1["open_time"], utc=True)
    return h1[times + pd.Timedelta(hours=1) <= t0].reset_index(drop=True)


def price_at(h1: pd.DataFrame, t: pd.Timestamp) -> float | None:
    """Clôture de la dernière bougie 1 h clôturée à `t` ; None si la bougie de l'heure précédant `t` manque."""
    times = pd.to_datetime(h1["open_time"], utc=True)
    hit = h1.loc[times == t - pd.Timedelta(hours=1), "close"]
    return float(hit.iloc[0]) if len(hit) else None


def sample_moments(frames: dict[str, pd.DataFrame], *, n: int = N_MOMENTS, seed: int = SEED,
                   start: pd.Timestamp = FROM, end: pd.Timestamp = TO) -> list[tuple[str, pd.Timestamp]]:
    """`n` moments tirés uniformément sans remise parmi les clôtures 4 h de [start ; end] précédées d'au moins
    MIN_BARS bougies 4 h ; reproductible (graine), triés."""
    pool: list[tuple[str, pd.Timestamp]] = []
    for symbol in sorted(frames):
        f = four_hours(frames[symbol])
        closes = f["open_time"] + STEP
        for k in range(MIN_BARS, len(f)):
            t = closes.iloc[k]
            if start <= t <= end:
                pool.append((symbol, t))
    rng = np.random.default_rng(seed)
    picked = rng.choice(len(pool), size=min(n, len(pool)), replace=False)
    return sorted((pool[i] for i in picked), key=lambda x: (x[1], x[0]))


# --- Ce que voit l'IA -----------------------------------------------------------------------------------------------

def context_of(past: pd.DataFrame, symbol: str, t0: pd.Timestamp) -> tuple[dict, dict, pd.DataFrame]:
    """Chiffres de la carte d'analyse de CSI à `t0` (bougies connues seulement) ; (contexte, analyse, bougies 4 h)."""
    a = analyze(past, "4h", now=t0, symbol=symbol)
    f4 = four_hours(past)
    f4 = f4[f4["open_time"] + STEP <= t0].reset_index(drop=True)
    last = f4.tail(6)
    flows = {"volume_6_dernieres_vs_moyenne_60": round(float(last["vol"].mean() / f4["vol"].tail(60).mean()), 2),
             "part_achats_agressifs_24h_pct": round(float(100 * last["buy"].sum() / last["vol"].sum()), 1)}
    structure = a["structure"]
    context = {"derniere_cloture": a["close"], "atr14": round(a["atr"], 6),
               "resistances": [{"prix": x["price"], "touches": x["touches"]} for x in a["resistances"][:3]],
               "supports": [{"prix": x["price"], "touches": x["touches"]} for x in a["supports"][:3]],
               "structure": {k: structure[k] for k in ("side", "kind", "level", "bars_ago", "mss")} if structure else None,
               "indicateurs": {k: round(v, 6) if v else v for k, v in a["indicators"].items()},
               "zones_fvg_ob": [{k: z[k] for k in ("kind", "side", "bottom", "top")} for z in a["zones"][-6:]],
               "niveaux_periode_precedente": a["previous"], "chiffres_ronds": a["round"],
               "figures_recentes": [{"famille": f["family"], "sens": f["side"], "entree": f["entry"], "stop": f["stop"],
                                     "objectifs": f["targets"]} for f in a["figures"][-4:]],
               "flux": flows}
    return context, a, f4


def chart_png(a: dict, f4: pd.DataFrame) -> bytes:
    """Graphique anonyme (ni paire ni date) : bougies 4 h, EMA 50/200, S/R, zones, figures, volume, RSI."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    bars = pd.DataFrame(a["bars"], columns=["t", "o", "h", "l", "c"]).tail(SHOWN_BARS).reset_index(drop=True)
    vol = f4.set_index(f4["open_time"].astype(str))["vol"].reindex(bars["t"]).to_numpy()
    closes = f4["close"].to_numpy(float)
    ema = {n: pd.Series(closes).ewm(span=n, adjust=False).mean().to_numpy()[-len(bars):] for n in (50, 200)}
    rsi = rsi_of(closes)[-len(bars):]
    fig, (ax, axv, axr) = plt.subplots(3, 1, figsize=(12, 9), dpi=100, sharex=True,
                                       gridspec_kw={"height_ratios": [5, 1, 1.4]})
    for i, r in bars.iterrows():
        color = "#26a69a" if r.c >= r.o else "#ef5350"
        ax.vlines(i, r.l, r.h, color=color, linewidth=1)
        ax.add_patch(plt.Rectangle((i - 0.35, min(r.o, r.c)), 0.7, max(abs(r.c - r.o), 1e-12), color=color))
        axv.bar(i, vol[i] if np.isfinite(vol[i]) else 0, color=color, width=0.7)
    ax.plot(ema[50], color="#ff9800", linewidth=1, label="EMA 50")
    ax.plot(ema[200], color="#3f51b5", linewidth=1, label="EMA 200")
    lo, hi = bars["l"].min(), bars["h"].max()
    for z in a["zones"][-6:]:
        if z["top"] < lo or z["bottom"] > hi:
            continue
        ax.axhspan(z["bottom"], z["top"], color="#26a69a" if z["side"] == "bull" else "#ef5350", alpha=0.10)
        ax.text(1, z["top"], f"{z['kind']} {'haussier' if z['side'] == 'bull' else 'baissier'}", fontsize=7, va="bottom")
    levels = [(x, "#ef5350", "R") for x in a["resistances"][:3]] + [(x, "#26a69a", "S") for x in a["supports"][:3]]
    for lv, color, tag in levels:
        ax.axhline(lv["price"], color=color, linestyle="--", linewidth=1)
        ax.text(len(bars) + 0.5, lv["price"], f"{tag} {lv['price']:.6g}", color=color, va="center", fontsize=8)
    last_open = pd.Timestamp(a["bars"][-1][0])
    for figure in a["figures"][-3:]:
        pts = [(len(bars) - 1 - (last_open - pd.Timestamp(p["time"])) / STEP, p["price"]) for p in figure["points"]]
        pts = [(x, y) for x, y in pts if x >= 0]
        if len(pts) >= 2:
            ax.plot([x for x, _ in pts], [y for _, y in pts], color="#9c27b0", linewidth=1, alpha=0.8)
            ax.text(pts[-1][0], pts[-1][1], figure["family"], color="#9c27b0", fontsize=7)
    axr.plot(rsi, color="#607d8b", linewidth=1)
    for y in (30, 70):
        axr.axhline(y, color="grey", linewidth=0.6, linestyle=":")
    axr.set_ylabel("RSI 14", fontsize=8)
    axv.set_ylabel("Volume", fontsize=8)
    ax.legend(fontsize=8, loc="upper left")
    ax.set_xlim(-1, len(bars) + 10)
    axr.set_xticks([])
    ax.set_title("Bougies 4 h (paire et dates masquées) — dernière bougie à droite", fontsize=10)
    for item in (ax, axv, axr):
        item.grid(alpha=0.2)
    fig.tight_layout()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", metadata={"Software": None})
    plt.close(fig)
    return buffer.getvalue()


def ema_rule(f4: pd.DataFrame) -> int:
    """Règle simple : +1 si l'EMA 50 des clôtures 4 h est au-dessus de l'EMA 200, sinon −1."""
    closes = pd.Series(f4["close"].to_numpy(float))
    return 1 if closes.ewm(span=50, adjust=False).mean().iloc[-1] > closes.ewm(span=200, adjust=False).mean().iloc[-1] else -1


# --- IA locale ------------------------------------------------------------------------------------------------------

def _local(url: str) -> str:
    host = urlparse(url).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise LocalOnly(f"serveur IA non local refusé : {url}")
    return url


def ask(model: str, prompt: str, image: bytes, *, url: str = OLLAMA, timeout: int = 900) -> dict:
    """Une question à Ollama en local : JSON imposé, température 0, graine fixe. Renvoie la réponse brute décodée."""
    body = json.dumps({"model": model, "prompt": prompt, "images": [base64.b64encode(image).decode()], "format": SCHEMA,
                       "stream": False, "options": {"temperature": 0, "seed": SEED, "num_ctx": 6144}}).encode()
    request = urllib.request.Request(_local(url) + "/api/generate", data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def model_digest(model: str, *, url: str = OLLAMA) -> str | None:
    with urllib.request.urlopen(_local(url) + "/api/tags", timeout=30) as response:
        tags = json.loads(response.read())
    return next((m.get("digest") for m in tags.get("models", []) if m.get("name") == model), None)


def parse_bias(raw: dict) -> tuple[str, str]:
    """(biais, analyse) ; une réponse sans JSON valide ou sans biais connu est ILLISIBLE."""
    try:
        answer = json.loads(raw.get("response", ""))
    except (json.JSONDecodeError, TypeError):
        return UNREADABLE, ""
    bias = answer.get("biais") if isinstance(answer, dict) else None
    if bias not in BIASES:
        return UNREADABLE, ""
    return bias, str(answer.get("analyse", ""))[:1000]


# --- Mesure ---------------------------------------------------------------------------------------------------------

def week_ci(values: np.ndarray, weeks: np.ndarray, *, level: float = LEVEL, samples: int = SAMPLES,
            seed: int = SEED) -> tuple[float, float] | None:
    """Intervalle de la moyenne par tirage avec remise des semaines calendaires (moments d'une même semaine ensemble)."""
    frame = pd.DataFrame({"w": weeks, "v": values}).groupby("w")["v"].agg(["sum", "count"])
    if len(frame) < 10:
        return None
    sums, counts = frame["sum"].to_numpy(), frame["count"].to_numpy()
    rng = np.random.default_rng(seed)
    picks = rng.integers(0, len(frame), size=(samples, len(frame)))
    draws = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    tail = (1 - level) / 2 * 100
    low, high = np.percentile(draws, [tail, 100 - tail])
    return round(float(low), 6), round(float(high), 6)


def verdict(ci: tuple[float, float] | None) -> str:
    if ci is None:
        return INSUFFICIENT
    return BETTER if ci[0] > 0 else WORSE if ci[1] < 0 else NOT_BETTER


def evaluate(rows: pd.DataFrame, *, samples: int = SAMPLES) -> dict:
    """Décision (écart de rendement signé IA − règle) et descriptif."""
    done = rows[rows["r72"].notna()].copy()
    r = done["r72"].to_numpy(float)
    s_ia = done["bias"].map(SCORE).to_numpy(float)
    s_rule = done["rule"].to_numpy(float)
    weeks = pd.to_datetime(done["decision_at"], utc=True).dt.strftime("%G-%V").to_numpy()
    diff = s_ia * r - s_rule * r
    ci = week_ci(diff, weeks, samples=samples)
    directional = done[done["bias"].isin(["HAUSSIER", "BAISSIER"])]
    right = (np.sign(directional["r72"]) == directional["bias"].map(SCORE)).mean() if len(directional) else None
    rule_right = (np.sign(done["r72"]) == done["rule"]).mean()
    big = done[done["r72"].abs() > 0.10]
    alone = week_ci(s_ia * r, weeks, samples=samples)
    return {
        "n": int(len(done)), "missing_return": int(rows["r72"].isna().sum()), "weeks": int(len(set(weeks))),
        "decision": {"diff_mean_pct": round(100 * float(diff.mean()), 4),
                     "ci_pct": [round(100 * x, 4) for x in ci] if ci else None, "level": LEVEL, "verdict": verdict(ci)},
        "ia_signed_mean_pct": round(100 * float((s_ia * r).mean()), 4),
        "ia_signed_ci_pct": [round(100 * x, 4) for x in alone] if alone else None,
        "rule_signed_mean_pct": round(100 * float((s_rule * r).mean()), 4),
        "share_up": round(float((r > 0).mean()), 4),
        "ia_right_share": round(float(right), 4) if right is not None else None, "rule_right_share": round(float(rule_right), 4),
        "biases": done["bias"].value_counts().to_dict(),
        "agreement_with_rule": round(float((done["bias"].map(SCORE) == done["rule"]).mean()), 4),
        "long_only": {"mean_r72_when_haussier_pct": round(100 * float(done.loc[done["bias"] == "HAUSSIER", "r72"].mean()), 4)
                      if (done["bias"] == "HAUSSIER").any() else None,
                      "mean_r72_when_rule_up_pct": round(100 * float(done.loc[done["rule"] == 1, "r72"].mean()), 4)
                      if (done["rule"] == 1).any() else None,
                      "mean_r72_all_pct": round(100 * float(r.mean()), 4)},
        "big_moves": {"rises": big[big["r72"] > 0]["bias"].value_counts().to_dict(),
                      "falls": big[big["r72"] < 0]["bias"].value_counts().to_dict()},
        "seconds_median": round(float(rows["seconds"].median()), 1) if "seconds" in rows else None,
    }


# --- Exécution --------------------------------------------------------------------------------------------------

def run(settings: Settings, *, model: str, now: datetime, progress: Callable[[str], None] | None = None,
        allow_dirty: bool = False, ask_fn: Callable[..., dict] = ask, digest_fn: Callable[[str], str | None] = model_digest,
        symbols: list[str] | None = None, n: int = N_MOMENTS) -> dict:
    from .long_history import load_long
    say = progress or (lambda _text: None)
    if model not in MODELS:
        raise ValueError(f"modèle non déclaré : {model} (déclarés : {', '.join(MODELS)})")
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    digest = digest_fn(model)
    if digest is None:
        raise ValueError(f"modèle absent du serveur local : {model} (ollama pull {model})")
    frames = {s: load_long(settings, s) for s in (symbols or list(RESEARCH_UNIVERSE))}
    moments = sample_moments(frames, n=n, start=FROM, end=TO)
    registry = ExperimentRegistry(settings.experiments_db)
    run_id = new_run_id("IABI")
    consultation = None
    if not registry.final_test_consulted(STRATEGY):         # consultation n° 3, inscrite avant le premier calcul
        consultation = registry.consult_final_test(run_id, STRATEGY)
    cache_path = settings.root / "ia_cache" / f"{model.replace(':', '_')}.jsonl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache = {}
    if cache_path.exists():
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            cache[item["key"]] = item
    prompt_hash = hashlib.sha256((PROMPT + json.dumps(SCHEMA)).encode()).hexdigest()[:16]
    rows = []
    for k, (symbol, t0) in enumerate(moments, 1):
        h1 = frames[symbol]
        past = known(h1, t0)
        context, a, f4 = context_of(past, symbol, t0)
        image = chart_png(a, f4)
        key = f"{model}|{digest}|{prompt_hash}|{symbol}|{t0.isoformat()}|{hashlib.sha256(image).hexdigest()[:16]}"
        if key in cache:
            item = cache[key]
        else:
            started = time.time()
            try:
                raw = ask_fn(model, PROMPT.format(context=json.dumps(context, ensure_ascii=False, default=str)), image)
            except Exception as exc:  # noqa: BLE001 - une panne du serveur local est comptée illisible
                raw = {"response": "", "error": f"{type(exc).__name__}: {exc}"}
            bias, analysis = parse_bias(raw)
            item = {"key": key, "bias": bias, "analysis": analysis, "seconds": round(time.time() - started, 1)}
            with cache_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")
        p0, p1 = price_at(h1, t0), price_at(h1, t0 + HORIZON)
        rows.append({"symbol": symbol, "decision_at": t0, "bias": item["bias"], "analysis": item["analysis"],
                     "seconds": item["seconds"], "rule": ema_rule(f4),
                     "r72": (p1 / p0 - 1) if p0 and p1 else None})
        say(f"{k}/{len(moments)} {symbol} {item['bias']}")
    table = pd.DataFrame(rows)
    result = evaluate(table)
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "model": model, "digest": digest, "prompt_hash": prompt_hash, "n_trials": 1,
               "consultation": consultation, "result": result, "doc": "docs/IA_GRAPHES.md"}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    table.to_parquet(report_dir / "moments.parquet", index=False)
    registry.record(run_id=run_id, created_at=now.isoformat(), kind=KIND,
                    hypothesis="l'avis à 72 h d'une IA locale qui lit le graphique et la carte d'analyse bat-il la règle "
                               "EMA 50 / 200 ?",
                    strategy=STRATEGY, strategy_version=1, variant=f"{model} ({digest}), consigne {prompt_hash}",
                    params={"model": model, "digest": digest, "n": n, "seed": SEED, "from": str(FROM), "to": str(TO),
                            "level": LEVEL, "samples": SAMPLES},
                    period_label="FINAL_TEST", period_start=str(FROM), period_end=str(TO),
                    universe=sorted(frames), data_hashes={"moments": hashlib.sha256(
                        json.dumps([(s, t.isoformat()) for s, t in moments]).encode()).hexdigest()[:16]},
                    git_commit=state, dependencies=dependency_versions(), seed=SEED, cost_scenario="aucun (prévision)",
                    simulation_rules={"horizon": "72 h", "rule": "EMA 50 > EMA 200 sur les clôtures 4 h"},
                    metrics={"n_trials": 1, "verdict": result["decision"]["verdict"], "consultation": consultation},
                    status="COMPLETED", report_dir=str(report_dir))
    return payload


def cache_dir(settings: Settings) -> Path:
    return settings.root / "ia_cache"
