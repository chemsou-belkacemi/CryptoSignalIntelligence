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

## Risques : classement en observation (point 11 du plan, déclaré le 2026-10-03, corrigé avant le début de la population)

Code : `news/risk.py`. Tests : `tests/test_news_risk.py`. Commande : `csi news-risk [--days 7] [--llm <modèle>] [--study]`.

Chaque article (chaque révision) reçoit des catégories de **risque** :

| Catégorie | Exemples de règles (anglais et français, sans tenir compte de la casse) |
|---|---|
| PIRATAGE | hack, exploit, drained, stolen, breach, compromised, piratage |
| RETRAIT | delist, removal of trading pairs, monitoring tag, cease trading |
| REGLEMENTATION | SEC/CFTC/DOJ sues/charges, lawsuit, sanction, ban, subpoena, crackdown, Wells notice |
| DEPEG | depeg, loses peg, off its peg |
| PANNE | outage, halts/suspends deposits, withdrawals or trading, network halted |
| INSOLVABILITE | bankrupt, insolvency, Chapter 11, wind down, shut down |

- **Méthode par défaut, `MOTS_CLES:ff61d1b7`** : règles lexicales explicites, appliquées à la fin de chaque collecte.
  Le suffixe est l'empreinte des règles : retoucher une règle crée une autre méthode, et la population de l'étude
  reste celle des règles d'origine. Les actifs sont détectés sur la liste élargie des actifs suivis (paires du
  service, 40 paires de recherche, 24 de F13), avec les règles prudentes de `news/assets.py`.
- **Modèle local, facultatif** : `--llm qwen3:8b` (par exemple) interroge **Ollama sur la boucle locale
  uniquement** (127.0.0.1, sans passer par un proxy) ; toute autre adresse est refusée. Les appels au modèle se
  font hors de toute transaction : un modèle lent ne bloque jamais la collecte. Le texte de l'article est encadré comme une donnée
  non fiable ; la réponse doit être un JSON aux catégories connues et aux actifs de la liste, sinon l'article
  reste sans étiquette (jamais de supposition). La commande affiche l'accord entre mots-clés et modèle.
  **Ollama n'est pas installé** : son installation demande `sudo`, donc le terminal du propriétaire
  (`curl -fsSL https://ollama.com/install.sh | sh`, puis `ollama pull qwen3:8b`, environ 5 Go). Sans lui, seule la
  méthode par mots-clés tourne.
- **Aucune influence sur les signaux** : le mode `gate` reste refusé par la configuration. Une news, de risque ou
  non, ne crée jamais d'achat.

Faux positifs connus : un article sans actif détecté (« California subpoenas OpenAI over models that hacked… »)
n'est qu'un contexte ; « Blast to wind down Ethereum L2 » étiquette ETH alors que seul Blast ferme. La détection
d'actif reste une étiquette de tri.

### Étude d'événements déclarée (avant tout résultat)

- **Population** : articles étiquetés à risque par `MOTS_CLES:ff61d1b7` qui nomment au moins un actif suivi autre que
  BTC, dont le risque est devenu visible **à partir du 2026-10-04 00:00 UTC** (après cette déclaration), dans un
  regroupement d'articles né lui aussi après cette date ; un seul événement par actif et par regroupement
  (le premier article à risque). Hors population : un article publié plus de 24 h avant sa réception (flux
  rattrapé).
- **Départ** : l'heure de la **première révision étiquetée à risque** (un titre corrigé en « exploited » trois
  heures après sa première version n'est visible comme risque qu'à la correction).
- **Mesure** : rendement de l'actif **moins celui de BTC**, de l'ouverture de la première bougie 1 h qui commence
  après le départ à la clôture de la bougie qui finit 24 h (et 7 jours) plus tard, les deux sur la même bougie de
  départ ; bougies publiques Binance. Un événement sans prix (paire retirée de la cote) est compté à part, jamais
  oublié.
- **Hypothèse** : l'excès moyen à 24 h est négatif (une news de risque précède une baisse relative).
- **Évaluation** : le 2026-12-25 avec les autres tests si au moins 30 événements à 24 h ; sinon dès que 30 sont
  atteints, au plus tard le 2027-03-31 (au-delà : « trop peu d'événements », sans conclusion).
- **Décision** : veto **candidat** si la borne haute de l'IC à 95 % (blocs de 7 jours) de l'excès à 24 h est
  sous 0. Si l'intervalle est indisponible (moins de 10 semaines d'événements), pas de conclusion : nouvelle
  lecture au 2027-03-31. Deux essais comptés à l'évaluation (24 h, 7 jours). Activer un veto reste une décision du propriétaire,
  après un nouveau protocole ; aucun résultat ne peut créer un achat.

## Limites

- L'historique commence le 2026-09-30. Les éléments antérieurs, présents dans les flux au premier
  passage, ont une première réception postérieure à leur publication : seule la première réception
  est utilisable dans un test prospectif sans biais d'anticipation.
- Aucune règle de blocage n'existe. Avant d'activer un jour `gate`, une règle devra être définie à
  l'avance puis évaluée sur l'historique prospectif.
