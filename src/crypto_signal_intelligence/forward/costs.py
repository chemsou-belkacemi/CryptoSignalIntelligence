"""Modèle de frais UNIQUE des tests en direct (règle 7 de la mission du 2026-10-02).

Par ordre et par côté : frais de 0,075 % (réduction BNB, permise par le cadre) plus écart et glissement de
0,02 % pour BTC et ETH, 0,05 % pour les autres paires. Scénario défavorable : frais de 0,10 % (sans BNB),
écart et glissement doublés. Un ordre limite exécuté (maker) ne paie pas l'écart ni le glissement à l'entrée ;
au palier de base de Binance Spot, il paie les MÊMES frais que l'ordre au marché.
"""
from __future__ import annotations

from dataclasses import dataclass

CENTRAL, ADVERSE = "central", "defavorable"
SCENARIOS = (CENTRAL, ADVERSE)
MAJORS = frozenset({"BTCUSDT", "ETHUSDT"})
FEE = {CENTRAL: 0.00075, ADVERSE: 0.0010}
MARKET_MAJOR, MARKET_OTHER = 0.0002, 0.0005
MARKET_FACTOR = {CENTRAL: 1.0, ADVERSE: 2.0}


@dataclass(frozen=True)
class Costs:
    fee: float          # frais par ordre, en fraction du montant
    market: float       # écart + glissement par côté, en fraction du prix (ordre au marché seulement)


def costs_for(symbol: str, scenario: str) -> Costs:
    if scenario not in SCENARIOS:
        raise ValueError(f"scénario de coûts inconnu : {scenario}")
    market = MARKET_MAJOR if symbol.upper() in MAJORS else MARKET_OTHER
    return Costs(fee=FEE[scenario], market=market * MARKET_FACTOR[scenario])


def describe() -> dict:
    """Règles de coût telles qu'enregistrées au démarrage d'un test (empreinte des paramètres)."""
    return {"fee": FEE, "market": {"BTCUSDT/ETHUSDT": MARKET_MAJOR, "autres": MARKET_OTHER},
            "market_factor": MARKET_FACTOR}
