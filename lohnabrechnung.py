# =====================================================
# LOHNABRECHNUNGEN VERSENDEN
# Sammel-PDF (z. B. aus Lexware/DATEV) hochladen → Seiten werden anhand der
# Namen den Mitarbeitern zugeordnet → je Mitarbeiter ein eigenes PDF (ohne Passwort,
# wie gonder.py) → Vorschau prüfen → erst nach Klick per KG-Mail senden.
# Wie Murats Programme ayirici.py / gonder.py: Name je Seite aus „Abrechnung für … - Name“
# oder „Auszahlung an: Name“; Seiten ohne Namen werden NICHT angehängt (nur „bitte prüfen“);
# Betreff „Lohnabrechnung Oktober 2026“, Text und Signatur wie in gonder.py (ohne Passwort).
#
# Dateien liegen unter data/lohn/<Monat>/ und können gelöscht werden.
# =====================================================
import base64
import io
import json
import os
import re
import secrets
import shutil
import unicodedata
from datetime import datetime
from email.message import EmailMessage

from flask import jsonify, render_template, request, send_file

BASIS = os.path.join("data", "lohn")
MONATE = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember")


def _fold(text):
    # türkisches ı (ohne Punkt) und ß haben keine Zerlegung – sonst fiele „Fındık“ zu „fndk“
    text = str(text or "").replace("ı", "i").replace("ß", "ss")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower())


def _monat_ok(monat):
    return bool(re.fullmatch(r"20\d\d-(0[1-9]|1[0-2])", str(monat or "")))


def _passwort_aus_geburtsdatum(value):
    s = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%d%m%Y")
        except ValueError:
            continue
    return ""


def ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lohn_versand (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monat TEXT NOT NULL,
            worker_id INTEGER NOT NULL,
            seiten TEXT,
            datei TEXT,
            email TEXT,
            passwort_quelle TEXT,
            status TEXT NOT NULL DEFAULT 'bereit',
            fehler TEXT,
            erstellt_am TEXT,
            gesendet_am TEXT,
            UNIQUE (monat, worker_id)
        )
    """)
    if "korrektur" not in {r[1] for r in conn.execute("PRAGMA table_info(lohn_versand)")}:
        conn.execute("ALTER TABLE lohn_versand ADD COLUMN korrektur TEXT")  # z. B. „August 2026, September 2026“
    conn.commit()


def monat_name(monat):
    """„2026-10“ → „Oktober 2026“."""
    jahr, mon = str(monat).split("-")
    return f"{MONATE[int(mon) - 1]} {jahr}"


def name_aus_seite(text):
    """Wie ayirici.py: Name aus der Zeile „Abrechnung für … - Name“ (auch „Korrekturabrechnung für … - Name“),
    sonst „Auszahlung an: Name“ (sonst leer)."""
    zeilen = str(text or "").split("\n")
    for z in zeilen:
        if "abrechnung für" in z.lower() and "-" in z:
            name = z.split("-", 1)[1].strip()
            if name:
                return name
    for z in zeilen:
        if "Auszahlung an:" in z:
            name = z.replace("Auszahlung an:", "").strip()
            if name:
                return name
    return ""


def korrektur_monat(text):
    """„Korrekturabrechnung für August 2026 - Name“ → „August 2026“ (sonst leer)."""
    m = re.search(r"Korrekturabrechnung für\s+(\S+\s+\d{4})", str(text or ""), re.I)
    return m.group(1) if m else ""


def _woerter(text):
    return set(_fold(text).split())


def _passende(woerter, mitarbeiter):
    """Mitarbeiter, deren Vor- und Nachname (alle Wörter) in woerter stehen; bei mehreren der mit den meisten Wörtern,
    bei gleichem Namen zuerst die aktiven (z. B. alter, ausgetretener Eintrag mit demselben Namen)."""
    treffer = []
    for m in mitarbeiter:
        eigene = _woerter(m["vorname"]) | _woerter(m["nachname"])
        if _woerter(m["nachname"]) and eigene <= woerter:
            treffer.append((len(eigene), m.get("status") == "aktiv", m["id"]))
    if not treffer:
        return []
    beste = max(t[0] for t in treffer)
    treffer = [t for t in treffer if t[0] == beste]
    if len(treffer) > 1 and any(t[1] for t in treffer):
        treffer = [t for t in treffer if t[1]]
    return [t[2] for t in treffer]


def seiten_zuordnen(texte, mitarbeiter):
    """Jede Seite für sich einem Mitarbeiter zuordnen (wie ayirici.py): erst über den Namen aus „Abrechnung für … - Name“ /
    „Auszahlung an: Name“, sonst über Vor- und Nachname im Seitentext. Seiten ohne eindeutigen Namen (z. B. Lohnjournal,
    Zusammenfassung) werden niemandem angehängt und als „unklar“ gemeldet."""
    zuordnung, unklar = {}, []
    for nr, text in enumerate(texte):
        name = name_aus_seite(text)
        treffer = _passende(_woerter(name), mitarbeiter) if name else []
        if len(treffer) != 1:
            treffer = _passende(_woerter(text), mitarbeiter)
        if len(treffer) == 1:
            zuordnung.setdefault(treffer[0], []).append(nr)
        else:
            unklar.append(nr + 1)
    return zuordnung, unklar


def mail_inhalt(monat_text, passwort_hinweis, korrektur=""):
    """Text und HTML wie in gonder.py (Signatur Damla Kicci), dazu der Hinweis zum Passwort der PDF und ggf. zu den
    enthaltenen Korrekturabrechnungen früherer Monate."""
    k_text = f"Zusätzlich enthalten: Korrekturabrechnung für {korrektur}.\n\n" if korrektur else ""
    k_html = f"Zusätzlich enthalten: Korrekturabrechnung für <b>{korrektur}</b>.<br><br>\n    " if korrektur else ""
    p_text = f"{passwort_hinweis}\n\n" if passwort_hinweis else ""
    p_html = f"{passwort_hinweis}<br><br>\n    " if passwort_hinweis else ""
    text = (f"Guten Tag,\n\nhiermit erhalten Sie Ihre Lohnabrechnung für den Monat {monat_text}.\n\n{k_text}{p_text}"
            "Bei Fragen stehe ich Ihnen gern zur Verfügung.\n\nVielen Dank.\n\nMit freundlichen Grüßen\n\n--\n\n"
            "Damla Kicci\nInhaberin\nKG-Gebäudereinigung\nFliederstr. 59\n47055 Duisburg - Wanheimerort\n\n"
            "0203 / 47 96 68 22\n0163 / 194 70 55\ninfo@kg-reinigung.de\nwww.kg-reinigung.de\n")
    html = f"""<html>
  <body style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#000;">
    Guten Tag,<br><br>
    hiermit erhalten Sie Ihre Lohnabrechnung für den Monat <b>{monat_text}</b>.<br><br>
    {k_html}{p_html}
    Bei Fragen stehe ich Ihnen gern zur Verfügung.<br><br>
    Vielen Dank.<br><br>
    Mit freundlichen Grüßen<br><br>
    --<br><br>
    <b>Damla Kicci</b><br>
    Inhaberin<br>
    KG-Gebäudereinigung<br>
    Fliederstr. 59<br>
    47055 Duisburg - Wanheimerort<br><br>
    0203 / 47 96 68 22<br>
    0163 / 194 70 55<br>
    <a href="mailto:info@kg-reinigung.de">info@kg-reinigung.de</a><br>
    <a href="https://www.kg-reinigung.de">www.kg-reinigung.de</a>
  </body>
</html>"""
    return text, html


def _pdf_lib():
    """pypdf erst bei Bedarf laden – fehlt es, startet das CRM trotzdem."""
    try:
        from pypdf import PdfReader, PdfWriter
        import cryptography  # noqa: F401  (AES-256 braucht es)
    except ImportError:
        raise RuntimeError("Für Lohnabrechnungen fehlen Pakete: im CRM-Ordner einmal "
                           "„python -m pip install pypdf cryptography“ ausführen.")
    return PdfReader, PdfWriter


def register_lohnabrechnung(app, login_required, get_db_connection):

    def _conn():
        conn = get_db_connection()
        ensure_tables(conn)
        return conn

    def _ordner(monat):
        return os.path.join(BASIS, monat)

    @app.route("/mitarbeiter/lohnabrechnung")
    @login_required
    def lohn_seite():
        return render_template("lohnabrechnung.html")

    @app.route("/api/lohn/liste")
    @login_required
    def lohn_liste():
        monat = request.args.get("monat", "")
        if not _monat_ok(monat):
            return jsonify({"success": False, "error": "Monat fehlt."}), 400
        conn = _conn()
        try:
            rows = [dict(r) for r in conn.execute(
                "SELECT v.*, m.vorname, m.nachname FROM lohn_versand v LEFT JOIN mitarbeiter m ON m.id = v.worker_id "
                "WHERE v.monat = ? ORDER BY m.vorname", (monat,))]
            for r in rows:
                r["name"] = f"{r.pop('vorname') or ''} {r.pop('nachname') or ''}".strip()
                r.pop("datei", None)
            return jsonify({"success": True, "eintraege": rows})
        finally:
            conn.close()

    @app.route("/api/lohn/hochladen", methods=["POST"])
    @login_required
    def lohn_hochladen():
        monat = request.form.get("monat", "")
        datei = request.files.get("pdf")
        if not _monat_ok(monat) or not datei:
            return jsonify({"success": False, "error": "Monat und PDF auswählen."}), 400
        try:
            PdfReader, PdfWriter = _pdf_lib()
        except RuntimeError as exc:
            return jsonify({"success": False, "error": str(exc)}), 500
        roh = datei.read()
        if not roh.startswith(b"%PDF"):
            return jsonify({"success": False, "error": "Das ist keine PDF-Datei."}), 400
        try:
            reader = PdfReader(io.BytesIO(roh))
            if reader.is_encrypted:
                return jsonify({"success": False, "error": "Die Sammel-PDF ist verschlüsselt – bitte ohne Passwort exportieren."}), 400
            texte = [(p.extract_text() or "") for p in reader.pages]
        except Exception as exc:
            return jsonify({"success": False, "error": f"PDF nicht lesbar: {exc}"}), 400

        conn = _conn()
        try:
            # alle Mitarbeiter – auch ausgetretene bekommen ihre letzte Abrechnung; bei gleichem Namen zählt der aktive
            mitarbeiter = [dict(r) for r in conn.execute(
                "SELECT id, vorname, nachname, email, geburtsdatum, status FROM mitarbeiter")]
            zuordnung, unklar = seiten_zuordnen(texte, mitarbeiter)
            ordner = _ordner(monat)
            os.makedirs(ordner, exist_ok=True)
            # nur die Dateien der noch nicht gesendeten Einträge ersetzen – gesendete bleiben (für „Nochmal senden“)
            for (alt,) in conn.execute("SELECT datei FROM lohn_versand WHERE monat = ? AND status <> 'gesendet' AND datei IS NOT NULL",
                                       (monat,)).fetchall():
                if os.path.isfile(alt) and os.path.dirname(os.path.abspath(alt)) == os.path.abspath(ordner):
                    os.remove(alt)
            conn.execute("DELETE FROM lohn_versand WHERE monat = ? AND status <> 'gesendet'", (monat,))
            ergebnis = []
            for m in mitarbeiter:
                seiten = zuordnung.get(m["id"])
                if not seiten:
                    continue
                if conn.execute("SELECT 1 FROM lohn_versand WHERE monat = ? AND worker_id = ? AND status = 'gesendet'",
                                (monat, m["id"])).fetchone():
                    continue
                writer = PdfWriter()
                for nr in seiten:
                    writer.add_page(reader.pages[nr])
                quelle, status = "", "bereit"  # ohne Passwort (wie gonder.py)
                pfad = os.path.join(ordner, f"lohn_{m['id']}_{secrets.token_hex(4)}.pdf")
                with open(pfad, "wb") as fh:
                    writer.write(fh)
                if not (m.get("email") or "").strip():
                    status = "email_fehlt" if status == "bereit" else status
                korr = ", ".join(k for k in (korrektur_monat(texte[nr]) for nr in seiten) if k)
                conn.execute(
                    "INSERT OR REPLACE INTO lohn_versand (monat, worker_id, seiten, datei, email, passwort_quelle, status, erstellt_am, "
                    "korrektur) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (monat, m["id"], ",".join(str(n + 1) for n in seiten), pfad, (m.get("email") or "").strip(), quelle, status,
                     datetime.now().isoformat(timespec="seconds"), korr or None))
                ergebnis.append(m["id"])
            conn.commit()
            return jsonify({"success": True, "seiten": len(texte), "mitarbeiter": len(ergebnis), "unklar": unklar})
        finally:
            conn.close()

    @app.route("/api/lohn/passwort/<int:worker_id>", methods=["POST"])
    @login_required
    def lohn_passwort(worker_id):
        d = request.get_json(silent=True) or {}
        monat, pw = d.get("monat", ""), str(d.get("passwort") or "").strip()
        if not _monat_ok(monat) or len(pw) < 6:
            return jsonify({"success": False, "error": "Passwort mit mindestens 6 Zeichen eingeben."}), 400
        conn = _conn()
        try:
            row = conn.execute("SELECT * FROM lohn_versand WHERE monat = ? AND worker_id = ?", (monat, worker_id)).fetchone()
            if not row or not row["datei"] or not os.path.exists(row["datei"]):
                return jsonify({"success": False, "error": "Datei nicht gefunden – PDF neu hochladen."}), 404
            if row["status"] == "gesendet":
                return jsonify({"success": False, "error": "Schon gesendet."}), 409
            try:
                PdfReader, PdfWriter = _pdf_lib()
            except RuntimeError as exc:
                return jsonify({"success": False, "error": str(exc)}), 500
            reader = PdfReader(row["datei"])
            if reader.is_encrypted:
                return jsonify({"success": False, "error": "Hat schon ein Passwort."}), 409
            writer = PdfWriter(clone_from=reader)
            writer.encrypt(user_password=pw, owner_password=secrets.token_urlsafe(18), algorithm="AES-256")
            with open(row["datei"], "wb") as fh:
                writer.write(fh)
            neu = "bereit" if (row["email"] or "").strip() else "email_fehlt"
            conn.execute("UPDATE lohn_versand SET passwort_quelle = 'von Hand gesetzt', status = ? WHERE id = ?", (neu, row["id"]))
            conn.commit()
            return jsonify({"success": True})
        finally:
            conn.close()

    @app.route("/api/lohn/nochmal/<int:worker_id>", methods=["POST"])
    @login_required
    def lohn_nochmal(worker_id):
        """Schon gesendete Abrechnung wieder freigeben (z. B. Mail kam zurück) – danach normal mit „Ausgewählte senden“."""
        monat = (request.get_json(silent=True) or {}).get("monat", "")
        if not _monat_ok(monat):
            return jsonify({"success": False, "error": "Monat fehlt."}), 400
        conn = _conn()
        try:
            row = conn.execute("SELECT * FROM lohn_versand WHERE monat = ? AND worker_id = ?", (monat, worker_id)).fetchone()
            if not row or row["status"] != "gesendet":
                return jsonify({"success": False, "error": "Nicht gesendet."}), 409
            da = bool(row["datei"]) and os.path.exists(row["datei"])
            # Datei noch da → „bereit“; sonst „datei_fehlt“ – das nächste „Hochladen & aufteilen“ legt sie neu an
            conn.execute("UPDATE lohn_versand SET status = ?, fehler = NULL WHERE id = ?", ("bereit" if da else "datei_fehlt", row["id"]))
            conn.commit()
            return jsonify({"success": True, "status": "bereit" if da else "datei_fehlt"})
        finally:
            conn.close()

    @app.route("/api/lohn/datei/<int:worker_id>")
    @login_required
    def lohn_datei(worker_id):
        monat = request.args.get("monat", "")
        conn = _conn()
        try:
            row = conn.execute("SELECT datei FROM lohn_versand WHERE monat = ? AND worker_id = ?", (monat, worker_id)).fetchone()
        finally:
            conn.close()
        if not row or not row["datei"] or not os.path.exists(row["datei"]):
            return jsonify({"success": False, "error": "Datei nicht gefunden."}), 404
        return send_file(os.path.abspath(row["datei"]), mimetype="application/pdf", as_attachment=True,
                         download_name=f"Lohnabrechnung_{monat}_{worker_id}.pdf")

    def _senden(empfaenger, betreff, text, html, pdf_bytes, dateiname):
        import app2
        msg = EmailMessage()
        msg["From"] = "KG-Gebäudereinigung <info@kg-reinigung.de>"
        msg["To"] = empfaenger
        msg["Subject"] = betreff
        msg.set_content(text)
        msg.add_alternative(html, subtype="html")
        msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf", filename=dateiname)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        sent = app2.get_gmail_service().users().messages().send(userId="me", body={"raw": raw}).execute()
        return sent.get("id", "")

    @app.route("/api/lohn/senden", methods=["POST"])
    @login_required
    def lohn_senden():
        d = request.get_json(silent=True) or {}
        monat = d.get("monat", "")
        ids = [int(x) for x in (d.get("worker_ids") or []) if str(x).isdigit()]
        if not _monat_ok(monat) or not ids:
            return jsonify({"success": False, "error": "Monat und Mitarbeiter auswählen."}), 400
        monat_text = monat_name(monat)  # „Oktober 2026“ wie in gonder.py
        conn = _conn()
        bericht = []
        try:
            for wid in ids:
                row = conn.execute(
                    "SELECT v.*, m.vorname, m.nachname, m.anrede FROM lohn_versand v JOIN mitarbeiter m ON m.id = v.worker_id "
                    "WHERE v.monat = ? AND v.worker_id = ?", (monat, wid)).fetchone()
                if not row or row["status"] != "bereit":
                    bericht.append({"worker_id": wid, "ok": False, "fehler": "nicht bereit"})
                    continue
                try:
                    with open(row["datei"], "rb") as fh:
                        pdf = fh.read()
                    # Passwort-Hinweis nur noch für ältere, schon verschlüsselte Dateien (neu hochgeladene haben keins)
                    q = row["passwort_quelle"] or ""
                    hinweis = ("Die PDF ist mit einem Passwort geschützt: Ihr Geburtsdatum im Format TTMMJJJJ (z. B. 07031990)."
                               if q.startswith("Geburtsdatum") else
                               "Die PDF ist mit einem Passwort geschützt, das wir Ihnen separat mitteilen." if q else "")
                    text, html = mail_inhalt(monat_text, hinweis, row["korrektur"] or "")
                    name = re.sub(r'[\\/:*?"<>|]', "", f"{row['vorname'] or ''} {row['nachname'] or ''}".strip()) or str(wid)
                    _senden(row["email"], f"Lohnabrechnung {monat_text}", text, html, pdf, f"{name} Lohnabrechnung {monat_text}.pdf")
                    conn.execute("UPDATE lohn_versand SET status = 'gesendet', gesendet_am = ?, fehler = NULL WHERE id = ?",
                                 (datetime.now().isoformat(timespec="seconds"), row["id"]))
                    bericht.append({"worker_id": wid, "ok": True})
                except Exception as exc:
                    conn.execute("UPDATE lohn_versand SET fehler = ? WHERE id = ?", (str(exc)[:300], row["id"]))
                    bericht.append({"worker_id": wid, "ok": False, "fehler": str(exc)[:200]})
                conn.commit()
            return jsonify({"success": True, "bericht": bericht})
        finally:
            conn.close()

    @app.route("/api/lohn/loeschen", methods=["POST"])
    @login_required
    def lohn_loeschen():
        monat = (request.get_json(silent=True) or {}).get("monat", "")
        if not _monat_ok(monat):
            return jsonify({"success": False, "error": "Monat fehlt."}), 400
        ordner = _ordner(monat)
        if os.path.isdir(ordner):
            shutil.rmtree(ordner)
        conn = _conn()
        try:
            conn.execute("UPDATE lohn_versand SET datei = NULL WHERE monat = ?", (monat,))
            conn.execute("DELETE FROM lohn_versand WHERE monat = ? AND status <> 'gesendet'", (monat,))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True})
