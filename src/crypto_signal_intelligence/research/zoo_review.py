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
- `CONFIRMATION_REVIEW` : commit relu par `leak-auditor` pour la confirmation de TR37_1j et XS17_1j
  (docs/ZOO_CONFIRMATION.md, `zoo/confirmation.py`) ; même garde que `CODE_REVIEW`.
"""
from __future__ import annotations

CODE_REVIEW: str | None = "fb02f9a61ea0ecccc96ac9d97f6302dcc046ebab"
CONFIG_FINGERPRINT: str | None = ("998eb70cac4ab74e4e07b4503c078780"   # sha256 coupé en deux : sinon pris pour une
                                   "d04293d03edc4dbac522699552ee632e")  # clé Binance par tests/test_secrets.py
CONTROLE_H0: str | None = "/home/chamsou-blk/BinanceSpotManager/CryptoSignalIntelligence/reports/ZOO-H0-50090007fe2e/criteres.json#5cd82d72797aa5ff66349aa2cfae7bc32c5e5374f93046db8157a9fc4d8428b5"
COMPTAGE: str | None = "/home/chamsou-blk/BinanceSpotManager/CryptoSignalIntelligence/reports/ZOO-COMPTAGE-fb02f9a61ea0/comptage.json#d47af1d330b4465730e32b88e4a575aaef000b34a7f39f137f1245de4a81102a"
CONFIRMATION_REVIEW: str | None = "20898266d73fe0bc4f24b6ed8cef2ba6d0bf401a"
