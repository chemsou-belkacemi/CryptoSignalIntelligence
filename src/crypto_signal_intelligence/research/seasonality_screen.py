"""Criblage S : saisonnalité du SENS (point 6 du plan de travail ; docs/SCREENING.md, « Criblage S », déclaré le
2026-10-03 avant exécution). 8 conditions × 1 horizon chacune = 8 essais.

Bougies 1 h du magasin long, 40 paires de recherche, DEVELOPMENT, événements à partir du 2019-01-01. Un événement
achète à l'OUVERTURE de la bougie de l'heure T et vend à la CLÔTURE de la bougie qui finit à T + durée ; aucune
information de marché n'est lue (le calendrier seul décide, sauf S8 qui apprend sur l'année précédente). Excès =
rendement − rendement moyen de la paire sur la même durée à toutes les heures ; mesure des criblages D à K.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime

import numpy as np
import pandas as pd

from ..config import Settings
from . import factors as fa
from .derivatives_screen import fingerprint
from .experiments import ExperimentRegistry, dependency_versions, new_run_id
from .protocol import development_end
from .screen import _row
from .universe import RESEARCH_UNIVERSE

FIRST_DAY = pd.Timestamp("2019-01-01", tz="UTC")
#: condition → (description, durée en heures)
CONDITIONS: dict[str, tuple[str, int]] = {
    "S1_WEEKEND": ("achat samedi 00:00 UTC, vente dimanche 23:59 (48 h)", 48),
    "S2_LUNDI": ("achat lundi 00:00 UTC, 24 h", 24),
    "S3_TOURNANT_DU_MOIS": ("achat à 00:00 UTC deux jours avant la fin du mois, 72 h (passage au mois suivant)", 72),
    "S4_APRES_FINANCEMENT": ("achat à 00:00, 08:00 et 16:00 UTC (règlement du financement des perpétuels), 4 h", 4),
    "S5_EXPIRATION_OPTIONS": ("achat le dernier vendredi du mois à 08:00 UTC (expiration Deribit), 24 h", 24),
    "S6_OUVERTURE_USA": ("achat à 14:00 UTC les jours ouvrés (ouverture des marchés américains), 4 h", 4),
    "S7_SEANCE_ASIE": ("achat à 00:00 UTC, 8 h (séance asiatique)", 8),
    "S8_MEILLEURE_HEURE": ("achat à l'heure dont le rendement moyen sur 1 h a été le plus haut sur les 365 jours précédents, 1 h", 1),
}
N_TRIALS = len(CONDITIONS)
LEARN_DAYS, LEARN_MIN_DAYS = 365, 200


def hourly_panel(frames: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Ouvertures et clôtures 1 h (index : heure d'ouverture UTC sur une grille complète ; NaN si la bougie manque)."""
    opens, closes = {}, {}
    for symbol, h1 in frames.items():
        frame = h1.sort_values("open_time")
        index = pd.DatetimeIndex(pd.to_datetime(frame["open_time"], utc=True))
        opens[symbol] = pd.Series(frame["open"].to_numpy(float), index=index)
        closes[symbol] = pd.Series(frame["close"].to_numpy(float), index=index)
    grid = pd.date_range(min(s.index.min() for s in opens.values()), max(s.index.max() for s in opens.values()), freq="h")

    def table(series: dict[str, pd.Series]) -> pd.DataFrame:
        return pd.DataFrame({s: v[~v.index.duplicated()] for s, v in series.items()}).reindex(grid)

    return table(opens), table(closes)


def forward(opens: pd.DataFrame, closes: pd.DataFrame, hours: int) -> pd.DataFrame:
    """Rendement de l'ouverture de l'heure T à la clôture de la bougie qui finit à T + `hours`."""
    return closes.shift(-(hours - 1)) / opens - 1


def calendar_flags(index: pd.DatetimeIndex, name: str) -> np.ndarray:
    hour, dow = index.hour, index.dayofweek
    if name == "S1_WEEKEND":
        return (dow == 5) & (hour == 0)
    if name == "S2_LUNDI":
        return (dow == 0) & (hour == 0)
    if name == "S3_TOURNANT_DU_MOIS":
        last = (index + pd.offsets.MonthEnd(0)).normalize()
        return (hour == 0) & (index.normalize() == last - pd.Timedelta(days=1))
    if name == "S4_APRES_FINANCEMENT":
        return np.isin(hour, (0, 8, 16))
    if name == "S5_EXPIRATION_OPTIONS":
        last_friday = (index + pd.offsets.MonthEnd(0)).normalize()
        last_friday = last_friday - pd.to_timedelta((last_friday.dayofweek - 4) % 7, unit="D")
        return (hour == 8) & (index.normalize() == last_friday)
    if name == "S6_OUVERTURE_USA":
        return (hour == 14) & (dow < 5)
    if name == "S7_SEANCE_ASIE":
        return hour == 0
    raise ValueError(name)


def best_hour_flags(one_hour: pd.DataFrame) -> pd.DataFrame:
    """S8 : pour chaque paire et chaque journée D, l'heure de plus fort rendement moyen sur 1 h pendant les 365
    journées PRÉCÉDENTES (journées entières avant D, au moins 200) ; drapeau à cette heure-là de D."""
    flags = pd.DataFrame(False, index=one_hour.index, columns=one_hour.columns)
    days = one_hour.index.normalize()
    hours = one_hour.index.hour
    for symbol in one_hour.columns:
        series = one_hour[symbol]
        by_day_hour = series.groupby([days, hours]).mean().unstack()          # journée × heure
        trailing = by_day_hour.rolling(LEARN_DAYS, min_periods=min(LEARN_MIN_DAYS, LEARN_DAYS)).mean().shift(1)
        trailing = trailing[trailing.notna().any(axis=1)]                      # journées sans assez de passé : aucun drapeau
        best = trailing.idxmax(axis=1).astype(int)
        target = pd.Series(best.to_numpy(), index=pd.DatetimeIndex(best.index))
        chosen = target.reindex(days).to_numpy()
        flags[symbol] = (chosen == hours.to_numpy())
    return flags


def events(flags: pd.DataFrame | np.ndarray, fwd: pd.DataFrame) -> pd.DataFrame:
    mask = flags.to_numpy(bool) if isinstance(flags, pd.DataFrame) else np.repeat(np.asarray(flags)[:, None], fwd.shape[1], axis=1)
    drift = fwd.mean()
    parts = []
    for j, symbol in enumerate(fwd.columns):
        values = fwd[symbol].to_numpy(float)
        keep = mask[:, j] & np.isfinite(values) & (fwd.index >= FIRST_DAY)
        parts.append(pd.DataFrame({"time": fwd.index[keep], "symbol": symbol, "ret": values[keep], "excess": values[keep] - float(drift[symbol])}))
    frame = pd.concat([p for p in parts if not p.empty], ignore_index=True) if any(not p.empty for p in parts) \
        else pd.DataFrame(columns=["time", "symbol", "ret", "excess"])
    if not frame.empty:
        frame["time"] = pd.to_datetime(frame["time"], utc=True)
    return frame


def run(settings: Settings, *, now: datetime, symbols: list[str] | None = None, progress: Callable[[str], None] | None = None,
        allow_dirty: bool = False) -> dict:
    say = progress or (lambda _text: None)
    state = fa.code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise fa.DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    symbols = list(symbols or RESEARCH_UNIVERSE)
    end = pd.Timestamp(development_end(settings))
    costs = settings.costs["central"]
    hurdle_pct = (2 * costs.fee_bps + 2 * (costs.slippage_bps + costs.half_spread_bps)) / 100
    say("données")
    frames = fa.load_frames(settings, symbols, end)
    opens, closes = hourly_panel(frames)
    rows = []
    for name, (_text, hours) in CONDITIONS.items():
        say(name)
        fwd = forward(opens, closes, hours)
        flags = best_hour_flags(forward(opens, closes, 1)) if name == "S8_MEILLEURE_HEURE" else calendar_flags(fwd.index, name)
        rows.append(_row(name, hours, events(flags, fwd), hurdle_pct, settings))
    registry = ExperimentRegistry(settings.experiments_db)
    program = registry.program_trials() + N_TRIALS
    run_id = new_run_id("SCREEN")
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {"run_id": run_id, "n_trials": N_TRIALS, "program_trials": program, "cost_hurdle_pct": round(hurdle_pct, 4),
               "conditions": {k: v[0] for k, v in CONDITIONS.items()}, "rows": [asdict(r) for r in rows], "doc": "docs/SCREENING.md"}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    registry.record(run_id=run_id, created_at=now.isoformat(), kind="SCREEN",
                    hypothesis="criblage S : un moment du calendrier (week-end, lundi, tournant du mois, financement, expiration d'options, "
                               "séances) donne-t-il un rendement au-delà de la dérive et des coûts ?",
                    strategy="SCREEN_SEASONALITY_S", strategy_version=1, variant="conditions figées (docs/SCREENING.md, criblage S)",
                    params={"conditions": {k: list(v) for k, v in CONDITIONS.items()}, "learn_days": LEARN_DAYS},
                    period_label="DEVELOPMENT", period_start=str(FIRST_DAY)[:10], period_end=end.isoformat(), universe=symbols,
                    data_hashes={s: fingerprint(f) for s, f in frames.items()}, git_commit=state, dependencies=dependency_versions(),
                    seed=settings.protocol.seed, cost_scenario="central (seuil aller-retour)",
                    simulation_rules={"entry": "ouverture de l'heure T", "exit": "clôture de la bougie qui finit à T + durée"},
                    metrics={"n_trials": N_TRIALS, "program_trials": program, "rows": [asdict(r) for r in rows]}, status="COMPLETED",
                    report_dir=str(report_dir))
    return payload
