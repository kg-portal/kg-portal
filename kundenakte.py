# Kundenakte (/akte/<id>): alles zu einer Firma an einem Ort – Schritte, Verlauf, Besichtigung, Angebot, Vertrag, Mails, Notizen.
# Schlüssel ist die Tagesliste-ID (tagesliste_leads.id ist AUTOINCREMENT und wird nie neu vergeben; Status-Sicherung,
# Angebot-Editor und Vertrag-Editor benutzen dieselbe ID).
# Sicherung: jede Änderung an besichtigung_data_json und notiz in tagesliste_leads wird per Trigger in eigene Tabellen kopiert
# (akte_besichtigung = alle Versionen, akte_tl_notiz = letzter Stand). Die nächtliche Leerung der Tagesliste und ein Speichern
# ohne KG-Scan-Daten löschen dort nichts mehr. Bestehende Tabellen werden nur gelesen; geschrieben wird nur in akte_*.
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import jsonify, render_template, request

BERLIN = ZoneInfo("Europe/Berlin")
LEER = ("", "{}", "null")
STATUS_NAME = {"angerufen": "Angerufen", "interessiert": "Interesse", "besichtigung": "Für Besichtigung vorgemerkt",
               "kontaktformular": "Kontaktformular", "spaeter": "Später wieder anrufen", "verloren": "Kein Interesse"}
SCHRITTE = [("kontakt", "Kontakt"), ("interesse", "Interesse"), ("besichtigung", "Besichtigung"),
            ("angebot", "Angebot"), ("vertrag", "Vertrag"), ("kunde", "Kunde")]
NACHFASSEN_TAGE = 7


# ---------------------------------------------------------------- Tabellen, Trigger, Sicherung

def _hat_tabelle(conn, name):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone())


def _spalten(conn, tabelle):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({tabelle})").fetchall()}


_FERTIG = False


def tabellen(conn):
    """Eigene Tabellen + Trigger anlegen (einmal je Prozess, sobald es die Tagesliste gibt)."""
    global _FERTIG
    if _FERTIG:
        return
    conn.execute("""CREATE TABLE IF NOT EXISTS akte_besichtigung (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tagesliste_id INTEGER NOT NULL, data_json TEXT NOT NULL,
        gespeichert_am TEXT NOT NULL, quelle TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS akte_besichtigung_tid ON akte_besichtigung (tagesliste_id, id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS akte_tl_notiz (
        tagesliste_id INTEGER PRIMARY KEY, notiz TEXT, geaendert_am TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS akte_notizen (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tagesliste_id INTEGER NOT NULL, text TEXT NOT NULL, von TEXT, am TEXT NOT NULL)""")
    conn.execute("CREATE INDEX IF NOT EXISTS akte_notizen_tid ON akte_notizen (tagesliste_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS akte_ereignisse (
        id INTEGER PRIMARY KEY AUTOINCREMENT, tagesliste_id INTEGER NOT NULL, art TEXT NOT NULL, titel TEXT, text TEXT,
        an TEXT, am TEXT NOT NULL, ref TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS akte_ereignisse_tid ON akte_ereignisse (tagesliste_id)")
    conn.execute("CREATE TABLE IF NOT EXISTS akte_meta (schluessel TEXT PRIMARY KEY, wert TEXT)")
    conn.execute("INSERT OR IGNORE INTO akte_meta (schluessel, wert) VALUES ('mail_protokoll_seit', ?)", (_jetzt(),))
    if not _hat_tabelle(conn, "tagesliste_leads"):
        return
    sp = _spalten(conn, "tagesliste_leads")
    # Zeitpunkte in den Triggern in UTC (datetime('now')), beim Anzeigen nach Berlin umgerechnet
    if "besichtigung_data_json" in sp:
        conn.execute("""CREATE TRIGGER IF NOT EXISTS akte_besichtigung_neu AFTER INSERT ON tagesliste_leads
            WHEN COALESCE(NEW.besichtigung_data_json, '') NOT IN ('', '{}', 'null')
            BEGIN
                INSERT INTO akte_besichtigung (tagesliste_id, data_json, gespeichert_am, quelle)
                VALUES (NEW.id, NEW.besichtigung_data_json, datetime('now'), 'neu');
            END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS akte_besichtigung_aenderung AFTER UPDATE OF besichtigung_data_json ON tagesliste_leads
            WHEN COALESCE(NEW.besichtigung_data_json, '') NOT IN ('', '{}', 'null')
             AND NEW.besichtigung_data_json IS NOT OLD.besichtigung_data_json
            BEGIN
                INSERT INTO akte_besichtigung (tagesliste_id, data_json, gespeichert_am, quelle)
                VALUES (NEW.id, NEW.besichtigung_data_json, datetime('now'), 'aenderung');
            END""")
        # Erstübernahme: was heute schon in der Tagesliste steht, sofort sichern (einmal je Firma)
        conn.execute("""INSERT INTO akte_besichtigung (tagesliste_id, data_json, gespeichert_am, quelle)
            SELECT t.id, t.besichtigung_data_json, datetime('now'), 'uebernommen' FROM tagesliste_leads t
            WHERE COALESCE(t.besichtigung_data_json, '') NOT IN ('', '{}', 'null')
              AND NOT EXISTS (SELECT 1 FROM akte_besichtigung a WHERE a.tagesliste_id = t.id)""")
    if "notiz" in sp:
        conn.execute("""CREATE TRIGGER IF NOT EXISTS akte_notiz_neu AFTER INSERT ON tagesliste_leads
            WHEN COALESCE(NEW.notiz, '') != ''
            BEGIN
                INSERT OR REPLACE INTO akte_tl_notiz (tagesliste_id, notiz, geaendert_am) VALUES (NEW.id, NEW.notiz, datetime('now'));
            END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS akte_notiz_aenderung AFTER UPDATE OF notiz ON tagesliste_leads
            WHEN COALESCE(NEW.notiz, '') != '' AND NEW.notiz IS NOT OLD.notiz
            BEGIN
                INSERT OR REPLACE INTO akte_tl_notiz (tagesliste_id, notiz, geaendert_am) VALUES (NEW.id, NEW.notiz, datetime('now'));
            END""")
        conn.execute("""INSERT INTO akte_tl_notiz (tagesliste_id, notiz, geaendert_am)
            SELECT t.id, t.notiz, datetime('now') FROM tagesliste_leads t
            WHERE COALESCE(t.notiz, '') != '' AND NOT EXISTS (SELECT 1 FROM akte_tl_notiz n WHERE n.tagesliste_id = t.id)""")
    _FERTIG = "besichtigung_data_json" in sp and "notiz" in sp


def _jetzt():
    return datetime.now(BERLIN).isoformat(timespec="seconds")


def _json(text):
    try:
        wert = json.loads(text) if isinstance(text, str) else text
    except (TypeError, ValueError):
        return {}
    return wert if isinstance(wert, dict) else {}


def _txt(v):
    return " ".join(str(v or "").split())


def besichtigung_daten(conn, tid, zeile_json=None):
    """Neueste gesicherte Besichtigung (sonst die aus der Tagesliste-Zeile). Fehlt dort der KG Scan, kommt der zuletzt
    gesicherte KG Scan dazu – ein Speichern ohne Scan-Daten löscht ihn so nicht."""
    daten = {}
    scan = None
    if _hat_tabelle(conn, "akte_besichtigung"):
        for r in conn.execute("SELECT data_json FROM akte_besichtigung WHERE tagesliste_id = ? ORDER BY id DESC LIMIT 40", (tid,)).fetchall():
            d = _json(r[0])
            if not d:
                continue
            if not daten:
                daten = d
            if isinstance(d.get("kg_scan"), dict) and d["kg_scan"]:
                scan = d["kg_scan"]
                break
    if not daten:
        daten = _json(zeile_json)
        if isinstance(daten.get("kg_scan"), dict) and daten["kg_scan"]:
            scan = daten["kg_scan"]
    if daten and scan and not (isinstance(daten.get("kg_scan"), dict) and daten["kg_scan"]):
        daten = dict(daten, kg_scan=scan)
    return daten


def kg_scan_behalten(conn, tid, data_json):
    """Für tagesliste-status: kommen neue Besichtigung-Daten ohne KG Scan, bleibt der gesicherte KG Scan erhalten."""
    neu = _json(data_json)
    if not neu or (isinstance(neu.get("kg_scan"), dict) and neu["kg_scan"]):
        return data_json
    alt = besichtigung_daten(conn, tid, (conn.execute("SELECT besichtigung_data_json FROM tagesliste_leads WHERE id = ?", (tid,)).fetchone() or [None])[0])
    if isinstance(alt.get("kg_scan"), dict) and alt["kg_scan"]:
        neu["kg_scan"] = alt["kg_scan"]
        return json.dumps(neu, ensure_ascii=False)
    return data_json


def sichern(conn, tid, data_json, quelle):
    """Besichtigung direkt sichern (z. B. wenn die Tagesliste-Zeile nachts schon gelöscht wurde)."""
    if str(data_json or "").strip() in LEER or not _json(data_json):
        return False
    letzte = conn.execute("SELECT data_json FROM akte_besichtigung WHERE tagesliste_id = ? ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
    if letzte and letzte[0] == data_json:
        return False
    conn.execute("INSERT INTO akte_besichtigung (tagesliste_id, data_json, gespeichert_am, quelle) VALUES (?, ?, datetime('now'), ?)",
                 (tid, data_json, quelle))
    return True


# ---------------------------------------------------------------- Zeiten

def _zeit(text, utc=False):
    """Text → Zeit in Berlin (None, wenn unlesbar). utc=True für Felder aus CURRENT_TIMESTAMP / datetime('now')."""
    t = str(text or "").strip()
    if not t:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            d = datetime.strptime(t[:25] if "%z" in fmt else t[:19], fmt)
        except ValueError:
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc).astimezone(BERLIN) if utc and fmt not in ("%Y-%m-%d", "%d.%m.%Y") else d.replace(tzinfo=BERLIN)
        return d.astimezone(BERLIN)
    try:
        d = datetime.fromisoformat(t)
        return (d if d.tzinfo else d.replace(tzinfo=BERLIN)).astimezone(BERLIN)
    except ValueError:
        return None


def _iso(d):
    return d.isoformat(timespec="minutes") if d else ""


def _anz(d, datum=False):
    """Anzeige in Berlin-Zeit: „19.10.2026 · 09:27“ (nur Datum, wenn die Quelle keine Uhrzeit kennt)."""
    if not d:
        return ""
    return d.strftime("%d.%m.%Y") if datum or (d.hour == 0 and d.minute == 0) else d.strftime("%d.%m.%Y · %H:%M")


def _euro(v):
    try:
        return f"{float(v):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    except (TypeError, ValueError):
        return ""


def _zahl(text):
    t = str(text or "").replace("€", "").replace(" ", "").strip()
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return float(t)
    except ValueError:
        m = re.search(r"-?\d+(?:\.\d+)?", t)
        return float(m.group(0)) if m else 0.0


# ---------------------------------------------------------------- Mail-Protokoll

def mail_merken(get_db_connection, an, betreff, anhaenge, gmail_id):
    """Nach jedem erfolgreichen Versand über KG-Mail: zu allen Firmen mit dieser E-Mail-Adresse ins Protokoll."""
    adressen = {a.strip().lower() for a in re.split(r"[,;\s]+", str(an or "")) if "@" in a}
    if not adressen:
        return 0
    conn = get_db_connection()
    try:
        tabellen(conn)
        ids = set()
        for adr in adressen:
            for tab in ("tagesliste_leads", "tagesliste_status_backup"):
                if not _hat_tabelle(conn, tab):
                    continue
                spalte = "id" if tab == "tagesliste_leads" else "tagesliste_id"
                ids.update(r[0] for r in conn.execute(f"SELECT {spalte} FROM {tab} WHERE lower(trim(COALESCE(email, ''))) = ?", (adr,)).fetchall())
        namen = ", ".join(str(a.get("filename") or "").strip() for a in (anhaenge or []) if isinstance(a, dict) and a.get("filename"))
        for tid in ids:
            conn.execute("INSERT INTO akte_ereignisse (tagesliste_id, art, titel, text, an, am, ref) VALUES (?, 'mail', ?, ?, ?, ?, ?)",
                         (tid, _txt(betreff)[:300], namen[:500], ", ".join(sorted(adressen))[:300], _jetzt(), str(gmail_id or "")[:100]))
        conn.commit()
        return len(ids)
    finally:
        conn.close()


# ---------------------------------------------------------------- Akte zusammenstellen

def _zusammenfassung(daten):
    raeume = [r for r in (daten.get("raeume") or []) if isinstance(r, dict)]
    m2 = sum(_zahl(r.get("m2") or r.get("area")) for r in raeume)
    bereiche = []
    for r in raeume:
        n = _txt(r.get("typ") or r.get("name"))
        if n and n not in bereiche:
            bereiche.append(n)
    haeuf = Counter(_txt(r.get("haeufigkeit")) for r in raeume if _txt(r.get("haeufigkeit")))
    zeiten = []
    for e in daten.get("einsatzzeiten") or []:
        if isinstance(e, dict) and _txt(e.get("tag")):
            zeiten.append((_txt(e.get("tag")) + " " + _txt(e.get("uhrzeit"))).strip())
    scan = daten.get("kg_scan") if isinstance(daten.get("kg_scan"), dict) else {}
    return {
        "m2": round(m2, 1), "bereiche": bereiche, "anzahl_raeume": len(raeume),
        "haeufigkeit": haeuf.most_common(1)[0][0] if haeuf else "",
        "zeiten": zeiten, "notiz": _txt(daten.get("notiz"))[:600], "leistungen": [_txt(x) for x in daten.get("leistungen") or [] if _txt(x)][:6],
        "scan": bool(scan), "scan_bild": scan.get("preview_3d_url") or scan.get("preview_2d_url") or "",
        "scan_modell": scan.get("model_url") or "", "von_leon": _txt(daten.get("notiz")).startswith("Von Leon"),
    }


def _notiz_zeilen(text):
    """Notiz-Feld der Tagesliste → Einträge. „[Leon 06.10.2026 10:14] Interessiert: …“ wird zum Leon-Eintrag."""
    aus = []
    for zeile in str(text or "").splitlines():
        z = zeile.strip()
        if not z:
            continue
        m = re.match(r"\[Leon (\d{2}\.\d{2}\.\d{4} \d{2}:\d{2})\]\s*(.*)", z)
        if m:
            rest = m.group(2)
            ergebnis, _, summe = rest.partition(":")
            aus.append({"art": "leon", "zeit": _zeit(m.group(1)), "titel": "Leon-Anruf: " + (_txt(ergebnis) or "Gespräch"), "text": _txt(summe)[:700]})
        else:
            aus.append({"art": "notiz", "zeit": None, "titel": "Notiz", "text": z[:700]})
    return aus


def akte(conn, tid):
    tabellen(conn)
    zeile = conn.execute("SELECT * FROM tagesliste_leads WHERE id = ?", (tid,)).fetchone() if _hat_tabelle(conn, "tagesliste_leads") else None
    backups = conn.execute("SELECT * FROM tagesliste_status_backup WHERE tagesliste_id = ? ORDER BY backup_am DESC", (tid,)).fetchall() \
        if _hat_tabelle(conn, "tagesliste_status_backup") else []
    anfragen = conn.execute("SELECT * FROM neue_anfragen WHERE tagesliste_id = ? ORDER BY id", (tid,)).fetchall() \
        if _hat_tabelle(conn, "neue_anfragen") and "tagesliste_id" in _spalten(conn, "neue_anfragen") else []
    versionen = conn.execute("SELECT id, data_json, gespeichert_am, quelle FROM akte_besichtigung WHERE tagesliste_id = ? ORDER BY id",
                             (tid,)).fetchall()
    if not zeile and not backups and not versionen and not anfragen:
        return None
    zk = zeile.keys() if zeile else []

    def feld(name):
        for quelle in ([zeile] if zeile else []) + list(backups):
            if quelle is not None and name in quelle.keys() and _txt(quelle[name]):
                return _txt(quelle[name])
        for a in anfragen:
            if name in a.keys() and _txt(a[name]):
                return _txt(a[name])
        return ""

    entwurf = None
    if _hat_tabelle(conn, "angebot_editor_entwuerfe"):
        entwurf = conn.execute("SELECT felder_json, kalkulation_json, geaendert_am FROM angebot_editor_entwuerfe WHERE tagesliste_id = ?", (tid,)).fetchone()
    ef = _json(entwurf["felder_json"]) if entwurf else {}
    nummern = conn.execute("SELECT nr, vergeben_am FROM angebot_nummern WHERE tagesliste_id = ? ORDER BY id", (tid,)).fetchall() \
        if _hat_tabelle(conn, "angebot_nummern") else []
    vertrag = conn.execute("SELECT * FROM vertrag_editor WHERE tagesliste_id = ?", (tid,)).fetchone() if _hat_tabelle(conn, "vertrag_editor") else None
    leon = conn.execute("SELECT * FROM leon_links WHERE tagesliste_id = ? ORDER BY aktualisiert_am DESC", (tid,)).fetchall() \
        if _hat_tabelle(conn, "leon_links") else []
    historie = conn.execute("SELECT status, datum FROM tagesliste_status_history WHERE tagesliste_id = ? ORDER BY datum", (tid,)).fetchall() \
        if _hat_tabelle(conn, "tagesliste_status_history") else []
    tl_notiz = conn.execute("SELECT notiz, geaendert_am FROM akte_tl_notiz WHERE tagesliste_id = ?", (tid,)).fetchone()
    notizen = conn.execute("SELECT id, text, von, am FROM akte_notizen WHERE tagesliste_id = ? ORDER BY id DESC", (tid,)).fetchall()
    mails = conn.execute("SELECT titel, text, an, am FROM akte_ereignisse WHERE tagesliste_id = ? AND art = 'mail' ORDER BY id", (tid,)).fetchall()
    mail_seit = _zeit((conn.execute("SELECT wert FROM akte_meta WHERE schluessel = 'mail_protokoll_seit'").fetchone() or [""])[0])

    status_alle = {str(b["status"]).lower() for b in backups} | {str(h["status"]).lower() for h in historie}
    live = str(zeile["status"] or "offen").lower() if zeile else ""
    angebot_nr = _txt(zeile["angebot_nr"]) if zeile and "angebot_nr" in zk else ""
    daten = besichtigung_daten(conn, tid, zeile["besichtigung_data_json"] if zeile and "besichtigung_data_json" in zk else None)
    bes = _zusammenfassung(daten) if daten else None
    bes_echt = bool(bes and (bes["anzahl_raeume"] or bes["scan"]) and not (bes["von_leon"] and len(versionen) <= 1))

    jetzt = datetime.now(BERLIN)
    verlauf = []

    def eintrag(art, zeit, titel, text="", wer="", chips=None, geplant=False, datum=False):
        verlauf.append({"art": art, "zeit": _iso(zeit), "zeit_text": _anz(zeit, datum), "titel": titel, "text": text, "wer": wer, "chips": chips or [],
                        "geplant": geplant, "_s": (zeit or datetime(2000, 1, 1, tzinfo=BERLIN), len(verlauf))})

    erstellt = _zeit(feld("erstellt_am"), utc=True)
    quelle = feld("quelle")
    eintrag("start", erstellt, "Firma aufgenommen", ("Quelle: " + quelle) if quelle else "")
    for a in anfragen:
        eintrag("anfrage", _zeit(a["erstellt_am"], utc=True), "Anfrage eingegangen", _txt(a["notiz"])[:400], "Neue Anfrage")
        if "portfolio_am" in a.keys() and _txt(a["portfolio_am"]):
            eintrag("mail", _zeit(a["portfolio_am"]), "Info-Mail gesendet", _txt(a["portfolio_an"]), "Neue Anfrage", [{"text": "✓ gesendet", "farbe": "g"}])
        if "terminmail_am" in a.keys() and _txt(a["terminmail_am"]):
            eintrag("mail", _zeit(a["terminmail_am"]), "Terminbestätigung gesendet", _txt(a["termin_text"]), "Neue Anfrage", [{"text": "✓ gesendet", "farbe": "g"}])
    zeit_feld = {"angerufen": _zeit(feld("angerufen_am")), "interessiert": _zeit(feld("interessiert_am"))}
    gesehen = set()
    for h in historie:
        st = str(h["status"]).lower()
        if st == "angebot" or st not in STATUS_NAME or st in gesehen:
            continue
        gesehen.add(st)
        z = _zeit(h["datum"])
        genau = zeit_feld.get(st)
        if not genau:  # Uhrzeit aus der Status-Sicherung (zuletzt gesetzt), wenn es derselbe Tag ist
            genau = next((_zeit(b["backup_am"], utc=True) for b in backups if str(b["status"]).lower() == st and "backup_am" in b.keys()), None)
        if genau and z and genau.date() == z.date():
            z = genau
        text = ""
        if st == "spaeter" and feld("spaeter_datum"):
            text = "Wieder anrufen am " + feld("spaeter_datum")
        eintrag(st, z, STATUS_NAME[st], text, datum=z is not genau)
    for st, z in zeit_feld.items():
        if z and st not in gesehen:
            eintrag(st, z, STATUS_NAME[st])
    notiz_text = (zeile["notiz"] if zeile and "notiz" in zk else "") or (tl_notiz["notiz"] if tl_notiz else "") or feld("notiz")
    tl_notizen = []
    for n in _notiz_zeilen(notiz_text):
        if n["art"] == "leon":
            eintrag("leon", n["zeit"], n["titel"], n["text"], "Leon")
        else:
            tl_notizen.append(n["text"])
    for l in leon:
        if _txt(l["letztes_ergebnis"]) and not any(v["art"] == "leon" for v in verlauf):
            eintrag("leon", _zeit(l["aktualisiert_am"]), "Leon-Anruf: " + _txt(l["letztes_ergebnis"]), "", "Leon")
    if versionen:
        d = _zusammenfassung(_json(versionen[0]["data_json"]))
        if d["von_leon"]:
            eintrag("leon", _zeit(versionen[0]["gespeichert_am"], utc=True), "Leon hat Angaben zur Reinigung erfasst",
                    ", ".join(d["bereiche"]) or "", "Leon")
        letzte = versionen[-1]
        if bes_echt:
            chips = [{"text": "✓ gesichert", "farbe": "g"}]
            if bes and bes["scan"]:
                chips.append({"text": "KG Scan 3D", "farbe": "g"})
            teile = []
            if bes["m2"]:
                teile.append(f"{bes['m2']:g} m²".replace(".", ","))
            if bes["bereiche"]:
                teile.append(", ".join(bes["bereiche"][:5]))
            if bes["zeiten"]:
                teile.append("Zeiten: " + ", ".join(bes["zeiten"][:5]))
            eintrag("besichtigung", _zeit(letzte["gespeichert_am"], utc=True), "Besichtigung gespeichert", " · ".join(teile), "", chips)
    for n in nummern:
        eintrag("angebot", _zeit(n["vergeben_am"]), f"Angebot AN-{n['nr']} fertiggestellt", "", "")
    if angebot_nr and not nummern:
        eintrag("angebot", _zeit(zeile["angebot_datum"]), f"Angebot AN-{angebot_nr} fertiggestellt", "", "")
    netto = float(zeile["angebot_netto"] or 0) if zeile and "angebot_netto" in zk else 0.0
    if entwurf and not angebot_nr and not nummern:
        eintrag("angebot", _zeit(entwurf["geaendert_am"]), "Angebot in Arbeit (Entwurf)", "", "")
    angebot_mail = None
    for m in mails:
        z = _zeit(m["am"])
        titel = _txt(m["titel"]) or "Mail"
        eintrag("mail", z, "Mail gesendet: " + titel, ("An " + _txt(m["an"])) + (" · Anhang: " + _txt(m["text"]) if _txt(m["text"]) else ""), "",
                [{"text": "✓ gesendet", "farbe": "g"}])
        if "angebot" in titel.lower() or "angebot" in _txt(m["text"]).lower():
            angebot_mail = z
    if vertrag:
        eintrag("vertrag", _zeit(vertrag["erstellt_am"]), "Angebot angenommen – Vertrag vorbereitet", "", "")
        if _txt(vertrag["gesendet_am"]):
            eintrag("vertrag", _zeit(vertrag["gesendet_am"]), "Vertrag gesendet", ("An " + _txt(vertrag["gesendet_an"])) if _txt(vertrag["gesendet_an"]) else "", "",
                    [{"text": "✓ gesendet", "farbe": "g"}])
    for n in notizen:
        eintrag("notiz", _zeit(n["am"]), "Notiz", _txt(n["text"]), _txt(n["von"]))

    # ---- Schritte
    angebot_zeit = None
    for n in nummern:
        angebot_zeit = _zeit(n["vergeben_am"]) or angebot_zeit
    if not angebot_zeit and angebot_nr:
        angebot_zeit = _zeit(zeile["angebot_datum"])
    # „Zurück zu Besichtigung“ löscht die Nummer in der Zeile – dann zählt das Angebot nicht mehr als verschickt
    hat_angebot = bool(angebot_nr) or (bool(nummern) and (not zeile or live == "angebot"))
    stufe = 0
    if status_alle & {"interessiert", "besichtigung", "kontaktformular"} or anfragen:
        stufe = 1
    if "besichtigung" in status_alle or bes_echt:
        stufe = 2
    if hat_angebot or entwurf:
        stufe = 3
    if vertrag:
        stufe = 4
    if vertrag and _txt(vertrag["gesendet_am"]):
        stufe = 5
    besichtigung_weg = stufe >= 3 and not ("besichtigung" in status_alle or bes_echt)
    interesse_zeit = zeit_feld.get("interessiert") or next((_zeit(h["datum"]) for h in historie if str(h["status"]).lower() == "interessiert"), None)
    info = {
        0: _anz(erstellt, True),
        1: _anz(interesse_zeit, True),
        2: (f"{bes['m2']:g} m²".replace(".", ",") + (" · KG Scan" if bes["scan"] else "")) if bes_echt else ("vorgemerkt" if "besichtigung" in status_alle else ""),
        3: (f"AN-{angebot_nr or (nummern[-1]['nr'] if nummern else '')}" if hat_angebot else "Entwurf") if stufe >= 3 else "",
        4: ("gesendet" if vertrag and _txt(vertrag["gesendet_am"]) else "vorbereitet") if vertrag else "",
        5: "",
    }
    schritte = []
    for i, (key, name) in enumerate(SCHRITTE):
        if key == "besichtigung" and besichtigung_weg:
            zustand = "weg"
            text = "übersprungen"
        elif i < stufe:
            zustand, text = "ok", info.get(i) or "erledigt"
        elif i == stufe:
            zustand, text = "akt", info.get(i) or "jetzt"
        else:
            zustand, text = "offen", "noch offen"
        schritte.append({"key": key, "name": name, "zustand": zustand, "text": text})
    if stufe == 5:
        schritte[4]["zustand"] = "ok"
        schritte[5]["text"] = "Kunde anlegen"

    # ---- Nächster Schritt
    k_editor = {"text": "Angebot öffnen", "href": f"/angebot/editor?id={tid}", "art": "hell"}
    verloren = "verloren" in status_alle and stufe < 3
    tage = (jetzt.date() - angebot_zeit.date()).days if angebot_zeit else 0
    if verloren:
        naechst = {"titel": "Kein Interesse gemeldet", "text": "Die Firma hat abgesagt. Alles bleibt hier gespeichert.", "knoepfe": []}
    elif stufe == 5:
        naechst = {"titel": "Vertrag ist verschickt – Kunde anlegen", "text": "Wenn der Vertrag unterschrieben zurück ist: Kunde anlegen, feste Zeiten und Mitarbeiter eintragen.",
                   "knoepfe": [{"text": "Zu den Kunden", "href": "/kunden", "art": ""}]}
    elif stufe == 4:
        naechst = {"titel": "Vertrag prüfen und senden", "text": "Das Angebot ist angenommen. Der Vertrag ist vorbereitet und wartet.",
                   "knoepfe": [{"text": "Vertrag öffnen", "href": f"/vertrag/editor?angebot={tid}", "art": ""}]}
    elif stufe == 3 and hat_angebot:
        text = f"Seit {tage} {'Tag' if tage == 1 else 'Tagen'} keine Antwort." if tage else "Heute fertiggestellt."
        if tage >= NACHFASSEN_TAGE:
            text += " Jetzt nachfassen (anrufen oder Mail)."
        else:
            text += f" Nachfassen ab {(angebot_zeit + timedelta(days=NACHFASSEN_TAGE)).strftime('%d.%m.')} empfohlen."
        naechst = {"titel": f"Angebot AN-{angebot_nr or nummern[-1]['nr']} wartet auf Antwort", "text": text,
                   "knoepfe": [k_editor, {"text": "✓ Angenommen → Vertrag", "aktion": "angenommen", "art": "gruen"}]}
    elif stufe == 3:
        naechst = {"titel": "Angebot fertigstellen und senden", "text": "Der Entwurf ist gespeichert.", "knoepfe": [k_editor]}
    elif stufe == 2 and bes_echt:
        naechst = {"titel": "Angebot erstellen", "text": "Die Besichtigung ist gespeichert.",
                   "knoepfe": [{"text": "Angebot erstellen", "href": f"/angebot/editor?id={tid}", "art": ""}]}
    elif stufe == 2:
        naechst = {"titel": "Besichtigung durchführen", "text": "Die Firma ist für die Besichtigung vorgemerkt.",
                   "knoepfe": [{"text": "Zur Besichtigung", "href": "/besichtigung", "art": ""}]}
    elif stufe == 1:
        naechst = {"titel": "Besichtigungstermin vereinbaren", "text": "Die Firma hat Interesse.",
                   "knoepfe": [{"text": "Zur Datenbank", "href": "/datenbank", "art": "hell"}]}
    else:
        naechst = {"titel": "Interesse klären", "text": "Noch kein Ergebnis.", "knoepfe": [{"text": "Zur Datenbank", "href": "/datenbank", "art": "hell"}]}

    if stufe == 3 and hat_angebot and not vertrag and angebot_zeit:
        nf = angebot_zeit + timedelta(days=NACHFASSEN_TAGE)
        if nf > jetzt:
            eintrag("plan", nf, "Nachfassen empfohlen", "Falls bis dahin keine Antwort: anrufen oder Mail schicken.", "", [{"text": "Hinweis", "farbe": ""}], True)

    # ---- Mails (Übersicht)
    mail_liste = []
    for v in verlauf:
        if v["art"] == "mail":
            mail_liste.append({"titel": v["titel"].replace("Mail gesendet: ", ""), "zeit": v["zeit_text"][:10], "status": "gesendet"})
    if vertrag and _txt(vertrag["gesendet_am"]) and not any("vertrag" in m["titel"].lower() for m in mail_liste):
        mail_liste.append({"titel": "Vertrag", "zeit": _anz(_zeit(vertrag["gesendet_am"]), True), "status": "gesendet"})
    if hat_angebot:
        if angebot_mail:
            for m in mail_liste:
                if "angebot" in m["titel"].lower() and stufe == 3:
                    m["status"] = "wartet"
        elif angebot_zeit and mail_seit and angebot_zeit >= mail_seit:
            mail_liste.append({"titel": f"Angebot AN-{angebot_nr or nummern[-1]['nr']}", "zeit": "", "status": "fehlt"})
    if vertrag and not _txt(vertrag["gesendet_am"]):
        mail_liste.append({"titel": "Vertrag", "zeit": "", "status": "offen"})

    verlauf.sort(key=lambda v: v["_s"], reverse=True)
    for v in verlauf:
        v.pop("_s", None)

    positionen = []
    for p in (ef.get("positionen") or [])[:4]:
        if isinstance(p, dict) and (_txt(p.get("text")) or _zahl(p.get("preis"))):
            positionen.append({"text": _txt(p.get("text")), "einheit": _txt(p.get("einheit")), "preis": _euro(_zahl(p.get("preis"))) if _zahl(p.get("preis")) else ""})
    stamm = {k: feld(k) for k in ("firma", "ansprechpartner", "strasse", "plz", "ort", "telefon", "email", "website", "branche")}
    for k, ek in (("ansprechpartner", "ansprechpartner"), ("email", "email"), ("telefon", "telefon")):
        if not stamm[k] and _txt(ef.get(ek)):
            stamm[k] = _txt(ef.get(ek))
    stamm.update({"quelle": quelle, "seit": _anz(erstellt, True), "in_tagesliste": bool(zeile) and live != "angebot",
                  "leon_kampagne": next((l["kampagne_id"] for l in leon if l["kampagne_id"]), None)})
    return {
        "ok": True, "id": tid, "stamm": stamm, "schritte": schritte, "stufe": stufe, "naechst": naechst, "verlauf": verlauf,
        "mails": mail_liste, "verloren": verloren,
        "besichtigung": dict(bes, gespeichert=bes_echt, versionen=len(versionen),
                             gespeichert_am=_anz(_zeit(versionen[-1]["gespeichert_am"], utc=True)) if versionen else "") if bes else None,
        "angebot": {"nr": angebot_nr or (str(nummern[-1]["nr"]) if nummern else ""), "datum": _anz(angebot_zeit, True),
                    "netto": _euro(netto) if netto else "", "positionen": positionen, "entwurf": bool(entwurf)} if (hat_angebot or entwurf) else None,
        "vertrag": {"id": vertrag["id"], "status": vertrag["status"], "gesendet_am": _anz(_zeit(vertrag["gesendet_am"]))} if vertrag else None,
        "notizen": [{"id": n["id"], "text": n["text"], "von": n["von"] or "", "am": _anz(_zeit(n["am"]))} for n in notizen],
        "tl_notizen": tl_notizen[-8:],
    }


# ---------------------------------------------------------------- Routen

def register_kundenakte(app, login_required, get_db_connection):
    conn = get_db_connection()
    try:
        tabellen(conn)
        conn.commit()
    except Exception as exc:
        print("Kundenakte: Tabellen/Trigger nicht angelegt:", exc)
    finally:
        conn.close()

    class _Akte:
        """Für andere Module (app.extensions['kg_akte']): Besichtigung sicher lesen/schreiben, Mails merken."""
        @staticmethod
        def besichtigung(conn, tid, zeile_json=None):
            tabellen(conn)
            return besichtigung_daten(conn, tid, zeile_json)

        @staticmethod
        def kg_scan_behalten(conn, tid, data_json):
            tabellen(conn)
            return kg_scan_behalten(conn, tid, data_json)

        @staticmethod
        def sichern(conn, tid, data_json, quelle):
            tabellen(conn)
            return sichern(conn, tid, data_json, quelle)

        @staticmethod
        def mail(an, betreff, anhaenge, gmail_id):
            return mail_merken(get_db_connection, an, betreff, anhaenge, gmail_id)

    app.extensions["kg_akte"] = _Akte

    @app.route("/akte/<int:tid>")
    @login_required
    def kundenakte_seite(tid):
        return render_template("kundenakte.html", tid=tid)

    @app.route("/api/akte/<int:tid>")
    @login_required
    def kundenakte_daten(tid):
        conn = get_db_connection()
        try:
            a = akte(conn, tid)
            conn.commit()
            if not a:
                return jsonify({"ok": False, "error": "Diese Firma wurde nicht gefunden."}), 404
            return jsonify(a)
        finally:
            conn.close()

    @app.route("/api/akte/<int:tid>/notiz", methods=["POST"])
    @login_required
    def kundenakte_notiz(tid):
        d = request.get_json(silent=True) or {}
        text = str(d.get("text") or "").strip()[:2000]
        von = _txt(d.get("von"))[:40]
        if not text:
            return jsonify({"ok": False, "error": "Notiz ist leer."}), 400
        conn = get_db_connection()
        try:
            tabellen(conn)
            conn.execute("INSERT INTO akte_notizen (tagesliste_id, text, von, am) VALUES (?, ?, ?, ?)", (tid, text, von, _jetzt()))
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()
