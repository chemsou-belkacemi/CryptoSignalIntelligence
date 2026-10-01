"""Univers de recherche étendu, FIGÉ le 2026-10-01 (docs/FACTORS.md §1, docs/VOLATILITY.md §1).

Deux origines, toutes deux datées :
- cryptos FAVORABLES au relevé halal du 2026-10-01 (au moins deux sources halal, aucune réserve :
  config/halal_screening.toml), avec une paire USDT négociable sur Binance Spot ;
- cryptos acceptées par le propriétaire le 2026-10-01 parmi les « à décider » (sa décision, tracée dans
  docs/UNIVERSE.md).

La liste est figée ici pour que les protocoles restent reproductibles : un nouveau relevé ou une nouvelle
décision ne change pas un protocole déjà déclaré. Biais à déclarer par chaque protocole : la liste est choisie
AUJOURD'HUI (survivantes) ; l'appartenance « à la date » (cotation, liquidité) ne corrige que la disponibilité.
"""
from __future__ import annotations

FAVORABLE_2026_10_01: tuple[str, ...] = (
    "ADA", "ALGO", "APT", "ARB", "ATOM", "AVAX", "BCH", "BTC", "DOT", "EGLD", "ETC", "ETH", "FIL", "HBAR", "IOTA",
    "LINK", "LTC", "NEAR", "NEO", "POL", "RENDER", "SOL", "SUI", "TAO", "THETA", "TRX", "VET", "XLM", "XRP", "XTZ",
)
OWNER_ACCEPTED_2026_10_01: tuple[str, ...] = ("DASH", "DOGE", "FET", "ICP", "OP", "QNT", "ROSE", "SEI", "TIA", "ZEC")
RESEARCH_UNIVERSE: tuple[str, ...] = tuple(
    f"{base}USDT" for base in sorted(FAVORABLE_2026_10_01 + OWNER_ACCEPTED_2026_10_01))
MARKET = "BTCUSDT"
