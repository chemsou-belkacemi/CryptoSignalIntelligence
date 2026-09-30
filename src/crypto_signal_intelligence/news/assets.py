"""Actifs mentionnés dans un titre ou un résumé : règles lexicales explicites, sans modèle.

Un ticker court et ambigu (NEAR, DOT, ATOM…) n'est reconnu qu'en MAJUSCULES comme mot entier ou
entre parenthèses ; les noms complets sont reconnus sans tenir compte de la casse. Une détection
n'est qu'une étiquette de tri : elle ne prouve pas que l'article concerne réellement l'actif.
"""
from __future__ import annotations

import re

NAMES: dict[str, tuple[str, ...]] = {
    "BTC": ("bitcoin",), "ETH": ("ethereum", "ether"), "SOL": ("solana",), "XRP": ("ripple",),
    "NEAR": ("near protocol",), "AVAX": ("avalanche",), "HBAR": ("hedera",), "LINK": ("chainlink",),
    "XLM": ("stellar",), "ADA": ("cardano",), "TRX": ("tron",), "FIL": ("filecoin",), "ALGO": ("algorand",),
    "DOT": ("polkadot",), "ATOM": ("cosmos hub", "cosmos network"), "ETC": ("ethereum classic",),
}
_PAREN = re.compile(r"\(([A-Z0-9]{2,10})\)")


def base_assets(symbols: list[str]) -> list[str]:
    return [s.removesuffix("USDT").removesuffix("USDC") for s in symbols]


def detect(text: str, universe: list[str]) -> list[str]:
    found: set[str] = set()
    lowered = text.lower()
    for asset in universe:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(asset)}(?![A-Za-z0-9])", text):
            found.add(asset)
        # « Ethereum Classic » ne désigne pas ETH.
        haystack = lowered.replace("ethereum classic", " ") if asset == "ETH" else lowered
        for name in NAMES.get(asset, ()):
            if re.search(rf"\b{re.escape(name)}\b", haystack):
                found.add(asset)
    found |= {t for t in _PAREN.findall(text) if t in universe}
    return sorted(found)
