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
   Tesseract s'il est installé. Vote : 2 lectures d'accord sur 3, ou **2 sur 2** sans Tesseract.
3. **Calibrage de l'axe** sur ses graduations grises (droite linéaire ou logarithmique, tirage robuste).
4. **Valeur contre position** : une étiquette ne devient un niveau que si une **ligne horizontale de sa couleur**
   passe à l'ordonnée que prédit sa valeur. Écarte les étiquettes d'indicateurs (moyennes mobiles), l'outil de
   mesure et une erreur de lecture au-delà de 0,1 à 0,4 % du prix (pas celle du dernier chiffre).
5. **Structure** : un seul stop, sous les entrées, elles-mêmes sous des objectifs croissants ; prix courant
   compris entre le stop et le dernier objectif.
6. **Publication de résultat** (outil de mesure « 10.43 (5.2%) ») : jamais source de niveaux.
7. **Figures harmoniques** : niveaux « (12.345) » écrits dans le graphique ; un séparateur douteux (« 14:280 »)
   bloque la lecture.

## Quand l'image est ignorée

`signal_text` ne rend un signal que si **aucune** de ces alertes n'est levée : axe non calibré, désaccord OCR,
étiquette illisible, stop absent ou multiple, aucune entrée, aucun objectif, ordre stop/entrée ou
entrée/objectif faux, prix courant hors plage, deux étiquettes pour une ligne, séparateur douteux, position
incohérente, fichier illisible. Une ligne sans étiquette n'est qu'une remarque.

La **paire** vient d'abord de la légende du message (« #LINK », « LINK/USDT ») ; à défaut, du titre du graphique
(noms complets ramenés au ticker : ChainLink → LINK, Stellar → XLM, Bitcoin → BTC). Si la légende et l'image
donnent deux paires différentes, l'image est ignorée. Un graphique coté en USD, BTC ou ETH est refusé
(autre marché que Binance Spot USDT).

Le signal reconstruit (`#SOL/USDT`, `Entry1`, `TP1…`, `Stop`) passe ensuite par **le même parseur et le même
rejeu** que les messages texte. Dans le bilan, chaque ligne porte `from_image` : on peut comparer les signaux
lus sur image aux signaux texte du même groupe.

## Ce qui a été vérifié

- **Essai du 2026-10-02** sur 28 captures réelles de 3 analystes (26 distinctes) : 7 images de contrôle sur 7
  exactes, **62 niveaux sur 62** ; images sans signal (6) toutes rejetées ; 16 étiquettes d'indicateurs
  écartées. PP-OCR : 0 chiffre faux sur 150 étiquettes ; Tesseract : 2.
- **Port dans CSI, sans Tesseract** (2026-10-03) : mêmes niveaux que l'essai sur les 19 images relues ; la
  figure harmonique « (14:280) » est désormais ignorée (séparateur douteux bloquant).
- **Tests** (`tests/test_chart_ocr.py`) : règles de refus sans OCR ; lecture de l'export avec un lecteur
  factice ; bout en bout sur un graphique **synthétique** (6 niveaux exacts, stop au-dessus de l'entrée refusé,
  étiquette d'indicateur sans ligne écartée, fichier illisible refusé). Les tests de bout en bout sont sautés
  sans l'extra « ocr » (cas de la CI).

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
