"""Inscription de la relecture du code de l'étude « météo du marché » et des contrôles synthétiques (§ 7.2, § 7.3 de
docs/METEO_MARCHE.md).

Fichier à part, hors des chemins surveillés (meteo_execution.REVIEWED_PATHS) : inscrire le commit relu ne modifie aucun
fichier relu. Il ne peut contenir que sa docstring, `from __future__ import annotations` et les affectations ci-dessous
à une chaîne ou None (aucun code exécuté à l'import) ; il s'inscrit par un commit qui ne touche que lui.

- `CODE_REVIEW` : commit relu par `leak-auditor` ; toute différence ensuite sur un chemin surveillé refuse l'exécution.
- `CONFIG_FINGERPRINT` : empreinte des valeurs EFFECTIVES de la configuration lue par le calcul au moment de la
  relecture ; une autre valeur refuse l'exécution.
- `CONTROLES_PRINCIPALE`, `CONTROLES_VARIANTE` : chemin du fichier de critères des contrôles synthétiques de chaque
  question (`<question>_criteres.json`), suivi de « # » et de son empreinte SHA-256. Un fichier modifié, absent, ou
  dont un critère du § 7.2 a échoué refuse l'exécution (la question n'est ni exécutée ni comptée).
"""
from __future__ import annotations

CODE_REVIEW: str | None = None
CONFIG_FINGERPRINT: str | None = None
CONTROLES_PRINCIPALE: str | None = None
CONTROLES_VARIANTE: str | None = None
