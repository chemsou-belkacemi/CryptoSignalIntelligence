"""Lot 7 — portefeuilles hebdomadaires (classement, régime et variantes) : docs/FACTORS.md, protocole v1.

Question posée : un portefeuille rééquilibré UNE FOIS PAR SEMAINE selon une règle fixe fait-il mieux, à risque
égal, que sa référence (toutes les paires éligibles à parts égales, ou BTC acheté-conservé) ? 18 essais
déclarés à l'avance ; DEVELOPMENT seulement ; aucun ordre, aucun signal publié.

Chaîne causale :
- journées 00:00–24:00 UTC construites depuis les bougies 1 h du magasin long ; décision le lundi à 00:00 avec
  les clôtures connues à cet instant ; exécution à l'ouverture de la bougie 1 h de 01:00 ; valorisation
  quotidienne à ce prix ; coûts sur chaque montant échangé ;
- appartenance « à la date » : historique et liquidité mesurés avec les seules données passées ;
- le HMM n'apprend que du passé et n'utilise que des probabilités FILTRÉES ;
- audit des fuites (bougies tronquées, futur falsifié, mutation) avant tout résultat.
"""
from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from functools import cached_property
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import Settings
from .derivatives_screen import fingerprint
from .experiments import ExperimentRegistry, dependency_versions, git_state, new_run_id
from .long_history import load_long
from .protocol import development_end
from .universe import MARKET, RESEARCH_UNIVERSE

KIND, STRATEGY = "FACTORS", "FACTORS_WEEKLY"
PROTOCOL_VERSION = 2
DAY, HOUR = pd.Timedelta(days=1), pd.Timedelta(hours=1)
EXEC_HOUR = 1                              # exécution à l'ouverture de la bougie 1 h de 01:00 UTC
MIN_HOURS_PER_DAY = 20                     # journée valide : au moins 20 bougies horaires
HISTORY_DAYS, MIN_VALID_DAYS = 90, 85
LIQUIDITY_DAYS, MIN_LIQUIDITY_DAYS, MIN_MEDIAN_VOLUME = 30, 25, 1_000_000.0
MAX_CLOSE_AGE_DAYS = 2                     # les signaux lisent la dernière clôture valide, vieille de 2 jours au plus
FIRST_DECISION = pd.Timestamp("2018-07-02", tz="UTC")
FOLD_STARTS = tuple(pd.Timestamp(d, tz="UTC") for d in (
    "2018-07-02", "2019-07-01", "2020-07-06", "2021-07-05", "2022-07-04", "2023-07-03", "2024-07-01"))
BLOCK_WEEKS, BOOTSTRAP_SAMPLES = 8, 50_000
MIN_FOLDS_BETTER, MIN_INVESTED_SHARE = 5, 0.20
MIN_EXPOSURE = 0.20                        # une semaine « investie » : au moins 20 % du portefeuille hors USDT
VOL_DAYS, VOL_MIN_DECISIONS = 28, 26
HMM_MIN_RETURNS, HMM_MAX_ITER, HMM_TOL = 300, 100, 1e-6
AUDIT_DECISIONS = 30
END_TOLERANCE = pd.Timedelta(days=2)
MIN_STD = 1e-12                            # en dessous, une série est constante : son Sharpe vaut 0 par convention
EW, BTC = "EW", "BTC"


class IncompleteData(RuntimeError):
    pass


class LeakAuditFailed(RuntimeError):
    pass


class DirtyCode(RuntimeError):
    pass


def code_state() -> str:
    """Commit du code EXÉCUTÉ (dépôt qui contient ce module), « +DIRTY » s'il a des modifications non commitées."""
    return git_state(Path(__file__).resolve().parents[3])


# --- Panneau journalier ------------------------------------------------------------------------------------

@dataclass
class Panel:
    """Lignes = instants de décision possibles (00:00 UTC) ; colonnes = paires.

    `close` : clôture de la journée qui se termine à l'instant (NaN si la journée n'est pas valide) ;
    `volume` : volume en USDT de cette journée ; `price` : ouverture de la bougie 1 h de 01:00 du même jour,
    c'est-à-dire le premier prix d'exécution APRÈS la décision (jamais lu par une règle de décision)."""
    close: pd.DataFrame
    volume: pd.DataFrame
    price: pd.DataFrame

    @property
    def symbols(self) -> list[str]:
        return list(self.close.columns)


def build_panel(frames: dict[str, pd.DataFrame]) -> Panel:
    closes, volumes, prices = {}, {}, {}
    for symbol, h1 in frames.items():
        if h1.empty:
            continue
        ordered = h1.sort_values("open_time").reset_index(drop=True)
        grouped = ordered.groupby(ordered["open_time"].dt.floor("D"))
        daily = grouped.agg(hours=("close", "size"), close=("close", "last"), volume=("quote_volume", "sum"))
        valid = daily[daily["hours"] >= MIN_HOURS_PER_DAY]
        closes[symbol] = pd.Series(valid["close"].to_numpy(float), index=valid.index + DAY)
        volumes[symbol] = pd.Series(valid["volume"].to_numpy(float), index=valid.index + DAY)
        at_exec = ordered[ordered["open_time"].dt.hour == EXEC_HOUR]
        prices[symbol] = pd.Series(at_exec["open"].to_numpy(float),
                                   index=pd.DatetimeIndex(at_exec["open_time"].dt.floor("D")))
    if not closes:
        raise IncompleteData("aucune bougie dans le magasin long")
    first = min(s.index.min() for s in closes.values())
    last = max(max(s.index.max() for s in closes.values()),
               max((s.index.max() for s in prices.values() if len(s)), default=first))
    index = pd.date_range(first, last, freq="D", tz="UTC")

    def table(series: dict[str, pd.Series]) -> pd.DataFrame:
        return pd.DataFrame({s: v[~v.index.duplicated()] for s, v in series.items()}).reindex(index)

    return Panel(table(closes), table(volumes), table(prices))


def decisions_of(panel: Panel, *, first: pd.Timestamp | None = None) -> pd.DatetimeIndex:
    """Lundis 00:00 UTC, de `first` (défaut : FIRST_DECISION) jusqu'au dernier dont la semaine entière a un prix
    d'exécution derrière elle."""
    first = FIRST_DECISION if first is None else first
    last_price_day = panel.price.dropna(how="all").index.max()
    index = panel.close.index
    return index[(index.dayofweek == 0) & (index >= first) & (index + 7 * DAY <= last_price_day)]


# --- HMM gaussien à 2 états (probabilités filtrées seulement) ---------------------------------------------------

@dataclass(frozen=True)
class HmmParams:
    start: tuple[float, float]
    trans: tuple[tuple[float, float], tuple[float, float]]
    mean: tuple[float, float]
    var: tuple[float, float]

    @property
    def calm(self) -> int:
        """État « calme » : celui de plus faible variance (défini ainsi d'avance)."""
        return 0 if self.var[0] <= self.var[1] else 1


def hmm_initial(x: np.ndarray) -> HmmParams:
    """Départ fixe et déclaré : états séparés par la médiane des écarts absolus, persistance 0,95."""
    deviation = np.abs(x - np.median(x))
    low = x[deviation <= np.median(deviation)]
    high = x[deviation > np.median(deviation)]
    mean = float(np.mean(x))
    floor = 1e-10
    return HmmParams((0.5, 0.5), ((0.95, 0.05), (0.05, 0.95)), (mean, mean),
                     (max(float(np.var(low)), floor), max(float(np.var(high)) if len(high) else floor, floor)))


def _emissions(x: np.ndarray, params: HmmParams) -> tuple[list[float], list[float]]:
    out = []
    for j in (0, 1):
        var = params.var[j]
        out.append((np.exp(-0.5 * (x - params.mean[j]) ** 2 / var) / math.sqrt(2 * math.pi * var) + 1e-300).tolist())
    return out[0], out[1]


def _forward(b0: list[float], b1: list[float], params: HmmParams) -> tuple[list[float], list[float], list[float]]:
    """Récurrence avant normalisée : (P(état 0 | passé), P(état 1 | passé), facteurs d'échelle)."""
    (a00, a01), (a10, a11) = params.trans
    f0: list[float] = []
    f1: list[float] = []
    scale: list[float] = []
    p0, p1 = params.start
    for t in range(len(b0)):
        if t:
            p0, p1 = f0[-1] * a00 + f1[-1] * a10, f0[-1] * a01 + f1[-1] * a11
        u0, u1 = p0 * b0[t], p1 * b1[t]
        total = u0 + u1
        f0.append(u0 / total)
        f1.append(u1 / total)
        scale.append(total)
    return f0, f1, scale


def hmm_filter(x: np.ndarray, params: HmmParams) -> np.ndarray:
    """Probabilités FILTRÉES P(état_t | x_1..t), forme (n, 2) : aucune donnée postérieure à t n'est utilisée."""
    f0, f1, _ = _forward(*_emissions(np.asarray(x, dtype=float), params), params)
    return np.column_stack([f0, f1])


def hmm_fit(x: np.ndarray, init: HmmParams | None = None, *, max_iter: int = HMM_MAX_ITER,
            tol: float = HMM_TOL) -> HmmParams:
    """Baum-Welch (maximum de vraisemblance) sur la série entière fournie ; déterministe."""
    x = np.asarray(x, dtype=float)
    params = init or hmm_initial(x)
    n = len(x)
    previous = -math.inf
    for _ in range(max_iter):
        b0, b1 = _emissions(x, params)
        f0, f1, scale = _forward(b0, b1, params)
        (a00, a01), (a10, a11) = params.trans
        g0, g1 = [0.0] * n, [0.0] * n                    # P(état_t | toute la série)
        g0[-1], g1[-1] = f0[-1], f1[-1]
        r0, r1 = 1.0, 1.0                                # récurrence arrière normalisée
        x00 = x01 = x10 = x11 = 0.0
        for t in range(n - 2, -1, -1):
            c = scale[t + 1]
            m0, m1 = b0[t + 1] * r0 / c, b1[t + 1] * r1 / c
            x00 += f0[t] * a00 * m0
            x01 += f0[t] * a01 * m1
            x10 += f1[t] * a10 * m0
            x11 += f1[t] * a11 * m1
            r0, r1 = a00 * m0 + a01 * m1, a10 * m0 + a11 * m1
            g0[t], g1[t] = f0[t] * r0, f1[t] * r1
        w0, w1 = np.asarray(g0), np.asarray(g1)
        s0, s1 = float(w0.sum()), float(w1.sum())
        mean = (float(w0 @ x) / s0, float(w1 @ x) / s1)
        var = (max(float(w0 @ (x - mean[0]) ** 2) / s0, 1e-10), max(float(w1 @ (x - mean[1]) ** 2) / s1, 1e-10))
        row0, row1 = x00 + x01, x10 + x11
        trans = ((x00 / row0, x01 / row0) if row0 > 0 else params.trans[0],
                 (x10 / row1, x11 / row1) if row1 > 0 else params.trans[1])
        params = HmmParams((g0[0], g1[0]), trans, mean, var)
        likelihood = float(np.sum(np.log(scale)))
        if abs(likelihood - previous) <= tol * max(1.0, abs(likelihood)):
            break
        previous = likelihood
    return params


# --- Signaux et poids --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Trial:
    key: str
    family: str
    description: str
    benchmark: str = EW


class Book:
    """Poids de chaque essai à chaque décision, calculés avec les seules données connues à la décision.
    `leaky` n'existe que pour la mutation de l'audit des fuites (le signal lit la clôture du lendemain)."""

    def __init__(self, panel: Panel, decisions: pd.DatetimeIndex, *, leaky: bool = False):
        self.panel, self.decisions, self.leaky = panel, decisions, leaky

    @cached_property
    def close(self) -> pd.DataFrame:
        close = self.panel.close.ffill(limit=MAX_CLOSE_AGE_DAYS)
        return close.shift(-1) if self.leaky else close

    @cached_property
    def eligible(self) -> pd.DataFrame:
        valid = self.panel.close.notna()
        history = valid.rolling(HISTORY_DAYS, min_periods=HISTORY_DAYS).sum() >= MIN_VALID_DAYS
        liquid = self.panel.volume.rolling(LIQUIDITY_DAYS, min_periods=MIN_LIQUIDITY_DAYS).median() >= MIN_MEDIAN_VOLUME
        return valid & history & liquid

    @cached_property
    def returns(self) -> pd.DataFrame:
        return self.close.pct_change(fill_method=None)

    def momentum(self, days: int) -> pd.DataFrame:
        return self.close / self.close.shift(days) - 1

    def top(self, signal: pd.DataFrame, k: int, *, lowest: bool = False) -> pd.DataFrame:
        ranked = signal.where(self.eligible)
        rank = ranked.rank(axis=1, ascending=lowest, method="first")
        return ((rank <= k) & ranked.notna()).astype(float) / k

    @cached_property
    def equal_weight(self) -> pd.DataFrame:
        count = self.eligible.sum(axis=1)
        return self.eligible.astype(float).div(count.where(count > 0), axis=0).fillna(0.0)

    # régimes : séries booléennes par instant (False tant que le régime n'est pas calculable)
    def regime_sma(self, days: int) -> pd.Series:
        # Dernière clôture connue de BTC, même après une panne de plus de MAX_CLOSE_AGE_DAYS : sinon un seul jour
        # manquant (panne de février 2018) rendrait la moyenne incalculable pendant `days` jours.
        btc = self.close[MARKET].ffill()
        return (btc > btc.rolling(days, min_periods=days).mean()).fillna(False)

    def regime_momentum(self, days: int) -> pd.Series:
        return (self.momentum(days)[MARKET] > 0).fillna(False)

    @cached_property
    def regime_hmm(self) -> pd.Series:
        btc = np.log(self.panel.close[MARKET].dropna())
        log_returns = btc.diff().dropna()
        on = pd.Series(False, index=self.close.index)
        params: HmmParams | None = None
        month = None
        for moment in self.decisions:
            x = log_returns[log_returns.index <= moment].to_numpy()
            if len(x) < HMM_MIN_RETURNS:
                continue
            if params is None or (moment.year, moment.month) != month:      # première décision du mois
                params, month = hmm_fit(x, params), (moment.year, moment.month)
            on.loc[moment] = bool(hmm_filter(x, params)[-1, params.calm] > 0.5)
        return on

    def exposure(self, volatility: pd.Series) -> pd.Series:
        """min(1, cible / volatilité) aux décisions ; cible = médiane des volatilités des décisions passées."""
        at_decisions = volatility.reindex(self.decisions)
        target = at_decisions.expanding(min_periods=VOL_MIN_DECISIONS).median()
        ratio = (target / at_decisions.where(at_decisions > 0)).clip(upper=1.0)
        return ratio.fillna(1.0)

    def weights(self, key: str) -> pd.DataFrame:
        """Poids aux décisions (lignes) par paire (colonnes) pour l'essai `key` ou une référence."""
        if key == EW:
            full = self.equal_weight
        elif key == BTC:                                          # acheté une fois, puis conservé
            out = pd.DataFrame(0.0, index=self.decisions[:1], columns=self.panel.symbols)
            out[MARKET] = 1.0
            return out
        elif key.startswith("MOM_"):
            _, lookback, k = key.split("_")
            full = self.top(self.momentum(int(lookback[1:])), int(k[1:]))
        elif key.startswith("REGIME_"):
            full = self.equal_weight.mul(self._regime(key.removeprefix("REGIME_")).astype(float), axis=0)
        elif key.startswith("DUAL_"):
            full = self.top(self.momentum(28), 5).mul(self._regime(key.removeprefix("DUAL_")).astype(float), axis=0)
        elif key.startswith("TSMOM_"):
            positive = (self.momentum(int(key.split("_L")[1])) > 0) & self.eligible
            count = self.eligible.sum(axis=1)
            full = positive.astype(float).div(count.where(count > 0), axis=0).fillna(0.0)
        elif key == "VOLMAN_EW":
            basket = self.returns.where(self.eligible).mean(axis=1)
            exposure = self.exposure(basket.rolling(VOL_DAYS, min_periods=VOL_DAYS).std())
            return self.equal_weight.reindex(self.decisions).mul(exposure, axis=0)
        elif key == "VOLMAN_BTC":
            exposure = self.exposure(self.returns[MARKET].rolling(VOL_DAYS, min_periods=VOL_DAYS).std())
            out = pd.DataFrame(0.0, index=self.decisions, columns=self.panel.symbols)
            out[MARKET] = exposure.to_numpy()
            return out
        elif key == "LOWVOL_K5":
            full = self.top(self.returns.rolling(56, min_periods=56).std(), 5, lowest=True)
        elif key == "REVERSAL_K5":
            full = self.top(self.momentum(7), 5, lowest=True)
        else:
            raise KeyError(key)
        return full.reindex(self.decisions).fillna(0.0)

    def _regime(self, name: str) -> pd.Series:
        if name == "HMM":
            return self.regime_hmm
        if name.startswith("SMA"):
            return self.regime_sma(int(name[3:]))
        if name.startswith("MOM"):
            return self.regime_momentum(int(name[3:]))
        raise KeyError(name)


TRIALS: tuple[Trial, ...] = (
    *(Trial(f"MOM_L{lookback}_K{k}", "H1 classement",
            f"les {k} paires éligibles au meilleur rendement sur {lookback} jours, à 1/{k} chacune")
      for lookback in (14, 28, 56) for k in (3, 5)),
    Trial("REGIME_SMA200", "H2 régime", "EW si BTC clôture au-dessus de sa moyenne 200 jours, sinon USDT"),
    Trial("REGIME_SMA100", "H2 régime", "EW si BTC clôture au-dessus de sa moyenne 100 jours, sinon USDT"),
    Trial("REGIME_HMM", "H2 régime", "EW si la probabilité filtrée de l'état calme (HMM 2 états sur BTC) dépasse 0,5"),
    Trial("REGIME_MOM28", "H2 régime", "EW si le rendement de BTC sur 28 jours est positif, sinon USDT"),
    Trial("DUAL_SMA200", "H3 double momentum", "les 5 meilleures sur 28 jours, seulement si BTC est au-dessus de sa moyenne 200 jours"),
    Trial("DUAL_MOM28", "H3 double momentum", "les 5 meilleures sur 28 jours, seulement si BTC monte sur 28 jours"),
    Trial("TSMOM_L28", "H4 tendance par paire", "chaque paire éligible reçoit 1/N si son rendement sur 28 jours est positif"),
    Trial("TSMOM_L56", "H4 tendance par paire", "chaque paire éligible reçoit 1/N si son rendement sur 56 jours est positif"),
    Trial("VOLMAN_EW", "H5 exposition selon la volatilité", "EW × min(1, cible / volatilité 28 jours du panier)"),
    Trial("VOLMAN_BTC", "H5 exposition selon la volatilité", "BTC × min(1, cible / volatilité 28 jours de BTC)", BTC),
    Trial("LOWVOL_K5", "H6 faible volatilité", "les 5 paires éligibles à la plus faible volatilité sur 56 jours"),
    Trial("REVERSAL_K5", "H7 retournement", "les 5 paires éligibles au plus faible rendement sur 7 jours"),
)
N_TRIALS = len(TRIALS)
# Bilatéral au niveau 1 − 0,025/18 (v2) : la relecture a mesuré que l'intervalle percentile par blocs laisse passer
# environ deux fois le taux nominal dans la queue ; on divise donc ce taux par deux.
LEVEL = 1 - 0.025 / N_TRIALS


# --- Simulation ----------------------------------------------------------------------------------------------

@dataclass
class Simulation:
    weekly: pd.Series            # rendement net de chaque semaine, indexé par l'instant de décision
    values: pd.Series            # valeur du portefeuille chaque jour à 01:00, avant rééquilibrage
    turnover: pd.Series          # montant échangé / valeur, à chaque rééquilibrage
    exposure: pd.Series          # part investie après chaque rééquilibrage
    skipped_orders: int = 0      # ordres voulus mais non passés faute de prix de 01:00 ce jour-là


def simulate(panel: Panel, weights: pd.DataFrame, decisions: pd.DatetimeIndex, *, cost: float,
             delay_days: int = 0) -> Simulation:
    """Portefeuille valorisé chaque jour au prix de 01:00 ; ordres exécutés à ce prix `delay_days` jours après la
    décision ; `cost` prélevé sur chaque montant échangé. Une paire sans prix ce jour-là n'est pas échangée."""
    symbols = panel.symbols
    delay = delay_days * DAY
    last_price_day = panel.price.dropna(how="all").index.max()
    while len(decisions) > 1 and decisions[-1] + 7 * DAY + delay > last_price_day:
        decisions = decisions[:-1]                                # semaine sans prix de fin (retard) : écartée
    checkpoints = decisions.append(pd.DatetimeIndex([decisions[-1] + 7 * DAY])) + delay
    days = panel.price.index[(panel.price.index >= checkpoints[0]) & (panel.price.index <= checkpoints[-1])]
    quoted = panel.price.reindex(days)
    filled = panel.price.ffill().reindex(days).to_numpy(float)
    tradable = quoted.notna().to_numpy()
    targets = {moment + delay: weights.loc[moment].reindex(symbols).fillna(0.0).to_numpy(float)
               for moment in weights.index}
    holdings, cash = np.zeros(len(symbols)), 1.0
    values, turnover, exposure, skipped = [], {}, {}, 0
    for i, day in enumerate(days):
        if i:
            ratio = np.where(np.isfinite(filled[i]) & np.isfinite(filled[i - 1]) & (filled[i - 1] > 0),
                             filled[i] / filled[i - 1], 1.0)
            holdings = holdings * ratio
        total = cash + holdings.sum()
        values.append(total)
        target = targets.get(day)
        if target is not None:
            can = tradable[i]
            skipped += int((~can & (np.abs(target * total - holdings) > 1e-9 * max(total, 1.0))).sum())
            locked = holdings[~can].sum()                           # positions sans prix : inchangées
            desired = np.where(can, target * total, holdings)

            def cap(room: float, desired: np.ndarray = desired, can: np.ndarray = can) -> None:
                """Jamais plus que ce qui est disponible hors positions bloquées : aucun levier."""
                wanted = desired[can].sum()
                if wanted > room:
                    desired[can] *= max(room, 0.0) / wanted

            cap(total - locked)
            traded = float(np.abs(desired - holdings)[can].sum())
            fee = traded * cost
            desired[can] *= 1 - fee / total if total > 0 else 1.0   # les frais réduisent chaque position d'autant
            cap(total - fee - locked)
            holdings = desired
            cash = total - fee - holdings.sum()
            turnover[day - delay] = traded / total if total > 0 else 0.0
            exposure[day - delay] = float(holdings.sum() / (total - fee)) if total - fee > 0 else 0.0
    series = pd.Series(values, index=days)
    at = series.reindex(checkpoints).to_numpy()
    weekly = pd.Series(at[1:] / at[:-1] - 1, index=decisions)
    return Simulation(weekly, series, pd.Series(turnover), pd.Series(exposure), skipped)


# --- Mesures ----------------------------------------------------------------------------------------------------

def sharpe(weekly: np.ndarray) -> float:
    weekly = np.asarray(weekly, dtype=float)
    if len(weekly) < 2:
        return 0.0
    std = float(np.std(weekly, ddof=1))
    return float(np.mean(weekly) / std * math.sqrt(52)) if std > MIN_STD else 0.0


def max_drawdown(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=float)
    return float((values / np.maximum.accumulate(values) - 1).min()) if len(values) else 0.0


def sharpe_diff_ci(strategy: np.ndarray, benchmark: np.ndarray, *, level: float, seed: int,
                   block: int = BLOCK_WEEKS, samples: int = BOOTSTRAP_SAMPLES) -> list[float] | None:
    """IC de l'écart de Sharpe par blocs circulaires de `block` semaines, les deux séries tirées ENSEMBLE."""
    a, b = np.asarray(strategy, dtype=float), np.asarray(benchmark, dtype=float)
    n = len(a)
    if n < 4 * block:
        return None
    rng = np.random.default_rng(seed)
    blocks = math.ceil(n / block)
    starts = rng.integers(0, n, size=(samples, blocks))
    index = ((starts[:, :, None] + np.arange(block)[None, None, :]) % n).reshape(samples, -1)[:, :n]

    def sharpes(x: np.ndarray) -> np.ndarray:
        drawn = x[index]
        std = drawn.std(axis=1, ddof=1)
        return np.where(std > MIN_STD, drawn.mean(axis=1) / np.where(std > MIN_STD, std, 1.0), 0.0) * math.sqrt(52)

    diff = sharpes(a) - sharpes(b)
    low, high = np.percentile(diff, [(1 - level) / 2 * 100, (1 + level) / 2 * 100])
    return [round(float(low), 4), round(float(high), 4)]


def fold_of(moments: pd.DatetimeIndex) -> np.ndarray:
    """Numéro de la validation (juillet → juin) de chaque instant."""
    return np.asarray(pd.DatetimeIndex(FOLD_STARTS).searchsorted(pd.DatetimeIndex(moments), side="right")) - 1


def judge(metrics: dict) -> dict:
    """Règle du §7 à partir des mesures (fonction pure, testée critère par critère)."""
    ci = metrics.get("sharpe_diff_ci")
    checks = {
        "ic_ecart_de_sharpe_positif": bool(ci is not None and ci[0] > 0),
        "stable_par_validation": bool((metrics.get("folds_better") or 0) >= MIN_FOLDS_BETTER),
        "positif_scenario_defavorable": bool((metrics.get("adverse_sharpe_diff") or 0.0) > 0),
        "perte_maximale_contenue": bool(metrics.get("max_drawdown") is not None
                                        and metrics.get("benchmark_max_drawdown") is not None
                                        and metrics["max_drawdown"] >= metrics["benchmark_max_drawdown"]),
        "positif_sans_meilleure_validation": bool((metrics.get("sharpe_diff_without_best_fold") or 0.0) > 0),
        "assez_investi": bool((metrics.get("invested_share") or 0.0) >= MIN_INVESTED_SHARE),
    }
    return {"lead": all(checks.values()), "checks": checks}


def describe(sim: Simulation) -> dict:
    weekly = sim.weekly.to_numpy(float)
    total = float(np.prod(1 + weekly) - 1)
    years = len(weekly) / 52
    return {"weeks": len(weekly), "total_return": round(total, 4),
            "annual_return": round(float((1 + total) ** (1 / years) - 1), 4) if years > 0 and total > -1 else None,
            "sharpe": round(sharpe(weekly), 4), "max_drawdown": round(max_drawdown(sim.values.to_numpy(float)), 4),
            "turnover_per_year": round(float(sim.turnover.sum() / years), 2) if years > 0 else None,
            "average_exposure": round(float(sim.exposure.mean()), 4) if len(sim.exposure) else 0.0}


def compare(sim: Simulation, bench: Simulation, adverse: Simulation, adverse_bench: Simulation, btc: Simulation,
            weights: pd.DataFrame, *, seed: int) -> dict:
    """Mesures d'un essai face à sa référence (§6) et jugement (§7)."""
    a, b = sim.weekly.to_numpy(float), bench.weekly.to_numpy(float)
    folds = fold_of(sim.weekly.index)
    by_fold = []
    for k in range(len(FOLD_STARTS)):
        pick = folds == k
        by_fold.append((round(sharpe(a[pick]), 4), round(sharpe(b[pick]), 4)) if pick.sum() >= 2 else (None, None))
    gaps = [s - r if s is not None and r is not None else -math.inf for s, r in by_fold]
    best = int(np.argmax(gaps))
    without = folds != best
    invested = weights.reindex(sim.weekly.index).fillna(0.0).sum(axis=1) >= MIN_EXPOSURE
    metrics = describe(sim) | {
        "benchmark_sharpe": round(sharpe(b), 4), "sharpe_diff": round(sharpe(a) - sharpe(b), 4),
        "sharpe_diff_ci": sharpe_diff_ci(a, b, level=LEVEL, seed=seed),
        "fold_sharpes": by_fold, "folds_better": int(sum(g > 0 for g in gaps if g != -math.inf)),
        "adverse_sharpe_diff": round(sharpe(adverse.weekly.to_numpy(float)) - sharpe(adverse_bench.weekly.to_numpy(float)), 4),
        "benchmark_max_drawdown": round(max_drawdown(bench.values.to_numpy(float)), 4),
        "sharpe_diff_without_best_fold": round(sharpe(a[without]) - sharpe(b[without]), 4),
        "invested_share": round(float(invested.mean()), 4),
        "sharpe_diff_vs_btc": round(sharpe(a) - sharpe(btc.weekly.to_numpy(float)), 4),
        "skipped_orders": sim.skipped_orders, "adverse_skipped_orders": adverse.skipped_orders,
    }
    return metrics | judge(metrics)


# --- Données, audit, exécution -------------------------------------------------------------------------------

def load_frames(settings: Settings, symbols: list[str], end: pd.Timestamp) -> dict[str, pd.DataFrame]:
    """Bougies 1 h du magasin long, coupées à la fin de DEVELOPMENT ; refus si une paire manque ou s'arrête tôt."""
    from ..features.loader import MissingData
    frames, problems = {}, []
    for symbol in symbols:
        try:
            frame = load_long(settings, symbol)
        except MissingData:
            problems.append(f"{symbol} : absente du magasin long")
            continue
        frame = frame[frame["open_time"] <= end].reset_index(drop=True)
        if frame.empty or frame["open_time"].max() < end - END_TOLERANCE:
            problems.append(f"{symbol} : s'arrête avant la fin de DEVELOPMENT")
            continue
        frames[symbol] = frame
    if problems:
        raise IncompleteData("données incomplètes : " + " ; ".join(problems))
    return frames


def leak_audit(frames: dict[str, pd.DataFrame], panel: Panel, decisions: pd.DatetimeIndex, *, seed: int) -> dict:
    """Poids de chaque essai à AUDIT_DECISIONS décisions : identiques avec les seules bougies antérieures à la décision,
    puis avec un futur falsifié ; la mutation (signal qui lit la clôture du lendemain) doit être détectée ; la
    simulation EW jusqu'à une décision ne change pas quand les prix d'exécution postérieurs sont falsifiés."""
    rng = np.random.default_rng(seed)
    keys = [EW, BTC, *(t.key for t in TRIALS)]
    picks = sorted(rng.choice(len(decisions), size=min(AUDIT_DECISIONS, len(decisions)), replace=False))
    full = Book(panel, decisions)
    reference = {key: full.weights(key) for key in keys}
    leaky = Book(panel, decisions, leaky=True).weights("MOM_L28_K5")       # MUTATION : lit la clôture du lendemain
    violations: list[dict] = []
    mutation_detected = False
    for pick in picks:
        moment = decisions[pick]
        past = decisions[decisions <= moment]

        def falsified(frame: pd.DataFrame, moment=moment) -> pd.DataFrame:
            frame = frame.copy()
            future = (frame["open_time"] >= moment).to_numpy()
            factor = rng.uniform(0.5, 1.5, int(future.sum()))
            for column in ("open", "high", "low", "close", "quote_volume"):
                frame.loc[future, column] = frame.loc[future, column].to_numpy(float) * factor
            return frame

        variants = {"tronqué": {s: f[f["open_time"] < moment] for s, f in frames.items()},
                    "futur falsifié": {s: falsified(f) for s, f in frames.items()}}
        for label, altered in variants.items():
            book = Book(build_panel({s: f for s, f in altered.items() if not f.empty}), past)
            for key in keys:
                got = book.weights(key)
                expected = reference[key]
                if key == BTC:                                    # une seule ligne (achat initial)
                    same = got.reindex(columns=expected.columns).fillna(0.0).equals(expected)
                else:
                    row = got.loc[moment].reindex(expected.columns).fillna(0.0).to_numpy()
                    same = bool(np.allclose(row, expected.loc[moment].to_numpy(), atol=1e-12))
                if not same:
                    violations.append({"decision": str(moment), "check": label, "trial": key})
            if label == "tronqué":
                # Le même contrôle, appliqué au signal muté, doit le prendre en défaut.
                mutated = Book(book.panel, past, leaky=True).weights("MOM_L28_K5")
                row = mutated.loc[moment].reindex(leaky.columns).fillna(0.0).to_numpy()
                mutation_detected |= not np.allclose(row, leaky.loc[moment].to_numpy(), atol=1e-12)
    # Simulation : valeurs jusqu'à la décision du milieu identiques quand les prix de 01:00 postérieurs changent.
    cut = decisions[len(decisions) // 2]
    weights = reference[EW]
    honest = simulate(panel, weights, decisions, cost=0.0013).values
    later = panel.price.index > cut
    price = panel.price.copy()
    price.loc[later] = price.loc[later].to_numpy() * rng.uniform(0.5, 1.5, price.loc[later].shape)
    shaken = simulate(Panel(panel.close, panel.volume, price), weights, decisions, cost=0.0013).values
    before = honest.index <= cut
    if not np.allclose(honest[before].to_numpy(), shaken.reindex(honest.index)[before].to_numpy(), atol=1e-12):
        violations.append({"decision": str(cut), "check": "simulation", "trial": EW})
    return {"violations": violations, "mutation_detected": bool(mutation_detected),
            "decisions": [str(decisions[p]) for p in picks], "trials_checked": len(keys),
            "passed": not violations and bool(mutation_detected)}


@dataclass
class Result:
    run_id: str
    period_end: str
    level: float
    n_trials: int = N_TRIALS
    program_trials: int = 0
    verdict: str = ""
    leak_audit: dict = field(default_factory=dict)
    data_hashes: dict = field(default_factory=dict)
    coverage: dict = field(default_factory=dict)
    benchmarks: dict = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)


def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None,
        progress: Callable[[str], None] | None = None, allow_dirty: bool = False) -> Result:
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise DirtyCode(f"code non commité ({state}) : exécution refusée, l'enregistrement ne prouverait pas quel code "
                        "a tourné. Exécuter depuis un arbre propre (git worktree), ou --allow-dirty pour un essai local.")
    symbols = symbols or list(RESEARCH_UNIVERSE)
    end = pd.Timestamp(development_end(settings))
    result = Result(new_run_id("FACT"), end.isoformat(), round(LEVEL, 6))
    say("données")
    frames = load_frames(settings, symbols, end)
    result.data_hashes = {symbol: fingerprint(frame) for symbol, frame in frames.items()}
    panel = build_panel(frames)
    decisions = decisions_of(panel)
    if len(decisions) < 4 * BLOCK_WEEKS:
        raise IncompleteData(f"trop peu de décisions ({len(decisions)})")
    say("audit des fuites")
    result.leak_audit = leak_audit(frames, panel, decisions, seed=settings.protocol.seed)
    if not result.leak_audit["passed"]:
        raise LeakAuditFailed(f"audit des fuites en échec : {result.leak_audit}")
    book = Book(panel, decisions)
    central, adverse = settings.costs["central"], settings.costs["adverse"]
    cost = (central.fee_bps + central.slippage_bps + central.half_spread_bps) / 1e4
    adverse_cost = (adverse.fee_bps + adverse.slippage_bps + adverse.half_spread_bps) / 1e4

    def both(key: str) -> tuple[pd.DataFrame, Simulation, Simulation]:
        weights = book.weights(key)
        return (weights, simulate(panel, weights, decisions, cost=cost),
                simulate(panel, weights, decisions, cost=adverse_cost, delay_days=1))

    say("références")
    references = {key: both(key) for key in (EW, BTC)}
    result.benchmarks = {key: describe(sim) | {"adverse": describe(adv)} for key, (_, sim, adv) in references.items()}
    eligible = book.eligible.reindex(decisions).sum(axis=1)
    folds = fold_of(decisions)
    result.coverage = {"decisions": len(decisions), "first": str(decisions[0]), "last": str(decisions[-1]),
                       "cost_per_side": cost, "adverse_cost_per_side": adverse_cost,
                       "decisions_without_eligible_pair": int((eligible == 0).sum()),
                       "skipped_orders_ew": references[EW][1].skipped_orders,
                       "eligible_pairs_by_fold": [
                           {"fold": k, "min": int(eligible[folds == k].min()), "median": float(eligible[folds == k].median()),
                            "max": int(eligible[folds == k].max())} for k in range(len(FOLD_STARTS)) if (folds == k).any()]}
    btc = references[BTC][1]
    for trial in TRIALS:
        say(trial.key)
        weights, sim, adv = both(trial.key)
        _, bench, adverse_bench = references[trial.benchmark]
        result.rows.append({"key": trial.key, "family": trial.family, "description": trial.description,
                            "benchmark": trial.benchmark}
                           | compare(sim, bench, adv, adverse_bench, btc, weights, seed=settings.protocol.seed))
    leads = [row["key"] for row in result.rows if row["lead"]]
    result.verdict = f"{len(leads)} PISTE(S) À CONFIRMER" if leads else "AUCUNE_PISTE"
    registry = ExperimentRegistry(settings.experiments_db)
    result.program_trials = registry.program_trials() + result.n_trials
    _record(settings, result, now=now, symbols=symbols, code=state)
    return result


def _record(settings: Settings, result: Result, *, now: datetime, symbols: list[str], code: str) -> None:
    report_dir = settings.reports_dir / result.run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = asdict(result) | {"protocol_version": PROTOCOL_VERSION, "doc": "docs/FACTORS.md"}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                                             encoding="utf-8")
    ExperimentRegistry(settings.experiments_db).record(
        run_id=result.run_id, created_at=now.isoformat(), kind=KIND,
        hypothesis="un portefeuille hebdomadaire à règle fixe (classement, régime, variantes) fait-il mieux, à "
                   "risque égal, que sa référence ?",
        strategy=STRATEGY, strategy_version=PROTOCOL_VERSION, variant="18 essais figés (docs/FACTORS.md)",
        params={"trials": [t.key for t in TRIALS], "level": result.level, "block_weeks": BLOCK_WEEKS,
                "bootstrap_samples": BOOTSTRAP_SAMPLES, "first_decision": str(FIRST_DECISION),
                "cost_per_side": result.coverage.get("cost_per_side"),
                "adverse_cost_per_side": result.coverage.get("adverse_cost_per_side")},
        period_label="DEVELOPMENT", period_start=str(FIRST_DECISION), period_end=result.period_end, universe=symbols,
        data_hashes=result.data_hashes, git_commit=code, dependencies=dependency_versions(),
        seed=settings.protocol.seed, cost_scenario="central ; défavorable avec un jour de retard",
        simulation_rules={"decision": "lundi 00:00 UTC", "execution": "ouverture de la bougie 1 h de 01:00 UTC",
                          "valuation": "quotidienne à 01:00", "leverage": "aucun"},
        metrics={"n_trials": result.n_trials, "program_trials": result.program_trials, "verdict": result.verdict,
                 "leads": [row["key"] for row in result.rows if row["lead"]], "benchmarks": result.benchmarks,
                 "rows": result.rows}, status="COMPLETED", report_dir=str(report_dir))
