---
name: Bug
about: Un comportement incorrect du code (calcul, données, CLI, tableau de bord, signaux)
title: "[bug] "
labels: bug
---

> **Une faille de sécurité ?** N'ouvrez pas d'issue publique : onglet Security → Report a vulnerability (voir `SECURITY.md`).
> N'écrivez ici aucune clé, aucun jeton, aucun contenu de `.env`.

## Ce qui se passe

<!-- Décrivez le problème en quelques phrases. -->

## Ce qui était attendu

## Pour reproduire

1. Commande exacte (par exemple `.venv/bin/csi backtest --strategy …`) :
2. Configuration modifiée (variables `CSI_…`, fichier TOML) :
3. Sortie ou trace d'erreur :

```text
collez ici la sortie utile
```

## Environnement

- Commit (`git rev-parse --short HEAD`) :
- Système (Ubuntu, Windows…) et version de Python :
- Avec ou sans Docker :

## Vérifications

- [ ] J'ai relu `docs/DELIVERY_STATUS.md` : ce n'est pas une limite déjà connue.
- [ ] Il s'agit d'un défaut du code, pas d'un résultat de recherche décevant (une stratégie perdante n'est pas un bug).
