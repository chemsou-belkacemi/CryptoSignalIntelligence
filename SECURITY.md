# Politique de sécurité

## Signaler une vulnérabilité

**Ne publiez jamais une vulnérabilité dans une issue publique**, une discussion ou une demande de fusion.

Passez par les avis de sécurité privés de GitHub :

1. onglet **Security** du dépôt ;
2. bouton **Report a vulnerability** ;
3. décrivez le problème, la version (commit) concernée, les étapes pour le reproduire et l'effet constaté.

Le rapport n'est visible que du mainteneur et de vous. Si le bouton n'apparaît pas, ouvrez une issue publique
**sans aucun détail technique** qui demande seulement un moyen de contact privé.

N'incluez jamais de vraie clé, de vrai jeton ni de donnée personnelle dans un rapport : une valeur factice suffit.

## Ce qui est concerné

CryptoSignalIntelligence ne passe aucun ordre et n'utilise aucune clé API Binance. Sont notamment des
vulnérabilités :

- un **endpoint privé de Binance joignable** : contournement de la liste blanche de `data/http.py` (ordres, compte,
  clés d'écoute, marché à terme), redirection ou URL fabriquée qui y mène ;
- une **fuite de secret** : jeton Telegram du relais, `CSI_API_TOKEN`, contenu d'un `.env` écrit dans un journal,
  un rapport, le tableau de bord, une image Docker ou une sauvegarde ;
- une **injection par un signal Telegram** : texte ou image (OCR) reçu d'un groupe qui fait exécuter du code, écrit
  hors de son dossier, publie un signal, ajoute une paire ou modifie la configuration ;
- un **signal publié hors du mode shadow** alors que `publication.outbox_enabled` ou `integration_verified` est faux,
  ou un fichier TXT V3 malformé accepté par le parseur strict ;
- l'**API locale** (`127.0.0.1:8503`) : accès sans le jeton quand il est défini, CSRF, lecture de fichiers arbitraires
  (exports Telegram, rapports), exposition sur une autre adresse que `127.0.0.1` par défaut ;
- une **archive ou une donnée publique piégée** : contrôle SHA-256 contourné, chemin d'archive qui sort de `data/`,
  désérialisation dangereuse ;
- un contournement de l'**instance unique** (`live/lock.py`) ou de la **suspension de publication** après restauration.

Ne sont pas des vulnérabilités : un résultat de recherche décevant, une stratégie perdante, un écart entre le
backtest et Binance Demo. Ce sont des sujets d'issue ordinaires.

## Délai de réponse

Le projet est maintenu par une seule personne, sur son temps libre. À titre indicatif et **sans engagement** :
accusé de réception sous une semaine environ, premier avis sous un mois. Merci de laisser un délai raisonnable
avant toute publication, le temps qu'un correctif soit disponible.

Seule la branche `main` reçoit des correctifs.

## Rappels

- **Aucune clé dans le code.** CSI n'a besoin d'aucune clé API ; `tests/test_secrets.py` refuse tout secret en dur.
- **Les fichiers `.env` ne sont jamais versionnés** (seul `.env.example`, sans valeur réelle, l'est). Les jetons
  facultatifs (relais Telegram, `CSI_API_TOKEN`) ne vivent que dans `.env`, sur la machine qui les utilise.
- N'affichez jamais `docker compose config` sans `--quiet` : cette commande recopie le contenu de `.env` à l'écran.
- Si une clé ou un jeton a pu fuiter, considérez-le comme compromis et renouvelez-le, même après correction.
