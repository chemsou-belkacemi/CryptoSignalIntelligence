"""Inscription de la relecture du code de l'étude « combinaisons » (§ 9 de docs/COMBINAISONS.md).

Fichier à part, hors des chemins surveillés (combinations_study.REVIEWED_PATHS) : inscrire le commit relu ne modifie
aucun fichier relu. Les deux valeurs s'inscrivent ensemble, par un commit qui ne touche que ce fichier.

- `CODE_REVIEW` : commit relu par `leak-auditor` ; toute différence ensuite sur un chemin surveillé refuse l'exécution.
- `CONFIG_FINGERPRINT` : empreinte des valeurs EFFECTIVES de la configuration lue par le calcul (fichier et variables
  `CSI_*` compris, combinations_study.config_fingerprint) au moment de la relecture ; une autre valeur refuse
  l'exécution.
"""
from __future__ import annotations

CODE_REVIEW: str | None = "05bfe86d633a69cac5bf22a4fcdc6bc99d034d1b"
CONFIG_FINGERPRINT: str | None = "1c7622cf73ef97a40907ae409a25debae14342ebf6abdb8e1120eaad50b28105"
