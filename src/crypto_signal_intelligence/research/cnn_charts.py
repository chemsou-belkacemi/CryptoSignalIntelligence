"""CNN sur images de graphiques (docs/CNN.md, déclaré le 2026-10-03 avant exécution ; mission, phase 10.2).

Images de 20 journées (64 × 60 pixels, prix + volume, sans indicateur) des paires du top 40 à date ; cible : la
semaine suivante (clôture du lundi → lundi suivant) au-dessus de la moyenne de l'univers ; modèle fixe inspiré de
Jiang, Kelly et Xiu (2023), entraîné sur 2019-2022, mesuré de 2023-01-30 à la fin de DEVELOPMENT ; stratégie : les 8
plus fortes probabilités chaque lundi contre le panier à poids égaux. **Un essai.** Torch n'est importé que dans les
fonctions d'entraînement (extra `cnn`, hors de l'image Docker et du CI).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from ..config import Settings
from ..features.loader import MissingData
from .experiments import ExperimentRegistry, code_state, dependency_versions, new_run_id
from .factors import DirtyCode
from .protocol import development_end
from .xsection import COST_PER_SIDE, describe, holding_returns, portfolio_series

KIND = "CNN_CHARTS"
STRATEGY = "CNN_CHARTS_TOP8"
N_TRIALS = 1
DAYS, HEIGHT, WIDTH = 20, 64, 60
PRICE_ROWS, VOLUME_ROWS = 51, 12                       # une ligne vide entre les deux
FIRST_WEEK = pd.Timestamp("2019-01-07", tz="UTC")      # comme XSECTION.md
TRAIN_TARGET_END = pd.Timestamp("2022-12-26", tz="UTC")  # lundi de clôture de la dernière cible d'entraînement
FIRST_VALIDATION = pd.Timestamp("2023-01-30", tz="UTC")
STOP_SHARE, PICK = 0.30, 8
SEED = 20261003
LEARNING_RATE, BATCH, MAX_EPOCHS, PATIENCE = 1e-5, 128, 50, 2
PASS, FAIL = "PASSE", "NE_PASSE_PAS"


# --- données ----------------------------------------------------------------------------------------------------

def daily_bars(hourly: pd.DataFrame, *, end: pd.Timestamp) -> pd.DataFrame:
    """Journées UTC complètes (24 bougies 1 h) agrégées : ouverture, plus haut, plus bas, clôture, volume en USDT.
    Aucune bougie ouverte après `end` n'est lue. Index : la journée (00:00 UTC)."""
    times = pd.to_datetime(hourly["open_time"], utc=True)
    frame = hourly.loc[(times <= end).to_numpy(), ["open", "high", "low", "close", "quote_volume"]].astype(float)
    frame.index = pd.DatetimeIndex(times[times <= end]).floor("D")
    grouped = frame.groupby(level=0)
    out = pd.DataFrame({"open": grouped["open"].first(), "high": grouped["high"].max(), "low": grouped["low"].min(),
                        "close": grouped["close"].last(), "volume": grouped["quote_volume"].sum()})
    return out[(grouped.size() == 24).to_numpy()]


def image_days(days: pd.DataFrame, monday: pd.Timestamp) -> pd.DataFrame | None:
    """Les 20 journées qui finissent le dimanche précédant `monday` (inclus), ou None s'il en manque une."""
    first, sunday = monday - pd.Timedelta(days=DAYS), monday - pd.Timedelta(days=1)
    window = days.loc[first:sunday]
    if len(window) != DAYS or window.index[-1] != sunday:
        return None
    return window


def chart_image(window: pd.DataFrame) -> np.ndarray:
    """Image 64 × 60 (0 ou 1, ligne 0 en haut) : 3 colonnes par journée (ouverture, barre bas–haut, clôture), prix sur
    les 51 lignes du haut à l'échelle du plus bas et du plus haut des 20 journées, volume sur les 12 du bas."""
    image = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
    o, h, lo, c, v = (window[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    floor, ceiling = float(lo.min()), float(h.max())
    span = ceiling - floor

    def row(price: np.ndarray) -> np.ndarray:
        if span <= 0:
            return np.full(len(price), PRICE_ROWS // 2)
        return (PRICE_ROWS - 1) - np.rint((price - floor) / span * (PRICE_ROWS - 1)).astype(int)

    top, bottom, r_open, r_close = row(h), row(lo), row(o), row(c)
    v_max = float(v.max())
    bars = np.rint(v / v_max * VOLUME_ROWS).astype(int) if v_max > 0 else np.zeros(DAYS, dtype=int)
    for day in range(DAYS):
        x = 3 * day
        image[r_open[day], x] = 1
        image[top[day]:bottom[day] + 1, x + 1] = 1
        image[r_close[day], x + 2] = 1
        if bars[day]:
            image[HEIGHT - bars[day]:, x + 1] = 1
    return image


def build_samples(days_by_symbol: dict[str, pd.DataFrame], members: dict[pd.Period, set[str]],
                  mondays: pd.DatetimeIndex, returns: pd.DataFrame) -> tuple[pd.DataFrame, np.ndarray]:
    """Une ligne par (lundi, paire du top 40 du mois avec 20 journées complètes) : rendement de la semaine suivante,
    cible (au-dessus de la moyenne des paires imagées de la semaine) ; images dans le même ordre."""
    rows, images = [], []
    for monday in mondays:
        universe = sorted(s for s in members.get(monday.tz_convert(None).to_period("M"), set()) if s in days_by_symbol)
        for symbol in universe:
            window = image_days(days_by_symbol[symbol], monday)
            if window is None:
                continue
            ret = returns.at[monday, symbol] if (monday in returns.index and symbol in returns.columns) else np.nan
            rows.append({"monday": monday, "symbol": symbol, "ret": float(ret), "last_day": window.index[-1]})
            images.append(chart_image(window))
    table = pd.DataFrame(rows, columns=["monday", "symbol", "ret", "last_day"])
    mean = table.groupby("monday")["ret"].transform("mean")
    table["label"] = np.where(table["ret"].notna(), (table["ret"] > mean).astype(float), np.nan)
    stack = np.stack(images) if images else np.zeros((0, HEIGHT, WIDTH), dtype=np.uint8)
    return table, stack


def split(table: pd.DataFrame, end: pd.Timestamp) -> tuple[np.ndarray, np.ndarray]:
    """Masques entraînement / validation ; refuse tout chevauchement entre une image ou une cible de validation et une
    cible d'entraînement, et toute cible qui dépasse la fin de DEVELOPMENT."""
    target_end = table["monday"] + pd.Timedelta(days=7)                 # journée du lundi de clôture
    train = (target_end <= TRAIN_TARGET_END).to_numpy() & table["label"].notna().to_numpy()
    validation = ((table["monday"] >= FIRST_VALIDATION) & (target_end + pd.Timedelta(days=1) <= end + pd.Timedelta(seconds=1))).to_numpy()
    if train.any() and validation.any():
        last_train = (target_end[train] + pd.Timedelta(days=1)).max()  # clôture de la dernière cible d'entraînement
        first_image = (table.loc[validation, "monday"] - pd.Timedelta(days=DAYS)).min()
        if first_image < last_train:
            raise ValueError(f"chevauchement : image de validation dès {first_image}, cible d'entraînement jusqu'à {last_train}")
    if (table.loc[train | validation, "last_day"] >= table.loc[train | validation, "monday"]).any():
        raise ValueError("une image contient une journée postérieure au dimanche")
    return train, validation


# --- modèle -----------------------------------------------------------------------------------------------------

def build_model() -> Any:
    import torch
    from torch import nn
    layers: list[Any] = []
    channels = 1
    for out in (64, 128, 256):
        layers += [nn.Conv2d(channels, out, (5, 3), padding=(2, 1)), nn.BatchNorm2d(out), nn.LeakyReLU(0.01), nn.MaxPool2d((2, 1))]
        channels = out
    model = nn.Sequential(*layers, nn.Flatten(), nn.Dropout(0.5), nn.Linear(256 * (HEIGHT // 8) * WIDTH, 2))
    for module in model.modules():
        if isinstance(module, nn.Conv2d | nn.Linear):
            nn.init.xavier_uniform_(module.weight)
            if module.bias is not None:
                nn.init.zeros_(module.bias)
    return model.to(torch.float32)


def _tensor(images: np.ndarray) -> Any:
    import torch
    return torch.from_numpy(images.astype(np.float32)[:, None, :, :])


def train(images: np.ndarray, labels: np.ndarray, stop_images: np.ndarray, stop_labels: np.ndarray, *,
          max_epochs: int = MAX_EPOCHS, seed: int = SEED, say: Callable[[str], None] | None = None) -> tuple[Any, list[dict]]:
    """Adam, entropie croisée, lots de 128 ; garde l'état à la plus basse perte d'arrêt ; s'arrête après 2 époques sans
    baisse. Renvoie le modèle et l'historique des époques."""
    import torch
    say = say or (lambda _text: None)
    torch.manual_seed(seed)
    generator = torch.Generator().manual_seed(seed)
    model = build_model()
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
    loss_fn = torch.nn.CrossEntropyLoss()
    x, y = _tensor(images), torch.from_numpy(labels.astype(np.int64))
    x_stop, y_stop = _tensor(stop_images), torch.from_numpy(stop_labels.astype(np.int64))
    best, best_state, waited, history = float("inf"), None, 0, []
    for epoch in range(1, max_epochs + 1):
        model.train()
        order = torch.randperm(len(x), generator=generator)
        total = 0.0
        for start in range(0, len(x), BATCH):
            batch = order[start:start + BATCH]
            optimizer.zero_grad()
            loss = loss_fn(model(x[batch]), y[batch])
            loss.backward()
            optimizer.step()
            total += loss.item() * len(batch)
        stop_loss = _loss(model, x_stop, y_stop)
        history.append({"epoch": epoch, "train_loss": round(total / max(1, len(x)), 6), "stop_loss": round(stop_loss, 6)})
        say(f"époque {epoch} : perte {history[-1]['train_loss']} / arrêt {history[-1]['stop_loss']}")
        if stop_loss < best:
            best, waited = stop_loss, 0
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        else:
            waited += 1
            if waited >= PATIENCE:
                break
    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()
    return model, history


def _loss(model: Any, x: Any, y: Any) -> float:
    import torch
    model.eval()
    total = 0.0
    with torch.no_grad():
        for start in range(0, len(x), 1024):
            total += float(torch.nn.functional.cross_entropy(model(x[start:start + 1024]), y[start:start + 1024], reduction="sum"))
    return total / max(1, len(x))


def predict(model: Any, images: np.ndarray) -> np.ndarray:
    """Probabilité de la classe « au-dessus de la moyenne »."""
    import torch
    model.eval()
    out = []
    with torch.no_grad():
        x = _tensor(images)
        for start in range(0, len(x), 1024):
            out.append(torch.softmax(model(x[start:start + 1024]), dim=1)[:, 1].numpy())
    return np.concatenate(out) if out else np.zeros(0)


# --- mesure -----------------------------------------------------------------------------------------------------

def weights(table: pd.DataFrame, columns: pd.Index, mondays: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Poids de la stratégie (8 plus fortes probabilités, 1/8 chacune) et du panier (paires imagées à poids égaux)."""
    strategy = pd.DataFrame(0.0, index=mondays, columns=columns)
    basket = pd.DataFrame(0.0, index=mondays, columns=columns)
    for monday, group in table.groupby("monday"):
        basket.loc[monday, group["symbol"]] = 1.0 / len(group)
        top = group.sort_values(["prob", "symbol"], ascending=[False, True]).head(PICK)
        strategy.loc[monday, top["symbol"]] = 1.0 / PICK
    return strategy, basket


def ranking(table: pd.DataFrame) -> dict:
    """Qualité de classement (descriptive) : exactitude, AUC (Mann-Whitney), corrélation de rang moyenne par semaine."""
    from scipy.stats import rankdata, spearmanr
    known = table[table["label"].notna()]
    labels, prob = known["label"].to_numpy(float), known["prob"].to_numpy(float)
    positives = int(labels.sum())
    negatives = len(labels) - positives
    auc = None
    if positives and negatives:
        ranks = rankdata(prob)
        auc = round(float((ranks[labels == 1].sum() - positives * (positives + 1) / 2) / (positives * negatives)), 4)
    weekly = [spearmanr(g["prob"], g["ret"]).statistic for _m, g in known.groupby("monday") if len(g) > 2 and g["prob"].nunique() > 1]
    weekly = [float(r) for r in weekly if np.isfinite(r)]
    return {"samples": int(len(known)), "accuracy": round(float(((prob > 0.5) == (labels == 1)).mean()), 4) if len(known) else None,
            "auc": auc, "rank_corr_weekly_mean": round(float(np.mean(weekly)), 4) if weekly else None, "weeks": len(weekly)}


def verdict(row: dict) -> str:
    ci = row.get("excess_ci_pct")
    return PASS if ci and ci[0] > 0 else FAIL


# --- exécution --------------------------------------------------------------------------------------------------

def run(settings: Settings, *, now: datetime, progress: Callable[[str], None] | None = None, allow_dirty: bool = False,
        max_epochs: int = MAX_EPOCHS) -> dict:
    import torch

    from .long_history import load_long
    from .pit_universe import load_membership
    say = progress or (lambda _text: None)
    state = code_state()
    if (state.endswith("+DIRTY") or state == "NO_GIT_COMMIT") and not allow_dirty:
        raise DirtyCode(f"code non commité ({state}) : exécution refusée (versions reproductibles)")
    end = pd.Timestamp(development_end(settings))
    table = load_membership(settings)
    members = table.groupby(table["month"].dt.tz_convert(None).dt.to_period("M"))["symbol"].apply(set).to_dict()
    days_by_symbol: dict[str, pd.DataFrame] = {}
    missing = []
    for symbol in sorted(table["symbol"].unique()):
        say(f"bougies {symbol}")
        try:
            days_by_symbol[symbol] = daily_bars(load_long(settings, symbol), end=end)
        except MissingData:
            missing.append(symbol)
    closes = pd.DataFrame({s: d["close"] for s, d in days_by_symbol.items()}).sort_index()
    mondays = pd.date_range(FIRST_WEEK, end.floor("D"), freq="W-MON")
    returns = holding_returns(closes, mondays)
    say("images")
    samples, images = build_samples(days_by_symbol, members, mondays, returns)
    train_mask, validation_mask = split(samples, end)
    train_weeks = np.array(sorted(samples.loc[train_mask, "monday"].unique()))
    rng = np.random.default_rng(SEED)
    stop_weeks = set(rng.choice(train_weeks, size=round(STOP_SHARE * len(train_weeks)), replace=False))
    stop_mask = train_mask & samples["monday"].isin(stop_weeks).to_numpy()
    fit_mask = train_mask & ~stop_mask
    torch.set_num_threads(max(1, torch.get_num_threads()))
    model, history = train(images[fit_mask], samples.loc[fit_mask, "label"].to_numpy(), images[stop_mask],
                           samples.loc[stop_mask, "label"].to_numpy(), max_epochs=max_epochs, say=say)
    validation = samples[validation_mask].copy()
    validation["prob"] = predict(model, images[validation_mask])
    val_mondays = pd.DatetimeIndex(sorted(validation["monday"].unique()))
    strategy_w, basket_w = weights(validation, closes.columns, val_mondays)
    val_returns = returns.reindex(val_mondays)
    strategy_net, turnover = portfolio_series(strategy_w, val_returns)
    basket_net, basket_turnover = portfolio_series(basket_w, val_returns)
    rows = {"TOP8_CNN": describe(strategy_net, basket_net) | {"turnover_weekly": round(float(turnover.mean()), 3)},
            "PANIER": describe(basket_net, basket_net * 0) | {"turnover_weekly": round(float(basket_turnover.mean()), 3)}}
    result_verdict = verdict(rows["TOP8_CNN"])
    registry = ExperimentRegistry(settings.experiments_db)
    program = registry.program_trials() + N_TRIALS
    run_id = new_run_id("CNN")
    report_dir = settings.reports_dir / run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    coverage = {"pairs_missing_hourly": missing, "samples": int(len(samples)), "train": int(fit_mask.sum()), "stop": int(stop_mask.sum()),
                "validation": int(validation_mask.sum()), "train_weeks": int(len(train_weeks)), "stop_weeks": len(stop_weeks),
                "validation_weeks": int(len(val_mondays)), "first_validation": str(val_mondays.min())[:10] if len(val_mondays) else None,
                "last_validation": str(val_mondays.max())[:10] if len(val_mondays) else None,
                "train_label_share": round(float(samples.loc[fit_mask, "label"].mean()), 4) if fit_mask.any() else None}
    payload = {"run_id": run_id, "n_trials": N_TRIALS, "program_trials": program, "verdict": result_verdict, "rows": rows,
               "ranking": ranking(validation), "epochs": history, "coverage": coverage, "doc": "docs/CNN.md"}
    (report_dir / "summary.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    validation.drop(columns=["last_day"]).to_parquet(report_dir / "predictions.parquet", index=False)
    torch.save(model.state_dict(), report_dir / "model.pt")
    registry.record(run_id=run_id, created_at=now.isoformat(), kind=KIND,
                    hypothesis="un CNN sur images de 20 journées (prix + volume) classe-t-il les paires du top 40 à date mieux que le "
                               "hasard au point de battre le panier avec les 8 plus fortes probabilités ?",
                    strategy=STRATEGY, strategy_version=1, variant="architecture et réglages figés (docs/CNN.md)",
                    params={"days": DAYS, "image": [HEIGHT, WIDTH], "pick": PICK, "learning_rate": LEARNING_RATE, "batch": BATCH,
                            "max_epochs": max_epochs, "patience": PATIENCE, "stop_share": STOP_SHARE, "seed": SEED,
                            "train_target_end": str(TRAIN_TARGET_END)[:10], "first_validation": str(FIRST_VALIDATION)[:10],
                            "cost_per_side": COST_PER_SIDE},
                    period_label="DEVELOPMENT", period_start=str(FIRST_WEEK)[:10], period_end=end.isoformat(),
                    universe=sorted(days_by_symbol), data_hashes={}, git_commit=state,
                    dependencies=dependency_versions() | {"torch": torch.__version__}, seed=SEED,
                    cost_scenario="frais 7,5 pb + glissement 5 pb par côté sur la rotation",
                    simulation_rules={"entry": "clôture du lundi (image jusqu'au dimanche)", "exit": "clôture du lundi suivant"},
                    metrics={"n_trials": N_TRIALS, "program_trials": program, "verdict": result_verdict, "rows": rows,
                             "ranking": payload["ranking"], "coverage": coverage},
                    status="COMPLETED", report_dir=str(report_dir))
    return payload
