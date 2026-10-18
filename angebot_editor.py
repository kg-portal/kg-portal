# Angebot-Editor: Besichtigung → Angebot an einem Ort.
# Links die offenen Besichtigungen und verschickten Angebote, in der Mitte das Formular (Felder der Angebotsvorlage,
# Leistung 1–4, Richtpreis-Rechner), rechts die echte Angebotsvorlage als Live-Vorschau.
# „Senden“ vergibt die Angebots-Nr (gleiche Nummernfolge wie bisher), setzt den Status auf „angebot“ (Warteliste)
# und öffnet die vorhandene Mail (KG-Mail mit PDF + LV). besichtigung_data_json wird dabei NICHT verändert.
# Entwürfe liegen in der eigenen Tabelle angebot_editor_entwuerfe.
# /tablet/angebot zeigt dieselbe Seite ohne CRM-Menü (für das Tablet).
import html
import json
import re
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from flask import jsonify, render_template, request

import besichtigung_zeit as bz

BERLIN = ZoneInfo("Europe/Berlin")
EINHEITEN = ["monatlich", "einmalig", "pro Durchgang", "jährlich", "pro Stunde"]
MWST = 0.19
BEREICHE = [("buero", "Büro"), ("wc", "WC / Sanitär"), ("kueche", "Küche"), ("flur", "Flur / Eingang")]


def _tabelle(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS angebot_editor_entwuerfe (
            tagesliste_id INTEGER PRIMARY KEY,
            felder_json TEXT,
            kalkulation_json TEXT,
            geaendert_am TEXT
        )
    """)


def _jetzt():
    return datetime.now(BERLIN).isoformat(timespec="seconds")


def _json(text, leer):
    try:
        wert = json.loads(text or "")
        return wert if isinstance(wert, type(leer)) else leer
    except (TypeError, ValueError):
        return leer


def _txt(v):
    return " ".join(str(v or "").split())


def _zahl(text):
    t = str(text or "").replace("€", "").replace(" ", "").strip()
    if not t:
        return 0.0
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?", t)
        return float(m.group(0)) if m else 0.0


def _euro(v):
    return f"{float(v or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _heute():
    return datetime.now(BERLIN).strftime("%d.%m.%Y")


# ---------------------------------------------------------------- Aufmaß aus der Besichtigung

def aufmass(daten):
    """Flächen je Bereich (m², Häufigkeit) + Stückzahlen aus Besichtigung/KG Scan."""
    bereiche = {k: {"key": k, "name": n, "m2": 0.0, "haeufigkeit": "", "raeume": 0} for k, n in BEREICHE}
    for r in daten.get("raeume") or []:
        if not isinstance(r, dict):
            continue
        key = bz.bereich_schluessel(f"{r.get('typ', '')} {r.get('name', '')}") or bz.bereich_schluessel(r.get("section"))
        if key not in bereiche:
            key = "buero" if not key else None
        if not key:
            continue
        b = bereiche[key]
        b["m2"] += bz._zahl(r.get("m2") or r.get("area"))
        b["raeume"] += 1
        if not b["haeufigkeit"] and _txt(r.get("haeufigkeit")):
            b["haeufigkeit"] = _txt(r.get("haeufigkeit"))
    scan = daten.get("kg_scan") if isinstance(daten.get("kg_scan"), dict) else {}
    elemente = daten.get("elemente") if isinstance(daten.get("elemente"), dict) else {}

    def stueck(*werte):
        for w in werte:
            z = bz._zahl(w)
            if z:
                return int(z)
        return 0

    return {
        "bereiche": [dict(b, m2=round(b["m2"], 1)) for b in bereiche.values()],
        "stueck": {
            "wc": stueck(scan.get("toilets")), "urinal": stueck(scan.get("urinals")), "wb": stueck(scan.get("sinks")),
            "schreibtisch": stueck(elemente.get("arbeitstische"), scan.get("tables")), "muell": stueck(elemente.get("muellbehaelter")),
        },
        "scan": bool(scan),
        "leistungen": [_txt(x) for x in daten.get("leistungen") or [] if _txt(x)],
    }


def koeffizienten(stundensatz):
    return {
        "monat": bz.MONAT,
        "leistungswert": {k: bz.LEISTUNGSWERT[k] for k, _ in BEREICHE},
        "minuten": {"wc": bz.MIN_WC, "urinal": bz.MIN_URINAL, "wb": bz.MIN_WB, "schreibtisch": bz.MIN_SCHREIBTISCH,
                    "muell": bz.MIN_MUELL, "ruest": bz.MIN_RUESTZEIT},
        "verschmutzung": bz.VERSCHMUTZUNG,
        "stundensatz": float(stundensatz),
        "haeufigkeiten": ["1x wöchentlich", "2x wöchentlich", "3x wöchentlich", "4x wöchentlich", "5x wöchentlich", "täglich",
                          "14-tägig", "1x monatlich", "keine"],
        "einsaetze": {h: bz.einsaetze_pro_monat(h) for h in ["1x wöchentlich", "2x wöchentlich", "3x wöchentlich", "4x wöchentlich",
                                                               "5x wöchentlich", "täglich", "14-tägig", "1x monatlich", "keine"]},
    }


# ---------------------------------------------------------------- Felder ⇄ Vorlagen-Variablen

def _leer_felder(row, daten, am):
    kunde = daten.get("kunde") if isinstance(daten.get("kunde"), dict) else {}
    leistungsart = " und ".join(am["leistungen"][:2]) or "Büroreinigung"
    m2 = sum(b["m2"] for b in am["bereiche"])
    freq = next((b["haeufigkeit"] for b in am["bereiche"] if b["key"] == "buero" and b["haeufigkeit"]), "") or \
        next((b["haeufigkeit"] for b in am["bereiche"] if b["haeufigkeit"]), "")
    text = (f"{freq.replace('wöchentlich', 'wöchentliche').strip()} {leistungsart}" if freq else leistungsart)
    if m2:
        text += f" ({m2:.0f} m²)"
    return {
        "firma": _txt(kunde.get("firma") or row["firma"]),
        "ansprechpartner": _txt(kunde.get("ansprechpartner") or row["ansprechpartner"]),
        "objekt": "",
        "adresse": _txt(kunde.get("strasse") or row["strasse"]),
        "plz": _txt(kunde.get("plz") or row["plz"]),
        "ort": _txt(kunde.get("ort") or row["ort"]),
        "email": _txt(kunde.get("email") or row["email"]),
        "telefon": _txt(kunde.get("telefon") or row["telefon"]),
        "leistungsart": leistungsart,
        "positionen": [{"text": text, "einheit": "monatlich", "preis": ""}] + [{"text": "", "einheit": "monatlich", "preis": ""} for _ in range(3)],
        "ausfuehrungszeitraum": "nach Vereinbarung",
        "datum": _heute(),
    }


def _felder_aus_vars(v, row):
    kunde = str(v.get("Kunde") or "").split("<br>")
    pos = []
    for n in range(1, 5):
        preis = _zahl(v.get(f"Preis_{n}"))
        pos.append({"text": _txt(v.get(f"Leistung_{n}")), "einheit": _txt(v.get(f"Einheiten_{n}")) or "monatlich",
                    "preis": (f"{preis:.2f}".replace(".", ",") if preis else "")})
    return {
        "firma": html.unescape(kunde[0]).strip(), "ansprechpartner": html.unescape(kunde[1]).strip() if len(kunde) > 1 else _txt(v.get("Ansprechpartner")),
        "objekt": _txt(v.get("Objekt")), "adresse": _txt(v.get("Adresse")), "plz": _txt(v.get("Plz")), "ort": _txt(v.get("Ort")),
        "email": _txt(row["email"]), "telefon": _txt(row["telefon"]), "leistungsart": _txt(v.get("Leistungsart")),
        "positionen": pos, "ausfuehrungszeitraum": _txt(v.get("Ausführungszeitraum")) or "nach Vereinbarung", "datum": _txt(v.get("Datum")) or _heute(),
    }


def _sauber(f):
    """Formular-Eingabe prüfen und kürzen."""
    f = f if isinstance(f, dict) else {}
    pos = []
    for p in (f.get("positionen") or [])[:4]:
        p = p if isinstance(p, dict) else {}
        einheit = _txt(p.get("einheit"))[:40] or "monatlich"
        pos.append({"text": _txt(p.get("text"))[:300], "einheit": einheit, "preis": _txt(p.get("preis"))[:20]})
    while len(pos) < 4:
        pos.append({"text": "", "einheit": "monatlich", "preis": ""})
    aus = {k: _txt(f.get(k))[:200] for k in ("firma", "ansprechpartner", "objekt", "adresse", "plz", "ort", "email", "telefon",
                                              "leistungsart", "ausfuehrungszeitraum", "datum")}
    aus["positionen"] = pos
    return aus


def vorlage_vars(f, nr):
    """Felder → Variablen der templates/angebotvorlage.html (gleich wie angebot_build_template_vars)."""
    kunde = html.escape(f["firma"]) + ("<br>" + html.escape(f["ansprechpartner"]) if f["ansprechpartner"] else "")
    v = {"Nr": str(nr or ""), "Datum": f["datum"] or _heute(), "Kunde": kunde, "Ansprechpartner": f["ansprechpartner"],
         "Objekt": f["objekt"], "Adresse": f["adresse"], "Plz": f["plz"], "Ort": f["ort"], "Leistungsart": f["leistungsart"],
         "Ausführungszeitraum": f["ausfuehrungszeitraum"]}
    for n, p in enumerate(f["positionen"], 1):
        hat = bool(p["text"] or _zahl(p["preis"]))
        # leere Zeilen: Leerzeichen statt "-" (rechts bleibt die Zeile leer)
        v[f"Leistung_{n}"] = p["text"] if hat else " "
        v[f"Einheiten_{n}"] = p["einheit"] if hat else " "
        v[f"Preis_{n}"] = _euro(_zahl(p["preis"])) if _zahl(p["preis"]) else " "
    return v


def summen(f):
    monatlich = [_zahl(p["preis"]) for p in f["positionen"] if p["einheit"] == "monatlich" and _zahl(p["preis"])]
    alle = [_zahl(p["preis"]) for p in f["positionen"] if _zahl(p["preis"])]
    netto = round(sum(monatlich) if monatlich else sum(alle), 2)
    mwst = round(netto * MWST, 2)
    return netto, mwst, round(netto + mwst, 2)


# ---------------------------------------------------------------- Routen

def register_angebot_editor(app, login_required, get_db_connection, app2_modul):

    def lead_laden(cur, tid):
        row = cur.execute("SELECT * FROM tagesliste_leads WHERE id = ?", (tid,)).fetchone()
        if row:
            return row, False
        b = cur.execute("""SELECT * FROM tagesliste_status_backup WHERE tagesliste_id = ? AND status = 'besichtigung'
                           ORDER BY backup_am DESC LIMIT 1""", (tid,)).fetchone()
        return b, True

    def seite(layout):
        return render_template("angebot_editor.html", layout=layout, tablet=layout != "base.html",
                               start_id=request.args.get("id", type=int) or 0, einheiten=EINHEITEN)

    @app.route("/angebot/editor")
    @login_required
    def angebot_editor_seite():
        return seite("base.html")

    @app.route("/tablet/angebot")
    @login_required
    def angebot_editor_tablet():
        return seite("angebot_tablet_rahmen.html")

    @app.route("/api/angebot-editor/liste")
    @login_required
    def angebot_editor_liste():
        conn = get_db_connection()
        try:
            _tabelle(conn)
            entwuerfe = {r[0] for r in conn.execute("SELECT tagesliste_id FROM angebot_editor_entwuerfe").fetchall()}
            bes = []
            for r in conn.execute("""SELECT id, firma, ort, quelle, besichtigung_data_json FROM tagesliste_leads
                                     WHERE status = 'besichtigung' ORDER BY id DESC""").fetchall():
                d = _json(r["besichtigung_data_json"], {})
                bes.append({"id": r["id"], "firma": r["firma"] or "Ohne Firma", "ort": r["ort"] or "", "quelle": r["quelle"] or "",
                            "scan": isinstance(d.get("kg_scan"), dict), "raeume": len(d.get("raeume") or []), "entwurf": r["id"] in entwuerfe})
            live = {b["id"] for b in bes}
            try:
                for r in conn.execute("""SELECT tagesliste_id AS id, firma, ort, quelle FROM tagesliste_status_backup
                                         WHERE status = 'besichtigung' ORDER BY backup_am DESC""").fetchall():
                    if r["id"] in live or conn.execute("SELECT 1 FROM tagesliste_leads WHERE id = ? AND status = 'angebot'", (r["id"],)).fetchone():
                        continue
                    live.add(r["id"])
                    bes.append({"id": r["id"], "firma": r["firma"] or "Ohne Firma", "ort": r["ort"] or "", "quelle": r["quelle"] or "",
                                "scan": False, "raeume": 0, "entwurf": r["id"] in entwuerfe})
            except Exception:
                pass
            ang = [{"id": r["id"], "firma": r["firma"] or "Ohne Firma", "ort": r["ort"] or "", "nr": r["angebot_nr"] or "",
                    "datum": r["angebot_datum"] or "", "netto": float(r["angebot_netto"] or 0)}
                   for r in conn.execute("""SELECT id, firma, ort, angebot_nr, angebot_datum, angebot_netto FROM tagesliste_leads
                                            WHERE status = 'angebot' ORDER BY id DESC LIMIT 200""").fetchall()]
            conn.commit()
            return jsonify({"ok": True, "besichtigungen": bes, "angebote": ang})
        finally:
            conn.close()

    @app.route("/api/angebot-editor/<int:tid>")
    @login_required
    def angebot_editor_daten(tid):
        conn = get_db_connection()
        try:
            _tabelle(conn)
            cur = conn.cursor()
            row, aus_backup = lead_laden(cur, tid)
            if not row:
                return jsonify({"ok": False, "error": "Firma nicht gefunden."}), 404
            keys = row.keys()
            daten = _json(row["besichtigung_data_json"], {}) if "besichtigung_data_json" in keys else {}
            am = aufmass(daten)
            entwurf = cur.execute("SELECT felder_json, kalkulation_json, geaendert_am FROM angebot_editor_entwuerfe WHERE tagesliste_id = ?", (tid,)).fetchone()
            status = row["status"] if "status" in keys else "besichtigung"
            nr = (row["angebot_nr"] if "angebot_nr" in keys else "") or ""
            vars_ = _json(row["angebot_vars_json"], {}) if "angebot_vars_json" in keys else {}
            if entwurf and _json(entwurf["felder_json"], {}):
                felder, quelle = _sauber(_json(entwurf["felder_json"], {})), "entwurf"
            elif vars_:
                felder, quelle = _sauber(_felder_aus_vars(vars_, row)), "angebot"
            else:
                felder, quelle = _sauber(_leer_felder(row, daten, am)), "neu"
            conn.commit()
            return jsonify({
                "ok": True, "id": tid, "status": status, "aus_backup": aus_backup, "angebot_nr": nr,
                "angebot_datum": (row["angebot_datum"] if "angebot_datum" in keys else "") or "",
                "felder": felder, "felder_quelle": quelle,
                "kalkulation": _json(entwurf["kalkulation_json"], {}) if entwurf else {},
                "entwurf_am": entwurf["geaendert_am"] if entwurf else "",
                "aufmass": am, "koeffizienten": koeffizienten(app2_modul.ANGEBOT_STUNDENSATZ),
                "lead": {"firma": row["firma"] or "", "quelle": (row["quelle"] if "quelle" in keys else "") or "",
                         "telefon": row["telefon"] or "", "email": row["email"] or ""},
            })
        finally:
            conn.close()

    @app.route("/api/angebot-editor/aufmass", methods=["POST"])
    @login_required
    def angebot_editor_aufmass():
        """Aufmaß aus den noch nicht gespeicherten Eingaben der Besichtigung-Seite (rechnet nur, speichert nichts)."""
        d = request.get_json(silent=True) or {}
        daten = d.get("daten") if isinstance(d.get("daten"), dict) else {}
        am = aufmass(daten)
        leer = {k: "" for k in ("firma", "ansprechpartner", "strasse", "plz", "ort", "email", "telefon")}
        return jsonify({"ok": True, "aufmass": am, "felder": _sauber(_leer_felder(leer, daten, am))})

    @app.route("/api/angebot-editor/<int:tid>/entwurf", methods=["POST"])
    @login_required
    def angebot_editor_entwurf(tid):
        d = request.get_json(silent=True) or {}
        felder = _sauber(d.get("felder"))
        kalk = d.get("kalkulation") if isinstance(d.get("kalkulation"), dict) else {}
        conn = get_db_connection()
        try:
            _tabelle(conn)
            conn.execute("""INSERT INTO angebot_editor_entwuerfe (tagesliste_id, felder_json, kalkulation_json, geaendert_am) VALUES (?, ?, ?, ?)
                            ON CONFLICT(tagesliste_id) DO UPDATE SET felder_json = excluded.felder_json,
                            kalkulation_json = excluded.kalkulation_json, geaendert_am = excluded.geaendert_am""",
                         (tid, json.dumps(felder, ensure_ascii=False), json.dumps(kalk, ensure_ascii=False)[:20000], _jetzt()))
            conn.commit()
            return jsonify({"ok": True, "geaendert_am": _jetzt()})
        finally:
            conn.close()

    @app.route("/api/angebot-editor/<int:tid>/senden", methods=["POST"])
    @login_required
    def angebot_editor_senden(tid):
        d = request.get_json(silent=True) or {}
        f = _sauber(d.get("felder"))
        if not f["firma"]:
            return jsonify({"ok": False, "error": "Kunde (Firma) fehlt."}), 400
        if not any(p["text"] and _zahl(p["preis"]) for p in f["positionen"]):
            return jsonify({"ok": False, "error": "Mindestens eine Leistung mit Preis eintragen."}), 400
        conn = get_db_connection()
        try:
            _tabelle(conn)
            cur = conn.cursor()
            row = cur.execute("SELECT * FROM tagesliste_leads WHERE id = ?", (tid,)).fetchone()
            if not row:
                # wie /datenbank/angebot-senden: Besichtigung aus der Sicherung zurückholen
                b = cur.execute("""SELECT * FROM tagesliste_status_backup WHERE tagesliste_id = ? AND status = 'besichtigung'
                                   ORDER BY backup_am DESC LIMIT 1""", (tid,)).fetchone()
                if not b:
                    return jsonify({"ok": False, "error": "Firma nicht gefunden."}), 404
                cur.execute("""INSERT OR IGNORE INTO tagesliste_leads (id, source_lead_id, firma, branche, ansprechpartner, strasse, plz, ort,
                               telefon, email, website, quelle, status, notiz, spaeter_datum, company_key, erstellt_am)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'besichtigung', ?, NULL, ?, COALESCE(?, CURRENT_TIMESTAMP))""",
                            (int(b["tagesliste_id"] or tid), b["source_lead_id"], b["firma"], b["branche"], b["ansprechpartner"], b["strasse"],
                             b["plz"], b["ort"], b["telefon"], b["email"], b["website"], b["quelle"], b["notiz"],
                             f"{b['firma']}|{b['telefon']}|{b['email']}".lower(), b["erstellt_am"]))
                row = cur.execute("SELECT * FROM tagesliste_leads WHERE id = ?", (tid,)).fetchone()
                if not row:
                    return jsonify({"ok": False, "error": "Firma konnte nicht wiederhergestellt werden."}), 500
            nr = str(row["angebot_nr"] or "").strip() or app2_modul.angebot_next_number(cur)
            f["datum"] = f["datum"] or str(row["angebot_datum"] or "").strip() or _heute()
            v = vorlage_vars(f, nr)
            netto, mwst, brutto = summen(f)
            cur.execute("""UPDATE tagesliste_leads SET status = 'angebot', spaeter_datum = NULL, angebot_vars_json = ?, angebot_nr = ?,
                           angebot_datum = ?, angebot_netto = ?, angebot_mwst = ?, angebot_brutto = ?,
                           email = CASE WHEN ? != '' THEN ? ELSE email END WHERE id = ?""",
                        (json.dumps(v, ensure_ascii=False), nr, f["datum"], netto, mwst, brutto, f["email"], f["email"], tid))
            cur.execute("INSERT OR IGNORE INTO tagesliste_status_history (tagesliste_id, status, datum) VALUES (?, 'angebot', date('now','localtime'))", (tid,))
            cur.execute("""INSERT INTO angebot_editor_entwuerfe (tagesliste_id, felder_json, kalkulation_json, geaendert_am) VALUES (?, ?, ?, ?)
                           ON CONFLICT(tagesliste_id) DO UPDATE SET felder_json = excluded.felder_json, geaendert_am = excluded.geaendert_am""",
                        (tid, json.dumps(f, ensure_ascii=False), json.dumps(d.get("kalkulation") or {}, ensure_ascii=False)[:20000], _jetzt()))
            conn.commit()
            mail = {"modus": "angebot", "angebot_id": tid, "empfaenger": f["email"], "firma": f["firma"], "ansprechpartner": f["ansprechpartner"],
                    "adresse": f["adresse"], "ort": " ".join(x for x in (f["plz"], f["ort"]) if x), "telefon": f["telefon"],
                    "angebot_nr": f"AN-{nr}", "angebot_datum": f["datum"], "angebot_netto": _euro(netto), "leistungsart": f["leistungsart"]}
            return jsonify({"ok": True, "angebot_nr": nr, "angebot_datum": f["datum"], "netto": netto, "mwst": mwst, "brutto": brutto,
                            "mail_url": "/datenbank/mail-mini?" + urlencode(mail)})
        finally:
            conn.close()

    @app.route("/angebot/editor/vorschau")
    @login_required
    def angebot_editor_vorschau():
        """Live-Vorschau: echte Angebotsvorlage ohne Menü, ohne automatisches Drucken."""
        a = request.args
        f = _sauber({
            "firma": a.get("firma"), "ansprechpartner": a.get("ansprechpartner"), "objekt": a.get("objekt"), "adresse": a.get("adresse"),
            "plz": a.get("plz"), "ort": a.get("ort"), "leistungsart": a.get("leistungsart"), "ausfuehrungszeitraum": a.get("zeitraum"),
            "datum": a.get("datum"),
            "positionen": [{"text": a.get(f"l{n}"), "einheit": a.get(f"e{n}"), "preis": a.get(f"p{n}")} for n in range(1, 5)],
        })
        v = vorlage_vars(f, a.get("nr") or "…")
        seite_html = render_template("angebotvorlage.html", **v)
        if a.get("drucken") == "1":
            return seite_html  # „PDF ansehen“: wie /angebotvorlage?print=1 mit Druckdialog
        stopp = "<script>window.print = function () {};</script>"
        seite_html = seite_html.replace("<head>", "<head>" + stopp, 1) if "<head>" in seite_html else stopp + seite_html
        return seite_html
