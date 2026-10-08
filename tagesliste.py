# =====================================================
# ARBEITSLISTE FÜR HEUTE + AGENT PER WHATSAPP
# Jeden Werktag (Mo–Fr, kein Feiertag in NRW) zur eingestellten Uhrzeit
# (Standard 07:30) entsteht die Arbeitsliste des Tages:
#   Termine (Google Kalender), Besichtigungen, Rückrufe (Leon), fällige To-Dos,
#   offene Fälle aus Kampagnen-Berichten, Stundenzettel mit unklarer WhatsApp-Antwort,
#   neue Firmen vom Lead-Sammler – optional auch KG Business (To-Dos, Rückrufe).
# Sie kommt als To-Do-Karte („Liste öffnen“ → /heute) und – wenn eine Nummer
# gespeichert ist – als WhatsApp an den Chef.
#
# Schreibt der Chef von dieser Nummer an die WhatsApp des CRM, antwortet der
# Agent: „heute“, „rückrufe“, „berichte“, „todo“, „2 erledigt“, „todo …“ (neue
# Aufgabe), sonst kurze Antwort der KI mit den Tagesdaten. Andere Absender
# laufen unverändert wie bisher.
#
# KG Business (optional): KG_BUSINESS_URL, KG_BUSINESS_USER, KG_BUSINESS_PASSWORD
# im Render Environment – ohne diese Angaben bleibt der Business-Teil weg.
# Abschalten des Takts: TAGESLISTE_LAUF=0.
# =====================================================
import json
import os
import re
import threading
import time
from datetime import datetime, timedelta

import requests
from flask import jsonify, render_template, request

import kg_meldungen

try:
    from zoneinfo import ZoneInfo
    BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # pragma: no cover
    BERLIN = None

TAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
TAGE_LANG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
TODO_QUELLE = "tagesliste"
TAKT_SEKUNDEN = 300


def jetzt_berlin():
    return datetime.now(BERLIN).replace(tzinfo=None) if BERLIN else datetime.now()


def _feiertag(tag):
    try:
        from stundenzettel_auto import feiertage_nrw
        return tag in feiertage_nrw(tag.year)
    except Exception:
        return False


def _datum(text):
    """'2026-10-05…' oder '05.10.2026' → date (sonst None)."""
    s = str(text or "").strip()
    for fmt, laenge in (("%Y-%m-%d", 10), ("%d.%m.%Y", 10)):
        try:
            return datetime.strptime(s[:laenge], fmt).date()
        except ValueError:
            continue
    return None


def _uhrzeit(text):
    m = re.search(r"(\d{1,2}):(\d{2})", str(text or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


# ----------------------------------------------------- Quellen (jede für sich, Fehler stören die anderen nicht)

def _termine(app, tag):
    """Google Kalender über die bestehende Route (gleiche Anmeldung wie die Kalender-Seite)."""
    start = datetime.combine(tag, datetime.min.time())
    if BERLIN:
        start = start.replace(tzinfo=BERLIN)
    ende = start + timedelta(days=1)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
    r = client.get("/api/google-calendar/events", query_string={"timeMin": start.isoformat(), "timeMax": ende.isoformat()})
    d = r.get_json(silent=True) or {}
    if not d.get("success"):
        raise RuntimeError(d.get("error") or f"Kalender HTTP {r.status_code}")
    out = []
    for e in d.get("events") or []:
        name = e.get("calendarName") or e.get("calendar_name") or ""
        if name == "Feiertage in Deutschland":
            continue
        out.append({"zeit": _uhrzeit(e.get("start")) or "ganztägig", "titel": e.get("title") or "Termin",
                    "sub": " · ".join(x for x in (name, e.get("location") or "") if x), "link": "/kalender"})
    return out


def _besichtigungen(conn, tag):
    out = []
    try:
        rows = conn.execute("SELECT id, firma, ansprechpartner, telefon, strasse, plz, ort, termin_datum, termin_uhrzeit, status "
                            "FROM besichtigungen").fetchall()
    except Exception:
        return out
    for r in rows:
        if _datum(r["termin_datum"]) != tag or str(r["status"] or "").lower() in ("erledigt", "abgesagt", "storniert"):
            continue
        out.append({"zeit": _uhrzeit(r["termin_uhrzeit"]) or "", "titel": r["firma"] or "Besichtigung",
                    "sub": " · ".join(x for x in (r["ansprechpartner"] or "", " ".join(y for y in (r["strasse"] or "", r["plz"] or "", r["ort"] or "") if y)) if x),
                    "telefon": r["telefon"] or "", "link": "/besichtigung"})
    out.sort(key=lambda x: x["zeit"] or "99")
    return out


def _rueckrufe_leon(leon_client_factory):
    _code, d = leon_client_factory().request("GET", "/api/rueckrufe/liste", timeout=20)
    out = []
    for r in (d or {}).get("rueckrufe") or []:
        if r.get("lage") not in ("ueberfaellig", "heute"):
            continue
        out.append({"zeit": _uhrzeit(r.get("callback_at")), "titel": r.get("firma") or "Rückruf",
                    "sub": " · ".join(x for x in (r.get("kontaktperson") or r.get("ansprechpartner") or "",
                                                  "überfällig" if r.get("lage") == "ueberfaellig" else "",
                                                  (r.get("callback_note") or "")[:120]) if x),
                    "telefon": r.get("telefon") or "", "link": "/rueckrufe", "ueberfaellig": r.get("lage") == "ueberfaellig"})
    out.sort(key=lambda x: (not x["ueberfaellig"], x["zeit"] or "99"))
    return out


def _todos(conn, tag):
    try:
        rows = conn.execute("""
            SELECT id, task, deadline, due_time, priority, category FROM todos
            WHERE COALESCE(done, 0) = 0 AND COALESCE(deadline, '') <> '' AND deadline <= ?
              AND COALESCE(source, '') NOT IN (?, 'kampagnen-bericht')
            ORDER BY deadline, CASE priority WHEN 'hoch' THEN 0 WHEN 'high' THEN 0 ELSE 1 END, id
        """, (tag.isoformat(), TODO_QUELLE)).fetchall()
    except Exception:
        return []
    return [{"id": r["id"], "zeit": _uhrzeit(r["due_time"]), "titel": r["task"],
             "sub": ("überfällig seit " + _datum(r["deadline"]).strftime("%d.%m.")) if _datum(r["deadline"]) and _datum(r["deadline"]) < tag else (r["category"] or ""),
             "link": "/todo", "art": "todo"} for r in rows]


def _berichte(conn):
    """Offene Fälle (Termin, interessiert, Rückruf) aus Kampagnen-Berichten der letzten 14 Tage."""
    out = []
    try:
        from kampagnen_bericht import bericht_laden
        grenze = (datetime.utcnow() - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        ids = [r[0] for r in conn.execute("SELECT id FROM kampagnen_berichte WHERE erstellt_am >= ? ORDER BY id DESC", (grenze,))]
    except Exception:
        return out
    symbol = {"termin": "📅", "heiss": "🔥", "rueckruf": "📞"}
    for bid in ids:
        b = bericht_laden(conn, bid)
        for f in b["firmen"]:
            if f["kategorie"] in symbol and not f["erledigt"]:
                out.append({"titel": f"{symbol[f['kategorie']]} {f['firma']}", "zeit": "",
                            "sub": " · ".join(x for x in (b["kampagne"], f["rueckruf_am"] and "Rückruf " + f["rueckruf_am"], f["zusammenfassung"][:110]) if x),
                            "telefon": f["telefon"], "link": f"/leon/bericht/{bid}", "art": "bericht", "bericht_id": bid, "lead_id": f["lead_id"]})
    return out


def _stundenzettel(conn):
    # WhatsApp-Antwort, die der KG Agent nicht sicher verstanden hat → Büro prüft / ruft an
    try:
        rows = conn.execute("""
            SELECT m.worker_id, m.monat, m.wa_antwort, w.vorname, w.nachname, w.telefon FROM stundenzettel_monate m
            JOIN mitarbeiter w ON w.id = m.worker_id
            WHERE m.status IN ('wa_unklar', 'wa_nein', 'anruf_unklar', 'anruf_nicht_erreicht')
            ORDER BY m.monat, w.vorname
        """).fetchall()
    except Exception:
        return []
    return [{"titel": f"{r['vorname'] or ''} {r['nachname'] or ''}".strip(), "zeit": "",
             "sub": f"Stundenzettel {r['monat']}: Antwort unklar – bitte prüfen/anrufen"
                    + (f" („{str(r['wa_antwort'] or '').strip()[-80:]}“)" if r["wa_antwort"] else ""),
             "telefon": r["telefon"] or "", "link": "/stundenzettel/automatik"} for r in rows]


def _neue_firmen(conn, tag):
    try:
        gestern = (tag - timedelta(days=3 if tag.weekday() == 0 else 1)).isoformat()
        return conn.execute("SELECT COUNT(*) FROM leads WHERE quelle = 'Lead-Sammler' AND substr(erstellt_am, 1, 10) >= ? "
                            "AND substr(erstellt_am, 1, 10) < ?", (gestern, tag.isoformat())).fetchone()[0]
    except Exception:
        return 0


def _business(tag):
    """KG Business (optional, nur lesen): fällige To-Dos und Rückrufe."""
    url = (os.getenv("KG_BUSINESS_URL") or "").strip().rstrip("/")
    user, pw = os.getenv("KG_BUSINESS_USER") or "", os.getenv("KG_BUSINESS_PASSWORD") or ""
    if not (url and user and pw):
        return None
    s = requests.Session()
    s.post(url + "/login", data={"username": user, "password": pw}, timeout=20)
    out = {"todos": [], "rueckrufe": []}
    t = s.get(url + "/api/todo", timeout=20).json()
    for x in t.get("tasks") or []:
        if x.get("status") != "erledigt" and x.get("lage") in ("ueberfaellig", "heute"):
            out["todos"].append({"titel": x.get("title"), "zeit": x.get("due_time") or "",
                                 "sub": ("überfällig · " if x.get("lage") == "ueberfaellig" else "") + (x.get("lead_firma") or x.get("kunde_firma") or ""),
                                 "link": url + "/todo", "telefon": x.get("lead_telefon") or ""})
    r = s.get(url + "/api/rueckrufe/liste", timeout=20).json()
    for x in r.get("rueckrufe") or []:
        if x.get("lage") in ("ueberfaellig", "heute"):
            out["rueckrufe"].append({"titel": x.get("firma") or "Rückruf", "zeit": _uhrzeit(x.get("callback_at")),
                                     "sub": ("überfällig · " if x.get("lage") == "ueberfaellig" else "") + (x.get("kontaktperson") or ""),
                                     "telefon": x.get("telefon") or "", "link": url + "/rueckrufe"})
    return out


def liste_bauen(app, conn, leon_client_factory, tag=None):
    """Alle Abschnitte der Arbeitsliste. Was nicht erreichbar ist, steht unter 'fehler'."""
    tag = tag or jetzt_berlin().date()
    l = {"datum": tag.isoformat(), "titel": f"{TAGE_LANG[tag.weekday()]}, {tag.strftime('%d.%m.%Y')}", "fehler": []}
    for name, fn in (("termine", lambda: _termine(app, tag)), ("besichtigungen", lambda: _besichtigungen(conn, tag)),
                     ("rueckrufe", lambda: _rueckrufe_leon(leon_client_factory)), ("todos", lambda: _todos(conn, tag)),
                     ("berichte", lambda: _berichte(conn)), ("stundenzettel", lambda: _stundenzettel(conn)),
                     ("business", lambda: _business(tag))):
        try:
            l[name] = fn()
        except Exception as exc:
            l[name] = [] if name != "business" else None
            l["fehler"].append(f"{name}: {str(exc)[:120]}")
    l["neue_firmen"] = _neue_firmen(conn, tag)
    l["anzahl"] = sum(len(l[k]) for k in ("termine", "besichtigungen", "rueckrufe", "todos", "berichte", "stundenzettel")) + \
        (len(l["business"]["todos"]) + len(l["business"]["rueckrufe"]) if l.get("business") else 0)
    return l


ABSCHNITTE = [
    ("termine", "📅 Termine"), ("besichtigungen", "🏢 Besichtigungen"), ("rueckrufe", "📞 Rückrufe"),
    ("todos", "✅ Aufgaben"), ("berichte", "🔥 Aus Kampagnen"), ("stundenzettel", "⏱ Stundenzettel – bitte prüfen"),
]


def liste_text(l, link=""):
    """Kurzfassung für WhatsApp, durchnummeriert (für „2 erledigt“). Gibt (text, nummern) zurück."""
    zeilen = [f"☀️ Guten Morgen! Arbeitsliste {l['titel']}"]
    nummern, nr = [], 0
    for key, titel in ABSCHNITTE:
        eintraege = l.get(key) or []
        if not eintraege:
            continue
        zeilen.append(f"\n{titel} ({len(eintraege)})")
        for e in eintraege[:8]:
            nr += 1
            zeile = f"{nr}. " + (f"{e['zeit']} " if e.get("zeit") and e["zeit"] != "ganztägig" else "") + e["titel"]
            if e.get("telefon"):
                zeile += f" – {e['telefon']}"
            zeilen.append(zeile)
            nummern.append({"nr": nr, "art": e.get("art") or key, "id": e.get("id"), "bericht_id": e.get("bericht_id"),
                            "lead_id": e.get("lead_id"), "titel": e["titel"]})
        if len(eintraege) > 8:
            zeilen.append(f"… und {len(eintraege) - 8} weitere")
    b = l.get("business")
    if b and (b["todos"] or b["rueckrufe"]):
        zeilen.append(f"\n⚡ KG Business: {len(b['rueckrufe'])} Rückrufe · {len(b['todos'])} Aufgaben")
        for e in (b["rueckrufe"] + b["todos"])[:5]:
            zeilen.append("• " + e["titel"] + (f" – {e['telefon']}" if e.get("telefon") else ""))
    if l.get("neue_firmen"):
        zeilen.append(f"\n🆕 {l['neue_firmen']} neue Firmen vom Lead-Sammler")
    if nr == 0 and not (b and (b["todos"] or b["rueckrufe"])):
        zeilen.append("\nHeute steht nichts Dringendes an. 👍")
    else:
        zeilen.append("\nAntworte z. B. „2 erledigt“ oder „todo Müller morgen anrufen“.")
    if link:
        zeilen.append(f"Liste: {link}")
    return "\n".join(zeilen), nummern


# ----------------------------------------------------- Takt: einmal je Werktag zur eingestellten Uhrzeit

def tagesliste_lauf(app, conn, leon_client_factory, jetzt=None):
    """Legt die Arbeitsliste an, wenn es Zeit ist (höchstens einmal je Tag). Gibt True zurück, wenn angelegt."""
    jetzt = jetzt or jetzt_berlin()
    e = kg_meldungen.einstellungen(conn)
    if not e.get("tagesliste_an") or jetzt.weekday() >= 5 or _feiertag(jetzt.date()):
        return False
    if jetzt.strftime("%H:%M") < str(e.get("tagesliste_uhrzeit") or "07:30") or jetzt.hour >= 12:
        return False
    heute = jetzt.date().isoformat()
    conn.execute("CREATE TABLE IF NOT EXISTS kg_tagesliste_laeufe (datum TEXT PRIMARY KEY, zeit TEXT)")
    cur = conn.execute("INSERT OR IGNORE INTO kg_tagesliste_laeufe (datum, zeit) VALUES (?, ?)", (heute, jetzt.strftime("%H:%M")))
    conn.commit()
    if cur.rowcount != 1:
        return False  # heute schon (auch wenn mehrere Prozesse laufen)
    l = liste_bauen(app, conn, leon_client_factory, jetzt.date())
    pfad = f"/heute?d={heute}"
    text, nummern = liste_text(l, kg_meldungen.link(conn, pfad))
    teile = [f"{len(l[k])} {n}" for k, n in (("termine", "Termine"), ("besichtigungen", "Besichtigungen"),
                                               ("rueckrufe", "Rückrufe"), ("todos", "Aufgaben")) if l.get(k)]
    titel = f"📋 Arbeitsliste {TAGE[jetzt.weekday()]} {jetzt.strftime('%d.%m.')}" + (": " + " · ".join(teile) if teile else "")
    kg_meldungen.todo_anlegen(conn, titel, text, pfad, TODO_QUELLE, "hoch" if l["anzahl"] else "normal", "Planung")
    _merken(conn, "letzte_liste", nummern)
    if e.get("tagesliste_whatsapp"):
        kg_meldungen.whatsapp_an_chef(conn, text, "tagesliste")
    return True


def _merken(conn, schluessel, wert):
    conn.execute("INSERT INTO kg_meldungen_einstellungen (schluessel, wert) VALUES (?, ?) "
                 "ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert", (schluessel, json.dumps(wert, ensure_ascii=False)))
    conn.commit()


def _lesen(conn, schluessel, standard=None):
    r = conn.execute("SELECT wert FROM kg_meldungen_einstellungen WHERE schluessel = ?", (schluessel,)).fetchone()
    try:
        return json.loads(r[0]) if r else standard
    except (TypeError, ValueError):
        return standard


# ----------------------------------------------------- Agent per WhatsApp (nur die Chef-Nummer)

HILFE = ("🤖 KG Agent – das kann ich per WhatsApp:\n"
         "• heute – Arbeitsliste\n• rückrufe – Rückrufe heute\n• berichte – letzte Kampagnen\n• todo – offene Aufgaben\n"
         "• 2 erledigt – Punkt 2 der Liste abhaken\n• todo <Text> – neue Aufgabe (mit „morgen“ für morgen)\n"
         "Sonst einfach fragen (Deutsch oder Türkisch).")


def _norm(text):
    t = str(text or "").strip().lower()
    for a, b in (("ü", "u"), ("ö", "o"), ("ä", "a"), ("ß", "ss"), ("ı", "i"), ("ş", "s"), ("ğ", "g"), ("ç", "c")):
        t = t.replace(a, b)
    return re.sub(r"\s+", " ", t)


def agent_antwort(app, conn, leon_client_factory, text):
    """Antwort des Agents auf eine WhatsApp des Chefs (ohne KI, wo es geht)."""
    n = _norm(text)
    jetzt = jetzt_berlin()
    if n in ("hilfe", "help", "yardim", "?", "menu", "menue"):
        return HILFE
    if n in ("heute", "liste", "bugun", "is listesi", "arbeitsliste", "plan", "tagesliste"):
        l = liste_bauen(app, conn, leon_client_factory, jetzt.date())
        text_, nummern = liste_text(l, kg_meldungen.link(conn, f"/heute?d={jetzt.date().isoformat()}"))
        _merken(conn, "letzte_liste", nummern)
        return text_
    if n in ("ruckrufe", "ruckruf", "geri arama", "geri aramalar", "callbacks"):
        r = _rueckrufe_leon(leon_client_factory)
        if not r:
            return "📞 Heute keine Rückrufe offen."
        return "📞 Rückrufe heute:\n" + "\n".join(f"• {x['zeit'] + ' ' if x['zeit'] else ''}{x['titel']}{' – ' + x['telefon'] if x['telefon'] else ''}"
                                                + (" (überfällig)" if x.get("ueberfaellig") else "") for x in r[:15])
    if n in ("berichte", "bericht", "rapor", "raporlar", "kampagnen", "kampanya"):
        try:
            from kampagnen_bericht import berichte_liste
            bl = berichte_liste(conn, 3)
        except Exception:
            bl = []
        if not bl:
            return "📊 Noch kein Kampagnen-Bericht."
        return "📊 Letzte Kampagnen:\n" + "\n".join(
            f"• {b['kampagne']} ({b['bis']}): {b['zahlen']['angerufen']} angerufen, {b['zahlen']['heiss']} interessiert, "
            f"{b['zahlen']['termin']} Termin, {b['zahlen']['rueckruf']} Rückruf – {b['offen']} offen"
            + (f"\n  {kg_meldungen.link(conn, '/leon/bericht/' + str(b['id']))}" if kg_meldungen.link(conn, '/') else "") for b in bl)
    if n in ("todo", "todos", "aufgaben", "gorevler", "gorev"):
        t = _todos(conn, jetzt.date())
        return ("✅ Offene Aufgaben (fällig):\n" + "\n".join(f"• {x['titel']}" + (f" ({x['sub']})" if x["sub"].startswith("überfällig") else "") for x in t[:15])) if t else "✅ Keine fälligen Aufgaben."
    m = re.match(r"^(?:erledigt|tamam|ok|fertig|done)?\s*(\d{1,2})\s*(?:erledigt|tamam|ok|fertig|done|yapildi|bitti)?$", n)
    if m and re.search(r"[a-z]", n):
        return _abhaken(conn, int(m.group(1)))
    m = re.match(r"^(?:todo|aufgabe|not|gorev|hatirlat|erinnere)\s*[:\-]?\s+(.{3,})$", str(text or "").strip(), re.I)
    if m:
        aufgabe = m.group(1).strip()
        tag = jetzt.date() + timedelta(days=1 if re.search(r"\b(morgen|yarin|yarın)\b", aufgabe, re.I) else 0)
        try:
            from kg_todo_routes import ensure_todo_tables
            ensure_todo_tables(conn)
        except Exception:
            pass
        conn.execute("INSERT INTO todos (task, deadline, category, priority, status, period_type, source, created_by, created_at, updated_at) "
                     "VALUES (?, ?, 'WhatsApp', 'normal', 'open', 'once', 'whatsapp-agent', 'KG Agent', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
                     (aufgabe[:300], tag.isoformat()))
        conn.commit()
        return f"✅ Neue Aufgabe für {TAGE[tag.weekday()]} {tag.strftime('%d.%m.')}: {aufgabe[:200]}"
    return _ki_antwort(app, conn, leon_client_factory, text)


def _abhaken(conn, nr):
    eintrag = next((x for x in (_lesen(conn, "letzte_liste", []) or []) if int(x.get("nr") or 0) == nr), None)
    if not eintrag:
        return f"Punkt {nr} finde ich nicht. Schreib „heute“ für die aktuelle Liste."
    if eintrag.get("art") == "todo" and eintrag.get("id"):
        conn.execute("UPDATE todos SET done = 1, status = 'done', completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (eintrag["id"],))
        conn.commit()
        return f"✅ Erledigt: {eintrag['titel']}"
    if eintrag.get("art") == "bericht" and eintrag.get("bericht_id"):
        from kampagnen_bericht import erledigt_setzen, TODO_QUELLE as BERICHT_QUELLE
        b = erledigt_setzen(conn, int(eintrag["bericht_id"]), int(eintrag["lead_id"]), True)
        if b and b["offen"] == 0:
            kg_meldungen.todo_erledigt(conn, BERICHT_QUELLE, f"/leon/bericht/{eintrag['bericht_id']}", True)
        return f"✅ Erledigt: {eintrag['titel']}"
    return f"„{eintrag['titel']}“ kann ich nur anzeigen – bitte im CRM abhaken."


def _ki_antwort(app, conn, leon_client_factory, frage):
    try:
        from openai_client import ask_ai
        l = liste_bauen(app, conn, leon_client_factory)
        kontext, _ = liste_text(l)
        prompt = ("Du bist der KG Agent, der persönliche Assistent des Chefs von KG Gebäudereinigung und KG Business. "
                  "Antworte kurz (höchstens 6 Zeilen), freundlich und in der Sprache der Frage (Deutsch oder Türkisch). "
                  "Nutze nur die Daten unten; erfinde nichts. Wenn du etwas nicht weißt, sag es und nenne den passenden Befehl "
                  "(heute, rückrufe, berichte, todo, hilfe).\n\nDATEN VON HEUTE:\n" + kontext + "\n\nFRAGE DES CHEFS:\n" + str(frage)[:800])
        antwort = (ask_ai(prompt) or {}).get("answer") or ""
        return antwort.strip()[:1500] or HILFE
    except Exception as exc:
        print("[KG-AGENT WHATSAPP] KI nicht verfügbar:", exc)
        return HILFE


_kontext = {}


def chef_nachricht_beantworten(phone, raw_from, body):
    """Vom WhatsApp-Eingang aufgerufen. True = war der Chef und ist beantwortet (sonst unverändert weiter)."""
    if not _kontext:
        return False
    from whatsapp_connector_routes import wa_conn
    conn = _kontext["get_db_connection"]()
    try:
        kg_meldungen.ensure_tables(conn)
        if not kg_meldungen.ist_chef(conn, phone, raw_from) or not kg_meldungen.einstellungen(conn).get("agent_antwortet"):
            return False
        antwort = agent_antwort(_kontext["app"], conn, _kontext["leon_client_factory"], body)
    finally:
        conn.close()
    wc = wa_conn()
    try:
        wc.execute("INSERT INTO whatsapp_outbox (phone, text, status, source) VALUES (?, ?, 'pending', 'kg_agent')",
                   (phone or raw_from, antwort[:3500]))
        wc.commit()
    finally:
        wc.close()
    return True


# ----------------------------------------------------- Routen + Takt

def register_tagesliste(app, login_required, get_db_connection, leon_client_factory):
    _kontext.update(app=app, get_db_connection=get_db_connection, leon_client_factory=leon_client_factory)

    @app.route("/heute")
    @login_required
    def heute_seite():
        tag = _datum(request.args.get("d")) or jetzt_berlin().date()
        conn = get_db_connection()
        try:
            kg_meldungen.ensure_tables(conn)
            l = liste_bauen(app, conn, leon_client_factory, tag)
        finally:
            conn.close()
        return render_template("heute.html", l=l, abschnitte=ABSCHNITTE, ist_heute=(tag == jetzt_berlin().date()))

    @app.route("/api/heute/erledigt", methods=["POST"])
    @login_required
    def heute_erledigt():
        d = request.get_json(silent=True) or {}
        conn = get_db_connection()
        try:
            if d.get("art") == "todo":
                conn.execute("UPDATE todos SET done = 1, status = 'done', completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                             (int(d.get("id") or 0),))
                conn.commit()
            elif d.get("art") == "bericht":
                from kampagnen_bericht import erledigt_setzen, TODO_QUELLE as BERICHT_QUELLE
                b = erledigt_setzen(conn, int(d.get("bericht_id") or 0), int(d.get("lead_id") or 0), True)
                if b and b["offen"] == 0:
                    kg_meldungen.todo_erledigt(conn, BERICHT_QUELLE, f"/leon/bericht/{int(d.get('bericht_id') or 0)}", True)
            else:
                return jsonify({"success": False, "error": "Das bitte auf der jeweiligen Seite erledigen."}), 400
            return jsonify({"success": True})
        finally:
            conn.close()

    @app.route("/api/heute/jetzt-senden", methods=["POST"])
    @login_required
    def heute_jetzt_senden():
        """Arbeitsliste sofort per WhatsApp senden (zum Ausprobieren)."""
        conn = get_db_connection()
        try:
            l = liste_bauen(app, conn, leon_client_factory)
            text, nummern = liste_text(l, kg_meldungen.link(conn, "/heute"))
            _merken(conn, "letzte_liste", nummern)
            if not kg_meldungen.whatsapp_an_chef(conn, text, "tagesliste"):
                return jsonify({"success": False, "error": "Keine WhatsApp-Nummer gespeichert (Leon → Berichte → WhatsApp an dich)."}), 400
            return jsonify({"success": True})
        finally:
            conn.close()

    if os.getenv("TAGESLISTE_LAUF", "1").strip() == "0":
        return

    def schleife():
        time.sleep(100)
        while True:
            conn = None
            try:
                conn = get_db_connection()
                kg_meldungen.ensure_tables(conn)
                if tagesliste_lauf(app, conn, leon_client_factory):
                    print("[TAGESLISTE] Arbeitsliste angelegt")
            except Exception as exc:  # darf das CRM nie stören
                print("[TAGESLISTE] Fehler:", exc)
            finally:
                if conn is not None:
                    conn.close()
            time.sleep(TAKT_SEKUNDEN)

    if not getattr(app, "_kg_tagesliste_takt", False):
        app._kg_tagesliste_takt = True
        threading.Thread(target=schleife, daemon=True, name="kg-tagesliste").start()
