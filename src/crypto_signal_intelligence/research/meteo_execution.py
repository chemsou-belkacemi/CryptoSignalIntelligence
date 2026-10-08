"""Exécutions réelles de la météo du marché (docs/METEO_MARCHE.md, § 7.2 m5, § 7.3, § 8, § 12, § 15).

Chaque exécution est UNIQUE par type (`METEO_PRINCIPALE`, `METEO_VARIANTE_2018`, `METEO_CONFIRMATION`), exige un code
commité, identique sur les chemins relus au commit inscrit par la relecture `leak-auditor` (`meteo_review.CODE_REVIEW`),
une configuration effective identique (`CONFIG_FINGERPRINT`) et les contrôles synthétiques inscrits et réussis
(`CONTROLES_*`). La commande refuse aussi de tourner sans `--executer`.

Passage unique scellé (m5) pour la principale et la variante, sans regard intermédiaire :
1. calcul des couleurs ; la suite, les états des composantes et les `m_d` sont écrits dans un fichier scellé
   (empreinte au registre), jamais affichés ;
2. puissances sur la VRAIE suite des couleurs avec des rendements synthétiques `N2` (équivalence) et `N2` + injection
   de Δ_min (supériorité) ;
3. si la plus faible des puissances de supériorité (feu synthétique, feu réel) est < 0,50 : arrêt AVANT de calculer
   `y`, `INSTRUMENT_TROP_FAIBLE`, 0 essai ;
4. sinon, la question décisive dans le même passage.
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from ..backtest.metrics import day_block_ci
from ..config import Settings
from ..features.loader import MissingData
from ..forward.costs import ADVERSE, CENTRAL, costs_for
from . import meteo as mt
from . import meteo_controls as mc
from . import meteo_study as ms
from .derivatives_screen import fingerprint
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode
from .protocol import development_end
from .universe import MARKET, RESEARCH_UNIVERSE

STEPS = ("principale", "variante", "confirmation")
KINDS = {"principale": "METEO_PRINCIPALE", "variante": "METEO_VARIANTE_2018", "confirmation": "METEO_CONFIRMATION"}
PREFIX = {"principale": "METEO1", "variante": "METEO2", "confirmation": "METEOC"}
FNG_SERIES, FNG_KEY, FNG_FIELD = "fear_greed", "MARCHE", "value"
FNG_URL = "https://api.alternative.me/fng/"
FNG_FIRST = pd.Timestamp("2018-02-01", tz="UTC")
S1_FIRST = pd.Timestamp("2019-01-01", tz="UTC")      # achats S1 depuis 2019 (magasin minute des 40 paires)
SEAL_FILE = "scelle.parquet"
NO_HOURLY = ("AION", "ANT", "GAL", "JST", "LEVER", "SC", "SKL", "SUN")      # membres sans bougies 1 h (§ 5.2, m7)
MIGRATIONS = {"MATICUSDT": "POLUSDT", "FTMUSDT": "SUSDT", "RNDRUSDT": "RENDERUSDT", "ERDUSDT": "EGLDUSDT",
              "NPXSUSDT": "PUNDIXUSDT"}
PG_MARGIN = ms.BLOCK_DAYS                           # P_G : rotations de 91 à N − 91 jours, par pas de 7
CONFIG_SECTIONS = ("data", "protocol", "derivatives")

from .meteo_review import (  # noqa: E402
    CODE_REVIEW,
    CONFIG_FINGERPRINT,
    CONTROLES_PRINCIPALE,
    CONTROLES_VARIANTE,
)

_SRC = "src/crypto_signal_intelligence"
REVIEW_FILE = f"{_SRC}/research/meteo_review.py"
REVIEWED_PATHS: tuple[str, ...] = (
    *(f"{_SRC}/research/{m}.py" for m in ("meteo", "meteo_study", "meteo_controls", "meteo_execution",
                                          "volatility_hourly", "volatility", "pit_universe", "figures_history",
                                          "factors", "trendline_confirmation", "long_history", "minute_history",
                                          "protocol", "experiments", "universe", "derivatives_screen")),
    f"{_SRC}/forward/f15.py", f"{_SRC}/forward/costs.py", f"{_SRC}/forward/sources.py", f"{_SRC}/context/store.py",
    f"{_SRC}/derivatives/history.py", f"{_SRC}/features/indicators.py", f"{_SRC}/features/loader.py",
    f"{_SRC}/backtest/metrics.py", f"{_SRC}/data/store.py", f"{_SRC}/data/schema.py", f"{_SRC}/data/timeunits.py",
    f"{_SRC}/config.py", f"{_SRC}/cli.py", "config/default.toml",
)
INSCRIPTIONS = ("CODE_REVIEW", "CONFIG_FINGERPRINT", "CONTROLES_PRINCIPALE", "CONTROLES_VARIANTE")


class NotReady(RuntimeError):
    """Relecture, configuration ou contrôles non inscrits (ou différents) : exécution refusée, rien n'est compté."""


class AlreadyRun(RuntimeError):
    """Exécution unique déjà faite, ou étape précédente absente : rien n'est compté."""


# --- Garde : code commité et relu, configuration, contrôles -------------------------------------------------------------

def config_fingerprint(settings: Settings) -> str:
    """Empreinte des valeurs EFFECTIVES (fichier et variables `CSI_*`) des sections lues par le calcul."""
    values = settings.model_dump(mode="json", include=set(CONFIG_SECTIONS))
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def review_file_is_inert(path: Path) -> bool:
    """Le fichier d'inscription ne contient que sa docstring, `from __future__ import annotations` et les
    affectations permises à une chaîne ou None."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return False
    for index, node in enumerate(tree.body):
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__" \
                and [a.name for a in node.names] == ["annotations"]:
            continue
        targets = ([node.target] if isinstance(node, ast.AnnAssign)
                   else node.targets if isinstance(node, ast.Assign) else [])
        value = getattr(node, "value", None)
        if len(targets) == 1 and isinstance(targets[0], ast.Name) and targets[0].id in INSCRIPTIONS \
                and isinstance(value, ast.Constant) and (value.value is None or isinstance(value.value, str)):
            continue
        return False
    return True


def require_clean_and_reviewed(state: str, *, root: Path | None = None, review: str | None = None,
                               settings: Settings | None = None) -> None:
    """Code commité (jamais « +DIRTY ») et identique, sur les chemins relus, au commit `CODE_REVIEW` ; configuration
    effective identique à celle de la relecture. Sinon : exécution refusée."""
    if state.endswith("+DIRTY") or state == "NO_GIT_COMMIT":
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    commit = CODE_REVIEW if review is None else review
    if not commit:
        raise NotReady("relecture leak-auditor du code non inscrite (CODE_REVIEW) : exécution refusée (§ 7.3)")
    root = root or Path(__file__).resolve().parents[3]
    try:
        out = subprocess.run(["git", "diff", "--quiet", commit, "HEAD", "--", *REVIEWED_PATHS], cwd=root,
                             capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise NotReady(f"comparaison au commit relu impossible : {exc}") from None
    if out.returncode == 1:
        raise NotReady(f"code modifié depuis le commit relu {commit[:12]} : nouvelle relecture exigée")
    if out.returncode != 0:
        raise NotReady(f"commit relu {commit[:12]} introuvable ou illisible : {out.stderr.strip()[:200]}")
    inscription = root / REVIEW_FILE
    if inscription.exists() and not review_file_is_inert(inscription):
        raise NotReady("fichier d'inscription de la relecture modifié au-delà des valeurs permises : exécution refusée")
    if settings is not None:
        if not CONFIG_FINGERPRINT:
            raise NotReady("empreinte de la configuration relue non inscrite (CONFIG_FINGERPRINT) : exécution refusée")
        if config_fingerprint(settings) != CONFIG_FINGERPRINT:
            raise NotReady("configuration effective différente de celle de la relecture (fichier ou variables CSI_*) : "
                           "exécution refusée")


def controls_of(inscription: str | None, *, question: str) -> dict:
    """Critères des contrôles synthétiques inscrits pour une question : fichier présent, empreinte identique, tous les
    critères nuls réussis (sinon la question n'est ni exécutée ni comptée), puissances et règle d'inutilité."""
    if not inscription or "#" not in inscription:
        raise NotReady(f"contrôles synthétiques de la question {question} non inscrits : exécution refusée (§ 7.2)")
    path_text, digest = inscription.rsplit("#", 1)
    path = Path(path_text.strip())
    if not path.exists():
        raise NotReady(f"fichier des contrôles introuvable : {path}")
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest.strip():
        raise NotReady("fichier des contrôles modifié depuis son inscription : exécution refusée")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("question") != question:
        raise NotReady(f"contrôles d'une autre question ({report.get('question')}) inscrits pour {question}")
    if report.get("sims", 0) < mc.SIMS or report.get("samples", 0) < ms.SAMPLES:
        raise NotReady("contrôles inscrits avec moins de 500 simulations ou de 10 000 tirages")
    if not report.get("all_null_criteria_passed") or not report.get("rule"):
        raise NotReady(f"un critère du § 7.2 a échoué pour la question {question} : ni exécutée ni comptée")
    return report


def require_ready(settings: Settings, *, step: str | None = None) -> str:
    """Toutes les gardes, avant le moindre calcul (utilisée par la commande avant `run_step`)."""
    state = code_state()
    require_clean_and_reviewed(state, settings=settings)
    if step in (None, "principale", "confirmation"):
        controls_of(CONTROLES_PRINCIPALE, question="principale")
    if step in (None, "variante"):
        controls_of(CONTROLES_VARIANTE, question="variante")
    return state


def _done(registry: ExperimentRegistry, kind: str) -> dict | None:
    with registry.connect() as db:
        row = db.execute("SELECT run_id FROM runs WHERE kind=? ORDER BY created_at DESC LIMIT 1", (kind,)).fetchone()
    return registry.get(row[0]) if row else None


# --- Sceau ---------------------------------------------------------------------------------------------------------------

def write_seal(directory: Path, states: pd.DataFrame) -> str:
    """Suite des couleurs, états des composantes et `m_d` écrits dans un fichier scellé ; renvoie son empreinte. Le
    contenu n'est ni affiché ni résumé avant la fin du passage."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / SEAL_FILE
    frame = states.reset_index(names="day")
    frame.to_parquet(path, index=False)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_seal(path: Path, digest: str) -> pd.DataFrame:
    """Relit un fichier scellé et vérifie son empreinte (inscrite au registre) ; refuse un fichier modifié."""
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
        raise NotReady(f"fichier scellé modifié depuis son inscription : {path}")
    return pd.read_parquet(path).set_index("day")


# --- Données réelles, coupées à la fin de DEVELOPMENT --------------------------------------------------------------------

def cut(frame: pd.DataFrame, column: str, end: pd.Timestamp) -> pd.DataFrame:
    """Lignes dont `column` est ≤ `end` (fin de DEVELOPMENT) : rien d'ultérieur n'est lu."""
    if frame.empty:
        return frame
    return frame[np.asarray(mt.utc(frame[column]) <= end)].reset_index(drop=True)


def load_fng(settings: Settings, end: pd.Timestamp) -> pd.Series:
    from ..context import store
    frame = store.load(settings, FNG_SERIES)
    frame = frame[(frame["kind"] == store.HISTORY) & (frame["key"] == FNG_KEY) & (frame["field"] == FNG_FIELD)]
    frame = cut(frame, "date", end)
    if frame.empty:
        raise FileNotFoundError("historique du Fear & Greed absent (csi meteo fng-historique, accord du propriétaire)")
    return pd.Series(frame["value"].to_numpy(float), index=mt.utc(frame["date"]).floor("D")).sort_index()


def download_fng(settings: Settings, *, now: datetime) -> dict:
    """Un seul appel à alternative.me `/fng/` avec `limit=0` (client à liste blanche de `forward/sources.py`)."""
    from ..context import store
    from ..forward.sources import PublicSources
    client = PublicSources()
    try:
        payload = client.get_json(FNG_URL, {"limit": 0})
    finally:
        client.close()
    rows = [{"key": FNG_KEY, "date": pd.Timestamp(int(r["timestamp"]), unit="s", tz="UTC").floor("D"),
             "field": FNG_FIELD, "value": float(r["value"])} for r in payload.get("data") or []]
    frame = store.rows(rows, kind=store.HISTORY, source="alternative.me /fng/ limit=0", now=now)
    written = store.upsert(settings, FNG_SERIES, frame)
    return {"lignes": len(frame), "ecrites": written, "empreinte": fingerprint(frame[["date", "value"]]),
            "premier": str(frame["date"].min()) if len(frame) else None}


def load_funding(settings: Settings, end: pd.Timestamp) -> pd.DataFrame:
    from ..derivatives.history import DerivativesStore
    frame = DerivativesStore(settings.data_dir).load("funding", MARKET)
    if frame.empty:
        raise FileNotFoundError("financement de BTCUSDT absent du magasin des dérivés")
    return cut(frame[["time", "rate"]], "time", end)


def load_pit(settings: Settings, end: pd.Timestamp) -> tuple[pd.DataFrame, pd.DataFrame]:
    from .pit_universe import load_membership, pit_dir
    daily = pd.read_parquet(pit_dir(settings) / "daily.parquet")
    members = load_membership(settings)
    return cut(daily, "day", end - mt.DAY), cut(members, "month", end)


def load_h1(settings: Settings, symbol: str, end: pd.Timestamp) -> pd.DataFrame | None:
    from .long_history import load_long
    try:
        frame = load_long(settings, symbol)
    except MissingData:
        return None
    columns = ["open_time", "open", "high", "low", "close", "quote_volume", "available_at"]
    return cut(frame[columns], "open_time", end)


@dataclass
class RealData:
    btc: pd.DataFrame
    pit_daily: pd.DataFrame
    members: pd.DataFrame
    fng: pd.Series
    funding: pd.DataFrame
    pairs: dict[str, pd.DataFrame]
    missing_hourly: list[str]
    hashes: dict


def load_real(settings: Settings, period: ms.Period, *, say: Callable[[str], None]) -> RealData:
    end = pd.Timestamp(development_end(settings)).tz_convert("UTC")
    say("lecture : top 40 à date, BTC, Fear & Greed, financement")
    pit_daily, members = load_pit(settings, end)
    btc = load_h1(settings, MARKET, end)
    if btc is None:
        raise FileNotFoundError("BTCUSDT absent du magasin long")
    months = {(d - mt.DAY).tz_convert(None).to_period("M") for d in period.days}
    chosen = members[members["month"].dt.tz_convert(None).dt.to_period("M").isin(months)]
    pairs, missing = {}, []
    for symbol in sorted(set(chosen["symbol"]) | set(RESEARCH_UNIVERSE)):
        frame = btc if symbol == MARKET else load_h1(settings, symbol, end)
        if frame is None or frame.empty:
            missing.append(symbol)
            continue
        pairs[symbol] = frame
    fng = load_fng(settings, end)
    funding = load_funding(settings, end)
    hashes = {"btc": fingerprint(btc), "pit_daily": fingerprint(pit_daily), "membres": fingerprint(members),
              "fng": fingerprint(fng.rename("v").reset_index()), "financement": fingerprint(funding)}
    hashes |= {f"h1:{s}": fingerprint(f) for s, f in pairs.items()}
    return RealData(btc, pit_daily, members, fng, funding, pairs, missing, hashes)


# --- Achats S1 réels (transaction de référence sur les minutes) --------------------------------------------------------

def s1_eligibility(pairs: dict[str, pd.DataFrame], days: pd.DatetimeIndex) -> pd.DataFrame:
    """Règle de FACTORS (85 journées valides sur 90, médiane du volume ≥ 1 M$) sur les 40 paires de recherche, avec
    les seules journées closes avant le jour `d` (`factors.Book.eligible`, importé sans changement)."""
    from . import factors as fa
    frames = {s: pairs[s] for s in RESEARCH_UNIVERSE if s in pairs}
    panel = fa.build_panel(frames)
    eligible = fa.Book(panel, panel.close.index).eligible
    eligible.index = mt.utc(eligible.index)
    return eligible.reindex(days).fillna(False).astype(bool)


def s1_real(settings: Settings, data: RealData, days: pd.DatetimeIndex, *, say: Callable[[str], None],
            net: bool = True) -> pd.DataFrame:
    """20 achats par jour sur les 40 paires de recherche (minutes), R brut (et net, descriptif)."""
    from .minute_history import load_minutes
    end = pd.Timestamp(development_end(settings)).tz_convert("UTC")
    draws = mt.s1_draws(s1_eligibility(data.pairs, days))
    draws["r_gross"] = np.nan
    draws["risk_pct"] = np.nan
    for column in ("r_central", "r_defavorable"):
        draws[column] = np.nan
    for symbol, idx in draws.dropna(subset=["symbol"]).groupby("symbol").groups.items():
        say(f"achats S1 : {symbol}")
        try:
            minutes = load_minutes(settings, str(symbol))
        except MissingData:
            continue
        minutes = cut(minutes[["open_time", "open", "high", "low", "close"]], "open_time", end)
        minutes = minutes[mt.utc(minutes["open_time"]) >= days.min() - mt.DAY]
        at = pd.DatetimeIndex(draws.loc[idx, "at"])
        h1 = data.pairs[str(symbol)]
        r, _ = mt.play_s1(minutes, h1, at)
        draws.loc[idx, "r_gross"] = r
        stops = mt.decision_stops(h1, at)
        m = mt.fh.Minutes.from_frame(minutes.sort_values("open_time").reset_index(drop=True))
        first = np.searchsorted(m.ns, mt.utc(at).asi8)
        ok = first < len(m.ns)
        entry = np.where(ok, m.o[np.minimum(first, len(m.ns) - 1)], np.nan)
        draws.loc[idx, "risk_pct"] = np.where(np.isfinite(r), (entry - stops) / entry, np.nan)
        if net:
            costs = mt.play_s1_costs(minutes, h1, at, str(symbol))
            draws.loc[idx, "r_central"] = costs["r_central"].to_numpy(float)
            draws.loc[idx, "r_defavorable"] = costs["r_defavorable"].to_numpy(float)
    return draws


# --- Feu réel, prévisions, panier ---------------------------------------------------------------------------------------

def forecaster_of(data: RealData, settings: Settings) -> mt.RealForecaster:
    from . import volatility_hourly as vh
    frames = [vh.hourly_frame(data.pairs[s], data.btc).assign(symbol=s) for s in RESEARCH_UNIVERSE if s in data.pairs]
    return mt.RealForecaster(pd.concat(frames, ignore_index=True), seed=settings.protocol.seed)


def real_states(settings: Settings, data: RealData, period: ms.Period, s1: pd.DataFrame,
                forecaster: mt.RealForecaster | None) -> pd.DataFrame:
    from . import volatility_hourly as vh
    days = period.days
    vol = (mt.real_btc_forecasts(forecaster, vh.hourly_frame(data.btc, data.btc), days)
           if forecaster is not None else None)
    inputs = mt.LightInputs(btc_h1=data.btc, pit_daily=data.pit_daily, members=data.members, fng=data.fng,
                            funding=data.funding, s1=s1[["day", "r_gross"]], forecasts=vol,
                            latency=pd.Timedelta(seconds=settings.data.assumed_availability_latency_seconds))
    states = mt.components(inputs, days, rule=period.rule)
    if vol is not None:
        states["instance"] = vol.instance_of.reindex(days).to_numpy()
    return states


def basket_table(data: RealData, period: ms.Period, forecaster: mt.RealForecaster | None, *,
                 symbols: list[str] | None = None) -> tuple[pd.DataFrame, dict]:
    """`S2N` (et le % brut) du panier : membres du mois de d − 1 avec bougies 1 h ; σ̂ = prévision 24 h (principale)
    ou `σ24_168` (variante)."""
    from . import volatility_hourly as vh
    days = period.days
    names = sorted(s for s in (symbols if symbols is not None else data.pairs) if s in data.pairs)
    returns, sigma, firsts, kinds = {}, {}, {}, {}
    for s in names:
        frame = data.pairs[s]
        returns[s] = mt.basket_returns(frame, days)
        firsts[s] = mt.first_bar(frame)
        sigma[s] = (mt.real_sigma(forecaster, vh.hourly_frame(frame, data.btc), days) if forecaster is not None
                    else mt.sigma_168(frame, days))
        kinds[s] = returns[s]["exit_kind"].value_counts().to_dict()
    table = mt.s2n(returns, sigma, data.members, firsts, days, symbols=names)
    young = {s: int(((days - firsts[s]) < pd.Timedelta(days=400)).sum()) for s in names if firsts[s] is not None}
    return table, {"sorties": kinds, "jours_moins_de_400_jours": young,
                   "migrations": {s: MIGRATIONS[s] for s in names if s in MIGRATIONS}}


# --- Descriptif (§ 5.9), calculé avec la mesure ----------------------------------------------------------------------------

def contrast(color: np.ndarray, y: np.ndarray, period: ms.Period, *, samples: int = 2000) -> dict:
    """Δ_exc contre `P_T` et bruit du hasard, sans p-valeur ni verdict."""
    a = ms.analyse(color, y, period, samples=samples, boot_samples=samples, with_guards=False)
    return {"delta_exc": a.delta_exc, "bruit": list(a.noise), "ic90": list(a.ci), "blocs_utiles": a.useful_blocks}


def global_placebo(color: np.ndarray, y: np.ndarray) -> dict:
    """`P_G` (régime + timing, descriptif) : feu décalé de toute la période, de 91 à N − 91 jours par pas de 7."""
    red = color == mt.ROUGE
    hole = ms.holes_of(color, y)

    def diff(r: np.ndarray, h: np.ndarray) -> float:
        valid = ~hole & ~h
        a, b = y[valid & ~r], y[valid & r]
        return float(a.mean() - b.mean()) if len(a) and len(b) else np.nan

    observed = diff(red, hole)
    values = np.array([diff(np.roll(red, u), np.roll(hole, u)) for u in range(PG_MARGIN, len(color) - PG_MARGIN + 1, 7)])
    values = values[np.isfinite(values)]
    if not len(values):
        return {"observe": observed}
    return {"observe": observed, "excess": observed - float(values.mean()),
            "bruit": [float(np.percentile(values - values.mean(), 2.5)), float(np.percentile(values - values.mean(), 97.5))],
            "decalages": int(len(values))}


def drawdown(values: np.ndarray) -> float:
    path = np.cumsum(np.nan_to_num(values))
    return float((np.maximum.accumulate(np.concatenate([[0.0], path]))[1:] - path).max()) if len(path) else 0.0


def describe(states: pd.DataFrame, table: pd.DataFrame, s1: pd.DataFrame, period: ms.Period,
             analysis: ms.Analysis) -> tuple[dict, int]:
    """Mesures descriptives du § 5.9 (aucune décision) et leur nombre `n_descriptive`."""
    days = period.days
    color = states["color"].to_numpy()
    y = table["y"].to_numpy(float)
    pct = table["pct"].to_numpy(float)
    out: dict = {}
    n = 0
    out["P_G_S2N"] = global_placebo(color, y)
    n += 1
    out["S2_pct_brut"] = contrast(color, pct, period)
    n += 1
    cost = 2 * (costs_for("XUSDT", CENTRAL).fee + costs_for("XUSDT", CENTRAL).market)
    net = pct - cost
    keep = (color != mt.ROUGE) & np.isfinite(net)
    ci, _ = day_block_ci(net[keep], np.asarray(days[keep]), block_days=28, samples=2000, seed=mt.SEED, level=0.90)
    out["SANS_ROUGE_net_moyen_pct"] = {"moyenne": float(np.nanmean(net[keep]) * 100) if keep.any() else None,
                                      "ic90": [v * 100 for v in ci] if ci else None,
                                      "remarque": "la météo ne rend rien rentable"}
    s1_daily = s1.dropna(subset=["r_gross"]).groupby("day")
    for column in ("r_gross", "r_central", "r_defavorable"):
        by_day = s1_daily[column].agg(["mean", "size"])
        by_day = by_day[by_day["size"] >= mt.S1_MIN_USABLE]["mean"].reindex(days).to_numpy(float)
        out[f"S1_{column}"] = contrast(color, by_day, period)
        out[f"S1_{column}_P_G"] = global_placebo(color, by_day)
        n += 2
    for component in (c for c in mt.COMPONENTS if states[c].notna().any()):
        flag = states[component].to_numpy(float)
        light = np.where(np.isfinite(flag), np.where(flag == 1, mt.ROUGE, mt.VERT), mt.SANS_FEU)
        out[f"composante_{component}"] = contrast(light, y, period)
        n += 1
    orange = np.where(color == mt.ORANGE, mt.ROUGE, np.where(color == mt.VERT, mt.VERT, mt.SANS_FEU))
    out["jours_orange_contre_verts"] = contrast(orange, y, period)
    n += 1
    exposure = np.select([color == mt.VERT, color == mt.ORANGE, color == mt.ROUGE], [1.0, 0.5, 0.0], np.nan)
    out["FEU_COMPLET"] = {"moyenne_y_exposee": float(np.nanmean(exposure * y)), "moyenne_y": float(np.nanmean(y))}
    years = days.year
    out["par_annee"] = {int(yr): {"couleurs": {mt.COLOR_NAMES[c]: int(((years == yr) & (color == c)).sum())
                                               for c in mt.COLOR_NAMES},
                                  "y_rouge": float(np.nanmean(y[(years == yr) & (color == mt.ROUGE)]))
                                  if ((years == yr) & (color == mt.ROUGE)).any() else None,
                                  "y_non_rouge": float(np.nanmean(y[(years == yr) & (color != mt.ROUGE)
                                                                    & (color != mt.SANS_FEU)]))}
                        for yr in sorted(set(years))}
    out["jour_de_semaine"] = {int(k): float(np.nanmean(y[days.dayofweek == k])) for k in range(7)}
    out["pire_perte_S2_pct"] = {"tous_les_jours": drawdown(pct) * 100,
                                "SANS_ROUGE": drawdown(np.where(color == mt.ROUGE, 0.0, pct)) * 100}
    if "fng" in states:
        fng = states["fng"].to_numpy(float)
        out["fng_entre_23_et_27"] = int(((fng >= 23) & (fng <= 27)).sum())
    if "instance" in states and "VOL_HAUTE" in states:
        inst = states["instance"].to_numpy()
        flips = [i for i in range(1, len(inst)) if inst[i] != inst[i - 1]
                 and states["VOL_HAUTE"].iloc[i] != states["VOL_HAUTE"].iloc[i - 1]]
        out["bascules_vol_haute_reajustement"] = len(flips)
    flags = states[[c for c in mt.COMPONENTS if c in states]].fillna(0).to_numpy(float)
    out["activations_simultanees"] = {int(k): int((flags.sum(axis=1) == k).sum()) for k in range(flags.shape[1] + 1)}
    eps = mt.episodes(color)
    out["episodes"] = [{"debut": str(days[a].date()), "fin": str(days[b].date()), "jours": int(b - a + 1),
                        "y_moyen": float(np.nanmean(y[a:b + 1])) if np.isfinite(y[a:b + 1]).any() else None}
                       for a, b in eps]
    out["sous_periodes_hausse_nommees_apres_coup"] = {
        name: float(np.nanmean(np.where(color[(days >= lo) & (days <= hi)] == mt.ROUGE,
                                        y[(days >= lo) & (days <= hi)], np.nan)))
        for name, lo, hi in (("2020-T4_2021-T1", pd.Timestamp("2020-10-01", tz="UTC"), pd.Timestamp("2021-03-31", tz="UTC")),
                             ("2023-T4_2024-T1", pd.Timestamp("2023-10-01", tz="UTC"), pd.Timestamp("2024-03-31", tz="UTC")))
        if ((days >= lo) & (days <= hi)).any()}
    out["fausses_alertes_attendues"] = round(0.05 * n, 2)
    _ = analysis
    return out, n


# --- Exécutions --------------------------------------------------------------------------------------------------------

def _record(settings: Settings, registry: ExperimentRegistry, *, step: str, now: datetime, state: str, n_trials: int,
            n_descriptive: int, payload: dict, hashes: dict, report_dir: Path, universe: list[str]) -> dict:
    kind = KINDS[step]
    program = registry.program_trials() + n_trials
    payload |= {"kind": kind, "n_trials": n_trials, "n_descriptive": n_descriptive, "program_trials": program,
                "doc": mt.DOC, "code_review": CODE_REVIEW}
    registry.record(run_id=payload["run_id"], created_at=now.isoformat(), kind=kind,
                    hypothesis="dans un même bloc de 13 semaines, les jours rouges du feu sont-ils pires que des jours "
                               "tirés au hasard dans ce bloc (achat générique, sans frais, en unités de volatilité "
                               "prévue) ?",
                    strategy=kind, strategy_version=1, variant="règles figées (docs/METEO_MARCHE.md)",
                    params={"delta_min": ms.DELTA_MIN, "samples": ms.SAMPLES, "seed": mt.SEED,
                            "blocks_days": ms.BLOCK_DAYS, "window_days": ms.WINDOW_DAYS, "code_review": CODE_REVIEW,
                            "controles": {"principale": CONTROLES_PRINCIPALE, "variante": CONTROLES_VARIANTE}},
                    period_label="DEVELOPMENT", period_start=payload.get("period_start", ""),
                    period_end=payload.get("period_end", ""), universe=universe, data_hashes=hashes,
                    git_commit=state, dependencies=dependency_versions(), seed=mt.SEED,
                    cost_scenario="brut (décision) ; central et défavorable en descriptif (forward/costs.py)",
                    simulation_rules={"S2N": "achat 01:00, vente 01:00 le lendemain, rendement / σ̂",
                                      "S1": "transaction de référence (annexe A), copie compilée de F15"},
                    metrics={"n_trials": n_trials, "n_descriptive": n_descriptive, "program_trials": program,
                             "verdict": payload.get("verdict")},
                    status="COMPLETED", report_dir=str(report_dir))
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                                             encoding="utf-8")
    return payload


def _check_order(registry: ExperimentRegistry, step: str) -> dict | None:
    if _done(registry, KINDS[step]) is not None:
        raise AlreadyRun(f"{KINDS[step]} a déjà été exécuté : exécution unique (§ 15)")
    if step == "principale":
        return None
    main = _done(registry, KINDS["principale"])
    if main is None:
        raise AlreadyRun("la question principale n'a pas été exécutée : ordre du § 15")
    if main["metrics"].get("verdict") == ms.INSTRUMENT_TROP_FAIBLE:
        raise AlreadyRun("principale INSTRUMENT_TROP_FAIBLE : ni la variante ni la confirmation ne sont exécutées")
    return main


def sealed_pass(settings: Settings, step: str, *, now: datetime, workers: int, say: Callable[[str], None]) -> dict:
    """Principale ou variante, en passage unique scellé (m5)."""
    period = ms.MAIN if step == "principale" else ms.VARIANT
    question = "principale" if step == "principale" else "variante"
    state = require_ready(settings, step=step)
    registry = ExperimentRegistry(settings.experiments_db)
    main = _check_order(registry, step)
    controls = controls_of(CONTROLES_PRINCIPALE if question == "principale" else CONTROLES_VARIANTE, question=question)
    run_id = new_run_id(PREFIX[step])
    report_dir = settings.reports_dir / run_id
    data = load_real(settings, period, say=say)
    s1 = s1_real(settings, data, mt.day_index(S1_FIRST, period.end), say=say)
    forecaster = forecaster_of(data, settings) if period.rule == "F6" else None
    say("couleurs (scellées)")
    states = real_states(settings, data, period, s1, forecaster)
    seal = write_seal(report_dir, states)
    color = states["color"].to_numpy()
    say("puissance sur la vraie suite des couleurs")
    real_power = mc.real_color_power(color, period, workers=workers)
    rule = controls["rule"]
    superiority = min(rule["superiority"], real_power["superiority"])
    equivalence = min(rule["equivalence"], real_power["equivalence"])
    judgeable = equivalence >= mc.CRITERIA["power"] and rule["false_equivalence_max"] <= mc.CRITERIA["false_equivalence"]
    hashes = data.hashes | {"scelle": seal, "s1": fingerprint(s1[["day", "symbol", "at", "r_gross"]])}
    base = {"run_id": run_id, "period_start": str(period.start.date()), "period_end": str(period.end.date()),
            "puissances": {"feu_synthetique": {k: rule[k] for k in ("superiority", "equivalence")},
                           "feu_reel": real_power, "retenues": {"superiorite": superiority, "equivalence": equivalence},
                           "fausses_equivalences_max": rule["false_equivalence_max"]},
            "scelle": {"fichier": str(report_dir / SEAL_FILE), "empreinte": seal},
            "membres_sans_bougies_1h": data.missing_hourly}
    if superiority < mc.CRITERIA["power"]:
        base["verdict"] = ms.INSTRUMENT_TROP_FAIBLE
        base["global"] = ms.global_verdict(ms.INSTRUMENT_TROP_FAIBLE, None)
        return _record(settings, registry, step=step, now=now, state=state, n_trials=0, n_descriptive=0,
                       payload=base, hashes=hashes, report_dir=report_dir, universe=sorted(data.pairs))
    say("question décisive")
    table, basket_info = basket_table(data, period, forecaster)
    y = table["y"].to_numpy(float)
    analysis = ms.analyse(color, y, period)
    pct_analysis = ms.analyse(color, table["pct"].to_numpy(float), period, with_guards=False)
    if step == "principale":
        ms.decide(analysis, equivalence_judgeable=judgeable)
        base["verdict"] = analysis.verdict
        base["equivalence_jugeable"] = judgeable
    else:
        assert main is not None
        outcome = ms.judge_variant(analysis, main["metrics"]["verdict"], equivalence_judgeable=judgeable)
        base["verdict"] = (f"{analysis.verdict} (hypothèse {outcome['hypothesis']})" if outcome["hypothesis"]
                           else "DECRITE")
        base["variante"] = outcome
        base["global"] = ms.global_verdict(main["metrics"]["verdict"], outcome)
        only = ms.VARIANT_ONLY
        n_only = len(only.days)
        base["sous_periode_2018_2020"] = contrast(color[:n_only], y[:n_only], only)
    risk = s1["risk_pct"].dropna()
    base["borne"] = ms.upper_bound_text(analysis, sigma_median=float(np.nanmedian(table["sigma_median"])),
                                        pct_ci=pct_analysis.ci,
                                        r_unit_pct=float(risk.median() * 100) if len(risk) else None)
    sigma_median = float(np.nanmedian(table["sigma_median"]))
    if analysis.equivalence and np.isfinite(pct_analysis.ci[1]) and pct_analysis.ci[1] > ms.DELTA_MIN * sigma_median:
        base["remarque_pct"] = "équivalent en σ, pas en % (borne haute en % brut au-delà de la marge traduite, § 5.5)"
    base["analyse"] = asdict(analysis)
    base["panier"] = basket_info | {"paires": int(table["n"].max()), "jours_sans_y": int(table["y"].isna().sum())}
    descriptive, n_descriptive = describe(states, table, s1[s1["day"] >= period.start], period, analysis)
    base["descriptif"] = descriptive
    table.to_parquet(report_dir / "s2n.parquet")
    return _record(settings, registry, step=step, now=now, state=state, n_trials=1, n_descriptive=n_descriptive,
                   payload=base, hashes=hashes, report_dir=report_dir, universe=sorted(data.pairs))


def confirmation(settings: Settings, *, now: datetime, workers: int, say: Callable[[str], None]) -> dict:
    """Confirmation C (§ 8) : même question, même règle, sur le sous-panier des membres hors des 40 paires de
    recherche, couleurs de la principale (fichier scellé vérifié), niveau 0,025 (intervalle à 95 %)."""
    state = require_ready(settings, step="confirmation")
    registry = ExperimentRegistry(settings.experiments_db)
    main = _check_order(registry, "confirmation")
    assert main is not None
    summary = json.loads((Path(main["report_dir"]) / "summary.json").read_text(encoding="utf-8"))
    states = read_seal(Path(summary["scelle"]["fichier"]), main["data_hashes"]["scelle"])
    period = ms.MAIN
    data = load_real(settings, period, say=say)
    forecaster = forecaster_of(data, settings)
    symbols = [s for s in data.pairs if s not in RESEARCH_UNIVERSE]
    table, basket_info = basket_table(data, period, forecaster, symbols=symbols)
    analysis = ms.analyse(states["color"].to_numpy(), table["y"].to_numpy(float), period, ci_level=ms.CI_LEVEL_C)
    ms.decide(analysis, alpha_side=ms.ALPHA_SIDE_C, equivalence_judgeable=summary.get("equivalence_jugeable", True))
    run_id = new_run_id(PREFIX["confirmation"])
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "period_start": str(period.start.date()), "period_end": str(period.end.date()),
               "verdict": analysis.verdict, "analyse": asdict(analysis), "panier": basket_info,
               "mention": ms.confirmation_mention(main["metrics"]["verdict"], analysis.verdict),
               "borne": ms.upper_bound_text(analysis, sigma_median=float(np.nanmedian(table["sigma_median"])))}
    return _record(settings, registry, step="confirmation", now=now, state=state, n_trials=1, n_descriptive=0,
                   payload=payload, hashes=data.hashes | {"scelle_principale": main["data_hashes"]["scelle"]},
                   report_dir=report_dir, universe=symbols)


def run_step(settings: Settings, step: str, *, now: datetime, workers: int = 4,
             progress: Callable[[str], None] | None = None) -> dict:
    say = progress or (lambda _t: None)
    workers = max(1, min(4, workers))
    if step == "confirmation":
        return confirmation(settings, now=now, workers=workers, say=say)
    if step in ("principale", "variante"):
        return sealed_pass(settings, step, now=now, workers=workers, say=say)
    raise ValueError(f"étape inconnue : {step}")


def public_summary(payload: dict) -> dict:
    """Ce que la commande affiche à la fin du passage : verdicts, bornes, puissances, comptes d'essais."""
    keys = ("run_id", "kind", "verdict", "global", "variante", "mention", "equivalence_jugeable", "puissances", "borne",
            "n_trials", "n_descriptive", "program_trials")
    return {k: payload[k] for k in keys if k in payload}


_ = (ADVERSE, Path)
