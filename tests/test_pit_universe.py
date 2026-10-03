"""Univers à date (research/pit_universe.py) : recensement avec exclusions, appartenance mensuelle causale, masque,
couverture. SYNTHÉTIQUE."""
from __future__ import annotations

import numpy as np
import pandas as pd

from crypto_signal_intelligence.research import pit_universe as pit

SCREEN = {"structurelles": {"stablecoins": {"bases": ["USDC", "FDUSD"]}, "levier": {"motif": "^(BTC|ETH)(UP|DOWN)$"}}}


def test_census_excludes_structural_and_haram_and_keeps_delisted():
    info = {"symbols": [
        {"symbol": "BTCUSDT", "baseAsset": "BTC", "quoteAsset": "USDT", "status": "TRADING"},
        {"symbol": "EOSUSDT", "baseAsset": "EOS", "quoteAsset": "USDT", "status": "BREAK"},
        {"symbol": "USDCUSDT", "baseAsset": "USDC", "quoteAsset": "USDT", "status": "TRADING"},
        {"symbol": "BTCUPUSDT", "baseAsset": "BTCUP", "quoteAsset": "USDT", "status": "BREAK"},
        {"symbol": "XYZUSDT", "baseAsset": "XYZ", "quoteAsset": "USDT", "status": "TRADING"},
        {"symbol": "BUSDUSDT", "baseAsset": "BUSD", "quoteAsset": "USDT", "status": "BREAK"},
        {"symbol": "BULLUSDT", "baseAsset": "BULL", "quoteAsset": "USDT", "status": "BREAK"},
        {"symbol": "ETHBTC", "baseAsset": "ETH", "quoteAsset": "BTC", "status": "TRADING"}]}
    table = pit.census(info, SCREEN, {"BTC": "FAVORABLE", "XYZ": "DEFAVORABLE"}).set_index("symbol")
    assert set(table.index) == {"BTCUSDT", "EOSUSDT", "USDCUSDT", "BTCUPUSDT", "XYZUSDT", "BUSDUSDT", "BULLUSDT"}
    assert "stablecoin" in table.loc["BUSDUSDT", "excluded"] and "levier" in table.loc["BULLUSDT", "excluded"]
    assert pd.isna(table.loc["EOSUSDT", "excluded"]) and table.loc["EOSUSDT", "halal_status"] == "NON_RELEVE"
    assert table.loc["USDCUSDT", "excluded"] == "stablecoins" and table.loc["BTCUPUSDT", "excluded"] == "levier"
    assert "haram" in table.loc["XYZUSDT", "excluded"]


def daily(symbols: dict[str, float], start: str = "2018-01-01", days: int = 120) -> pd.DataFrame:
    index = pd.date_range(start, periods=days, freq="D", tz="UTC")
    return pd.concat([pd.DataFrame({"day": index, "symbol": s, "close": 1.0, "quote_volume": v}) for s, v in symbols.items()],
                     ignore_index=True)


def test_membership_uses_only_the_previous_30_days():
    data = daily({"A": 100.0, "B": 50.0, "C": 10.0})
    data.loc[(data["symbol"] == "C") & (data["day"] >= "2018-03-01"), "quote_volume"] = 1000.0   # C devient la plus liquide en mars
    members = pit.membership(data, top_n=2, first_month=pd.Timestamp("2018-02-01", tz="UTC"))
    feb = members[members["month"] == pd.Timestamp("2018-02-01", tz="UTC")]
    mar = members[members["month"] == pd.Timestamp("2018-03-01", tz="UTC")]
    apr = members[members["month"] == pd.Timestamp("2018-04-01", tz="UTC")]
    assert list(feb["symbol"]) == ["A", "B"] and list(mar["symbol"]) == ["A", "B"]   # mars ne voit pas encore mars
    assert list(apr["symbol"]) == ["C", "A"]
    short = daily({"D": 1e9}, start="2018-01-20", days=40)                            # moins de 30 jours avant février
    both = pit.membership(pd.concat([data, short]), top_n=3, first_month=pd.Timestamp("2018-02-01", tz="UTC"))
    assert both[both["month"] == pd.Timestamp("2018-02-01", tz="UTC")]["symbol"].tolist() == ["A", "B", "C"]


def test_member_mask_and_coverage():
    members = pd.DataFrame({"month": pd.to_datetime(["2018-02-01", "2018-02-01", "2018-03-01"], utc=True),
                            "symbol": ["BTCUSDT", "EOSUSDT", "XYZUSDT"], "rank": [1, 2, 1], "median_quote_volume": [1.0, 1.0, 1.0]})
    index = pd.date_range("2018-02-27", periods=4, freq="D", tz="UTC")
    mask = pit.member_mask(members, index, ["BTCUSDT", "EOSUSDT", "XYZUSDT"])
    assert mask.loc["2018-02-28"].tolist() == [True, True, False] and mask.loc["2018-03-01"].tolist() == [False, False, True]
    census = pd.DataFrame({"symbol": ["BTCUSDT", "EOSUSDT", "XYZUSDT"], "status": ["TRADING", "BREAK", "TRADING"]})
    cov = pit.coverage(members, census)
    assert cov["shares"] == {"univers_de_recherche": round(1 / 3, 4), "retiree_ou_renommee": round(1 / 3, 4), "cotee_hors_univers": round(1 / 3, 4)}
    assert cov["extra_symbols"] == ["EOSUSDT", "XYZUSDT"] and cov["extra_delisted"] == ["EOSUSDT"]
    assert np.isclose(sum(cov["shares"].values()), 1.0, atol=1e-3)
