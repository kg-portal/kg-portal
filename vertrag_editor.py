# Vertrag-Editor: bestätigtes Angebot → Reinigungsvertrag an einem Ort (wie der Angebot-Editor).
# Oben die Liste: wartende Verträge (Angebot bestätigt), verschickte Angebote (mit „Bestätigt“ in die Warteliste) und
# verschickte Verträge. In der Mitte das Formular, rechts die Vertragsvorlage (templates/vertrag_vorlage.html, 4 Seiten)
# als Live-Vorschau. „Per E-Mail senden“ öffnet das Mail-Fenster (templates/vertrag_mail.html, wie beim Angebot) mit
# Vertrag-PDF und – wenn es ein Angebot gibt – dem Leistungsverzeichnis als Anlage 1.
# Alles liegt in der eigenen Tabelle vertrag_editor; Angebote, Kunden und die Tagesliste werden NICHT verändert.
import io
import json
import re
from datetime import date, datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from flask import current_app, jsonify, render_template, request, send_file

import angebot_editor as ae

BERLIN = ZoneInfo("Europe/Berlin")
EINHEITEN = ae.EINHEITEN
MWST = ae.MWST
TAGE = [("Mo", "Montag", "montags"), ("Di", "Dienstag", "dienstags"), ("Mi", "Mittwoch", "mittwochs"),
        ("Do", "Donnerstag", "donnerstags"), ("Fr", "Freitag", "freitags"), ("Sa", "Samstag", "samstags"), ("So", "Sonntag", "sonntags")]
FELDER = ("firma", "ansprechpartner", "adresse", "plz", "ort", "email", "telefon", "startdatum", "wochentage", "zeiten", "datum", "zeitplan", "probezeit",
          "schluessel", "schluessel_datum")
PROBEZEITEN = ("1 Monat", "2 Monate", "3 Monate")
BUERO_ORT = "Duisburg"  # Verträge werden im KG-Büro Duisburg erstellt

_txt, _zahl, _euro, _json = ae._txt, ae._zahl, ae._euro, ae._json


def _tabelle(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS vertrag_editor (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tagesliste_id INTEGER,
            status TEXT NOT NULL DEFAULT 'wartend',
            felder_json TEXT,
            erstellt_am TEXT,
            geaendert_am TEXT,
            gesendet_am TEXT,
            gesendet_an TEXT
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS vertrag_editor_angebot ON vertrag_editor (tagesliste_id) WHERE tagesliste_id IS NOT NULL")


def _jetzt():
    return datetime.now(BERLIN).isoformat(timespec="seconds")


def _heute():
    return datetime.now(BERLIN).strftime("%d.%m.%Y")


def _naechster_monat():
    h = datetime.now(BERLIN).date()
    return date(h.year + h.month // 12, h.month % 12 + 1, 1).strftime("%d.%m.%Y")


def _betrag(v):
    """1234.5 → „1.234,50“ (für die Eingabefelder, ohne €)."""
    return f"{float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _aufzaehlung(teile, und="und"):
    teile = [t for t in teile if t]
    if len(teile) <= 1:
        return "".join(teile)
    return ", ".join(teile[:-1]) + f" {und} " + teile[-1]


def tage_aus_besichtigung(daten):
    """Besichtigung „Einsatztage & Uhrzeiten“ (Mo ab 17:00 …) → Wochentage-Text und Zeiten-Text für § 3."""
    eintraege = []
    for e in (daten or {}).get("einsatzzeiten") or []:
        if not isinstance(e, dict):
            continue
        kurz = _txt(e.get("tag"))[:2].capitalize()
        tag = next((t for t in TAGE if t[0] == kurz), None)
        if tag and tag not in [x[0] for x in eintraege]:
            eintraege.append((tag, _txt(e.get("uhrzeit"))))
    eintraege.sort(key=lambda x: TAGE.index(x[0]))
    if not eintraege:
        return "", ""
    wochentage = _aufzaehlung([t[1] for t, _ in eintraege])
    zeiten = {u for _, u in eintraege}
    if len(zeiten) == 1:
        u = next(iter(zeiten))
        zeit = _aufzaehlung([t[2] for t, _ in eintraege]) + (f" ab {u} Uhr" if u else "")
    else:
        zeit = ", ".join(t[2] + (f" ab {u} Uhr" if u else "") for t, u in eintraege)
    return wochentage, zeit


def _sauber(f):
    f = f if isinstance(f, dict) else {}
    pos = []
    for p in (f.get("positionen") or [])[:4]:
        p = p if isinstance(p, dict) else {}
        pos.append({"text": _txt(p.get("text"))[:300], "einheit": _txt(p.get("einheit"))[:40] or "monatlich",
                    "preis": _txt(p.get("preis"))[:20], "netto": _txt(p.get("netto"))[:20]})
    while len(pos) < 4:
        pos.append({"text": "", "einheit": "monatlich", "preis": "", "netto": ""})
    aus = {k: _txt(f.get(k))[:300] for k in FELDER}
    aus["positionen"] = pos
    return aus


def _leer():
    f = _sauber({})
    f["startdatum"], f["datum"], f["probezeit"] = _naechster_monat(), _heute(), PROBEZEITEN[0]
    return f


def felder_aus_angebot(conn, tid):
    """Felder aus dem Angebot (Editor-Stand, sonst die Vorlagen-Variablen) – Preise netto → inkl. MwSt."""
    row = conn.execute("SELECT * FROM tagesliste_leads WHERE id = ?", (tid,)).fetchone()
    if not row:
        return None
    keys = row.keys()
    af = None
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'angebot_editor_entwuerfe'").fetchone():
        e = conn.execute("SELECT felder_json FROM angebot_editor_entwuerfe WHERE tagesliste_id = ?", (tid,)).fetchone()
        if e and _json(e["felder_json"], {}):
            af = ae._sauber(_json(e["felder_json"], {}))
    if af is None:
        vars_ = _json(row["angebot_vars_json"], {}) if "angebot_vars_json" in keys else {}
        af = ae._sauber(ae._felder_aus_vars(vars_, row) if vars_ else {
            "firma": row["firma"], "ansprechpartner": row["ansprechpartner"], "adresse": row["strasse"], "plz": row["plz"],
            "ort": row["ort"], "email": row["email"], "telefon": row["telefon"]})
    daten = _json(row["besichtigung_data_json"], {}) if "besichtigung_data_json" in keys else {}
    try:  # Kundenakte: gesicherte Besichtigung (Wochentage/Zeiten), auch wenn die Zeile leer ist
        akte = current_app.extensions.get("kg_akte")
        if akte:
            daten = akte.besichtigung(conn, tid, row["besichtigung_data_json"] if "besichtigung_data_json" in keys else None) or daten
    except Exception as exc:
        print("Vertrag-Editor: Kundenakte nicht gelesen:", exc)
    wochentage, zeiten = tage_aus_besichtigung(daten)
    f = _leer()
    for k in ("firma", "ansprechpartner", "adresse", "plz", "ort", "email", "telefon"):
        spalte = "strasse" if k == "adresse" else k
        f[k] = af.get(k) or (_txt(row[spalte]) if spalte in keys else "")
    f["wochentage"], f["zeiten"] = wochentage, zeiten
    for i, p in enumerate(af["positionen"][:4]):
        netto = _zahl(p["preis"])
        f["positionen"][i] = {"text": p["text"], "einheit": p["einheit"] or "monatlich",
                              "preis": _betrag(round(netto * (1 + MWST), 2)) if netto else "", "netto": _betrag(netto) if netto else ""}
    return f


def _netto_mwst(p):
    """Brutto-Preis einer Leistung → (netto, MwSt.); der gespeicherte Netto-Wert aus dem Angebot zählt, wenn er zum Brutto passt."""
    brutto = _zahl(p["preis"])
    netto = _zahl(p.get("netto"))
    if not netto or abs(round(netto * (1 + MWST), 2) - brutto) > 0.01:
        netto = round(brutto / (1 + MWST), 2)
    return netto, round(brutto - netto, 2)


def _hat_text(t):
    """„-“, „–“ oder „.“ allein zählt als leer (Platzhalter-Striche in leeren Leistungszeilen)."""
    return bool(re.sub(r"[\s\-–—.]+", "", _txt(t)))


def vorlage_vars(f):
    """Formular → Platzhalter der Vertragsvorlage ({{Objekt}}, {{Kunde}}, … wie in Vertrag_Vorlage)."""
    pos = [p for p in f["positionen"] if _hat_text(p["text"]) or _zahl(p["preis"])]
    return {
        "Objekt": f["firma"], "Kunde": f["ansprechpartner"], "Adresse": f["adresse"], "Plz": f["plz"], "Ort": f["ort"],
        "Datum": f["datum"] or _heute(), "Startdatum": f["startdatum"], "Wochentagen": f["wochentage"], "Zeiten": f["zeiten"],
        "Probezeit": f["probezeit"] if f["probezeit"] in PROBEZEITEN else PROBEZEITEN[0],
        "BueroOrt": BUERO_ORT,  # Ort der Erstellung (oben rechts) und Unterschrift Auftragnehmer; Auftraggeber unterschreibt mit seinem Ort
        "leistungen_satz": [p["text"] for p in f["positionen"][:3] if _hat_text(p["text"])],
        "preise": [{"Leistung": p["text"], "Einheiten": p["einheit"], "Preis": _euro(_zahl(p["preis"])) if _zahl(p["preis"]) else "",
                    "Netto": _euro(_netto_mwst(p)[0]) if _zahl(p["preis"]) else "", "MwSt": _euro(_netto_mwst(p)[1]) if _zahl(p["preis"]) else ""}
                   for p in pos],
    }


def register_vertrag_editor(app, login_required, get_db_connection):

    def eintrag(conn, vid):
        return conn.execute("SELECT * FROM vertrag_editor WHERE id = ?", (vid,)).fetchone()

    def aus_zeile(conn, r):
        f = _sauber(_json(r["felder_json"], {}))
        nr = ""
        if r["tagesliste_id"]:
            a = conn.execute("SELECT angebot_nr FROM tagesliste_leads WHERE id = ?", (r["tagesliste_id"],)).fetchone()
            nr = ae._nr(a["angebot_nr"]) if a else ""
        return {"id": r["id"], "tagesliste_id": r["tagesliste_id"] or 0, "status": r["status"], "angebot_nr": nr, "felder": f,
                "erstellt_am": r["erstellt_am"] or "", "geaendert_am": r["geaendert_am"] or "", "gesendet_am": r["gesendet_am"] or "",
                "gesendet_an": r["gesendet_an"] or ""}

    @app.route("/vertrag/editor")
    @login_required
    def vertrag_editor_seite():
        return render_template("vertrag_editor.html", start_id=request.args.get("id", type=int) or 0,
                               angebot_id=request.args.get("angebot", type=int) or 0, einheiten=EINHEITEN)

    @app.route("/api/vertrag-editor/liste")
    @login_required
    def vertrag_editor_liste():
        conn = get_db_connection()
        try:
            _tabelle(conn)
            wartend, gesendet, belegt = [], [], set()
            for r in conn.execute("SELECT * FROM vertrag_editor ORDER BY COALESCE(gesendet_am, geaendert_am, erstellt_am) DESC").fetchall():
                z = aus_zeile(conn, r)
                f = z["felder"]
                netto = sum(_zahl(p["preis"]) for p in f["positionen"] if p["einheit"] == "monatlich")
                e = {"id": z["id"], "firma": f["firma"] or "Ohne Firma", "ort": f["ort"], "nr": z["angebot_nr"], "start": f["startdatum"],
                     "monat": netto, "datum": (z["gesendet_am"] or z["geaendert_am"] or z["erstellt_am"])[:10],
                     "entwurf": bool(z["geaendert_am"] and z["geaendert_am"] != z["erstellt_am"])}
                if r["tagesliste_id"]:
                    belegt.add(r["tagesliste_id"])
                (gesendet if r["status"] == "gesendet" else wartend).append(e)
            angebote = [{"id": r["id"], "firma": r["firma"] or "Ohne Firma", "ort": r["ort"] or "", "nr": ae._nr(r["angebot_nr"]),
                         "datum": r["angebot_datum"] or "", "netto": float(r["angebot_netto"] or 0)}
                        for r in conn.execute("""SELECT id, firma, ort, angebot_nr, angebot_datum, angebot_netto FROM tagesliste_leads
                                                 WHERE status = 'angebot' ORDER BY id DESC LIMIT 200""").fetchall()
                        if r["id"] not in belegt]
            conn.commit()
            return jsonify({"ok": True, "wartend": wartend, "angebote": angebote, "gesendet": gesendet})
        finally:
            conn.close()

    @app.route("/api/vertrag-editor/neu")
    @login_required
    def vertrag_editor_neu():
        """Leeres Formular (nur lesen, legt nichts an)."""
        return jsonify({"ok": True, "felder": _leer()})

    @app.route("/api/vertrag-editor/aus-angebot/<int:tid>")
    @login_required
    def vertrag_editor_aus_angebot(tid):
        """Felder aus einem Angebot (nur lesen) – z. B. wenn beim Tippen der Firma ein Angebot gewählt wird."""
        conn = get_db_connection()
        try:
            f = felder_aus_angebot(conn, tid)
            if not f:
                return jsonify({"ok": False, "error": "Angebot nicht gefunden."}), 404
            return jsonify({"ok": True, "felder": f})
        finally:
            conn.close()

    @app.route("/api/vertrag-editor/bestaetigen", methods=["POST"])
    @login_required
    def vertrag_editor_bestaetigen():
        """Angebot bestätigt → wartet auf den Vertrag (nur in vertrag_editor; das Angebot selbst bleibt unverändert)."""
        tid = int((request.get_json(silent=True) or {}).get("tagesliste_id") or 0)
        conn = get_db_connection()
        try:
            _tabelle(conn)
            schon = conn.execute("SELECT id FROM vertrag_editor WHERE tagesliste_id = ?", (tid,)).fetchone()
            if schon:
                return jsonify({"ok": True, "id": schon["id"], "schon": True})
            f = felder_aus_angebot(conn, tid)
            if not f:
                return jsonify({"ok": False, "error": "Angebot nicht gefunden."}), 404
            jetzt = _jetzt()
            cur = conn.execute("INSERT INTO vertrag_editor (tagesliste_id, status, felder_json, erstellt_am, geaendert_am) VALUES (?, 'wartend', ?, ?, ?)",
                               (tid, json.dumps(f, ensure_ascii=False), jetzt, jetzt))
            conn.commit()
            return jsonify({"ok": True, "id": cur.lastrowid})
        finally:
            conn.close()

    @app.route("/api/vertrag-editor/<int:vid>")
    @login_required
    def vertrag_editor_daten(vid):
        conn = get_db_connection()
        try:
            _tabelle(conn)
            r = eintrag(conn, vid)
            if not r:
                return jsonify({"ok": False, "error": "Vertrag nicht gefunden."}), 404
            return jsonify(dict(aus_zeile(conn, r), ok=True))
        finally:
            conn.close()

    @app.route("/api/vertrag-editor/speichern", methods=["POST"])
    @login_required
    def vertrag_editor_speichern():
        """Neu anlegen (ohne id) oder Stand speichern (mit id)."""
        d = request.get_json(silent=True) or {}
        f = _sauber(d.get("felder"))
        vid = int(d.get("id") or 0)
        conn = get_db_connection()
        try:
            _tabelle(conn)
            jetzt = _jetzt()
            if vid:
                if not eintrag(conn, vid):
                    return jsonify({"ok": False, "error": "Vertrag nicht gefunden."}), 404
                conn.execute("UPDATE vertrag_editor SET felder_json = ?, geaendert_am = ? WHERE id = ?", (json.dumps(f, ensure_ascii=False), jetzt, vid))
            else:
                if not f["firma"]:
                    return jsonify({"ok": False, "error": "Bitte die Firma eintragen."}), 400
                tid = int(d.get("tagesliste_id") or 0) or None
                if tid:
                    schon = conn.execute("SELECT id FROM vertrag_editor WHERE tagesliste_id = ?", (tid,)).fetchone()
                    if schon:
                        vid = schon["id"]
                        conn.execute("UPDATE vertrag_editor SET felder_json = ?, geaendert_am = ? WHERE id = ?", (json.dumps(f, ensure_ascii=False), jetzt, vid))
                if not vid:
                    vid = conn.execute("INSERT INTO vertrag_editor (tagesliste_id, status, felder_json, erstellt_am, geaendert_am) VALUES (?, 'wartend', ?, ?, ?)",
                                       (tid, json.dumps(f, ensure_ascii=False), jetzt, jetzt)).lastrowid
            conn.commit()
            return jsonify({"ok": True, "id": vid, "geaendert_am": jetzt})
        finally:
            conn.close()

    @app.route("/api/vertrag-editor/<int:vid>/loeschen", methods=["POST"])
    @login_required
    def vertrag_editor_loeschen(vid):
        """Nur den Vertrag-Eintrag entfernen (das Angebot erscheint wieder unter „Angebote“)."""
        conn = get_db_connection()
        try:
            _tabelle(conn)
            conn.execute("DELETE FROM vertrag_editor WHERE id = ?", (vid,))
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/vertrag-editor/<int:vid>/gesendet", methods=["POST"])
    @login_required
    def vertrag_editor_gesendet(vid):
        """Vom Mail-Fenster nach erfolgreichem Versand: Vertrag gilt als verschickt."""
        an = _txt((request.get_json(silent=True) or {}).get("an"))[:200]
        conn = get_db_connection()
        try:
            _tabelle(conn)
            conn.execute("UPDATE vertrag_editor SET status = 'gesendet', gesendet_am = ?, gesendet_an = ? WHERE id = ?", (_jetzt(), an, vid))
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    def _felder_aus_url(a):
        f = {k: a.get(k) for k in FELDER}
        f["positionen"] = [{"text": a.get(f"l{n}"), "einheit": a.get(f"e{n}"), "preis": a.get(f"p{n}")} for n in range(1, 5)]
        return _sauber(f)

    def _gespeichert(vid):
        conn = get_db_connection()
        try:
            _tabelle(conn)
            r = eintrag(conn, vid)
            return aus_zeile(conn, r) if r else None
        finally:
            conn.close()

    @app.route("/vertrag/editor/vorschau")
    @login_required
    def vertrag_editor_vorschau():
        """Vertragsvorlage: mit ?id= der gespeicherte Stand, sonst die Werte aus der Adresse (Live-Vorschau)."""
        vid = request.args.get("id", type=int) or 0
        if vid:
            z = _gespeichert(vid)
            if not z:
                return "Vertrag nicht gefunden.", 404
            f = z["felder"]
        else:
            f = _felder_aus_url(request.args)
        return render_template("vertrag_vorlage.html", **vorlage_vars(f), drucken=request.args.get("drucken") == "1")

    def _pdf(urls):
        from playwright.sync_api import sync_playwright
        keks_name = app.config.get("SESSION_COOKIE_NAME", "session")
        keks = request.cookies.get(keks_name, "")
        basis = request.host_url.rstrip("/")
        aus = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                ctx = browser.new_context(viewport={"width": 1240, "height": 1754}, device_scale_factor=1)
                if keks:
                    ctx.add_cookies([{"name": keks_name, "value": keks, "url": basis}])
                seite = ctx.new_page()
                for url in urls:
                    seite.goto(basis + url, wait_until="networkidle", timeout=25000)
                    seite.emulate_media(media="print")
                    aus.append(seite.pdf(format="A4", print_background=True, prefer_css_page_size=True, scale=1,
                                         margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"}))
            finally:
                browser.close()
        return aus

    def _dateiname(firma):
        return re.sub(r"[^A-Za-z0-9ÄÖÜäöüß]+", "_", _txt(firma))[:40].strip("_")

    @app.route("/vertrag/editor/pdf")
    @login_required
    def vertrag_editor_pdf():
        """Vertrag als PDF herunterladen (wie die Vorschau)."""
        a = request.args
        vid = a.get("id", type=int) or 0
        if vid and not a.get("firma"):
            url, firma = f"/vertrag/editor/vorschau?id={vid}", (_gespeichert(vid) or {"felder": {"firma": ""}})["felder"]["firma"]
        else:
            werte = [(k, v) for k, v in a.items(multi=True) if k not in ("id", "drucken", "ansehen")]
            url, firma = "/vertrag/editor/vorschau?" + urlencode(werte), a.get("firma")
        try:
            pdf = _pdf([url])[0]
        except Exception as exc:
            return jsonify({"ok": False, "error": "PDF konnte nicht erstellt werden: " + str(exc)}), 500
        name = "Reinigungsvertrag" + (f"_{_dateiname(firma)}" if _dateiname(firma) else "") + ".pdf"
        # ansehen=1: im Browser öffnen (lesen) statt herunterladen
        return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=a.get("ansehen") != "1", download_name=name)

    @app.route("/api/vertrag-editor/<int:vid>/anhaenge")
    @login_required
    def vertrag_editor_anhaenge(vid):
        """Für das Mail-Fenster: Vertrag-PDF (gespeicherter Stand) + Leistungsverzeichnis des Angebots als Anlage 1."""
        import base64
        z = _gespeichert(vid)
        if not z:
            return jsonify({"ok": False, "message": "Vertrag nicht gefunden."}), 404
        name = _dateiname(z["felder"]["firma"])
        jobs = [(f"/vertrag/editor/vorschau?id={vid}", "Reinigungsvertrag" + (f"_{name}" if name else "") + ".pdf")]
        if z["tagesliste_id"] and "app2_leistungsverzeichnis" in app.view_functions:
            nr = z["angebot_nr"]
            jobs.append((f"/angebot/editor/lv-vorschau?angebot_id={z['tagesliste_id']}&print=1",
                         f"Anlage_1_Leistungsverzeichnis{'_AN-' + nr if nr else ''}.pdf"))
        try:
            pdfs = _pdf([u for u, _ in jobs])
        except Exception as exc:
            return jsonify({"ok": False, "message": "PDF-Anhänge konnten nicht erstellt werden: " + str(exc)}), 500
        return jsonify({"ok": True, "attachments": [{"filename": n, "mimeType": "application/pdf", "data": base64.b64encode(b).decode("ascii")}
                                                    for (_, n), b in zip(jobs, pdfs)]})

    @app.route("/vertrag/editor/mail")
    @login_required
    def vertrag_editor_mail():
        """Mail-Fenster für den Vertrag (gleiches Fenster wie beim Angebot, eigene Vorlage)."""
        z = _gespeichert(request.args.get("id", type=int) or 0)
        if not z:
            return "Vertrag nicht gefunden.", 404
        f = z["felder"]
        return render_template("vertrag_mail.html", vertrag_id=z["id"], empfaenger=f["email"], firma=f["firma"],
                               ansprechpartner=f["ansprechpartner"], adresse=f["adresse"], ort=" ".join(x for x in (f["plz"], f["ort"]) if x),
                               startdatum=f["startdatum"], hat_lv=bool(z["tagesliste_id"]),
                               schluessel_datum=f["schluessel_datum"] if f["schluessel"] == "ja" else "")
