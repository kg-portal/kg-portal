# Startseite: Kasten „Objekte“ unter den 8 Kacheln – Bestandskunden als Mosaik (Fläche = Umsatz / Monat).
# Klick auf ein Objekt: Mitarbeiter dort, Stunden, Lohn und Ergebnis im gewählten Monat.
# Liest kunden, work_logs und mitarbeiter nur. Schreibt nur die eigene Tabelle startseite_einsatzorte
# (welcher Einsatzort aus dem Stundenzettel zu welchem Kunden gehört).
from datetime import date, datetime
from zoneinfo import ZoneInfo

from flask import jsonify, request

from stundenzettel_auto import MONATE, ORTE, SONDER_ORTE, STUNDENLOHN, _stunden

BERLIN = ZoneInfo("Europe/Berlin")


def _tabelle(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS startseite_einsatzorte (
            ort TEXT PRIMARY KEY,
            kunde_id INTEGER NOT NULL,
            geaendert_am TEXT
        )
    """)


def _betrag(wert):
    """Betrag wie im Formular eingegeben: „450“, „450,50“, „1.250,00 €“, „1250.5“ → Zahl (sonst 0)."""
    t = str(wert or "").replace("€", "").replace("\u00a0", "").replace(" ", "").strip()
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    elif t.count(".") > 1:
        t = t.replace(".", "")
    try:
        return float(t or 0)
    except ValueError:
        return 0.0


def _schluessel(ort):
    return " ".join(str(ort or "").split()).lower()


def _monat(wert, heute):
    """'YYYY-MM' → (start, ende_exklusiv). Ohne Angabe: der laufende Monat."""
    try:
        jahr, mon = [int(x) for x in str(wert or "").split("-")[:2]]
        start = date(jahr, mon, 1)
    except (ValueError, TypeError):
        start = heute.replace(day=1)
    erster = heute.replace(day=1)
    if start > erster:
        start = erster
    ende = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, ende


def _kundenart(k):
    """Wie auf der Kunden-Seite: nur Bestandskunden haben einen festen Betrag je Monat."""
    if str(k["vertragsstatus"] or "").strip().lower() == "gekuendigt":
        return "gekuendigt"
    f = str(k["haeufigkeit"] or "").lower()
    if "einmal" in f:
        return "einmalig"
    if "jährlich" in f or "jaehrlich" in f:
        return "jaehrlich"
    return "bestand"


def objekte_daten(conn, monat=None):
    heute = datetime.now(BERLIN).date()
    start, ende = _monat(monat, heute)
    _tabelle(conn)

    kunden = {}
    for k in conn.execute("SELECT id, firma, ort, monat, haeufigkeit, vertragsstatus FROM kunden").fetchall():
        betrag = _betrag(k["monat"])
        kunden[k["id"]] = {
            "id": k["id"], "firma": (k["firma"] or "").strip() or "Ohne Namen", "ort": (k["ort"] or "").strip(),
            "freq": (k["haeufigkeit"] or "").strip(), "bestand": _kundenart(k) == "bestand",
            "umsatz": round(betrag, 2) if _kundenart(k) == "bestand" else 0.0,
            "orte": [], "stunden": 0.0, "lohn": 0.0, "mitarbeiter": {},
        }

    zuordnung = {}
    for z in conn.execute("SELECT ort, kunde_id FROM startseite_einsatzorte").fetchall():
        if z["kunde_id"] in kunden:
            zuordnung[_schluessel(z["ort"])] = z["kunde_id"]
            kunden[z["kunde_id"]]["orte"].append(z["ort"])

    # Stunden im Monat je Einsatzort und Mitarbeiter (Krank/Urlaub/Feiertag gehören zu keinem Objekt)
    offen = {}
    lohn_gesamt = 0.0
    zeilen = conn.execute("""
        SELECT w.worker_id, w.start_time, w.end_time, w.place, m.vorname, m.nachname, m.stundenlohn
        FROM work_logs w LEFT JOIN mitarbeiter m ON m.id = w.worker_id
        WHERE w.datum >= ? AND w.datum < ?
    """, (start.isoformat(), ende.isoformat())).fetchall()
    for z in zeilen:
        ort = " ".join(str(z["place"] or "").split())
        if not ort or ort in SONDER_ORTE or not (z["start_time"] and z["end_time"]):
            continue
        h = _stunden(z["start_time"], z["end_time"])
        if h <= 0:
            continue
        satz = _betrag(z["stundenlohn"]) or STUNDENLOHN
        if satz < 0:
            satz = STUNDENLOHN
        name = " ".join(x for x in ((z["vorname"] or "").strip(), (z["nachname"] or "").strip()) if x) or f"Mitarbeiter {z['worker_id']}"
        lohn_gesamt += h * satz
        kid = zuordnung.get(_schluessel(ort))
        if kid is None:
            o = offen.setdefault(_schluessel(ort), {"ort": ort, "stunden": 0.0, "lohn": 0.0, "mitarbeiter": set()})
            o["stunden"] += h
            o["lohn"] += h * satz
            o["mitarbeiter"].add(z["worker_id"])
            continue
        k = kunden[kid]
        k["stunden"] += h
        k["lohn"] += h * satz
        m = k["mitarbeiter"].setdefault(z["worker_id"], {"name": name, "stunden": 0.0, "lohn": 0.0, "satz": satz})
        m["stunden"] += h
        m["lohn"] += h * satz

    liste = []
    for k in kunden.values():
        if not (k["bestand"] and k["umsatz"] > 0) and not k["stunden"]:
            continue
        k["mitarbeiter"] = sorted(
            ({**m, "stunden": round(m["stunden"], 2), "lohn": round(m["lohn"], 2)} for m in k["mitarbeiter"].values()),
            key=lambda m: -m["stunden"])
        k["stunden"] = round(k["stunden"], 2)
        k["lohn"] = round(k["lohn"], 2)
        k["orte"].sort(key=str.lower)
        # Ergebnis nur, wenn dem Objekt ein Einsatzort zugeordnet ist – sonst wäre der Lohn unbekannt
        k["ergebnis"] = round(k["umsatz"] - k["lohn"], 2) if k["orte"] else None
        del k["bestand"]
        liste.append(k)
    liste.sort(key=lambda k: (-k["umsatz"], -k["lohn"], k["firma"].lower()))

    # Auswahl für die Zuordnung: feste Orte aus dem Stundenzettel + alle Orte der letzten 13 Monate
    ab = date(start.year - 1, start.month, 1).isoformat()
    namen = {}
    for o in ORTE + [r[0] for r in conn.execute("SELECT DISTINCT place FROM work_logs WHERE datum >= ?", (ab,)).fetchall()]:
        o = " ".join(str(o or "").split())
        if o and o not in SONDER_ORTE:
            namen.setdefault(_schluessel(o), o)
    for o in conn.execute("SELECT ort FROM startseite_einsatzorte").fetchall():
        namen.setdefault(_schluessel(o["ort"]), o["ort"])
    orte_alle = []
    for s, o in sorted(namen.items(), key=lambda x: x[1].lower()):
        kid = zuordnung.get(s)
        orte_alle.append({"ort": o, "kunde_id": kid, "kunde": kunden[kid]["firma"] if kid else None})

    erster = heute.replace(day=1)
    zugeordnet = [k for k in liste if k["ergebnis"] is not None]
    return {
        "ok": True,
        "monat": start.strftime("%Y-%m"),
        "monat_name": f"{MONATE[start.month - 1]} {start.year}",
        "laeuft": start == erster,
        "max": erster.strftime("%Y-%m"),
        "min": date(erster.year - 1, erster.month, 1).strftime("%Y-%m"),
        "kunden": liste,
        "offen": sorted(({"ort": o["ort"], "stunden": round(o["stunden"], 2), "lohn": round(o["lohn"], 2),
                          "mitarbeiter": len(o["mitarbeiter"])} for o in offen.values()), key=lambda o: -o["stunden"]),
        "orte_alle": orte_alle,
        "summe": {
            "umsatz": round(sum(k["umsatz"] for k in liste), 2),
            "lohn": round(lohn_gesamt, 2),
            "umsatz_zugeordnet": round(sum(k["umsatz"] for k in zugeordnet), 2),
            "lohn_zugeordnet": round(sum(k["lohn"] for k in zugeordnet), 2),
            "ergebnis": round(sum(k["ergebnis"] for k in zugeordnet), 2),
            "objekte_zugeordnet": len(zugeordnet),
        },
    }


def register_startseite_objekte(app, login_required, get_db_connection):

    @app.route("/api/startseite/objekte")
    @login_required
    def startseite_objekte():
        conn = get_db_connection()
        try:
            daten = objekte_daten(conn, request.args.get("monat"))
            conn.commit()
            return jsonify(daten)
        except Exception as exc:
            print("Startseite-Objekte: nicht geladen:", exc)
            return jsonify({"ok": False, "error": "Daten konnten nicht geladen werden."}), 500
        finally:
            conn.close()

    @app.route("/api/startseite/objekte/zuordnung", methods=["POST"])
    @login_required
    def startseite_objekte_zuordnung():
        d = request.get_json(silent=True) or {}
        ort = " ".join(str(d.get("ort") or "").split())[:120]
        if not ort or ort in SONDER_ORTE:
            return jsonify({"ok": False, "error": "Einsatzort fehlt."}), 400
        kunde_id = d.get("kunde_id")
        conn = get_db_connection()
        try:
            _tabelle(conn)
            # gleicher Ort in anderer Schreibweise: alte Zeile ersetzen
            for z in conn.execute("SELECT ort FROM startseite_einsatzorte").fetchall():
                if _schluessel(z["ort"]) == _schluessel(ort):
                    conn.execute("DELETE FROM startseite_einsatzorte WHERE ort = ?", (z["ort"],))
            if kunde_id not in (None, "", 0):
                try:
                    kunde_id = int(kunde_id)
                except (TypeError, ValueError):
                    return jsonify({"ok": False, "error": "Kunde unbekannt."}), 400
                if not conn.execute("SELECT 1 FROM kunden WHERE id = ?", (kunde_id,)).fetchone():
                    return jsonify({"ok": False, "error": "Kunde unbekannt."}), 400
                conn.execute("INSERT INTO startseite_einsatzorte (ort, kunde_id, geaendert_am) VALUES (?, ?, ?)",
                             (ort, kunde_id, datetime.now(BERLIN).isoformat(timespec="seconds")))
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()
