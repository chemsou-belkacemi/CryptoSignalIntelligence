"""Univers « à date » (point 2 du plan de travail, docs/UNIVERSE_PIT.md, déclaré le 2026-10-03 avant exécution).

L'univers de recherche (40 paires) a été choisi en 2026 parmi les paires ENCORE cotées : les cryptos retirées de la
cote, souvent celles qui ont le plus mal fini, manquent à tous nos backtests (biais de survivance). Ce module :
1. recense toutes les paires USDT de Binance Spot, cotées (TRADING) et retirées ou renommées (BREAK), moins les
   exclusions structurelles du cadre (stablecoins, monnaies, tokens adossés, à levier) et les cryptos jugées haram
   par au moins une source du relevé halal ;
2. lit leurs bougies JOURNALIÈRES par l'API publique (klines, liste blanche), retirées comprises ;
3. définit, au 1er de chaque mois, les 40 paires les plus liquides (médiane du volume en USDT des 30 journées
   précédentes, au moins 30 journées), avec les seules données connues à cette date ;
4. télécharge l'historique 1 h (archives publiques, magasin long) des paires entrées un jour dans ce top 40 et
   absentes de l'univers de recherche.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from ..config import Settings
from ..data.http import PublicHttpClient
from .universe import RESEARCH_UNIVERSE

TOP_N, WINDOW_DAYS, MIN_DAYS = 40, 30, 30
FIRST_MONTH = pd.Timestamp("2018-01-01", tz="UTC")
PIT_DIR = "pit"


def pit_dir(settings: Settings) -> Path:
    return settings.data_dir / PIT_DIR


# --- 1. Recensement ------------------------------------------------------------------------------------------------

def census(exchange_info: dict, screen: dict, statuses: dict[str, str]) -> pd.DataFrame:
    """Paires USDT de Binance Spot avec leur statut, et la raison d'exclusion éventuelle (structurelle ou haram).
    `screen` : config/halal_screen.yaml ; `statuses` : statut CSI de chaque crypto du relevé halal."""
    from ..forward.halal import structural_reason
    rows = []
    for item in exchange_info.get("symbols", []):
        if item.get("quoteAsset") != "USDT":
            continue
        base = str(item["baseAsset"]).upper()
        reason = structural_reason(base, screen)
        if reason is None and statuses.get(base) == "DEFAVORABLE":
            reason = "haram pour au moins une source du relevé halal"
        rows.append({"symbol": item["symbol"], "base": base, "status": item.get("status"),
                     "halal_status": statuses.get(base, "NON_RELEVE"), "excluded": reason})
    return pd.DataFrame(rows).sort_values("symbol").reset_index(drop=True)


# --- 2. Bougies journalières -----------------------------------------------------------------------------------------

def daily_klines(client: PublicHttpClient, symbol: str, *, end: pd.Timestamp) -> pd.DataFrame:
    """Toutes les bougies journalières d'une paire (pages de 1 000), jusqu'à `end` exclu."""
    rows: list[list] = []
    start = 0
    while True:
        page = client.get_json("/api/v3/klines", {"symbol": symbol, "interval": "1d", "startTime": start, "limit": 1000})
        if not page:
            break
        rows += page
        start = int(page[-1][0]) + 86_400_000
        if len(page) < 1000 or start >= end.timestamp() * 1000:
            break
    frame = pd.DataFrame([[r[0], r[4], r[7]] for r in rows], columns=["open_ms", "close", "quote_volume"])
    frame["day"] = pd.to_datetime(frame["open_ms"], unit="ms", utc=True)
    frame = frame[frame["day"] < end]
    return pd.DataFrame({"day": frame["day"], "symbol": symbol, "close": frame["close"].astype(float),
                         "quote_volume": frame["quote_volume"].astype(float)})


def collect_daily(settings: Settings, symbols: list[str], *, end: pd.Timestamp, client: PublicHttpClient | None = None,
                  progress: Callable[[str], None] | None = None) -> pd.DataFrame:
    say = progress or (lambda _text: None)
    rest = client or PublicHttpClient.rest(settings.data.rest_base_url)
    parts = []
    for i, symbol in enumerate(symbols):
        if i % 50 == 0:
            say(f"bougies journalières {i}/{len(symbols)}")
        try:
            parts.append(daily_klines(rest, symbol, end=end))
        except Exception as exc:  # noqa: BLE001 - une paire illisible n'arrête pas les autres
            say(f"{symbol} : {type(exc).__name__}")
    return pd.concat([p for p in parts if not p.empty], ignore_index=True) if parts else pd.DataFrame(columns=["day", "symbol", "close", "quote_volume"])


# --- 3. Appartenance à date ------------------------------------------------------------------------------------------------

def membership(daily: pd.DataFrame, *, top_n: int = TOP_N, window: int = WINDOW_DAYS, min_days: int = MIN_DAYS,
               first_month: pd.Timestamp = FIRST_MONTH) -> pd.DataFrame:
    """Au 1er de chaque mois M : les `top_n` paires de plus forte médiane de volume en USDT sur les `window`
    journées qui FINISSENT la veille de M (au moins `min_days` journées avec volume). Rien de postérieur à M n'est lu."""
    volume = daily.pivot_table(index="day", columns="symbol", values="quote_volume").sort_index()
    rows = []
    last = volume.index.max()
    for month in pd.date_range(first_month, last, freq="MS"):
        window_data = volume[(volume.index < month) & (volume.index >= month - pd.Timedelta(days=window))]
        counts = window_data.notna().sum()
        median = window_data.median()[counts >= min_days].dropna()
        for rank, (symbol, value) in enumerate(median.sort_values(ascending=False).head(top_n).items(), start=1):
            rows.append({"month": month, "symbol": symbol, "rank": rank, "median_quote_volume": float(value)})
    return pd.DataFrame(rows)


def member_mask(members: pd.DataFrame, index: pd.DatetimeIndex, symbols: list[str]) -> pd.DataFrame:
    """Booléens (jour × paire) : la paire appartient au top du mois du jour."""
    mask = pd.DataFrame(False, index=index, columns=symbols)
    months = index.to_period("M") if index.tz is None else index.tz_convert(None).to_period("M")
    by_month = members.groupby(members["month"].dt.tz_convert(None).dt.to_period("M"))["symbol"].apply(set).to_dict()
    for i, period in enumerate(months):
        chosen = by_month.get(period, set())
        if chosen:
            mask.iloc[i] = [s in chosen for s in symbols]
    return mask


def coverage(members: pd.DataFrame, census_table: pd.DataFrame) -> dict:
    """Part des (mois, place) du top tenues par l'univers de recherche, par une paire cotée hors univers, par une
    paire retirée ou renommée ; paires hors univers entrées au moins une fois."""
    status = census_table.set_index("symbol")["status"].to_dict()
    research = set(RESEARCH_UNIVERSE)
    kind = members["symbol"].map(lambda s: "univers_de_recherche" if s in research else
                                 ("cotee_hors_univers" if status.get(s) == "TRADING" else "retiree_ou_renommee"))
    shares = kind.value_counts(normalize=True).round(4).to_dict()
    by_year = members.assign(kind=kind).groupby([members["month"].dt.year, "kind"]).size().unstack(fill_value=0)
    by_year = (by_year.div(by_year.sum(axis=1), axis=0).round(3)).to_dict(orient="index")
    extra = sorted(set(members["symbol"]) - research)
    return {"member_months": int(len(members)), "shares": shares, "by_year": {str(k): v for k, v in by_year.items()},
            "extra_symbols": extra, "extra_delisted": [s for s in extra if status.get(s) != "TRADING"]}


# --- Exécution -----------------------------------------------------------------------------------------------------------

def build(settings: Settings, *, now: datetime | None = None, end: pd.Timestamp | None = None,
          progress: Callable[[str], None] | None = None, client: PublicHttpClient | None = None,
          download_hourly: bool = True) -> dict:
    """Recensement, bougies journalières, appartenance mensuelle et téléchargement de l'historique 1 h des paires
    hors univers. `end` : fin de lecture des données (par défaut la fin de DEVELOPMENT ; la période finale n'est
    pas lue)."""
    import yaml

    from ..config import config_file
    from ..external.admission import load_screening
    from ..research.protocol import development_end
    from .long_history import download_long
    say = progress or (lambda _text: None)
    now = now or datetime.now(UTC)
    end = end or pd.Timestamp(development_end(settings)).ceil("D")
    rest = client or PublicHttpClient.rest(settings.data.rest_base_url)
    screen = yaml.safe_load((config_file().parent / "halal_screen.yaml").read_text(encoding="utf-8")) or {}
    statuses = {base: verdict.status for base, verdict in load_screening(settings)[0].items()}
    say("recensement")
    table = census(rest.get_json("/api/v3/exchangeInfo", {}), screen, statuses)
    kept = table[table["excluded"].isna()]["symbol"].tolist()
    daily = collect_daily(settings, kept, end=end, client=rest, progress=say)
    members = membership(daily)
    out_dir = pit_dir(settings)
    out_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_dir / "census.csv", index=False)
    daily.to_parquet(out_dir / "daily.parquet", index=False)
    members.to_parquet(out_dir / "membership.parquet", index=False)
    report = {"built_at": now.isoformat(), "end": str(end), "symbols_listed": int(len(table)), "symbols_kept": int(len(kept)),
              "excluded": table["excluded"].dropna().value_counts().to_dict(), "coverage": coverage(members, table)}
    if download_hourly:
        extra = report["coverage"]["extra_symbols"]
        say(f"historique 1 h de {len(extra)} paires hors univers")
        report["hourly_downloads"] = download_long(settings, extra, now=now, progress=say, rest_client=rest)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return report


def load_membership(settings: Settings) -> pd.DataFrame:
    path = pit_dir(settings) / "membership.parquet"
    if not path.exists():
        raise FileNotFoundError(f"appartenance à date absente : {path} (lancer `csi pit-universe`)")
    return pd.read_parquet(path)
