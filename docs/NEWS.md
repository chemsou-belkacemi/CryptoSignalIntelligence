# Actualités : collecte sans LLM, mode observe

Implémentation : `news/` (lecture des flux, actifs, stockage, collecte). Données dans
`news/news.sqlite3`. Aucun LLM, aucune clé, uniquement des GET HTTPS publics vers les URL configurées.

## Modes (`[news] mode`)

| Mode | Effet |
|---|---|
| `off` | aucune collecte ; `NEWS_STATUS=OFF` dans les signaux |
| `observe` (défaut) | collecte, historique et affichage ; **aucune influence sur les signaux** ; `NEWS_STATUS=OBSERVE` |
| `gate` | **refusé par la configuration** : aucune règle de blocage n'a encore été évaluée. Une news positive ne pourra jamais créer un achat. |

## Traçabilité

Chaque élément garde : source, catégorie de source, URL, titre, résumé (balises retirées, tronqué),
actifs détectés, date de publication (si la source la donne, sinon vide), **première réception**
(jamais réécrite), dernière observation, révision et identifiant d'événement.

- **Corrections** : un même élément dont le titre ou le résumé change reçoit une nouvelle révision.
  Toutes les versions sont conservées dans `news_revisions`.
- **Reprises** : deux sources différentes qui publient la même information (titres proches,
  Jaccard ≥ 0,5 sur les mots significatifs, mêmes actifs, fenêtre de 48 h) partagent un
  identifiant d'événement. Une source ne se « reprend » pas elle-même : deux annonces d'un même
  émetteur au titre voisin restent deux événements.
- **Actifs** : règles lexicales explicites (`news/assets.py`). Un ticker ambigu (NEAR, DOT…) n'est
  reconnu qu'en majuscules ou entre parenthèses. Une détection est une étiquette de tri, pas une
  preuve que l'article concerne l'actif.
- Le contenu des articles est une donnée non fiable : il n'est jamais interprété comme une
  instruction (un test l'illustre avec un texte « Ignore previous instructions »).

## Sources

État vérifié le 2026-09-30 avec `news-sources --check` : réponse HTTPS 200, format lisible, au
moins un élément, au moins 80 % des éléments datés.

| Source | Catégorie | Vérification du 2026-09-30 |
|---|---|---|
| coindesk | CRYPTO_MEDIA | 25 éléments, 25 datés |
| cointelegraph | CRYPTO_MEDIA | 30 éléments, 30 datés |
| decrypt | CRYPTO_MEDIA | 36 éléments, 36 datés |
| theblock | CRYPTO_MEDIA | 20 éléments, 20 datés |
| binance_announcements | EXCHANGE_OFFICIAL | 140 éléments, 140 datés (listings, retraits, maintenance…) |
| federal_reserve | MACRO_OFFICIAL | 20 éléments, 20 datés |
| ecb | MACRO_OFFICIAL | 15 éléments, 15 datés |
| sec | REGULATOR | 25 éléments, 25 datés |

Écartées : **Bitcoin Magazine**, dont le flux redirige vers `http://` (HTTPS obligatoire). Le
**calendrier des publications du BLS** (`bls.ics`, IPC, emploi) répond 403 aux clients automatisés.
Il n'est pas contourné : il n'existe donc **pas encore de calendrier macro prévisionnel**, seulement
les communiqués publiés (Fed, BCE). C'est un accès externe bloqué.

État d'une source : `UNVERIFIED` (jamais vérifiée), `OPERATIONAL` (vérifiée et succès depuis
moins de 90 min), `DOWN` (échec, ou muette trop longtemps), `DISABLED`. **Une source DOWN ou
UNVERIFIED ne signifie jamais « aucune mauvaise nouvelle ».**

## Commandes

```powershell
.\.venv\Scripts\python.exe -m crypto_signal_intelligence news-sources --check   # vérifie chaque source
.\.venv\Scripts\python.exe -m crypto_signal_intelligence news-collect           # un passage de collecte
.\.venv\Scripts\python.exe -m crypto_signal_intelligence news --hours 24 --asset BTC
```

`run` collecte aussi toutes les 15 minutes, **après** l'analyse de chaque cycle : une source lente
ou en panne ne retarde jamais les signaux.

## Limites

- L'historique commence le 2026-09-30. Les éléments antérieurs, présents dans les flux au premier
  passage, ont une première réception postérieure à leur publication : seule la première réception
  est utilisable dans un test prospectif sans biais d'anticipation.
- Aucune règle de blocage n'existe. Avant d'activer un jour `gate`, une règle devra être définie à
  l'avance puis évaluée sur l'historique prospectif.
