# Angebot-Editor: Besichtigung → Angebot an einem Ort.
# Links die offenen Besichtigungen und verschickten Angebote, in der Mitte das Formular (Felder der Angebotsvorlage,
# Leistung 1–4, Richtpreis-Rechner), rechts die echte Angebotsvorlage als Live-Vorschau.
# „Senden“ vergibt die Angebots-Nr (gleiche Nummernfolge wie bisher), setzt den Status auf „angebot“ (Warteliste)
# und öffnet die vorhandene Mail (KG-Mail mit PDF + LV). besichtigung_data_json wird dabei NICHT verändert.
# Entwürfe liegen in der eigenen Tabelle angebot_editor_entwuerfe.
# /tablet/angebot zeigt dieselbe Seite ohne CRM-Menü (für das Tablet).
# Ohne Besichtigung: Formular startet leer („Neues Angebot“). Beim Tippen der Firma werden Kunden und Firmen aus der
# Datenbank vorgeschlagen (Daten werden übernommen); erst „Entwurf“/„Senden“ legt – wie „Direktes Angebot“ in der
# Datenbank – einen Eintrag in der Besichtigungsliste an.
import html
import io
import json
import re
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from flask import jsonify, render_template, request, send_file

import besichtigung_zeit as bz

BERLIN = ZoneInfo("Europe/Berlin")
EINHEITEN = ["monatlich", "einmalig", "pro Durchgang", "jährlich", "pro Stunde"]
MWST = 0.19
BEREICHE = [("buero", "Büro"), ("wc", "WC / Sanitär"), ("kueche", "Küche"), ("flur", "Flur / Eingang")]
# Kalkulation im Rechner (alles im Editor änderbar, wird mit dem Angebot gespeichert):
# Lohn = Mindestlohn Gebäudereinigung Lohngruppe 1 ab 01.01.2026; Abgaben = Minijob gewerblich 2026
# (KV 13 % + RV 15 % + Pauschsteuer 2 % + U1 0,8 % + U2 0,22 % + Insolvenzgeld 0,15 %); Material = Richtwert in % der Lohnkosten;
# Aufschlag auf die Kosten = 40 % vorausgewählt. Fahrt gibt es nur, wenn sie im Editor hinzugefügt wird (km + Cent/km).
KALKULATION = {"lohn": 15.0, "abgaben": 31.17, "material": 5.0, "aufschlag": 40, "stufen": [25, 40, 50, 60]}
STATUS_NAME = {"offen": "Tagesliste", "angerufen": "Angerufen", "interessiert": "Interessiert", "besichtigung": "Besichtigung",
               "angebot": "Angebot", "kontaktformular": "Kontaktformular", "spaeter": "Später", "verloren": "Verloren", "kunde": "Kunde"}


def _tabelle(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS angebot_editor_entwuerfe (
            tagesliste_id INTEGER PRIMARY KEY,
            felder_json TEXT,
            kalkulation_json TEXT,
            geaendert_am TEXT
        )
    """)
    # vergebene Angebot-Nummern (zuletzt vergebene = Grundlage für die nächste, siehe app2.angebot_next_number)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS angebot_nummern (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nr INTEGER NOT NULL,
            tagesliste_id INTEGER,
            vergeben_am TEXT
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


def _nr(text):
    """„AN-2267“, „2267“ → „2267“ (sonst leer)."""
    m = re.search(r"\d{1,8}", str(text or ""))
    return m.group(0).lstrip("0") or "0" if m else ""


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
        "kalkulation": KALKULATION,
        "haeufigkeiten": ["1x wöchentlich", "2x wöchentlich", "3x wöchentlich", "4x wöchentlich", "5x wöchentlich", "täglich",
                          "14-tägig", "1x monatlich", "keine"],
        "einsaetze": {h: bz.einsaetze_pro_monat(h) for h in ["1x wöchentlich", "2x wöchentlich", "3x wöchentlich", "4x wöchentlich",
                                                               "5x wöchentlich", "täglich", "14-tägig", "1x monatlich", "keine"]},
    }


# ---------------------------------------------------------------- Felder ⇄ Vorlagen-Variablen

def kalk_ergebnis(text):
    """Gespeichertes Kalkulationsergebnis (Kosten, Preis, Gewinn) für Listen und die Kundenakte."""
    e = _json(text, {}).get("ergebnis")
    if not isinstance(e, dict):
        return None
    aus = {}
    for k in ("stunden", "kosten", "richtpreis", "preis", "gewinn", "prozent", "aufschlag"):
        try:
            aus[k] = round(float(e.get(k) or 0), 2)
        except (TypeError, ValueError):
            aus[k] = 0.0
    return aus if aus["kosten"] > 0 else None


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
    aus["nr"] = _nr(f.get("nr"))
    aus["nr_manuell"] = bool(f.get("nr_manuell")) and bool(aus["nr"])
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

LV_KURZ = {"täglich": "tägl.", "14-tägig": "14-tägl.", "1x monatlich": "1x monatl."}
LV_FELDER = {"buero": ("Freq_Muell", "Freq_Tueren", "Freq_Boden_Teppich"),
             "kueche": ("Freq_Kueche_Flaechen", "Freq_Kuechengeraete", "Freq_Kueche_Muell", "Freq_Boden_Kueche"),
             "wc": ("Freq_WC", "Freq_Waschbecken", "Freq_Auffuellen", "Freq_Sanitaer_Muell", "Freq_Boden_Sanitaer"),
             "flur": ("Freq_Flur_Kontakt", "Freq_Flur_Sicht", "Freq_Flur_Boden")}
LV_NAME = {"buero": "Büro", "wc": "WC", "kueche": "Küche", "flur": "Flur"}


def _lv_haeufigkeit(h):
    h = _txt(h)
    if not h or h.lower() == "keine":
        return "-"
    return LV_KURZ.get(h, h.replace("wöchentlich", "wöch."))


def lv_mit_editor(v, felder, kalk):
    """Leistungsverzeichnis-Werte (aus der Besichtigung) mit dem überschreiben, was im Angebot-Editor steht:
    Firma, Adresse, Leistungsart und die Häufigkeiten aus dem Richtpreis-Rechner – damit LV und Angebot zusammenpassen."""
    v = dict(v)
    f = _sauber(felder)
    if f["firma"]:
        v["Kunde"] = f["firma"]
    if f["adresse"] or f["plz"] or f["ort"]:
        v["Adresse"], v["Plz"], v["Ort"] = f["adresse"], f["plz"], f["ort"]
    if f["objekt"]:
        v["Objekt"] = f["objekt"]
    if f["leistungsart"]:
        v["Leistungsart"] = f["leistungsart"]
    if f["nr"] and not _nr(v.get("Nr")):
        v["Nr"] = f["nr"]
    oben = []
    for b in (kalk or {}).get("bereiche") or []:
        if not isinstance(b, dict) or b.get("key") not in LV_FELDER:
            continue
        h = _lv_haeufigkeit(b.get("haeufigkeit"))
        for name in LV_FELDER[b["key"]]:
            v[name] = h
        if b["key"] == "buero":  # Schreibtische / Elektro: aus der Besichtigung, sonst wie das Büro
            for name in ("Freq_Schreibtische", "Freq_Elektro"):
                if _txt(v.get(name)) in ("", "-"):
                    v[name] = h
        if h != "-":
            oben.append(f"{LV_NAME[b['key']]}: {h}")
    if oben:
        v["Haeufigkeit"] = "<br>".join(oben)
    return v


def register_angebot_editor(app, login_required, get_db_connection, app2_modul):

    def lv_anpassen(tid, v):
        """Für /leistungsverzeichnis (auch PDF-Anhang der Mail): Werte aus dem gespeicherten Editor-Stand übernehmen."""
        try:
            conn = get_db_connection()
            try:
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'angebot_editor_entwuerfe'").fetchone():
                    return v
                e = conn.execute("SELECT felder_json, kalkulation_json FROM angebot_editor_entwuerfe WHERE tagesliste_id = ?", (tid,)).fetchone()
            finally:
                conn.close()
            if not e:
                return v
            return lv_mit_editor(v, _json(e["felder_json"], {}), _json(e["kalkulation_json"], {}))
        except Exception as exc:
            print("Angebot-Editor: LV-Werte nicht übernommen:", exc)
            return v

    app.extensions["kg_lv_anpassen"] = lv_anpassen

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
            kalk = {r[0]: kalk_ergebnis(r[1]) for r in conn.execute("SELECT tagesliste_id, kalkulation_json FROM angebot_editor_entwuerfe").fetchall()}
            entwuerfe = set(kalk)
            bes = []
            for r in conn.execute("""SELECT id, firma, ort, quelle, besichtigung_data_json FROM tagesliste_leads
                                     WHERE status = 'besichtigung' ORDER BY id DESC""").fetchall():
                d = _json(r["besichtigung_data_json"], {})
                bes.append({"id": r["id"], "firma": r["firma"] or "Ohne Firma", "ort": r["ort"] or "", "quelle": r["quelle"] or "",
                            "scan": isinstance(d.get("kg_scan"), dict), "raeume": len(d.get("raeume") or []), "entwurf": r["id"] in entwuerfe,
                            "kalk": kalk.get(r["id"])})
            live = {b["id"] for b in bes}
            try:
                for r in conn.execute("""SELECT tagesliste_id AS id, firma, ort, quelle FROM tagesliste_status_backup
                                         WHERE status = 'besichtigung' ORDER BY backup_am DESC""").fetchall():
                    if r["id"] in live or conn.execute("SELECT 1 FROM tagesliste_leads WHERE id = ? AND status = 'angebot'", (r["id"],)).fetchone():
                        continue
                    live.add(r["id"])
                    d = {}
                    akte = app.extensions.get("kg_akte")
                    if akte:  # Kundenakte: gesicherte Besichtigung (Scan, Räume) auch für schon gelöschte Tagesliste-Zeilen
                        try:
                            d = akte.besichtigung(conn, r["id"]) or {}
                        except Exception:
                            d = {}
                    bes.append({"id": r["id"], "firma": r["firma"] or "Ohne Firma", "ort": r["ort"] or "", "quelle": r["quelle"] or "",
                                "scan": isinstance(d.get("kg_scan"), dict) and bool(d.get("kg_scan")), "raeume": len(d.get("raeume") or []),
                                "entwurf": r["id"] in entwuerfe, "kalk": kalk.get(r["id"])})
            except Exception:
                pass
            ang = [{"id": r["id"], "firma": r["firma"] or "Ohne Firma", "ort": r["ort"] or "", "nr": r["angebot_nr"] or "",
                    "datum": r["angebot_datum"] or "", "netto": float(r["angebot_netto"] or 0), "kalk": kalk.get(r["id"])}
                   for r in conn.execute("""SELECT id, firma, ort, angebot_nr, angebot_datum, angebot_netto FROM tagesliste_leads
                                            WHERE status = 'angebot' ORDER BY id DESC LIMIT 200""").fetchall()]
            conn.commit()
            return jsonify({"ok": True, "besichtigungen": bes, "angebote": ang})
        finally:
            conn.close()

    @app.route("/api/angebot-editor/neu")
    @login_required
    def angebot_editor_neu():
        """Leeres Formular für ein Angebot ohne Besichtigung (nur lesen, legt nichts an)."""
        am = aufmass({})
        leer = {k: "" for k in ("firma", "ansprechpartner", "strasse", "plz", "ort", "email", "telefon")}
        felder = _sauber(_leer_felder(leer, {}, am))
        felder["positionen"] = [{"text": "", "einheit": "monatlich", "preis": ""} for _ in range(4)]
        conn = get_db_connection()
        try:
            _tabelle(conn)
            naechste = app2_modul.angebot_next_number(conn.cursor())
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "felder": felder, "aufmass": am, "koeffizienten": koeffizienten(app2_modul.ANGEBOT_STUNDENSATZ),
                        "naechste_nr": naechste})

    @app.route("/api/angebot-editor/suche")
    @login_required
    def angebot_editor_suche():
        """Vorschläge beim Tippen der Firma: Kunden und Firmen aus der Datenbank (nur lesen)."""
        q = _txt(request.args.get("q")).lower()[:80]
        if len(q) < 2:
            return jsonify({"ok": True, "treffer": []})
        conn = get_db_connection()
        try:
            treffer = []
            namen = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
            if "kunden" in namen:
                spalten = {r[1] for r in conn.execute("PRAGMA table_info(kunden)").fetchall()}
                mail = "COALESCE(NULLIF(rechnung_email, ''), email)" if "rechnung_email" in spalten else "email"
                for r in conn.execute(f"""SELECT id, firma, ansprechpartner_name, strasse, plz, ort, telefon, {mail} AS mail, vertragsstatus FROM kunden
                                         WHERE instr(lower(COALESCE(firma, '')), ?) > 0 OR instr(lower(COALESCE(ort, '')), ?) > 0
                                         ORDER BY instr(lower(COALESCE(firma, '')), ?) = 0, firma COLLATE NOCASE LIMIT 6""", (q, q, q)).fetchall():
                    treffer.append({"art": "kunde", "id": r["id"], "firma": r["firma"] or "", "ort": r["ort"] or "",
                                    "stand": "Kunde" + (" · gekündigt" if str(r["vertragsstatus"] or "").lower().startswith("gek") else ""),
                                    "felder": {"firma": _txt(r["firma"]), "ansprechpartner": _txt(r["ansprechpartner_name"]), "adresse": _txt(r["strasse"]),
                                               "plz": _txt(r["plz"]), "ort": _txt(r["ort"]), "email": _txt(r["mail"]), "telefon": _txt(r["telefon"])}})
            if "tagesliste_leads" in namen:
                for r in conn.execute("""SELECT t.id, t.firma, t.ansprechpartner, t.strasse, t.plz, t.ort, t.telefon, t.email, t.status, t.angebot_nr,
                                                (SELECT b.status FROM tagesliste_status_backup b WHERE b.tagesliste_id = t.id ORDER BY b.backup_am DESC LIMIT 1) AS liste
                                         FROM tagesliste_leads t
                                         WHERE instr(lower(COALESCE(t.firma, '')), ?) > 0 OR instr(lower(COALESCE(t.ort, '')), ?) > 0
                                         ORDER BY instr(lower(COALESCE(t.firma, '')), ?) = 0, t.id DESC LIMIT 8""", (q, q, q)).fetchall():
                    st = str(r["status"] or "offen").lower()
                    art = st if st in ("besichtigung", "angebot") else "lead"
                    stand = STATUS_NAME.get(st if st != "offen" else str(r["liste"] or "offen").lower(), "Datenbank")
                    if st == "angebot" and r["angebot_nr"]:
                        stand += " AN-" + str(r["angebot_nr"])
                    treffer.append({"art": art, "id": r["id"], "firma": r["firma"] or "", "ort": r["ort"] or "", "stand": "Datenbank · " + stand,
                                    "felder": {"firma": _txt(r["firma"]), "ansprechpartner": _txt(r["ansprechpartner"]), "adresse": _txt(r["strasse"]),
                                               "plz": _txt(r["plz"]), "ort": _txt(r["ort"]), "email": _txt(r["email"]), "telefon": _txt(r["telefon"])}})
            return jsonify({"ok": True, "treffer": treffer})
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
            akte = app.extensions.get("kg_akte")
            if akte:  # Kundenakte: gesicherte Besichtigung, auch wenn die Tagesliste-Zeile leer oder schon gelöscht ist
                try:
                    daten = akte.besichtigung(conn, tid, row["besichtigung_data_json"] if "besichtigung_data_json" in keys else None) or daten
                except Exception as exc:
                    print("Angebot-Editor: Kundenakte nicht gelesen:", exc)
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
            naechste = "" if nr else app2_modul.angebot_next_number(cur)
            conn.commit()
            return jsonify({
                "ok": True, "id": tid, "status": status, "aus_backup": aus_backup, "angebot_nr": nr, "naechste_nr": naechste,
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
            # Nummer: von Hand eingetragen → genau diese (wenn frei); sonst die schon vergebene; sonst die nächste freie
            alt = _nr(row["angebot_nr"])
            if f["nr_manuell"] and f["nr"] != alt:
                belegt = None
                for r in cur.execute("SELECT id, firma, angebot_nr FROM tagesliste_leads WHERE id != ? AND COALESCE(angebot_nr, '') != ''", (tid,)).fetchall():
                    if _nr(r["angebot_nr"]) == f["nr"]:
                        belegt = r
                        break
                if belegt:
                    return jsonify({"ok": False, "error": f"AN-{f['nr']} ist schon vergeben ({belegt['firma'] or 'ohne Firma'})."}), 409
                nr = f["nr"]
            else:
                nr = alt or app2_modul.angebot_next_number(cur)
            if nr != alt:
                cur.execute("INSERT INTO angebot_nummern (nr, tagesliste_id, vergeben_am) VALUES (?, ?, ?)", (int(nr), tid, _jetzt()))
            f["datum"] = f["datum"] or str(row["angebot_datum"] or "").strip() or _heute()
            v = vorlage_vars(f, nr)
            netto, mwst, brutto = summen(f)
            cur.execute("""UPDATE tagesliste_leads SET status = 'angebot', spaeter_datum = NULL, angebot_vars_json = ?, angebot_nr = ?,
                           angebot_datum = ?, angebot_netto = ?, angebot_mwst = ?, angebot_brutto = ?,
                           email = CASE WHEN ? != '' THEN ? ELSE email END WHERE id = ?""",
                        (json.dumps(v, ensure_ascii=False), nr, f["datum"], netto, mwst, brutto, f["email"], f["email"], tid))
            cur.execute("INSERT OR IGNORE INTO tagesliste_status_history (tagesliste_id, status, datum) VALUES (?, 'angebot', date('now','localtime'))", (tid,))
            cur.execute("""INSERT INTO angebot_editor_entwuerfe (tagesliste_id, felder_json, kalkulation_json, geaendert_am) VALUES (?, ?, ?, ?)
                           ON CONFLICT(tagesliste_id) DO UPDATE SET felder_json = excluded.felder_json,
                           kalkulation_json = excluded.kalkulation_json, geaendert_am = excluded.geaendert_am""",
                        (tid, json.dumps(f, ensure_ascii=False), json.dumps(d.get("kalkulation") or {}, ensure_ascii=False)[:20000], _jetzt()))
            conn.commit()
            mail = {"modus": "angebot", "angebot_id": tid, "empfaenger": f["email"], "firma": f["firma"], "ansprechpartner": f["ansprechpartner"],
                    "adresse": f["adresse"], "ort": " ".join(x for x in (f["plz"], f["ort"]) if x), "telefon": f["telefon"],
                    "angebot_nr": f"AN-{nr}", "angebot_datum": f["datum"], "angebot_netto": _euro(netto), "leistungsart": f["leistungsart"]}
            return jsonify({"ok": True, "angebot_nr": nr, "angebot_datum": f["datum"], "netto": netto, "mwst": mwst, "brutto": brutto,
                            "mail_url": "/datenbank/mail-mini?" + urlencode(mail)})
        finally:
            conn.close()

    @app.route("/angebot/editor/lv-vorschau")
    @login_required
    def angebot_editor_lv_vorschau():
        """Leistungsverzeichnis (bestehende Seite /leistungsverzeichnis) für die Vorschau – ohne Druckdialog."""
        ansicht = app.view_functions.get("app2_leistungsverzeichnis")
        if not ansicht:
            return "Leistungsverzeichnis nicht verfügbar.", 404
        seite_html = ansicht()
        if not isinstance(seite_html, str):
            return seite_html
        stopp = "<script>window.print = function () {};</script>"
        return seite_html.replace("<head>", "<head>" + stopp, 1) if "<head>" in seite_html else stopp + seite_html

    @app.route("/angebot/editor/pdf")
    @login_required
    def angebot_editor_pdf():
        """PDF zum Herunterladen: Angebot (wie die Vorschau) oder Leistungsverzeichnis (gespeicherter Eintrag)."""
        a = request.args
        nr = _nr(a.get("nr")) or "Entwurf"
        firma = re.sub(r"[^A-Za-z0-9ÄÖÜäöüß]+", "_", _txt(a.get("firma")))[:40].strip("_")
        basis = request.host_url.rstrip("/")
        if a.get("art") == "lv":
            tid = a.get("id", type=int) or 0
            if tid <= 0:
                return jsonify({"ok": False, "error": "Leistungsverzeichnis gibt es erst nach dem Speichern."}), 400
            url, name = f"{basis}/angebot/editor/lv-vorschau?angebot_id={tid}&print=1", f"Leistungsverzeichnis_AN-{nr}.pdf"
        else:
            werte = [(k, v) for k, v in a.items(multi=True) if k not in ("art", "drucken", "id")]
            url, name = f"{basis}/angebot/editor/vorschau?" + urlencode(werte), f"Angebot_AN-{nr}" + (f"_{firma}" if firma else "") + ".pdf"
        try:
            from playwright.sync_api import sync_playwright
            keks_name = app.config.get("SESSION_COOKIE_NAME", "session")
            keks = request.cookies.get(keks_name, "")
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
                try:
                    ctx = browser.new_context(viewport={"width": 1240, "height": 1754}, device_scale_factor=1)
                    if keks:
                        ctx.add_cookies([{"name": keks_name, "value": keks, "url": basis}])
                    seite = ctx.new_page()
                    seite.goto(url, wait_until="networkidle", timeout=25000)
                    seite.emulate_media(media="print")
                    pdf = seite.pdf(format="A4", print_background=True, prefer_css_page_size=True, scale=1,
                                    margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"})
                finally:
                    browser.close()
        except Exception as exc:
            return jsonify({"ok": False, "error": "PDF konnte nicht erstellt werden: " + str(exc)}), 500
        return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=True, download_name=name)

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
