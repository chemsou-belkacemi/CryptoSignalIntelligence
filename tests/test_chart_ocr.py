"""Signaux publiés en IMAGE (external/chart_ocr.py, branchés sur l'audit des exports Telegram) : règles de refus sans
OCR (paire, alertes bloquantes, publication de résultat, format des prix), lecture de l'export avec un lecteur
factice, puis lecture de bout en bout d'un graphique SYNTHÉTIQUE (sautée sans l'extra « ocr »)."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import numpy as np
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


def test_caption_pair_wins_and_must_agree_with_the_image():
    assert co.caption_symbol("🚀 #LINK spot") == "LINKUSDT"
    assert co.caption_symbol("LINK/USDT long") == "LINKUSDT"
    assert co.caption_symbol("#LINK et #SOL") is None             # ambiguë
    assert co.caption_symbol("bonne journée") is None
    image_without_pair = rep(symbole=None)
    assert co.signal_text(image_without_pair) is None
    assert co.signal_text(image_without_pair, symbol_hint="SOLUSDT").startswith("#SOL/USDT")
    assert co.signal_text(rep(), symbol_hint="LINKUSDT") is None  # légende et image en désaccord


# --- Export Telegram avec un lecteur factice ---------------------------------------------------------------

def export(tmp_path: Path) -> dict:
    (tmp_path / "photos").mkdir(exist_ok=True)
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        (tmp_path / "photos" / name).write_bytes(b"x")
    return {"name": "Groupe A", "messages": [
        {"id": 1, "type": "message", "date_unixtime": "1772445600", "photo": "photos/a.jpg", "text": "#SOL 🔥"},
        {"id": 2, "type": "message", "date_unixtime": "1772449200", "photo": "photos/b.jpg", "text": ""},
        {"id": 3, "type": "message", "date_unixtime": "1772452800", "photo": "photos/c.jpg",
         "text": "#ABC/USDT\nEntry1: 100\nTP1: 104\nStop: 95"},
        {"id": 4, "type": "message", "date_unixtime": "1772456400", "photo": "photos/absente.jpg", "text": ""},
        {"id": 5, "type": "message", "date_unixtime": "1772460000", "text": "bonjour"}]}


def test_export_images_are_read_only_when_the_text_is_not_a_signal(tmp_path):
    calls: list[tuple[str, str]] = []

    def reader(path: Path, caption: str) -> str | None:
        calls.append((path.name, caption))
        return co.signal_text(rep(symbole=None), symbol_hint=co.caption_symbol(caption))   # b.jpg : pas de paire

    items = au.read_telegram_export(export(tmp_path), images_dir=tmp_path, image_reader=reader)
    assert calls == [("a.jpg", "#SOL 🔥"), ("b.jpg", "")]       # c.jpg : texte déjà lisible ; absente : pas d'appel
    by_id = {i.message_id: i for i in items}
    assert by_id["1"].from_image and parse(by_id["1"].text).symbol == "SOLUSDT"
    assert by_id["1"].group == "Groupe A"
    assert "2" not in by_id and "4" not in by_id                 # image douteuse ou absente, sans texte : ignorée
    assert not by_id["3"].from_image and parse(by_id["3"].text).symbol == "ABCUSDT"
    assert not by_id["5"].from_image
    assert [i.message_id for i in au.read_telegram_export(export(tmp_path))] == ["1", "3", "5"]   # sans lecteur : inchangé


def test_audit_rows_say_which_signals_came_from_an_image(settings, tmp_path):
    items = au.read_telegram_export(export(tmp_path), images_dir=tmp_path,
                                    image_reader=lambda path, caption: co.signal_text(rep(symbole=None), symbol_hint="SOLUSDT"))
    report = au.audit(settings, items, now=datetime(2026, 6, 1, tzinfo=UTC), bars_for=lambda symbol, start, end: None)
    flags = {r.message_id: r.from_image for r in report.rows}
    assert flags["1"] is True and flags["2"] is True and flags["3"] is False
    assert report.to_dict()["rows"][0]["from_image"] is True


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

