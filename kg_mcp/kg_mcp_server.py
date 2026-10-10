#!/usr/bin/env python3
"""KG Daten – MCP-Connector für ChatGPT (lesen, Firmen im KG CRM pflegen, Leon-Kampagnen als Entwurf).

ChatGPT (auch im Sprachmodus) stellt hier Fragen zu KG CRM, Leon Reinigung und
KG Business. Der Dienst liest die SQLite-Datenbanken ausschließlich im
Nur-Lese-Modus (mode=ro + PRAGMA query_only + SQLite-Authorizer):
Ändern, Löschen oder Senden ist technisch unmöglich.

- Nur Python-Standardbibliothek (kein pip nötig).
- Protokoll: MCP "Streamable HTTP" (JSON-RPC per POST, Antwort als JSON).
- Erreichbar nur unter einem geheimen Pfad: https://<domain>/<KG_MCP_PFAD>/mcp
- Geheime Spalten (Passwörter, Tokens, IBAN, Steuer-ID, SV-Nummer, Zugangscodes)
  und Einstellungs-/Token-Tabellen werden nie ausgegeben.
- Schreiben nur über das CRM (Token KG_MCP_CRM_TOKEN): leon_kampagne (/internal/mcp/leon-kampagne) und
  Firmen/Kampagnen-Entwürfe (/internal/mcp/crm). Das CRM prüft Dubletten, Schutz (Kunde, Nicht anrufen …),
  protokolliert jede Änderung und legt Kampagnen nur als ENTWURF an. Gestartet wird nie. KG Business: nie.

  python3 kg_mcp_server.py [env.json]     Server starten (Standard: /opt/kg-mcp-geheim/env.json)
"""
import hmac
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
WOCHENTAGE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
ENV_DATEI = sys.argv[1] if len(sys.argv) > 1 else "/opt/kg-mcp-geheim/env.json"

STANDARD = {
    "PORT": 8810,
    "DB_CRM": "/opt/kg-crm/data/kg_portal.db",
    "DB_REINIGUNG": "/opt/leon-reinigung/data/kg_business_voice.db",
    "DB_BUSINESS": "/opt/kg-business/data/kg_business_voice.db",
    "KG_CRM_URL": "http://127.0.0.1:8803",
}

SYSTEME = {
    "crm": ("DB_CRM", "KG CRM (KG Gebäudereinigung, portal.kg-reinigung.de)"),
    "reinigung": ("DB_REINIGUNG", "Leon Reinigung – Telefon-KI des CRM (leon.kg-reinigung.de)"),
    "business": ("DB_BUSINESS", "KG Business – Energie-Vertrieb Strom/Gas (portal.kg-business.de)"),
}

GEHEIME_SPALTE = re.compile(
    r"(passw|token|secret|access_code|api_?key|payload_json|refresh|cookie|session_id|"
    r"iban|steuer_?id|sv_?nummer|credential)",
    re.I,
)
GESPERRTE_TABELLE = re.compile(r"(setting|token|secret|oauth|credential|auth)", re.I)
# Ausnahme: Leons Agenten-Einstellungen (Begrüßung, Prompts, Stimme, Modell) – enthalten keine Geheimnisse
FREIGEGEBEN = {"agent_settings"}


def _gesperrt(tabelle):
    return bool(GESPERRTE_TABELLE.search(tabelle or "")) and (tabelle or "").lower() not in FREIGEGEBEN

MAX_ZEILEN = 200
MAX_TEXT = 500
MAX_ANTWORT = 60000
ABFRAGE_SEKUNDEN = 4

PROTOKOLLE = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")

ANLEITUNG = """\
KG Daten: Zugriff auf die Firmendaten von Murat (Inhaber von KG Gebäudereinigung und KG Business, Duisburg).
Lesen ist frei. Ändern darfst du – nach Murats Ja – NUR im KG CRM / Leon Reinigung: Firmen (leads) suchen, anlegen,
importieren, ergänzen und Leon-Reinigung-Kampagnen als ENTWURF anlegen. Dafür hast du die Berechtigung.
Starten kannst du eine Kampagne nie (das macht Murat im CRM). E-Mails verschickst du nie. KG Business änderst du nie.
Antworte in der Sprache des Nutzers (meist Türkisch, manchmal Deutsch). Im Sprachmodus: kurz, Zahlen zuerst, keine Tabellen vorlesen.

Systeme (Parameter "system"):
- crm = KG CRM (Gebäudereinigung). Wichtige Tabellen: kunden (Kunden/Verträge), mitarbeiter (Arbeiter), work_logs (Arbeitsstunden:
  worker_id -> mitarbeiter.id, datum, start_time, end_time, place), leads (Firmen-Datenbank, u. a. aus dem Lead-Sammler),
  tagesliste_leads (Tagesliste; status: offen, angerufen, interessiert, besichtigung, angebot, verloren, spaeter),
  tagesliste_status_history, besichtigungen (Termine), angebote, todos (To-Do), leon_links (welche CRM-Firma an Leon übergeben wurde
  und mit welchem Ergebnis), leon_auto_einstellungen / leon_auto_runden (Leon Auto-Kampagne), whatsapp_inbox, lohn_versand, stundenzettel_*.
- reinigung = Leon Reinigung (Telefon-KI, ruft Firmen für das CRM an). Tabellen: leads (callback_at = geplanter Rückruf,
  callback_note, status, last_call_result), calls (Anrufe: status, result, summary, transcript, duration_seconds, created_at),
  campaigns, campaign_leads.
- business = KG Business (Strom/Gas-Vertrieb). Gleicher Aufbau wie reinigung (leads, calls, campaigns, campaign_leads).

Werkzeuge: Für häufige Fragen zuerst kg_heute, todo_brett, rueckrufe, leon_anrufe, firma_suchen benutzen.
todo_brett = derselbe To-Do-Kasten, den Murat im Süper Program sieht (heute / woche / monat).
Für alles andere: erst schema(system), dann sql_lesen(system, SELECT ...). Du kannst frei SELECT-Abfragen schreiben
(auch JOIN, GROUP BY, WITH).

FIRMEN FÜR KG GEBÄUDEREINIGUNG (KG CRM) – z. B. „Finde 10 Firmen in Duisburg, lege sie an, mach eine Kampagne“:
1. Recherchieren: nur echte Firmen mit Geschäftskontakt (Impressum/Website/Branchenbuch). Nichts erfinden:
   was du nicht sicher gefunden hast, leer lassen. quelle_url immer angeben. Ansprechpartner nur, wenn er auf
   einer Quelle steht – dann ansprechpartner_geprueft=true. punkte 0–100 = deine Einschätzung, Grund in notiz
   (z. B. Fläche, Mitarbeiter, Praxis/Büro). Keine Privatpersonen.
2. Liste Murat zeigen und auf sein Ja warten. Dann leads_importieren (bis 100 je Aufruf, größere Listen in Teilen;
   CSV geht als Text im Feld csv). Das CRM prüft selbst auf Dubletten (Telefon, E-Mail, Domain, Firma+Ort/Straße)
   und meldet: neu, vorhanden (übersprungen), unklar (ähnlich – Murat fragen, nie zusammenführen), fehler.
   Doppelt senden ist sicher – Vorhandenes wird nie ein zweites Mal angelegt.
3. Kampagne: leads_kampagne_hinzufuegen mit name (neuer Entwurf) oder kampagne_id (nur Entwürfe), dazu
   crm_lead_ids (z. B. „kampagnenfaehig“ aus dem Import) und/oder leon_lead_ids (Leads direkt in Leon Reinigung,
   z. B. aus sql_lesen system=reinigung). Geschützte Firmen (Kunde, Nicht anrufen, Kein Interesse, Gesperrt,
   verloren, schon im Verkauf, KG-Agent) werden nie aufgenommen und als „uebersprungen“ gemeldet.
4. Mit kampagne_pruefen Name, Status (Entwurf) und Firmen zurücklesen und Murat bestätigen. Starten tut Murat.
Werte nie selbst „Kunde“ setzen; Kunden werden nicht geändert. Änderungen stehen im Protokoll des CRM.

LEON-AUSWAHL AUS DER DATENBANK:
leon_kampagne legt eine Leon-Kampagne (Telefon-KI) als Entwurf an. Ablauf: aktion=branchen → staedte → vorschau
(Liste dem Nutzer zeigen) → erst nach ausdrücklichem Ja des Nutzers aktion=anlegen mit Namen. Gestartet wird nie –
das macht Murat selbst im CRM (Leon → Kampagnen).
Zeiten: calls.created_at und andere *_at mit CURRENT_TIMESTAMP sind UTC – für den Nutzer in Berliner Zeit umrechnen.
Geheime Felder (Passwörter, Tokens, IBAN, Steuer-ID, SV-Nummer) sind gesperrt und kommen als NULL.
Erfinde nie Zahlen oder Namen: wenn ein Werkzeug nichts liefert, sag das.
"""


# ----------------------------------------------------------------- Einstellungen

def einstellungen():
    werte = dict(STANDARD)
    try:
        with open(ENV_DATEI, encoding="utf-8") as f:
            werte.update(json.load(f))
    except FileNotFoundError:
        pass
    return werte


EINST = einstellungen()
PFAD = str(EINST.get("KG_MCP_PFAD") or "").strip("/")


# ----------------------------------------------------------------- Datenbank

_SQLITE_OK = 0
_SQLITE_DENY = 1
_SQLITE_IGNORE = 2
_ERLAUBT = {
    getattr(sqlite3, "SQLITE_SELECT", 21),
    getattr(sqlite3, "SQLITE_FUNCTION", 31),
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}
_LESEN = getattr(sqlite3, "SQLITE_READ", 20)


def _authorizer(aktion, arg1, arg2, _db, _quelle):
    if aktion in _ERLAUBT:
        return _SQLITE_OK
    if aktion == _LESEN:
        tabelle, spalte = arg1 or "", arg2 or ""
        if _gesperrt(tabelle):
            return _SQLITE_DENY
        if GEHEIME_SPALTE.search(spalte):
            return _SQLITE_IGNORE  # Spalte kommt als NULL
        return _SQLITE_OK
    return _SQLITE_DENY


class Fehler(Exception):
    pass


def verbinden(system):
    if system not in SYSTEME:
        raise Fehler(f"Unbekanntes System '{system}'. Erlaubt: crm, reinigung, business.")
    pfad = os.path.abspath(str(EINST.get(SYSTEME[system][0]) or ""))
    if not os.path.exists(pfad):
        raise Fehler(f"Datenbank für '{system}' nicht gefunden.")
    conn = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = 1")
    ende = time.monotonic() + ABFRAGE_SEKUNDEN
    conn.set_progress_handler(lambda: 1 if time.monotonic() > ende else 0, 20000)
    conn.set_authorizer(_authorizer)
    return conn


def _wert(v):
    if isinstance(v, bytes):
        return f"<{len(v)} Bytes>"
    if isinstance(v, str) and len(v) > MAX_TEXT:
        return v[:MAX_TEXT] + " …"
    return v


def abfragen(conn, sql, params=(), limit=MAX_ZEILEN):
    cur = conn.execute(sql, params)
    namen = [d[0] for d in cur.description or []]
    zeilen = cur.fetchmany(limit + 1)
    mehr = len(zeilen) > limit
    return [{n: _wert(z[i]) for i, n in enumerate(namen)} for z in zeilen[:limit]], mehr


def tabellen(conn):
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def spalten(conn, tabelle):
    # PRAGMA ist im Authorizer gesperrt – Spalten aus einer leeren Abfrage lesen.
    cur = conn.execute(f'SELECT * FROM "{tabelle}" LIMIT 0')
    return [d[0] for d in cur.description]


# ----------------------------------------------------------------- Zeit

def jetzt_berlin():
    return datetime.now(BERLIN)


def utc_text(dt_berlin):
    return dt_berlin.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def berlin_text(utc_wert):
    try:
        dt = datetime.strptime(str(utc_wert)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone(BERLIN).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return utc_wert


def rueckruf_zeit(text):
    text = str(text or "").strip().replace("T", " ")
    for laenge, fmt in ((19, "%Y-%m-%d %H:%M:%S"), (16, "%Y-%m-%d %H:%M"), (10, "%Y-%m-%d"),
                        (16, "%d.%m.%Y %H:%M"), (10, "%d.%m.%Y")):
        try:
            return datetime.strptime(text[:laenge], fmt)
        except ValueError:
            continue
    return None


# ----------------------------------------------------------------- Werkzeuge

def w_rueckrufe(system="reinigung", zeitraum="faellig"):
    if system not in ("reinigung", "business"):
        raise Fehler("Rückrufe gibt es für system=reinigung (Leon) oder system=business.")
    conn = verbinden(system)
    try:
        rows = conn.execute("""
            SELECT l.id, l.firma, l.ansprechpartner, l.entscheider_name, l.telefon, l.stadt,
                   l.callback_at, l.callback_note, l.status,
                   (SELECT c.summary FROM calls c WHERE c.lead_id = l.id AND COALESCE(c.summary, '') != ''
                    ORDER BY c.id DESC LIMIT 1) AS letzte_zusammenfassung
            FROM leads l
            WHERE COALESCE(l.callback_at, '') != '' AND COALESCE(l.do_not_call, 0) = 0
        """).fetchall()
    finally:
        conn.close()
    jetzt = jetzt_berlin().replace(tzinfo=None)
    heute = jetzt.date()
    wochenende = heute + timedelta(days=6 - heute.weekday())
    liste = []
    for r in rows:
        t = rueckruf_zeit(r["callback_at"])
        if t is None:
            lage = "spaeter"
        elif t < jetzt and t.date() < heute:
            lage = "ueberfaellig"
        elif t.date() == heute:
            lage = "heute"
        elif t.date() <= wochenende:
            lage = "woche"
        else:
            lage = "spaeter"
        liste.append({
            "firma": r["firma"], "kontakt": r["ansprechpartner"] or r["entscheider_name"],
            "telefon": r["telefon"], "ort": r["stadt"],
            "wann": t.strftime("%d.%m.%Y %H:%M") if t else r["callback_at"],
            "lage": lage, "notiz": _wert(r["callback_note"]),
            "letztes_gespraech": _wert(r["letzte_zusammenfassung"]),
            "_t": t or datetime.max,
        })
    stats = {k: sum(1 for x in liste if x["lage"] == k) for k in ("ueberfaellig", "heute", "woche", "spaeter")}
    stats["gesamt"] = len(liste)
    erlaubt = {"faellig": ("ueberfaellig", "heute"), "woche": ("ueberfaellig", "heute", "woche"),
               "alle": ("ueberfaellig", "heute", "woche", "spaeter")}.get(zeitraum, ("ueberfaellig", "heute"))
    rang = {"ueberfaellig": 0, "heute": 1, "woche": 2, "spaeter": 3}
    auswahl = sorted((x for x in liste if x["lage"] in erlaubt), key=lambda x: (rang[x["lage"]], x["_t"]))
    for x in auswahl:
        x.pop("_t", None)
    return {"system": system, "stand": jetzt.strftime("%d.%m.%Y %H:%M"), "zaehler": stats,
            "rueckrufe": auswahl[:50], "weitere": max(0, len(auswahl) - 50)}


def w_leon_anrufe(system="reinigung", tage=1, nur_erreicht=False):
    if system not in ("reinigung", "business"):
        raise Fehler("Anrufe gibt es für system=reinigung (Leon) oder system=business.")
    tage = max(1, min(31, int(tage or 1)))
    start = jetzt_berlin().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=tage - 1)
    conn = verbinden(system)
    try:
        sql = """
            SELECT c.id, c.created_at, c.status, c.result, c.duration_seconds, c.summary,
                   l.firma, l.ansprechpartner, l.telefon, cp.name AS kampagne
            FROM calls c
            LEFT JOIN leads l ON l.id = c.lead_id
            LEFT JOIN campaigns cp ON cp.id = c.campaign_id
            WHERE c.created_at >= ?
        """
        if nur_erreicht:
            sql += " AND c.status = 'Beendet' AND c.duration_seconds >= 15"
        sql += " ORDER BY c.id DESC"
        anrufe, mehr = abfragen(conn, sql, (utc_text(start),), limit=100)
        zaehler = [dict(r) for r in conn.execute("""
            SELECT COALESCE(NULLIF(result, ''), status) AS ergebnis, COUNT(*) AS anzahl
            FROM calls WHERE created_at >= ? GROUP BY 1 ORDER BY 2 DESC
        """, (utc_text(start),))]
        kampagnen = [dict(r) for r in conn.execute("""
            SELECT cp.id, cp.name, cp.status,
                   SUM(CASE WHEN cl.status = 'Wartet' THEN 1 ELSE 0 END) AS wartend,
                   COUNT(cl.id) AS firmen
            FROM campaigns cp LEFT JOIN campaign_leads cl ON cl.campaign_id = cp.id
            WHERE cp.status NOT IN ('Entwurf', 'Archiviert')
            GROUP BY cp.id ORDER BY cp.id DESC LIMIT 10
        """)]
    finally:
        conn.close()
    for a in anrufe:
        a["zeit"] = berlin_text(a.pop("created_at"))
    return {"system": system, "ab": start.strftime("%d.%m.%Y"), "anzahl": sum(z["anzahl"] for z in zaehler),
            "nach_ergebnis": zaehler, "kampagnen": kampagnen, "anrufe": anrufe, "weitere_vorhanden": mehr}


def w_firma_suchen(name):
    name = str(name or "").strip()
    if len(name) < 2:
        raise Fehler("Bitte mindestens 2 Buchstaben vom Firmennamen angeben.")
    muster = f"%{name}%"
    treffer = {}
    suchen = {
        "crm": [
            ("kunden", "SELECT id, firma, ort, ansprechpartner_name, telefon, email, vertragsstatus, haeufigkeit "
                       "FROM kunden WHERE firma LIKE ? LIMIT 10"),
            ("tagesliste", "SELECT id, firma, branche, ort, telefon, status, notiz, spaeter_datum "
                           "FROM tagesliste_leads WHERE firma LIKE ? LIMIT 10"),
            ("datenbank", "SELECT id, firma, branche_name, stadt, telefon, status FROM leads WHERE firma LIKE ? LIMIT 10"),
            ("besichtigungen", "SELECT id, firma, termin_datum, termin_uhrzeit, status, ort FROM besichtigungen "
                               "WHERE firma LIKE ? LIMIT 10"),
            ("angebote", "SELECT id, firma, ort, m2, reinigungsart, status, created_at FROM angebote "
                         "WHERE firma LIKE ? LIMIT 10"),
            ("leon", "SELECT firma, letzter_status, letztes_ergebnis, aktualisiert_am FROM leon_links "
                     "WHERE firma LIKE ? ORDER BY id DESC LIMIT 10"),
        ],
        "reinigung": [
            ("leads", "SELECT id, firma, ansprechpartner, telefon, stadt, status, last_call_result, callback_at "
                      "FROM leads WHERE firma LIKE ? LIMIT 10"),
        ],
        "business": [
            ("leads", "SELECT id, firma, ansprechpartner, telefon, stadt, status, last_call_result, callback_at "
                      "FROM leads WHERE firma LIKE ? LIMIT 10"),
        ],
    }
    for system, abfragen_liste in suchen.items():
        try:
            conn = verbinden(system)
        except Fehler as exc:
            treffer[system] = {"fehler": str(exc)}
            continue
        try:
            for bereich, sql in abfragen_liste:
                try:
                    zeilen, _ = abfragen(conn, sql, (muster,), limit=10)
                except sqlite3.Error as exc:
                    zeilen = [{"fehler": str(exc)}]
                if zeilen:
                    treffer.setdefault(system, {})[bereich] = zeilen
        finally:
            conn.close()
    return {"suche": name, "treffer": treffer or "nichts gefunden"}


def w_kg_heute():
    jetzt = jetzt_berlin()
    heute = jetzt.date().isoformat()
    in7 = (jetzt.date() + timedelta(days=7)).isoformat()
    ergebnis = {"stand": f"{WOCHENTAGE[jetzt.weekday()]} {jetzt.strftime('%d.%m.%Y %H:%M')}"}

    def abschnitt(name, fn):
        try:
            ergebnis[name] = fn()
        except Exception as exc:  # ein Abschnitt darf den Überblick nie verhindern
            ergebnis[name] = {"fehler": str(exc)}

    def rueckruf_kurz(system):
        r = w_rueckrufe(system, "faellig")
        return {"zaehler": r["zaehler"], "faellig": [
            {k: x[k] for k in ("firma", "kontakt", "telefon", "wann", "lage", "notiz")} for x in r["rueckrufe"][:10]]}

    def anrufe_kurz(system):
        a = w_leon_anrufe(system, 1)
        return {"heute_anrufe": a["anzahl"], "nach_ergebnis": a["nach_ergebnis"], "kampagnen": a["kampagnen"]}

    def crm():
        conn = verbinden("crm")
        try:
            daten = {}
            for name, sql, params in (
                ("tagesliste_nach_status", "SELECT COALESCE(status, 'offen') AS status, COUNT(*) AS anzahl "
                                           "FROM tagesliste_leads GROUP BY 1 ORDER BY 2 DESC", ()),
                ("besichtigungen_naechste_7_tage", "SELECT firma, termin_datum, termin_uhrzeit, ort, status "
                                                   "FROM besichtigungen WHERE termin_datum BETWEEN ? AND ? "
                                                   "ORDER BY termin_datum, termin_uhrzeit", (heute, in7)),
                ("todos_offen_faellig", "SELECT task, deadline, priority FROM todos "
                                        "WHERE COALESCE(done, 0) = 0 AND COALESCE(deadline, '') != '' "
                                        "AND substr(deadline, 1, 10) <= ? ORDER BY deadline", (heute,)),
                ("arbeitsstunden_heute", "SELECT m.vorname || ' ' || m.nachname AS mitarbeiter, w.start_time, "
                                         "w.end_time, w.place FROM work_logs w LEFT JOIN mitarbeiter m "
                                         "ON m.id = w.worker_id WHERE w.datum = ? ORDER BY w.start_time", (heute,)),
            ):
                try:
                    daten[name], _ = abfragen(conn, sql, params, limit=30)
                except sqlite3.Error as exc:
                    daten[name] = {"fehler": str(exc)}
            return daten
        finally:
            conn.close()

    abschnitt("crm", crm)
    abschnitt("leon_reinigung_rueckrufe", lambda: rueckruf_kurz("reinigung"))
    abschnitt("leon_reinigung_anrufe", lambda: anrufe_kurz("reinigung"))
    abschnitt("business_rueckrufe", lambda: rueckruf_kurz("business"))
    abschnitt("business_anrufe", lambda: anrufe_kurz("business"))
    return ergebnis


WT_KURZ = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
WICHTIG = ("termin", "heiss", "rueckruf")


def w_todo_brett(zeitraum="heute"):
    """Gleicher Inhalt wie der To-Do-Kasten im Süper Program (unten, ganze Breite):
    Rückrufe, Besichtigungen, Kampagnen, Zahlungen, Stunden der Mitarbeiter, Aufgaben – CRM und KG Business."""
    zeitraum = zeitraum if zeitraum in ("heute", "woche", "monat") else "heute"
    heute = jetzt_berlin().date()
    bis = heute + timedelta(days={"heute": 0, "woche": 6, "monat": 30}[zeitraum])
    stunden_von = {"heute": heute, "woche": heute - timedelta(days=heute.weekday()),
                   "monat": heute.replace(day=1)}[zeitraum]
    spalten = {k: [] for k in ("rueckrufe", "besichtigungen", "kampagnen", "zahlungen", "stunden", "aufgaben")}
    fehler = []

    def tag(d):
        if d == heute:
            return "heute"
        if d == heute + timedelta(days=1):
            return "morgen"
        return f"{WT_KURZ[d.weekday()]} {d:%d.%m.}"

    def datum(wert):
        t = rueckruf_zeit(wert)
        return t.date() if t else None

    def add(spalte, text, d=None, uhr="", quelle="CRM", hot=None, wann=None):
        if hot is None:
            hot = bool(d and d < heute)
        if wann is None:
            wann = "überfällig" if hot and d else ((tag(d) if d else "") + (" " + uhr if uhr else "")).strip()
        spalten[spalte].append({"text": text, "wann": wann, "quelle": quelle, "dringend": hot,
                                "_s": f"{0 if hot else 1}{d.isoformat() if d else '9'}{uhr}"})

    def berichte(conn, quelle, anzahl):
        try:
            zeilen = conn.execute("SELECT id, daten_json, erledigt_json FROM kampagnen_berichte ORDER BY id DESC LIMIT ?",
                                  (anzahl,)).fetchall()
        except sqlite3.Error as exc:  # Berichte fehlen → die anderen Spalten trotzdem zeigen
            fehler.append(f"{quelle} Kampagnen-Berichte: {exc}")
            return
        for r in zeilen:
            try:
                b = json.loads(r["daten_json"] or "{}")
                erledigt = set(json.loads(r["erledigt_json"] or "[]"))
            except (TypeError, ValueError):
                continue
            offen = [f for f in b.get("firmen", []) if f.get("kategorie") in WICHTIG and f.get("lead_id") not in erledigt]
            z = b.get("zahlen") or {}
            add("kampagnen", f"{b.get('kampagne') or 'Kampagne'}: {z.get('angerufen', 0)} angerufen, "
                             f"{z.get('termin', 0)} Termin, {z.get('heiss', 0)} interessiert, {len(offen)} offen",
                datum(b.get("bis") or b.get("von")), quelle=quelle, hot=False)
            for f in offen[:8]:
                art = {"termin": "Termin", "heiss": "interessiert", "rueckruf": "Rückruf"}.get(f.get("kategorie"), "")
                add("kampagnen", f"{f.get('firma') or '—'} ({art})", quelle=quelle, hot=False,
                    wann=str(f.get("rueckruf_am") or "offen")[:16])

    bis_text = bis.isoformat()
    # ---------------- CRM
    try:
        conn = verbinden("crm")
        try:
            for r in conn.execute("SELECT * FROM todos WHERE COALESCE(done, 0) = 0 AND COALESCE(deadline, '') != '' "
                                  "AND substr(deadline, 1, 10) <= ? ORDER BY deadline", (bis_text,)).fetchall():
                r = dict(r)
                if str(r.get("status") or "").lower() in {"done", "erledigt"}:
                    continue
                d = datum(r.get("deadline"))
                text = r.get("task") or "—"
                if r.get("amount_text"):
                    text += f" ({r['amount_text']})"
                spalte = "zahlungen" if r.get("amount") else (
                    "kampagnen" if r.get("source") == "kampagnen-bericht" else "aufgaben")
                add(spalte, text, d)
            for r in conn.execute("SELECT firma, ansprechpartner, rueckruf_am FROM leon_anrufe WHERE COALESCE(erledigt, 0) = 0 "
                                  "AND COALESCE(rueckruf_am, '') != '' AND substr(rueckruf_am, 1, 10) <= ?", (bis_text,)).fetchall():
                add("rueckrufe", f"{r['firma']} – {r['ansprechpartner'] or ''}".strip(" –"), datum(r["rueckruf_am"]),
                    str(r["rueckruf_am"])[11:16])
            for r in conn.execute("SELECT firma, ort, termin_datum, termin_uhrzeit FROM besichtigungen "
                                  "WHERE termin_datum BETWEEN ? AND ? ORDER BY termin_datum, termin_uhrzeit",
                                  (heute.isoformat(), bis_text)).fetchall():
                add("besichtigungen", f"{r['firma']} – {r['ort'] or ''}".strip(" –"), datum(r["termin_datum"]),
                    r["termin_uhrzeit"] or "", hot=False)
            import calendar
            for r in conn.execute("SELECT kreditname, monatliche_rate, rest_raten, beginn FROM ratenzahlungen").fetchall():
                try:
                    if int(r["rest_raten"] or 0) <= 0:
                        continue
                    tag_im_monat = int(str(r["beginn"] or "")[8:10])
                except ValueError:
                    continue
                jahr, monat = heute.year, heute.month
                for _ in range(2):
                    faellig = date(jahr, monat, min(tag_im_monat, calendar.monthrange(jahr, monat)[1]))
                    if faellig >= heute:
                        break
                    jahr, monat = (jahr + 1, 1) if monat == 12 else (jahr, monat + 1)
                if faellig <= bis:
                    add("zahlungen", f"Rate {r['kreditname'] or ''}: {r['monatliche_rate']} € (noch {r['rest_raten']} Raten)",
                        faellig, hot=False)
            # Stunden der Mitarbeiter
            namen = {r["id"]: f"{r['vorname'] or ''} {r['nachname'] or ''}".strip()
                     for r in conn.execute("SELECT id, vorname, nachname FROM mitarbeiter").fetchall()}
            je = {}
            for r in conn.execute("SELECT worker_id, start_time, end_time FROM work_logs WHERE datum BETWEEN ? AND ?",
                                  (stunden_von.isoformat(), heute.isoformat())).fetchall():
                try:
                    h1, m1 = (int(x) for x in str(r["start_time"])[:5].split(":"))
                    h2, m2 = (int(x) for x in str(r["end_time"])[:5].split(":"))
                    minuten = (h2 * 60 + m2) - (h1 * 60 + m1)
                except ValueError:
                    minuten = 0
                x = je.setdefault(r["worker_id"], [0.0, 0])
                x[0] += (minuten + 1440 if minuten < 0 else minuten) / 60
                x[1] += 1
            add("stunden", f"Gesamt {sum(v[0] for v in je.values()):.1f} Stunden, {len(je)} Mitarbeiter", hot=False,
                wann="heute" if zeitraum == "heute" else f"seit {stunden_von:%d.%m.}")
            try:
                for r in conn.execute("SELECT worker_id, monat FROM stundenzettel_monate WHERE status = 'leon_fertig'").fetchall():
                    add("stunden", f"Leon-Kontrolle fertig, bitte bestätigen: {namen.get(r['worker_id'], '')} ({r['monat']})".replace(":  (", ": ("),
                        hot=True, wann="prüfen")
            except sqlite3.Error:
                pass  # Tabelle gibt es erst nach dem ersten Öffnen der Stundenzettel-Automatik
            if zeitraum != "heute":
                frueher = {r[0] for r in conn.execute(
                    "SELECT DISTINCT worker_id FROM work_logs WHERE datum BETWEEN ? AND ?",
                    ((stunden_von - timedelta(days=30)).isoformat(), (stunden_von - timedelta(days=1)).isoformat())).fetchall()}
                for wid in sorted(frueher - set(je), key=lambda w: namen.get(w, "")):
                    add("stunden", f"{namen.get(wid, '#' + str(wid))}: kein Eintrag", hot=True, wann="fehlt")
            for wid, (h, tage) in sorted(je.items(), key=lambda kv: -kv[1][0]):
                add("stunden", f"{namen.get(wid, '#' + str(wid))}: {h:.1f} Stunden", hot=False,
                    wann=f"{tage} Tag{'e' if tage != 1 else ''}")
            berichte(conn, "CRM", 2 if zeitraum == "heute" else 4)
        finally:
            conn.close()
    except (Fehler, sqlite3.Error) as exc:
        fehler.append(f"CRM: {exc}")

    # ---------------- KG Business
    try:
        conn = verbinden("business")
        try:
            for r in conn.execute("SELECT title, due_date, due_time, auto_type FROM todo_tasks "
                                  "WHERE deleted = 0 AND status != 'erledigt' AND due_date != '' AND substr(due_date, 1, 10) <= ?",
                                  (bis_text,)).fetchall():
                add("kampagnen" if r["auto_type"] == "kampagnenbericht" else "aufgaben", r["title"] or "—",
                    datum(r["due_date"]), r["due_time"] or "", "Business")
            berichte(conn, "Business", 2 if zeitraum == "heute" else 4)
        finally:
            conn.close()
        for x in w_rueckrufe("business", "alle")["rueckrufe"]:
            t = rueckruf_zeit(x.get("wann"))
            d = t.date() if t else None
            if x.get("lage") != "ueberfaellig" and not (d and d <= bis):
                continue
            add("rueckrufe", " – ".join(v for v in (x.get("firma"), x.get("kontakt")) if v) or "—", d,
                t.strftime("%H:%M") if t and t.strftime("%H:%M") != "00:00" else "", "Business",
                hot=x.get("lage") == "ueberfaellig")
    except (Fehler, sqlite3.Error) as exc:
        fehler.append(f"Business: {exc}")

    for k in ("rueckrufe", "besichtigungen", "zahlungen", "aufgaben"):
        spalten[k].sort(key=lambda i: i["_s"])
    for liste in spalten.values():
        for i in liste:
            i.pop("_s", None)
    return {"zeitraum": {"heute": "heute", "woche": "nächste 7 Tage", "monat": "nächste 31 Tage"}[zeitraum],
            "stand": jetzt_berlin().strftime("%d.%m.%Y %H:%M"),
            "anzahl": {k: len(v) for k, v in spalten.items()},
            "ueberfaellig": sum(1 for v in spalten.values() for i in v if i["wann"] == "überfällig"),
            "spalten": spalten, "fehler": fehler}


def w_schema(system):
    conn = verbinden(system)
    try:
        aus = []
        for t in tabellen(conn):
            if _gesperrt(t):
                continue
            try:
                anzahl = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                cols = [c for c in spalten(conn, t) if not GEHEIME_SPALTE.search(c)]
            except sqlite3.Error:
                continue
            aus.append({"tabelle": t, "zeilen": anzahl, "spalten": cols})
    finally:
        conn.close()
    return {"system": system, "beschreibung": SYSTEME[system][1], "tabellen": aus}


def w_sql_lesen(system, sql):
    sql = str(sql or "").strip().rstrip(";").strip()
    if not re.match(r"(?is)^\s*(select|with)\b", sql):
        raise Fehler("Nur SELECT- oder WITH-Abfragen sind erlaubt.")
    conn = verbinden(system)
    try:
        try:
            zeilen, mehr = abfragen(conn, sql)
        except sqlite3.OperationalError as exc:
            text = str(exc)
            if "interrupted" in text:
                raise Fehler(f"Abfrage dauerte länger als {ABFRAGE_SEKUNDEN} Sekunden – bitte enger fassen.")
            if "not authorized" in text:
                raise Fehler("Diese Tabelle/Aktion ist gesperrt (Einstellungen/Tokens oder Schreibzugriff).")
            raise Fehler(f"SQL-Fehler: {text}")
        except sqlite3.Error as exc:
            raise Fehler(f"SQL-Fehler: {exc}")
    finally:
        conn.close()
    return {"system": system, "zeilen": zeilen, "anzahl": len(zeilen), "abgeschnitten": mehr}


def w_leon_einstellungen(system="reinigung"):
    """Alle Agenten-Profile des Leon-Motors mit vollständigen Texten (nicht gekürzt)."""
    conn = verbinden(system)
    try:
        spalten_da = set(spalten(conn, "agent_settings"))
        felder = [f for f in ("id", "agent_name", "model", "voice", "speed", "turn_detection", "vad_eagerness",
                              "delegated_model", "reasoning_effort", "opening", "voice_prompt", "backend_prompt")
                  if f in spalten_da]
        profile = [dict(r) for r in conn.execute(f"SELECT {', '.join(felder)} FROM agent_settings ORDER BY id")]
    finally:
        conn.close()
    return {"system": system, "profile": profile,
            "hinweis": "Profil 1 = Verkaufs-Leon (Kundenanrufe). Weitere Profile z. B. KG-Agent (Stundenzettel-Anruf)."}


def w_leon_kampagne(a):
    """Leon-Kampagne über das CRM: Branchen/Städte/Vorschlag ansehen oder als Entwurf anlegen."""
    token = str(EINST.get("KG_MCP_CRM_TOKEN") or "").strip()
    if not token:
        raise Fehler("Kampagnen-Anlage ist noch nicht freigeschaltet (kg_mcp/kampagne_freischalten.sh).")
    aktion = str(a.get("aktion") or "").strip()
    if aktion not in ("branchen", "staedte", "vorschau", "anlegen"):
        raise Fehler("aktion: branchen, staedte, vorschau oder anlegen")
    body = {"aktion": aktion, "branchen": a.get("branchen") or [], "stadt": a.get("stadt") or "gemischt",
            "anzahl": a.get("anzahl") or 10, "name": a.get("name") or ""}
    url = str(EINST.get("KG_CRM_URL") or STANDARD["KG_CRM_URL"]).rstrip("/") + "/internal/mcp/leon-kampagne"
    anfrage = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-KG-MCP-Token": token})
    try:
        with urllib.request.urlopen(anfrage, timeout=120) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            daten = json.loads(exc.read().decode() or "{}")
        except ValueError:
            daten = {}
        raise Fehler(daten.get("error") or f"CRM antwortet mit HTTP {exc.code}")
    except (urllib.error.URLError, OSError) as exc:
        raise Fehler(f"CRM nicht erreichbar: {exc}")


def _crm(body, zeit=120):
    """Aufruf der CRM-Route /internal/mcp/crm (nur KG CRM / Leon Reinigung)."""
    token = str(EINST.get("KG_MCP_CRM_TOKEN") or "").strip()
    if not token:
        raise Fehler("Schreiben ins CRM ist noch nicht freigeschaltet (kg_mcp/kampagne_freischalten.sh).")
    url = str(EINST.get("KG_CRM_URL") or STANDARD["KG_CRM_URL"]).rstrip("/") + "/internal/mcp/crm"
    anfrage = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "X-KG-MCP-Token": token})
    try:
        with urllib.request.urlopen(anfrage, timeout=zeit) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            daten = json.loads(exc.read().decode() or "{}")
        except ValueError:
            daten = {}
        details = {k: v for k, v in daten.items() if k not in ("success", "error")}
        raise Fehler((daten.get("error") or f"CRM antwortet mit HTTP {exc.code}")
                     + (" " + json.dumps(details, ensure_ascii=False) if details else ""))
    except (urllib.error.URLError, OSError) as exc:
        raise Fehler(f"CRM nicht erreichbar: {exc}")


LEAD_FELDER = {
    "firma": {"type": "string", "description": "Firmenname (Pflicht)"},
    "telefon": {"type": "string", "description": "Geschäftliche Telefonnummer – ohne Telefon kein Leon-Anruf"},
    "email": {"type": "string"},
    "website": {"type": "string"},
    "strasse": {"type": "string", "description": "Straße und Hausnummer"},
    "plz": {"type": "string"},
    "stadt": {"type": "string"},
    "branche_id": {"type": "string", "enum": ["1", "2", "3", "4", "5", "6", "7", "8", "9", "11", "12", "13"],
                   "description": "Kasten: 1 Büro/Kanzlei/Beratung, 2 Medizin/Gesundheit, 3 Pflege/Soziales, 4 Bildung/Betreuung, "
                                  "5 Einzelhandel/Lebensmittel, 6 Fitness/Sport/Freizeit, 7 Industrie/Produktion, "
                                  "8 Lager/Logistik/Großhandel, 9 Immobilien/Hausverwaltung, 11 Handwerk/Bau/Kfz, "
                                  "12 Friseur/Kosmetik/Sonstige, 13 Gastronomie/Hotel"},
    "branche": {"type": "string", "description": "Branche als Text (z. B. Steuerberater) – wenn branche_id fehlt"},
    "ansprechpartner": {"type": "string", "description": "Nur wenn auf einer Quelle bestätigt"},
    "ansprechpartner_geprueft": {"type": "boolean", "description": "true nur wenn der Ansprechpartner belegt ist"},
    "quelle_url": {"type": "string", "description": "Wo die Daten stehen (Impressum, Branchenbuch …)"},
    "punkte": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Potenzial 0–100 (deine Einschätzung)"},
    "notiz": {"type": "string", "description": "Warum die Firma passt (Fläche, Mitarbeiter, Art des Objekts …)"},
}
LEAD_SCHEMA = {"type": "object", "properties": LEAD_FELDER, "required": ["firma"], "additionalProperties": False}
SCHREIBEN = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False}


SYSTEM_PARAM = {"type": "string", "enum": ["crm", "reinigung", "business"],
                "description": "crm = KG CRM (Gebäudereinigung), reinigung = Leon Reinigung (Telefon-KI), "
                               "business = KG Business (Strom/Gas)"}
NUR_LESEN = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

WERKZEUGE = {
    "kg_heute": {
        "fn": lambda a: w_kg_heute(),
        "title": "Heute im Überblick",
        "description": "Tagesüberblick für heute: fällige/überfällige Rückrufe (Leon Reinigung und Business), "
                       "Leon-Anrufe heute mit Ergebnissen, laufende Kampagnen, Tagesliste nach Status, "
                       "Besichtigungen der nächsten 7 Tage, fällige To-Dos, Arbeitsstunden heute. "
                       "Für Fragen wie 'was steht heute an', 'wie läuft der Tag', 'bugün neler var'.",
        "schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    "todo_brett": {
        "fn": lambda a: w_todo_brett(a.get("zeitraum", "heute")),
        "title": "To-Do-Brett (Süper Program)",
        "description": "Genau der To-Do-Kasten aus Murats Süper Program: alles Offene aus KG CRM und KG Business in 6 Spalten – "
                       "Rückrufe, Besichtigungen, Kampagnen (Ergebnisse + offene Firmen), Zahlungen (Zahlungs-To-Dos + Kreditraten), "
                       "Stunden der Mitarbeiter (Summe, je Mitarbeiter, wer nichts eingetragen hat, Leon-Kontrolle), Aufgaben. "
                       "Überfälliges ist markiert (dringend). Für Fragen wie 'To-Do'da ne var', 'was steht an', "
                       "'bu hafta ne var', 'wer hat keine Stunden eingetragen', 'welche Zahlungen sind fällig'.",
        "schema": {"type": "object", "properties": {
            "zeitraum": {"type": "string", "enum": ["heute", "woche", "monat"],
                         "description": "heute (Standard, inkl. Überfälliges), woche = nächste 7 Tage, monat = nächste 31 Tage"},
        }, "additionalProperties": False},
    },
    "rueckrufe": {
        "fn": lambda a: w_rueckrufe(a.get("system", "reinigung"), a.get("zeitraum", "faellig")),
        "title": "Rückrufe",
        "description": "Geplante Rückrufe (von Leon oder von Hand) mit Firma, Kontakt, Telefon, Zeitpunkt, Notiz "
                       "und letzter Gesprächszusammenfassung. Zähler: überfällig, heute, Woche, später.",
        "schema": {"type": "object", "properties": {
            "system": {"type": "string", "enum": ["reinigung", "business"],
                       "description": "reinigung = Leon Reinigung/CRM (Standard), business = KG Business"},
            "zeitraum": {"type": "string", "enum": ["faellig", "woche", "alle"],
                         "description": "faellig = überfällig + heute (Standard), woche = bis Sonntag, alle"},
        }, "additionalProperties": False},
    },
    "leon_anrufe": {
        "fn": lambda a: w_leon_anrufe(a.get("system", "reinigung"), a.get("tage", 1), bool(a.get("nur_erreicht"))),
        "title": "Leon-Anrufe",
        "description": "Anrufe der Telefon-KI (Leon) der letzten Tage: Zeit (Berlin), Firma, Status, Ergebnis, Dauer, "
                       "Zusammenfassung; dazu Zähler nach Ergebnis und laufende Kampagnen mit wartenden Firmen.",
        "schema": {"type": "object", "properties": {
            "system": {"type": "string", "enum": ["reinigung", "business"],
                       "description": "reinigung = Leon Reinigung (Standard), business = KG Business"},
            "tage": {"type": "integer", "minimum": 1, "maximum": 31, "description": "1 = nur heute (Standard)"},
            "nur_erreicht": {"type": "boolean", "description": "nur Gespräche, bei denen jemand erreicht wurde"},
        }, "additionalProperties": False},
    },
    "firma_suchen": {
        "fn": lambda a: w_firma_suchen(a.get("name")),
        "title": "Firma suchen",
        "description": "Sucht eine Firma (Teil des Namens) überall: CRM-Kunden, Tagesliste, Firmen-Datenbank, "
                       "Besichtigungen, Angebote, Leon-Ergebnisse, Leon Reinigung und KG Business.",
        "schema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Firmenname oder Teil davon"},
        }, "required": ["name"], "additionalProperties": False},
    },
    "leon_einstellungen": {
        "fn": lambda a: w_leon_einstellungen(a.get("system", "reinigung")),
        "title": "Leon-Einstellungen",
        "description": "Leons Agenten-Profile vollständig: Begrüßung (opening), Gesprächs-Prompt (voice_prompt), "
                       "Hintergrund-Prompt (backend_prompt), Stimme, Modell, Tempo, Pausen-Erkennung.",
        "schema": {"type": "object", "properties": {
            "system": {"type": "string", "enum": ["reinigung", "business"],
                       "description": "reinigung = Leon Reinigung/CRM (Standard), business = KG Business"},
        }, "additionalProperties": False},
    },
    "leon_kampagne": {
        "fn": w_leon_kampagne,
        "title": "Leon-Kampagne anlegen (Entwurf)",
        "description": "Legt eine Leon-Kampagne (Telefon-KI, Verkauf Gebäudereinigung) mit den besten Firmen aus der "
                       "CRM-Datenbank an – nur als ENTWURF, gestartet wird nie. Schritte: aktion=branchen (alle Branchen "
                       "mit Anzahl), aktion=staedte (Städte für die Branchen), aktion=vorschau (beste Firmen, dem Nutzer "
                       "zeigen), aktion=anlegen (erst nach ausdrücklichem Ja des Nutzers, mit name). Bewertung ohne KI: "
                       "Lead-Sammler-Punkte, Nähe zu Duisburg, Website, E-Mail. Ausgeschlossen: Kunden, verloren, "
                       "Besichtigung/Angebot, Nicht anrufen/Kein Interesse, in den letzten 21 Tagen an Leon gegeben.",
        "schema": {"type": "object", "properties": {
            "aktion": {"type": "string", "enum": ["branchen", "staedte", "vorschau", "anlegen"]},
            "branchen": {"type": "array", "items": {"type": "string"}, "maxItems": 10,
                         "description": "Branchen genau wie bei aktion=branchen (für staedte, vorschau, anlegen)"},
            "stadt": {"type": "string", "description": "Stadt genau wie bei aktion=staedte, oder 'gemischt' = 30 km um Duisburg (Standard)"},
            "anzahl": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Wie viele Firmen (Standard 10)"},
            "name": {"type": "string", "description": "Name der neuen Kampagne (nur bei anlegen)"},
        }, "required": ["aktion"], "additionalProperties": False},
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    "leads_suchen": {
        "fn": lambda a: _crm(dict(a, aktion="leads_suchen")),
        "title": "Firmen im KG CRM suchen",
        "description": "Sucht Firmen (leads) im KG CRM nach Name, E-Mail, Webseite oder Telefon (Teil reicht), "
                       "optional Stadt, Status, Branche, Quelle (z. B. ChatGPT) oder id. Zeigt je Firma auch den Schutz "
                       "(Kunde, Nicht anrufen, Kein Interesse, schon im Verkauf).",
        "schema": {"type": "object", "properties": {
            "suche": {"type": "string"}, "id": {"type": "integer"}, "stadt": {"type": "string"},
            "status": {"type": "string"}, "branche_id": LEAD_FELDER["branche_id"], "branche": {"type": "string"},
            "quelle": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        }, "additionalProperties": False},
    },
    "lead_erstellen": {
        "fn": lambda a: _crm({"aktion": "lead_erstellen", "lead": a.get("lead") or {}}),
        "title": "Eine Firma im KG CRM anlegen",
        "description": "Legt EINE Firma im KG CRM an (Quelle „ChatGPT“, Status Neu) – nach Murats Ja. Prüft vorher auf "
                       "Dubletten; Vorhandenes wird nicht doppelt angelegt. Nichts erfinden.",
        "schema": {"type": "object", "properties": {"lead": LEAD_SCHEMA}, "required": ["lead"], "additionalProperties": False},
        "annotations": SCHREIBEN,
    },
    "leads_importieren": {
        "fn": lambda a: _crm({"aktion": "leads_importieren", "leads": a.get("leads"), "csv": a.get("csv")}, zeit=300),
        "title": "Firmen ins KG CRM importieren",
        "description": "Mehrere Firmen (bis 100 je Aufruf) als JSON-Liste oder CSV-Text ins KG CRM – nach Murats Ja. "
                       "Dubletten (Telefon, E-Mail, Domain, Firma+Ort/Straße) werden übersprungen, Ähnliche als „unklar“ "
                       "gemeldet. Antwort: neu, vorhanden, unklar, fehler, kampagnenfaehig (IDs mit Telefon).",
        "schema": {"type": "object", "properties": {
            "leads": {"type": "array", "items": LEAD_SCHEMA, "maxItems": 100},
            "csv": {"type": "string", "description": "Alternativ CSV mit Kopfzeile (Firma, Telefon, E-Mail, Website, Straße, PLZ, Ort, Branche, Ansprechpartner, Quelle, Punkte, Notiz)"},
        }, "additionalProperties": False},
        "annotations": SCHREIBEN,
    },
    "lead_aktualisieren": {
        "fn": lambda a: _crm({"aktion": "lead_aktualisieren", "id": a.get("id"), "felder": a.get("felder") or {}}),
        "title": "Firma im KG CRM ändern",
        "description": "Ändert EINE Firma im KG CRM – nach Murats Ja: Kontaktdaten, Adresse, Branche, Status "
                       "(Neu, Kontaktiert, Interessiert, Kein Interesse, Nicht anrufen, Verloren), Notiz anhängen. "
                       "Kunden werden nicht geändert. Vorher/Nachher wird protokolliert.",
        "schema": {"type": "object", "properties": {
            "id": {"type": "integer", "description": "CRM-Lead-ID"},
            "felder": {"type": "object", "properties": {
                "firma": {"type": "string"}, "telefon": {"type": "string"}, "email": {"type": "string"},
                "website": {"type": "string"}, "strasse": {"type": "string"}, "plz": {"type": "string"},
                "stadt": {"type": "string"}, "branche_id": LEAD_FELDER["branche_id"], "branche": {"type": "string"},
                "ansprechpartner": {"type": "string"}, "ansprechpartner_geprueft": {"type": "boolean"},
                "quelle_url": {"type": "string"},
                "status": {"type": "string", "enum": ["Neu", "Kontaktiert", "Interessiert", "Kein Interesse", "Nicht anrufen", "Verloren"]},
                "notiz_anhaengen": {"type": "string"},
            }, "additionalProperties": False},
        }, "required": ["id", "felder"], "additionalProperties": False},
        "annotations": SCHREIBEN,
    },
    "leads_kampagne_hinzufuegen": {
        "fn": lambda a: _crm(dict(a, aktion="leads_kampagne"), zeit=300),
        "title": "Leon-Reinigung-Kampagne als Entwurf (mit gewählten Firmen)",
        "description": "Legt eine Leon-Reinigung-Kampagne als ENTWURF an (name) oder ergänzt einen Entwurf (kampagne_id) "
                       "mit gewählten Firmen: crm_lead_ids (KG CRM) und/oder leon_lead_ids (Leon Reinigung). Nach Murats Ja. "
                       "Startet nie. Geschützte Firmen werden übersprungen. Antwort enthält die zurückgelesene Kampagne.",
        "schema": {"type": "object", "properties": {
            "name": {"type": "string", "description": "Name der neuen Kampagne"},
            "kampagne_id": {"type": "integer", "description": "Bestehender Entwurf (statt name)"},
            "crm_lead_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 200},
            "leon_lead_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 200},
        }, "additionalProperties": False},
        "annotations": SCHREIBEN,
    },
    "kampagne_pruefen": {
        "fn": lambda a: _crm(dict(a, aktion="kampagne_pruefen")),
        "title": "Leon-Reinigung-Kampagne zurücklesen",
        "description": "Liest eine Leon-Reinigung-Kampagne (kampagne_id oder genauer name): Name, Status, Agent, Firmen.",
        "schema": {"type": "object", "properties": {
            "kampagne_id": {"type": "integer"}, "name": {"type": "string"},
        }, "additionalProperties": False},
    },
    "schema": {
        "fn": lambda a: w_schema(a.get("system")),
        "title": "Tabellen anzeigen",
        "description": "Listet alle Tabellen eines Systems mit Spalten und Zeilenzahl – vor sql_lesen benutzen.",
        "schema": {"type": "object", "properties": {"system": SYSTEM_PARAM},
                   "required": ["system"], "additionalProperties": False},
    },
    "sql_lesen": {
        "fn": lambda a: w_sql_lesen(a.get("system"), a.get("sql")),
        "title": "Daten abfragen (SQL, nur lesen)",
        "description": "Freie SQLite-Abfrage (nur SELECT/WITH, max. 200 Zeilen, 4 Sekunden). Für alles, was die "
                       "anderen Werkzeuge nicht abdecken: Stunden pro Mitarbeiter, Kunden, Verträge, Statistiken usw.",
        "schema": {"type": "object", "properties": {
            "system": SYSTEM_PARAM,
            "sql": {"type": "string", "description": "Eine SELECT- oder WITH-Abfrage (SQLite)"},
        }, "required": ["system", "sql"], "additionalProperties": False},
    },
}


def werkzeug_liste():
    return [{"name": name, "title": w["title"], "description": w["description"],
             "inputSchema": w["schema"], "annotations": dict(w.get("annotations") or NUR_LESEN, title=w["title"])}
            for name, w in WERKZEUGE.items()]


def werkzeug_aufrufen(name, argumente):
    w = WERKZEUGE.get(name)
    if not w:
        return {"content": [{"type": "text", "text": f"Unbekanntes Werkzeug: {name}"}], "isError": True}
    try:
        daten = w["fn"](argumente or {})
        text = json.dumps(daten, ensure_ascii=False, default=str)
        if len(text) > MAX_ANTWORT:
            text = text[:MAX_ANTWORT] + ' … (gekürzt – bitte Abfrage enger fassen)'
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except Fehler as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    except Exception as exc:  # nie abstürzen
        return {"content": [{"type": "text", "text": f"Interner Fehler: {type(exc).__name__}: {exc}"}], "isError": True}


# ----------------------------------------------------------------- JSON-RPC / MCP

def antwort(rid, ergebnis=None, fehler=None):
    nachricht = {"jsonrpc": "2.0", "id": rid}
    if fehler is not None:
        nachricht["error"] = fehler
    else:
        nachricht["result"] = ergebnis
    return nachricht


def bearbeiten(nachricht):
    if not isinstance(nachricht, dict) or nachricht.get("jsonrpc") != "2.0":
        return antwort(None, fehler={"code": -32600, "message": "Invalid Request"})
    methode = nachricht.get("method")
    rid = nachricht.get("id")
    params = nachricht.get("params") or {}
    if methode is None:  # Antwort des Clients – nichts zu tun
        return None
    if "id" not in nachricht:  # Benachrichtigung (z. B. notifications/initialized)
        return None
    if methode == "initialize":
        gewuenscht = str(params.get("protocolVersion") or "")
        version = gewuenscht if gewuenscht in PROTOKOLLE else PROTOKOLLE[-1]
        return antwort(rid, {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "kg-daten", "title": "KG Daten", "version": "1.3.0"},
            "instructions": ANLEITUNG,
        })
    if methode == "ping":
        return antwort(rid, {})
    if methode == "tools/list":
        return antwort(rid, {"tools": werkzeug_liste()})
    if methode == "tools/call":
        return antwort(rid, werkzeug_aufrufen(params.get("name"), params.get("arguments")))
    if methode == "resources/list":
        return antwort(rid, {"resources": []})
    if methode == "resources/templates/list":
        return antwort(rid, {"resourceTemplates": []})
    if methode == "prompts/list":
        return antwort(rid, {"prompts": []})
    return antwort(rid, fehler={"code": -32601, "message": f"Method not found: {methode}"})


class Handler(BaseHTTPRequestHandler):
    server_version = "KG-Daten/1.0"
    sys_version = ""

    def log_message(self, fmt, *args):
        # Pfad (enthält das Geheimnis) nie ins Log schreiben
        sys.stderr.write("%s %s\n" % (self.command, args[1] if len(args) > 1 else ""))

    def _pfad_ok(self):
        teile = self.path.split("?", 1)[0].strip("/").split("/")
        return (PFAD and len(teile) == 2 and teile[1] == "mcp"
                and hmac.compare_digest(teile[0].encode(), PFAD.encode()))

    def _senden(self, code, daten=None, typ="application/json"):
        body = b"" if daten is None else (daten if isinstance(daten, bytes) else
                                          json.dumps(daten, ensure_ascii=False).encode("utf-8"))
        self.send_response(code)
        if body:
            self.send_header("Content-Type", typ + ("; charset=utf-8" if typ.startswith(("application/json", "text/")) else ""))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?", 1)[0] == "/gesund":
            return self._senden(200, b"ok", "text/plain")
        if self._pfad_ok():
            self.send_response(405)
            self.send_header("Allow", "POST")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        return self._senden(404, {"error": "not found"})

    def do_DELETE(self):
        return self._senden(200 if self._pfad_ok() else 404)

    def do_POST(self):
        if not self._pfad_ok():
            return self._senden(404, {"error": "not found"})
        try:
            laenge = int(self.headers.get("Content-Length") or 0)
            if laenge > 1_000_000:
                return self._senden(413, antwort(None, fehler={"code": -32600, "message": "zu groß"}))
            daten = json.loads(self.rfile.read(laenge) or b"null")
        except (ValueError, json.JSONDecodeError):
            return self._senden(400, antwort(None, fehler={"code": -32700, "message": "Parse error"}))
        if isinstance(daten, list):
            antworten = [a for a in (bearbeiten(n) for n in daten) if a is not None]
            return self._senden(200, antworten) if antworten else self._senden(202)
        a = bearbeiten(daten)
        return self._senden(202) if a is None else self._senden(200, a)


def main():
    if not PFAD or len(PFAD) < 24:
        sys.exit(f"KG_MCP_PFAD fehlt oder ist zu kurz in {ENV_DATEI}")
    port = int(EINST.get("PORT") or 8810)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(f"KG Daten (MCP) läuft auf 127.0.0.1:{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
