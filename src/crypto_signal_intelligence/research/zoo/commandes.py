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
from .causality import (
    INSTANTS_MIN,
    Rapport,
    controler_mutations,
    instants_aleatoires,
    instants_de_signal,
    verifier,
    verifier_portefeuille,
    verifier_test,
)
from .data import DAY_H, DECOUVERTE, HOUR_NS, Paire, SerieExterne
from .run import Source, contexte, panneau
from .spec import FORME_A, MENSUELLE, QUOTIDIENNE, Contexte, Test
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


def _decisions_de(test: Test, board) -> np.ndarray:
    """Instants de décision du calendrier du test (hebdomadaire, quotidien ou mensuel)."""
    from .strategies.commun import decisions_mensuelles, decisions_quotidiennes
    calendar = {QUOTIDIENNE: decisions_quotidiennes, MENSUELLE: decisions_mensuelles}.get(test.frequence or "",
                                                                                       decisions_hebdomadaires)
    return calendar(board)[1]


def causalite(settings: Settings | None, tests: list[Test], source: Source, *, out_dir: Path, now: datetime,
              n_paires: int = 12, progress: Callable[[str], None] | None = None) -> dict:
    """Causalité de chaque test codé (§ 7) sur un échantillon de paires de la découverte, puis les quatre mutations
    exigées. Forme (b) : panneau COMPLET de la source (toutes les paires, BTC et ETH compris), instants pris dans le
    calendrier de décision du test. EV02 : paires du recensement cotées pendant l'étape, tous leurs instants de signal.
    Aucun rendement : seuls des signaux et des poids sont recalculés."""
    say = progress or (lambda _t: None)
    rng = np.random.default_rng(20261012)
    symbols = list(source.symboles)
    chosen = sorted(rng.choice(symbols, size=min(n_paires, len(symbols)), replace=False).tolist())
    pairs = [p for p in (source.paire(s) for s in chosen) if p is not None]
    out: dict = {"date": now.isoformat(), "source": source.nom, "paires": [p.symbole for p in pairs], "tests": {}}
    board = None
    if any(t.forme != FORME_A for t in tests):
        say("panneau complet")
        daily = {}
        for s in symbols:
            pair = source.paire(s)
            if pair is not None:
                daily[s] = pair.unite(DAY_H)
        board = panneau(daily, source.univers, jusqu_a_jour=DECOUVERTE.fin_jour)
        out["panneau_paires"] = len(daily)
    census_pairs: list[Paire] = []
    if any(t.univers == "recensement" for t in tests):
        census = source.quotidien_recensement()
        listed = sorted(s for s, b in census.items() if len(b) and
                        DECOUVERTE.debut_jour <= int(b.ouverture_h[0]) // DAY_H <= DECOUVERTE.fin_jour)
        picks = sorted(rng.choice(listed, size=min(3 * n_paires, len(listed)), replace=False).tolist()) if listed else []
        census_pairs = [p for p in (source.paire_recensement(s) for s in picks) if p is not None]
        out["paires_recensement"] = [p.symbole for p in census_pairs]
    import time
    started = time.perf_counter()
    for number, test in enumerate(tests, start=1):
        say(f"{number}/{len(tests)} {test.id} ({time.perf_counter() - started:.0f} s)")
        if test.forme == FORME_A and test.univers == "recensement":
            fn = test.strategie_fonction()
            total = Rapport(test.id)
            for pair in census_pairs:
                bars = pair.unite(test.execution_heures)
                ctx = contexte(test, pair, DECOUVERTE, source)
                taus = np.unique(np.r_[instants_aleatoires(bars, DECOUVERTE, 4, rng),
                                       instants_de_signal(fn, bars, ctx, DECOUVERTE.debut_ns, DECOUVERTE.fin_ns)])
                rep = verifier(fn, pair.h1, test.execution_heures, ctx, taus, rng=rng, test_id=test.id)
                total.instants += rep.instants
                total.instants_signal += int(len(taus))
                total.ecarts_tronque += rep.ecarts_tronque
                total.ecarts_falsifie += rep.ecarts_falsifie
            out["tests"][test.id] = total.resume()
        elif test.forme == FORME_A:
            def ctx_of(pair: Paire, test: Test = test) -> Contexte:
                return contexte(test, pair, DECOUVERTE, source)
            out["tests"][test.id] = verifier_test(test, pairs, DECOUVERTE, ctx_of).resume()
        else:
            assert board is not None
            ctx = Contexte("", DAY_H, test.params, DECOUVERTE.debut_ns, externes=MappingProxyType(source.externes),
                           evenements=MappingProxyType(source.evenements), univers=source.univers)
            decisions = _decisions_de(test, board)
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
