# Indicateurs dérivés du prix : définitions mécaniques (écrites avant le code)

Mission du 2026-10-03, phase 1.4 (bibliothèque) et base de la phase 11 (détecteur de figures, test F15). Écrit le
2026-10-03 **avant** le code (`src/crypto_signal_intelligence/patterns/`). Chaque définition est mécanique : aucun
paramètre n'est laissé à l'appréciation ; chaque paramètre est fixé ici, une fois, **sans optimisation** (aucun n'a
été choisi en regardant un résultat). Ces indicateurs ne sont utilisés que par la phase 11 ou par de futurs tests
pré-enregistrés séparément ; ils ne font l'objet d'aucun test autonome.

## Conventions communes

- Bougies **clôturées** d'une seule unité de temps, indices `i = 0, 1, …` ; `O, H, L, C, V` ; `TB` = volume de base
  acheté par des ordres au marché (« taker buy base volume » des bougies Binance).
- **Causalité** : chaque résultat porte l'indice de la bougie à la clôture de laquelle il est connu (`known_at`).
  Un résultat n'utilise jamais une bougie d'indice supérieur à `known_at`. Les tests vérifient qu'ajouter ou
  falsifier les bougies suivantes ne change rien à ce qui est connu avant.
- **ATR** (Wilder, 14) : `TR_i = max(H_i − L_i, |H_i − C_{i−1}|, |L_i − C_{i−1}|)` (`TR_0 = H_0 − L_0`) ; la première
  valeur, à `i = 13`, est la moyenne des 14 premiers `TR` ; ensuite `ATR_i = (13 · ATR_{i−1} + TR_i) / 14`. Avant
  `i = 13` : non défini, et tout ce qui en dépend aussi. Justification : le réglage d'origine de Wilder (1978), le
  plus répandu ; non optimisé.
- Égalités : « au-dessus » et « en dessous » sont **stricts** sauf mention contraire.

## 1. Pivots

**Pivot fractal** (k = 2, la « fractale » à 5 bougies de Bill Williams, réglage standard) : la bougie `j` est un
pivot haut si `H_j > H_{j−2}, H_{j−1}, H_{j+1}, H_{j+2}` ; pivot bas si `L_j <` les quatre `L` voisins. Connu à
`j + 2`.

**ZigZag en ATR** (pivots des figures, phase 11) : on suit un extrême courant ; un retournement est confirmé quand le
prix s'éloigne de l'extrême d'au moins `m × ATR` (ATR de la bougie de l'extrême). Montée en cours : le plus haut
courant `Hmax` à l'indice `a` ; dès qu'une bougie `i` a `L_i ≤ Hmax − m · ATR_a`, `a` devient un **pivot haut**
connu à `i`, et l'on suit le plus bas. Symétrique pour la descente. Le premier sens est donné par le premier
mouvement de `m × ATR` depuis le début. Seuils fixés par unité de temps : **m = 3,0 en 1 h, 2,5 en 4 h, 2,0 en
1 jour**. Justification : plus l'unité est courte, plus le bruit est grand rapporté à l'ATR ; ces valeurs visent
des oscillations de quelques jours à quelques semaines, l'échelle des figures publiées par les analystes ; elles
sont fixées a priori et ne seront pas réglées.

*Exemple vérifiable* (1 jour, m = 2, ATR = 1 constant pour l'exemple) : plus hauts 10, 11, 12, 11, 10 et plus bas 9,
10, 11, 10,5, 9,5. À l'indice 1, `H_1 = 11 ≥ 9 + 2` : le plus bas 9 (indice 0) devient pivot bas, connu à 1. Le plus
haut 12 (indice 2) devient pivot haut à l'indice 4, car `L_3 = 10,5 > 12 − 2` mais `L_4 = 9,5 ≤ 10`.

## 2. Fair Value Gap (FVG)

Sur 3 bougies `i − 2, i − 1, i` :
- **FVG haussier** si `L_i > H_{i−2}` ; zone `[H_{i−2}, L_i]` ;
- **FVG baissier** si `H_i < L_{i−2}` ; zone `[H_i, L_{i−2}]`.
Taille minimale : **0,5 × ATR_{i−1}** (un écart plus petit qu'une demi-bougie moyenne est du bruit de cotation).
Connu à `i`. **Comblé** : haussier quand une bougie `j > i` a `L_j ≤ H_{i−2}` (toute la zone revisitée) ; baissier
quand `H_j ≥ L_{i−2}` ; date de comblement = `j`.

*Exemple* : bougies (H, L) = (10, 9), (12, 10,5), (13, 11) et ATR = 1 : FVG haussier `[10 ; 11]`, taille 1 ≥ 0,5 ;
comblé à la première bougie suivante dont le plus bas est ≤ 10.

## 3. Order block (OB)

**OB haussier** : la **dernière bougie baissière** (`C_k < O_k`) avant un déplacement haussier. Déplacement : dans les
**3 bougies** qui suivent `k`, une clôture dépasse `H_k` et le plus haut des clôtures atteint
`C_k + 2 × ATR_k`. Connu à la bougie où les deux conditions sont réunies. Zone : `[L_k, H_k]`. **OB baissier**
symétrique (dernière bougie haussière, clôture sous `L_k`, plus bas des clôtures ≤ `C_k − 2 × ATR_k`).
Justification de **X = 2 ATR en 3 bougies** : un mouvement de deux bougies moyennes en au plus trois bougies est rare
(le « déplacement » de la littérature ICT), et la limite de 3 bougies garde la bougie d'origine liée au mouvement.

*Exemple* : bougie baissière `k` (O 10, C 9, H 10,2, L 8,8, ATR 1), puis clôtures 10, 11,5 : à la deuxième
(`11,5 ≥ 9 + 2` et `> 10,2`), OB haussier `[8,8 ; 10,2]`, connu à `k + 2`.

## 4. Liquidity sweep

**Sweep haussier** (balayage des plus bas) à `i` : `L_i < min(L_{i−20}, …, L_{i−1})` et `C_i > min(L_{i−20}, …,
L_{i−1})` ; **sweep baissier** : `H_i > max(H_{i−20..i−1})` et `C_i < ce maximum`. **N = 20** bougies (environ un
mois en journalier, réglage courant des plus hauts / plus bas de « range »). Connu à `i`.

*Exemple* : plus bas des 20 dernières bougies = 100 ; bougie `i` : L 99, C 101 → sweep haussier.

## 5. Structure : BOS, CHoCH, MSS

À partir des pivots fractals (§ 1) connus : `SH` = dernier pivot haut connu, `SL` = dernier pivot bas connu.
- Une bougie `i` **casse vers le haut** si `C_i > SH` (pivot connu avant `i`) ; vers le bas si `C_i < SL`. Une cassure
  n'est comptée qu'une fois par pivot.
- État de tendance : inconnu au départ ; après une cassure vers le haut il devient haussier, vers le bas baissier.
- **BOS** (break of structure) : cassure dans le sens de la tendance en cours. **CHoCH** (change of character) :
  première cassure contre la tendance (ou la toute première cassure). **MSS** (market structure shift) : un CHoCH
  dont le mouvement de cassure laisse un FVG du même sens (§ 2) parmi les bougies allant du pivot cassé à la cassure.

*Exemple* : tendance baissière, `SH` = 105 ; bougie qui clôture à 106 → CHoCH haussier ; si un FVG haussier s'est
formé depuis le pivot de 105 jusqu'à cette bougie → MSS haussier.

## 6. Premium et discount

Sur le **dernier swing ZigZag** terminé (§ 1 : du dernier pivot connu au pivot opposé précédent) : `bas`, `haut`,
`milieu = (bas + haut) / 2`. Prix au-dessus du milieu : **premium** ; en dessous : **discount** ; zone OTE (optimal
trade entry, d'usage courant) : retracement de 62 % à 79 % du swing.

## 7. CVD (cumulative volume delta)

`delta_i = TB_i − (V_i − TB_i) = 2 · TB_i − V_i` (achats au marché moins ventes au marché, en volume de base, d'après
les bougies Binance). `CVD` = somme des `delta` depuis une ancre (début de la série, ou une bougie donnée). Connu
à `i`. Approximation déclarée : le volume « taker » de la bougie, pas le carnet.

*Exemple* : V = 10 et TB = 7 → delta = 4 ; V = 10 et TB = 3 → delta = −4 ; CVD = 0.

## 8. Profil de volume et VWAP ancré

**Profil de volume** sur une fenêtre de bougies : intervalle `[min L ; max H]` découpé en **50 tranches égales** ; le
volume de chaque bougie est réparti uniformément sur les tranches que couvre `[L ; H]` (au prorata de la longueur
couverte). **POC** : tranche de plus grand volume (en cas d'égalité, la plus basse). **Zone de valeur à 70 %** : en
partant du POC, on ajoute à chaque étape la tranche voisine (au-dessus ou en dessous) de plus grand volume (égalité :
celle du dessus) jusqu'à couvrir au moins 70 % du volume ; bornes = VAL et VAH.

**VWAP ancré** sur le dernier swing majeur (dernier pivot ZigZag connu, § 1) : `Σ P_i · V_i / Σ V_i` depuis la bougie du
pivot, avec `P_i = (H_i + L_i + C_i) / 3`.

*Exemple* : deux bougies, `[L ; H]` = [0 ; 10] volume 10 et [5 ; 10] volume 10, 50 tranches de 0,2 : chaque tranche
de [0 ; 5] reçoit 10 / 50 = 0,2, chaque tranche de [5 ; 10] reçoit 0,2 + 10 / 25 = 0,6 ; POC = la tranche [5 ; 5,2]
(égalité : la plus basse) ; la zone de valeur s'étend vers le haut (tranches voisines de 0,6 contre 0,2) et s'arrête
dès 70 % : 24 tranches, de 5 à 9,8 (14,4 sur 20, soit 72 %).

## 9. Figures du détecteur (phase 11, test F15)

Unités de temps : **1 h, 4 h et 1 jour** seulement (bougies 4 h et 1 jour agrégées à partir des bougies 1 h
clôturées, alignées sur 00:00 UTC). Pivots : ZigZag en ATR (§ 1, m = 3,0 / 2,5 / 2,0). Une figure est **détectée** à
la clôture de la bougie où son dernier pivot nécessaire est connu (ou, pour une cassure, où la cassure a lieu) ;
rien de postérieur n'est lu. Seules les figures **haussières** sont jouées ; les baissières sont inscrites pour
information.

**Tolérance des ratios : ± 5 % relatifs.** Un ratio mesuré `r` « vaut » une valeur publiée `v` si
`0,95 v ≤ r ≤ 1,05 v` ; il est « dans » une plage `[a ; b]` si `0,95 a ≤ r ≤ 1,05 b`.

### 9.1 Harmoniques (Carney ; Gartley, Bat, Butterfly, Crab) et ABCD

Figure haussière : pivots ZigZag consécutifs `X` (bas), `A` (haut), `B` (bas), `C` (haut), connus ; `D` est le
point à venir. `XA = A − X`, `AB = A − B`, `BC = C − B`. Ratios : `B/XA = AB / XA`, `C/AB = BC / AB`.

| Figure | B (part de XA) | C (part de AB) | D : retracement de XA | D : extension de BC |
|---|---|---|---|---|
| Gartley | 0,618 | 0,382 – 0,886 | 0,786 | 1,272 – 1,618 |
| Bat | 0,382 – 0,50 | 0,382 – 0,886 | 0,886 | 1,618 – 2,618 |
| Butterfly | 0,786 | 0,382 – 0,886 | 1,272 | 1,618 – 2,24 |
| Crab | 0,382 – 0,618 | 0,382 – 0,886 | 1,618 | 2,24 – 3,618 |

**Zone de retournement (PRZ)** : intersection de deux intervalles de prix pour `D` : (1) `A − r × XA` pour
`r ∈ [0,95 r_D ; 1,05 r_D]` (retracement de XA) ; (2) `C − e × BC` pour `e` dans la plage d'extension de BC
(± 5 %). Intersection vide : pas de figure. Une figure dont le prix est déjà sous la PRZ à la détection est écartée.

**ABCD** (haussier) : pivots `A` (haut), `B` (bas), `C` (haut) connus, `C/AB ∈ [0,382 ; 0,886]` ; PRZ = intersection
de `C − (A − B) × [0,95 ; 1,05]` (AB = CD) et de `C − e × BC` pour `e ∈ [1,272 ; 1,618]` (± 5 %).

### 9.2 Triangles et biseaux

Ligne haute par les **deux derniers pivots hauts** ZigZag connus, ligne basse par les **deux derniers pivots bas**,
les quatre pivots alternés et la ligne haute au-dessus de la ligne basse sur toute la figure ; lignes
**convergentes** (pente haute < pente basse : elles se croisent après la dernière bougie). Couvre triangles
(symétrique, ascendant, descendant) et biseaux (montant, descendant) ; le type est descriptif, une ligne étant
« plate » si elle varie de moins de 10 % de la hauteur sur la figure. **Cassure haussière** : première clôture
au-dessus de la ligne haute **à partir de la bougie où le 4e pivot devient connu (incluse)**, avant le point de
croisement ; baissière : sous la ligne basse. Une clôture déjà au-dessus avant que le 4e pivot soit connu n'existe pas
pour la figure (elle n'était pas encore formée) : si le prix est encore au-dessus à la confirmation, la cassure est
datée de cette bougie. **Hauteur** = écart entre les deux lignes à l'indice du premier des quatre pivots.

### 9.3 Cassure de ligne de tendance

Ligne **descendante** de résistance par le premier et le dernier de **trois pivots hauts** ZigZag consécutifs connus
(`P1`, `P2`, `P3`, prix décroissants) : `P2` à moins de **0,5 × ATR** de la ligne, et aucun plus haut entre `P1` et
`P3` au-dessus de la ligne de plus de 0,5 × ATR. **Cassure haussière** : première clôture au-dessus de la ligne
à partir de la bougie où `P3` devient connu (incluse). Hauteur = écart entre la ligne et le plus bas atteint entre `P1` et la cassure (mesuré à l'indice de ce
plus bas). Symétrique (ligne montante de support, cassure baissière) pour information.

### 9.4 Configuration ICT/SMC

**Haussière** : un sweep haussier (§ 4) suivi, dans les **10 bougies**, d'un **MSS haussier** (§ 5). Zone d'entrée :
le FVG haussier qui a fait le MSS (le plus récent avant la cassure).

### 9.6 Figures chartistes classiques (ajout du 2026-10-03, écrit avant le code)

Toutes sur les pivots ZigZag de l'unité de temps (§ 1), pivots **consécutifs** (donc alternés) ; « connu » = bougie où
le dernier pivot de la figure est confirmé. **Cassure** : première clôture au-delà de la ligne de cou (ou du bord) **à
partir de la bougie où le dernier pivot est connu (incluse)**, et au plus tard à la bougie où le pivot suivant devient
connu ; sinon pas de figure. Version haussière décrite ; la baissière est le miroir exact (inscrite, jamais jouée).
ATR = ATR de Wilder 14 de la bougie du dernier pivot.

- **Tête-épaules inverse** (`HEAD_SHOULDERS`) : 5 pivots `L1` (bas, épaule gauche), `H1` (haut), `L2` (bas, tête),
  `H2` (haut), `L3` (bas, épaule droite). Tête plus basse que les deux épaules (`L2 < L1`, `L2 < L3`) ; ligne de cou par
  `H1` et `H2` ; profondeur `P` = ligne de cou à l'indice de `L2` − `L2` ; épaules de même niveau : `|L1 − L3| ≤ 0,25 P` ;
  symétrie de durée : `(L3 − L2) / (L2 − L1)` (en bougies) dans `[0,5 ; 2]`. Cassure : clôture au-dessus de la ligne de cou.
- **Double creux** (`DOUBLE`) : 3 pivots `L1` (bas), `H` (haut), `L2` (bas) ; hauteur `P = H − min(L1, L2)` ; creux égaux :
  `|L1 − L2| ≤ 0,10 P` ; au moins **5 bougies** entre `L1` et `L2`. Cassure : clôture au-dessus de `H` (ligne de cou
  horizontale).
- **Drapeau / fanion** (`FLAG`) : 3 pivots `P0` (bas), `P1` (haut), `P2` (bas). Mât `P0 → P1` : hauteur `M = P1 − P0` d'au
  moins **2 m × ATR** (deux fois le seuil du ZigZag) en au plus **10 bougies** ; repli `P1 → P2` d'au plus **50 %** du
  mât, en au plus **2 fois** la durée du mât. Cassure : clôture au-dessus de `P1`. Drapeaux et fanions ne sont pas
  distingués (la forme intérieure du repli est plus fine que le ZigZag) : déclaré.
- **Coupe avec anse** (`CUP_HANDLE`) : 4 pivots `H1` (haut, bord gauche), `L1` (bas, fond), `H2` (haut, bord droit),
  `L2` (bas, anse). Profondeur `P = min(H1, H2) − L1` ; bords égaux : `|H1 − H2| ≤ 0,10 P` ; fond « arrondi » : `L1` dans
  les 60 % centraux de `[H1 ; H2]` (en bougies) ; anse dans la moitié haute de la coupe : `L2 ≥ L1 + 0,5 P` ; coupe au
  moins **3 fois** plus longue que l'anse (`H2 − H1 ≥ 3 (L2 − H2)` en bougies). Cassure : clôture au-dessus de
  `max(H1, H2)`.

Le détecteur de F15 n'utilise aucun des niveaux ni indicateurs du § 10 (rien n'est écrit en ce sens dans son
pré-enregistrement).

### 9.5 Règles de transaction simulée (identiques pour toutes les familles)

| Famille | Ordre limite d'achat | Stop | Objectifs (sortie par tiers) |
|---|---|---|---|
| Harmoniques | haut de la PRZ | sous le plus bas de `X` et de la PRZ, moins 0,25 × ATR | `D + 0,382 AD`, `D + 0,618 AD`, `A` (D = prix d'entrée) |
| ABCD | haut de la PRZ | sous le bas de la PRZ, moins 0,25 × ATR (pas de `X`) | `D + 0,382 AD`, `D + 0,618 AD`, `A` (D = prix d'entrée) |
| Triangles, biseaux | valeur de la ligne haute à la cassure (retest) | valeur de la ligne basse à la cassure | entrée + ⅓, ⅔ et 1 × hauteur |
| Ligne de tendance | valeur de la ligne à la cassure (retest) | plus bas entre `P3` et la cassure, moins 0,25 × ATR | entrée + ⅓, ⅔ et 1 × hauteur |
| ICT/SMC | haut du FVG du MSS | plus bas du sweep, moins 0,25 × ATR | entrée + 1, 2 et 3 R (R = entrée − stop) |
| Tête-épaules inverse | ligne de cou à la cassure (retest) | plus bas de l'épaule droite `L3`, moins 0,25 × ATR | entrée + ⅓, ⅔ et 1 × `P` |
| Double creux | `H` (retest) | `min(L1, L2)` moins 0,25 × ATR | entrée + ⅓, ⅔ et 1 × `P` |
| Drapeau / fanion | `P1` (retest) | `P2` moins 0,25 × ATR | entrée + ⅓, ⅔ et 1 × `M` (mât) |
| Coupe avec anse | `max(H1, H2)` (retest) | bas de l'anse `L2`, moins 0,25 × ATR | entrée + ⅓, ⅔ et 1 × `P` |

- L'ordre limite vaut **20 bougies** de l'unité de temps de la figure, comptées depuis la clôture de la bougie de
  détection (annulé sinon, ou dès que le stop est touché avant l'entrée) ; il est posé à la première minute qui suit
  cette clôture plus la latence et son inscription ; exécuté seulement si le prix **traverse** la limite (bougies 1
  minute, et 1 seconde pour départager une minute ambiguë ; phase 1.5) ; si la première minute ouvre déjà sous la
  limite, il est exécuté à cette ouverture comme un ordre au marché (glissement compté).
- Sortie par **tiers** au premier, deuxième et troisième objectif ; **stop fixe** sur le reste ; durée maximale
  **60 bougies** de l'unité de temps (60 h, 10 jours, 60 jours), comptées en temps depuis l'exécution, le reste vendu
  à la clôture ; minute où le stop et un objectif sont touchés : les secondes décident ; « objectif d'abord » ne prend
  que **ce premier objectif**, le reste sort au stop dans la même minute ; stop d'abord si l'ordre ne peut pas être
  établi (prudence).
- **R** = résultat net / (entrée prévue − stop) : le risque prévu, pas celui du prix d'exécution.
- Une figure dont le stop est au-dessus ou à moins de 0,1 % de l'entrée, ou dont le premier objectif est sous
  l'entrée, est écartée (géométrie invalide), comptée.
- Frais du modèle commun (central et défavorable) : entrée limite (maker, sans glissement), sorties aux objectifs
  limites (maker), stop et sortie à l'échéance au marché (taker, avec glissement).

## 10. Niveaux et indicateurs supplémentaires (ajout au plan du 2026-10-03, écrit avant le code)

Bibliothèque seulement (`patterns/levels.py`, `patterns/indicators.py`) : **aucun test en cours ne les utilise** (F15
ne les cite pas dans son pré-enregistrement) ; un test futur devra les déclarer avant de démarrer. Réglages standard
de chaque indicateur, non optimisés. Sorties alignées sur les bougies : la valeur à l'indice `i` est connue à la
clôture de `i` (sauf mention « connu à »). Les moyennes exponentielles (EMA de période `n`, `α = 2/(n+1)`) partent de
la moyenne simple des `n` premières valeurs ; les lissages de Wilder (RSI, ATR) comme au § conventions.

### 10.1 Niveaux de la période précédente

À partir des bougies 1 h (journées UTC, semaines du lundi 00:00 UTC, mois civils UTC) : pour chaque période **complète**
(toutes ses heures présentes), ouverture, plus haut, plus bas et clôture ; à l'instant `t`, les niveaux « précédents »
sont ceux de la dernière période **terminée** avant `t` : `PDO/PDH/PDL/PDC` (jour), `PWO/PWH/PWL/PWC` (semaine),
`PMO/PMH/PML/PMC` (mois). Une période incomplète n'a pas de niveaux (on ne remonte pas à la précédente).

### 10.2 Sessions

Horaires **UTC fixes** (sans heure d'été, déclaré) : Asie 00:00–08:00, Londres 07:00–16:00, New York 13:00–22:00 (début
inclus, fin exclue). Plus haut et plus bas de chaque session de chaque jour, connus à la fin de la session (toutes
ses heures présentes).

### 10.3 Chiffres ronds

Pour un prix `p > 0`, `k = ⌊log10 p⌋` : niveaux **majeurs** = multiples de `10^k`, niveaux **mineurs** = multiples de
`5 × 10^(k−1)`. On donne le majeur et le mineur immédiatement au-dessus et en dessous de `p` (un niveau égal à `p`
compte comme « au-dessus »). Exemple : `p = 67 300` → `k = 4`, majeurs 60 000 / 70 000, mineurs 65 000 / 70 000.

### 10.4 Indicateurs classiques

- **RSI** (14, Wilder) : gains et pertes moyens lissés à la Wilder, première valeur à `i = 14` ; `RSI = 100 − 100/(1 + G/P)`
  (100 si `P = 0`).
- **Divergences RSI** (régulières seulement) sur pivots fractals (§ 1, k = 2) : **haussière** si deux pivots bas
  fractals consécutifs `j1 < j2` ont `L_j2 < L_j1` et `RSI_j2 > RSI_j1`, avec `j2 − j1` entre 5 et 60 bougies ; connue
  à `j2 + 2`. **Baissière** : miroir sur deux pivots hauts (`H_j2 > H_j1`, `RSI_j2 < RSI_j1`).
- **MACD** (12, 26, 9) : `EMA12 − EMA26`, signal = EMA9 du MACD, histogramme = différence.
- **Stochastique** (14, 3, 3) : `%K brut = 100 (C − min L_14) / (max H_14 − min L_14)` (50 si l'écart est nul),
  `%K` = moyenne simple 3 du brut, `%D` = moyenne simple 3 de `%K`.
- **CCI** (20) : prix typique `TP = (H + L + C)/3`, `CCI = (TP − SMA20(TP)) / (0,015 × écart absolu moyen à la SMA)`.
- **EMA** 20, 50 et 200 des clôtures.
- **Bollinger** (20, 2) : SMA20 ± 2 écarts-types (population) des 20 dernières clôtures.
- **Keltner** (20, 10, 2) : EMA20 des clôtures ± 2 × ATR de Wilder sur 10.
- **Ichimoku** (9, 26, 52) : tenkan = (max H_9 + min L_9)/2 ; kijun = idem sur 26 ; à l'indice `i`, le nuage
  **applicable** est celui calculé 26 bougies plus tôt : `senkou A_i = (tenkan + kijun)_{i−26} / 2`,
  `senkou B_i = (max H_52 + min L_52)_{i−26} / 2` ; la ligne retardée (chikou) n'est donnée que sous la forme causale
  `C_i − C_{i−26}`.
- **Supertrend** (10, 3) : bandes `(H+L)/2 ± 3 × ATR10` (Wilder), bandes finales et sens selon la règle d'origine
  (la bande inférieure ne descend pas tant que la clôture précédente reste au-dessus, et symétriquement ; le sens
  bascule quand la clôture traverse la bande finale opposée).
- **Points pivots** classiques (journaliers, à partir de `PDH`, `PDL`, `PDC`) : `P = (H + L + C)/3`, `R1 = 2P − L`,
  `S1 = 2P − H`, `R2 = P + (H − L)`, `S2 = P − (H − L)`, `R3 = H + 2(P − L)`, `S3 = L − 2(H − P)`.

### 10.5 Flux

- **Ratio achat/vente des takers** par bougie : `TBQ / (QV − TBQ)` (volume en devise de cotation acheté par des
  ordres au marché, sur le reste) ; non défini si le dénominateur est nul.
- **Grosse activité** : une bougie dont le volume en devise de cotation dépasse **5 fois** la moyenne des **20
  bougies précédentes** (hors bougie courante). Les bougies Binance ne donnent pas les transactions une à une : c'est
  une détection sur le volume de la bougie, pas sur une transaction isolée (déclaré).

### 10.6 ICT/SMC avancé

- **Plus hauts (bas) égaux** : deux pivots hauts (bas) fractals consécutifs `j1 < j2` avec `|H_j2 − H_j1| ≤ 0,1 × ATR_j2`
  et au moins 3 bougies d'écart ; niveau de liquidité = le plus haut (bas) des deux, connu à `j2 + 2` ; **pris** à la
  première bougie ultérieure dont le plus haut dépasse (plus bas passe sous) ce niveau.
- **Zone OTE** : retracement de 62 à 79 % du dernier swing (§ 6, déjà défini).
- **Breaker block** : un order block (§ 3) **invalidé** : OB haussier dont une clôture ultérieure passe sous le bas de
  sa zone → breaker baissier (même zone), connu à cette bougie ; miroir pour l'OB baissier.
- **Mitigation** (définition simplifiée, déclarée) : première bougie ultérieure à `known_at` qui revient dans la zone
  d'un OB non invalidé (plus bas ≤ haut de la zone pour un OB haussier, plus haut ≥ bas de la zone pour un baissier).

## Historique

- 2026-10-03 : définitions écrites avant le code (mission, phase 1.4) ; § 9 (figures du détecteur, phase 11) écrit
  avant le code du détecteur.
- 2026-10-03, avant le démarrage de F15 (relecture) : cassure des triangles et des lignes de tendance comptée dès la
  bougie qui confirme le dernier pivot (le code partait de la suivante et datait en retard ~40 % des triangles) ;
  type de triangle avec une tolérance de 10 % (« ascendant » et « descendant » n'arrivaient jamais) ; stop des ABCD
  écrit ; minute ambiguë « objectif d'abord » limitée au premier objectif ; R au risque prévu ; ordre exécutable dès
  la pose compté au marché ; échéance en temps.
