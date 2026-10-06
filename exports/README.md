# Exports Telegram à auditer avec leurs images

Copie ici les dossiers d'export de Telegram Desktop faits **avec les photos** (ouvrir le groupe, menu ⋮ →
« Exporter l'historique du chat », cocher Photos, format **JSON**) : un sous-dossier par groupe, chacun avec son
`result.json` et son dossier `photos/`. Exemple :

```
exports/
├── README.md               (ce fichier, seul élément versionné)
├── LEGEND TRADING/
│   ├── result.json
│   └── photos/…
└── IN CRYPTO/
    ├── result.json
    └── photos/…
```

Puis, dans le tableau de bord (http://127.0.0.1:8503/, onglet « Évaluer un signal », carte « Bilan d'un groupe sur
son historique »), choisir le dossier et cliquer « Mesurer avec les images ». Les images sont lues sur cette
machine ; rien ne part sur le réseau. En terminal, un dossier à la fois (même mesure que le tableau de bord) :
`csi audit-telegram --dir "exports/<groupe>" --ocr`. `--dir exports` audite tous les dossiers ensemble : même
preuve pour chaque groupe, SAUF si un même nom de groupe (nom du trader lu en tête des signaux, ou nom du chat)
apparaît dans deux dossiers : ses messages sont alors réunis en un seul bilan.

Un audit de milliers de photos charge les modèles OCR et occupe un cœur pendant de longues minutes : le lancer
sur le PC plutôt que sur un petit VPS où tournent déjà la surveillance et BinanceSpotManager.

- Avec Docker, ce dossier est monté en lecture seule dans le service `api` (`/srv/csi/exports`). Le créer
  (`mkdir -p exports`) **avant** le premier `docker compose up`, sinon Docker le crée au nom de root.
- Tout ce qui est ici, sauf ce README, est ignoré par git (`.gitignore`) : ce sont tes messages privés.
- Détails et limites de la lecture des images : `docs/OCR.md`.
