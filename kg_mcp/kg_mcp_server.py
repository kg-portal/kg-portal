#!/usr/bin/env python3
"""KG Daten – MCP-Connector für ChatGPT (nur lesen).

ChatGPT (auch im Sprachmodus) stellt hier Fragen zu KG CRM, Leon Reinigung und
KG Business. Der Dienst liest die SQLite-Datenbanken ausschließlich im
Nur-Lese-Modus (mode=ro + PRAGMA query_only + SQLite-Authorizer):
Ändern, Löschen oder Senden ist technisch unmöglich.

- Nur Python-Standardbibliothek (kein pip nötig).
- Protokoll: MCP "Streamable HTTP" (JSON-RPC per POST, Antwort als JSON).
- Erreichbar nur unter einem geheimen Pfad: https://<domain>/<KG_MCP_PFAD>/mcp
- Geheime Spalten (Passwörter, Tokens, IBAN, Steuer-ID, SV-Nummer, Zugangscodes)
  und Einstellungs-/Token-Tabellen werden nie ausgegeben.

  python3 kg_mcp_server.py [env.json]     Server starten (Standard: /opt/kg-mcp-geheim/env.json)
"""
import hmac
import json
import os
import re
import sqlite3
import sys
import time
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

MAX_ZEILEN = 200
MAX_TEXT = 500
MAX_ANTWORT = 60000
ABFRAGE_SEKUNDEN = 4

PROTOKOLLE = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")

ANLEITUNG = """\
KG Daten: Nur-Lese-Zugriff auf die Firmendaten von Murat (Inhaber von KG Gebäudereinigung und KG Business, Duisburg).
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

Werkzeuge: Für häufige Fragen zuerst kg_heute, rueckrufe, leon_anrufe, firma_suchen benutzen.
Für alles andere: erst schema(system), dann sql_lesen(system, SELECT ...). Du kannst frei SELECT-Abfragen schreiben
(auch JOIN, GROUP BY, WITH). Ändern ist nicht möglich.
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
        if GESPERRTE_TABELLE.search(tabelle):
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


def w_schema(system):
    conn = verbinden(system)
    try:
        aus = []
        for t in tabellen(conn):
            if GESPERRTE_TABELLE.search(t):
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
             "inputSchema": w["schema"], "annotations": dict(NUR_LESEN, title=w["title"])}
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
            "serverInfo": {"name": "kg-daten", "title": "KG Daten", "version": "1.0.0"},
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
