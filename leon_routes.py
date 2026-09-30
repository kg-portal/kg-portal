# =====================================================
# LEON – TELEFONASSISTENT (KG Gebäudereinigung)
# CRM <-> Leon-Motor (https://leon.kg-reinigung.de)
#
# Der Motor telefoniert (Twilio + OpenAI). Das CRM schickt nur
# Firma/Telefon hin, startet den Anruf und holt das Ergebnis zurück.
# Zugangsdaten nur aus tokenlar.env / Render Environment:
#   LEON_URL, LEON_USER, LEON_PASSWORD
# =====================================================

import os
import threading
from datetime import datetime

import requests
from flask import Response, abort, jsonify, render_template, request, stream_with_context


LEON_TERMINAL_STATUSES = {
    "Beendet",
    "Besetzt",
    "Fehler",
    "Nicht erreichbar",
    "Abgebrochen",
    "OpenAI Fehler",
    "Anrufbeantworter",
    "Fax",
}

LEON_DEFAULT_NAME = "Leon Reinigung"
LEON_DEFAULT_OPENING = "Hallo, schönen guten Tag. Leon, mein Name. Ich rufe von der Firma Ka Ge Gebäudereinigung an und möchte gern mit jemandem sprechen, der bei Ihnen für das Thema Reinigung zuständig ist."

LEON_DEFAULT_VOICE_PROMPT = """Du bist Leon von KG Gebäudereinigung aus Duisburg.

Du rufst Unternehmen, Praxen, Kanzleien und andere Gewerbekunden an.
ZIEL jedes Gesprächs: ein kostenloser, unverbindlicher Besichtigungstermin vor Ort, damit wir ein individuelles Angebot machen können.

STIL:
Lebendig, freundlich, höflich, mit hörbarem Lächeln. Kurze, einfache Sätze.
Nicht wie ein Callcenter, nicht wie ein vorgelesenes Skript.
Reagiere immer auf das, was der Gesprächspartner wirklich sagt. Immer nur eine Frage auf einmal.
Wenn der Gesprächspartner spricht oder dich unterbricht, sofort verstummen und zuhören.
„KG“ sprichst du immer „Ka Ge“ aus.

1. EINLEITUNG
Sobald sich jemand meldet („Hallo“, Firmenname, Name …), sagst du genau den Begrüßungstext:
„Hallo, schönen guten Tag. Leon, mein Name. Ich rufe von der Firma Ka Ge Gebäudereinigung an und möchte gern mit jemandem sprechen, der bei Ihnen für das Thema Reinigung zuständig ist.“
Danach warten und zuhören.

2. WENN DU WEITERVERBUNDEN WIRST
Sagt die Person z. B. „Ich verbinde Sie mit Frau Müller“: kurz bedanken und warten.
Meldet sich danach eine neue Person, stellst du dich kurz neu vor und gehst zu Punkt 4.

3. WENN DIE ZUSTÄNDIGE PERSON NICHT DA IST (z. B. Zentrale)
Frage freundlich nach und notiere alles:
- Name der zuständigen Person („Wie ist der Name der Ansprechpartnerin bzw. des Ansprechpartners?“)
- direkte Telefonnummer oder E-Mail-Adresse, falls man sie dir geben möchte
- wann du die Person am besten erreichst: Tag und konkrete Uhrzeit
Eine genannte Telefonnummer einmal zur Kontrolle wiederholen.
E-Mail-Adressen immer buchstabieren lassen: „Können Sie mir die E-Mail-Adresse bitte einmal buchstabieren?“ und danach einmal vollständig zur Bestätigung vorlesen.
Wenn Tag und Uhrzeit feststehen, wird der Rückruf mit Name und Kontaktdaten im Kalender eingetragen.
Sage erst dann „Alles klar, dann melde ich mich am … um … bei Frau/Herrn …“, wenn der Eintrag bestätigt wurde.
Erfinde nie einen Namen, eine Nummer, einen Tag oder eine Uhrzeit.

4. WENN DU MIT DER ZUSTÄNDIGEN PERSON SPRICHST
Zum Beispiel:
„Ach, hallo Frau Müller, schön, dass ich Sie erreiche. Ich möchte das Thema Reinigung einmal kurz ansprechen: Wir bieten für Gewerbekunden regelmäßige Büroreinigung bzw. Fensterreinigung an. Wie sieht das Thema aktuell bei Ihnen aus?“
Dann den Gesprächspartner erzählen lassen und gut zuhören.
Danach – passend zum Gespräch, eine Frage nach der anderen:
- „Wie häufig wird aktuell bei Ihnen gereinigt?“
- „Kommt eine Reinigungsfirma oder machen das interne Angestellte?“
- „Wie viel Fläche bzw. wie viele Quadratmeter hat Ihr Objekt ungefähr?“
Wenn verschiedene Bereiche unterschiedlich oft gereinigt werden sollen (z. B. Sanitär zweimal, Büro einmal pro Woche), das getrennt aufnehmen.
Wenn der Kunde Quadratmeter oder Details nicht weiß, nicht drängen: „Kein Problem, das schauen wir uns bei der Besichtigung an.“
Keine Preise nennen. Bei Preisfragen: „Das hängt von Fläche und Rhythmus ab – deshalb machen wir Ihnen nach der Besichtigung ein festes Angebot.“

5. BESICHTIGUNGSTERMIN (Hauptziel)
An passender Stelle, spätestens nach den Fragen:
„Wir möchten Ihnen gerne ein individuelles, unverbindliches Angebot machen und dazu einen unverbindlichen Besichtigungstermin mit Ihnen vereinbaren. Wann passt es Ihnen, Frau Müller – zum Beispiel nächste Woche Dienstag vormittags?“
Kläre Tag, konkrete Uhrzeit, Adresse des Objekts und wer vor Ort ist.
Wenn nur „nächste Woche“ genannt wird, nach dem Tag fragen; bei Tageszeit nach der konkreten Uhrzeit.
Der Termin wird im Kalender eingetragen. Erst wenn das bestätigt ist, sagen:
„Wunderbar, ich habe den Termin eingetragen. Sie bekommen gleich noch eine Terminbestätigung per E-Mail.“

6. E-MAIL-ADRESSE
Frage im Gespräch nach der E-Mail-Adresse – bei vereinbarter Besichtigung für die Terminbestätigung, sonst für Kontaktdaten und Unternehmensportfolio:
- Wenn in den Kundendaten schon eine E-Mail-Adresse steht: „Ist die E-Mail-Adresse … noch aktuell?“
- Wenn nicht: neu erfragen, buchstabieren lassen und einmal vollständig bestätigen.
Danach bei Besichtigung: „Vielen Dank, Sie bekommen gleich die Terminbestätigung per E-Mail.“
Sonst: „Vielen Dank, wir schicken Ihnen unsere Kontaktdaten per E-Mail zu.“
Behaupte einen Versand nur, wenn er bestätigt wurde.

7. WENN KEIN INTERESSE
Akzeptieren, nicht drängen, freundlich verabschieden.
Wenn der Kunde zufrieden ist, höchstens einmal fragen, wann der aktuelle Vertrag ungefähr endet.

KI-FRAGE:
Fragt jemand, ob du eine KI oder ein Bot bist: „Ich bin ein digitaler Sprachassistent von Ka Ge Gebäudereinigung.“ Keine technische Erklärung.

ABSCHIED:
Bevor du dich verabschiedest, fragst du immer höflich: „Haben Sie sonst noch eine Frage an mich?“
Beantworte eine Frage kurz. Wenn keine Frage mehr kommt:
„Vielen Dank für das nette Telefongespräch. Ich wünsche Ihnen noch einen schönen Tag. Tschüss!“
Sagt der Gesprächspartner danach noch „Tschüss“ oder „Auf Wiederhören“, antworte genau einmal kurz „Tschüss.“ und sage danach nichts mehr – das Gespräch wird dann beendet.

WICHTIGSTE REGEL:
Führe ein echtes Gespräch. Der Gesprächspartner bestimmt mit seiner Antwort den nächsten sinnvollen Schritt."""

LEON_DEFAULT_BACKEND_PROMPT = """Du bist der interne technische Assistent für Leons Telefonate von KG Gebäudereinigung.

Leon führt das hörbare Gespräch. Du führst ausschließlich technische Aufgaben im Hintergrund aus.
Keine eigene Gesprächsführung, keine Begrüßung, keine Kundenfragen, keine Verkaufstexte.
Nutze ausschließlich Informationen, die im aktuellen Gespräch tatsächlich genannt oder bestätigt wurden.
Erfinde niemals Namen, Firmen, Adressen, Telefonnummern, E-Mail-Adressen, Termine oder Uhrzeiten.

RÜCKRUF (zuständige Person nicht erreichbar):
create_lena_callback verwenden, sobald Tag und konkrete Uhrzeit feststehen.
In notiz schreiben: „Rückruf“ + Name der zuständigen Person + genannte Telefonnummer + genannte E-Mail-Adresse.
entscheider_name = Name der zuständigen Person, falls genannt.

BESICHTIGUNG (Hauptziel):
create_lena_callback verwenden, sobald Tag und konkrete Uhrzeit der Besichtigung feststehen.
notiz MUSS mit „Besichtigung“ beginnen und enthält Adresse des Objekts, Ansprechpartner vor Ort und kurz Objekt/Fläche/Rhythmus, soweit genannt.

E-MAIL (zwei Vorlagen):
send_lena_email nur mit einer im Gespräch buchstabierten und bestätigten Adresse (email_confirmed=true).
- vorlage=kontakt: Kontaktdaten & Unternehmensportfolio, wenn der Kunde das möchte oder die Adresse dafür genannt hat.
- vorlage=besichtigung: Terminbestätigung – nur NACH erfolgreich eingetragener Besichtigung (create_lena_callback success=true).
  termin = das vom Backend bestätigte Datum mit Uhrzeit (z. B. „07.10.2026 um 10:00 Uhr“), adresse = Objektadresse, falls genannt.
Wurde eine Besichtigung vereinbart: NUR vorlage=besichtigung senden, KEINE Kontakt-/Portfolio-Mail.
Kein Besichtigungstermin, aber Interesse oder Wunsch nach Infos: NUR vorlage=kontakt senden.
Kein Interesse: keine Mail. Jede Mail höchstens einmal.
anrede = Herr oder Frau des Ansprechpartners (leer, wenn unklar).
Ausschließlich die bestätigte Kundenadresse verwenden. info@kg-reinigung.de ist unsere eigene Adresse, niemals Empfänger.

NICHT ANRUFEN / KEIN INTERESSE:
mark_do_not_call sofort verwenden, wenn der Gesprächspartner klar sagt, dass kein Interesse besteht oder dass er nicht mehr angerufen werden möchte.
Bei allgemeiner Ablehnung reason_type=kein_interesse, bei „nicht mehr anrufen“ reason_type=nicht_mehr_anrufen.
Keine Ablehnung erfinden oder aus Schweigen ableiten.

RELATIVE TERMINE:
Bei Angaben wie „nächste Woche Dienstag“ datum_text und wochentag genau wie gesagt übergeben, kein eigenes Datum erfinden.

AKTIONEN:
Bereits erfolgreich ausgeführte Aktionen niemals doppelt ausführen.
Ein pending- oder uncertain-Ergebnis ist kein Erfolg. Erfolg nur melden, wenn success=true zurückkam.
Schlägt etwas fehl, Leon kurz melden, dass die Kollegin sich dazu meldet – ohne technische Details.

Gib Leon nur das kurze, für das Gespräch notwendige Ergebnis zurück. Keine Wartefloskeln."""


# Business-Leon-Seiten im CRM (templates/leon_ui, API über /leon-api)
LEON_PAGES = {
    "live": ("live.html", {}),
    "kampagnen": ("kampagnen.html", {}),
    "leads": ("leads.html", {}),
    "gespraeche": ("gespraechsuebersicht.html", {"archive_mode": False}),
    "archiv": ("gespraechsuebersicht.html", {"archive_mode": True}),
    "anrufe": ("anrufe.html", {}),
    "ergebnisse": ("ergebnisse.html", {}),
    "agent": ("agent.html", {}),
    "system": ("einstellungen.html", {}),
}

LEON_TABS = [
    ("uebersicht", "/leon", "Übersicht"),
    ("live", "/leon/live", "Live Call"),
    ("datenbank", "/leon/datenbank", "Aus Datenbank"),
    ("kampagnen", "/leon/kampagnen", "Kampagnen"),
    ("leads", "/leon/leads", "Leads"),
    ("gespraeche", "/leon/gespraeche", "Gespräche"),
    ("archiv", "/leon/archiv", "Archiv"),
    ("anrufe", "/leon/anrufe", "Anrufe"),
    ("ergebnisse", "/leon/ergebnisse", "Ergebnisse"),
    ("agent", "/leon/agent", "Leon Einstellungen"),
    ("system", "/leon/system", "Anrufzeiten & Kosten"),
]


# -----------------------------------------------------
# Verbindung zum Leon-Motor
# -----------------------------------------------------

class LeonError(Exception):
    pass


class LeonClient:
    """Eingeloggte Sitzung beim Leon-Motor (gleiches Login wie dort)."""

    def __init__(self):
        self._session = None
        self._lock = threading.Lock()

    @staticmethod
    def base_url():
        return (os.getenv("LEON_URL") or "https://leon.kg-reinigung.de").strip().rstrip("/")

    @staticmethod
    def configured():
        return bool((os.getenv("LEON_USER") or "").strip() and os.getenv("LEON_PASSWORD"))

    def _login(self):
        if not self.configured():
            raise LeonError("LEON_USER / LEON_PASSWORD fehlen in tokenlar.env bzw. Render Environment.")
        session = requests.Session()
        try:
            resp = session.post(
                self.base_url() + "/login",
                data={
                    "username": (os.getenv("LEON_USER") or "").strip(),
                    "password": os.getenv("LEON_PASSWORD") or "",
                },
                allow_redirects=False,
                timeout=15,
            )
        except requests.RequestException as exc:
            raise LeonError(f"Leon nicht erreichbar: {exc}")
        location = resp.headers.get("Location", "")
        if resp.status_code not in (301, 302, 303) or "/login" in location:
            raise LeonError("Anmeldung bei Leon fehlgeschlagen (Benutzer/Passwort prüfen).")
        self._session = session

    @staticmethod
    def _needs_login(resp):
        if resp.status_code in (401, 403):
            return True
        if resp.status_code in (301, 302, 303) and "/login" in resp.headers.get("Location", ""):
            return True
        return False

    def raw(self, method, path, params=None, data=None, headers=None, stream=False, timeout=60):
        """Ungefilterte Antwort vom Motor (für den /leon-api Proxy)."""
        for attempt in range(2):
            with self._lock:
                if self._session is None:
                    self._login()
                session = self._session
            try:
                resp = session.request(
                    method,
                    self.base_url() + path,
                    params=params,
                    data=data,
                    headers=headers,
                    allow_redirects=False,
                    stream=stream,
                    timeout=timeout,
                )
            except requests.RequestException as exc:
                raise LeonError(f"Leon nicht erreichbar: {exc}")
            if self._needs_login(resp) and attempt == 0:
                resp.close()
                with self._lock:
                    if self._session is session:
                        self._session = None
                continue
            return resp
        raise LeonError("Anmeldung bei Leon fehlgeschlagen.")

    def request(self, method, path, payload=None, timeout=30):
        with self._lock:
            for attempt in range(2):
                if self._session is None:
                    self._login()
                try:
                    resp = self._session.request(
                        method,
                        self.base_url() + path,
                        json=payload,
                        allow_redirects=False,
                        timeout=timeout,
                    )
                except requests.RequestException as exc:
                    raise LeonError(f"Leon nicht erreichbar: {exc}")
                if self._needs_login(resp) and attempt == 0:
                    self._session = None
                    continue
                try:
                    data = resp.json()
                except ValueError:
                    raise LeonError(f"Unerwartete Antwort von Leon (HTTP {resp.status_code}).")
                return resp.status_code, data
        raise LeonError("Anmeldung bei Leon fehlgeschlagen.")


leon_client = LeonClient()


# -----------------------------------------------------
# Datenbank (eigene Tabelle im CRM)
# -----------------------------------------------------

def ensure_leon_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leon_anrufe (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crm_ref TEXT,
            firma TEXT,
            telefon TEXT,
            ansprechpartner TEXT,
            ort TEXT,
            leon_lead_id INTEGER,
            leon_call_id INTEGER,
            status TEXT,
            ergebnis TEXT,
            zusammenfassung TEXT,
            transkript TEXT,
            dauer_sekunden INTEGER,
            rueckruf_am TEXT,
            rueckruf_notiz TEXT,
            lead_status TEXT,
            fehler TEXT,
            erledigt INTEGER DEFAULT 0,
            erstellt_am TEXT,
            aktualisiert_am TEXT
        )
        """
    )
    conn.commit()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean(value, limit=300):
    return str(value or "").strip()[:limit]


def _row_to_dict(row):
    item = dict(row)
    item["abgeschlossen"] = bool(
        (item.get("status") in LEON_TERMINAL_STATUSES and (item.get("zusammenfassung") or item.get("lead_status")))
        or item.get("fehler")
    )
    return item


def _sync_row(conn, row):
    """Holt Status/Ergebnis eines laufenden Anrufs vom Leon-Motor."""
    if not row["leon_call_id"]:
        return
    _, call_data = leon_client.request("GET", f"/api/calls/{row['leon_call_id']}")
    call = (call_data or {}).get("call") or {}
    updates = {
        "status": call.get("status") or row["status"],
        "ergebnis": call.get("result") or row["ergebnis"],
        "zusammenfassung": call.get("summary") or row["zusammenfassung"],
        "transkript": call.get("transcript") or row["transkript"],
        "dauer_sekunden": call.get("duration_seconds") or row["dauer_sekunden"],
    }
    if call.get("error_message"):
        updates["fehler"] = call.get("error_message")

    if row["leon_lead_id"]:
        _, lead_data = leon_client.request("GET", f"/api/leads/{row['leon_lead_id']}")
        lead = (lead_data or {}).get("lead") or {}
        updates["lead_status"] = lead.get("status") or row["lead_status"]
        updates["rueckruf_am"] = lead.get("callback_at") or row["rueckruf_am"]
        updates["rueckruf_notiz"] = lead.get("callback_note") or row["rueckruf_notiz"]

    updates["aktualisiert_am"] = _now()
    sets = ", ".join(f"{key} = ?" for key in updates)
    conn.execute(
        f"UPDATE leon_anrufe SET {sets} WHERE id = ?",
        list(updates.values()) + [row["id"]],
    )


# -----------------------------------------------------
# Routen
# -----------------------------------------------------

def register_leon_routes(app, login_required, get_db_connection):

    conn = get_db_connection()
    try:
        ensure_leon_tables(conn)
    finally:
        conn.close()

    # Datenbank/Tagesliste <-> Leon (Übergabe + Rückmeldung)
    from leon_datenbank import register_leon_datenbank
    register_leon_datenbank(app, login_required, get_db_connection, leon_client, LeonError)

    @app.route("/leon")
    @login_required
    def leon_page():
        return render_template("leon.html", leon_tab="uebersicht", leon_tabs=LEON_TABS)

    @app.route("/leon/datenbank")
    @login_required
    def leon_datenbank_page():
        return render_template("leon_datenbank.html", leon_tab="datenbank", leon_tabs=LEON_TABS)

    @app.route("/leon/<page>")
    @login_required
    def leon_frame(page):
        if page not in LEON_PAGES:
            abort(404)
        return render_template("leon_frame.html", leon_tab=page, leon_tabs=LEON_TABS, frame_src=f"/leon-ui/{page}")

    @app.route("/leon/gespraech/<int:call_id>")
    @login_required
    def leon_frame_gespraech(call_id):
        return render_template("leon_frame.html", leon_tab="gespraeche", leon_tabs=LEON_TABS, frame_src=f"/leon-ui/gespraech/{call_id}")

    @app.route("/leon-ui/<page>")
    @login_required
    def leon_ui(page):
        if page not in LEON_PAGES:
            abort(404)
        template, context = LEON_PAGES[page]
        return render_template(f"leon_ui/{template}", **context)

    @app.route("/leon-ui/gespraech/<int:call_id>")
    @login_required
    def leon_ui_gespraech(call_id):
        return render_template("leon_ui/gespraech_detail.html", call_id=call_id)

    @app.route("/leon-api/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    @login_required
    def leon_api_proxy(path):
        headers = {}
        for key in ("Content-Type", "Range", "Accept"):
            if request.headers.get(key):
                headers[key] = request.headers[key]
        try:
            upstream = leon_client.raw(
                request.method,
                "/api/" + path,
                params=request.args,
                data=request.get_data(),
                headers=headers,
                stream=True,
                timeout=120,
            )
        except LeonError as exc:
            return jsonify({"success": False, "error": str(exc)}), 502

        passthrough = {}
        for key in ("Content-Type", "Content-Range", "Accept-Ranges", "Content-Disposition", "Cache-Control"):
            if upstream.headers.get(key):
                passthrough[key] = upstream.headers[key]
        if upstream.headers.get("Content-Length") and "Content-Encoding" not in upstream.headers:
            passthrough["Content-Length"] = upstream.headers["Content-Length"]

        def generate():
            try:
                for chunk in upstream.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        return Response(stream_with_context(generate()), status=upstream.status_code, headers=passthrough)

    @app.route("/api/leon/status")
    @login_required
    def leon_status():
        if not leon_client.configured():
            return jsonify({"ok": False, "error": "LEON_USER / LEON_PASSWORD fehlen in tokenlar.env."})
        try:
            code, data = leon_client.request("GET", "/api/telephony/status", timeout=15)
        except LeonError as exc:
            return jsonify({"ok": False, "error": str(exc)})
        ready = code == 200 and bool((data or {}).get("ready"))
        return jsonify({
            "ok": ready,
            "error": "" if ready else "Leon erreichbar, aber Telefonie noch nicht fertig eingerichtet.",
            "url": leon_client.base_url(),
            "telefonie": data,
        })

    @app.route("/api/leon/anrufen", methods=["POST"])
    @login_required
    def leon_anrufen():
        data = request.get_json(silent=True) or {}
        firma = _clean(data.get("firma"))
        telefon = _clean(data.get("telefon"), 60)
        ansprechpartner = _clean(data.get("ansprechpartner"))
        ort = _clean(data.get("ort"))
        crm_ref = _clean(data.get("crm_ref"), 120)
        if not firma or not telefon:
            return jsonify({"success": False, "error": "Firma und Telefon sind Pflicht."}), 400

        try:
            code, lead_resp = leon_client.request(
                "POST",
                "/api/leads",
                {
                    "firma": firma,
                    "telefon": telefon,
                    "ansprechpartner": ansprechpartner,
                    "stadt": ort,
                    "source": "KG CRM",
                },
            )
            if code == 409 and lead_resp.get("duplicate_id"):
                lead_id = lead_resp["duplicate_id"]
            elif lead_resp.get("success") and (lead_resp.get("lead") or lead_resp.get("id") or lead_resp.get("lead_id")):
                lead_id = (lead_resp.get("lead") or {}).get("id") or lead_resp.get("id") or lead_resp.get("lead_id")
            else:
                return jsonify({"success": False, "error": lead_resp.get("error") or "Lead konnte nicht angelegt werden."}), 502

            code, call_resp = leon_client.request("POST", "/api/calls/start", {"lead_id": lead_id}, timeout=60)
        except LeonError as exc:
            return jsonify({"success": False, "error": str(exc)}), 502

        started = bool(call_resp.get("success"))
        conn = get_db_connection()
        try:
            cur = conn.execute(
                """
                INSERT INTO leon_anrufe (
                    crm_ref, firma, telefon, ansprechpartner, ort,
                    leon_lead_id, leon_call_id, status, fehler, erstellt_am, aktualisiert_am
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    crm_ref, firma, telefon, ansprechpartner, ort,
                    lead_id, call_resp.get("call_id"),
                    call_resp.get("status") or ("Gestartet" if started else "Fehler"),
                    None if started else (call_resp.get("error") or "Anruf konnte nicht gestartet werden."),
                    _now(), _now(),
                ),
            )
            conn.commit()
            anruf_id = cur.lastrowid
        finally:
            conn.close()

        if not started:
            return jsonify({"success": False, "id": anruf_id, "error": call_resp.get("error") or "Anruf konnte nicht gestartet werden."}), 502
        return jsonify({"success": True, "id": anruf_id, "status": call_resp.get("status")})

    @app.route("/api/leon/anrufe")
    @login_required
    def leon_anrufe():
        conn = get_db_connection()
        try:
            offen = conn.execute(
                """
                SELECT * FROM leon_anrufe
                WHERE leon_call_id IS NOT NULL
                  AND COALESCE(fehler, '') = ''
                  AND (status NOT IN ({}) OR COALESCE(zusammenfassung, '') = '')
                  AND erstellt_am >= datetime('now', 'localtime', '-2 days')
                """.format(",".join("?" for _ in LEON_TERMINAL_STATUSES)),
                list(LEON_TERMINAL_STATUSES),
            ).fetchall()
            sync_error = ""
            for row in offen:
                try:
                    _sync_row(conn, row)
                except LeonError as exc:
                    sync_error = str(exc)
                    break
            conn.commit()
            rows = conn.execute(
                "SELECT * FROM leon_anrufe ORDER BY id DESC LIMIT 200"
            ).fetchall()
        finally:
            conn.close()
        return jsonify({"success": True, "anrufe": [_row_to_dict(r) for r in rows], "sync_error": sync_error})

    @app.route("/api/leon/anrufe/<int:anruf_id>/erledigt", methods=["POST"])
    @login_required
    def leon_anruf_erledigt(anruf_id):
        data = request.get_json(silent=True) or {}
        conn = get_db_connection()
        try:
            conn.execute(
                "UPDATE leon_anrufe SET erledigt = ?, aktualisiert_am = ? WHERE id = ?",
                (0 if data.get("rueckgaengig") else 1, _now(), anruf_id),
            )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True})

    @app.route("/api/leon/einstellungen", methods=["GET"])
    @login_required
    def leon_einstellungen_get():
        try:
            code, data = leon_client.request("GET", "/api/agent/settings")
        except LeonError as exc:
            return jsonify({"success": False, "error": str(exc)}), 502
        if code != 200:
            return jsonify({"success": False, "error": data.get("error") or "Einstellungen nicht lesbar."}), 502
        return jsonify({
            "success": True,
            "settings": {
                "agent_name": data.get("agent_name") or "",
                "opening": data.get("opening") or "",
                "voice_prompt": data.get("voice_prompt") or "",
                "backend_prompt": data.get("backend_prompt") or "",
            },
            "vorlage": {
                "agent_name": LEON_DEFAULT_NAME,
                "opening": LEON_DEFAULT_OPENING,
                "voice_prompt": LEON_DEFAULT_VOICE_PROMPT,
                "backend_prompt": LEON_DEFAULT_BACKEND_PROMPT,
            },
        })

    @app.route("/api/leon/einstellungen", methods=["POST"])
    @login_required
    def leon_einstellungen_save():
        data = request.get_json(silent=True) or {}
        try:
            code, current = leon_client.request("GET", "/api/agent/settings")
            if code != 200:
                return jsonify({"success": False, "error": current.get("error") or "Einstellungen nicht lesbar."}), 502
            # Stimme, Modell usw. bleiben wie im Motor; nur Name, Eröffnung und Prompts ändern
            payload = dict(current)
            for key in ("agent_name", "opening", "voice_prompt", "backend_prompt"):
                if key in data:
                    payload[key] = str(data.get(key) or "")
            code, saved = leon_client.request("POST", "/api/agent/settings", payload)
        except LeonError as exc:
            return jsonify({"success": False, "error": str(exc)}), 502
        if code != 200 or not saved.get("success", True):
            return jsonify({"success": False, "error": saved.get("error") or "Speichern fehlgeschlagen."}), 502
        return jsonify({"success": True})
