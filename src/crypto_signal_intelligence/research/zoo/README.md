# Moteur du zoo des stratégies

Protocole pré-inscrit : [`docs/ZOO.md`](../../../../docs/ZOO.md) ; recensement : [`docs/ZOO_CATALOGUE.md`](../../../../docs/ZOO_CATALOGUE.md).
Ce dossier code **une seule méthode** pour les 239 tests. Rien ici n'annonce une rentabilité ; aucun ordre, aucune clé,
**aucune lecture après le 2025-06-30**.

## Architecture

```
magasin long (1 h)  ──►  data.py : coupure dure, agrégation 4 h / 1 j datée par la dernière bougie 1 h,
marché synthétique ─┘              univers à date (mois de la décision), séries externes sur available_at
                            │
                            ▼
              strategies/<famille>.py : signal(frame, ctx)  — fonction PURE, bougies closes, aucune I/O
                            │
             ┌──────────────┴──────────────┐
   forme (a) : positions.py (aucun prix)    forme (b) : Poids (poids cibles)
             │                               │
             ▼                               ▼
   measure.py : r(i,d), e(i,d), μ*, m(p)   measure.py : excès hebdomadaire sur la référence
             └──────────────┬──────────────┘
                            ▼
   decide.py : p (sens, condition jointe, max des scénarios), Benjamini-Hochberg 10 %, garde-fous,
               Benjamini-Yekutieli (descriptif), validation à 1 − 0,05/m
                            │
        ┌───────────────────┼────────────────────────┐
   h0.py (synth.py)    reel.py (comptage, exécution)   causality.py, review.py
```

`run.py` est le pipeline **commun** au contrôle sous H0 et à l'exécution réelle : seule la source de données change
(`SourceSynthetique` dans `h0.py`, `SourceMagasin` dans `reel.py`).

| Module | Rôle |
|---|---|
| `data.py` | `Bougies`, `Paire`, `Grille`, `Univers`, `Etape` (découverte 2019-01-01 → 2022-12-31, validation 2023-01-01 → 2025-06-30), `borne_lecture` (refus au-delà du 2025-07-01 00:00), `joindre` (séries externes, `available_at` strictement antérieur) |
| `spec.py` | interface (`Signaux`, `Poids`, `Panneau`, `Contexte`) et registre des 239 tests (`TESTS`, `PAR_ID`, `verifier_registre`) |
| `indicators.py` | indicateurs purs (SMA, EMA, RMA de Wilder, ATR, DMI, SuperTrend, SAR, Ichimoku, Aroon, Vortex, TRIX, KAMA, HMA, filtre d'Alexander, point et figure) |
| `positions.py` | positions d'une règle : entrée à la clôture suivante, sortie à la clôture après la règle, plafond N, trous, fin d'étape, retrait de la cote, durée nulle ; **aucun prix** |
| `measure.py` | forme (a) : `MesureA` ; forme (b) : `MesureB` ; références ; intervalles par blocs ; placebos descriptifs |
| `decide.py` | BH, BY, garde-fous, validation, verdicts |
| `synth.py`, `h0.py` | marché synthétique (§ 8.1), contrôle sous H0 (§ 8.2, § 8.3), répliques reprises après coupure |
| `causality.py` | recalcul tronqué et futur falsifié, quatre mutations exigées |
| `review.py` | garde d'exécution (inscriptions de `research/zoo_review.py`) |
| `reel.py` | comptage à blanc (avec contrôles CPI/NFP et EV02), exécution unique par étape, registre des essais |
| `commandes.py` | corps des commandes `csi zoo …` |

## Interface des stratégies

Une stratégie est **une fonction pure** `signal(frame, ctx)` dans `strategies/<module>.py`, déclarée au registre
(`spec.py`, champ `fonction = "module:nom"`, paramètres standard du recensement dans `params`).

- **Aucune I/O**, aucun réseau, aucun état global, aucun hasard, aucun appel à l'horloge.
- **Causale** : la valeur rendue à la bougie `t` ne lit que les bougies `≤ t` et les valeurs externes dont
  l'`available_at` est strictement antérieur à `frame.disponible[t]` (`data.joindre`). Les pivots sont datés à leur
  **confirmation**, jamais à leur extrême. Un indicateur dont la fenêtre n'est pas entièrement disponible vaut NaN et
  ne produit aucun signal.
- Les paramètres viennent de `ctx.params` (jamais de valeurs en dur différentes du registre).

### Forme (a) : règle de transaction

```python
def signal(frame: Bougies, ctx: Contexte) -> Signaux
```

- `frame` : bougies closes de l'**unité d'exécution** du test (`test.execution` : `1h`, `4h` ou `1j`), tableaux numpy
  `ouverture, haut, bas, cloture, volume (USDT), achat (volume acheteur au marché, USDT), transactions`,
  `ouverture_h` (heure d'ouverture, en heures depuis 1970), `cloture_h`, `disponible` (instant de décision, ns).
- `ctx` : `symbole`, `unite`, `params`, `debut_etape_ns`, `quotidien` (bougies journalières de la même paire quand
  l'exécution est en 1 h : MR19, VO01, VO02, VO03-VO05, ST06-ST10, ST14, MR09, CA09-CA11), `externes`
  (`SerieExterne` déjà coupées), `evenements` (tables d'événements déjà coupées), `univers`.
- Rend `Signaux(entree, sortie)` : deux tableaux **booléens** de la longueur de `frame`.
  - règle d'**état** : `entree[t]` = condition vraie (le moteur ouvre à la première décision où elle l'est, y compris
    en début d'étape et quand la paire entre dans le top 40) ; `sortie[t]` = condition de sortie du § 12 ;
  - règle d'**événement** : `entree[t]` = déclencheur (croisement, figure…) ;
  - `sortie=None` : aucune règle de sortie (tenue jusqu'au plafond `N` du registre, ou fin d'étape).
- Le moteur fait le reste (identique pour toutes) : appartenance au top 40 du mois de la décision, une position par
  paire et par test, entrée à la clôture `t + 1`, sortie à la clôture `u + 1` après la condition vraie en `u ≥ t`,
  plafond `N` (registre), trous, fin d'étape, retrait de la cote, frais, `μ*`.
- Exécution 1 h d'un signal journalier (MR19, XS24) : la stratégie reçoit les bougies 1 h et marque le signal sur la
  bougie 1 h **00:00-01:00** (décision à 01:00:02) : l'entrée « à la clôture suivante » tombe alors à 02:00, la
  « clôture de la 1re bougie 1 h qui s'ouvre après l'`available_at` du signal » ; `N = 24` donne la sortie 24 h plus
  tard. Calendriers connus d'avance (CA09-CA11) : marquer la bougie qui **précède** l'entrée voulue.
- Stratégie dont les signaux dépendent du début de l'étape (filtre d'Alexander) : nom ajouté à `DEPENDENT_ETAPE`.

Exemple (MR02, RSI 14 en 30 / 70) :

```python
def rsi_30_70(frame: Bougies, ctx: Contexte) -> Signaux:
    p = ctx.params
    delta = np.diff(frame.cloture, prepend=np.nan)
    gain, perte = np.where(delta > 0, delta, 0.0), np.where(delta < 0, -delta, 0.0)
    gain[0] = perte[0] = np.nan
    rs = ind.rma(gain, p["n"]) / ind.rma(perte, p["n"])
    rsi = 100 - 100 / (1 + rs)
    niveau_bas = np.full(len(rsi), p["bas"])
    return Signaux(ind.croise_dessus(rsi, niveau_bas), ind.superieur(rsi, np.full(len(rsi), p["haut"])))
```

### Forme (b) : facteur ou portefeuille

```python
def signal(frame: Panneau, ctx: Contexte) -> Poids
```

- `frame` : panneau journalier `jours × paires` (`ouverture, haut, bas, cloture, volume, achat`, NaN sans bougie
  complète), `membre[j, k]` = paire membre du top 40 du mois de la décision prise à la clôture du jour `j`,
  `disponible[j]` = instant de cette décision.
- Calendrier : `strategies/commun.py` (`decisions_hebdomadaires` : décision lundi 00:00 sur la clôture du dimanche,
  échange à la clôture du lundi ; `decisions_quotidiennes` ; `decisions_mensuelles` : premier lundi du mois).
- Rend `Poids(decisions, execution_h, symboles, poids, univers=…, composante_a=…, composante_b=…, dans_a=…)` :
  poids cibles ≥ 0, somme ≤ 1 (le reste en USDT). `univers` (booléens décisions × paires) = univers de la
  **référence** (membres éligibles pour un facteur XS, top 40 pour TR37/TR40/RO04/RO05/RG02/SE02/PF, BTC seul pour
  les AM) ; `composante_a`, `composante_b`, `dans_a` pour les rotations RO02 et RO03 (référence de même composition).
- Le moteur valorise le portefeuille et sa référence à chaque échange et chaque lundi, paie les frais de rotation,
  mesure l'excès hebdomadaire.

Exemple : `trend.momentum_temporel` (TR37, TR40).

### Ajouter une famille

1. Coder chaque fonction de `strategies/<module>.py` (aujourd'hui `NotImplementedError` explicite) ; ajouter son nom à
   `IMPLEMENTEES` (et à `DEPENDENT_ETAPE` si besoin). Ne pas modifier le registre (pré-inscrit) ni le moteur commun ;
   un besoin du moteur passe par une demande au chantier du moteur, avec un test.
2. Tests à fournir, **par stratégie** (fichier `tests/test_zoo_<famille>.py`, données synthétiques, sans réseau) :
   - **définitions** : valeurs de l'indicateur calculées **à la main** sur de petits tableaux (chandeliers : contre
     TA-Lib s'il est installé, sinon contre des exemples écrits à la main d'après sa documentation) ;
   - **signaux** : un cas construit où l'entrée et la sortie tombent aux bougies attendues ;
   - **causalité** : `causality.verifier` sur une paire synthétique, à au moins 6 instants par test unitaire
     (paramétrer sur tous les tests de la famille, comme `tests/test_zoo_causality.py`) ; pour les familles qui lisent
     des pivots, des séries externes ou des calendriers, un témoin et sa **mutation détectée** (pivot daté à son
     extrême, série jointe sur sa date) ;
   - **règles propres** : exécution 1 h, fin de journée ou de séance, calendrier connu d'avance, univers d'exception ;
   - **comptage** : `csi zoo comptage --familles <XX>` tourne sans erreur (comptes seulement).
3. `ruff check src tests`, `mypy`, `pytest tests/test_zoo_*.py tests/test_frozen_running_tests.py`.

Besoins propres déclarés (à couvrir par le chantier de la famille, sans changer la méthode) : EV02 lit l'univers
d'exception du recensement (`data.charger_quotidien_recensement`, référence `measure.r180`, cotations synthétiques
`synth.generer_ev02` et `synth.annonces_ev02` pour le contrôle H0) ; les AM, SE et CA lisent les séries et calendriers
réels (`h0.externes_reels`, `reel.externes_pour`) ; CH sans TA-Lib installé : exemples écrits à la main.

## Lectures déclarées du protocole (choix prudents, à relire)

1. **`r(i, d)` d'une journée partielle** : rendement de la partie détenue, rapporté à la valeur au début de cette
   partie ; la référence est `e × m`. Le rendement n'est pas multiplié une seconde fois par `e` (son unité ne serait
   plus « par jour d'exposition »). Sous dérive constante : `μ* = 0` à 1e-12 en journées entières (test exigé), écart
   < 1e-8 en cours de journée (second ordre).
2. **Entrée dans un trou** (aucune bougie 1 h close à la clôture `t + 1`) : signal écarté et compté (pas d'entrée
   reportée). Sortie dans un trou : première clôture disponible (§ 2).
3. **Sortie évaluée dès la bougie du signal** (`u ≥ t`) : une sortie vraie en `t` donne une durée nulle, écartée et
   comptée (§ 3).
4. **Mois de la décision** = mois de l'instant `available_at` de la bougie de décision ; le top du mois `M` est
   calculé sur les 30 journées qui finissent la veille de `M` (`pit_universe.membership`, inchangée).
5. **Jointure externe** : valeur dont l'`available_at` est **strictement** antérieur à la décision.
6. **« Plus haut / plus bas des n bougies précédentes »** : extrêmes des plus hauts et plus bas (pas des clôtures),
   bougie `t` exclue.
7. **Forme (b)** : semaines de clôture du lundi à clôture du lundi ; la première semaine commence au premier échange de
   l'étape ; frais d'un échange imputés à la semaine qui commence ; liquidation à la dernière clôture de l'étape, frais
   compris, pour la stratégie comme pour la référence ; `f̄` pondérée par la durée.
8. **« Sans la meilleure paire »** : estimation ponctuelle sans les positions de la paire (forme a) ; pour un facteur
   XS, excès moins la contribution de la paire (Σ semaines (poids stratégie − poids référence) × rendement de la paire).
9. **Garde-fous de découverte** : estimation ponctuelle > 0 dans les deux scénarios ; garde-fou « sans la meilleure
   paire » appliqué à toute la forme (a) et aux facteurs XS (sans objet pour les autres formes (b), déclaré au § 5.1).
10. **Validation** : intervalle à 1 − 0,05/m entièrement > 0 ⇔ statistique > 0 et p bilatérale < 0,05/m.
11. **VO11** : `m(p)` = 24 × rendement moyen de la bougie 23:00-24:00 (la référence d'une position d'une heure est ce
    rendement moyen).
12. **TR31, TR32** : machine à deux états démarrée à la première décision de l'étape (« depuis la dernière sortie, ou le
    début de l'étape ») ; elle se recalcule par étape.
13. **Contrôle H0** : H0-a, H0-b et H0-c partagent les tirages d'une réplique (la dérive et les régimes s'ajoutent) ;
    puissance : `δ = g + (statistique à frais nuls − statistique centrale)` de la même réplique et de la même étape ;
    l'« ancienne statistique contre zéro » (descriptif H0-b) ne concerne que la forme (a).
14. **Volume synthétique** : niveau de base 2 × 10⁵ USDT par heure (a priori ; n'influence que le classement du top 40).
15. Les « signaux » comptés pour une règle d'état sont les bougies où l'état est vrai (les transactions sont le compte
    utile).

## Commandes

```bash
csi zoo liste [--familles TR]
csi zoo comptage [--familles TR]            # à blanc : comptes, CPI/NFP, EV02 ; aucun rendement
csi zoo causalite --familles TR             # ≥ 50 instants + signaux de 3 paires × 1 an, mutations
csi zoo controle-h0 --mini 3 --familles TR  # vérification de fonctionnement (jamais inscriptible)
csi zoo controle-h0 --workers 4             # contrôle complet : une fois, 239 tests codés, code relu
csi zoo executer --etape decouverte --executer
csi zoo executer --etape validation --executer
```
