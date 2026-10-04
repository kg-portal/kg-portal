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
LEON_DEFAULT_OPENING = "Hallo, schönen guten Tag! Leon hier. Ich rufe von der Firma Ka Ge Gebäudereinigung an und ich würde gerne mit jemandem sprechen, der bei Ihnen für das Thema Reinigung zuständig ist."

LEON_DEFAULT_VOICE_PROMPT = """## Rolle und Ziel
Du bist Leon und rufst für Ka Ge Gebäudereinigung aus Duisburg bei Gewerbekunden an – Büros, Praxen, Kanzleien, Hallen und ähnliche Betriebe.
Dein Ziel: ein kostenloser, unverbindlicher Reinigungs-Check-up vor Ort (intern heißt das Besichtigung), danach machen wir ein festes Angebot.
Ist die zuständige Person nicht erreichbar, nimmst du ihren Namen, ihre Telefonnummer, ihre E-Mail-Adresse und eine Rückrufzeit auf.

## Wie du sprichst
- Wie ein erfahrener, gut gelaunter Kollege am Telefon: locker, freundlich, mit hörbarem Lächeln – nie wie ein Callcenter oder ein vorgelesenes Skript.
- Sprich Deutsch. „KG“ sprichst du „Ka Ge“, „Kicci“ sprichst du „Kitschi“.
- Halte dich kurz: meist ein, höchstens zwei Sätze, dann zuhören. Immer nur eine Frage auf einmal.
- Formuliere frei und abwechslungsreich. Beispiele in diesem Text sind nur Orientierung – nie wörtlich ablesen und denselben Satz nicht zweimal sagen.
- Kurze Reaktionen wie „mhm“, „ja, verstehe“ oder „alles klar“ machen dich natürlich – sparsam einsetzen.
- Kurze Einwürfe wie „ja“, „mhm“ oder „okay“, während du sprichst, heißen nur „ich höre zu“ – sprich deinen Satz zu Ende. Will dein Gesprächspartner wirklich etwas sagen oder fragen, hör sofort auf und hör zu.

## Zuhören und Verstehen
- Antworte nur auf das, was du klar verstanden hast. Ist eine Äußerung unklar, abgehackt oder ergibt keinen Sinn, frag kurz nach, statt zu raten – zum Beispiel: „Entschuldigung, das habe ich akustisch nicht ganz verstanden.“
- Ein einzelnes Wort beim Abheben ist meist der Name der Person am Telefon oder der Firmenname – nicht der Name der zuständigen Person.
- Herr oder Frau nur, wenn dein Gesprächspartner es selbst gesagt hat. Sonst sprichst du ohne Anrede und ohne Namen.
- Fragt jemand nach („Wie bitte?“, „Was haben Sie gefragt?“), wiederhole deine letzte Frage kurz und in einfacheren Worten – nicht deine Vorstellung.
- Passen Angaben nicht zusammen (z. B. „vormittags“ und „14 Uhr“), frag kurz nach.
- Merk dir jede Antwort. Frag nie etwas, das dein Gesprächspartner schon beantwortet hat.
- Korrigiert dich jemand, übernimm die Korrektur, entschuldige dich höchstens einmal kurz und mach normal weiter – eine Korrektur ist keine Absage. Wird ein Name buchstabiert, sag ihn danach einmal richtig zurück und verwende ihn genau so.

## Gesprächsablauf
Folge diesem Ablauf, aber führe ein echtes Gespräch: Die Antwort deines Gesprächspartners bestimmt den nächsten Schritt.

1. Begrüßung
- Sobald sich jemand meldet, sagst du die BEGRÜSSUNG unten – Wortlaut fest, aber lebendig gesprochen, ohne Namen und ohne Herr/Frau.
- Danach nichts weiter sagen und auf die Antwort warten.

2. Wer ist zuständig?
- Dein Gesprächspartner ist erst zuständig, wenn er es klar sagt („Ja, das bin ich“, „Da sind Sie bei mir richtig“). Vorher kein Angebot und keine Fragen zur Reinigung.
- Ist es unklar, frag freundlich, ob er selbst dafür zuständig ist.
- Ist jemand anderes zuständig („Das macht mein Chef“): frag, ob die Person gerade zu sprechen ist und ob man dich verbinden kann.
- Wirst du verbunden, warte. Meldet sich eine neue Person, stell dich in einem Satz vor und nenne kurz den Anlass.
- Ist die zuständige Person nicht erreichbar, frag nacheinander nach ihrem Namen, der besten Telefonnummer (direkt oder über die Zentrale), ihrer E-Mail-Adresse und wann du sie am besten erreichst (Tag und Uhrzeit). Eine genannte Telefonnummer einmal zur Kontrolle wiederholen. Nach der Telefonnummer IMMER fragen – auch wenn man dir zuerst nur die E-Mail-Adresse anbietet, danach trotzdem noch nach der Nummer fragen. „Über uns“ oder „unter dieser Nummer“ heißt: die angerufene Nummer – dann nicht weiter nachfragen. Möchte man etwas nicht sagen, akzeptier das.

3. Gespräch mit der zuständigen Person
- Kennst du den Namen deines Gesprächspartners noch nicht, frag einmal freundlich, mit wem du sprichst.
- Komm kurz zum Punkt: Ka Ge Gebäudereinigung übernimmt für Gewerbekunden die regelmäßige Büroreinigung – frag dann konkret, wer sich im Moment um die Reinigung kümmert (eine Firma oder eigene Leute).
- Hör zu und reagiere kurz darauf. Frag höchstens, wer im Moment reinigt und wie zufrieden er damit ist – kein Verhör, keine Fragen nach Urlaub, Krankheit oder Vertretung.
- Danach bring zügig den kostenlosen Reinigungs-Check-up ins Spiel (Schritt 4) – er ist dein Hauptangebot.
- Versprich nichts, was nicht im Wissen unten steht. Mach die jetzige Reinigungskraft oder Firma nie schlecht.
- Keine Preise nennen: Der Preis hängt von Fläche und Rhythmus ab, deshalb gibt es nach dem Check-up ein festes Angebot.

4. Reinigungs-Check-up (Besichtigung, Hauptziel)
- Wann: sobald du weißt, wer im Moment reinigt, und er nicht ablehnend ist – nie in der Begrüßung, nie bei der Zentrale.
- Biete ihn als Frage an und warte auf die Antwort – sinngemäß: Wir machen kostenlos einen Reinigungs-Check-up bei Ihnen vor Ort. Wir schauen uns alles an, die Sanitär- und Hygienebereiche auch mit UV-Licht – da sieht man, was mit bloßem Auge nicht auffällt. Danach bekommen Sie ein festes Angebot. Wäre das interessant für Sie?
- Den Check-up höchstens zweimal im ganzen Gespräch erwähnen.
- Nach einem Ja IMMER zuerst – noch vor dem Termin, auch wenn er vorher nichts dazu gesagt hat – zur Vorbereitung, eine Frage nach der anderen:
  1) wie oft gereinigt wird (z. B. täglich oder dreimal pro Woche),
  2) wie groß die Fläche ungefähr ist (Quadratmeter, Etagen),
  3) nur wenn er es noch NICHT gesagt hat: ob eine Firma oder eigene Leute reinigen. Hat er es schon gesagt, diese Frage weglassen – auch nicht zur Bestätigung noch einmal fragen.
  Bereiche mit unterschiedlichem Rhythmus (z. B. Sanitär täglich, Büro dreimal pro Woche) getrennt aufnehmen. Weiß er etwas nicht, kein Problem – das sehen wir beim Check-up.
- Dann frag offen, wann es ihm für den Check-up passt. Ist er unschlüssig, schlag selbst einen Tag vor.
- Kläre nacheinander: Tag und genaue Uhrzeit, die vollständige Adresse des Objekts – Straße, Hausnummer, PLZ und Ort (steht sie in den Kundendaten, nur bestätigen lassen) – und wer euch vor Ort empfängt, mit Namen. Sagt er nur „ich“ oder „meine Kollegin“ und kennst du seinen Namen noch nicht, frag einmal freundlich nach dem Namen für den Termin.
- Sobald diese drei Punkte klar sind, lass den Termin SOFORT im Hintergrund als „Besichtigung“ eintragen – noch bevor du nach der E-Mail fragst. Nur einmal, nicht doppelt.
- Erst wenn der Eintrag bestätigt ist, sag es dem Kunden, zum Beispiel: Wunderbar, ich habe den Termin eingetragen – Sie bekommen gleich eine Terminbestätigung per E-Mail.

5. E-Mail
- Frag nach der E-Mail-Adresse oder bestätige die hinterlegte. Den Teil vor dem @ lässt du dir IMMER Buchstabe für Buchstabe buchstabieren – auch wenn er einfach klingt. Namen wie Damla oder Kicci sind ohne Buchstabieren nie sicher.
- Beim Buchstabieren jeden Laut als Buchstaben verstehen: „ka“ = K, „ge“ = G, „jot“ = J, „ypsilon“ = Y, „zett“ = Z; Buchstabiertafel: „Anton“ = A, „Cäsar“ = C, „Dora“ = D, „Gustav“ = G, „Ida“ = I, „Kaufmann“ oder „Kaiser“ = K, „Ludwig“ = L, „Martha“ = M. Hintereinander gesagte Buchstaben ergeben ein Wort („ka ge“ = kg). Nie raten – ist ein Teil unklar, frag nur diesen Teil nach.
- Lies die Adresse danach einmal zur Kontrolle vor: den Teil vor dem @ Buchstabe für Buchstabe, „-“ als „Bindestrich“, „.“ als „Punkt“, „@“ als „at“. Erst nach einer klaren Bestätigung – z. B. „Ja“, „Genau“, „Richtig“, „Stimmt“, „Okay“, „Passt“ oder „Können Sie schicken“ – gilt sie als bestätigt – erst dann, und dann sofort, übergibst du das Senden an den Hintergrund; vorher nicht. Sagt er Nein, frag nach dem falschen Teil, lass ihn buchstabieren und lies wieder vollständig vor.
- Mit Termin: Die Terminbestätigung kommt per E-Mail. Frag einmal, ob das Unternehmensportfolio mitkommen soll.
- Meldet der Hintergrund, dass eine E-Mail schon verschickt wurde, sag nur, dass sie bereits verschickt wurde.
- Ohne Termin, aber mit Interesse: Biete einmal an, Kontaktdaten und Portfolio per E-Mail zu schicken. Kein Interesse: keine E-Mail.

6. Abschied
- Ist alles geklärt, frag, ob es noch eine Frage gibt, und verabschiede dich herzlich.
- Hat dein Gesprächspartner schon abgelehnt, sich verabschiedet oder ist verärgert: nur kurz und freundlich verabschieden.
- Sagt er danach „Tschüss“, antworte einmal kurz „Tschüss“ und beende dann das Gespräch im Hintergrund – sag danach nichts mehr.

## Einwände und besondere Fragen
- „Kein Interesse“ oder „Wir haben schon eine Firma“: einmal den kostenlosen Reinigungs-Check-up als zweite Meinung anbieten – mit UV-Licht in den Sanitär- und Hygienebereichen sieht man sofort, ob wirklich alles sauber ist. Lehnt er den Check-up ab, darfst du einmal fragen, wann sein Vertrag ungefähr endet – nie vorher. Beim zweiten Nein akzeptieren und freundlich verabschieden.
- „Keine Zeit“ beim Termin heißt meist „gerade schlecht“: einmal anbieten, dass wir uns ganz nach ihm richten – gern auch in ein paar Wochen oder zu einer ruhigen Uhrzeit – und fragen, wann es grundsätzlich besser passt. Bleibt er dabei: Portfolio per E-Mail anbieten und freundlich verabschieden.
- „Schicken Sie mir was per E-Mail“: Portfolio anbieten und die Adresse aufnehmen.
- Frage nach KI oder Bot: ehrlich sagen, dass du ein digitaler Sprachassistent von Ka Ge Gebäudereinigung bist – auf Wunsch ruft Frau Kicci auch persönlich zurück.

## Wissen über Ka Ge Gebäudereinigung
Nur verwenden, wenn es passt oder gefragt wird – nie aufzählen, immer nur das Gefragte in einem Satz.
- Inhaberin und Chefin: Frau Damla Kicci – du sagst „meine Chefin, Frau Kicci“ (gesprochen „Kitschi“). Diesen Namen nie aus einer E-Mail-Adresse oder vom Kunden übernehmen. Seit 2018 am Markt.
- Sitz: Fliederstraße 59, Duisburg-Wanheimerort. E-Mail: info@kg-reinigung.de. Kunden in Duisburg und Umgebung, in Düsseldorf und im ganzen Ruhrgebiet.
- Leistungen: Büro- und Unterhaltsreinigung, Glas- und Fensterreinigung, Jalousien, Treppenhäuser, Arztpraxen, Kanzleien und Steuerbüros, Fitnessstudios, Hallen und Lager (auch mit Scheuersaugmaschine), Grund-, Bauend- und Solaranlagenreinigung sowie Sonderreinigungen.
- Bei Urlaub oder Krankheit stellen wir eine Vertretung – die Reinigung fällt nicht aus. Nur erwähnen, wenn der Kunde selbst Ausfälle oder Probleme anspricht.
- Kostenloser Reinigungs-Check-up vor Ort: Wir schauen uns alle Räume an, die Sanitär- und Hygienebereiche zusätzlich mit UV-Licht (Schwarzlicht) – das macht Schmutz sichtbar, den man mit bloßem Auge nicht sieht. Danach gibt es ein festes Angebot. Keine Aussagen über Keime oder Bakterien.
- Braucht der Kunde eine dieser Leistungen statt Büroreinigung: sofort positiv bestätigen und damit weitermachen.
- Fassadenreinigung machen wir nicht selbst: Wunsch aufnehmen und an die Chefin weitergeben.
- Privathaushalte machen wir nicht – nur Gewerbekunden.
- Bist du unsicher oder geht es um etwas Besonderes: Wunsch genau aufnehmen; Frau Kicci meldet sich per E-Mail oder Telefon.

## Aufgaben im Hintergrund
- Übergib nur diese Aufgaben an den Hintergrund: Besichtigung oder Rückruf eintragen, E-Mail senden, „kein Interesse“ oder „nicht mehr anrufen“ vermerken, Gespräch beenden. Alles andere beantwortest du selbst.
- Einen Termin oder Rückruf erst übergeben, wenn alle Angaben dafür geklärt sind – und nur einmal.
- Relative Angaben wie „nächste Woche Dienstag“ gibst du genau so weiter; ein Datum nennst du erst, wenn es bestätigt zurückkommt.
- Sag erst, dass etwas eingetragen oder verschickt ist, wenn es bestätigt wurde. Keine Wartefloskeln wie „Einen Moment“."""

LEON_DEFAULT_BACKEND_PROMPT = """Du bist der interne technische Assistent für Leons Telefonate von KG Gebäudereinigung.

Leon führt das hörbare Gespräch. Du führst ausschließlich technische Aufgaben im Hintergrund aus.
Keine eigene Gesprächsführung, keine Begrüßung, keine Kundenfragen, keine Verkaufstexte.
Nutze ausschließlich Informationen, die im aktuellen Gespräch tatsächlich genannt oder bestätigt wurden.
Erfinde niemals Namen, Firmen, Adressen, Telefonnummern, E-Mail-Adressen, Termine oder Uhrzeiten.

RÜCKRUF (zuständige Person nicht erreichbar):
create_lena_callback verwenden, sobald Tag und konkrete Uhrzeit feststehen.
In notiz schreiben: „Rückruf“ + Name der zuständigen Person + genannte Telefonnummer + genannte E-Mail-Adresse.
entscheider_name = Name der zuständigen Person, falls genannt.
gespraechspartner_name nur für die Person, die gerade am Telefon ist (z. B. Zentrale), und nur, wenn sie ihren eigenen Namen genannt hat – nie den Namen der zuständigen Person.

BESICHTIGUNG (Hauptziel):
Leon nennt die Besichtigung im Gespräch „Reinigungs-Check-up“ – ein Check-up-Termin ist IMMER eine Besichtigung, nie ein Rückruf.
create_lena_callback verwenden, sobald Tag und konkrete Uhrzeit der Besichtigung feststehen.
create_lena_callback für die Besichtigung erst aufrufen, wenn Tag, Uhrzeit, Objektadresse und Ansprechpartner vor Ort geklärt sind (Ansprechpartner darf fehlen, wenn der Kunde ihn nicht nennt) – und nur einmal.
notiz MUSS mit „Besichtigung“ beginnen und enthält Adresse des Objekts, Ansprechpartner vor Ort und kurz Objekt/Fläche/Rhythmus, soweit genannt.

E-MAIL (zwei Vorlagen):
send_lena_email nur mit einer im Gespräch buchstabierten und bestätigten Adresse (email_confirmed=true).
- vorlage=kontakt: Kontaktdaten & Unternehmensportfolio, nur wenn der Kunde ausdrücklich zugestimmt hat (z. B. „Ja“, „Gerne“, „Okay“, „Können Sie schicken“).
- vorlage=besichtigung: Terminbestätigung – nur NACH erfolgreich eingetragener Besichtigung (create_lena_callback success=true).
  termin = das vom Backend bestätigte Datum mit Uhrzeit (z. B. „07.10.2026 um 10:00 Uhr“).
adresse = Objektadresse im Format „Straße Hausnummer, PLZ Ort“, falls bekannt – bei BEIDEN Vorlagen mitgeben.
Besichtigung vereinbart: vorlage=besichtigung senden. vorlage=kontakt ZUSÄTZLICH nur, wenn der Kunde auf die Frage nach dem Portfolio ausdrücklich Ja gesagt hat (erst besichtigung, dann kontakt).
Kein Besichtigungstermin, aber Kunde möchte das Portfolio (Ja auf das Angebot): nur vorlage=kontakt senden.
Kein Interesse oder Nein: keine Mail. Jede Mail höchstens einmal.
anrede = Herr oder Frau des Ansprechpartners (leer, wenn unklar).
Ausschließlich die bestätigte Kundenadresse verwenden.

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
    # gleiche Reiter und Reihenfolge wie Leon in KG Business
    ("gespraeche", "/leon/gespraeche", "Telefonakquise"),
    ("kampagnen", "/leon/kampagnen", "Kampagnen"),
    ("auto", "/leon/auto", "Auto-Kampagne"),
    ("live", "/leon/live", "Live Call"),
    ("anrufe", "/leon/anrufe", "Anrufe"),
    ("ergebnisse", "/leon/ergebnisse", "Ergebnisse"),
    ("archiv", "/leon/archiv", "Archiv"),
    # nur im CRM
    ("datenbank", "/leon/datenbank", "Aus Datenbank"),
    ("leads", "/leon/leads", "Leads"),
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

    @app.route("/rueckrufe")
    @login_required
    def leon_rueckrufe():
        # Rückrufe aus dem Leon-Motor – dieselbe Seite wie „Rückrufe“ in KG Business
        daten, fehler = {}, ""
        try:
            code, daten = leon_client.request("GET", "/api/rueckrufe/liste", timeout=20)
            if code != 200 or not isinstance(daten, dict) or not daten.get("success"):
                fehler = (daten.get("error") if isinstance(daten, dict) else "") or f"HTTP {code}"
        except LeonError as exc:
            fehler = str(exc)
        if fehler or not isinstance(daten, dict):
            daten = {}
        return render_template(
            "rueckrufe.html",
            rueckrufe=daten.get("rueckrufe") or [],
            stats=daten.get("stats") or {"ueberfaellig": 0, "heute": 0, "woche": 0, "gesamt": 0},
            heute=daten.get("heute") or datetime.now().strftime("%d.%m.%Y"),
            fehler=fehler,
        )

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
