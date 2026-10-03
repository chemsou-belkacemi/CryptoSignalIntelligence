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
(symétrique, ascendant, descendant) et biseaux (montant, descendant). **Cassure haussière** : première clôture au-dessus
de la ligne haute, avant le point de croisement ; baissière : sous la ligne basse. **Hauteur** = écart entre les deux
lignes à l'indice du premier des quatre pivots.

### 9.3 Cassure de ligne de tendance

Ligne **descendante** de résistance par le premier et le dernier de **trois pivots hauts** ZigZag consécutifs connus
(`P1`, `P2`, `P3`, prix décroissants) : `P2` à moins de **0,5 × ATR** de la ligne, et aucun plus haut entre `P1` et
`P3` au-dessus de la ligne de plus de 0,5 × ATR. **Cassure haussière** : première clôture au-dessus de la ligne
après `P3`. Hauteur = écart entre la ligne et le plus bas atteint entre `P1` et la cassure (mesuré à l'indice de ce
plus bas). Symétrique (ligne montante de support, cassure baissière) pour information.

### 9.4 Configuration ICT/SMC

**Haussière** : un sweep haussier (§ 4) suivi, dans les **10 bougies**, d'un **MSS haussier** (§ 5). Zone d'entrée :
le FVG haussier qui a fait le MSS (le plus récent avant la cassure).

### 9.5 Règles de transaction simulée (identiques pour toutes les familles)

| Famille | Ordre limite d'achat | Stop | Objectifs (sortie par tiers) |
|---|---|---|---|
| Harmoniques, ABCD | haut de la PRZ | sous le plus bas de `X` et de la PRZ, moins 0,25 × ATR | `D + 0,382 AD`, `D + 0,618 AD`, `A` (D = prix d'entrée) |
| Triangles, biseaux | valeur de la ligne haute à la cassure (retest) | valeur de la ligne basse à la cassure | entrée + ⅓, ⅔ et 1 × hauteur |
| Ligne de tendance | valeur de la ligne à la cassure (retest) | plus bas entre `P3` et la cassure, moins 0,25 × ATR | entrée + ⅓, ⅔ et 1 × hauteur |
| ICT/SMC | haut du FVG du MSS | plus bas du sweep, moins 0,25 × ATR | entrée + 1, 2 et 3 R (R = entrée − stop) |

- L'ordre limite vaut **20 bougies** de l'unité de temps de la figure (annulé sinon, ou dès que le stop est touché
  avant l'entrée) ; exécuté seulement si le prix **traverse** la limite (bougies 1 minute, et 1 seconde pour départager
  une minute ambiguë ; phase 1.5).
- Sortie par **tiers** au premier, deuxième et troisième objectif ; **stop fixe** sur le reste ; durée maximale
  **60 bougies** de l'unité de temps (60 h, 10 jours, 60 jours), le reste vendu à la clôture ; dans une même minute,
  stop avant objectif si l'ordre ne peut pas être établi (prudence).
- Une figure dont le stop est au-dessus ou à moins de 0,1 % de l'entrée, ou dont le premier objectif est sous
  l'entrée, est écartée (géométrie invalide), comptée.
- Frais du modèle commun (central et défavorable) : entrée limite (maker, sans glissement), sorties aux objectifs
  limites (maker), stop et sortie à l'échéance au marché (taker, avec glissement).

## Historique

- 2026-10-03 : définitions écrites avant le code (mission, phase 1.4) ; § 9 (figures du détecteur, phase 11) écrit
  avant le code du détecteur.
