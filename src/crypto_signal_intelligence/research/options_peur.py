"""Étude historique PRÉ-INSCRITE « peur sur les options » (docs/OPTIONS_PEUR.md), liée au test en direct F29 :
DVOL journalier de BTC (Deribit) au-dessus du quantile 0,95 de ses 30 jours précédents → achat de BTCUSDT.

Ordre imposé : (1) contrôle sous H0 synthétique (`controle_h0`, lancé une fois, fichier de critères inscrit dans
`CONTROLE_H0` avec son empreinte) ; (2) relecture du code inscrite (`CODE_REVIEW`) ; (3) exécution réelle UNIQUE (`run`),
qui télécharge le DVOL jusqu'au 2025-06-30 seulement et lit les bougies 1 h de BTCUSDT de DEVELOPMENT seulement. Sans
(1) et (2), `run` refuse sans rien lire. Aucune donnée de la période réservée n'est demandée ni lue : la dernière bougie
DVOL retenue est celle du 2025-06-29 (close au 2025-06-30 00:00), et un événement n'est gardé que si sa sortie la plus
lointaine (7 jours) se clôt au plus tard le 2025-06-30 23:59:59.

Source : `GET https://www.deribit.com/api/v2/public/get_volatility_index_data` (`currency=BTC`, `resolution=1D`), par le
client à liste fermée du collecteur (`collect/net.py`) : `data/http.py` n'est pas élargi. Format vérifié le 2026-10-10
sur 3 jours d'avril 2021 (valeurs DVOL seulement, aucun prix) : bougie journalière horodatée au DÉBUT du jour UTC, close
= valeur à 00:00 du jour suivant (égale à la close de la dernière bougie horaire du jour) ; premier jour : 2021-03-24.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci
from ..config import Settings
from ..forward.costs import ADVERSE, CENTRAL, SCENARIOS, costs_for
from ..forward.journal import utc_iso
from .protocol import FROZEN_DEVELOPMENT_END

STUDY = "OPTIONS_PEUR"
URL = "https://www.deribit.com/api/v2/public/get_volatility_index_data"
FIRST_DAY = pd.Timestamp("2021-03-01", tz="UTC")
DVOL_START = pd.Timestamp("2021-03-24", tz="UTC")                # premier jour du DVOL chez Deribit
DEV_END = pd.Timestamp(FROZEN_DEVELOPMENT_END)                  # 2025-06-30 23:59:59 UTC
LAST_DAY = pd.Timestamp("2025-06-29", tz="UTC")                  # dernière bougie DVOL : close au 2025-06-30 00:00
QUANTILE, REFERENCE_DAYS, MIN_REFERENCE = 0.95, 30, 24           # quantile 0,95 des 30 jours précédents (≥ 24 présents)
MIN_GAP = pd.Timedelta(days=5)                                   # un événement par 5 jours au plus
ENTRY_DELAY = pd.Timedelta(hours=1)                              # entrée : clôture 1 h de 01:00 UTC le lendemain
HORIZONS = {"24h": pd.Timedelta(hours=24), "72h": pd.Timedelta(hours=72), "7j": pd.Timedelta(days=7)}
PRIMARY = "24h"
GROSS = "brut"
LEVEL = 0.99
MIN_EVENTS = 30
SAMPLES, SEED, BLOCK_DAYS = 10_000, 20261013, 1
PISTE, RIEN, INSUFFISANT = "PISTE", "RIEN", "INSUFFISANT"
# Contrôle sous H0 (synthétique) : critère et contrôle positif déclarés avant le passage.
H0_SEED, H0_REPLICATES, H0_MAX_FALSE, H0_INJECT, H0_MIN_POWER = 20261014, 200, 0.02, 0.005, 0.50

CODE_REVIEW: str | None = None          # commit relu (relecture leak-auditor), inscrit avant l'exécution réelle
CONTROLE_H0: str | None = None          # "<chemin du criteres.json>#<sha256>", inscrit après le passage unique du contrôle


class NotReady(RuntimeError):
    """Contrôle sous H0 ou relecture non inscrits, ou contrôle en échec : exécution refusée, rien n'est lu."""


class AlreadyRun(RuntimeError):
    """L'exécution unique a déjà eu lieu."""


# --- Données ------------------------------------------------------------------------------------------------------------------

def download_dvol(http, *, first: pd.Timestamp = FIRST_DAY, last: pd.Timestamp = LAST_DAY) -> pd.Series:
    """DVOL journalier de BTC, bougies dont le DÉBUT est dans [first, last] (last ≤ 2025-06-29 imposé), pages suivies par
    `continuation` (20 au plus). Index = début du jour UTC, valeur = close."""
    if pd.Timestamp(last) > LAST_DAY:
        raise ValueError(f"téléchargement au-delà du {LAST_DAY.date()} refusé : période réservée")
    end = int((pd.Timestamp(last) + pd.Timedelta(hours=12)).timestamp() * 1000)
    start = int(pd.Timestamp(first).timestamp() * 1000)
    rows: dict[int, float] = {}
    for _ in range(20):
        payload = http.get_json(URL, {"currency": "BTC", "start_timestamp": start, "end_timestamp": end, "resolution": "1D"})
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise ValueError("réponse Deribit sans « result »")
        for row in result.get("data") or []:
            rows[int(row[0])] = float(row[4])
        nxt = result.get("continuation")
        if not nxt or int(nxt) <= start or int(nxt) >= end:
            break
        end = int(nxt)
    series = pd.Series(rows, dtype=float).sort_index()
    series.index = pd.to_datetime(series.index, unit="ms", utc=True)
    return series[(series.index >= first) & (series.index <= last)]


def load_btc_hours(settings: Settings) -> pd.Series:
    """Clôtures 1 h de BTCUSDT du magasin long (`<racine>/long_history`, archives officielles ; index = instant de clôture),
    coupées à la fin de DEVELOPMENT avant tout calcul."""
    from ..data.store import CandleStore
    from .long_history import long_settings
    frame = CandleStore(long_settings(settings).data_dir).load("BTCUSDT", "1h")
    frame = frame[pd.to_datetime(frame["open_time"], utc=True) + pd.Timedelta(hours=1) <= DEV_END + pd.Timedelta(seconds=1)]
    closes = pd.Series(frame["close"].to_numpy(float), index=pd.to_datetime(frame["open_time"], utc=True) + pd.Timedelta(hours=1))
    return closes[~closes.index.duplicated()].sort_index()


# --- Règle (fonctions pures) ---------------------------------------------------------------------------------------------------

def events(dvol: pd.Series) -> list[dict]:
    """Jours D dont la close du DVOL dépasse le quantile 0,95 (linéaire) des 30 jours STRICTEMENT précédents (au moins 24
    présents), un événement par 5 jours au plus ; connu à la close (D + 1 jour), entrée 1 h plus tard."""
    daily = dvol.asfreq("D")
    thr = daily.shift(1).rolling(REFERENCE_DAYS, min_periods=MIN_REFERENCE).quantile(QUANTILE, interpolation="linear")
    out: list[dict] = []
    last: pd.Timestamp | None = None
    for day, value in daily.items():
        threshold = thr.get(day)
        if pd.isna(value) or threshold is None or pd.isna(threshold) or not value > threshold:
            continue
        if last is not None and day - last < MIN_GAP:
            continue
        last = day
        known = day + pd.Timedelta(days=1)
        out.append({"day": day, "value": float(value), "threshold": float(threshold), "known_at": known,
                    "entry_at": known + ENTRY_DELAY})
    return out


def net_return(entry: float, exit_: float, scenario: str) -> float:
    costs = costs_for("BTCUSDT", scenario)
    return exit_ * (1 - costs.market) * (1 - costs.fee) / (entry * (1 + costs.market) * (1 + costs.fee)) - 1


def measure(closes: pd.Series, event: dict, *, shift: float = 0.0) -> dict | None:
    """Rendements brut et nets d'un événement par horizon ; None si une clôture manque ou si la sortie la plus lointaine
    dépasse la fin de DEVELOPMENT. `shift` : contrôle positif (prix de sortie à 24 h × (1 + shift))."""
    entry_at = pd.Timestamp(event["entry_at"])
    if entry_at + max(HORIZONS.values()) > DEV_END:
        return None
    entry = closes.get(entry_at)
    if entry is None or not entry > 0:
        return None
    out = {}
    for name, horizon in HORIZONS.items():
        exit_ = closes.get(entry_at + horizon)
        if exit_ is None or not exit_ > 0:
            return None
        if name == PRIMARY and shift:
            exit_ = exit_ * (1 + shift)
        out[name] = {GROSS: exit_ / entry - 1, **{s: net_return(entry, exit_, s) for s in SCENARIOS}}
    return out


def _ci(values: np.ndarray, times, *, samples: int) -> tuple[float, float] | None:
    ci, _ = day_block_ci(values, times, block_days=BLOCK_DAYS, samples=samples, seed=SEED, level=LEVEL, min_blocks=MIN_EVENTS)
    return ci


def decide(rows: list[dict], *, samples: int = SAMPLES) -> dict:
    """`PISTE` si l'IC 99 % du rendement NET moyen à 24 h est > 0 en central ET en défavorable, et le reste sans l'année
    civile dont la somme des rendements nets centraux est la plus forte ; `INSUFFISANT` sous 30 événements ; sinon `RIEN`."""
    if len(rows) < MIN_EVENTS:
        return {"decision": INSUFFISANT, "n": len(rows)}
    times = pd.to_datetime([r["entry_at"] for r in rows], utc=True).to_numpy()
    out: dict = {"n": len(rows)}
    for m in (GROSS, CENTRAL, ADVERSE):
        values = np.array([r["returns"][PRIMARY][m] for r in rows], float)
        out[m] = {"mean": round(float(values.mean()), 6), "ci99": _ci(values, times, samples=samples),
                  "positive_share": round(float((values > 0).mean()), 4)}
    holds = all(out[s]["ci99"] is not None and out[s]["ci99"][0] > 0 for s in (CENTRAL, ADVERSE))
    years: dict[int, float] = {}
    for r in rows:
        year = pd.Timestamp(r["entry_at"]).year
        years[year] = years.get(year, 0.0) + r["returns"][PRIMARY][CENTRAL]
    best = max(years, key=lambda y: years[y]) if years else None
    rest = [r for r in rows if pd.Timestamp(r["entry_at"]).year != best]
    without = None
    if len(rest) >= MIN_EVENTS:
        t2 = pd.to_datetime([r["entry_at"] for r in rest], utc=True).to_numpy()
        without = {s: _ci(np.array([r["returns"][PRIMARY][s] for r in rest], float), t2, samples=samples) for s in (CENTRAL, ADVERSE)}
    guard = without is not None and all((ci := without[s]) is not None and ci[0] > 0 for s in (CENTRAL, ADVERSE))
    out |= {"best_year": best, "without_best_year": without, "guard": guard,
            "by_year": {y: round(v, 6) for y, v in sorted(years.items())},
            "decision": PISTE if holds and guard else RIEN}
    return out


def study(dvol: pd.Series, closes: pd.Series, *, shift: float = 0.0, samples: int = SAMPLES) -> dict:
    found = events(dvol)
    rows = []
    for e in found:
        returns = measure(closes, e, shift=shift)
        if returns is not None:
            rows.append({"day": utc_iso(e["day"]), "entry_at": utc_iso(e["entry_at"]), "value": e["value"],
                         "threshold": e["threshold"], "returns": returns})
    return {"events": len(found), "measured": len(rows), "rows": rows, "decision": decide(rows, samples=samples)}


# --- Contrôle sous H0 (synthétique) -----------------------------------------------------------------------------------------

def h0_market(rng: np.random.Generator, *, first: pd.Timestamp = DVOL_START,
              last: pd.Timestamp = LAST_DAY) -> tuple[pd.Series, pd.Series]:
    """BTC synthétique (clôtures 1 h, martingale : prix = Π(1 + r), r = σ_t z, z Student à 4 degrés de variance 1, σ_t
    GARCH(1,1) α = 0,05 β = 0,94, σ moyen horaire 0,6 %) et DVOL synthétique LIÉ AUX RENDEMENTS PASSÉS : close du jour =
    10 + 0,85 × la veille + 0,15 × (volatilité réalisée annualisée des 7 jours écoulés, en %) + 40 × (baisse des 7 jours
    écoulés, si baisse) + bruit gaussien (σ 1,5) ; aucune information sur le futur."""
    hours = pd.date_range(first + pd.Timedelta(hours=1), last + pd.Timedelta(days=12), freq="h")
    n = len(hours)
    z = rng.standard_t(4, n) / math.sqrt(2.0)
    mean = 0.006
    omega = mean ** 2 * (1 - 0.05 - 0.94)
    var, r = mean ** 2, np.empty(n)
    for t in range(n):
        r[t] = max(math.sqrt(var) * z[t], -0.5)
        var = omega + 0.05 * r[t] ** 2 + 0.94 * var
    closes = pd.Series(100.0 * np.cumprod(1 + r), index=hours)
    logs = pd.Series(np.log1p(r), index=hours)
    week = 24 * 7
    realized_all = logs.rolling(week, min_periods=25).std() * math.sqrt(24 * 365) * 100
    drop_all = (-logs.rolling(week, min_periods=1).sum()).clip(lower=0.0)
    days = pd.date_range(first, last, freq="D")
    closes_at = days + pd.Timedelta(days=1)                     # close du DVOL du jour D : 00:00 du jour suivant
    realized = realized_all.reindex(closes_at).fillna(60.0).to_numpy()
    drop = drop_all.reindex(closes_at).fillna(0.0).to_numpy()
    noise = rng.standard_normal(len(days)) * 1.5
    values, previous = [], 60.0
    for k in range(len(days)):
        previous = 10 + 0.85 * previous + 0.15 * realized[k] + 40 * drop[k] + noise[k]
        values.append(previous)
    return pd.Series(values, index=days), closes


def controle_h0(*, out_path: Path | None = None, replicates: int = H0_REPLICATES, samples: int = SAMPLES,
                progress: Callable[[str], None] | None = None) -> dict:
    """200 marchés synthétiques indépendants : faux `PISTE` (critère ≤ 0,02) et puissance avec +0,5 % ajouté au rendement
    brut à 24 h de chaque événement (critère ≥ 0,50, sinon `INSTRUMENT_TROP_FAIBLE` : étude non exécutée, 0 essai)."""
    seeds = np.random.SeedSequence(H0_SEED).spawn(replicates)
    reps = []
    for k, seed in enumerate(seeds):
        dvol, closes = h0_market(np.random.default_rng(seed))
        null, positive = study(dvol, closes, samples=samples), study(dvol, closes, shift=H0_INJECT, samples=samples)
        reps.append({"events": null["events"], "measured": null["measured"], "null": null["decision"]["decision"],
                     "positive": positive["decision"]["decision"],
                     "gross_ci": (null["decision"].get(GROSS) or {}).get("ci99")})
        if progress and (k + 1) % 20 == 0:
            progress(f"{k + 1}/{replicates}")
    false = sum(r["null"] == PISTE for r in reps) / replicates
    power = sum(r["positive"] == PISTE for r in reps) / replicates
    passes = false <= H0_MAX_FALSE
    status = "PASSE" if passes and power >= H0_MIN_POWER else ("ECHEC" if not passes else "INSTRUMENT_TROP_FAIBLE")
    report = {"study": STUDY, "seed": H0_SEED, "replicates": replicates, "samples": samples, "false_piste": round(false, 4),
              "max_false": H0_MAX_FALSE, "power": round(power, 4), "min_power": H0_MIN_POWER, "inject": H0_INJECT,
              "status": status, "executable": status == "PASSE",
              "null_decisions": {d: sum(r["null"] == d for r in reps) for d in (PISTE, RIEN, INSUFFISANT)},
              "gross_ci_positive_rate": round(sum(1 for r in reps if r["gross_ci"] and r["gross_ci"][0] > 0) / replicates, 4),
              "events_mean": round(float(np.mean([r["events"] for r in reps])), 1),
              "measured_mean": round(float(np.mean([r["measured"] for r in reps])), 1)}
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def control_of(inscription: str | None) -> dict:
    if not inscription or "#" not in inscription:
        raise NotReady("contrôle sous H0 non inscrit (CONTROLE_H0) : exécution refusée")
    path_text, digest = inscription.rsplit("#", 1)
    path = Path(path_text.strip())
    if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != digest.strip():
        raise NotReady("fichier du contrôle sous H0 absent ou modifié depuis son inscription : exécution refusée")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("study") != STUDY:
        raise NotReady("fichier du contrôle sous H0 d'une autre étude")
    if not report.get("executable"):
        raise NotReady(f"contrôle sous H0 : {report.get('status')} : étude non exécutée, 0 essai (docs/OPTIONS_PEUR.md)")
    return report


# --- Exécution réelle (unique, gardée) --------------------------------------------------------------------------------------

def run(settings: Settings, *, now: datetime, http=None) -> dict:
    """Exécution UNIQUE : contrôle H0 inscrit et passé, relecture inscrite, code commité ; télécharge le DVOL (≤ 2025-06-29),
    lit BTCUSDT 1 h de DEVELOPMENT, décide, enregistre 1 essai DEVELOPMENT, écrit `reports/<run_id>/options_peur.json`."""
    from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
    control_of(CONTROLE_H0)
    if not CODE_REVIEW:
        raise NotReady("relecture du code non inscrite (CODE_REVIEW) : exécution refusée")
    state = code_state()
    if state.endswith("+DIRTY") or state == "NO_GIT_COMMIT":
        raise NotReady(f"code non commité ({state}) : exécution refusée")
    registry = ExperimentRegistry(settings.experiments_db)
    if registry.count_runs(STUDY) > 0:
        raise AlreadyRun("étude « options peur » déjà exécutée : exécution unique")
    if http is None:
        from ..collect.net import CollectHttp
        http = CollectHttp()
    dvol = download_dvol(http)
    closes = load_btc_hours(settings)
    result = study(dvol, closes)
    run_id = new_run_id("OPT")
    digest = hashlib.sha256(json.dumps({str(k): v for k, v in dvol.items()}, sort_keys=True).encode()).hexdigest()
    summary = {"run_id": run_id, "study": STUDY, "decision": result["decision"], "events": result["events"],
               "measured": result["measured"], "dvol_days": int(len(dvol)), "dvol_sha256": digest,
               "first_day": utc_iso(dvol.index.min()) if len(dvol) else None, "last_day": utc_iso(dvol.index.max()) if len(dvol) else None}
    out = settings.reports_dir / run_id
    out.mkdir(parents=True, exist_ok=True)
    (out / "options_peur.json").write_text(json.dumps(summary | {"rows": result["rows"]}, ensure_ascii=False, indent=1,
                                                      default=str), encoding="utf-8")
    registry.record(run_id=run_id, created_at=utc_iso(pd.Timestamp(now)), kind="HISTORICAL_STUDY",
                    hypothesis="DVOL BTC > q0,95 de 30 jours → achat BTCUSDT, rendement net à 24 h > 0",
                    strategy=STUDY, strategy_version=1, variant="dvol", params=params(), period_label="DEVELOPMENT",
                    period_start=utc_iso(FIRST_DAY), period_end=utc_iso(DEV_END), universe=["BTCUSDT"],
                    data_hashes={"dvol": digest}, git_commit=state, dependencies=dependency_versions(), seed=SEED,
                    cost_scenario="central+defavorable", simulation_rules={"doc": "docs/OPTIONS_PEUR.md"},
                    metrics={"n_trials": 1, "decision": result["decision"]["decision"], "n": result["measured"]},
                    status="COMPLETED", report_dir=str(out))
    return summary


def params() -> dict:
    return {"quantile": QUANTILE, "reference_days": REFERENCE_DAYS, "min_reference": MIN_REFERENCE, "min_gap_days": MIN_GAP.days,
            "entry_delay_hours": int(ENTRY_DELAY / pd.Timedelta(hours=1)), "horizons": list(HORIZONS), "primary": PRIMARY,
            "level": LEVEL, "min_events": MIN_EVENTS, "samples": SAMPLES, "seed": SEED, "block_days": BLOCK_DAYS,
            "first_day": str(FIRST_DAY.date()), "last_day": str(LAST_DAY.date())}
