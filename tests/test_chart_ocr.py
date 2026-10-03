"""Signaux publiés en IMAGE (external/chart_ocr.py, branchés sur l'audit des exports Telegram) : règles de refus sans
OCR (paire, alertes bloquantes, publication de résultat, format des prix), lecture de l'export avec un lecteur
factice, puis lecture de bout en bout d'un graphique SYNTHÉTIQUE (sautée sans l'extra « ocr »)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from crypto_signal_intelligence.external import audit as au
from crypto_signal_intelligence.external import chart_ocr as co
from crypto_signal_intelligence.external.parser import parse

GOOD = {"symbole": "SOLUSDT", "resultat_mesure": False, "alertes": [],
        "signal": {"entrees": [106.9, 108.4], "stop": [103.15], "objectifs": [114.25, 111.8]}}


def rep(**changes) -> dict:
    return GOOD | changes


# --- Règles de refus (sans OCR) ----------------------------------------------------------------------------

def test_signal_text_is_parsed_like_a_text_signal():
    text = co.signal_text(rep())
    assert text is not None
    signal = parse(text)
    assert signal.errors == []
    assert signal.symbol == "SOLUSDT"
    assert signal.entries == [108.4, 106.9]          # entrée la plus haute d'abord
    assert signal.targets == [111.8, 114.25]
    assert signal.stop == 103.15


def test_large_prices_keep_every_digit():
    text = co.signal_text(rep(signal={"entrees": [83521.15], "stop": [81234.56], "objectifs": [86000.75]}))
    assert text is not None and "83521.15" in text and "81234.56" in text and "86000.75" in text


@pytest.mark.parametrize("alert", ["AXE_NON_CALIBRE", "DESACCORD_OCR 1.2 ['1.2', '1.8']", "STOP_NON_UNIQUE (2)",
                                   "ORDRE_STOP_ENTREE", "PRIX_COURANT_HORS_PLAGE", "SEPARATEUR_DOUTEUX (14:280)",
                                   "DEUX_ETIQUETTES_POUR_UNE_LIGNE 1 / 2", "IMAGE_ILLISIBLE"])
def test_any_blocking_alert_means_no_signal(alert):
    assert co.signal_text(rep(alertes=[alert])) is None


def test_a_line_without_label_does_not_block():
    assert co.signal_text(rep(alertes=["LIGNE_SANS_ETIQUETTE vert y=426 prix~0.0023"])) is not None


def test_a_result_publication_is_never_a_signal():
    assert co.signal_text(rep(resultat_mesure=True)) is None


@pytest.mark.parametrize("signal", [{"entrees": [], "stop": [1.0], "objectifs": [2.0]},
                                    {"entrees": [1.5], "stop": [], "objectifs": [2.0]},
                                    {"entrees": [1.5], "stop": [1.0, 0.9], "objectifs": [2.0]},
                                    {"entrees": [1.5], "stop": [1.0], "objectifs": []}])
def test_incomplete_levels_mean_no_signal(signal):
    assert co.signal_text(rep(signal=signal)) is None


def test_pair_names_and_quotes():
    assert co.ticker("CHAINLINK/USDT") == "LINKUSDT"
    assert co.ticker("Stellar/TetherUS".replace("TetherUS", "USDT")) == "XLMUSDT"
    assert co.ticker("GRAMUSDT") == "GRAMUSDT"
    assert co.ticker("BTCUSD") is None                # graphique en dollars : autre marché, refusé
    assert co.ticker("ETH/BTC") is None
    assert co.ticker(None) is None


def test_caption_pairs_and_tags():
    assert co.caption_pairs("🚀 #LINK spot") == (set(), {"LINKUSDT"})
    assert co.caption_pairs("LINK/USDT long #LINK") == ({"LINKUSDT"}, set())
    assert co.caption_pairs("bonne journée") == (set(), set())


@pytest.mark.parametrize(("image", "caption", "expected"), [
    ("SOLUSDT", None, "SOLUSDT"),                 # paire de l'image
    (None, "SOL/USDT long", "SOLUSDT"),           # paire explicite de la légende
    ("SOLUSDT", "SOLUSDT 🚀", "SOLUSDT"),          # les deux concordent
    ("SOLUSDT", "LINK/USDT", None),               # désaccord
    ("SOLUSDT", "#SOL", "SOLUSDT"),               # tag qui concorde avec l'image
    (None, "#SOL", None),                         # tag seul : insuffisant
    (None, "#AI #TP1", None),                     # plusieurs tags : ambigu
    ("SOLUSDT", "#SOL #LINK", None),
    ("SOLUSDT", "SOL/USDT ou LINK/USDT", None),   # deux paires explicites : ambigu
    (None, None, None),
])
def test_pair_rules(image, caption, expected):
    text = co.signal_text(rep(symbole=image), caption=caption)
    assert (parse(text).symbol if text else None) == expected


def test_an_orphan_level_line_inside_the_signal_blocks():
    assert co.signal_text(rep(alertes=["LIGNE_ORPHELINE_DANS_LE_SIGNAL vert prix~112.9"])) is None


# --- Export Telegram avec un lecteur factice ---------------------------------------------------------------

def export(tmp_path: Path) -> dict:
    (tmp_path / "photos").mkdir(exist_ok=True)
    for name in ("a.jpg", "b.jpg", "c.jpg", "d.jpg"):
        (tmp_path / "photos" / name).write_bytes(b"x")
    (tmp_path.parent / "dehors.jpg").write_bytes(b"x")
    return {"name": "Groupe A", "messages": [
        {"id": 1, "type": "message", "date_unixtime": "1772445600", "photo": "photos/a.jpg", "text": "#SOL 🔥"},
        {"id": 2, "type": "message", "date_unixtime": "1772449200", "photo": "photos/b.jpg", "text": ""},
        {"id": 3, "type": "message", "date_unixtime": "1772452800", "photo": "photos/c.jpg",
         "text": "#ABC/USDT\nEntry1: 100\nTP1: 104\nStop: 95"},
        {"id": 4, "type": "message", "date_unixtime": "1772456400", "photo": "photos/absente.jpg", "text": ""},
        {"id": 5, "type": "message", "date_unixtime": "1772460000", "text": "bonjour"},
        {"id": 6, "type": "message", "date_unixtime": "1772463600", "photo": "photos/d.jpg", "text": "TP1 ✅",
         "reply_to_message_id": 1},
        {"id": 7, "type": "message", "date_unixtime": "1772467200", "photo": "../dehors.jpg", "text": ""},
        {"id": 8, "type": "message", "date_unixtime": "1772470800", "photo": str(tmp_path.parent / "dehors.jpg"), "text": ""}]}


def fake_reader(calls: list | None = None):
    def read(path: Path, caption: str) -> str | None:
        if calls is not None:
            calls.append((path.name, caption))
        return co.signal_text(rep(symbole="SOLUSDT" if path.name == "a.jpg" else None), caption=caption)
    return read


def test_export_images_are_read_only_when_the_text_is_not_a_signal(tmp_path):
    calls: list[tuple[str, str]] = []
    items = au.read_telegram_export(export(tmp_path), images_dir=tmp_path, image_reader=fake_reader(calls))
    # c.jpg : texte déjà lisible ; absente : pas de fichier ; d.jpg : réponse (mise à jour) ; dehors : hors de l'export
    assert calls == [("a.jpg", "#SOL 🔥"), ("b.jpg", "")]
    by_id = {i.message_id: i for i in items}
    assert by_id["1"].from_image and parse(by_id["1"].text).symbol == "SOLUSDT"
    assert by_id["1"].group == "Groupe A"
    assert by_id["2"].image_rejected and not by_id["2"].from_image          # image douteuse : gardée, illisible
    assert "4" not in by_id and "7" not in by_id and "8" not in by_id
    assert not by_id["3"].from_image and parse(by_id["3"].text).symbol == "ABCUSDT"
    assert not by_id["5"].from_image and not by_id["6"].from_image and not by_id["6"].image_rejected
    without = au.read_telegram_export(export(tmp_path))
    assert [i.message_id for i in without] == ["1", "3", "5", "6"]          # sans lecteur : inchangé
    assert not any(i.from_image or i.image_rejected for i in without)


def test_a_failing_image_never_stops_the_audit(tmp_path, monkeypatch):
    (tmp_path / "x.png").write_bytes(b"x")

    class Boom:
        def analyse(self, path):
            raise RuntimeError("cv2.error")

    monkeypatch.setattr(co, "available", lambda: True)
    monkeypatch.setattr(co, "Lecteur", Boom)
    assert au.chart_reader()(tmp_path / "x.png", "") is None


def test_audit_counts_images_apart_from_the_text_measure(settings, tmp_path):
    items = au.read_telegram_export(export(tmp_path), images_dir=tmp_path, image_reader=fake_reader())
    report = au.audit(settings, items, now=datetime(2026, 6, 1, tzinfo=UTC), bars_for=lambda symbol, start, end: None)
    by_id = {r.message_id: r for r in report.rows}
    assert by_id["1"].from_image and by_id["1"].status == au.NO_DATA
    assert by_id["2"].image_rejected and by_id["2"].status == au.UNREADABLE and "image" in by_id["2"].reason
    block = report.summary["Groupe A"]
    assert block["images"]["lues"] == 1 and block["images"]["ignorees"] == 1
    assert block["preuve"]["ocr"] is True
    assert report.to_dict()["rows"][0]["from_image"] is True


def test_image_signals_never_make_a_proof(settings):
    """60 signaux lus sur image, tous gagnants sur 30 jours : la preuve reste fausse (elle ne lit que le texte)."""
    rows = []
    for k in range(60):
        row = au.AuditRow(received_at=(pd.Timestamp("2026-03-01", tz="UTC") + pd.Timedelta(hours=12 * k)).isoformat(),
                          group="G", status=au.OK, symbol="SOLUSDT", entry=100.0, stop=95.0, targets=[104.0],
                          stop_pct=5.0, tp1_pct=4.0, from_image=True)
        row.outcomes = {c: {"issue": "TP", "r": 0.8, "provisoire": False} for c in au.CONVENTIONS}
        rows.append(row)
    entry = au.summarize(rows, samples=200, seed=1)["G"]
    assert entry["preuve"]["proven"] is False and entry["preuve"]["signaux_image_exclus"] == 60
    assert entry["images"]["conventions"][au.proof_convention()]["resolus"] == 60
    text_rows = [au.AuditRow(**{**r.__dict__, "from_image": False}) for r in rows]
    assert au.summarize(text_rows, samples=200, seed=1)["G"]["preuve"]["proven"] is True   # même chose en texte


def test_an_updated_chart_is_a_duplicate_not_a_new_trade(settings):
    t0 = datetime(2026, 3, 2, 10, tzinfo=UTC)
    first = au.HistoryItem(text="#SOL/USDT\nEntry1: 100\nTP1: 104\nTP2: 108\nStop: 95", received_at=t0, group="G")
    update = au.HistoryItem(text="#SOL/USDT\nEntry1: 100\nTP1: 108\nStop: 95", received_at=t0 + timedelta(days=1),
                            group="G", from_image=True)
    report = au.audit(settings, [first, update], now=datetime(2026, 6, 1, tzinfo=UTC), bars_for=lambda symbol, start, end: None)
    assert [r.status for r in report.rows] == [au.NO_DATA, au.DUPLICATE]


# --- Bout en bout sur un graphique synthétique (extra « ocr ») ----------------------------------------------

LEVELS = [("vert", 114.25), ("vert", 117.5), ("vert", 111.8), ("bleu", 108.4), ("bleu", 106.9), ("rouge", 103.15)]


def draw_chart(path: Path, levels: list[tuple[str, float]], *, title: str = "SOLUSDT") -> None:
    """Graphique sombre type TradingView : graduations grises de 100 à 120, étiquettes pleines et lignes de niveau."""
    cv2 = pytest.importorskip("cv2")
    width, height = 1400, 800

    def y_of(price: float) -> int:
        return int(round(60 + (120.0 - price) / 20.0 * 680))

    img = np.zeros((height, width, 3), np.uint8)
    img[:] = (34, 23, 19)
    font = cv2.FONT_HERSHEY_SIMPLEX
    cv2.putText(img, title, (30, 35), font, 0.8, (230, 230, 230), 2, cv2.LINE_AA)
    for price in np.arange(100.0, 120.01, 2.0):
        cv2.putText(img, f"{price:.2f}", (1290, y_of(price) + 6), font, 0.55, (170, 170, 170), 1, cv2.LINE_AA)
    colors = {"vert": (40, 190, 40), "bleu": (230, 120, 20), "rouge": (40, 40, 235)}
    for color, price in levels:
        y = y_of(price)
        cv2.line(img, (40, y), (1270, y), colors[color], 1)
        cv2.rectangle(img, (1272, y - 12), (1392, y + 12), colors[color], -1)
        cv2.putText(img, f"{price:.2f}", (1290, y + 6), font, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(path), img)


@pytest.fixture(scope="module")
def reader():
    pytest.importorskip("rapidocr")
    if not co.available():
        pytest.skip("extra « ocr » absent")
    return co.Lecteur()


def test_synthetic_chart_is_read_exactly(reader, tmp_path):
    draw_chart(tmp_path / "chart.png", LEVELS)
    result = reader.analyse(tmp_path / "chart.png")
    assert result["axe"] is not None and result["axe"]["residu_px_max"] < 2
    assert result["signal"] == {"entrees": [106.9, 108.4], "stop": [103.15], "objectifs": [111.8, 114.25, 117.5]}
    signal = parse(co.signal_text(result) or "")
    assert signal.symbol == "SOLUSDT" and signal.stop == 103.15 and signal.targets == [111.8, 114.25, 117.5]


def test_synthetic_chart_with_a_stop_above_the_entry_is_refused(reader, tmp_path):
    levels = [*LEVELS[:5], ("rouge", 109.6)]
    draw_chart(tmp_path / "chart.png", levels)
    result = reader.analyse(tmp_path / "chart.png")
    assert "ORDRE_STOP_ENTREE" in result["alertes"]
    assert co.signal_text(result) is None


def test_label_whose_value_does_not_match_its_line_is_not_a_level(reader, tmp_path):
    """Une étiquette d'indicateur (valeur sans ligne à la bonne hauteur) n'est jamais prise pour un objectif."""
    draw_chart(tmp_path / "chart.png", LEVELS)
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(tmp_path / "chart.png"))
    y = int(round(60 + (120.0 - 116.0) / 20.0 * 680))
    cv2.rectangle(img, (1272, y - 12), (1392, y + 12), (40, 190, 40), -1)
    cv2.putText(img, "118.90", (1290, y + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(tmp_path / "chart.png"), img)
    result = reader.analyse(tmp_path / "chart.png")
    assert 118.9 not in result["signal"]["objectifs"]
    assert result["signal"]["objectifs"] == [111.8, 114.25, 117.5]


def test_unreadable_file_is_refused(reader, tmp_path):
    (tmp_path / "x.png").write_bytes(b"pas une image")
    result = reader.analyse(tmp_path / "x.png")
    assert result["alertes"] == ["IMAGE_ILLISIBLE"] and co.signal_text(result) is None



@pytest.mark.parametrize(("old", "misread"), [("111.80", "118.80"), ("108.40", "101.40")])
def test_a_misread_label_blocks_the_image(reader, tmp_path, old, misread):
    """Deux lectures d'accord sur une valeur FAUSSE : l'étiquette perd sa ligne, la ligne reste dans la plage du
    signal, l'image est refusée au lieu de rendre un objectif ou une entrée faux."""
    draw_chart(tmp_path / "chart.png", LEVELS)
    cv2 = pytest.importorskip("cv2")
    img = cv2.imread(str(tmp_path / "chart.png"))
    price = float(old)
    y = int(round(60 + (120.0 - price) / 20.0 * 680))
    color = (40, 190, 40) if price > 110 else (230, 120, 20)
    cv2.rectangle(img, (1272, y - 12), (1392, y + 12), color, -1)
    cv2.putText(img, misread, (1290, y + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(tmp_path / "chart.png"), img)
    result = reader.analyse(tmp_path / "chart.png")
    assert any(a.startswith("LIGNE_ORPHELINE_DANS_LE_SIGNAL") for a in result["alertes"])
    assert co.signal_text(result) is None
