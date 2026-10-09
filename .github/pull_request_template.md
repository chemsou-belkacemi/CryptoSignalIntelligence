## Ce qui change

<!-- Quoi, et pourquoi. Liez l'issue concernée (« Ferme #… »). -->

## Comment c'est testé

<!-- Tests ajoutés ou modifiés, commandes lancées, sortie résumée. -->

## Pour un travail de recherche

<!-- Protocole déclaré (lien), nombre d'essais, période lue, verdict tel quel (négatif compris). Sinon : « sans objet ». -->

## Liste de contrôle

- [ ] `.venv/bin/python -m pytest -m "not network"` vert.
- [ ] `.venv/bin/ruff check src tests` et `.venv/bin/mypy` verts.
- [ ] Tout calcul nouveau ou corrigé de niveau, de coût, de R ou de remplissage simulé a son test.
- [ ] Aucune clé, aucun jeton, aucun secret, aucun fichier `.env` ajouté.
- [ ] Aucun ordre, aucun endpoint privé : la liste blanche de `data/http.py` n'est pas élargie.
- [ ] Pas de look-ahead : jointures et coupures sur `available_at` ; aucun paramètre choisi sur toute la période.
- [ ] Modules gelés des tests en direct intacts : `tests/test_frozen_running_tests.py` vert sans modifier ses empreintes.
- [ ] Aucune phrase ni aucun chiffre ne promet un gain.
- [ ] Documentation à jour (`docs/DELIVERY_STATUS.md` si un livrable change d'état), en français.
