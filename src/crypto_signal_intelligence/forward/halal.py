"""Filtre halal EN AMONT de tous les tests en direct (config/halal_screen.yaml).

La source de vérité est la décision du propriétaire déjà enregistrée dans CSI (table `pair_admissions`,
external/admission.py) : ce module ne reclasse aucune crypto. Il refuse de servir quoi que ce soit tant que le
fichier n'est pas marqué VALIDE, applique les exclusions structurelles du cadre (stablecoins, tokens adossés ou
wrapped, tokens à levier) et calcule une empreinte de la liste admise, que chaque test fige à son démarrage.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..config import Settings, config_file
from ..external.admission import FAVORABLE, AdmissionLog, base_of, load_screening
from .journal import canonical

VALIDATED = "VALIDE"


class HalalNotValidated(RuntimeError):
    """Le fichier de screening est absent, illisible ou pas encore validé par le propriétaire."""


@dataclass(frozen=True)
class HalalList:
    symbols: tuple[str, ...]                     # paires USDT admises, triées
    excluded: dict[str, str] = field(default_factory=dict)   # paire écartée → motif
    sha256: str = ""                             # empreinte de la liste admise
    file_sha256: str = ""                        # empreinte du fichier de screening

    def allows(self, symbol: str) -> bool:
        return symbol.upper() in self.symbols


def screen_path() -> Path:
    """À côté de la configuration (Docker : /app/config), comme config/halal_screening.toml."""
    return config_file().parent / "halal_screen.yaml"


def load_screen(path: Path | None = None) -> dict:
    path = path or screen_path()
    if not path.exists():
        raise HalalNotValidated(f"fichier de screening absent : {path}")
    try:
        screen = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise HalalNotValidated(f"fichier de screening illisible : {exc}") from None
    if not isinstance(screen, dict) or screen.get("statut") != VALIDATED:
        raise HalalNotValidated("screening halal non validé par le propriétaire (statut différent de VALIDE)")
    for key in ("admis", "exclus", "absent"):
        if key not in screen.get("regles", {}):
            raise HalalNotValidated(f"règle « {key} » manquante dans le fichier de screening")
    return screen


def structural_reason(base: str, screen: dict) -> str | None:
    """Motif d'exclusion structurelle (indépendant de l'avis sur la crypto), sinon None."""
    for name, rule in screen.get("structurelles", {}).items():
        if base in {b.upper() for b in rule.get("bases", [])}:
            return name
        pattern = rule.get("motif")
        if pattern and re.fullmatch(pattern, base):
            return name
    return None


def admitted(settings: Settings, *, path: Path | None = None, decisions: list[dict] | None = None,
             screenings: dict | None = None) -> HalalList:
    """Liste admise aujourd'hui : paires AJOUTEES et paires de la configuration FAVORABLES au relevé, moins les
    décisions d'exclusion du propriétaire (elles priment) et les exclusions structurelles. Le reste est exclu."""
    path = path or screen_path()
    screen = load_screen(path)
    rules = screen["regles"]
    admit_states, exclude_states = set(rules["admis"]), set(rules["exclus"])
    decisions = AdmissionLog(settings.external_db).all() if decisions is None else decisions
    decided = {d["symbol"].upper(): d for d in decisions}
    screenings = load_screening(settings)[0] if screenings is None else screenings
    configured = {s.upper() for s in settings.data.symbols}
    candidates = {s for s in configured
                  if getattr(screenings.get(base_of(s)), "status", None) == FAVORABLE or s in decided}
    candidates |= {s for s, d in decided.items() if d["decision"] in admit_states}
    symbols, excluded = [], {}
    for symbol in sorted(configured - candidates):
        excluded[symbol] = "paire configurée sans décision ni avis FAVORABLE : exclue par défaut"
    for symbol in sorted(candidates | {s for s, d in decided.items() if d["decision"] in exclude_states}):
        decision = decided.get(symbol)
        if decision is not None and decision["decision"] in exclude_states:
            excluded[symbol] = f"décision {decision['decision']} ({decision['decided_by']})"
            continue
        if decision is not None and decision["decision"] not in admit_states and symbol not in candidates:
            excluded[symbol] = f"décision {decision['decision']} : exclue par défaut"
            continue
        reason = structural_reason(base_of(symbol), screen)
        if reason:
            excluded[symbol] = f"exclusion structurelle : {reason}"
            continue
        if not symbol.endswith("USDT"):
            excluded[symbol] = "pas une paire USDT"
            continue
        symbols.append(symbol)
    digest = hashlib.sha256(canonical(symbols).encode()).hexdigest()
    file_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return HalalList(tuple(symbols), excluded, digest, file_digest)
