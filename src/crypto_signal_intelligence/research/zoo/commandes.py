"""Corps des commandes `csi zoo …` (la CLI Typer de `cli.py` ne fait qu'afficher)."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pandas as pd

from ...config import Settings
from .causality import INSTANTS_MIN, controler_mutations, verifier_portefeuille, verifier_test
from .data import DAY_H, DECOUVERTE, HOUR_NS, Paire, SerieExterne
from .run import Source, contexte, panneau
from .spec import FORME_A, Contexte, Test
from .strategies.commun import decisions_hebdomadaires


def dossier_h0(settings: Settings, commit: str, *, mini: int, familles: list[str] | None, now: datetime) -> Path:
    tag = commit[:12] + ("-DIRTY" if commit.endswith("+DIRTY") else "")
    if mini:
        name = f"ZOO-H0-MINI-{tag}-{'-'.join(familles or ['TOUT'])}-{mini}R-{now:%Y%m%dT%H%M%SZ}"
    else:
        name = f"ZOO-H0-{tag}"
    return settings.reports_dir / name


def serie_temoin(debut_h: int, fin_h: int, graine: int = 7) -> SerieExterne:
    """Série externe TÉMOIN (synthétique) des contrôles de mutation : valeur quotidienne datée minuit, publiée 18 h
    plus tard (comme une clôture de New York)."""
    rng = np.random.default_rng(graine)
    days = np.arange(debut_h // DAY_H, fin_h // DAY_H + 1)
    dates = days * DAY_H * HOUR_NS
    return SerieExterne("temoin", dates + 18 * HOUR_NS, dates, rng.normal(0, 1, len(days)))


def causalite(settings: Settings | None, tests: list[Test], source: Source, *, out_dir: Path, now: datetime,
              n_paires: int = 12, progress: Callable[[str], None] | None = None) -> dict:
    """Causalité de chaque test codé (§ 7) sur un échantillon de paires de la découverte, puis les quatre mutations
    exigées. Aucun rendement : seuls des signaux sont recalculés."""
    say = progress or (lambda _t: None)
    rng = np.random.default_rng(20261012)
    symbols = list(source.symboles)
    chosen = sorted(rng.choice(symbols, size=min(n_paires, len(symbols)), replace=False).tolist())
    pairs = [p for p in (source.paire(s) for s in chosen) if p is not None]
    out: dict = {"date": now.isoformat(), "source": source.nom, "paires": [p.symbole for p in pairs], "tests": {}}
    for test in tests:
        say(test.id)
        if test.forme == FORME_A:
            def ctx_of(pair: Paire, test: Test = test) -> Contexte:
                return contexte(test, pair, DECOUVERTE, source)
            out["tests"][test.id] = verifier_test(test, pairs, DECOUVERTE, ctx_of).resume()
        else:
            daily = {p.symbole: p.unite(DAY_H) for p in pairs}
            board = panneau(daily, source.univers, jusqu_a_jour=DECOUVERTE.fin_jour)
            ctx = Contexte("", DAY_H, test.params, DECOUVERTE.debut_ns, externes=MappingProxyType(source.externes),
                           evenements=MappingProxyType(source.evenements), univers=source.univers)
            _, decisions, _ = decisions_hebdomadaires(board)
            pool = decisions[(decisions >= DECOUVERTE.debut_ns) & (decisions <= DECOUVERTE.fin_ns)]
            taus = np.sort(rng.choice(pool, size=min(INSTANTS_MIN, len(pool)), replace=False))
            out["tests"][test.id] = verifier_portefeuille(test.strategie_fonction(), board, ctx, taus, test.id).resume()
    base = max(pairs, key=lambda p: len(p.h1))
    serie = serie_temoin(int(base.h1.ouverture_h[0]), int(base.h1.ouverture_h[-1]))
    out["mutations"] = controler_mutations(base.h1, serie, DECOUVERTE)
    out["ok"] = all(v["ok"] for v in out["tests"].values()) and all(
        m["temoin_ok"] and m["mutation_detectee"] for m in out["mutations"].values())
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "causalite.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    out["path"] = str(path)
    out["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def instant(text: str) -> pd.Timestamp:
    ts = pd.Timestamp(text)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
