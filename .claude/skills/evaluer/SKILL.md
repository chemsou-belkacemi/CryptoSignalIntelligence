---
name: evaluer
description: Évalue un signal de trading reçu d'un groupe Telegram (paire, entrée, TP, stop) avec CryptoSignalIntelligence et explique le verdict simplement. À utiliser quand le propriétaire colle un signal ou demande si un signal Telegram est bon à prendre.
argument-hint: "<groupe> puis le texte du signal"
---

# Évaluer un signal Telegram

Entrée : $ARGUMENTS. Le premier mot est le nom du groupe (source) ; le reste est le texte du
signal. Si le groupe manque, le demander : le bilan par groupe en dépend.

## 1. Évaluer

Surveillance Docker active (`docker compose ps` montre `monitor` en marche) : évaluer dans l'état
Docker, pour que la surveillance résolve le signal ensuite toute seule :

```powershell
$texte | .\scripts\evaluer-signal.ps1 -Source "<groupe>"
```

Sinon, en local :

```
.venv/Scripts/python.exe -m crypto_signal_intelligence evaluate-signal --source "<groupe>" --text "<signal>"
```

Ajouter `-SansEnregistrer` (ou `--no-record`) seulement si le propriétaire veut un simple essai.
Rien n'est exécuté : aucun ordre, aucune clé. L'exécution reste le rôle de BinanceSpotManager (Demo).

## 2. Expliquer (en français, simplement)

- **Verdict** : REFUSE, DEFAVORABLE, INDETERMINE ou FAVORABLE, et **pourquoi** (veto déclenché,
  espérance de la même géométrie dans le même régime, intervalle).
- **Les chiffres avec leur définition** : « sur N ordres semblables pris à l'aveugle dans ce régime,
  X % ont touché le TP1 avant le stop » est un taux de base historique, pas la probabilité que CE
  signal réussisse. Toujours donner l'intervalle, jamais un chiffre seul.
- **Le groupe** : bilan de la source (`.venv/Scripts/python.exe -m crypto_signal_intelligence sources`)
  s'il existe. Aucune conclusion sur un groupe avant 20 signaux résolus sur au moins 10 jours ; ne
  jamais conclure sur un groupe à partir d'un seul signal.
- **Ce que CSI ne sait pas** : l'information du groupe (actualité, flux), les frais réels Demo, le
  glissement réel. Un INDETERMINE veut dire « pas assez d'éléments », pas « 50/50 ».
- Terminer par une phrase de décision prudente (ex. « refus conseillé : stop déjà franchi »,
  « rien ne justifie de le préférer au hasard »), sans jamais annoncer de gain.
