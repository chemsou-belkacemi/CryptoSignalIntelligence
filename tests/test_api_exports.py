"""Audit d'un export Telegram AVEC ses images depuis le tableau de bord (external/exports.py, routes
/sources/exports) : listing du dossier exports/, nom de dossier validé (jamais lu ailleurs), un seul audit à la
fois en arrière-plan, progression, résultat identique à la commande, jeton requis. Lecteur d'images FACTICE
(aucun OCR), bougies absentes (aucun réseau)."""
from __future__ import annotations

import json
import shutil
import threading
import urllib.error
import urllib.request
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from crypto_signal_intelligence.api.server import ApiError, CsiApi, make_handler
from crypto_signal_intelligence.external import exports as ex

from .test_chart_ocr import export, fake_reader

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)


def NO_BARS(symbol, start, end):     # noqa: N802 - aucune bougie (aucun réseau) : chaque signal reste SANS_DONNEES
    return None


def make_export(settings, name: str, *, photos: bool = True) -> Path:
    """Un sous-dossier d'exports/ avec le result.json et les photos de tests/test_chart_ocr.export."""
    folder = settings.exports_dir / name
    folder.mkdir(parents=True)
    payload = export(folder)
    if not photos:
        shutil.rmtree(folder / "photos")
    (folder / "result.json").write_text(json.dumps(payload), encoding="utf-8")
    return folder


@pytest.fixture
def api(settings, monkeypatch):
    monkeypatch.setenv("CSI_API_TOKEN", "jeton-de-test")
    api = CsiApi(settings, now=lambda: NOW)
    api.audit_bars = NO_BARS
    api.image_reader = fake_reader
    return api


def launch(api, folder: str = "Groupe A", **extra) -> dict:
    return api.dispatch("POST", "/sources/exports/audit", {}, {"folder": folder, "weights": "early", "ocr": True} | extra)


def listed(api) -> dict:
    out = api.dispatch("GET", "/sources/exports", {}, None)
    return {x["folder"]: x for x in out["exports"]} | {"_": out}


# --- Listing ----------------------------------------------------------------------------------------------

def test_exports_are_listed_with_their_images_and_only_from_the_exports_folder(api, settings):
    assert api.dispatch("GET", "/sources/exports", {}, None)["exports"] == []          # dossier absent : vide
    make_export(settings, "Groupe A")
    make_export(settings, "Sans photos", photos=False)
    (settings.exports_dir / "Pas un export").mkdir()
    (settings.exports_dir / "Pas un export" / "result.json").write_text("[1, 2]", encoding="utf-8")
    (settings.exports_dir / "Vide").mkdir()                                            # sans result.json
    (settings.exports_dir / ".cache").mkdir()
    shutil.copy(settings.exports_dir / "Groupe A" / "result.json", settings.exports_dir / ".cache" / "result.json")
    out = listed(api)
    assert [x["folder"] for x in out["_"]["exports"]] == ["Groupe A", "Pas un export", "Sans photos"]
    assert out["_"]["running"] is None and out["_"]["directory"] == str(settings.exports_dir)
    assert out["_"]["ocr_available"] is True                                           # lecteur factice fourni
    group = out["Groupe A"]
    # 4 messages texte ; 7 photos nommées, 4 présentes DANS le dossier (absente.jpg manque, les deux « dehors »
    # sont hors du dossier donc jamais lues ni comptées) : même compte que la commande.
    assert (group["messages"], group["images_named"], group["images_present"]) == (4, 7, 4)
    assert group["chats"] == ["Groupe A"] and group["error"] is None and group["audit"] is None
    assert group["without_photos"] is False
    without = out["Sans photos"]
    assert (without["images_named"], without["images_present"], without["without_photos"]) == (7, 0, True)
    assert "pas un export" in out["Pas un export"]["error"]
    payload = json.loads((settings.exports_dir / "Groupe A" / "result.json").read_text(encoding="utf-8"))
    assert ex.export_images(payload, settings.exports_dir / "Groupe A") == (7, 4)


def test_the_command_counts_the_images_the_same_way(settings, monkeypatch):
    from typer.testing import CliRunner

    from crypto_signal_intelligence import cli
    from crypto_signal_intelligence.external import audit as au
    make_export(settings, "Sans photos", photos=False)
    monkeypatch.setattr(au, "market_bars", lambda settings, now: NO_BARS)
    done = CliRunner().invoke(cli.app, ["audit-telegram", "--dir", str(settings.exports_dir)])
    assert done.exit_code == 0, done.output
    output = " ".join(done.output.split())                                # Rich replie les lignes à 80 colonnes
    assert "Sans photos : 4 message(s) ; images : 0 présente(s) sur 7" in output
    assert "export fait SANS les photos" in output


# --- Nom de dossier ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["..", ".", "", "a/b", "a\\b", "/etc", ".cache", "Groupe A/../Groupe A", 5, None,
                                 "x" * 121, " Groupe A", "a\0b"])
def test_folder_names_that_are_paths_or_hidden_are_refused(api, settings, bad):
    make_export(settings, "Groupe A")
    assert not ex.folder_name_ok(bad)
    with pytest.raises(ApiError) as caught:
        launch(api, folder=bad)
    assert caught.value.status == HTTPStatus.BAD_REQUEST
    if isinstance(bad, str):
        with pytest.raises(ApiError) as caught:
            api.dispatch("GET", "/sources/exports/audit", {"folder": [bad]}, None)
        assert caught.value.status == HTTPStatus.BAD_REQUEST


def test_unknown_folders_and_symlinks_outside_are_not_read(api, settings, tmp_path):
    make_export(settings, "Groupe A")
    (settings.exports_dir / "Vide").mkdir()
    elsewhere = tmp_path / "ailleurs"
    elsewhere.mkdir()
    shutil.copy(settings.exports_dir / "Groupe A" / "result.json", elsewhere / "result.json")   # un vrai export
    (settings.exports_dir / "Lien").symlink_to(elsewhere, target_is_directory=True)
    # Dossier réel dans exports/, mais son result.json est un lien vers l'extérieur.
    (settings.exports_dir / "Lien interne").mkdir()
    (settings.exports_dir / "Lien interne" / "result.json").symlink_to(elsewhere / "result.json")
    for name in ("Inconnu", "Vide", "Lien", "Lien interne"):
        with pytest.raises(ApiError) as caught:
            launch(api, folder=name)
        assert caught.value.status == HTTPStatus.NOT_FOUND, name
    with pytest.raises(ApiError) as caught:
        api.dispatch("GET", "/sources/exports/audit", {"folder": ["Groupe A"]}, None)
    assert caught.value.status == HTTPStatus.NOT_FOUND                   # aucun audit lancé
    for weights, ocr in (("moyen", True), ("early", "oui")):
        with pytest.raises(ApiError) as caught:
            launch(api, weights=weights, ocr=ocr)
        assert caught.value.status == HTTPStatus.BAD_REQUEST
    # La liste (lisible sans jeton) ne suit aucun lien qui sort d'exports/.
    assert [x["folder"] for x in api.dispatch("GET", "/sources/exports", {}, None)["exports"]] == ["Groupe A"]
    assert [p.name for p in ex.list_folders(settings)] == ["Groupe A"]


def test_photos_copied_after_a_first_listing_are_counted(api, settings):
    """F4 : le compte des images suit les photos copiées après coup (le listing est mis en cache)."""
    folder = make_export(settings, "Groupe A", photos=False)
    first = listed(api)["Groupe A"]
    assert (first["images_present"], first["without_photos"]) == (0, True)
    export(folder)                                                       # recrée photos/ et ses images
    again = listed(api)["Groupe A"]
    assert again["images_present"] == 4 and again["without_photos"] is False


# --- Jeton --------------------------------------------------------------------------------------------------

def test_the_audit_needs_the_api_token(settings, monkeypatch):
    monkeypatch.delenv("CSI_API_TOKEN", raising=False)
    make_export(settings, "Groupe A")
    api = CsiApi(settings, now=lambda: NOW)
    api.audit_bars, api.image_reader = NO_BARS, fake_reader
    with pytest.raises(ApiError) as caught:
        launch(api)
    assert caught.value.status == HTTPStatus.FORBIDDEN and "CSI_API_TOKEN" in caught.value.message
    assert listed(api)["Groupe A"]["audit"] is None                      # rien n'a été lancé
    handler = make_handler(api, token="jeton-de-test")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{httpd.server_address[1]}/sources/exports/audit"
        request = urllib.request.Request(url, data=b'{"folder": "Groupe A"}', method="POST",
                                         headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as http:
            urllib.request.urlopen(request, timeout=10)
        assert http.value.code == HTTPStatus.UNAUTHORIZED
    finally:
        httpd.shutdown()
        httpd.server_close()


# --- Audit en arrière-plan ----------------------------------------------------------------------------------

def test_audit_runs_in_the_background_with_progress_then_the_same_result_as_the_command(api, settings):
    make_export(settings, "Groupe A")
    started, gate = threading.Event(), threading.Event()

    def bars(symbol, start, end):
        started.set()
        assert gate.wait(10)
        return None

    api.audit_bars = bars
    out = launch(api)
    assert out["folder"] == "Groupe A" and out["audit"]["state"] == ex.RUNNING and out["audit"]["result"] is None
    assert started.wait(10)
    running = listed(api)
    assert running["_"]["running"] == "Groupe A"
    progress = running["Groupe A"]["audit"]
    assert progress["state"] == ex.RUNNING and progress["step"].startswith("rejeu : bougies ")
    # Deux images passées au lecteur (a.jpg : signal sûr, b.jpg : douteuse) ; comptées dès la lecture.
    assert (progress["images_read"], progress["images_as_signals"]) == (2, 1)
    assert (progress["images_named"], progress["images_present"]) == (7, 4)
    with pytest.raises(ApiError) as second:
        launch(api)
    assert second.value.status == HTTPStatus.CONFLICT
    with pytest.raises(ApiError) as sync:                                 # même verrou que l'import d'un fichier
        api.dispatch("POST", "/sources/history", {}, {"export": {"name": "G", "messages": [
            {"id": 1, "type": "message", "date_unixtime": "1772445600", "text": "bonjour"}]}})
    assert sync.value.status == HTTPStatus.CONFLICT
    gate.set()
    api.join_export_audit(30)
    done = listed(api)
    assert done["_"]["running"] is None
    audit = done["Groupe A"]["audit"]
    assert audit["state"] == ex.DONE and audit["has_result"] is True and audit["result"] is None   # listing sans le résultat
    full = api.dispatch("GET", "/sources/exports/audit", {"folder": ["Groupe A"]}, None)["audit"]
    result = full["result"]
    assert full["finished_at"] == NOW.isoformat() and full["error"] is None
    block = result["summary"]["Groupe A"]
    # 4 messages texte + l'image douteuse gardée comme message ILLISIBLE (le taux de rejet se voit) ; les deux
    # signaux (image sûre, texte) restent SANS_DONNEES faute de bougies.
    assert result["messages"] == 5 and block["messages"] == 5
    assert block["statuts"] == {"ILLISIBLE": 3, "SANS_DONNEES": 2}
    assert block["images"]["lues"] == 1 and block["images"]["ignorees"] == 1 and block["images"]["mesurees"] == 0
    assert block["preuve"]["ocr"] is True and block["preuve"]["proven"] is False
    assert result["conventions"] and result["all"] == "ensemble" and result["rows"] == []      # aucune bougie : rien d'OK
    directory = settings.reports_dir / result["report"]
    assert (directory / "audit.json").is_file() and (directory / "signaux.csv").is_file()
    saved = json.loads((directory / "audit.json").read_text(encoding="utf-8"))
    assert saved["summary"]["Groupe A"]["images"] == block["images"] and saved["rows"][0]["from_image"] is True
    # Preuve enregistrée comme par la commande, et audit relu par une nouvelle instance de l'API.
    assert [g["source"] for g in api.dispatch("GET", "/sources/history", {}, None)["groups"]] == ["Groupe A"]
    assert (settings.reports_dir / ex.INDEX_FILE).is_file()
    again = CsiApi(settings, now=lambda: NOW)
    assert listed(again)["Groupe A"]["audit"]["state"] == ex.DONE
    assert again.dispatch("GET", "/sources/exports/audit", {"folder": ["Groupe A"]}, None)["audit"]["result"]["messages"] == 5


def test_audit_without_images_then_a_failure_both_release_the_lock(api, settings):
    make_export(settings, "Groupe A")
    launch(api, ocr=False)
    api.join_export_audit(30)
    full = api.dispatch("GET", "/sources/exports/audit", {"folder": ["Groupe A"]}, None)["audit"]
    assert full["state"] == ex.DONE and full["ocr"] is False and full["images_read"] == 0
    assert "images" not in full["result"]["summary"]["Groupe A"]                   # aucune image lue
    assert full["result"]["summary"]["Groupe A"]["preuve"]["ocr"] is False

    def broken(symbol, start, end):
        raise RuntimeError("Binance injoignable")

    api.audit_bars = broken
    launch(api)
    api.join_export_audit(30)
    failed = api.dispatch("GET", "/sources/exports/audit", {"folder": ["Groupe A"]}, None)["audit"]
    assert failed["state"] == ex.FAILED and "RuntimeError : Binance injoignable" in failed["error"]
    assert failed["result"] is None and failed["has_result"] is False
    assert listed(api)["_"]["running"] is None
    api.audit_bars = NO_BARS
    launch(api)                                                                     # le verrou est libre
    api.join_export_audit(30)
    assert api.dispatch("GET", "/sources/exports/audit", {"folder": ["Groupe A"]}, None)["audit"]["state"] == ex.DONE
    index = ex.load_index(settings)
    assert index["Groupe A"].state == ex.DONE                                       # le dernier audit remplace l'échec


def test_a_thread_that_cannot_start_releases_the_lock(api, settings, monkeypatch):
    """F6 : si le fil d'audit ne démarre pas, le verrou est rendu et l'audit est marqué en échec."""
    from types import SimpleNamespace

    from crypto_signal_intelligence.api import server

    class Broken(threading.Thread):
        def start(self):
            raise RuntimeError("can't start new thread")

    make_export(settings, "Groupe A")
    monkeypatch.setattr(server, "threading", SimpleNamespace(Thread=Broken, Lock=threading.Lock))
    with pytest.raises(ApiError) as caught:
        launch(api)
    assert caught.value.status == HTTPStatus.SERVICE_UNAVAILABLE
    out = listed(api)
    assert out["_"]["running"] is None and out["Groupe A"]["audit"]["state"] == ex.FAILED
    monkeypatch.setattr(server, "threading", threading)
    launch(api)                                                          # le verrou est libre
    api.join_export_audit(30)
    assert listed(api)["Groupe A"]["audit"]["state"] == ex.DONE


def test_a_result_read_back_later_shows_the_current_proof(api, settings):
    """F7 : un résultat relu après redémarrage montre la date de la preuve, sa fin de validité et son état actuel."""
    from datetime import timedelta
    make_export(settings, "Groupe A")
    launch(api)
    api.join_export_audit(30)
    now = api.dispatch("GET", "/sources/exports/audit", {"folder": ["Groupe A"]}, None)["proofs_now"]["Groupe A"]
    assert now["generated_at"] == NOW.isoformat() and now["expired"] is False
    assert now["expires_at"] == (NOW + timedelta(days=30)).isoformat()
    later = CsiApi(settings, now=lambda: NOW + timedelta(days=40))
    old = later.dispatch("GET", "/sources/exports/audit", {"folder": ["Groupe A"]}, None)
    proof = old["proofs_now"]["Groupe A"]
    assert proof["expired"] is True and proof["proven"] is False and "à refaire" in proof["text"]
    assert old["audit"]["result"]["summary"]["Groupe A"]["preuve"]                 # résultat d'origine gardé à côté


def test_the_dashboard_and_the_command_write_the_same_report(api, settings, monkeypatch):
    """Même export, mêmes bougies synthétiques (signaux remplis et résolus), même lecteur d'images : l'audit lancé
    depuis le tableau de bord et `audit-telegram --dir … --ocr` écrivent le même audit.json (hors date)."""
    import pandas as pd
    from typer.testing import CliRunner

    from crypto_signal_intelligence import cli
    from crypto_signal_intelligence.external import audit as au

    from .test_audit_independence import synthetic_bars
    folder = make_export(settings, "Groupe A")
    bars = synthetic_bars(pd.Timestamp("2026-03-02 12:30", tz="UTC"))
    api.audit_bars = bars
    launch(api)
    api.join_export_audit(30)
    result = api.dispatch("GET", "/sources/exports/audit", {"folder": ["Groupe A"]}, None)["audit"]["result"]
    from_api = json.loads((settings.reports_dir / result["report"] / "audit.json").read_text(encoding="utf-8"))
    monkeypatch.setattr(au, "market_bars", lambda settings, now: bars)
    monkeypatch.setattr(au, "chart_reader", lambda: fake_reader())
    done = CliRunner().invoke(cli.app, ["audit-telegram", "--dir", str(folder), "--ocr", "--weights", "early"])
    assert done.exit_code == 0, done.output
    reports = sorted(d for d in settings.reports_dir.glob("AUDIT-*") if d.name != result["report"])
    assert len(reports) == 1
    from_cli = json.loads((reports[0] / "audit.json").read_text(encoding="utf-8"))
    measured = [r for r in from_api["rows"] if r["status"] == "OK"]
    assert {r["from_image"] for r in measured} == {True, False}          # un signal image et un signal texte mesurés
    assert all(r["outcomes"][au.TP1_TOUCH]["r"] is not None for r in measured)
    assert {k: v for k, v in from_api.items() if k != "generated_at"} == \
        {k: v for k, v in from_cli.items() if k != "generated_at"}


def test_image_reading_unavailable_is_a_clear_refusal(api, settings, monkeypatch):
    from crypto_signal_intelligence.external import chart_ocr
    make_export(settings, "Groupe A")
    api.image_reader = None
    monkeypatch.setattr(chart_ocr, "available", lambda: False)
    assert api.dispatch("GET", "/sources/exports", {}, None)["ocr_available"] is False
    with pytest.raises(ApiError) as caught:
        launch(api)
    assert caught.value.status == HTTPStatus.SERVICE_UNAVAILABLE and "ocr" in caught.value.message
    assert listed(api)["Groupe A"]["audit"] is None
    launch(api, ocr=False)                                                          # le texte seul reste possible
    api.join_export_audit(30)
    assert listed(api)["Groupe A"]["audit"]["state"] == ex.DONE
