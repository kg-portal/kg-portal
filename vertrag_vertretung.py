# =====================================================
# VERTRAG (Kunden-Reinigungsvertrag) und VERTRETUNG (Personal)
# Füllt die bisher leeren Menüpunkte /vertrag und /vertretung.
#
# Vertrag: Kunde oder Angebot wählen → Felder vorbefüllt → druckfertiger
#          Reinigungsvertrag (Browser: Drucken / als PDF speichern).
# Vertretung: Woche ansehen – wer fehlt (Urlaub/Krank), welche Objekte laut
#          festen Zeiten betroffen sind, wer frei ist; Vertretung mit einem
#          Klick in den Stundenzettel der Vertretung eintragen.
#          Abwesenheiten werden über die bestehende Stundenzettel-Speicherung
#          eingetragen (gleiche Urlaubsrechnung wie bisher).
# =====================================================
import json
from datetime import date, datetime, timedelta

from flask import jsonify, render_template, request

from stundenzettel_auto import WOCHENTAGE, WOCHENTAGE_LANG, ensure_tables as stz_tables, _plan_laden

FIRMA = {
    "name": "KG-Gebäudereinigung",
    "inhaber": "Damla Kicci",
    "strasse": "Fliederstr. 59",
    "ort": "47055 Duisburg",
    "telefon": "0203 47966822",
    "email": "info@kg-reinigung.de",
    "web": "www.kg-reinigung.de",
    "ustid": "DE325770756",
    "steuernr": "134/5102/3515",
    "bank": "Sparkasse Duisburg",
    "iban": "DE58 3505 0000 0200 3259 34",
    "bic": "DUISDE33XXX",
}


def _zahl(v, standard=0.0):
    try:
        return float(str(v).replace(".", "").replace(",", ".")) if isinstance(v, str) and "," in v else float(v)
    except (TypeError, ValueError):
        return standard


def _euro(v):
    s = f"{_zahl(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{s} €"


def _datum_de(iso):
    try:
        return datetime.strptime(str(iso)[:10], "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return str(iso or "")


def ensure_vertretung_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vertretungen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            datum TEXT NOT NULL,
            abwesend_id INTEGER,
            vertreter_id INTEGER NOT NULL,
            objekt TEXT,
            start_time TEXT,
            end_time TEXT,
            erstellt_am TEXT
        )
    """)
    conn.commit()


def register_vertrag_vertretung(app, login_required, get_db_connection):

    # ------------------------------------------------- Vertrag
    @app.route("/vertrag")
    @login_required
    def vertrag_seite():
        conn = get_db_connection()
        try:
            kunden = [dict(r) for r in conn.execute(
                "SELECT id, firma, strasse, plz, ort, ansprechpartner_name, email, monat, haeufigkeit, "
                "vertrag_beginn, vertrag_ende, vertragslaufzeit, kundennummer FROM kunden ORDER BY firma COLLATE NOCASE")]
            angebote = [dict(r) for r in conn.execute(
                "SELECT id, firma, ansprechpartner, strasse, plz, ort, m2, reinigungsart, haeufigkeit, status, created_at "
                "FROM angebote ORDER BY id DESC LIMIT 200")]
        finally:
            conn.close()
        return render_template("vertrag.html", kunden=kunden, angebote=angebote)

    @app.route("/vertrag/erstellen", methods=["POST"])
    @login_required
    def vertrag_erstellen():
        f = request.form
        beginn = f.get("beginn") or date.today().isoformat()
        laufzeit = int(_zahl(f.get("laufzeit"), 12)) or 12
        try:
            b = date.fromisoformat(beginn)
            m = b.month - 1 + laufzeit
            ende = date(b.year + m // 12, m % 12 + 1, 1) - timedelta(days=1)
        except ValueError:
            b, ende = date.today(), date.today()
        netto = _zahl(f.get("netto"), 0)
        daten = {
            "firma": FIRMA,
            "kunde": {
                "firma": f.get("firma", "").strip(),
                "strasse": f.get("strasse", "").strip(),
                "ort": f"{f.get('plz', '').strip()} {f.get('ort', '').strip()}".strip(),
                "ansprechpartner": f.get("ansprechpartner", "").strip(),
                "email": f.get("email", "").strip(),
                "kundennummer": f.get("kundennummer", "").strip(),
            },
            "objekt": f.get("objekt", "").strip() or f"{f.get('strasse', '').strip()}, {f.get('plz', '').strip()} {f.get('ort', '').strip()}",
            "leistung": f.get("leistung", "").strip(),
            "haeufigkeit": f.get("haeufigkeit", "").strip(),
            "flaeche": f.get("flaeche", "").strip(),
            "zeiten": f.get("zeiten", "").strip(),
            "netto": _euro(netto),
            "ust": _euro(netto * 0.19),
            "brutto": _euro(netto * 1.19),
            "beginn": _datum_de(b.isoformat()),
            "ende": _datum_de(ende.isoformat()),
            "laufzeit": laufzeit,
            "kuendigung": int(_zahl(f.get("kuendigung"), 3)) or 3,
            "verlaengerung": int(_zahl(f.get("verlaengerung"), 12)) or 12,
            "zahlungsziel": int(_zahl(f.get("zahlungsziel"), 14)) or 14,
            "material": f.get("material") != "kunde",
            "besonderes": f.get("besonderes", "").strip(),
            "datum": date.today().strftime("%d.%m.%Y"),
        }
        return render_template("kunden_vertrag.html", **daten)

    # ------------------------------------------------- Vertretung
    def _woche(param):
        try:
            tag = date.fromisoformat(param) if param else date.today()
        except ValueError:
            tag = date.today()
        start = tag - timedelta(days=tag.weekday())
        return start

    @app.route("/vertretung")
    @login_required
    def vertretung_seite():
        return render_template("vertretung.html")

    @app.route("/api/vertretung/woche")
    @login_required
    def vertretung_woche():
        start = _woche(request.args.get("von"))
        ende = start + timedelta(days=7)
        conn = get_db_connection()
        try:
            stz_tables(conn)
            ensure_vertretung_table(conn)
            workers = [dict(r) for r in conn.execute(
                "SELECT id, vorname, nachname, telefon FROM mitarbeiter WHERE status = 'aktiv' ORDER BY sort_order, vorname")]
            for w in workers:
                w["name"] = f"{w['vorname'] or ''} {w['nachname'] or ''}".strip()
                w["plan"] = _plan_laden(conn, w["id"])
            logs = {}
            for r in conn.execute("SELECT worker_id, datum, start_time, end_time, place FROM work_logs WHERE datum >= ? AND datum < ?",
                                  (start.isoformat(), ende.isoformat())):
                logs[(r["worker_id"], r["datum"])] = dict(r)
            vertretungen = [dict(r) for r in conn.execute(
                "SELECT * FROM vertretungen WHERE datum >= ? AND datum < ? ORDER BY datum", (start.isoformat(), ende.isoformat()))]
            tage = []
            for i in range(6):  # Mo–Sa
                d = start + timedelta(days=i)
                iso = d.isoformat()
                tag = {"datum": iso, "label": f"{WOCHENTAGE_LANG[i]} {d.strftime('%d.%m.')}", "abwesend": [], "frei": []}
                for w in workers:
                    log = logs.get((w["id"], iso))
                    p = w["plan"][WOCHENTAGE[i]]
                    if log and (log["place"] or "") in ("Urlaub", "Krank"):
                        offen = None
                        if p["aktiv"]:
                            schon = next((v for v in vertretungen if v["datum"] == iso and v["abwesend_id"] == w["id"]), None)
                            offen = {"objekt": p["ort"], "start": p["start"], "ende": p["ende"],
                                     "vertretung": schon and next((x["name"] for x in workers if x["id"] == schon["vertreter_id"]), "?")}
                        tag["abwesend"].append({"id": w["id"], "name": w["name"], "grund": log["place"], "einsatz": offen})
                    elif not log and not p["aktiv"]:
                        tag["frei"].append({"id": w["id"], "name": w["name"], "telefon": w["telefon"] or ""})
                    elif not log or (log["place"] or "") not in ("Urlaub", "Krank"):
                        # arbeitet – als "evtl. verfügbar" nur, wenn andere Uhrzeit
                        einsatz = log or (p if p["aktiv"] else None)
                        tag.setdefault("arbeitet", []).append({
                            "id": w["id"], "name": w["name"],
                            "zeit": f"{(einsatz or {}).get('start_time') or (einsatz or {}).get('start') or ''}–{(einsatz or {}).get('end_time') or (einsatz or {}).get('ende') or ''}",
                            "objekt": (einsatz or {}).get("place") or (einsatz or {}).get("ort") or ""})
                tage.append(tag)
            return jsonify({"success": True, "von": start.isoformat(), "tage": tage,
                            "mitarbeiter": [{"id": w["id"], "name": w["name"]} for w in workers]})
        finally:
            conn.close()

    def _intern_speichern(worker_id, eintraege):
        """Über die bestehende Stundenzettel-Route speichern (gleiche Urlaubslogik)."""
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        resp = client.post("/api/stundenzettel/save", json={"worker_id": worker_id, "entries": eintraege})
        data = resp.get_json(silent=True) or {}
        if not data.get("success"):
            raise ValueError(data.get("error") or f"Speichern fehlgeschlagen (HTTP {resp.status_code}).")

    @app.route("/api/vertretung/abwesenheit", methods=["POST"])
    @login_required
    def vertretung_abwesenheit():
        d = request.get_json(silent=True) or {}
        grund = "Krank" if d.get("grund") == "Krank" else "Urlaub"
        try:
            wid = int(d.get("worker_id"))
            von = date.fromisoformat(d.get("von"))
            bis = date.fromisoformat(d.get("bis") or d.get("von"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Mitarbeiter und Datum angeben."}), 400
        if bis < von or (bis - von).days > 62:
            return jsonify({"success": False, "error": "Zeitraum prüfen (höchstens 2 Monate)."}), 400
        eintraege, tag = [], von
        while tag <= bis:
            if tag.weekday() < 6:
                eintraege.append({"date": tag.isoformat(), "start": "", "end": "", "place": grund, "signed": False})
            tag += timedelta(days=1)
        try:
            _intern_speichern(wid, eintraege)
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        return jsonify({"success": True, "tage": len(eintraege)})

    @app.route("/api/vertretung/eintragen", methods=["POST"])
    @login_required
    def vertretung_eintragen():
        d = request.get_json(silent=True) or {}
        try:
            vertreter = int(d.get("vertreter_id"))
            abwesend = int(d.get("abwesend_id")) if d.get("abwesend_id") else None
            datum = date.fromisoformat(d.get("datum")).isoformat()
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Vertretung, Datum angeben."}), 400
        objekt = str(d.get("objekt") or "").strip()[:120]
        start, ende = str(d.get("start") or "")[:5], str(d.get("ende") or "")[:5]
        if not (objekt and start and ende):
            return jsonify({"success": False, "error": "Objekt, Beginn und Ende fehlen."}), 400
        conn = get_db_connection()
        try:
            ensure_vertretung_table(conn)
            vorhanden = conn.execute("SELECT place FROM work_logs WHERE worker_id = ? AND datum = ?", (vertreter, datum)).fetchone()
        finally:
            conn.close()
        if vorhanden:
            return jsonify({"success": False, "error": f"Die Vertretung hat an diesem Tag schon einen Eintrag ({vorhanden['place']})."}), 409
        try:
            _intern_speichern(vertreter, [{"date": datum, "start": start, "end": ende, "place": objekt, "signed": False}])
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        conn = get_db_connection()
        try:
            conn.execute("INSERT INTO vertretungen (datum, abwesend_id, vertreter_id, objekt, start_time, end_time, erstellt_am) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (datum, abwesend, vertreter, objekt, start, ende, datetime.now().isoformat(timespec="seconds")))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True})
