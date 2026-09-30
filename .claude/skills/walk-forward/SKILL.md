---
name: walk-forward
description: Lance un walk-forward encadré de CryptoSignalIntelligence (un seul travail lourd à la fois, 7,7 Go de RAM), le compare au précédent et met à jour la documentation. Réservé au propriétaire.
disable-model-invocation: true
argument-hint: "[STRATEGIE ...] (défaut : les trois stratégies, l'une après l'autre)"
---

# Walk-forward encadré

Argument : $ARGUMENTS (identifiants de stratégies ; vide = DONCHIAN_VOLUME_BREAKOUT,
EMA_PULLBACK_CONTINUATION, RANGE_REENTRY).

## 1. Avant de lancer

- **Un seul travail lourd** : vérifier qu'aucun autre walk-forward, backtest ou criblage ne tourne
  (`tasklist /FI "IMAGENAME eq python.exe"` : un processus python de plus de 500 Mo = travail en
  cours → attendre, ne pas lancer en parallèle). La surveillance Docker (≈ 0,7 Go) peut rester active.
- Les tests passent : `.venv/Scripts/python.exe -m pytest -q`. Sinon, s'arrêter et le dire.
- Rappeler le nombre d'essais déjà faits sur DEVELOPMENT (dernier `program_trials` affiché par
  `compare_runs.py`) : ce run en ajoute (taille de la grille).
- Jamais `--period final-test` ni `--i-understand-final-test` sans demande explicite du propriétaire.

## 2. Lancer

Une commande par stratégie, en arrière-plan, sortie dans le dossier temporaire de la session :

```
.venv/Scripts/python.exe -m crypto_signal_intelligence walk-forward --strategy <ID>
```

Durée : environ 40 min par stratégie sur 16 paires. Ne pas interroger en boucle : attendre la
notification de fin.

## 3. Comparer

```
.venv/Scripts/python.exe .claude/skills/walk-forward/compare_runs.py <ID ...>
```

Présenter, pour chaque stratégie : verdict, critères en échec, E[R] et IC95 par variante contre le
run précédent, et **expliquer tout écart** (changement de code, de coûts, de données) avant de le
présenter comme un résultat. Un écart inexpliqué = le signaler comme tel.

## 4. Documenter

- `README.md` (section résultats) et `docs/DELIVERY_STATUS.md` : nouveaux run_id, verdicts, nombre
  d'exécutions et d'essais cumulés.
- Aucune rentabilité annoncée, aucun nombre présenté comme une probabilité. Un verdict
  VALIDATED_OOS ne change aucun statut : la promotion reste une décision du propriétaire.
- Proposer ensuite de lancer le sous-agent `leak-auditor` si le code a changé depuis le run précédent.
