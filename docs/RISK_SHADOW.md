# Risque à 24 h en shadow

Suite de la confirmation de la volatilité (`VOLATILITY.md` § 19) : la **seule** prévision confirmée hors
échantillon est la prévision horaire à 24 h (HAR + profil heure × jour). Les prévisions à 1, 3 et 7 jours ne sont
pas confirmées : elles restent un affichage.

## Ce qui est branché (shadow : information seulement)

- **Source** : la prévision que le test en direct F12 calcule déjà chaque jour à 00:00 UTC pour les paires du
  service, lue dans son journal sans rien y écrire (code de F12 gelé, non modifié). Prévision de plus de 36 h :
  aucun conseil.
- **Ampleur typique sur 24 h** : la racine de la variance prévue, soit un écart-type de rendement sur 24 h, en %.
  Un stop plus serré que cette ampleur est à l'intérieur du mouvement ordinaire d'une journée. Ce n'est **pas** une
  probabilité d'être touché : la forme des rendements n'est pas supposée.
- **Taille relative à risque égal** : ampleur médiane des paires / ampleur de la paire, bornée à [0,25 ; 2]. Une
  paire deux fois plus agitée que la médiane reçoit deux fois moins.
- **Où** : carte « Risque à 24 h » de l'onglet Marché du tableau de bord, route `GET /risk` de l'API, journal
  quotidien `state/risk_shadow.jsonl` (une ligne par prévision, ajout seul) pour comparer plus tard le conseil et ce
  qui s'est passé.

**Aucune influence** : ni sur les signaux de CSI, ni sur BinanceSpotManager, ni sur aucun test en direct. Aucune
prévision de sens ni de gain.

## Ce qui reste avant d'aller plus loin

- Passer ces conseils à BSM (taille, stop) demande un protocole déclaré : par exemple, sur les signaux mesurés en
  direct, comparer un stop au-delà de l'ampleur prévue à un stop en pourcentage fixe, avec placebos. Rien n'est
  décidé ; ce sera au propriétaire, après quelques semaines de journal.
- L'abstention quand la volatilité est extrême demande un historique de prévisions (F12 n'en a que depuis le
  2026-10-02) : pas avant 60 jours de journal.

Code : `risk/advice.py`. Tests : `tests/test_risk_advice.py`.
