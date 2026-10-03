# Signaux publiés en image (lecture locale, sans modèle de langage)

Point 10 du plan de travail (validé par le propriétaire le 2026-10-03). Beaucoup de groupes publient le signal
sous forme de capture TradingView : niveaux en étiquettes colorées sur l'axe des prix, sans texte. CSI sait
maintenant les lire pour **l'audit d'un groupe sur son historique** (`csi audit-telegram --file … --ocr`).
Rien n'est envoyé hors de la machine ; aucune clé, aucun service payant.

Module : `external/chart_ocr.py`. Extra Python : `pip install -e ".[ocr]"` (OpenCV, RapidOCR, onnxruntime ;
Tesseract facultatif). L'image Docker de service ne l'embarque pas.

## Comment l'image est lue

1. **Segmentation par couleur** des étiquettes pleines de l'axe des prix : vert = objectif, bleu = entrée,
   rouge = stop.
2. **Lectures OCR** de chaque étiquette : RapidOCR sur l'image entière, RapidOCR sur la découpe agrandie, et
   Tesseract s'il est installé. Vote : 2 lectures d'accord sur 3, ou **2 sur 2** sans Tesseract. Les deux lectures
   RapidOCR viennent du même modèle : elles ne sont pas deux juges indépendants, d'où les contrôles 4 et 5.
3. **Calibrage de l'axe** sur ses graduations grises (droite linéaire ou logarithmique, tirage robuste).
4. **Valeur contre position** : une étiquette ne devient un niveau que si une **ligne horizontale de sa couleur**
   passe à l'ordonnée que prédit sa valeur. Écarte les étiquettes d'indicateurs (moyennes mobiles), l'outil de
   mesure et une erreur de lecture au-delà de 0,1 à 0,4 % du prix (pas celle du dernier chiffre).
5. **Structure** : un seul stop, sous les entrées, elles-mêmes sous des objectifs croissants ; prix courant
   compris entre le stop et le dernier objectif. Une **ligne de niveau sans étiquette entre le stop et le dernier
   objectif** bloque la lecture : c'est la trace d'une étiquette mal lue (deux lectures d'accord sur une valeur
   fausse la font disparaître, et le signal sortirait sans elle).
6. **Publication de résultat** (outil de mesure « 10.43 (5.2%) ») : jamais source de niveaux.
7. **Figures harmoniques** : niveaux « (12.345) » écrits dans le graphique, relus sur leur découpe (deux
   lectures d'accord, score ≥ 0,8, position à moins d'une tolérance de l'axe) ; un séparateur douteux
   (« 14:280 ») bloque la lecture.

## Quand l'image est ignorée

`signal_text` ne rend un signal que si **aucune** de ces alertes n'est levée : axe non calibré, désaccord OCR,
étiquette illisible, stop absent ou multiple, aucune entrée, aucun objectif, ordre stop/entrée ou
entrée/objectif faux, prix courant hors plage, deux étiquettes pour une ligne, séparateur douteux, position
incohérente, fichier illisible, ligne de niveau sans étiquette dans la plage du signal. Une ligne sans
étiquette HORS de cette plage n'est qu'une remarque.

La **paire** : une paire explicite de la légende (« LINK/USDT », « LINKUSDT ») passe avant le titre du graphique
(noms complets ramenés au ticker : ChainLink → LINK, Stellar → XLM, Bitcoin → BTC) et doit concorder avec lui.
Un « #LINK » seul ne suffit pas : il doit concorder avec la paire lue dans l'image (« #AI », « #TP1 » ne
deviennent jamais une paire). Deux paires ou deux tags : ambigu, image ignorée. Un graphique coté en USD, BTC ou
ETH est refusé (autre marché que Binance Spot USDT).

Le signal reconstruit (`#SOL/USDT`, `Entry1`, `TP1…`, `Stop`) passe ensuite par **le même parseur et le même
rejeu** que les messages texte, avec trois précautions :

- **Bilan à part** : les signaux lus sur image ne comptent **ni dans le bilan du groupe ni dans sa preuve sur
  historique** (taux d'erreur hors échantillon inconnu). Ils ont leur propre bilan (« Signaux lus sur image »),
  à comparer à celui des signaux texte ; la preuve enregistrée dit si l'OCR a servi et combien de signaux image
  ont été exclus.
- **Images ignorées comptées** : une image douteuse reste dans le bilan comme message illisible (« image
  ignorée »), pour que le taux de rejet se voie.
- **Mises à jour** : une réponse à un message (« TP1 ✅ » avec la capture mise à jour) n'est jamais lue ; un
  signal image de même paire et même stop, ou même entrée 1, qu'un signal des 7 jours précédents est un doublon.
  Une image hors du dossier de l'export (chemin absolu ou « .. ») est ignorée ; une image qui fait échouer la
  lecture aussi, sans arrêter l'audit.

## Ce qui a été vérifié

- **Essai du 2026-10-02** sur 28 captures réelles de 3 analystes (26 distinctes) : 7 images de contrôle sur 7
  exactes, **62 niveaux sur 62** ; images sans signal (6) toutes rejetées ; 16 étiquettes d'indicateurs
  écartées. PP-OCR : 0 chiffre faux sur 150 étiquettes ; Tesseract : 2.
- **Port dans CSI, sans Tesseract** (2026-10-03) : mêmes niveaux que l'essai sur les 19 images relues ; la
  figure harmonique « (14:280) » est désormais ignorée (séparateur douteux bloquant).
- **Après la relecture du code** (2026-10-03) : une étiquette mal lue de façon concordante faisait sortir un signal
  faux (objectif 1 ou entrée 1 perdu, démontré sur le graphique synthétique) ; corrigé par le contrôle des lignes
  sans étiquette. Coût mesuré : sur les 9 images correctes de l'essai, 8 restent lues, GALA est refusée (une ligne
  verte sans étiquette dans la plage de son signal). Aucune lecture fausse acceptée.
- **Tests** (`tests/test_chart_ocr.py`) : règles de refus sans OCR ; lecture de l'export avec un lecteur
  factice ; bout en bout sur un graphique **synthétique** (6 niveaux exacts, stop au-dessus de l'entrée refusé,
  étiquette d'indicateur sans ligne écartée, étiquette mal lue refusée, fichier illisible refusé). Les tests de
  bout en bout sont sautés sans l'extra « ocr » (cas de la CI).

**Réserve** : 28 images, 3 analystes, 4 défauts corrigés en voyant les images. Ce n'est **pas** un taux
d'erreur hors échantillon. Sur un nouveau groupe, contrôler à la main quelques signaux lus avant de croire
le bilan.

## Ce qui n'est pas fait

- **F4 n'est pas branché** : c'est un test en direct en cours, gelé ; le modifier casserait son
  pré-enregistrement. Un éventuel F4 bis « images comprises » serait un nouveau test, à déclarer.
- **Tableau de bord** : l'envoi d'un `result.json` ne lit pas les images (l'export doit être fait **avec les
  photos**, et l'API ne reçoit que le JSON). Utiliser le terminal.
- **Prix de l'image contre prix Binance** à l'heure du message, et niveaux multiples du `tickSize` : contrôles
  envisagés, pas encore écrits. Les graphiques MEXC ou OKX peuvent différer légèrement de Binance.
- Secours par un modèle de vision (API payante ou modèle local) : non retenu (point 5 du plan refusé ; Ollama
  absent).
