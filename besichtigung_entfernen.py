# Besichtigung „Entfernen“ (Besichtigung-Seite): eine Firma nur aus den Besichtigung-Listen nehmen
# (Besichtigung-Seite, Datenbank-Liste „Besichtigung“, KG-Scan-App). Es wird nichts gelöscht:
# Tagesliste, Status-Sicherung, Verlauf und Datenbank bleiben, wie sie sind. Die Firma steht nur in der
# Tabelle besichtigung_entfernt (zurueck_am leer = ausgeblendet).
# Zurück: in der Datenbank (Liste „Besichtigung“ → „Entfernte Besichtigungen“) oder wie gewohnt über den
# Status „Besichtigung“ (Route /datenbank/tagesliste-status setzt zurueck_am).
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import jsonify, request

BERLIN = ZoneInfo("Europe/Berlin")

# Für Abfragen in app2.py / besichtigung_uebersicht.py: IDs, die gerade ausgeblendet sind
AUSGEBLENDET_SQL = "SELECT tagesliste_id FROM besichtigung_entfernt WHERE zurueck_am IS NULL"


def ensure_besichtigung_entfernt(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS besichtigung_entfernt (
            tagesliste_id INTEGER PRIMARY KEY,
            firma TEXT,
            ort TEXT,
            entfernt_am TEXT,
            zurueck_am TEXT
        )
    """)
    conn.commit()


def _jetzt():
    return datetime.now(BERLIN).strftime("%Y-%m-%d %H:%M:%S")


def _id(daten):
    try:
        return int((daten or {}).get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _eintrag(conn, tid):
    """Die Firma, wie sie in den Besichtigung-Listen steht (Tagesliste im Status „besichtigung“ oder Status-Sicherung)."""
    zeile = conn.execute("SELECT * FROM tagesliste_leads WHERE id = ? AND status = 'besichtigung'", (tid,)).fetchone()
    if zeile:
        return zeile
    return conn.execute("SELECT * FROM tagesliste_status_backup WHERE tagesliste_id = ? AND status = 'besichtigung'",
                        (tid,)).fetchone()


def register_besichtigung_entfernen(app, login_required, get_db_connection):

    conn = get_db_connection()
    try:
        ensure_besichtigung_entfernt(conn)
    finally:
        conn.close()

    @app.route("/api/besichtigung/entfernen", methods=["POST"])
    @login_required
    def besichtigung_entfernen():
        tid = _id(request.get_json(silent=True))
        if tid <= 0:
            return jsonify({"ok": False, "message": "ID fehlt."}), 400
        conn = get_db_connection()
        try:
            ensure_besichtigung_entfernt(conn)
            e = _eintrag(conn, tid)
            if not e:
                return jsonify({"ok": False, "message": "Diese Firma steht nicht in den Besichtigungsterminen."}), 404
            ort = " ".join(x for x in (str(e["plz"] or "").strip(), str(e["ort"] or "").strip()) if x)
            conn.execute("""
                INSERT INTO besichtigung_entfernt (tagesliste_id, firma, ort, entfernt_am, zurueck_am)
                VALUES (?, ?, ?, ?, NULL)
                ON CONFLICT(tagesliste_id) DO UPDATE SET
                    firma = excluded.firma,
                    ort = excluded.ort,
                    entfernt_am = excluded.entfernt_am,
                    zurueck_am = NULL
            """, (tid, e["firma"] or "", ort, _jetzt()))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "message": "Aus den Besichtigungsterminen entfernt."})

    @app.route("/api/besichtigung/zurueck", methods=["POST"])
    @login_required
    def besichtigung_zurueck():
        tid = _id(request.get_json(silent=True))
        if tid <= 0:
            return jsonify({"ok": False, "message": "ID fehlt."}), 400
        conn = get_db_connection()
        try:
            ensure_besichtigung_entfernt(conn)
            n = conn.execute("UPDATE besichtigung_entfernt SET zurueck_am = ? WHERE tagesliste_id = ? AND zurueck_am IS NULL",
                             (_jetzt(), tid)).rowcount
            conn.commit()
        finally:
            conn.close()
        if not n:
            return jsonify({"ok": False, "message": "Diese Firma ist nicht entfernt."}), 404
        return jsonify({"ok": True, "message": "Wieder in den Besichtigungsterminen."})

    @app.route("/api/besichtigung/entfernt")
    @login_required
    def besichtigung_entfernt_liste():
        """Entfernte Besichtigungen, die man wieder aufnehmen kann (nur solche, die noch eine Besichtigung haben)."""
        conn = get_db_connection()
        try:
            ensure_besichtigung_entfernt(conn)
            rows = conn.execute("SELECT * FROM besichtigung_entfernt WHERE zurueck_am IS NULL ORDER BY entfernt_am DESC").fetchall()
            liste = [{"id": r["tagesliste_id"], "firma": r["firma"] or "", "ort": r["ort"] or "", "entfernt_am": r["entfernt_am"] or ""}
                     for r in rows if _eintrag(conn, r["tagesliste_id"])]
        finally:
            conn.close()
        return jsonify({"ok": True, "eintraege": liste})
