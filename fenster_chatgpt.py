# ChatGPT ↔ Fensterreinigung (Model Context Protocol, JSON-RPC über POST – wie die ChatGPT-Verbindung im KG LifeHub).
# Adresse: https://portal.kg-reinigung.de/internal/mcp/fenster/<geheimer Schlüssel>
# Den Schlüssel legt ein Server-Befehl an; gespeichert wird nur sein SHA-256 (Tabelle chatgpt_zugang, name „fenster“).
# Falscher oder fehlender Schlüssel → 404.
# Zweiter Zugang für „KG Daten“ (kg_mcp/kg_mcp_server.py, ChatGPT-App „KG“ unter mcp.kg-reinigung.de):
#   POST /internal/mcp/fenster mit Kopfzeile X-KG-MCP-Token (gleiches Token wie /internal/mcp/crm, KG_MCP_TOKEN).
# Nur Fensterreinigung: Pläne ansehen, anlegen, ändern, pausieren/fortsetzen, Einsatz erledigt, Rechnung geschrieben,
# Kunden suchen. Kein Löschen, keine Mails, keine anderen CRM-Daten. Es laufen dieselben Funktionen wie auf der Seite
# (fenster_plan.FUNKTIONEN) – also auch Google-Kalender und LifeHub. Jede Änderung steht in der Tabelle mcp_protokoll.
import hashlib
import hmac
import json
import math
import os
from datetime import date, datetime

from flask import Response, jsonify, request

import fenster_plan as fp

PROTOKOLL_VERSIONEN = ("2025-06-18", "2025-03-26", "2024-11-05")
LEERE_LISTEN = {"resources/list": {"resources": []}, "resources/templates/list": {"resourceTemplates": []},
                "prompts/list": {"prompts": []}, "logging/setLevel": {}}
TAGE = {"mo": 0, "montag": 0, "di": 1, "dienstag": 1, "mi": 2, "mittwoch": 2, "do": 3, "donnerstag": 3, "fr": 4, "freitag": 4,
        "sa": 5, "samstag": 5, "so": 6, "sonntag": 6, "pzt": 0, "pazartesi": 0, "sal": 1, "salı": 1, "sali": 1, "çar": 2,
        "çarşamba": 2, "carsamba": 2, "per": 3, "perşembe": 3, "persembe": 3, "cum": 4, "cuma": 4, "cmt": 5, "cumartesi": 5,
        "paz": 6, "pazar": 6}


class WerkzeugFehler(Exception):
    """Für ChatGPT lesbarer Fehler (isError=true)."""


def schluessel_hash(schluessel):
    return hashlib.sha256(str(schluessel).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- Werkzeuge
_PLAN_ID = {"type": "integer", "description": "Plan-ID aus fenster_uebersicht"}
_RHYTHMUS = {
    "erster_termin": {"type": "string", "description": "Erster Termin, Datum YYYY-MM-DD (ist auch der erste Termin der Serie)"},
    "rhythmus": {"type": "string", "enum": ["wochen", "monate", "bestimmte"],
                 "description": "wochen = alle X Wochen, monate = alle X Monate, bestimmte = in bestimmten Monaten jedes Jahr"},
    "intervall": {"type": "integer", "description": "alle X Wochen (1–52) bzw. alle X Monate (1–24)"},
    "am": {"type": "string", "enum": ["tag", "wochentag"],
           "description": "nur bei monate/bestimmte: am Tag X des Monats oder am n-ten Wochentag. Ohne Angabe: Tag des ersten Termins"},
    "monatstag": {"type": "integer", "description": "Tag im Monat 1–28 oder -1 = letzter Tag (Standard: Tag des ersten Termins)"},
    "nte": {"type": "integer", "description": "1., 2., 3., 4. oder -1 = letzter Wochentag im Monat"},
    "wochentag": {"type": "string", "description": "Mo, Di, Mi, Do, Fr, Sa, So (Standard: Wochentag des ersten Termins)"},
    "monate": {"type": "array", "items": {"type": "integer"}, "description": "bei bestimmte: Monate 1–12, z. B. [3, 9]"},
}
_DETAILS = {
    "uhrzeit": {"type": "string", "description": "Uhrzeit HH:MM (Europe/Berlin)"},
    "dauer_min": {"type": "integer", "description": "Dauer in Minuten"},
    "betrag": {"type": "number", "description": "Betrag je Einsatz in Euro netto"},
    "notiz": {"type": "string", "description": "Notiz für das Team"},
    "strasse": {"type": "string"}, "plz": {"type": "string"}, "ort": {"type": "string"},
    "ansprechpartner": {"type": "string"}, "telefon": {"type": "string"},
}
WERKZEUGE = [
    {"name": "fenster_uebersicht", "title": "Fenster-Kunden anzeigen",
     "description": "Alle Fenster-Kunden (Pläne) mit Rhythmus, Uhrzeit, Betrag, nächstem Termin, überfällig, pausiert; dazu "
                    "Kennzahlen (Termine diese Woche, überfällig, Rechnung offen, Ø Umsatz/Monat).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "fenster_termine", "title": "Nächste Fenster-Termine",
     "description": "Termine der nächsten Tage (höchstens 56), sortiert – so, wie sie im Google-Kalender stehen "
                    "(verschoben_von = ursprüngliches Datum laut Plan). faellig=true heißt: dieser Termin kann jetzt mit "
                    "fenster_erledigt abgeschlossen werden.",
     "inputSchema": {"type": "object", "properties": {"tage": {"type": "integer", "description": "1–56, Standard 14"}}}},
    {"name": "fenster_kunden_suchen", "title": "Kunden suchen (Fensterreinigung)",
     "description": "Kunden aus der CRM-Kundenliste suchen, ohne gekündigte (für fenster_anlegen: kunde_id übernimmt Adresse "
                    "und Ansprechpartner).",
     "inputSchema": {"type": "object", "properties": {"suche": {"type": "string", "description": "Teil des Firmennamens"}},
                     "required": ["suche"]}},
    {"name": "fenster_anlegen", "title": "Fenster-Kunden eintragen",
     "description": "Neuen Fenster-Plan anlegen. Trägt alle Termine in den Google-Kalender „Fensterreinigung“ und als "
                    "wiederkehrende Aufgabe in den KG LifeHub ein. Gibt es für die Firma schon einen Plan, fenster_aendern benutzen.",
     "inputSchema": {"type": "object", "properties": {
         "kunde_id": {"type": "integer", "description": "ID aus fenster_kunden_suchen (empfohlen)"},
         "firma": {"type": "string", "description": "Firmenname, wenn kein kunde_id"}, **_RHYTHMUS, **_DETAILS},
         "required": ["erster_termin", "rhythmus"]}},
    {"name": "fenster_aendern", "title": "Fenster-Plan ändern",
     "description": "Plan ändern (nur die angegebenen Felder). Neuer Rhythmus/Termin/Uhrzeit: die alte Serie endet gestern, "
                    "die neue beginnt mit dem ersten Termin ab heute (Google-Kalender und LifeHub).",
     "inputSchema": {"type": "object", "properties": {"plan_id": _PLAN_ID, "firma": {"type": "string"}, **_RHYTHMUS, **_DETAILS},
                     "required": ["plan_id"]}},
    {"name": "fenster_pausieren", "title": "Fenster-Plan pausieren oder fortsetzen",
     "description": "Pausieren: kommende Termine verschwinden aus dem Google-Kalender, die LifeHub-Aufgabe wird gestoppt "
                    "(nichts gelöscht). fortsetzen=true: ab heute wieder planen. Vorher kurz nachfragen.",
     "inputSchema": {"type": "object", "properties": {"plan_id": _PLAN_ID, "fortsetzen": {"type": "boolean"}},
                     "required": ["plan_id"]}},
    {"name": "fenster_erledigt", "title": "Fensterreinigung erledigt",
     "description": "Den fälligen Termin eines Plans als erledigt speichern (Mitarbeiter, Dauer, Notiz, Rechnung geschrieben). "
                    "Hakt die Aufgabe im LifeHub ab; der nächste Termin kommt von selbst.",
     "inputSchema": {"type": "object", "properties": {
         "plan_id": _PLAN_ID, "mitarbeiter": {"type": "string"}, "dauer_min": {"type": "integer"},
         "notiz": {"type": "string"}, "rechnung": {"type": "boolean", "description": "Rechnung schon geschrieben"}},
         "required": ["plan_id"]}},
    {"name": "fenster_einsaetze", "title": "Erledigte Einsätze anzeigen",
     "description": "Erledigte Fenster-Einsätze, z. B. mit offener Rechnung oder fehlenden Angaben (im LifeHub abgehakt).",
     "inputSchema": {"type": "object", "properties": {
         "filter": {"type": "string", "enum": ["alle", "rechnung_offen", "details_fehlen"]},
         "firma": {"type": "string", "description": "Teil des Firmennamens"}}}},
    {"name": "fenster_einsatz_aendern", "title": "Einsatz ergänzen / Rechnung geschrieben",
     "description": "Angaben eines erledigten Einsatzes ergänzen oder „Rechnung geschrieben“ setzen (einsatz_id aus fenster_einsaetze).",
     "inputSchema": {"type": "object", "properties": {
         "einsatz_id": {"type": "integer"}, "mitarbeiter": {"type": "string"}, "dauer_min": {"type": "integer"},
         "notiz": {"type": "string"}, "rechnung": {"type": "boolean"}}, "required": ["einsatz_id"]}},
    {"name": "fenster_erledigt_rueckgaengig", "title": "Erledigt zurücknehmen",
     "description": "„Erledigt“ zurücknehmen – nur der letzte erledigte Einsatz eines Kunden (in fenster_einsaetze rueckgaengig=true). "
                    "Der Termin ist danach wieder offen; im LifeHub wird die Aufgabe wieder geöffnet. Der Einsatz bleibt im CRM "
                    "als storniert gespeichert. Vorher kurz nachfragen.",
     "inputSchema": {"type": "object", "properties": {"einsatz_id": {"type": "integer"}}, "required": ["einsatz_id"]}},
]
_NUR_LESEN = {"fenster_uebersicht", "fenster_termine", "fenster_kunden_suchen", "fenster_einsaetze"}
for _w in WERKZEUGE:
    _w["annotations"] = {"title": _w["title"], "readOnlyHint": _w["name"] in _NUR_LESEN, "destructiveHint": False,
                         "idempotentHint": _w["name"] in _NUR_LESEN, "openWorldHint": False}


# --------------------------------------------------------------------------- Hilfen

def _f(name):
    f = fp.FUNKTIONEN.get(name)
    if not f:
        raise WerkzeugFehler("Fensterreinigung ist auf dem Server nicht geladen.")
    return f


def _ergebnis(r):
    antwort, status = r if isinstance(r, tuple) else (r, 200)
    if status >= 400 or not antwort.get("ok", True):
        raise WerkzeugFehler(antwort.get("fehler") or f"Fehler ({status}).")
    return antwort


def _wochentag(wert):
    if wert is None or wert == "":
        return None
    if isinstance(wert, int) or str(wert).strip().isdigit():
        n = int(wert)
        if 0 <= n <= 6:
            return n
        raise WerkzeugFehler("wochentag: 0 (Mo) bis 6 (So) oder Mo, Di, Mi, Do, Fr, Sa, So.")
    n = TAGE.get(str(wert).strip().lower().rstrip("."))
    if n is None:
        raise WerkzeugFehler("Unbekannter Wochentag – bitte Mo, Di, Mi, Do, Fr, Sa oder So.")
    return n


def _rhythmus(a, alt=None):
    """Argumente (+ bisheriger Rhythmus) → Rhythmus wie im Formular der Seite. Ohne Tag-Angabe: aus dem ersten Termin."""
    alt = alt or {}
    start = fp._datum(a.get("erster_termin") or alt.get("start"))
    if not start:
        raise WerkzeugFehler("erster_termin fehlt (YYYY-MM-DD).")
    art = a.get("rhythmus") or alt.get("art")
    if art not in fp.ARTEN:
        raise WerkzeugFehler("rhythmus: wochen, monate oder bestimmte.")
    neu = any(k in a for k in ("erster_termin", "rhythmus", "am", "monatstag", "nte", "wochentag", "monate"))
    def wert(k):
        return a[k] if k in a else (None if neu and k in ("monatstag", "nte", "wochentag") else alt.get(k))
    r = {"art": art, "intervall": a.get("intervall") or (alt.get("intervall") if art == alt.get("art") else None) or (4 if art == "wochen" else 1)}
    wt = _wochentag(wert("wochentag"))
    if art == "wochen":
        r["wochentag"] = start.weekday() if wt is None else wt
        return r, start
    if art == "bestimmte":
        monate = a.get("monate") or ([int(m) for m in str(alt.get("monate") or "").split(",") if m] if art == alt.get("art") else [])
        r["monate"] = monate or [start.month]
    am = a.get("am") or ("wochentag" if wert("nte") or (wt is not None and not wert("monatstag")) else "tag")
    if am == "wochentag":
        r.update(tagart="wochentag", wochentag=start.weekday() if wt is None else wt,
                 nte=wert("nte") or (-1 if start.day > 28 else math.ceil(start.day / 7)))
    else:
        r.update(tagart="tag", monatstag=wert("monatstag") or (start.day if start.day <= 28 else -1))
    return r, start


def _plan(pid):
    for p in _f("uebersicht")()["plaene"]:
        if p["id"] == pid:
            return p
    raise WerkzeugFehler(f"Plan {pid} nicht gefunden – IDs stehen in fenster_uebersicht.")


def _kurz(p):
    return {k: p.get(k) for k in ("id", "firma", "ort", "rhythmus_text", "uhrzeit", "dauer_min", "betrag", "naechster",
                                  "ueberfaellig", "aktiv", "pausiert_am", "notiz", "google", "lifehub", "google_fehler",
                                  "lifehub_fehler", "letzter")}


# --------------------------------------------------------------------------- Ausführen

def werkzeug(name, a, protokoll):
    if name == "fenster_uebersicht":
        d = _f("uebersicht")()
        return {"kennzahlen": d["kpi"], "lifehub_ok": d["lifehub_ok"], "plaene": [_kurz(p) for p in d["plaene"]]}

    if name == "fenster_termine":
        tage = max(1, min(56, int(a.get("tage") or 14)))
        d = _f("uebersicht")()
        bis = date.fromordinal(date.fromisoformat(d["heute"]).toordinal() + tage).isoformat()
        return {"heute": d["heute"], "termine": [t for t in d["termine"] if t["datum"] <= bis]}

    if name in ("fenster_kunden_suchen", "kunden_suchen"):  # alter Name: bis ChatGPT die Werkzeuge neu lädt
        q = str(a.get("suche") or "").strip().lower()
        if len(q) < 2:
            raise WerkzeugFehler("Bitte mindestens 2 Buchstaben suchen.")
        kunden = [k for k in _f("stammdaten")()["kunden"] if q in k["firma"].lower()][:20]
        return {"kunden": kunden, "anzahl": len(kunden)}

    if name == "fenster_anlegen":
        kunde = None
        if a.get("kunde_id"):
            kunde = next((k for k in _f("stammdaten")()["kunden"] if k["id"] == int(a["kunde_id"])), None)
            if not kunde:
                raise WerkzeugFehler("kunde_id nicht gefunden (oder gekündigt) – bitte fenster_kunden_suchen benutzen.")
        firma = str(a.get("firma") or (kunde or {}).get("firma") or "").strip()
        if not firma:
            raise WerkzeugFehler("firma oder kunde_id fehlt.")
        for p in _f("uebersicht")()["plaene"]:
            if p["aktiv"] and ((kunde and p["kunde_id"] == kunde["id"]) or p["firma"].strip().lower() == firma.lower()):
                raise WerkzeugFehler(f"Für {p['firma']} gibt es schon einen Plan (plan_id {p['id']}, {p['rhythmus_text']}) – "
                                     "bitte fenster_aendern benutzen.")
        r, start = _rhythmus(a)
        k = kunde or {}
        e = {"kunde_id": k.get("id"), "firma": firma, "rhythmus": r, "start": start.isoformat(),
             "uhrzeit": a.get("uhrzeit") or "", "dauer_min": a.get("dauer_min") or 0,
             "betrag": a.get("betrag") if a.get("betrag") is not None else k.get("fenster_betrag") or "",
             "notiz": a.get("notiz") if a.get("notiz") is not None else k.get("fenster_notiz") or ""}
        for feld in ("strasse", "plz", "ort", "ansprechpartner", "telefon"):
            e[feld] = a.get(feld) if a.get(feld) is not None else k.get(feld) or ""
        if isinstance(e["betrag"], (int, float)):
            e["betrag"] = str(e["betrag"])
        antwort = _ergebnis(_f("speichern")(e))
        protokoll("fenster_anlegen", {"plan_id": antwort.get("id"), "firma": firma})
        return {"plan_id": antwort.get("id"), "meldung": antwort.get("meldung"),
                "erste_termine": [d.isoformat() for d in _f("vorschau")(fp.rhythmus(r)[0], start)]}

    if name == "fenster_aendern":
        pid = int(a.get("plan_id") or 0)
        p = _plan(pid)
        if not p["aktiv"]:
            raise WerkzeugFehler("Der Plan ist pausiert – erst mit fenster_pausieren (fortsetzen=true) fortsetzen.")
        r, start = _rhythmus(a, dict(p["rhythmus"], start=p["start"]))
        e = {"id": pid, "kunde_id": p["kunde_id"], "rhythmus": r, "start": start.isoformat()}
        for feld in ("firma", "uhrzeit", "dauer_min", "betrag", "notiz", "strasse", "plz", "ort", "ansprechpartner", "telefon"):
            e[feld] = a[feld] if a.get(feld) is not None else p.get(feld)
        if isinstance(e["betrag"], (int, float)):
            e["betrag"] = str(e["betrag"]).replace(".", ",")
        antwort = _ergebnis(_f("speichern")(e))
        protokoll("fenster_aendern", {"plan_id": pid, "felder": sorted(a.keys())})
        return {"plan_id": pid, "meldung": antwort.get("meldung"), "plan": _kurz(_plan(pid))}

    if name == "fenster_pausieren":
        pid = int(a.get("plan_id") or 0)
        _plan(pid)
        antwort = _ergebnis(_f("pause")(pid, bool(a.get("fortsetzen"))))
        protokoll("fenster_fortsetzen" if a.get("fortsetzen") else "fenster_pausieren", {"plan_id": pid})
        return {"plan_id": pid, "meldung": antwort.get("meldung") or ("fortgesetzt" if a.get("fortsetzen") else "pausiert"),
                "plan": _kurz(_plan(pid))}

    if name == "fenster_erledigt":
        pid = int(a.get("plan_id") or 0)
        _plan(pid)
        e = {k: a.get(k) for k in ("mitarbeiter", "dauer_min", "notiz", "rechnung") if a.get(k) is not None}
        faellig = next((t for t in _f("uebersicht")()["termine"] if t["plan_id"] == pid and t["faellig"]), None)
        if faellig:
            e["datum"] = faellig["datum"]  # wie im Kalender (ggf. verschoben)
        antwort = _ergebnis(_f("erledigt")(pid, e))
        protokoll("fenster_erledigt", {"plan_id": pid, "einsatz_id": antwort.get("id")})
        return {"einsatz_id": antwort.get("id"), "plan": _kurz(_plan(pid))}

    if name == "fenster_einsaetze":
        filt = a.get("filter") or "alle"
        q = str(a.get("firma") or "").strip().lower()
        liste = [x for x in _f("uebersicht")()["einsaetze"]
                 if (filt == "alle" or (filt == "rechnung_offen" and not x["rechnung"]) or (filt == "details_fehlen" and x["details_fehlen"]))
                 and (not q or q in x["firma"].lower())]
        return {"einsaetze": liste[:50], "anzahl": len(liste)}

    if name == "fenster_einsatz_aendern":
        eid = int(a.get("einsatz_id") or 0)
        e = {k: a[k] for k in ("mitarbeiter", "dauer_min", "notiz", "rechnung") if k in a}
        if not e:
            raise WerkzeugFehler("Nichts zu ändern – mitarbeiter, dauer_min, notiz oder rechnung angeben.")
        _ergebnis(_f("einsatz")(eid, e))
        protokoll("fenster_einsatz_aendern", {"einsatz_id": eid, "felder": sorted(e.keys())})
        return {"einsatz_id": eid, "meldung": "gespeichert"}

    if name == "fenster_erledigt_rueckgaengig":
        eid = int(a.get("einsatz_id") or 0)
        x = next((y for y in _f("uebersicht")()["einsaetze"] if y["id"] == eid), None)
        _ergebnis(_f("rueckgaengig")(eid))
        protokoll("fenster_erledigt_rueckgaengig", {"einsatz_id": eid, "plan_id": x and x["plan_id"]})
        return {"einsatz_id": eid, "meldung": "zurückgenommen – der Termin ist wieder offen",
                "plan": _kurz(_plan(x["plan_id"])) if x else None}

    raise WerkzeugFehler(f"Unbekanntes Werkzeug: {name}")


# --------------------------------------------------------------------------- Protokoll (JSON-RPC)

def _antwort(msg_id, ergebnis):
    return {"jsonrpc": "2.0", "id": msg_id, "result": ergebnis}


def _fehler(msg_id, code, text):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": text}}


def register_fenster_chatgpt(app, get_db_connection):

    def _tabellen(conn):
        conn.execute("CREATE TABLE IF NOT EXISTS chatgpt_zugang (name TEXT PRIMARY KEY, sha256 TEXT NOT NULL, erstellt_am TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS mcp_protokoll (id INTEGER PRIMARY KEY AUTOINCREMENT, zeit TEXT, aktion TEXT, details TEXT)")
        conn.commit()

    conn = get_db_connection()
    try:
        _tabellen(conn)
    finally:
        conn.close()

    def _erlaubt(schluessel):
        conn = get_db_connection()
        try:
            z = conn.execute("SELECT sha256 FROM chatgpt_zugang WHERE name = 'fenster'").fetchone()
        finally:
            conn.close()
        return bool(z and z["sha256"]) and hmac.compare_digest(z["sha256"], schluessel_hash(schluessel))

    def _protokoll(aktion, details):
        conn = get_db_connection()
        try:
            conn.execute("INSERT INTO mcp_protokoll (zeit, aktion, details) VALUES (?, ?, ?)",
                         (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "chatgpt_" + aktion, json.dumps(details, ensure_ascii=False)[:5000]))
            conn.commit()
        finally:
            conn.close()

    def behandeln(m):
        if isinstance(m, dict) and "method" not in m and ("result" in m or "error" in m):
            return None
        if not isinstance(m, dict) or m.get("jsonrpc") != "2.0" or "method" not in m:
            return _fehler(m.get("id") if isinstance(m, dict) else None, -32600, "Invalid Request")
        methode, msg_id, params = m["method"], m.get("id"), m.get("params") or {}
        if msg_id is None:
            return None
        if methode == "initialize":
            v = params.get("protocolVersion")
            return _antwort(msg_id, {
                "protocolVersion": v if v in PROTOKOLL_VERSIONEN else PROTOKOLL_VERSIONEN[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "kg-crm-fenster", "title": "KG Fensterreinigung", "version": "1.0"},
                "instructions": "KG CRM – nur Fensterreinigung: feste Fenster-Kunden mit Rhythmus, Termine im Google-Kalender "
                                "„Fensterreinigung“ und Aufgaben im KG LifeHub. Daten auf Deutsch oder Türkisch, Zeiten Europe/Berlin. "
                                "Vor dem Anlegen kurz die ersten Termine nennen; vor Pausieren nachfragen. Löschen gibt es nicht.",
            })
        if methode == "ping":
            return _antwort(msg_id, {})
        if methode in LEERE_LISTEN:
            return _antwort(msg_id, LEERE_LISTEN[methode])
        if methode == "tools/list":
            return _antwort(msg_id, {"tools": WERKZEUGE})
        if methode == "tools/call":
            args = params.get("arguments") or {}
            try:
                daten = werkzeug(str(params.get("name")), args if isinstance(args, dict) else {}, _protokoll)
            except (WerkzeugFehler, ValueError, TypeError) as exc:
                return _antwort(msg_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
            except Exception as exc:  # unerwartet: lesbar melden, im Server-Log festhalten
                print(f"⚠️ Fensterreinigung-ChatGPT {params.get('name')}: {type(exc).__name__}: {exc}")
                return _antwort(msg_id, {"content": [{"type": "text", "text": f"Interner Fehler ({type(exc).__name__}) – bitte im CRM prüfen."}],
                                         "isError": True})
            return _antwort(msg_id, {"content": [{"type": "text", "text": json.dumps(daten, ensure_ascii=False, default=str)}],
                                     "structuredContent": daten})
        return _fehler(msg_id, -32601, f"Method not found: {methode}")

    def _rpc():
        try:
            nachricht = json.loads(request.get_data() or b"null")
        except ValueError:
            return jsonify(_fehler(None, -32700, "Parse error")), 400
        if isinstance(nachricht, list):
            antworten = [x for x in (behandeln(m) for m in nachricht) if x is not None]
            return jsonify(antworten) if antworten else Response(status=202)
        antwort = behandeln(nachricht)
        return jsonify(antwort) if antwort is not None else Response(status=202)

    @app.route("/internal/mcp/fenster/<schluessel>", methods=["POST"])
    def fenster_chatgpt(schluessel):
        if not _erlaubt(schluessel):
            return jsonify({"error": "not found"}), 404
        return _rpc()

    @app.route("/internal/mcp/fenster", methods=["POST"])
    def fenster_kg_daten():
        """Für „KG Daten“ (ChatGPT-App „KG“): gleiches Token wie /internal/mcp/crm."""
        token = (os.getenv("KG_MCP_TOKEN") or "").strip()
        given = (request.headers.get("X-KG-MCP-Token") or "").strip()
        if not token or not hmac.compare_digest(given.encode(), token.encode()):
            return jsonify({"error": "Zugriff verweigert"}), 403
        return _rpc()

    @app.route("/internal/mcp/fenster/<schluessel>", methods=["GET"])
    def fenster_chatgpt_get(schluessel):
        return Response(status=405, headers={"Allow": "POST"})
