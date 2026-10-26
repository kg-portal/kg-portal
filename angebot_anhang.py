# Angebot-Mail: Preisliste (PDF) als zusätzlicher Anhang.
# Im Angebot-Editor per Knopf „+ Preisliste“ wählbar; das Mail-Fenster hängt sie dann neben Angebot und
# Leistungsverzeichnis an. Die Datei liegt nur auf dem Server (data/anhaenge, nicht in Git) und wird im Editor
# hochgeladen bzw. ersetzt. Eine ersetzte Preisliste bleibt als Sicherung im selben Ordner liegen.
import base64
import json
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import jsonify, request, send_file

BERLIN = ZoneInfo("Europe/Berlin")
ORDNER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "anhaenge")
DATEI = os.path.join(ORDNER, "preisliste.pdf")
INFO = os.path.join(ORDNER, "preisliste.json")
MAX_BYTES = 15 * 1024 * 1024


def _info():
    if not os.path.isfile(DATEI):
        return {"vorhanden": False}
    try:
        with open(INFO, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        info = {}
    return {"vorhanden": True, "name": info.get("name") or "Preisliste.pdf", "hochgeladen_am": info.get("hochgeladen_am") or "",
            "groesse": os.path.getsize(DATEI)}


def _dateiname(name):
    """Name, unter dem der Kunde die Datei bekommt (ohne Pfad, endet auf .pdf)."""
    name = os.path.basename(str(name or "").replace("\\", "/")).strip()
    name = re.sub(r'[\x00-\x1f"<>:|?*]', "", name)[:120].strip(" .")
    if not name:
        name = "Preisliste"
    return name if name.lower().endswith(".pdf") else name + ".pdf"


def register_angebot_anhang(app, login_required):

    @app.route("/api/angebot-anhang/preisliste/info")
    @login_required
    def angebot_preisliste_info():
        return jsonify({"ok": True, **_info()})

    @app.route("/api/angebot-anhang/preisliste")
    @login_required
    def angebot_preisliste_ansehen():
        info = _info()
        if not info["vorhanden"]:
            return jsonify({"ok": False, "message": "Noch keine Preisliste hochgeladen."}), 404
        return send_file(DATEI, mimetype="application/pdf", download_name=info["name"], max_age=0)

    @app.route("/api/angebot-anhang/preisliste/mail")
    @login_required
    def angebot_preisliste_mail():
        """Für das Mail-Fenster: Anhang im gleichen Format wie Angebot und Leistungsverzeichnis."""
        info = _info()
        if not info["vorhanden"]:
            return jsonify({"ok": False, "message": "Preisliste fehlt – bitte im Angebot-Editor hochladen."}), 404
        with open(DATEI, "rb") as f:
            daten = base64.b64encode(f.read()).decode("ascii")
        return jsonify({"ok": True, "attachment": {"filename": info["name"], "mimeType": "application/pdf", "data": daten}})

    @app.route("/api/angebot-anhang/preisliste", methods=["POST"])
    @login_required
    def angebot_preisliste_hochladen():
        datei = request.files.get("datei")
        if not datei:
            return jsonify({"ok": False, "message": "Keine Datei gewählt."}), 400
        inhalt = datei.read(MAX_BYTES + 1)
        if len(inhalt) > MAX_BYTES:
            return jsonify({"ok": False, "message": "Die Datei ist zu groß (höchstens 15 MB)."}), 400
        if not inhalt.startswith(b"%PDF-"):
            return jsonify({"ok": False, "message": "Bitte eine PDF-Datei wählen."}), 400
        os.makedirs(ORDNER, exist_ok=True)
        jetzt = datetime.now(BERLIN)
        neu = DATEI + ".neu"
        with open(neu, "wb") as f:
            f.write(inhalt)
        if os.path.isfile(DATEI):  # alte Preisliste nicht löschen, sondern als Sicherung behalten
            os.replace(DATEI, os.path.join(ORDNER, "preisliste_bis_" + jetzt.strftime("%Y%m%d_%H%M%S") + ".pdf"))
        os.replace(neu, DATEI)
        with open(INFO, "w", encoding="utf-8") as f:
            json.dump({"name": _dateiname(datei.filename), "hochgeladen_am": jetzt.strftime("%Y-%m-%d %H:%M")}, f, ensure_ascii=False)
        return jsonify({"ok": True, **_info()})
