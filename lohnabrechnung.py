# =====================================================
# LOHNABRECHNUNGEN VERSENDEN
# Sammel-PDF (z. B. aus Lexware/DATEV) hochladen → Seiten werden anhand der
# Namen den Mitarbeitern zugeordnet → je Mitarbeiter ein eigenes PDF mit
# Passwort (AES-256) → Vorschau prüfen → erst nach Klick per KG-Mail senden.
#
# Passwort: Geburtsdatum des Mitarbeiters im Format TTMMJJJJ (z. B. 07031990).
# Ohne Geburtsdatum kann ein Passwort von Hand gesetzt werden.
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


def _fold(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
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
    conn.commit()


def seiten_zuordnen(texte, mitarbeiter):
    """Jede Seite einem Mitarbeiter zuordnen. Seiten ohne Namen gehören zum vorherigen."""
    zuordnung, aktuell, unklar = {}, None, []
    for nr, text in enumerate(texte):
        t = " " + _fold(text) + " "
        treffer = []
        for m in mitarbeiter:
            vn, nn = _fold(m["vorname"]).strip(), _fold(m["nachname"]).strip()
            if nn and f" {nn} " in t and (not vn or f" {vn} " in t):
                treffer.append(m["id"])
        if len(treffer) == 1:
            aktuell = treffer[0]
        elif len(treffer) > 1:
            unklar.append(nr + 1)
            aktuell = None
        if aktuell is None:
            if not treffer:
                unklar.append(nr + 1)
            continue
        zuordnung.setdefault(aktuell, []).append(nr)
    return zuordnung, sorted(set(unklar))


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
            mitarbeiter = [dict(r) for r in conn.execute(
                "SELECT id, vorname, nachname, email, geburtsdatum FROM mitarbeiter WHERE status = 'aktiv'")]
            zuordnung, unklar = seiten_zuordnen(texte, mitarbeiter)
            ordner = _ordner(monat)
            if os.path.isdir(ordner):
                shutil.rmtree(ordner)
            os.makedirs(ordner, exist_ok=True)
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
                pw = _passwort_aus_geburtsdatum(m.get("geburtsdatum"))
                quelle = "Geburtsdatum (TTMMJJJJ)" if pw else ""
                status = "bereit" if pw else "passwort_fehlt"
                if pw:
                    writer.encrypt(user_password=pw, owner_password=secrets.token_urlsafe(18), algorithm="AES-256")
                pfad = os.path.join(ordner, f"lohn_{m['id']}_{secrets.token_hex(4)}.pdf")
                with open(pfad, "wb") as fh:
                    writer.write(fh)
                if not (m.get("email") or "").strip():
                    status = "email_fehlt" if status == "bereit" else status
                conn.execute(
                    "INSERT OR REPLACE INTO lohn_versand (monat, worker_id, seiten, datei, email, passwort_quelle, status, erstellt_am) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (monat, m["id"], ",".join(str(n + 1) for n in seiten), pfad, (m.get("email") or "").strip(), quelle, status,
                     datetime.now().isoformat(timespec="seconds")))
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

    def _senden(empfaenger, betreff, text, pdf_bytes, dateiname):
        import app2
        msg = EmailMessage()
        msg["From"] = "KG-Gebäudereinigung <info@kg-reinigung.de>"
        msg["To"] = empfaenger
        msg["Subject"] = betreff
        msg.set_content(text)
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
        monat_text = datetime.strptime(monat, "%Y-%m").strftime("%m/%Y")
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
                    hinweis = ("Das Passwort ist Ihr Geburtsdatum im Format TTMMJJJJ (z. B. 07031990)."
                               if row["passwort_quelle"].startswith("Geburtsdatum") else
                               "Das Passwort teilen wir Ihnen separat mit.")
                    text = (f"Hallo {row['vorname'] or ''},\n\n"
                            f"anbei Ihre Lohnabrechnung für {monat_text}. Die PDF ist mit einem Passwort geschützt.\n"
                            f"{hinweis}\n\nBei Fragen melden Sie sich gern.\n\n"
                            "Viele Grüße\nKG-Gebäudereinigung\nFliederstr. 59, 47055 Duisburg · 0203 47966822")
                    _senden(row["email"], f"Ihre Lohnabrechnung {monat_text}", text, pdf,
                            f"Lohnabrechnung_{monat}_{_fold(row['nachname']).strip().replace(' ', '_') or wid}.pdf")
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
