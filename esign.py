# Digitale Unterschrift (einfache elektronische Signatur) für Angebot und Reinigungsvertrag.
# Ablauf:
#   Mail-Fenster (Angebot / Vertrag): Kästchen „Button Digital unterschreiben“ → beim Senden POST /api/esign/anlegen.
#     Das PDF wird genau wie der Mail-Anhang erstellt und gespeichert (data/esign/<id>/original.pdf + SHA-256),
#     dazu Seitenbilder für Handy/Tablet. Zurück kommt der persönliche Link /unterschrift/<token> (30 Tage gültig).
#     Ein neuer Link für dasselbe Angebot/denselben Vertrag macht den alten ungültig.
#   Kunde (ohne Login): /unterschrift/<token> → Dokument ansehen, Name, Unterschrift mit Finger/Maus, Zustimmung.
#     Unterschriebenes PDF = Original + Unterschrift auf der Linie des Auftraggebers + Seite „Unterschriftsprotokoll“
#     (Zeitpunkt, IP, Gerät, Prüfwert). Danach Mail mit PDF an den Kunden und an info@,
#     beim Angebot zusätzlich „Bestätigt → Vertrag“ (Vertrag-Warteliste, wie der Knopf im Angebot-Editor).
#   CRM: /unterschriften (Liste mit Status und PDFs).
# Im Token-Feld der Datenbank steht nur der SHA-256 des Tokens; der Link selbst wird nirgends gespeichert.
import base64
import hashlib
import html
import io
import json
import os
import re
import secrets
import threading
from datetime import datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from flask import jsonify, render_template, request, send_file

BERLIN = ZoneInfo("Europe/Berlin")
GUELTIG_TAGE = 30
KG_MAIL = "info@kg-reinigung.de"
MAX_PNG = 400_000
PX_MM = 25.4 / 96  # CSS-Pixel → mm

# Quelle je Art: Seite (wie der Mail-Anhang), Seitenelement, Unterschriftslinie des Auftraggebers
QUELLE = {
    "angebot": {"seiten": ".page", "ziel": ".page3 .signature-line"},
    "vertrag": {"seiten": "main.page", "ziel": "main.s4 .strich"},
}

_MESS_JS = """(q) => {
  const seiten = Array.from(document.querySelectorAll(q.seiten)), ziel = document.querySelector(q.ziel);
  if (!ziel) return { seiten: seiten.length, ziel: null };
  const s = seiten.findIndex(p => p.contains(ziel)), p = seiten[s].getBoundingClientRect(), r = ziel.getBoundingClientRect();
  return { seiten: seiten.length, ziel: { seite: s, x: r.left - p.left, y: r.top - p.top, w: r.width, h: r.height } };
}"""


def _jetzt():
    return datetime.now(BERLIN)


def _txt(v, n=200):
    s = str(v if v is not None else "").strip()
    return ("" if s.lower() == "none" else s)[:n]


def _hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _sha(daten):
    return hashlib.sha256(daten).hexdigest()


def _datum(iso, mit_zeit=True):
    try:
        d = datetime.fromisoformat(iso)
        return d.strftime("%d.%m.%Y, %H:%M Uhr") if mit_zeit else d.strftime("%d.%m.%Y")
    except Exception:
        return ""


def _tabelle(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS esign_vorgang (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            art TEXT NOT NULL,
            ref_id INTEGER NOT NULL,
            tagesliste_id INTEGER,
            titel TEXT,
            firma TEXT,
            email TEXT,
            token_hash TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'offen',
            erstellt_am TEXT,
            gueltig_bis TEXT,
            geoeffnet_am TEXT,
            seiten INTEGER,
            ziel_json TEXT,
            original_sha256 TEXT,
            name TEXT,
            funktion TEXT,
            unterschrieben_am TEXT,
            ip TEXT,
            geraet TEXT,
            signiert_sha256 TEXT,
            mail_kunde TEXT,
            mail_kg TEXT,
            angenommen TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS esign_vorgang_ref ON esign_vorgang (art, ref_id)")


def _abgelaufen(gueltig_bis):
    try:
        return datetime.fromisoformat(gueltig_bis) < _jetzt()
    except Exception:
        return False


def _status(v):
    """offen / geoeffnet / unterschrieben / ersetzt / abgelaufen (abgelaufen wird nur berechnet)."""
    if v["status"] == "offen" and v["gueltig_bis"] and _abgelaufen(v["gueltig_bis"]):
        return "abgelaufen"
    if v["status"] == "offen" and v["geoeffnet_am"]:
        return "geoeffnet"
    return v["status"]


def _vorgangs_nr(v):
    return f"KG-ES-{(v['erstellt_am'] or '0000')[:4]}-{int(v['id']):04d}"


def register_esign(app, login_required, get_db_connection):
    ordner = os.path.join(app.root_path, "data", "esign")

    def pfad(vid, name):
        return os.path.join(ordner, str(int(vid)), name)

    def basis_url():
        """Adresse für den Kunden-Link: wie das CRM aufgerufen wird, außerhalb von localhost immer https."""
        b = request.host_url.rstrip("/")
        host = request.host.split(":")[0]
        if b.startswith("http://") and host not in ("127.0.0.1", "localhost"):
            b = "https://" + b[len("http://"):]
        return b

    def vorgang(conn, token):
        if not token or len(token) > 100:
            return None
        return conn.execute("SELECT * FROM esign_vorgang WHERE token_hash = ?", (_hash(token),)).fetchone()

    # ------------------------------------------------------------------ CRM: Link anlegen (beim Senden der Mail)

    def quelle(conn, art, ref_id):
        """→ (url, titel, firma, tagesliste_id) für das Dokument, wie es als Mail-Anhang verschickt wird."""
        if art == "angebot":
            r = conn.execute("SELECT id, firma, angebot_nr, COALESCE(angebot_vars_json, '') AS v FROM tagesliste_leads WHERE id = ?", (ref_id,)).fetchone()
            if not r or not r["v"]:
                return None
            nr = re.sub(r"(?i)^AN-", "", _txt(r["angebot_nr"]))
            return f"/angebotvorlage?angebot_id={ref_id}&print=1", (f"Angebot AN-{nr}" if nr else "Angebot"), _txt(r["firma"]), ref_id
        r = conn.execute("SELECT id, tagesliste_id, felder_json FROM vertrag_editor WHERE id = ?", (ref_id,)).fetchone() \
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'vertrag_editor'").fetchone() else None
        if not r:
            return None
        try:
            firma = _txt(json.loads(r["felder_json"] or "{}").get("firma"))
        except Exception:
            firma = ""
        return f"/vertrag/editor/vorschau?id={ref_id}", "Reinigungsvertrag", firma, r["tagesliste_id"]

    def dokument_erstellen(url, art, vid):
        """PDF (wie der Mail-Anhang), Lage der Unterschriftslinie und Seitenbilder (JPEG) für die Kunden-Seite."""
        from playwright.sync_api import sync_playwright
        keks_name = app.config.get("SESSION_COOKIE_NAME", "session")
        keks = request.cookies.get(keks_name, "")
        basis = request.host_url.rstrip("/")
        q = QUELLE[art]
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                ctx = browser.new_context(viewport={"width": 1240, "height": 1754}, device_scale_factor=1)
                if keks:
                    ctx.add_cookies([{"name": keks_name, "value": keks, "url": basis}])
                seite = ctx.new_page()
                seite.goto(basis + url, wait_until="networkidle", timeout=25000)
                seite.emulate_media(media="print")
                pdf = seite.pdf(format="A4", print_background=True, prefer_css_page_size=True, scale=1,
                                margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"})
                mass = seite.evaluate(_MESS_JS, q)
                # Seitenbilder (schärfer, für Handy/Tablet)
                bild = browser.new_context(viewport={"width": 1240, "height": 1754}, device_scale_factor=1.6)
                if keks:
                    bild.add_cookies([{"name": keks_name, "value": keks, "url": basis}])
                bs = bild.new_page()
                bs.goto(basis + url, wait_until="networkidle", timeout=25000)
                bs.emulate_media(media="print")
                for i, el in enumerate(bs.query_selector_all(q["seiten"])):
                    el.screenshot(path=pfad(vid, f"seite{i + 1}.jpg"), type="jpeg", quality=80)
            finally:
                browser.close()
        return pdf, mass

    @app.route("/api/esign/anlegen", methods=["POST"])
    @login_required
    def esign_anlegen():
        d = request.get_json(silent=True) or {}
        art = _txt(d.get("art"))
        ref_id = int(d.get("id") or 0)
        email = _txt(d.get("email"), 200)
        if art not in QUELLE or ref_id <= 0:
            return jsonify({"ok": False, "error": "Angebot oder Vertrag fehlt."}), 400
        conn = get_db_connection()
        try:
            _tabelle(conn)
            q = quelle(conn, art, ref_id)
            if not q:
                return jsonify({"ok": False, "error": ("Angebot" if art == "angebot" else "Vertrag") + " nicht gefunden – bitte zuerst speichern."}), 404
            url, titel, firma, tid = q
            token = secrets.token_urlsafe(32)
            jetzt = _jetzt()
            vid = conn.execute("""INSERT INTO esign_vorgang (art, ref_id, tagesliste_id, titel, firma, email, token_hash, status, erstellt_am, gueltig_bis)
                                  VALUES (?, ?, ?, ?, ?, ?, ?, 'erstellen', ?, ?)""",
                               (art, ref_id, tid, titel, firma, email, _hash(token), jetzt.isoformat(timespec="seconds"),
                                (jetzt + timedelta(days=GUELTIG_TAGE)).isoformat(timespec="seconds"))).lastrowid
            conn.commit()
        finally:
            conn.close()
        try:
            os.makedirs(pfad(vid, ""), exist_ok=True)
            pdf, mass = dokument_erstellen(url, art, vid)
            if not mass.get("ziel"):
                raise RuntimeError("Unterschriftslinie im Dokument nicht gefunden.")
            with open(pfad(vid, "original.pdf"), "wb") as fh:
                fh.write(pdf)
        except Exception as exc:
            conn = get_db_connection()
            try:
                conn.execute("DELETE FROM esign_vorgang WHERE id = ? AND status = 'erstellen'", (vid,))
                conn.commit()
            finally:
                conn.close()
            return jsonify({"ok": False, "error": "Unterschrift-Link nicht erstellt: " + str(exc)}), 500
        conn = get_db_connection()
        try:
            # ältere offene Links für dasselbe Dokument gelten nicht mehr
            conn.execute("UPDATE esign_vorgang SET status = 'ersetzt' WHERE art = ? AND ref_id = ? AND status = 'offen' AND id != ?", (art, ref_id, vid))
            conn.execute("UPDATE esign_vorgang SET status = 'offen', seiten = ?, ziel_json = ?, original_sha256 = ? WHERE id = ?",
                         (mass["seiten"], json.dumps(mass["ziel"]), _sha(pdf), vid))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "url": f"{basis_url()}/unterschrift/{token}", "gueltig_tage": GUELTIG_TAGE})

    # ------------------------------------------------------------------ Kunde (öffentlich, nur mit Token)

    def _antwort(resp):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.route("/unterschrift/<token>")
    def esign_kunde(token):
        conn = get_db_connection()
        try:
            _tabelle(conn)
            v = vorgang(conn, token)
            if v and v["status"] == "offen" and not v["geoeffnet_am"]:
                conn.execute("UPDATE esign_vorgang SET geoeffnet_am = ? WHERE id = ?", (_jetzt().isoformat(timespec="seconds"), v["id"]))
                conn.commit()
        finally:
            conn.close()
        if not v or v["status"] == "erstellen":
            return _antwort(app.make_response((render_template("esign_kunde.html", zustand="ungueltig", v=None), 404)))
        st = _status(v)
        zustand = {"offen": "offen", "geoeffnet": "offen", "unterschrieben": "fertig"}.get(st, "ungueltig")
        return _antwort(app.make_response(render_template(
            "esign_kunde.html", zustand=zustand, v=v, token=token, seiten=int(v["seiten"] or 0),
            unterschrieben=_datum(v["unterschrieben_am"]), gueltig_bis=_datum(v["gueltig_bis"], False), zustimmung=zustimmung_text(v))))

    @app.route("/unterschrift/<token>/seite/<int:n>.jpg")
    def esign_seite(token, n):
        conn = get_db_connection()
        try:
            _tabelle(conn)
            v = vorgang(conn, token)
        finally:
            conn.close()
        datei = pfad(v["id"], f"seite{n}.jpg") if v and v["status"] in ("offen", "unterschrieben") else ""
        if not datei or not os.path.exists(datei):
            return _antwort(app.make_response(("Nicht gefunden.", 404)))
        return _antwort(send_file(os.path.abspath(datei), mimetype="image/jpeg"))

    @app.route("/unterschrift/<token>/pdf")
    def esign_kunde_pdf(token):
        conn = get_db_connection()
        try:
            _tabelle(conn)
            v = vorgang(conn, token)
        finally:
            conn.close()
        if not v or v["status"] not in ("offen", "unterschrieben"):
            return _antwort(app.make_response(("Nicht gefunden.", 404)))
        fertig = v["status"] == "unterschrieben"
        datei = pfad(v["id"], "unterschrieben.pdf" if fertig else "original.pdf")
        if not os.path.exists(datei):
            return _antwort(app.make_response(("Nicht gefunden.", 404)))
        return _antwort(send_file(os.path.abspath(datei), mimetype="application/pdf", as_attachment=request.args.get("laden") == "1",
                                  download_name=dateiname(v, fertig)))

    @app.route("/unterschrift/<token>", methods=["POST"])
    def esign_unterschreiben(token):
        if (request.content_length or 0) > 1_500_000:
            return jsonify({"ok": False, "error": "Unterschrift zu groß – bitte noch einmal unterschreiben."}), 413
        d = request.get_json(silent=True) or {}
        name = re.sub(r"\s+", " ", _txt(d.get("name"), 120))
        funktion = re.sub(r"\s+", " ", _txt(d.get("funktion"), 80))
        bild = str(d.get("unterschrift") or "")
        if len(name) < 3:
            return jsonify({"ok": False, "error": "Bitte Vor- und Nachnamen eintragen."}), 400
        if not d.get("zustimmung"):
            return jsonify({"ok": False, "error": "Bitte die Bestätigung ankreuzen."}), 400
        if not bild.startswith("data:image/png;base64,"):
            return jsonify({"ok": False, "error": "Bitte im Feld unterschreiben."}), 400
        try:
            png = base64.b64decode(bild.split(",", 1)[1], validate=True)
        except Exception:
            png = b""
        if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > MAX_PNG or len(png) < 200:
            return jsonify({"ok": False, "error": "Unterschrift nicht lesbar – bitte noch einmal unterschreiben."}), 400
        conn = get_db_connection()
        try:
            _tabelle(conn)
            v = vorgang(conn, token)
            if not v or _status(v) not in ("offen", "geoeffnet"):
                return jsonify({"ok": False, "error": "Dieser Link ist nicht mehr gültig."}), 410
            # gegen doppeltes Absenden: nur ein Aufruf darf von „offen“ auf „wird unterschrieben“ wechseln
            cur = conn.execute("UPDATE esign_vorgang SET status = 'unterschreiben' WHERE id = ? AND status = 'offen'", (v["id"],))
            conn.commit()
            if cur.rowcount != 1:
                return jsonify({"ok": False, "error": "Wird bereits unterschrieben – bitte kurz warten und die Seite neu laden."}), 409
        finally:
            conn.close()
        ip = _txt((request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0], 60)
        geraet = _txt(request.headers.get("User-Agent"), 300)
        zeit = _jetzt().isoformat(timespec="seconds")
        try:
            original = open(pfad(v["id"], "original.pdf"), "rb").read()
            if _sha(original) != v["original_sha256"]:
                raise RuntimeError("Dokument wurde verändert.")
            with open(pfad(v["id"], "unterschrift.png"), "wb") as fh:
                fh.write(png)
            daten = dict(v)
            daten.update({"name": name, "funktion": funktion, "unterschrieben_am": zeit, "ip": ip, "geraet": geraet})
            signiert = signiertes_pdf(daten, original, png)
            with open(pfad(v["id"], "unterschrieben.pdf"), "wb") as fh:
                fh.write(signiert)
        except Exception as exc:
            conn = get_db_connection()
            try:
                conn.execute("UPDATE esign_vorgang SET status = 'offen' WHERE id = ? AND status = 'unterschreiben'", (v["id"],))
                conn.commit()
            finally:
                conn.close()
            print("E-Unterschrift: Fehler beim Unterschreiben", v["id"], exc)
            return jsonify({"ok": False, "error": "Leider ist ein Fehler aufgetreten. Bitte versuchen Sie es noch einmal."}), 500
        angenommen = ""
        conn = get_db_connection()
        try:
            conn.execute("""UPDATE esign_vorgang SET status = 'unterschrieben', name = ?, funktion = ?, unterschrieben_am = ?, ip = ?, geraet = ?,
                            signiert_sha256 = ? WHERE id = ?""", (name, funktion, zeit, ip, geraet, _sha(signiert), v["id"]))
            conn.commit()
            if v["art"] == "angebot":
                # wie der Knopf „✓ Bestätigt → Vertrag“: Angebot kommt in die Vertrag-Warteliste
                try:
                    import vertrag_editor
                    r = vertrag_editor.angebot_bestaetigen(conn, v["ref_id"])
                    angenommen = f"Vertrag-Warteliste #{r['id']}" + (" (stand schon dort)" if r and r.get("schon") else "") if r else "Angebot nicht gefunden"
                except Exception as exc:
                    angenommen = "Fehler: " + str(exc)[:200]
                conn.execute("UPDATE esign_vorgang SET angenommen = ? WHERE id = ?", (angenommen, v["id"]))
                conn.commit()
        finally:
            conn.close()
        daten = dict(v)
        daten.update({"name": name, "funktion": funktion, "unterschrieben_am": zeit})
        threading.Thread(target=mails_senden, args=(daten, signiert, basis_url()), daemon=True).start()
        return jsonify({"ok": True, "pdf": f"/unterschrift/{token}/pdf?laden=1", "zeit": _datum(zeit)})

    # ------------------------------------------------------------------ PDF mit Unterschrift + Protokoll

    def dateiname(v, fertig):
        firma = re.sub(r"[^A-Za-z0-9ÄÖÜäöüß]+", "_", _txt(v["firma"]))[:40].strip("_")
        teile = [_txt(v["titel"]).replace(" ", "_") or "Dokument"] + ([firma] if firma else []) + (["unterschrieben"] if fertig else [])
        return "_".join(teile) + ".pdf"

    def _bild_daten(name):
        try:
            with open(os.path.join(app.root_path, "static", name), "rb") as fh:
                return "data:image/png;base64," + base64.b64encode(fh.read()).decode("ascii")
        except OSError:
            return ""

    def overlay_html(v, png):
        """Eine A4-Seite, durchsichtig: Unterschrift über der Linie des Auftraggebers + kleiner Vermerk."""
        z = json.loads(v["ziel_json"])
        mm = lambda px: round(px * PX_MM, 2)
        x, y, w, h = mm(z["x"]), mm(z["y"]), mm(z["w"]), mm(z["h"])
        sig = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        wer = html.escape(v["name"] + (f" ({v['funktion']})" if v["funktion"] else ""))
        vermerk = f"Elektronisch unterschrieben von {wer} am {html.escape(_datum(v['unterschrieben_am']))}"
        if v["art"] == "vertrag":
            # Linie = Unterkante des X-Strichs; Unterschrift darüber, Vermerk unter „Ort, den Datum“ links
            linie = y + h
            teile = (f'<img src="{sig}" style="position:absolute;left:{x + 6}mm;top:{linie - 17}mm;width:{w - 8}mm;height:16mm;object-fit:contain;object-position:left bottom">'
                     f'<div style="position:absolute;left:31.3mm;top:{linie + 1.6}mm;width:90mm;font-size:6.6pt;line-height:1.3;color:#1e3a8a;white-space:normal">{vermerk}</div>')
        else:
            # Angebot: Linie liegt 88 px unter dem Anfang der Unterschriftsfläche (Vorlage: .signature-line::after top:88px)
            linie = y + mm(88)
            teile = (f'<img src="{sig}" style="position:absolute;left:{x + 4.5}mm;top:{linie - 17}mm;width:{w - 6}mm;height:15.5mm;object-fit:contain;object-position:left bottom">'
                     f'<div style="position:absolute;left:{x}mm;top:{y - 1.5}mm;width:{w}mm;text-align:right;font-size:6.4pt;line-height:1.25;color:#1e3a8a;white-space:normal">{vermerk}</div>')
        return ("<!doctype html><html><head><meta charset='utf-8'><style>@page{size:A4;margin:0}html,body{margin:0;background:transparent}"
                "body{width:210mm;height:297mm;position:relative;font-family:Arial,Helvetica,sans-serif}</style></head><body>" + teile + "</body></html>")

    def protokoll_html(v, png):
        sig = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        return render_template("esign_protokoll.html", v=v, sig=sig, logo=_bild_daten("kg_logo_farbe.png"), nr=_vorgangs_nr(v),
                               versendet=_datum(v["erstellt_am"]), geoeffnet=_datum(v["geoeffnet_am"]) if v["geoeffnet_am"] else "",
                               unterschrieben=_datum(v["unterschrieben_am"]), gueltig_bis=_datum(v["gueltig_bis"], False),
                               zustimmung=zustimmung_text(v))

    def signiertes_pdf(v, original, png):
        from playwright.sync_api import sync_playwright
        from pypdf import PdfReader, PdfWriter
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                seite = browser.new_page()
                seite.set_content(overlay_html(v, png), wait_until="load")
                ueber = seite.pdf(format="A4", print_background=False, prefer_css_page_size=True, margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"})
                seite.set_content(protokoll_html(v, png), wait_until="load")
                prot = seite.pdf(format="A4", print_background=True, prefer_css_page_size=True, margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"})
            finally:
                browser.close()
        ziel = json.loads(v["ziel_json"])["seite"]
        schreiber = PdfWriter()
        for i, blatt in enumerate(PdfReader(io.BytesIO(original)).pages):
            if i == ziel:
                blatt.merge_page(PdfReader(io.BytesIO(ueber)).pages[0])
            schreiber.add_page(blatt)
        for blatt in PdfReader(io.BytesIO(prot)).pages:
            schreiber.add_page(blatt)
        schreiber.add_metadata({"/Title": f"{_txt(v['titel'])} – {_txt(v['firma'])} (elektronisch unterschrieben)", "/Author": "KG-Gebäudereinigung"})
        aus = io.BytesIO()
        schreiber.write(aus)
        return aus.getvalue()

    def zustimmung_text(v):
        was = "das Angebot" if v["art"] == "angebot" else "den Reinigungsvertrag"
        firma = _txt(v["firma"]) or "die oben genannte Firma"
        return (f"Ich habe {was} gelesen und erteile den Auftrag verbindlich. Ich bin berechtigt, für {firma} zu unterschreiben."
                if v["art"] == "angebot" else
                f"Ich habe {was} gelesen und schließe ihn verbindlich ab. Ich bin berechtigt, für {firma} zu unterschreiben.")

    # ------------------------------------------------------------------ Mails nach der Unterschrift

    def _gmail(an, betreff, text, html_text, pdf, name):
        import app2
        msg = EmailMessage()
        msg["From"] = f"KG-Gebäudereinigung <{KG_MAIL}>"
        msg["To"] = an
        msg["Subject"] = betreff
        msg.set_content(text)
        msg.add_alternative(html_text, subtype="html")
        msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=name)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        return app2.get_gmail_service().users().messages().send(userId="me", body={"raw": raw}).execute().get("id", "")

    def mails_senden(v, pdf, basis):
        titel, firma = _txt(v["titel"]), _txt(v["firma"])
        name = dateiname(v, True)
        zeit = _datum(v["unterschrieben_am"])
        ergebnis = {}
        if v["email"]:
            was = "Ihr unterschriebenes Angebot" if v["art"] == "angebot" else "Ihr unterschriebener Reinigungsvertrag"
            text = (f"Guten Tag {v['name']},\n\nvielen Dank für Ihre Unterschrift. Anbei erhalten Sie {('das unterschriebene Angebot' if v['art'] == 'angebot' else 'den unterschriebenen Reinigungsvertrag')} "
                    f"({titel}) als PDF für Ihre Unterlagen.\n\nBei Fragen sind wir jederzeit gerne für Sie da.\n\nMit freundlichen Grüßen\nKG-Gebäudereinigung\n"
                    "Fliederstr. 59 · 47055 Duisburg\n0203 / 47 96 68 22 · info@kg-reinigung.de")
            h = ("<p>Guten Tag " + html.escape(v["name"]) + ",</p><p>vielen Dank für Ihre Unterschrift. Anbei erhalten Sie "
                 + ("das unterschriebene Angebot" if v["art"] == "angebot" else "den unterschriebenen Reinigungsvertrag")
                 + " (" + html.escape(titel) + ") als PDF für Ihre Unterlagen.</p><p>Bei Fragen sind wir jederzeit gerne für Sie da.</p>"
                 "<p>Mit freundlichen Grüßen<br>KG-Gebäudereinigung<br>Fliederstr. 59 · 47055 Duisburg<br>0203 / 47 96 68 22 · info@kg-reinigung.de</p>")
            try:
                _gmail(v["email"], f"{was} – KG-Gebäudereinigung", text, h, pdf, name)
                ergebnis["mail_kunde"] = "gesendet " + _jetzt().strftime("%d.%m.%Y %H:%M")
            except Exception as exc:
                ergebnis["mail_kunde"] = "Fehler: " + str(exc)[:200]
        try:
            text = (f"{titel} wurde digital unterschrieben.\n\nFirma: {firma}\nUnterschrieben von: {v['name']}"
                    + (f" ({v['funktion']})" if v["funktion"] else "") + f"\nZeitpunkt: {zeit}\n"
                    + (f"Status: in die Vertrag-Warteliste übernommen.\n" if v["art"] == "angebot" else "")
                    + f"\nAlle Unterschriften: {basis}/unterschriften")
            h = "<p>" + html.escape(text).replace("\n", "<br>") + "</p>"
            _gmail(KG_MAIL, f"✓ Digital unterschrieben: {firma} – {titel}", text, h, pdf, name)
            ergebnis["mail_kg"] = "gesendet " + _jetzt().strftime("%d.%m.%Y %H:%M")
        except Exception as exc:
            ergebnis["mail_kg"] = "Fehler: " + str(exc)[:200]
        try:
            conn = get_db_connection()
            try:
                conn.execute("UPDATE esign_vorgang SET mail_kunde = ?, mail_kg = ? WHERE id = ?",
                             (ergebnis.get("mail_kunde", "keine E-Mail-Adresse"), ergebnis.get("mail_kg", ""), v["id"]))
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            print("E-Unterschrift: Mail-Status nicht gespeichert", exc)

    # ------------------------------------------------------------------ CRM: Übersicht

    @app.route("/unterschriften")
    @login_required
    def esign_liste():
        conn = get_db_connection()
        try:
            _tabelle(conn)
            zeilen = conn.execute("SELECT * FROM esign_vorgang WHERE status != 'erstellen' ORDER BY id DESC LIMIT 300").fetchall()
        finally:
            conn.close()
        liste = [dict(dict(v), zustand=_status(v), nr=_vorgangs_nr(v), erstellt=_datum(v["erstellt_am"]), geoeffnet=_datum(v["geoeffnet_am"]) if v["geoeffnet_am"] else "",
                      unterschrieben=_datum(v["unterschrieben_am"]) if v["unterschrieben_am"] else "", gueltig=_datum(v["gueltig_bis"], False)) for v in zeilen]
        return render_template("esign_liste.html", liste=liste)

    @app.route("/unterschriften/<int:vid>/pdf")
    @login_required
    def esign_crm_pdf(vid):
        conn = get_db_connection()
        try:
            _tabelle(conn)
            v = conn.execute("SELECT * FROM esign_vorgang WHERE id = ?", (vid,)).fetchone()
        finally:
            conn.close()
        fertig = request.args.get("art") != "original"
        datei = pfad(vid, "unterschrieben.pdf" if fertig else "original.pdf") if v else ""
        if not datei or not os.path.exists(datei):
            return "PDF nicht gefunden.", 404
        return send_file(os.path.abspath(datei), mimetype="application/pdf", as_attachment=False, download_name=dateiname(v, fertig))

    @app.route("/api/esign/status")
    @login_required
    def esign_status():
        """Für Angebot/Vertrag/Kundenakte: letzter Unterschrift-Vorgang je Dokument."""
        art, ref_id = _txt(request.args.get("art")), request.args.get("id", type=int) or 0
        conn = get_db_connection()
        try:
            _tabelle(conn)
            v = conn.execute("SELECT * FROM esign_vorgang WHERE art = ? AND ref_id = ? AND status != 'erstellen' ORDER BY id DESC LIMIT 1", (art, ref_id)).fetchone()
        finally:
            conn.close()
        if not v:
            return jsonify({"ok": True, "vorgang": None})
        return jsonify({"ok": True, "vorgang": {"id": v["id"], "status": _status(v), "name": v["name"] or "", "unterschrieben": _datum(v["unterschrieben_am"]) if v["unterschrieben_am"] else "",
                                                "pdf": f"/unterschriften/{v['id']}/pdf" if v["status"] == "unterschrieben" else ""}})
