# Wetter Duisburg für die Startseite: der Server holt die Daten bei Open-Meteo (höchstens alle 10 Minuten) und
# speichert den letzten Stand in data/wetter.json. Der Browser fragt nur noch /api/wetter (gleiche Adresse wie das
# Portal – keine Sperre durch Browser oder Erweiterungen). Lehnt Open-Meteo die volle Abfrage ab, kommt eine kurze;
# antwortet Open-Meteo gar nicht, bleibt der letzte gespeicherte Stand sichtbar. Kein Schlüssel nötig.
import json
import os
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import jsonify

BERLIN = ZoneInfo("Europe/Berlin")
URL = "https://api.open-meteo.com/v1/forecast"
ORT = {"latitude": 51.4344, "longitude": 6.7623, "forecast_days": 7, "timezone": "Europe/Berlin"}
VOLL = {"current": "temperature_2m,weather_code,apparent_temperature,relative_humidity_2m,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,sunrise,sunset"}
KURZ = {"current": "temperature_2m,weather_code", "daily": "weather_code,temperature_2m_max,temperature_2m_min"}
FRISCH = 600       # Sekunden: so lange gilt ein Stand als aktuell
NEU_VERSUCH = 60   # nach einem Fehler frühestens nach einer Minute wieder fragen
DATEI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "wetter.json")

_lock = threading.Lock()
_stand = {"daten": None, "geholt": 0.0, "versucht": 0.0, "fehler": "", "datei_gelesen": False}


def _gueltig(d):
    return isinstance(d, dict) and isinstance(d.get("current"), dict) and isinstance(d.get("daily"), dict) \
        and isinstance(d["daily"].get("time"), list)


def _datei_lesen():
    try:
        with open(DATEI, encoding="utf-8") as f:
            gespeichert = json.load(f)
        if _gueltig(gespeichert.get("daten")):
            _stand["daten"], _stand["geholt"] = gespeichert["daten"], float(gespeichert.get("geholt") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        pass


def _datei_schreiben(daten, geholt):
    try:
        os.makedirs(os.path.dirname(DATEI), exist_ok=True)
        neu = DATEI + ".neu"
        with open(neu, "w", encoding="utf-8") as f:
            json.dump({"geholt": geholt, "daten": daten}, f, ensure_ascii=False)
        os.replace(neu, DATEI)
    except OSError as exc:
        print("Wetter: Stand nicht gespeichert:", exc)


def _holen():
    """Erst die volle Abfrage, sonst die kurze. → (daten, fehlertext)"""
    fehler = []
    for teil in (VOLL, KURZ):
        try:
            r = requests.get(URL, params={**ORT, **teil}, timeout=8)
            d = r.json()
        except (requests.RequestException, ValueError) as exc:
            fehler.append(type(exc).__name__)
            continue
        if r.status_code == 200 and _gueltig(d):
            return d, ""
        fehler.append(f"{r.status_code} {str(d.get('reason') or '')[:150] if isinstance(d, dict) else ''}".strip())
    return None, "; ".join(fehler)


def wetter_stand():
    jetzt = time.time()
    with _lock:
        if not _stand["datei_gelesen"]:
            _stand["datei_gelesen"] = True
            _datei_lesen()
        aktuell = _stand["daten"] is not None and jetzt - _stand["geholt"] < FRISCH
        if not aktuell and jetzt - _stand["versucht"] >= NEU_VERSUCH:
            _stand["versucht"] = jetzt
            daten, fehler = _holen()
            if daten:
                _stand.update(daten=daten, geholt=jetzt, fehler="")
                _datei_schreiben(daten, jetzt)
            else:
                _stand["fehler"] = fehler
                print("Wetter: Open-Meteo antwortet nicht:", fehler)
        return dict(_stand)


def register_wetter(app, login_required):

    @app.route("/api/wetter")
    @login_required
    def api_wetter():
        s = wetter_stand()
        if s["daten"] is None:
            return jsonify({"ok": False, "fehler": s["fehler"] or "Keine Wetterdaten."}), 503
        daten = dict(s["daten"])
        daten["kg_stand"] = datetime.fromtimestamp(s["geholt"], BERLIN).strftime("%H:%M")
        return jsonify(daten)
