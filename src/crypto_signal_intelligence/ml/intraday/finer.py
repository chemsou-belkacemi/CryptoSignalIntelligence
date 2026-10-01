"""Vérification en bougies 1 min (docs/ML_INTRADAY.md §5), obligatoire avant la période finale.

Contrôle prévu, sur les trades réellement pris par le système retenu (validations) : entrée au prix de
la première minute suivant l'ouverture de t+1 (latence réaliste de publication puis d'exécution) et
ordre réel stop / objectif à l'intérieur de chaque bougie 15 min. Elle n'est construite que si une
sélection retient un système (téléchargement 1 min à activer pour ce seul contrôle) : tant qu'elle
n'existe pas, la période finale reste verrouillée — un fichier écrit à la main ne suffit pas.
"""
from __future__ import annotations

from pathlib import Path

IMPLEMENTED = False


def minute_check_status(report_dir: Path) -> tuple[bool, str]:
    """(réussie, détail) pour la sélection de `report_dir`."""
    if not IMPLEMENTED:
        return False, "pas encore implémentée (construite seulement si une sélection retient un système)"
    return False, f"aucun résultat dans {report_dir}"
