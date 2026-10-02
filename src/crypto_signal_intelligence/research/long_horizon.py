"""Lot 8 v2 — horizons longs et ciblage de volatilité : docs/LONG_HORIZON.md (déclaré avant exécution, 3 essais au
plus ; mission du propriétaire du 2026-10-02, phase 2).

- A : tendance par VOTE de trois horizons fixés (4, 12 et 26 semaines : un actif est détenu si au moins 2 des 3
  rendements passés sont positifs), poids 1/5 × min(1, σ_cible / σ̂) avec σ_cible = 50 % par an FIGÉE et σ̂ la
  volatilité PRÉVUE à 7 jours (modèle HAR + BTC du protocole de volatilité, réajusté le 1er du mois sur le seul
  passé), plafond 100 %, bande de 5 points ;
- A + funding : poids divisé par deux quand le financement moyen du perpétuel sur 7 jours dépasse 0,05 % / 8 h ;
  lancé seulement si le filtre s'active au moins 2 % du temps (sinon l'essai n'est pas consommé) ;
- B : les 5 paires liquides les moins volatiles sur 180 jours, tous les 26 lundis, parts égales, stop à −25 % puis
  stablecoin jusqu'à la sélection suivante.
Références : allocation STATIQUE au panier égale à l'exposition moyenne du modèle (le reste en stablecoin, sans
rendement), et, pour information, buy-and-hold du même panier ; frais inclus. Univers : paires de recherche
admises par le screening halal, éligibles par leur volume passé à chaque date. DEVELOPMENT seulement ; aucun
ordre ; audit des fuites d'abord.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.stats import norm

from ..config import Settings
from . import factors as fa
from . import volatility as vol
from .derivatives_screen import fingerprint
from .experiments import ExperimentRegistry, dependency_versions, new_run_id
from .protocol import development_end
from .universe import MARKET, RESEARCH_UNIVERSE

KIND, STRATEGY, PROTOCOL_VERSION = "LONG_HORIZON", "LONG_HORIZON", 2
DOC = "docs/LONG_HORIZON.md"
N_TRIALS = 3
LEVEL = 1 - 0.025 / N_TRIALS
DAY = pd.Timedelta(days=1)
FIRST_DECISION = pd.Timestamp("2019-01-07", tz="UTC")
FOLD_STARTS = tuple(pd.Timestamp(d, tz="UTC") for d in (
    "2019-01-07", "2019-07-01", "2020-07-06", "2021-07-05", "2022-07-04", "2023-07-03", "2024-07-01"))
CORE = ("BTCUSDT", "ETHUSDT")
EXTRA_LIQUID, BASKET = 3, 5
HORIZONS_DAYS, VOTE_MIN = (28, 84, 182), 2        # 4, 12 et 26 semaines ; détenu si ≥ 2 rendements positifs
SIGMA_TARGET = 0.50                               # volatilité cible annualisée, FIGÉE (mission du 2026-10-02)
BAND = 0.05
VOL_HORIZON, VOL_MODEL = 7, "M4_HAR_POOLED_BTC"
FUNDING_DAYS, FUNDING_LIMIT, FUNDING_CUT = 7, 0.0005, 0.5
FUNDING_MIN_ACTIVATION = 0.02                     # part des (décision, actif) où le filtre agit : en dessous, pas d'essai
B_EVERY, B_COUNT, B_DAYS, B_MIN_DAYS, B_STOP = 26, 5, 180, 170, 0.25
FEE, FEE_ADVERSE = 7.5e-4, 1e-3
SPREAD = {"BTCUSDT": 2e-4, "ETHUSDT": 2e-4}
SPREAD_OTHER = 5e-4
MIN_RATIO_RETURN, MAX_RATIO_DRAWDOWN, MIN_FOLDS_DRAWDOWN = 0.8, 0.6, 5
DSR_MIN = 0.95                                    # quasi inatteignable avec 716+ essais : critère secondaire


class LeakAuditFailed(RuntimeError):
    pass


def cost_per_side(symbols: list[str], *, adverse: bool) -> pd.Series:
    fee = FEE_ADVERSE if adverse else FEE
    spread = pd.Series({s: SPREAD.get(s, SPREAD_OTHER) for s in symbols})
    return fee + spread * (2 if adverse else 1)


# --- Volatilité prévue hors échantillon ------------------------------------------------------------------------

def vol_forecasts(frames: dict[str, pd.DataFrame], decisions: pd.DatetimeIndex, *, seed: int) -> pd.DataFrame:
    """σ̂ annualisée à 7 jours (modèle HAR + BTC) à chaque décision (lignes) et paire (colonnes). Le modèle d'un mois
    est ajusté le 1er de ce mois sur les seules lignes purgées (`vol.fit_at`) : rien de postérieur n'est lu."""
    market = frames[MARKET][list(vol.READ_COLUMNS)]
    data = pd.concat([vol.daily_frame(f[list(vol.READ_COLUMNS)], market).assign(symbol=s) for s, f in frames.items()],
                     ignore_index=True)
    out = pd.DataFrame(np.nan, index=decisions, columns=list(frames))
    usable = vol.complete_rows(data) & (data["history_days"] >= vol.MIN_HISTORY_DAYS)
    months = sorted({pd.Timestamp(year=m.year, month=m.month, day=1, tz="UTC") for m in pd.DatetimeIndex(decisions)})
    for refit in months:
        moments = decisions[(decisions >= refit) & (decisions < refit + pd.offsets.MonthBegin(1))]
        rows = data[usable & data["origin"].isin(moments)]
        if rows.empty:
            continue
        models = vol.fit_at(data, refit, VOL_HORIZON, seed=seed)
        table = vol.month_forecasts(models, rows, VOL_HORIZON)
        sigma = np.sqrt(table[VOL_MODEL].to_numpy(float) * 365 / VOL_HORIZON)
        for symbol, origin, value in zip(table["symbol"], table["origin"], sigma, strict=True):
            out.loc[origin, symbol] = value
    return out


def funding_mean(funding: dict[str, pd.DataFrame], decisions: pd.DatetimeIndex) -> pd.DataFrame:
    """Financement moyen (par 8 h) des règlements connus dans les 7 jours avant chaque décision."""
    out = pd.DataFrame(np.nan, index=decisions, columns=list(funding))
    for symbol, frame in funding.items():
        if frame.empty:
            continue
        # `available_at` peut arriver en objets Timestamp (magasin des dérivés) : comparé en datetime64 UTC.
        known = pd.to_datetime(frame["available_at"], utc=True).dt.tz_convert(None).to_numpy(dtype="datetime64[ns]")
        rate8 = (frame["rate"] * 8 / frame["interval_hours"]).to_numpy(float)
        for moment in decisions:
            at = np.datetime64(pd.Timestamp(moment).tz_convert("UTC").tz_convert(None), "ns")
            pick = (known <= at) & (known > at - np.timedelta64(FUNDING_DAYS, "D"))
            if pick.any():
                out.loc[moment, symbol] = float(rate8[pick].mean())
    return out


# --- Poids ---------------------------------------------------------------------------------------------------

@dataclass
class Inputs:
    panel: fa.Panel
    decisions: pd.DatetimeIndex
    sigma: pd.DataFrame                  # σ̂ annualisée par décision et paire
    funding: pd.DataFrame                # financement moyen 7 jours par décision et paire (NaN : inconnu)


def baskets(inputs: Inputs, book: fa.Book) -> dict[pd.Timestamp, list[str]]:
    """Panier de A à chaque décision : BTC et ETH (si éligibles avec σ̂), puis les plus liquides éligibles avec σ̂."""
    volume = inputs.panel.volume.rolling(fa.LIQUIDITY_DAYS, min_periods=fa.MIN_LIQUIDITY_DAYS).median()
    out = {}
    for moment in inputs.decisions:
        ok = book.eligible.loc[moment] & inputs.sigma.loc[moment].reindex(book.eligible.columns).notna()
        members = [s for s in CORE if ok.get(s, False)]
        others = volume.loc[moment][ok & ~ok.index.isin(CORE)].sort_values(ascending=False)
        members += list(others.index[:BASKET - len(members)])
        out[moment] = members[:BASKET]
    return out


def trend_votes(book: fa.Book) -> pd.DataFrame:
    """Nombre d'horizons (4, 12, 26 semaines) dont le rendement passé est strictement positif ; un horizon sans
    historique ne vote pas."""
    votes = None
    for days in HORIZONS_DAYS:
        positive = (book.momentum(days) > 0).astype(int)
        votes = positive if votes is None else votes + positive
    return votes


def weights_a(inputs: Inputs, book: fa.Book, *, with_funding: bool) -> pd.DataFrame:
    symbols = inputs.panel.symbols
    votes = trend_votes(book)
    out = pd.DataFrame(0.0, index=inputs.decisions, columns=symbols)
    for moment, members in baskets(inputs, book).items():
        for symbol in members:
            if votes.loc[moment, symbol] < VOTE_MIN:
                continue
            sigma = float(inputs.sigma.loc[moment, symbol])
            weight = min(1.0, SIGMA_TARGET / sigma) / BASKET if sigma > 0 else 0.0
            if with_funding and symbol in inputs.funding.columns and inputs.funding.loc[moment, symbol] > FUNDING_LIMIT:
                weight *= FUNDING_CUT
            out.loc[moment, symbol] = weight
    return out


def funding_activation(inputs: Inputs, book: fa.Book) -> float:
    """Part des couples (décision, actif du panier) où le filtre funding agit (financement connu > limite)."""
    pairs, active = 0, 0
    for moment, members in baskets(inputs, book).items():
        for symbol in members:
            pairs += 1
            if symbol in inputs.funding.columns and inputs.funding.loc[moment, symbol] > FUNDING_LIMIT:
                active += 1
    return round(active / pairs, 4) if pairs else 0.0


def static_targets(table: pd.DataFrame, exposure: float) -> dict[pd.Timestamp, pd.Series]:
    """Référence statique : à chaque changement de composition, `exposure` du portefeuille réparti à parts égales
    entre les membres du panier (le reste en stablecoin) ; échangé seulement à ces changements."""
    out: dict[pd.Timestamp, pd.Series] = {}
    previous = None
    for moment in table.index:
        members = list(table.columns[table.loc[moment] > 0])
        key = frozenset(members)
        if key != previous:
            out[moment] = pd.Series(exposure / len(members) if members else 0.0, index=members, dtype=float)
            previous = key
    return out


def weights_bench_a(inputs: Inputs, book: fa.Book) -> pd.DataFrame:
    out = pd.DataFrame(0.0, index=inputs.decisions, columns=inputs.panel.symbols)
    for moment, members in baskets(inputs, book).items():
        for symbol in members:
            out.loc[moment, symbol] = 1 / len(members)
    return out


def selections_b(inputs: Inputs, book: fa.Book) -> dict[pd.Timestamp, list[str]]:
    """Tous les 26 lundis : les 5 paires éligibles de plus faible volatilité réalisée sur 180 jours."""
    returns = book.returns
    volatility = returns.rolling(B_DAYS, min_periods=B_MIN_DAYS).std()
    out = {}
    for moment in inputs.decisions[::B_EVERY]:
        ok = book.eligible.loc[moment] & volatility.loc[moment].notna()
        out[moment] = list(volatility.loc[moment][ok].sort_values().index[:B_COUNT])
    return out


# --- Simulation ----------------------------------------------------------------------------------------------

@dataclass
class Run:
    weekly: pd.Series
    values: pd.Series
    trades: int
    fees: float
    exposure: pd.Series


def simulate(panel: fa.Panel, targets: dict[pd.Timestamp, pd.Series], decisions: pd.DatetimeIndex, *,
             costs: pd.Series, delay_days: int = 0, band: float | None = None, full_rebalance: bool = False,
             stop: float | None = None) -> Run:
    """Portefeuille valorisé chaque jour au prix de 01:00. `targets` : poids voulus aux dates où l'on peut échanger
    (exécution `delay_days` jours plus tard). Échange d'un actif si son poids s'écarte de la cible de plus de `band`
    ou si l'on entre / sort ; `full_rebalance` : tout est ramené à la cible. `stop` : vente d'une position dont la
    clôture connue passe sous (1 − stop) × prix d'achat ; sa part reste en stablecoin jusqu'à la cible suivante."""
    symbols = panel.symbols
    delay = delay_days * DAY
    last_day = panel.price.dropna(how="all").index.max()
    while len(decisions) > 1 and decisions[-1] + 7 * DAY + delay > last_day:
        decisions = decisions[:-1]
    checkpoints = decisions.append(pd.DatetimeIndex([decisions[-1] + 7 * DAY])) + delay
    days = panel.price.index[(panel.price.index >= checkpoints[0]) & (panel.price.index <= checkpoints[-1])]
    filled = panel.price.ffill().reindex(days).to_numpy(float)
    tradable = panel.price.reindex(days).notna().to_numpy()
    closes = panel.close.reindex(days).to_numpy(float)
    cost = costs.reindex(symbols).fillna(SPREAD_OTHER + FEE).to_numpy(float)
    plan = {moment + delay: target.reindex(symbols).fillna(0.0).to_numpy(float) for moment, target in targets.items()}
    holdings, cash, entry = np.zeros(len(symbols)), 1.0, np.full(len(symbols), np.nan)
    values, exposure, trades, fees = [], {}, 0, 0.0
    for i, day in enumerate(days):
        if i:
            ratio = np.where(np.isfinite(filled[i]) & np.isfinite(filled[i - 1]) & (filled[i - 1] > 0),
                             filled[i] / filled[i - 1], 1.0)
            holdings = holdings * ratio
        total = cash + holdings.sum()
        values.append(total)
        desired = holdings.copy()
        if stop is not None:
            hit = (holdings > 0) & np.isfinite(closes[i]) & np.isfinite(entry) & (closes[i] < (1 - stop) * entry) \
                & tradable[i]
            desired[hit] = 0.0
        target = plan.get(day)
        if target is not None:
            current = holdings / total if total > 0 else holdings
            if band is None or full_rebalance:
                change = np.ones(len(symbols), dtype=bool)
            else:
                change = (np.abs(target - current) > band) | ((target > 0) != (holdings > 1e-12))
            desired = np.where(change, target * total, desired)
        desired = np.where(tradable[i], desired, holdings)
        moved = np.abs(desired - holdings) > 1e-12
        if moved.any():
            locked = holdings[~tradable[i]].sum()
            room = total - locked
            wanted = desired[tradable[i]].sum()
            if wanted > room > 0:
                desired[tradable[i]] *= room / wanted
            fee_now = float((np.abs(desired - holdings) * cost).sum())
            scale = 1 - fee_now / total if total > 0 else 1.0
            desired[tradable[i]] *= scale
            bought = moved & (desired > holdings)
            entry = np.where(bought & (holdings <= 1e-12), filled[i], entry)
            entry = np.where(desired <= 1e-12, np.nan, entry)
            trades += int(moved.sum())
            fees += fee_now
            cash = total - fee_now - desired.sum()
            holdings = desired
        if target is not None:
            exposure[day - delay] = float(holdings.sum() / (cash + holdings.sum())) if cash + holdings.sum() > 0 else 0.0
    series = pd.Series(values, index=days)
    at = series.reindex(checkpoints).to_numpy()
    return Run(pd.Series(at[1:] / at[:-1] - 1, index=decisions), series, trades, round(fees, 6), pd.Series(exposure))


# --- Mesures ----------------------------------------------------------------------------------------------------

def deflated_sharpe(weekly: np.ndarray, trials: int) -> float | None:
    """Sharpe déflaté (Bailey et López de Prado, 2014) : P(vrai Sharpe > SR₀), SR₀ = maximum attendu par hasard
    parmi `trials` essais, V = 1/T (variance de l'estimateur sous l'hypothèse nulle) ; Sharpe hebdomadaire."""
    x = np.asarray(weekly, dtype=float)
    t = len(x)
    if t < 30 or x.std(ddof=1) <= 0:
        return None
    sr = x.mean() / x.std(ddof=1)
    skew = float(((x - x.mean()) ** 3).mean() / x.std(ddof=0) ** 3)
    kurt = float(((x - x.mean()) ** 4).mean() / x.std(ddof=0) ** 4)
    gamma = 0.5772156649015329
    sr0 = math.sqrt(1 / t) * ((1 - gamma) * norm.ppf(1 - 1 / trials) + gamma * norm.ppf(1 - 1 / (trials * math.e)))
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    return round(float(norm.cdf((sr - sr0) * math.sqrt(t - 1) / denom)), 4)


def drawdown_days(values: pd.Series) -> int:
    """Plus longue durée (jours) entre un sommet et son dépassement ; une perte non rattrapée compte jusqu'à la fin."""
    if values.empty:
        return 0
    peak, peak_day, longest = -math.inf, values.index[0], 0
    for day, value in values.items():
        if value >= peak:
            longest = max(longest, (day - peak_day).days)
            peak, peak_day = value, day
    if values.iloc[-1] < peak:
        longest = max(longest, (values.index[-1] - peak_day).days)
    return int(longest)


def fold_of(moments: pd.DatetimeIndex) -> np.ndarray:
    return np.asarray(pd.DatetimeIndex(FOLD_STARTS).searchsorted(pd.DatetimeIndex(moments), side="right")) - 1


def describe(run: Run, *, trials: int) -> dict:
    weekly = run.weekly.to_numpy(float)
    total = float(np.prod(1 + weekly) - 1)
    years = len(weekly) / 52
    folds = fold_of(run.weekly.index)
    by_fold: list[dict | None] = []
    for k in range(len(FOLD_STARTS)):
        moments = run.weekly.index[folds == k]
        if len(moments) < 2:
            by_fold.append(None)
            continue
        part = run.values[(run.values.index >= moments[0]) & (run.values.index <= moments[-1] + 7 * DAY)]
        by_fold.append({"sharpe": round(fa.sharpe(weekly[folds == k]), 4), "max_drawdown": round(fa.max_drawdown(part.to_numpy()), 4)})
    return {"weeks": len(weekly), "annual_return": round((1 + total) ** (1 / years) - 1, 4) if years > 0 and total > -1 else None,
            "volatility": round(float(weekly.std(ddof=1) * math.sqrt(52)), 4), "sharpe": round(fa.sharpe(weekly), 4),
            "deflated_sharpe": deflated_sharpe(weekly, trials), "max_drawdown": round(fa.max_drawdown(run.values.to_numpy()), 4),
            "drawdown_days": drawdown_days(run.values), "trades": run.trades, "fees_pct": round(run.fees * 100, 3),
            "average_exposure": round(float(run.exposure.mean()), 4) if len(run.exposure) else 0.0, "folds": by_fold}


def judge(model: dict, reference: dict, ci: list[float] | None) -> dict:
    """Critères du §7 (v2 : référence STATIQUE), pour UN scénario de coûts. Le critère ajusté au risque exige un
    Sharpe déflaté ≥ 0,95, quasi inatteignable avec 716+ essais : « perte réduite » est le critère réaliste."""
    better_risk = bool(ci is not None and ci[0] > 0 and (model["deflated_sharpe"] or 0.0) >= DSR_MIN)
    ref_return, ret = reference["annual_return"], model["annual_return"]
    comparable = ret is not None and ref_return is not None and (
        ret >= MIN_RATIO_RETURN * ref_return if ref_return > 0 else ret >= ref_return)
    shallower = abs(model["max_drawdown"]) <= MAX_RATIO_DRAWDOWN * abs(reference["max_drawdown"])
    folds = sum(1 for m, r in zip(model["folds"], reference["folds"], strict=True)
                if m and r and abs(m["max_drawdown"]) < abs(r["max_drawdown"]))
    lower_loss = bool(comparable and shallower and folds >= MIN_FOLDS_DRAWDOWN)
    return {"risque_ajuste": better_risk, "rendement_comparable": bool(comparable), "perte_max_reduite": bool(shallower),
            "validations_perte_plus_faible": folds, "perte_reduite": lower_loss}


def verdict(central: dict, adverse: dict) -> str:
    if central["risque_ajuste"] and adverse["risque_ajuste"]:
        return "INTERESSANT_RISQUE_AJUSTE"
    if central["perte_reduite"] and adverse["perte_reduite"]:
        return "INTERESSANT_PERTE_REDUITE"
    return "NON_INTERESSANT"


# --- Exécution --------------------------------------------------------------------------------------------------

def load_funding(settings: Settings, symbols: list[str], end: pd.Timestamp) -> dict[str, pd.DataFrame]:
    from ..derivatives.history import DerivativesStore
    store = DerivativesStore(settings.data_dir)
    out = {}
    for symbol in symbols:
        frame = store.load("funding", symbol)
        if frame.empty or "available_at" not in frame.columns:
            continue                                   # pas de perpétuel, ou historique absent : pas de filtre
        out[symbol] = frame[frame["available_at"] <= end].reset_index(drop=True)
    return out


def prepare(settings: Settings, frames: dict[str, pd.DataFrame], funding: dict[str, pd.DataFrame], *,
            first: pd.Timestamp | None = None) -> Inputs:
    panel = fa.build_panel(frames)
    decisions = fa.decisions_of(panel, first=first if first is not None else FIRST_DECISION)
    return Inputs(panel, decisions, vol_forecasts(frames, decisions, seed=settings.protocol.seed),
                  funding_mean(funding, decisions))


def all_weights(inputs: Inputs) -> dict[str, pd.DataFrame]:
    book = fa.Book(inputs.panel, inputs.decisions)
    return {"A": weights_a(inputs, book, with_funding=False), "A_FUNDING": weights_a(inputs, book, with_funding=True),
            "REF_A": weights_bench_a(inputs, book)}


def leak_audit(settings: Settings, frames: dict[str, pd.DataFrame], funding: dict[str, pd.DataFrame], inputs: Inputs,
               *, seed: int, picks: int = 4) -> dict:
    """Poids de A, A + funding, référence et sélection de B recalculés avec les seules données antérieures à une
    décision (bougies, financement), puis avec un futur falsifié : identiques. Mutation : un σ̂ qui lit la semaine
    suivante doit être détecté."""
    rng = np.random.default_rng(seed)
    full = all_weights(inputs)
    chosen = sorted(rng.choice(np.arange(len(inputs.decisions) // 3, len(inputs.decisions)), size=picks, replace=False))
    violations: list[dict] = []
    detected = False
    for pick in chosen:
        moment = inputs.decisions[pick]
        variants = {
            "tronqué": ({s: f[f["open_time"] < moment] for s, f in frames.items()},
                        {s: f[f["available_at"] <= moment] for s, f in funding.items()}),
            "futur falsifié": ({s: _falsify(f, moment, rng) for s, f in frames.items()},
                               {s: f.assign(rate=np.where(f["available_at"] > moment, f["rate"] * 50, f["rate"]))
                                for s, f in funding.items()}),
        }
        for label, (cut_frames, cut_funding) in variants.items():
            kept = {s: f for s, f in cut_frames.items() if not f.empty}
            past = inputs.decisions[inputs.decisions <= moment]
            again = Inputs(fa.build_panel(kept), past, vol_forecasts(kept, past, seed=settings.protocol.seed),
                           funding_mean(cut_funding, past))
            redo = all_weights(again)
            for key, table in full.items():
                row = redo[key].loc[moment].reindex(table.columns).fillna(0.0).to_numpy()
                if not np.allclose(row, table.loc[moment].to_numpy(), atol=1e-12):
                    violations.append({"decision": str(moment), "check": label, "weights": key})
        # Mutation : un σ̂ qui lit la semaine SUIVANTE (décuplé, pour qu'un plafond min(1, σ_cible/σ̂) déjà atteint
        # ne masque pas la lecture) doit changer au moins un poids de A aux décisions tirées.
        leaky = inputs.sigma.shift(-1) * 10
        mutated = all_weights(Inputs(inputs.panel, inputs.decisions, leaky, inputs.funding))["A"]
        detected |= not np.allclose(mutated.loc[moment].to_numpy(), full["A"].loc[moment].to_numpy(), atol=1e-12)
    return {"violations": violations, "mutation_detected": bool(detected), "decisions": [str(inputs.decisions[p]) for p in chosen],
            "passed": not violations and bool(detected)}


def _falsify(frame: pd.DataFrame, moment: pd.Timestamp, rng) -> pd.DataFrame:
    frame = frame.copy()
    future = (frame["open_time"] >= moment).to_numpy()
    factor = rng.uniform(0.5, 1.5, int(future.sum()))
    for column in ("open", "high", "low", "close", "quote_volume"):
        frame.loc[future, column] = frame.loc[future, column].to_numpy(float) * factor
    return frame


@dataclass
class Result:
    run_id: str
    period_end: str
    n_trials: int = N_TRIALS
    program_trials: int = 0
    level: float = LEVEL
    leak_audit: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    data_hashes: dict = field(default_factory=dict)
    models: dict = field(default_factory=dict)
    verdicts: dict = field(default_factory=dict)


def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None,
        progress: Callable[[str], None] | None = None, allow_dirty: bool = False) -> Result:
    say = progress or (lambda _text: None)
    state = fa.code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise fa.DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    symbols = list(symbols or RESEARCH_UNIVERSE)
    end = pd.Timestamp(development_end(settings))
    result = Result(new_run_id("LONG"), end.isoformat())
    say("données")
    frames = fa.load_frames(settings, symbols, end)
    funding = load_funding(settings, symbols, end)
    result.data_hashes = {s: fingerprint(f) for s, f in frames.items()}
    say("volatilité prévue (réajustements mensuels)")
    inputs = prepare(settings, frames, funding)
    say("audit des fuites")
    result.leak_audit = leak_audit(settings, frames, funding, inputs, seed=settings.protocol.seed)
    if not result.leak_audit["passed"]:
        raise LeakAuditFailed(f"audit des fuites en échec : {result.leak_audit}")
    book = fa.Book(inputs.panel, inputs.decisions)
    weights = all_weights(inputs)
    selections = selections_b(inputs, book)
    decisions = inputs.decisions
    activation = funding_activation(inputs, book)
    with_funding = activation >= FUNDING_MIN_ACTIVATION
    result.n_trials = 3 if with_funding else 2
    before = ExperimentRegistry(settings.experiments_db).program_trials()
    trials = before + result.n_trials
    funding_first = {s: str(f["time"].min())[:10] for s, f in funding.items() if not f.empty and "time" in f.columns}
    result.coverage = {"decisions": len(decisions), "first": str(decisions[0]), "last": str(decisions[-1]),
                       "baskets": {str(m): b for m, b in list(baskets(inputs, book).items())[::13]},
                       "b_selections": {str(m): s for m, s in selections.items()}, "b_rebalances": len(selections),
                       "funding_first": funding_first, "funding_activation": activation,
                       "funding_trial": with_funding,
                       "unlock_filter": "non implémenté : aucune source historique fiable (docs/LONG_HORIZON.md §3)"}
    pairs = inputs.panel.symbols
    as_targets = lambda table: {m: table.loc[m] for m in table.index}            # noqa: E731
    b_targets = {m: pd.Series(1 / len(s), index=s) if s else pd.Series(dtype=float) for m, s in selections.items()}
    ref_b = {}
    for moment in selections:
        eligible = list(book.eligible.loc[moment][book.eligible.loc[moment]].index)
        ref_b[moment] = pd.Series(1 / len(eligible), index=eligible) if eligible else pd.Series(dtype=float)
    specs: dict[str, dict] = {
        "A": dict(targets=as_targets(weights["A"]), band=BAND),
        "REF_A": dict(targets=_membership_changes(weights["REF_A"]), full_rebalance=True),
        "B": dict(targets=b_targets, full_rebalance=True, stop=B_STOP),
        "REF_B": dict(targets=ref_b, full_rebalance=True),
        "B_SANS_STOP": dict(targets=b_targets, full_rebalance=True),
        "BTC": dict(targets={decisions[0]: pd.Series({MARKET: 1.0})}, full_rebalance=True),
    }
    if with_funding:
        specs["A_FUNDING"] = dict(targets=as_targets(weights["A_FUNDING"]), band=BAND)
    runs: dict[str, dict[str, Run]] = {"central": {}, "adverse": {}}
    b_table = pd.DataFrame(0.0, index=list(selections), columns=pairs)
    for moment, members in selections.items():
        for symbol in members:
            b_table.loc[moment, symbol] = 1 / len(members)
    for scenario, adverse in (("central", False), ("adverse", True)):
        costs = cost_per_side(pairs, adverse=adverse)
        for key, spec in specs.items():
            say(f"{key} ({scenario})")
            runs[scenario][key] = simulate(inputs.panel, spec["targets"], decisions, costs=costs,
                                           delay_days=1 if adverse else 0, band=spec.get("band"),
                                           full_rebalance=spec.get("full_rebalance", False), stop=spec.get("stop"))
        # Références STATIQUES (mission du 2026-10-02) : exposition moyenne du modèle, répartie à parts égales sur son
        # panier, le reste en stablecoin ; échangées seulement quand la composition change.
        for model, table in (("A", weights["REF_A"]), ("B", b_table)):
            exposure = float(runs[scenario][model].exposure.mean()) if len(runs[scenario][model].exposure) else 0.0
            say(f"STATIC_{model} ({scenario})")
            runs[scenario][f"STATIC_{model}"] = simulate(inputs.panel, static_targets(table, exposure), decisions, costs=costs,
                                                         delay_days=1 if adverse else 0, full_rebalance=True)
    for scenario in runs:
        result.models[scenario] = {k: describe(r, trials=trials) for k, r in runs[scenario].items()}
    comparisons = [("A", "STATIC_A", "REF_A"), ("B", "STATIC_B", "REF_B")]
    if with_funding:
        comparisons.insert(1, ("A_FUNDING", "STATIC_A", "REF_A"))
    for model, reference, info in comparisons:
        judged: dict = {}
        for scenario in ("central", "adverse"):
            a, b = runs[scenario][model].weekly, runs[scenario][reference].weekly
            common = a.index.intersection(b.index)
            ci = fa.sharpe_diff_ci(a.loc[common].to_numpy(), b.loc[common].to_numpy(), level=LEVEL,
                                   seed=settings.protocol.seed)
            judged[scenario] = judge(result.models[scenario][model], result.models[scenario][reference], ci) | {
                "sharpe_diff": round(fa.sharpe(a.loc[common].to_numpy()) - fa.sharpe(b.loc[common].to_numpy()), 4),
                "sharpe_diff_ci": ci,
                "vs_buy_and_hold": judge(result.models[scenario][model], result.models[scenario][info], None)}
        result.verdicts[model] = {"reference": reference, "buy_and_hold": info,
                                  "verdict": verdict(judged["central"], judged["adverse"])} | judged
    result.program_trials = before + result.n_trials
    _record(settings, result, now=now, symbols=symbols, code=state)
    return result


def _membership_changes(table: pd.DataFrame) -> dict[pd.Timestamp, pd.Series]:
    """Référence de A : on n'échange que quand la composition du panier change."""
    out, previous = {}, None
    for moment in table.index:
        members = frozenset(table.columns[table.loc[moment] > 0])
        if members != previous:
            out[moment] = table.loc[moment]
            previous = members
    return out


def _record(settings: Settings, result: Result, *, now: datetime, symbols: list[str], code: str) -> None:
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(result) | {"protocol_version": PROTOCOL_VERSION, "doc": DOC}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    ExperimentRegistry(settings.experiments_db).record(
        run_id=result.run_id, created_at=now.isoformat(), kind=KIND,
        hypothesis="horizons longs v2 : tendance par vote de 3 horizons (4, 12, 26 semaines) avec ciblage de volatilité "
                   "prévue à 50 % (A, A + funding) et basse volatilité 6 mois avec stop (B) réduisent-ils la perte, à "
                   "rendement comparable, face à une allocation statique au même panier, frais inclus ?",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION,
        variant=f"{result.n_trials} essais figés (docs/LONG_HORIZON.md v2)",
        params={"horizons_days": list(HORIZONS_DAYS), "vote_min": VOTE_MIN, "sigma_target": SIGMA_TARGET, "band": BAND,
                "basket": BASKET, "funding_limit": FUNDING_LIMIT, "funding_min_activation": FUNDING_MIN_ACTIVATION,
                "b": {"every_weeks": B_EVERY, "count": B_COUNT, "days": B_DAYS, "stop": B_STOP}, "level": LEVEL,
                "fee": FEE, "spread": SPREAD, "spread_other": SPREAD_OTHER},
        period_label="DEVELOPMENT", period_start=str(FIRST_DECISION), period_end=result.period_end, universe=symbols,
        data_hashes=result.data_hashes, git_commit=code, dependencies=dependency_versions(), seed=settings.protocol.seed,
        cost_scenario="frais 7,5 pb + écart 2 à 5 pb ; défavorable 10 pb, écart doublé, un jour de retard",
        simulation_rules={"decision": "lundi 00:00 UTC", "execution": "bougie 1 h de 01:00", "leverage": "aucun"},
        metrics={"n_trials": result.n_trials, "program_trials": result.program_trials, "coverage": result.coverage,
                 "verdict": " ; ".join(f"{k} {v['verdict']}" for k, v in result.verdicts.items()),
                 "verdicts": result.verdicts, "models": result.models}, status="COMPLETED", report_dir=str(report_dir))
