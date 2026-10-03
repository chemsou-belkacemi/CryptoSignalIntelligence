"""Lecture des signaux publiés en IMAGE (captures de graphiques TradingView) : niveaux d'entrée, stop et objectifs.

Porté de l'essai du 2026-10-02 (docs/OCR.md : 62 niveaux exacts sur 62, 7 images, 3 analystes, mesuré DANS
l'échantillon qui a servi à régler la méthode : ce n'est pas un taux d'erreur), sans modèle de langage, entièrement
local (OpenCV, RapidOCR ; Tesseract facultatif) :
 1. segmentation par couleur des étiquettes pleines de l'axe des prix (vert = objectif, bleu = entrée, rouge = stop) ;
 2. lectures OCR de chaque étiquette (RapidOCR sur l'image entière, RapidOCR sur la découpe, Tesseract si présent) ;
 3. lignes horizontales de même couleur dans le graphique ;
 4. calibrage de l'axe des prix sur ses graduations (linéaire ou logarithmique) ;
 5. garde-fous : une étiquette ne devient un niveau que si une ligne de sa couleur passe à l'ordonnée prédite par sa
    valeur ; vote des lectures ; ordre stop < entrées < objectifs ; une publication de RÉSULTAT (outil de mesure) n'est
    jamais source de niveaux ;
 6. figures harmoniques : niveaux « (12.345) » écrits dans le graphique.
Au moindre doute, `signal_text` ne rend rien : l'image n'est pas lue, jamais devinée.
"""
from __future__ import annotations

import importlib
import re
import time
from pathlib import Path
from typing import Any

import numpy as np


def _optional(name: str) -> Any:
    """OpenCV, RapidOCR et Tesseract sont facultatifs (extra « ocr ») : sans eux, aucune image n'est lue."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


cv2: Any = _optional("cv2")


def available() -> bool:
    """Vrai si OpenCV et RapidOCR sont installés (Tesseract reste facultatif)."""
    return cv2 is not None and _optional("rapidocr") is not None

HARMONIC_MIN_SCORE = 0.8
RE_NOMBRE = re.compile(r"^\d+\.\d+$|^\d+$")
RE_MILLIERS = re.compile(r"^\d{1,3}(,\d{3})+\.\d+$")


def normalise(txt: str) -> str:
    """« 82,563.73 » -> « 82563.73 » (seulement si le motif des milliers est exact)."""
    t = txt.strip().replace(" ", "")
    return t.replace(",", "") if RE_MILLIERS.match(t) else t
RE_COMPTE = re.compile(r"^\d{1,2}:\d{2}(:\d{2})?$")
RE_PAREN = re.compile(r"^\((\d+)([.:,])(\d+)\)$")
RE_MESURE = re.compile(r"\d+(?:\.\d+)?\s*\(\s*\d+(?:\.\d+)?%\)")


def masques(hsv: np.ndarray) -> dict[str, np.ndarray]:
    h, s, v = (hsv[..., k].astype(np.int16) for k in range(3))
    return {
        "vert": (h >= 45) & (h <= 80) & (s >= 90) & (v >= 120),
        "bleu": (h >= 100) & (h <= 124) & (s >= 150) & (v >= 170),
        "rouge": ((h <= 6) | (h >= 172)) & (s >= 165) & (v >= 170),
        "orange": (h >= 8) & (h <= 28) & (s >= 150) & (v >= 170),
        "rose": ((h <= 6) | (h >= 172)) & (s >= 90) & (s < 165) & (v >= 170),
        "blanc": (s <= 40) & (v >= 235),
        "gris": (s <= 40) & (v >= 130) & (v < 235),
    }


def masques_lignes(hsv: np.ndarray) -> dict[str, np.ndarray]:
    h, s, v = (hsv[..., k].astype(np.int16) for k in range(3))
    return {
        "vert": (h >= 50) & (h <= 78) & (s >= 60) & (v >= 95),
        "bleu": (h >= 104) & (h <= 120) & (s >= 130) & (v >= 135),
        "rouge": ((h <= 6) | (h >= 168)) & (s >= 80) & (v >= 115),
    }


def classe_fond(hsv_box: np.ndarray) -> tuple[str, float]:
    """Classe de couleur majoritaire d'une boîte de texte (le texte lui-même est minoritaire)."""
    if hsv_box.size == 0:
        return "fond", 0.0
    m = masques(hsv_box)
    parts = {k: float(val.mean()) for k, val in m.items()}
    # le texte blanc d'une étiquette colorée ne doit pas la faire passer pour « blanc »
    for k in ("vert", "bleu", "rouge", "orange", "rose"):
        if parts[k] >= 0.35:
            return k, parts[k]
    for k in ("blanc", "gris"):
        if parts[k] >= 0.45:
            return k, parts[k]
    return "fond", 1.0 - sum(parts.values())


def etiquettes_par_couleur(img: np.ndarray, hsv: np.ndarray) -> list[dict]:
    """Segmentation : rectangles pleins vert/bleu/rouge dans la colonne de l'axe des prix."""
    H, W = img.shape[:2]
    out = []
    kv = max(7, int(round(0.006 * H)))
    for coul in ("vert", "bleu", "rouge"):
        m = masques(hsv)[coul].astype(np.uint8) * 255
        m[:, : int(0.55 * W)] = 0
        # fermeture : rebouche le texte blanc ; ouverture verticale : supprime les lignes fines, garde les étiquettes
        kf = max(9, int(round(0.009 * H)))
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (kf, kf)))
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, kv)))
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            if w < 0.06 * W or w > 0.30 * W or h < 0.011 * H or h > 0.12 * H:
                continue
            if (x + w / 2) < 0.70 * W:
                continue
            plein = cv2.contourArea(c) / float(w * h)
            if plein < 0.80:
                continue
            out.append({"couleur": coul, "x": x, "y": y, "w": w, "h": h})
    return out


def lignes_de_texte(img: np.ndarray, r: dict) -> list[tuple[int, int]]:
    """Découpe une étiquette (éventuellement plusieurs étiquettes collées) en lignes de texte blanc."""
    crop = img[r["y"] : r["y"] + r["h"], r["x"] : r["x"] + r["w"]]
    mn = crop.min(axis=2)
    texte = mn >= 200
    rows = texte.sum(axis=1) >= 2
    runs, start = [], None
    for i, on in enumerate(rows):
        if on and start is None:
            start = i
        if not on and start is not None:
            runs.append((start, i))
            start = None
    if start is not None:
        runs.append((start, len(rows)))
    return [(a, b) for a, b in runs if b - a >= max(6, int(0.25 * min(r["h"], 60)))]


def prepare(crop: np.ndarray) -> np.ndarray:
    """Texte blanc sur fond coloré -> texte noir sur fond blanc, agrandi (canal minimum)."""
    mn = crop.min(axis=2)
    inv = 255 - mn
    inv = cv2.normalize(inv, None, 0, 255, cv2.NORM_MINMAX)
    f = max(2.0, 96.0 / max(crop.shape[0], 1))
    big = cv2.resize(inv, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    return cv2.copyMakeBorder(big, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)


def ajuste_axe(pts: list[tuple[float, float]], tol: float) -> dict | None:
    """y -> prix. Essaie linéaire et logarithmique (RANSAC sur les paires), garde le meilleur."""
    pts = [(y, v) for y, v in pts if v > 0]
    if len(pts) < 4:
        return None
    ys = np.array([p[0] for p in pts], float)
    meilleur: dict[str, Any] | None = None
    for mode in ("lin", "log"):
        vs = np.array([p[1] for p in pts], float)
        vs = np.log(vs) if mode == "log" else vs
        best = None
        n = len(pts)
        for i in range(n):
            for j in range(i + 1, n):
                if abs(ys[i] - ys[j]) < 30:
                    continue
                a = (vs[j] - vs[i]) / (ys[j] - ys[i])
                if a >= 0:
                    continue
                b = vs[i] - a * ys[i]
                inl = np.abs((vs - b) / a - ys) <= tol
                if best is None or inl.sum() > best.sum():
                    best = inl
        if best is None or best.sum() < 4:
            continue
        a, b = np.polyfit(ys[best], vs[best], 1)
        res = np.abs((vs[best] - b) / a - ys[best])
        cand: dict[str, Any] = {"mode": mode, "a": float(a), "b": float(b), "n": int(best.sum()), "n_total": n,
                "residu_px_max": float(res.max()), "residu_px_moy": float(res.mean())}
        if meilleur is None or (cand["n"], -cand["residu_px_moy"]) > (meilleur["n"], -meilleur["residu_px_moy"]):
            meilleur = cand
    return meilleur


def y_de(axe: dict, prix: float) -> float:
    v = np.log(prix) if axe["mode"] == "log" else prix
    return float((v - axe["b"]) / axe["a"])


def prix_de(axe: dict, y: float) -> float:
    v = axe["a"] * y + axe["b"]
    return float(np.exp(v)) if axe["mode"] == "log" else float(v)


def lignes_horizontales(hsv: np.ndarray, x_max: int) -> list[dict]:
    """Lignes horizontales fines par couleur. Les zones pleines (rectangles) sont retirées avant ;
    une ligne interrompue (bougie, outil de mesure) est reconstituée en additionnant ses segments."""
    H, W = hsv.shape[:2]
    out = []
    lmin = int(0.05 * W)
    lseg = max(12, int(0.02 * W))
    ep_max = max(6, int(0.006 * H))
    for coul, m in masques_lignes(hsv).items():
        m = m[:, :x_max].astype(np.uint8)
        epais = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, ep_max + 3)))
        epais = cv2.dilate(epais, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
        fin = m & (1 - epais)
        fin = cv2.morphologyEx(fin, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (21, 1)))
        rows = []
        for y in range(H):
            row = fin[y]
            if int(row.sum()) < lmin:
                continue
            d = np.diff(np.concatenate(([0], row, [0])).astype(np.int16))
            st, en = np.where(d == 1)[0], np.where(d == -1)[0]
            keep = (en - st) >= lseg
            if not keep.any():
                continue
            total = int((en - st)[keep].sum())
            if total >= lmin:
                rows.append((y, int(st[keep].min()), int(en[keep].max()), total))
        groupe: list[tuple[int, int, int, int]] = []
        for r in rows + [(10**9, 0, 0, 0)]:
            if groupe and r[0] - groupe[-1][0] > 1:
                ep = groupe[-1][0] - groupe[0][0] + 1
                if ep <= ep_max:
                    out.append({"couleur": coul, "y": float(np.mean([g[0] for g in groupe])), "epaisseur": ep,
                                "x0": min(g[1] for g in groupe), "x1": max(g[2] for g in groupe),
                                "longueur": max(g[3] for g in groupe)})
                groupe = []
            groupe.append(r)
    return out


class Lecteur:
    """Lecteur d'images de signaux. Tesseract est facultatif : sans lui, deux lectures RapidOCR doivent être d'accord
    (2 sur 2) au lieu de 2 sur 3."""

    def __init__(self, tessdata: str | None = None) -> None:
        from rapidocr import RapidOCR
        self.rapid: Any = RapidOCR()
        self.rapid_rec: Any = RapidOCR()  # instance séparée : l'appel « reconnaissance seule » modifie l'état de l'objet
        self.tess: Any = None
        try:
            import tesserocr
            self.tess = tesserocr.PyTessBaseAPI(path=tessdata or "", lang="eng", psm=tesserocr.PSM.SINGLE_LINE,
                                                oem=tesserocr.OEM.LSTM_ONLY)
            self.tess.SetVariable("tessedit_char_whitelist", "0123456789.,:")
        except Exception:  # noqa: BLE001 - Tesseract absent : deux lectures seulement, accord exigé
            self.tess = None

    def tesseract(self, gris: np.ndarray) -> tuple[str, int]:
        if self.tess is None:
            return "", 0
        from PIL import Image
        self.tess.SetImage(Image.fromarray(gris))
        return normalise(self.tess.GetUTF8Text()), int(self.tess.MeanTextConf())

    def rapid_decoupe(self, crop_bgr: np.ndarray) -> tuple[str, float]:
        f = max(1.0, 64.0 / max(crop_bgr.shape[0], 1))
        big = cv2.resize(crop_bgr, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
        r = self.rapid_rec(big, use_det=False, use_cls=False, use_rec=True)
        if not r.txts:
            return "", 0.0
        return normalise(r.txts[0]), float(r.scores[0])

    def analyse(self, chemin: Path) -> dict:
        t0 = time.time()
        img = cv2.imread(str(chemin))
        if img is None:
            return {"image": Path(chemin).name, "alertes": ["IMAGE_ILLISIBLE"], "signal": {"entrees": [], "stop": [], "objectifs": []},
                    "symbole": None, "resultat_mesure": False, "famille": None}
        H, W = img.shape[:2]
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        rep: dict = {"image": chemin.name, "taille": [W, H], "alertes": []}

        # --- OCR image entière
        t = time.time()
        res = self.rapid(img)
        rep["t_ocr_entier_s"] = round(time.time() - t, 2)
        boites = []
        if res.boxes is not None:
            for box, txt, sc in zip(res.boxes, res.txts, res.scores, strict=False):
                xs = [float(p[0]) for p in box]
                ys = [float(p[1]) for p in box]
                b = {"x0": min(xs), "y0": min(ys), "x1": max(xs), "y1": max(ys), "txt": normalise(txt), "brut": txt.strip(), "score": float(sc)}
                b["yc"] = (b["y0"] + b["y1"]) / 2
                x0, y0, x1, y1 = (int(round(b[k])) for k in ("x0", "y0", "x1", "y1"))
                b["classe"], b["part"] = classe_fond(hsv[max(y0, 0):y1, max(x0, 0):x1])
                boites.append(b)
        textes = " | ".join(b["txt"] for b in boites)
        rep["resultat_mesure"] = bool(RE_MESURE.search(textes))

        # --- colonne de l'axe : boîtes numériques sur fond sombre, à droite
        axe_boites = [b for b in boites if b["x0"] >= 0.70 * W]
        grad = [b for b in axe_boites if b["classe"] == "fond" and RE_NOMBRE.match(b["txt"]) and "." in b["txt"]]
        # décimales attendues = mode du nombre de décimales des graduations
        tol = max(4.0, 0.004 * H)
        axe = ajuste_axe([(b["yc"], float(b["txt"])) for b in grad], tol)
        rep["axe"] = axe
        if axe is None:
            rep["alertes"].append("AXE_NON_CALIBRE")

        # --- prix courant : boîte numérique juste au-dessus d'un compte à rebours
        prix_courant = None
        for c in axe_boites:
            if RE_COMPTE.match(c["txt"]):
                au_dessus = [b for b in axe_boites if RE_NOMBRE.match(b["txt"]) and c["y0"] - b["y1"] + 0.6 * (b["y1"] - b["y0"]) >= 0 and abs(c["y0"] - b["y1"]) <= 0.8 * (b["y1"] - b["y0"])]
                if au_dessus:
                    b = min(au_dessus, key=lambda b: abs(c["y0"] - b["y1"]))
                    prix_courant = float(b["txt"])
                    b["prix_courant"] = True
        rep["prix_courant"] = prix_courant

        # --- (1) étiquettes par segmentation de couleur, (2) OCR des découpes
        t = time.time()
        rects = etiquettes_par_couleur(img, hsv)
        etiqs = []
        for r in rects:
            for a, b_ in lignes_de_texte(img, r):
                y0 = r["y"] + max(a - 3, 0)
                y1 = r["y"] + min(b_ + 3, r["h"])
                crop = img[y0:y1, r["x"] + 2 : r["x"] + r["w"] - 2]
                g = prepare(crop)
                t_txt, t_conf = self.tesseract(g)
                r_txt, r_sc = self.rapid_decoupe(crop)
                yc = (y0 + y1) / 2
                # lecture « image entière » correspondante
                cand = [b for b in boites if b["x1"] > r["x"] and b["x0"] < r["x"] + r["w"] and abs(b["yc"] - yc) <= 0.5 * (y1 - y0)]
                e_txt = min(cand, key=lambda b: abs(b["yc"] - yc))["txt"] if cand else ""
                e_pc = bool(cand and min(cand, key=lambda b: abs(b["yc"] - yc)).get("prix_courant"))
                etiqs.append({"couleur": r["couleur"], "x": r["x"], "y": yc, "h": y1 - y0, "tesseract": t_txt, "tess_conf": t_conf,
                              "rapid_decoupe": r_txt, "rapid_score": round(r_sc, 3), "rapid_entier": e_txt, "prix_courant": e_pc})
        rep["t_etiquettes_s"] = round(time.time() - t, 2)

        # --- (3) lignes horizontales
        x_axe = int(np.median([e["x"] for e in etiqs])) if etiqs else int(min([b["x0"] for b in grad], default=0.8 * W) - 8)
        lignes = lignes_horizontales(hsv, x_axe - 1)
        rep["lignes"] = lignes

        # --- (4)(5) niveaux : vote OCR + ligne à l'ordonnée prédite (appariement un pour un)
        m_ent = re.search(r"(?:Binance|MEXC|OKX|Bybit|KuCoin|Gate)\s*(\d+\.\d+)", textes)
        prix_entete = m_ent.group(1) if m_ent else None
        rep["prix_entete"] = prix_entete
        niveaux = []
        for e in etiqs:
            lectures = [e["rapid_entier"], e["rapid_decoupe"], e["tesseract"]]
            valides = [x for x in lectures if RE_NOMBRE.match(x) and "." in x]
            if RE_COMPTE.match(e["rapid_entier"]) or RE_COMPTE.match(e["rapid_decoupe"]):
                continue  # compte à rebours
            if not valides:
                rep["alertes"].append(f"ETIQUETTE_ILLISIBLE y={e['y']:.0f} {lectures}")
                continue
            val = max(set(valides), key=valides.count)
            accord = valides.count(val)
            lecteurs = 3 if self.tess is not None else 2
            n = {"couleur": e["couleur"], "valeur": val, "accord_ocr": f"{accord}/{lecteurs}", "lectures": lectures,
                 "y_etiquette": round(e["y"], 1), "role": "SANS_LIGNE"}
            if e["prix_courant"] or (prix_entete is not None and val == prix_entete.rstrip(".")):
                n["role"] = "PRIX_COURANT"
            elif axe is not None:
                n["y_predit"] = round(y_de(axe, float(val)), 1)
            else:
                n["role"] = {"vert": "TP", "bleu": "ENTREE", "rouge": "STOP"}[e["couleur"]] + "?"
            if accord < 2:                               # 2 sur 3 avec Tesseract, 2 sur 2 sans
                rep["alertes"].append(f"DESACCORD_OCR {val} {lectures}")
            niveaux.append(n)
        lignes_axe = [ln for ln in lignes if ln["x1"] >= x_axe - 0.04 * W]
        orphelines: list[tuple[str, float]] = []
        if axe is not None:
            for ln in lignes_axe:
                cands = [n for n in niveaux if n["couleur"] == ln["couleur"] and "y_predit" in n and abs(ln["y"] - n["y_predit"]) <= tol]
                if not cands:
                    if ln["longueur"] < 0.15 * W:
                        continue
                    orphelines.append((ln["couleur"], prix_de(axe, ln["y"])))
                    rep["alertes"].append(f"LIGNE_SANS_ETIQUETTE {ln['couleur']} y={ln['y']:.0f} prix~{prix_de(axe, ln['y']):.6g}")
                    continue
                n = min(cands, key=lambda n: abs(ln["y"] - n["y_predit"]))
                if "ligne_y" in n and abs(n["ligne_y"] - n["y_predit"]) <= abs(ln["y"] - n["y_predit"]):
                    continue
                n["ligne_y"] = round(ln["y"], 1)
                n["ecart_px"] = round(ln["y"] - n["y_predit"], 1)
                n["prix_selon_ligne"] = prix_de(axe, ln["y"])
                n["role"] = {"vert": "TP", "bleu": "ENTREE", "rouge": "STOP"}[n["couleur"]]
                for autre in cands:
                    if autre is not n:
                        rep["alertes"].append(f"DEUX_ETIQUETTES_POUR_UNE_LIGNE {n['valeur']} / {autre['valeur']}")
        rep["niveaux"] = niveaux

        # --- (6) niveaux entre parenthèses (figures harmoniques)
        harmo = []
        lm = masques_lignes(hsv)
        hh, ss, vv = (hsv[..., k].astype(np.int16) for k in range(3))
        cyan = (hh >= 84) & (hh <= 102) & (ss >= 90) & (vv >= 120)
        for b in boites:
            m = RE_PAREN.match(b["txt"].replace(" ", ""))
            if not m or b["x0"] >= x_axe:
                continue
            x0, y0, x1, y1 = (int(round(b[k])) for k in ("x0", "y0", "x1", "y1"))
            pc = float(cyan[y0:y1, x0:x1].mean())
            pr = float(lm["rouge"][y0:y1, x0:x1].mean())
            coul = "cyan" if pc > pr and pc > 0.03 else ("rouge" if pr > 0.03 else "autre")
            # trait horizontal à droite du texte (objectif) ?
            yc = int(round(b["yc"]))
            msk = cyan if coul == "cyan" else lm["rouge"]
            larg = int(0.08 * W)
            bd = msk[max(yc - 6, 0): yc + 7, x1 + 4: min(x1 + 4 + larg, x_axe)]
            bg = msk[max(yc - 6, 0): yc + 7, max(x0 - 4 - larg, 0): max(x0 - 4, 0)]
            # objectif = petit trait des DEUX côtés du texte ; borne de zone = trait d'un seul côté (bord de la boîte)
            trait_droite = bool(bd.size and bd.any(axis=0).mean() >= 0.5) and bool(bg.size and bg.any(axis=0).mean() >= 0.5)
            val = f"{m.group(1)}.{m.group(3)}"
            relu, _sc = self.rapid_decoupe(img[max(y0 - 2, 0):y1 + 2, max(x0 - 2, 0):x1 + 2])
            m2 = RE_PAREN.match(relu.replace(" ", ""))
            if m2 is None or f"{m2.group(1)}.{m2.group(3)}" != val or b["score"] < HARMONIC_MIN_SCORE:
                rep["alertes"].append(f"DESACCORD_OCR harmonique {b['txt']} / {relu} (score {b['score']:.2f})")
            h = {"valeur": val, "couleur": coul, "trait_a_droite": trait_droite, "y": round(b["yc"], 1), "score": round(b["score"], 3)}
            if m.group(2) != ".":
                rep["alertes"].append(f"SEPARATEUR_DOUTEUX {b['txt']} lu comme {val}")
            if axe is not None:
                h["y_predit"] = round(y_de(axe, float(val)), 1)
                h["ecart_px"] = round(b["yc"] - h["y_predit"], 1)
                if abs(h["ecart_px"]) > tol:
                    rep["alertes"].append(f"POSITION_INCOHERENTE {val} (écart {h['ecart_px']} px)")
            h["role"] = "STOP" if coul == "rouge" else ("TP" if trait_droite else "ZONE_ENTREE")
            harmo.append(h)
        rep["harmonique"] = harmo

        # --- synthèse + garde-fous d'ordre
        if harmo:
            tp = sorted(float(h["valeur"]) for h in harmo if h["role"] == "TP")
            en = sorted(float(h["valeur"]) for h in harmo if h["role"] == "ZONE_ENTREE")
            st = sorted(float(h["valeur"]) for h in harmo if h["role"] == "STOP")
            rep["famille"] = "HARMONIQUE"
        else:
            tp = sorted(float(n["valeur"]) for n in niveaux if n["role"] == "TP")
            en = sorted(float(n["valeur"]) for n in niveaux if n["role"] == "ENTREE")
            st = sorted(float(n["valeur"]) for n in niveaux if n["role"] == "STOP")
            rep["famille"] = "ETIQUETTES"
        rep["signal"] = {"entrees": en, "stop": st, "objectifs": tp}
        if len(st) != 1:
            rep["alertes"].append(f"STOP_NON_UNIQUE ({len(st)})")
        if not en:
            rep["alertes"].append("AUCUNE_ENTREE")
        if not tp:
            rep["alertes"].append("AUCUN_OBJECTIF")
        if st and en and not max(st) < min(en):
            rep["alertes"].append("ORDRE_STOP_ENTREE")
        if en and tp and not max(en) < min(tp):
            rep["alertes"].append("ORDRE_ENTREE_OBJECTIF")
        if prix_courant and en and st and tp and not (min(st) < prix_courant < max(tp)):
            rep["alertes"].append("PRIX_COURANT_HORS_PLAGE")
        # Une ligne de niveau sans étiquette DANS la plage du signal veut dire qu'une étiquette a pu être mal lue
        # (deux lectures d'accord sur une valeur fausse la font disparaître) : bloquant.
        if st and tp:
            for couleur, prix in orphelines:
                if min(st) <= prix <= max(tp):
                    rep["alertes"].append(f"LIGNE_ORPHELINE_DANS_LE_SIGNAL {couleur} prix~{prix:.6g}")
        # symbole
        ms = re.search(r"([A-Za-z0-9]{2,12})\s*/\s*(TetherUS|USDT)", textes)
        mb = re.search(r"\b([A-Z0-9]{2,10}USDT?)\b", textes)
        rep["symbole"] = (ms.group(1).upper() + "/USDT") if ms else (mb.group(1) if mb else None)
        mx = re.search(r"(Binance|MEXC|OKX|Bybit|KuCoin|Gate)", textes, re.I)
        rep["bourse"] = mx.group(1) if mx else None
        rep["t_total_s"] = round(time.time() - t0, 2)
        return rep




#: Alertes qui interdisent d'utiliser une lecture (l'image est alors ignorée, jamais devinée).
BLOCKING = ("AXE_NON_CALIBRE", "DESACCORD_OCR", "ETIQUETTE_ILLISIBLE", "STOP_NON_UNIQUE", "AUCUNE_ENTREE", "AUCUN_OBJECTIF",
            "ORDRE_STOP_ENTREE", "ORDRE_ENTREE_OBJECTIF", "PRIX_COURANT_HORS_PLAGE", "DEUX_ETIQUETTES_POUR_UNE_LIGNE",
            "SEPARATEUR_DOUTEUX", "POSITION_INCOHERENTE", "IMAGE_ILLISIBLE", "LIGNE_ORPHELINE_DANS_LE_SIGNAL")


#: Noms complets affichés par TradingView à la place du ticker (essai : « ChainLink », « Stellar », « Bitcoin »).
NAME_TO_TICKER = {"BITCOIN": "BTC", "ETHEREUM": "ETH", "CHAINLINK": "LINK", "STELLAR": "XLM", "SOLANA": "SOL",
                  "AVALANCHE": "AVAX", "CARDANO": "ADA", "POLKADOT": "DOT", "DOGECOIN": "DOGE", "LITECOIN": "LTC",
                  "RIPPLE": "XRP", "TRON": "TRX", "UNISWAP": "UNI", "COSMOS": "ATOM", "POLYGON": "POL", "TONCOIN": "TON",
                  "SHIBAINU": "SHIB", "BITCOINCASH": "BCH", "FILECOIN": "FIL", "INTERNETCOMPUTER": "ICP", "AAVE": "AAVE",
                  "ARBITRUM": "ARB", "OPTIMISM": "OP", "SUI": "SUI", "APTOS": "APT", "BINANCECOIN": "BNB", "BNB": "BNB"}
CAPTION_TAG = re.compile(r"#([A-Z0-9]{2,12})\b")


def ticker(raw: str | None) -> str | None:
    """« LINK/USDT », « LINKUSDT », « ChainLink/USDT » → « LINKUSDT » ; une cotation autre que l'USDT → None."""
    if not raw:
        return None
    text = re.sub(r"[^A-Z0-9/]", "", raw.upper())
    base, _, quote = text.partition("/")
    if not quote:
        if text.endswith("USDT"):
            base, quote = text[:-4], "USDT"
        elif text.endswith(("USD", "USDC", "BTC", "ETH")):
            return None
        else:
            base, quote = text, "USDT"
    if quote != "USDT" or not base:
        return None
    return NAME_TO_TICKER.get(base, base) + "USDT"


def caption_pairs(text: str | None) -> tuple[set[str], set[str]]:
    """Paires EXPLICITES de la légende (« LINK/USDT », « LINKUSDT ») et tickers en « #TAG » (cotation USDT seulement)."""
    from .parser import _label_text, _pairs
    label = _label_text(text or "")
    explicit = {p for p in _pairs(label) if p.endswith("USDT")}
    tags = {t for t in (ticker(tag) for tag in CAPTION_TAG.findall(label) if not tag.isdigit()) if t}
    return explicit, tags - explicit


def signal_text(rep: dict, *, caption: str | None = None) -> str | None:
    """Texte de signal standard (lu par le même parseur que les messages texte) si la lecture est sûre, sinon None.

    Refus : publication de résultat, alerte bloquante, niveaux incomplets, ou paire douteuse. Paire : une paire
    explicite de la légende passe avant l'image (et doit concorder avec elle si l'image en montre une) ; plusieurs
    paires ou plusieurs « #TAG » : ambigu, refus ; un « #TAG » seul ne suffit pas, il doit concorder avec la paire
    lue dans l'image (« #AI », « #TP1 » ne deviennent jamais une paire à eux seuls)."""
    if rep.get("resultat_mesure") or any(a.startswith(BLOCKING) for a in rep.get("alertes", [])):
        return None
    signal = rep.get("signal") or {}
    entries, stops, targets = signal.get("entrees") or [], signal.get("stop") or [], signal.get("objectifs") or []
    if len(stops) != 1 or not entries or not targets:
        return None
    image = ticker(rep.get("symbole"))
    explicit, tags = caption_pairs(caption)
    pair: str | None
    if len(explicit) > 1 or (not explicit and len(tags) > 1):
        return None
    if explicit:
        pair = next(iter(explicit))
        if image and image != pair:
            return None
    elif tags:
        if image is None or image not in tags:
            return None
        pair = image
    else:
        pair = image
    if not pair:
        return None
    lines = [f"#{pair[:-4]}/USDT"]
    lines += [f"Entry{i}: {v:.12g}" for i, v in enumerate(sorted(entries, reverse=True), start=1)]
    lines += [f"TP{i}: {v:.12g}" for i, v in enumerate(sorted(targets), start=1)]
    lines.append(f"Stop: {stops[0]:.12g}")
    return "\n".join(lines)
