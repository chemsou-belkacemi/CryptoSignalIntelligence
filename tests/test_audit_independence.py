"""La mesure d'un groupe (bilan texte, preuve, gestions comparées) ne dépend ni de la lecture des images ni des
autres groupes audités avec lui (relecture leak-auditor du 2026-10-06, F1, F2, F5). Bougies SYNTHÉTIQUES : chaque
signal est rempli puis atteint tous ses objectifs, pour que les doublons changent vraiment les comptes."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from crypto_signal_intelligence.external import audit as au
from crypto_signal_intelligence.external import chart_ocr as co

from .test_chart_ocr import fake_reader, rep

NOW = datetime(2026, 6, 1, tzinfo=UTC)
T = pd.Timestamp("2026-03-02 10:00", tz="UTC")
STEP = pd.Timedelta(minutes=15)
ABC = "#ABC/USDT\nEntry1: 100\nTP1: 104\nStop: 95"
XYZ = "#XYZ/USDT\nEntry1: 10\nTP1: 10.4\nStop: 9.5"
LEVELS = {"SOLUSDT": (108.4, 114.25), "ABCUSDT": (100.0, 104.0), "XYZUSDT": (10.0, 10.4)}


def synthetic_bars(dip_at: pd.Timestamp, levels: dict[str, tuple[float, float]] = LEVELS) -> au.Bars:
    """Bougies 15 min : prix 0,5 % au-dessus de l'entrée 1 jusqu'à `dip_at`, puis une mèche qui traverse l'entrée,
    puis une bougie qui dépasse le dernier objectif (TP atteints, stop jamais touché)."""
    def fetch(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame | None:
        if symbol not in levels:
            return None
        entry, top = levels[symbol]
        ref, high = entry * 1.005, top * 1.02
        rows = []
        for t in pd.date_range(start.floor("15min"), end, freq="15min"):
            if t < dip_at:
                rows.append((t, ref, ref, ref, ref))
            elif t == dip_at:
                rows.append((t, ref, ref, entry * 0.99, entry))
            elif t == dip_at + STEP:
                rows.append((t, entry, high, entry, high))
            else:
                rows.append((t, high, high, high, high))
        return pd.DataFrame(rows, columns=["open_time", "open", "high", "low", "close"])
    return fetch


def telegram_export(folder: Path, messages: list[tuple[pd.Timedelta, str, str | None]]) -> dict:
    """Export d'un groupe : (décalage depuis T, texte, photo) ; les photos sont créées dans `folder/photos`."""
    (folder / "photos").mkdir(parents=True, exist_ok=True)
    out = []
    for k, (offset, text, photo) in enumerate(messages, 1):
        message = {"id": k, "type": "message", "date_unixtime": str(int((T + offset).timestamp())), "text": text}
        if photo:
            (folder / "photos" / photo).write_bytes(b"x")
            message["photo"] = f"photos/{photo}"
        out.append(message)
    return {"name": "Groupe A", "messages": out}


def summary(settings, payload: dict, folder: Path, *, images: bool, dip_at: pd.Timestamp) -> dict:
    items = au.read_telegram_export(payload, images_dir=folder, image_reader=fake_reader() if images else None)
    return au.audit(settings, items, now=NOW, bars_for=synthetic_bars(dip_at)).summary


def without_ocr_keys(proof: dict) -> dict:
    return {k: v for k, v in proof.items() if k not in ("ocr", "signaux_image_exclus")}


def test_an_image_never_turns_the_same_text_signal_into_a_duplicate(settings, tmp_path):
    """F1 : une image sûre puis le même signal en texte 30 min après ; le texte reste mesuré, et le bilan comme la
    preuve sont identiques avec et sans lecture des images."""
    payload = telegram_export(tmp_path, [(pd.Timedelta(0), "", "a.jpg"),
                                         (pd.Timedelta(minutes=30), co.signal_text(rep()) or "", None)])
    dip = T + pd.Timedelta(hours=2)
    plain = summary(settings, payload, tmp_path, images=False, dip_at=dip)["Groupe A"]
    read = summary(settings, payload, tmp_path, images=True, dip_at=dip)["Groupe A"]
    assert read["images"]["lues"] == 1 and read["images"]["mesurees"] == 1      # l'image est bien lue et mesurée
    assert plain["conventions"][au.TP1_TOUCH]["resolus"] == 1                    # le texte est résolu sans OCR…
    assert read["conventions"] == plain["conventions"]                           # … et avec
    assert without_ocr_keys(read["preuve"]) == without_ocr_keys(plain["preuve"])
    assert read["statuts"].get(au.DUPLICATE, 0) == 0


def test_an_image_is_still_a_duplicate_of_an_earlier_text(settings, tmp_path):
    """Le sens inverse reste un doublon : une capture du signal déjà publié en texte n'est pas un nouveau trade."""
    payload = telegram_export(tmp_path, [(pd.Timedelta(0), co.signal_text(rep()) or "", None),
                                         (pd.Timedelta(minutes=30), "", "a.jpg")])
    read = summary(settings, payload, tmp_path, images=True, dip_at=T + pd.Timedelta(hours=2))["Groupe A"]
    assert read["statuts"][au.DUPLICATE] == 1 and read["images"]["mesurees"] == 0


def test_a_group_is_measured_the_same_alone_or_with_the_group_it_copies(settings):
    """F2 : B reprend le signal de A 20 min après ; sa mesure et sa preuve ne changent pas si A est audité avec lui."""
    a = au.HistoryItem(text=ABC, received_at=T.to_pydatetime(), group="A")
    b = au.HistoryItem(text=ABC, received_at=(T + pd.Timedelta(minutes=20)).to_pydatetime(), group="B")
    bars = synthetic_bars(T + pd.Timedelta(hours=2))
    together = au.audit(settings, [a, b], now=NOW, bars_for=bars).summary["B"]
    alone = au.audit(settings, [b], now=NOW, bars_for=bars).summary["B"]
    assert alone["conventions"][au.TP1_TOUCH]["resolus"] == 1
    assert together["conventions"] == alone["conventions"] and together["preuve"] == alone["preuve"]
    assert together["statuts"] == alone["statuts"] == {au.OK: 1}
    same = au.audit(settings, [a, au.HistoryItem(text=ABC, received_at=b.received_at, group="A")], now=NOW, bars_for=bars)
    assert same.summary["A"]["statuts"] == {au.OK: 1, au.DUPLICATE: 1}          # dans un même groupe : toujours doublon


def test_compared_managements_use_text_signals_only(settings, tmp_path):
    """F5 : l'étude « Gestions comparées » porte sur les mêmes signaux avec ou sans lecture des images."""
    payload = telegram_export(tmp_path, [(pd.Timedelta(0), "", "a.jpg"), (pd.Timedelta(hours=1), ABC, None),
                                         (pd.Timedelta(minutes=90), XYZ, None)])
    dip = T + pd.Timedelta(hours=2)
    plain = summary(settings, payload, tmp_path, images=False, dip_at=dip)["Groupe A"]
    read = summary(settings, payload, tmp_path, images=True, dip_at=dip)["Groupe A"]
    assert read["images"]["mesurees"] == 1
    assert plain["gestions"]["signals"] == read["gestions"]["signals"] == 2
    assert read["gestions"] == plain["gestions"]
