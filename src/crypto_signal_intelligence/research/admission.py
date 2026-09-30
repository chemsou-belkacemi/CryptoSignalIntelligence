"""Critères d'admission de docs/PROTOCOL.md, évalués sur l'agrégat HORS ÉCHANTILLON.

Déclarés avant les tests. VALIDATED_OOS seulement si tous sont vrais ; sinon
REJECTED si E[R] nette <= 0 en coûts centraux, INCONCLUSIVE dans les autres cas.
Le verdict ne modifie pas la configuration : toute promotion reste explicite.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from ..backtest.metrics import CLOSED
from ..config import AdmissionSection
from ..domain.enums import ValidationVerdict


@dataclass(frozen=True)
class Criterion:
    number: int
    label: str
    passed: bool
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def closed_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "exit_reason" not in trades:
        return trades.iloc[0:0]
    filled = trades["entry_status"].isin(["FILLED_OPEN", "FILLED_TOUCH"])
    return trades[filled & trades["exit_reason"].isin(CLOSED)]


def pnl_shares(closed: pd.DataFrame, column: str) -> pd.Series:
    """Part du PnL total en R portée par chaque groupe (total supposé > 0)."""
    return closed.groupby(column)["r_multiple"].sum() / closed["r_multiple"].sum()


def evaluate(*, central: dict, adverse: dict, central_trades: pd.DataFrame, windows_with_trades: int,
             ablations: dict[str, dict], integrity: tuple[bool, str],
             rules: AdmissionSection) -> tuple[ValidationVerdict, list[Criterion]]:
    expectancy = central.get("expectancy_r")
    ci = central.get("expectancy_r_ci95_block_bootstrap")
    closed_count = central.get("trades_closed", 0)
    criteria = [Criterion(1, "Données intègres, causalité vérifiée", integrity[0], integrity[1])]

    criteria.append(Criterion(
        2, f">= {rules.min_closed_trades} trades clos, >= {rules.min_windows_with_trades} fenêtres avec trades",
        closed_count >= rules.min_closed_trades and windows_with_trades >= rules.min_windows_with_trades,
        f"{closed_count} trades clos, {windows_with_trades} fenêtre(s) avec trades"))

    criteria.append(Criterion(
        3, "E[R] > 0 en coûts centraux et borne basse de l'IC95 > 0",
        expectancy is not None and expectancy > 0 and ci is not None and ci[0] > 0,
        f"E[R] {expectancy}, IC95 {ci}"))

    adverse_expectancy = adverse.get("expectancy_r")
    criteria.append(Criterion(4, "E[R] > 0 en coûts défavorables",
                              adverse_expectancy is not None and adverse_expectancy > 0, f"E[R] {adverse_expectancy}"))

    closed = closed_trades(central_trades)
    if closed.empty or closed["r_multiple"].sum() <= 0:
        criteria.append(Criterion(5, f"Aucune paire ni année > {rules.max_group_pnl_share:.0%} du PnL en R",
                                  False, "PnL total en R <= 0 : critère non satisfait"))
    else:
        grouped = closed.assign(year=pd.to_datetime(closed["entry_time"], utc=True).dt.year)
        worst = {column: pnl_shares(grouped, column) for column in ("symbol", "year")}
        detail = ", ".join(f"{column} max {shares.idxmax()} = {shares.max():.0%}" for column, shares in worst.items())
        criteria.append(Criterion(5, f"Aucune paire ni année > {rules.max_group_pnl_share:.0%} du PnL en R",
                                  all(s.max() <= rules.max_group_pnl_share for s in worst.values()), detail))

    drawdown = central.get("max_drawdown_r_closed_trades")
    criteria.append(Criterion(6, f"Drawdown sur trades clos < {rules.max_drawdown_r:g} R",
                              drawdown is not None and -drawdown < rules.max_drawdown_r, f"DD {drawdown} R"))

    label = "Les filtres ne font pas moins bien que la règle sans filtre"
    if expectancy is None:
        criteria.append(Criterion(7, label, False, "aucun trade clos pour la version de base"))
    elif not ablations:
        criteria.append(Criterion(7, label, True, "aucune ablation déclarée"))
    else:
        others = {name: summary.get("expectancy_r") for name, summary in ablations.items()}
        # Un filtre dont le retrait AMÉLIORE l'E[R] hors échantillon n'a pas d'apport démontré : le critère
        # échoue. Le retirer puis relancer sur ces mêmes fenêtres serait choisir la règle APRÈS avoir vu le
        # hors-échantillon : la version sans filtre est une nouvelle hypothèse, à juger sur des données non vues.
        worse = [name for name, other in others.items() if other is not None and other > expectancy]
        detail = f"base {expectancy} vs " + ", ".join(f"{name} {other}" for name, other in others.items())
        criteria.append(Criterion(7, label, not worse, detail + (
            f" ; sans apport démontré : {', '.join(worse)} (les retirer = nouvelle hypothèse, "
            "à tester sur une période non vue, pas sur ces fenêtres)" if worse else "")))

    if all(c.passed for c in criteria):
        verdict = ValidationVerdict.VALIDATED_OOS
    elif expectancy is not None and expectancy <= 0:
        verdict = ValidationVerdict.REJECTED
    else:
        verdict = ValidationVerdict.INCONCLUSIVE
    return verdict, criteria
