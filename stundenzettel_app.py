"""Neue Mitarbeiter-Ansicht des Stundenzettels (Test): /stundenzettel/worker/<code>/neu

Eigene Seite neben der bisherigen – die alte Seite und ihre Links bleiben unverändert.
Gespeichert wird in dieselben work_logs wie bisher (gleiche Regeln: gesperrter Monat, Urlaub/Resturlaub).
Der Mitarbeiter tippt nur Knöpfe: „Alles richtig“ oder einen Tag ändern (Krank, Urlaub, Nicht gearbeitet,
andere Zeit, Extra). Jede Änderung steht in stundenzettel_korrekturen (quelle „app“).
"""
import calendar
import sqlite3
from datetime import date

from flask import jsonify, render_template, request

import stundenzettel_auto as stz

_get_db = None


def _spalten_anlegen(conn):
    """App-Bestätigung in eigenen Spalten – der bisherige Status (WhatsApp, Sperre) bleibt unberührt."""
    stz.ensure_tables(conn)
    vorhanden = {r[1] for r in conn.execute("PRAGMA table_info(stundenzettel_monate)").fetchall()}
    for spalte in ("app_bestaetigt_am", "app_geoeffnet_am"):
        if spalte not in vorhanden:
            try:
                conn.execute(f"ALTER TABLE stundenzettel_monate ADD COLUMN {spalte} TEXT")
            except sqlite3.OperationalError:
                pass  # gleichzeitig von einer anderen Anfrage angelegt


def _worker_zum_code(conn, code):
    return conn.execute(
        "SELECT id, vorname, nachname, resturlaub FROM mitarbeiter WHERE access_code = ?", (str(code or "").strip(),)
    ).fetchone()


def _monat(value):
    start, _ende, monat = stz._monat_param(value or stz.berlin_jetzt().strftime("%Y-%m"))
    return start, monat


def _eintrag(conn, wid, iso):
    return conn.execute(
        "SELECT datum, start_time, end_time, place, signed FROM work_logs WHERE worker_id = ? AND datum = ?", (wid, iso)
    ).fetchone()


def _speichern(conn, wid, iso, beginn, ende, ort):
    """Wie /api/stundenzettel/save: Tag eintragen (unterschrieben) und Resturlaub mitführen."""
    vorher = _eintrag(conn, wid, iso)
    war_urlaub = bool(vorher) and vorher["place"] == "Urlaub"
    conn.execute(
        "INSERT INTO work_logs (worker_id, datum, start_time, end_time, place, signed) VALUES (?, ?, ?, ?, ?, 1) "
        "ON CONFLICT(worker_id, datum) DO UPDATE SET start_time = excluded.start_time, end_time = excluded.end_time, "
        "place = excluded.place, signed = 1",
        (wid, iso, beginn, ende, ort or ""),
    )
    ist_urlaub = ort == "Urlaub"
    if ist_urlaub and not war_urlaub:
        conn.execute("UPDATE mitarbeiter SET resturlaub = MAX(0, COALESCE(resturlaub, 0) - 1) WHERE id = ?", (wid,))
    elif war_urlaub and not ist_urlaub:
        conn.execute("UPDATE mitarbeiter SET resturlaub = COALESCE(resturlaub, 0) + 1 WHERE id = ?", (wid,))


def _loeschen(conn, wid, iso):
    """Wie /api/stundenzettel/delete: Tag entfernen, Urlaubstag zurückgeben."""
    vorher = _eintrag(conn, wid, iso)
    conn.execute("DELETE FROM work_logs WHERE worker_id = ? AND datum = ?", (wid, iso))
    if vorher and vorher["place"] == "Urlaub":
        conn.execute("UPDATE mitarbeiter SET resturlaub = COALESCE(resturlaub, 0) + 1 WHERE id = ?", (wid,))


def monat_daten(conn, wid, monat_wert):
    _spalten_anlegen(conn)
    start, monat = _monat(monat_wert)
    plan = stz._plan_laden(conn, wid)
    feiertage = stz.feiertage_nrw(start.year)
    logs = {l["datum"]: l for l in conn.execute(
        "SELECT datum, start_time, end_time, place, signed FROM work_logs WHERE worker_id = ? AND datum LIKE ?",
        (wid, monat + "-%")).fetchall()}
    tage = []
    for nr in range(1, calendar.monthrange(start.year, start.month)[1] + 1):
        d = date(start.year, start.month, nr)
        l = logs.get(d.isoformat())
        p = plan[stz.WOCHENTAGE[d.weekday()]]
        tage.append({
            "datum": d.isoformat(),
            "wt": d.weekday(),
            "eintrag": {"start": (l["start_time"] or "")[:5], "ende": (l["end_time"] or "")[:5],
                        "ort": l["place"] or "", "signed": bool(l["signed"])} if l else None,
            "geplant": {"start": p["start"], "ende": p["ende"], "ort": p["ort"]} if p["aktiv"] else None,
            "feiertag": feiertage.get(d, ""),
        })
    row = stz._monat_row(conn, wid, monat)
    t = stz.monat_termine(monat)
    gesperrt = row.get("status") == "bestaetigt" or stz.berlin_jetzt() >= t["frist"]
    return {
        "monat": monat,
        "monat_de": f"{stz.MONATE[start.month - 1]} {start.year}",
        "monat_tr": f"{stz.MONATE_TR[start.month - 1]} {start.year}",
        "frist_de": stz._frist_text(t, "de"),
        "frist_tr": stz._frist_text(t, "tr"),
        "gesperrt": bool(gesperrt),
        "bestaetigt_am": row.get("app_bestaetigt_am") or "",
        "tage": tage,
        "orte": [o for o in stz.ORTE if o not in stz.SONDER_ORTE],
    }


def tag_aendern(conn, wid, daten):
    """Ein Tag aus der App. → (ok, Fehlertext)"""
    try:
        d = date.fromisoformat(str(daten.get("datum") or "")[:10])
    except ValueError:
        return False, "Datum fehlt"
    iso, monat = d.isoformat(), d.isoformat()[:7]
    if stz.monat_gesperrt(conn, wid, iso) or stz.berlin_jetzt() >= stz.monat_termine(monat)["frist"]:
        return False, "gesperrt"
    art = str(daten.get("art") or "").strip().lower()
    alt = _eintrag(conn, wid, iso)
    p = stz._plan_laden(conn, wid)[stz.WOCHENTAGE[d.weekday()]]
    beginn = (alt["start_time"] if alt else None) or (p["start"] if p["aktiv"] else None)
    ende = (alt["end_time"] if alt else None) or (p["ende"] if p["aktiv"] else None)
    ort = (alt["place"] if alt else None) or (p["ort"] if p["aktiv"] else "")
    if ort in stz.SONDER_ORTE and p["aktiv"]:
        ort_normal = p["ort"]
    else:
        ort_normal = ort if ort not in stz.SONDER_ORTE else ""

    if art == "stimmt":
        if not (beginn and ende):
            return True, ""  # freier Tag bleibt frei
        _speichern(conn, wid, iso, beginn[:5], ende[:5], ort)
    elif art in ("krank", "urlaub"):
        if not (beginn and ende):
            return False, "keine Uhrzeit"
        _speichern(conn, wid, iso, beginn[:5], ende[:5], "Krank" if art == "krank" else "Urlaub")
    elif art == "frei":
        if alt:
            _loeschen(conn, wid, iso)
    elif art == "zeiten":
        b, e = str(daten.get("beginn") or "")[:5], str(daten.get("ende") or "")[:5]
        neuer_ort = str(daten.get("ort") or "").strip()
        if not (stz._zeit_ok(b) and stz._zeit_ok(e)) or b == e:
            return False, "Uhrzeit fehlt"
        if neuer_ort not in stz.ORTE or neuer_ort in stz.SONDER_ORTE:
            return False, "Ort fehlt"
        _speichern(conn, wid, iso, b, e, neuer_ort)
    elif art == "extra":
        try:
            plus = float(str(daten.get("stunden") or 0).replace(",", "."))
        except ValueError:
            plus = 0
        if not (0 < plus <= 6) or not (beginn and ende):
            return False, "Extra unklar"
        h, m = [int(x) for x in str(ende)[:5].split(":")]
        neu = h * 60 + m + round(plus * 60)
        if neu >= 24 * 60:
            return False, "über Mitternacht"
        _speichern(conn, wid, iso, beginn[:5], f"{neu // 60:02d}:{neu % 60:02d}", ort_normal or ort)
    else:
        return False, "unbekannt"

    neu_row = _eintrag(conn, wid, iso)
    vorher_text, nachher_text = stz._eintrag_text(alt), stz._eintrag_text(neu_row) if neu_row else "frei"
    if vorher_text != nachher_text:
        stz._korrektur_merken(conn, wid, monat, iso, vorher_text, nachher_text, "app")
    return True, ""


def register_stundenzettel_app(app, get_db_connection):
    global _get_db
    _get_db = get_db_connection

    @app.route("/stundenzettel/worker/<string:code>/neu")
    def stz_app_seite(code):
        conn = get_db_connection()
        try:
            w = _worker_zum_code(conn, code)
            if not w:
                return "<h1>⚠️ Zugriff verweigert / Geçersiz Link</h1>", 403
            sprache = stz.sprache_laden(conn, w["id"])
        finally:
            conn.close()
        return render_template("stundenzettel_neu.html", code=code, vorname=(w["vorname"] or "").strip(),
                               name=f"{w['vorname'] or ''} {w['nachname'] or ''}".strip(),
                               sprache=sprache if sprache in ("de", "tr") else "de")

    @app.route("/api/stundenzettel/neu/<string:code>/monat")
    def stz_app_monat(code):
        conn = get_db_connection()
        try:
            w = _worker_zum_code(conn, code)
            if not w:
                return jsonify({"ok": False}), 403
            daten = monat_daten(conn, w["id"], request.args.get("m"))
            if daten["monat"] == stz.berlin_jetzt().strftime("%Y-%m"):
                stz._monat_speichern(conn, w["id"], daten["monat"], app_geoeffnet_am=stz.berlin_jetzt().isoformat(timespec="seconds"))
                conn.commit()
            daten["resturlaub"] = w["resturlaub"]
            return jsonify({"ok": True, **daten})
        finally:
            conn.close()

    @app.route("/api/stundenzettel/neu/<string:code>/tag", methods=["POST"])
    def stz_app_tag(code):
        conn = get_db_connection()
        try:
            w = _worker_zum_code(conn, code)
            if not w:
                return jsonify({"ok": False}), 403
            _spalten_anlegen(conn)
            ok, fehler = tag_aendern(conn, w["id"], request.get_json(silent=True) or {})
            if not ok:
                conn.rollback()
                return jsonify({"ok": False, "fehler": fehler}), 423 if fehler == "gesperrt" else 400
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/stundenzettel/neu/<string:code>/bestaetigen", methods=["POST"])
    def stz_app_bestaetigen(code):
        conn = get_db_connection()
        try:
            w = _worker_zum_code(conn, code)
            if not w:
                return jsonify({"ok": False}), 403
            _spalten_anlegen(conn)
            _start, monat = _monat((request.get_json(silent=True) or {}).get("monat"))
            if stz.monat_gesperrt(conn, w["id"], monat + "-01") or stz.berlin_jetzt() >= stz.monat_termine(monat)["frist"]:
                return jsonify({"ok": False, "fehler": "gesperrt"}), 423
            # alle Einträge des Monats gelten als vom Mitarbeiter bestätigt
            conn.execute("UPDATE work_logs SET signed = 1 WHERE worker_id = ? AND datum LIKE ?", (w["id"], monat + "-%"))
            jetzt = stz.berlin_jetzt().isoformat(timespec="seconds")
            stz._monat_speichern(conn, w["id"], monat, app_bestaetigt_am=jetzt)
            conn.commit()
            return jsonify({"ok": True, "bestaetigt_am": jetzt})
        finally:
            conn.close()
