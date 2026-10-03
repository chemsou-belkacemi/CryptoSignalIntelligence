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
10, 11, 10, 9,5 : le plus haut 12 (indice 2) devient pivot haut à l'indice 4, car `L_4 = 9,5 ≤ 12 − 2`.

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
(égalité : la plus basse) ; la zone de valeur couvre les 25 tranches de [5 ; 10] (75 % du volume) plus rien d'autre.

## Historique

- 2026-10-03 : définitions écrites avant le code (mission, phase 1.4).
