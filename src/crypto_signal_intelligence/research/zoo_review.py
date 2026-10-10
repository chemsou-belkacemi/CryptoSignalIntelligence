"""Inscription de la relecture du moteur du zoo et de son contrôle sous H0 (docs/ZOO.md § 7 et § 8.4).

Fichier à part, hors des chemins surveillés (`zoo/review.REVIEWED_PATHS`) : inscrire le commit relu ne modifie aucun
fichier relu. Il ne peut contenir que sa docstring, `from __future__ import annotations` et les affectations ci-dessous
à une chaîne ou None (aucun code exécuté à l'import) ; il s'inscrit par un commit qui ne touche que lui.

- `CODE_REVIEW` : commit relu par `leak-auditor` ; toute différence ensuite sur un chemin surveillé refuse l'exécution.
- `CONFIG_FINGERPRINT` : empreinte des valeurs EFFECTIVES de la configuration lue par le calcul au moment de la
  relecture (`zoo/review.config_fingerprint`) ; une autre valeur refuse l'exécution.
- `CONTROLE_H0` : chemin du fichier de critères du contrôle sous H0 (`criteres.json`), suivi de « # » et de son
  empreinte SHA-256. Fichier absent, modifié, d'un mini-contrôle ou d'un autre commit relu : exécution refusée.
- `COMPTAGE` : chemin du comptage à blanc de la découverte (`comptage.json`, publié avant toute lecture de rendement),
  suivi de « # » et de son empreinte : les tests `INSUFFISANT_PAR_CONSTRUCTION` qu'il liste sont retirés (0 essai).
"""
from __future__ import annotations

CODE_REVIEW: str | None = None
CONFIG_FINGERPRINT: str | None = None
CONTROLE_H0: str | None = None
COMPTAGE: str | None = None
