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

import angebot_editor as ae

BERLIN = ZoneInfo("Europe/Berlin")
LEER = ("", "{}", "null")
STATUS_NAME = {"angerufen": "Angerufen", "interessiert": "Interesse", "besichtigung": "Für Besichtigung vorgemerkt",
               "kontaktformular": "Kontaktformular", "spaeter": "Später wieder anrufen", "verloren": "Kein Interesse"}
SCHRITTE = [("kontakt", "Kontakt"), ("interesse", "Interesse"), ("besichtigung", "Besichtigung"),
            ("angebot", "Angebot"), ("vertrag", "Vertrag"), ("kunde", "Kunde")]
NACHFASSEN_TAGE = 7
# Ältere Angebote ohne Antwort kommen nicht mehr in die Arbeitsliste (bleiben in der Kundenakte sichtbar) – sonst stünden
# alte Angebote aus der Zeit vor der Kundenakte (auch längst erledigte) jeden Morgen auf der Liste.
NACHFASSEN_FENSTER = 30
ABSAGE_GRUENDE = ("Zu teuer", "Andere Firma beauftragt", "Kein Bedarf mehr", "Keine Antwort", "Sonstiges")
NACHFASS_WEGE = ("Anruf", "Mail", "WhatsApp", "Vor Ort")


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
    if "von" not in _spalten(conn, "akte_ereignisse"):  # wer „Abgelehnt“ / „Nachgefasst“ eingetragen hat
        try:
            conn.execute("ALTER TABLE akte_ereignisse ADD COLUMN von TEXT")
        except Exception:
            pass  # ein zweiter Prozess war schneller
    conn.execute("CREATE TABLE IF NOT EXISTS akte_meta (schluessel TEXT PRIMARY KEY, wert TEXT)")
    conn.execute("""CREATE TABLE IF NOT EXISTS akte_kunde (
        tagesliste_id INTEGER PRIMARY KEY, kunde_id INTEGER NOT NULL, art TEXT, am TEXT NOT NULL, von TEXT)""")
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


# ---------------------------------------------------------------- Nach dem Angebot: angenommen / abgelehnt / nachfassen
# „Abgelehnt“ und „Nachgefasst“ stehen nur im Protokoll der Kundenakte (akte_ereignisse). Tagesliste, Angebot und Status
# der Firma bleiben unverändert. Ein neues Angebot (neue Nummer) beginnt eine neue Runde: ältere Einträge zählen dann nicht.

def _angebot_zeit(conn, tid, zeile):
    """Wann das aktuelle Angebot fertig wurde: zuletzt vergebene Nummer, sonst Angebotsdatum der Zeile."""
    z = None
    if _hat_tabelle(conn, "angebot_nummern"):
        r = conn.execute("SELECT vergeben_am FROM angebot_nummern WHERE tagesliste_id = ? ORDER BY id DESC LIMIT 1", (tid,)).fetchone()
        z = _zeit(r[0]) if r else None
    if not z and zeile is not None and "angebot_datum" in zeile.keys():
        z = _zeit(zeile["angebot_datum"])
    return z


def angebot_lage(conn, tid, angebot_zeit, vertrag=False, jetzt=None):
    """Stand nach dem Angebot. Nachfassen ist fällig 7 Tage nach dem Angebot bzw. nach dem letzten Nachfassen."""
    jetzt = jetzt or datetime.now(BERLIN)
    ab = angebot_zeit or datetime(2000, 1, 1, tzinfo=BERLIN)
    absage, nachgefasst, anzahl = None, None, 0
    for e in conn.execute("SELECT art, titel, text, von, am FROM akte_ereignisse WHERE tagesliste_id = ? "
                          "AND art IN ('angebot_abgelehnt', 'angebot_wieder_offen', 'nachgefasst') ORDER BY id", (tid,)).fetchall():
        z = _zeit(e["am"])
        if not z or z < ab:
            continue
        if e["art"] == "angebot_abgelehnt":
            absage = {"grund": _txt(e["titel"]), "text": _txt(e["text"]), "von": _txt(e["von"]), "am": z}
        elif e["art"] == "angebot_wieder_offen":
            absage = None
        else:
            nachgefasst = {"wie": _txt(e["titel"]), "text": _txt(e["text"]), "von": _txt(e["von"]), "am": z}
            anzahl += 1
    basis = max([d for d in (angebot_zeit, nachgefasst["am"] if nachgefasst else None) if d], default=None)
    tage = (jetzt.date() - basis.date()).days if basis else 0
    status = "angenommen" if vertrag else ("abgelehnt" if absage else "offen")
    return {"status": status, "tage": tage, "faellig": status == "offen" and basis is not None and tage >= NACHFASSEN_TAGE,
            "faellig_am": basis + timedelta(days=NACHFASSEN_TAGE) if basis else None, "absage": absage, "nachgefasst": nachgefasst,
            "nachgefasst_anzahl": anzahl, "alter": (jetzt.date() - angebot_zeit.date()).days if angebot_zeit else 0}


def _lage_json(l):
    a, n = l["absage"], l["nachgefasst"]
    return {"status": l["status"], "tage": l["tage"], "faellig": l["faellig"], "alter": l["alter"],
            "faellig_am": l["faellig_am"].strftime("%d.%m.%Y") if l["faellig_am"] else "",
            "grund": a["grund"] if a else "", "grund_text": a["text"] if a else "", "absage_am": _anz(a["am"]) if a else "",
            "nachgefasst_am": _anz(n["am"]) if n else "", "nachgefasst_wie": n["wie"] if n else "", "nachgefasst_anzahl": l["nachgefasst_anzahl"]}


def _offenes_angebot(conn, tid):
    """Zeile + Zeitpunkt, wenn die Firma ein verschicktes Angebot ohne Vertrag hat (sonst None)."""
    if not _hat_tabelle(conn, "tagesliste_leads") or not {"angebot_nr", "angebot_datum"} <= _spalten(conn, "tagesliste_leads"):
        return None
    zeile = conn.execute("SELECT id, firma, ort, telefon, status, angebot_nr, angebot_datum, angebot_netto FROM tagesliste_leads WHERE id = ?",
                         (tid,)).fetchone()
    if not zeile or str(zeile["status"] or "").lower() != "angebot" or not _txt(zeile["angebot_nr"]):
        return None
    vertrag = _hat_tabelle(conn, "vertrag_editor") and bool(conn.execute("SELECT 1 FROM vertrag_editor WHERE tagesliste_id = ?", (tid,)).fetchone())
    vertrag = vertrag or bool(_hat_tabelle(conn, "akte_kunde") and conn.execute("SELECT 1 FROM akte_kunde WHERE tagesliste_id = ?", (tid,)).fetchone())
    return zeile, _angebot_zeit(conn, tid, zeile), vertrag


def lage_kurz(conn, tid):
    """Für die Angebot-Liste im Editor: wartet / Nachfassen fällig / abgelehnt / angenommen."""
    tabellen(conn)
    o = _offenes_angebot(conn, tid)
    return _lage_json(angebot_lage(conn, tid, o[1], o[2])) if o else None


def ereignis_merken(conn, tid, art, titel, text, von):
    conn.execute("INSERT INTO akte_ereignisse (tagesliste_id, art, titel, text, an, am, ref, von) VALUES (?, ?, ?, ?, '', ?, '', ?)",
                 (tid, art, _txt(titel)[:120], str(text or "").strip()[:1000], _jetzt(), _txt(von)[:40]))


def nachgefasst_merken(conn, tid, wie="Anruf", text="", von=""):
    """„Nachgefasst“ eintragen (Kundenakte, Arbeitsliste, WhatsApp „3 erledigt“). Gibt die Firma zurück, sonst None."""
    tabellen(conn)
    o = _offenes_angebot(conn, tid)
    if not o or o[2] or angebot_lage(conn, tid, o[1])["status"] == "abgelehnt":
        return None
    ereignis_merken(conn, tid, "nachgefasst", wie if wie in NACHFASS_WEGE else "Anruf", text, von)
    conn.commit()
    return _txt(o[0]["firma"]) or "Ohne Firma"


def nachfassen_liste(conn, tag=None):
    """Arbeitsliste (/heute + WhatsApp am Morgen): Angebote ohne Antwort, deren 7 Tage um sind – ohne Vertrag, ohne Absage,
    nicht älter als NACHFASSEN_FENSTER Tage. Die frischesten zuerst."""
    tabellen(conn)
    if not _hat_tabelle(conn, "tagesliste_leads") or not {"angebot_nr", "angebot_datum"} <= _spalten(conn, "tagesliste_leads"):
        return []
    jetzt = datetime.now(BERLIN)
    if tag and tag != jetzt.date():
        jetzt = datetime(tag.year, tag.month, tag.day, 23, 59, tzinfo=BERLIN)
    aus = []
    for r in conn.execute("SELECT id FROM tagesliste_leads WHERE status = 'angebot' AND COALESCE(angebot_nr, '') != '' ORDER BY id DESC").fetchall():
        o = _offenes_angebot(conn, r["id"])
        if not o or o[2] or not o[1]:
            continue
        zeile, az, _ = o
        l = angebot_lage(conn, r["id"], az, False, jetzt)
        if not l["faellig"] or l["alter"] > NACHFASSEN_FENSTER:
            continue
        netto = _zahl(zeile["angebot_netto"])
        teile = [f"seit {l['alter']} Tagen keine Antwort"]
        if l["nachgefasst"]:
            teile.append(f"zuletzt nachgefasst {l['nachgefasst']['am'].strftime('%d.%m.')} ({l['nachgefasst']['wie']})")
        if netto:
            teile.append(_euro(netto) + " netto")
        aus.append({"id": r["id"], "zeit": "", "titel": f"{_txt(zeile['firma']) or 'Ohne Firma'} · AN-{_txt(zeile['angebot_nr'])}",
                    "sub": " · ".join(teile), "telefon": _txt(zeile["telefon"]), "link": f"/akte/{r['id']}", "art": "nachfassen", "_t": l["tage"]})
    aus.sort(key=lambda x: x.pop("_t"))
    return aus


# ---------------------------------------------------------------- Doppelte Firmen erkennen (Kunde anlegen, Schnell-Angebot)

_RECHTSFORM = re.compile(r"\b(gmbh|mbh|ug|ag|kg|ohg|gbr|ek|e k|co|und|haftungsbeschraenkt|inh|inhaber)\b")


def _norm_firma(t):
    t = _txt(t).lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"), ("&", " ")):
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return " ".join(_RECHTSFORM.sub(" ", t).split())


def _norm_tel(t):
    d = re.sub(r"\D", "", str(t or ""))
    if d.startswith("0049"):
        d = "0" + d[4:]
    elif d.startswith("49") and len(d) > 10:
        d = "0" + d[2:]
    return d[-9:] if len(d) >= 7 else ""


def _norm_mail(t):
    t = _txt(t).lower()
    return t if "@" in t else ""


def doppelte(conn, firma, telefon="", email="", ohne=None):
    """Gibt es die Firma schon? Kunden, Tagesliste und Status-Sicherung – gleicher Name (ohne GmbH usw.),
    gleiche Telefonnummer oder gleiche E-Mail. Nur lesen."""
    nf, nt, nm = _norm_firma(firma), _norm_tel(telefon), _norm_mail(email)
    if len(nf) < 3:
        nf = ""
    if not (nf or nt or nm):
        return []

    def gruende(f, t, m):
        g = []
        if nf and _norm_firma(f) == nf:
            g.append("gleicher Name")
        if nt and _norm_tel(t) == nt:
            g.append("gleiche Telefonnummer")
        if nm and _norm_mail(m) == nm:
            g.append("gleiche E-Mail")
        return g

    aus, gesehen = [], set()
    if _hat_tabelle(conn, "kunden"):
        for r in conn.execute("SELECT id, firma, ort, telefon, email, kundennummer, vertragsstatus FROM kunden ORDER BY id DESC").fetchall():
            g = gruende(r["firma"], r["telefon"], r["email"])
            if g:
                aus.append({"art": "kunde", "id": r["id"], "firma": _txt(r["firma"]), "ort": _txt(r["ort"]), "gruende": g,
                            "stand": "Kunde" + (f" · Kd.-Nr. {_txt(r['kundennummer'])}" if _txt(r["kundennummer"]) else "")
                                     + (" · gekündigt" if _txt(r["vertragsstatus"]).lower().startswith("gek") else "")})
    if _hat_tabelle(conn, "tagesliste_leads"):
        sp = _spalten(conn, "tagesliste_leads")
        nr = "angebot_nr" if "angebot_nr" in sp else "''"
        for r in conn.execute(f"SELECT id, firma, ort, telefon, email, status, {nr} AS nr FROM tagesliste_leads ORDER BY id DESC").fetchall():
            if r["id"] == ohne:
                continue
            g = gruende(r["firma"], r["telefon"], r["email"])
            if g:
                gesehen.add(r["id"])
                st = str(r["status"] or "offen").lower()
                art = st if st in ("besichtigung", "angebot") else "lead"
                stand = {"besichtigung": "Besichtigung", "angebot": "Angebot" + (f" AN-{_txt(r['nr'])}" if _txt(r["nr"]) else "")}.get(st, "Datenbank")
                aus.append({"art": art, "id": r["id"], "firma": _txt(r["firma"]), "ort": _txt(r["ort"]), "gruende": g, "stand": stand})
    if _hat_tabelle(conn, "tagesliste_status_backup"):
        for r in conn.execute("SELECT tagesliste_id AS id, firma, ort, telefon, email, status FROM tagesliste_status_backup ORDER BY backup_am DESC").fetchall():
            if r["id"] in gesehen or r["id"] == ohne:
                continue
            gesehen.add(r["id"])
            g = gruende(r["firma"], r["telefon"], r["email"])
            if g:
                st = str(r["status"] or "").lower()
                aus.append({"art": "besichtigung" if st == "besichtigung" else "lead", "id": r["id"], "firma": _txt(r["firma"]), "ort": _txt(r["ort"]),
                            "gruende": g, "stand": "Datenbank · " + STATUS_NAME.get(st, st or "Lead")})
    return aus[:12]


# ---------------------------------------------------------------- Vertrag → Kunde

HAEUFIGKEITEN = ("1x w", "2x w", "3x w", "4x w", "täglich", "6x w", "1x mtl", "alle 2 Monate", "alle 3 Monate", "1x jährlich", "2x jährlich", "einmalig")
_WOCHENTAGE = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag")


def _haeufigkeit(wochentage, bes_text):
    """„Dienstag und Freitag“ → 2x w; sonst aus der Besichtigung („2x wöchentlich“).
    „Freitag oder Samstag“ / „Freitag/Samstag“ ist nur ein Einsatz (Mo–Do + Fr oder Sa → 5 Tage → täglich)."""
    tage = _txt(wochentage).lower()
    tag = "(" + "|".join(_WOCHENTAGE) + ")"
    while True:
        kurz = re.sub(tag + r"\s*(?:/|\boder\b)\s*" + tag, r"\1", tage)
        if kurz == tage:
            break
        tage = kurz
    n = sum(1 for t in _WOCHENTAGE if t in tage)
    if n:
        return {5: "täglich", 6: "6x w", 7: "täglich"}.get(n, f"{n}x w")
    t = _txt(bes_text).lower()
    m = re.search(r"(\d)\s*x\s*w", t)
    if m and f"{m.group(1)}x w" in HAEUFIGKEITEN:
        return f"{m.group(1)}x w"
    if "täglich" in t or "taeglich" in t:
        return "täglich"
    if "monat" in t:
        return "1x mtl"
    return "2x w"


def kunde_link(conn, tid):
    """Verknüpfter Kunde (nur wenn es ihn in der Kundenliste noch gibt)."""
    r = conn.execute("SELECT kunde_id, art, am, von FROM akte_kunde WHERE tagesliste_id = ?", (tid,)).fetchone()
    if not r or not _hat_tabelle(conn, "kunden"):
        return None
    k = conn.execute("SELECT id, firma, kundennummer, vertrag_beginn FROM kunden WHERE id = ?", (r["kunde_id"],)).fetchone()
    if not k:
        return None
    return {"id": k["id"], "firma": _txt(k["firma"]), "kundennummer": _txt(k["kundennummer"]), "beginn": _txt(k["vertrag_beginn"]),
            "art": r["art"] or "", "am": _zeit(r["am"]), "von": _txt(r["von"])}


def kunde_vorschlag(conn, tid):
    """Felder für „Kunde anlegen“ aus Vertrag, Angebot und Besichtigung (nur lesen)."""
    zeile = conn.execute("SELECT * FROM tagesliste_leads WHERE id = ?", (tid,)).fetchone() if _hat_tabelle(conn, "tagesliste_leads") else None
    backup = conn.execute("SELECT * FROM tagesliste_status_backup WHERE tagesliste_id = ? ORDER BY backup_am DESC LIMIT 1", (tid,)).fetchone() \
        if _hat_tabelle(conn, "tagesliste_status_backup") else None
    vertrag = conn.execute("SELECT * FROM vertrag_editor WHERE tagesliste_id = ?", (tid,)).fetchone() if _hat_tabelle(conn, "vertrag_editor") else None
    if not zeile and not backup and not vertrag:
        return None
    vf = _json(vertrag["felder_json"]) if vertrag else {}

    def wert(feld, spalte=None):
        spalte = spalte or feld
        if _txt(vf.get(feld)):
            return _txt(vf.get(feld))
        for q in (zeile, backup):
            if q is not None and spalte in q.keys() and _txt(q[spalte]):
                return _txt(q[spalte])
        return ""

    netto = 0.0
    for p in vf.get("positionen") or []:
        if isinstance(p, dict) and _txt(p.get("einheit")).lower().startswith("monat"):
            netto += _zahl(p.get("netto")) or round(_zahl(p.get("preis")) / 1.19, 2)
    if not netto and zeile is not None and "angebot_netto" in zeile.keys():
        netto = _zahl(zeile["angebot_netto"])
    name = wert("ansprechpartner")
    anrede = next((a for a in ("Herr", "Frau") if name.startswith(a + " ")), "")
    bes = _zusammenfassung(besichtigung_daten(conn, tid, zeile["besichtigung_data_json"] if zeile is not None and "besichtigung_data_json" in zeile.keys() else None) or {})
    knr = ""
    if _hat_tabelle(conn, "kunden"):
        r = conn.execute("SELECT kundennummer FROM kunden WHERE kundennummer GLOB '[0-9]*' ORDER BY CAST(kundennummer AS INTEGER) DESC LIMIT 1").fetchone()
        knr = str(int(r[0]) + 1) if r and str(r[0] or "").isdigit() else "10002"
    return {"firma": wert("firma"), "strasse": wert("adresse", "strasse"), "plz": wert("plz"), "stadt": wert("ort"), "anrede": anrede, "name": name,
            "telefon": wert("telefon"), "email": wert("email"), "rechnung_email": "", "kundennummer": knr, "beginn": _txt(vf.get("startdatum")),
            "betrag": f"{netto:.2f}" if netto else "", "haeufigkeit": _haeufigkeit(vf.get("wochentage"), bes.get("haeufigkeit")),
            "wochentage": _txt(vf.get("wochentage")), "zeiten": _txt(vf.get("zeiten"))}


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
    kunde = kunde_link(conn, tid)
    leon = conn.execute("SELECT * FROM leon_links WHERE tagesliste_id = ? ORDER BY aktualisiert_am DESC", (tid,)).fetchall() \
        if _hat_tabelle(conn, "leon_links") else []
    historie = conn.execute("SELECT status, datum FROM tagesliste_status_history WHERE tagesliste_id = ? ORDER BY datum", (tid,)).fetchall() \
        if _hat_tabelle(conn, "tagesliste_status_history") else []
    tl_notiz = conn.execute("SELECT notiz, geaendert_am FROM akte_tl_notiz WHERE tagesliste_id = ?", (tid,)).fetchone()
    notizen = conn.execute("SELECT id, text, von, am FROM akte_notizen WHERE tagesliste_id = ? ORDER BY id DESC", (tid,)).fetchall()
    mails = conn.execute("SELECT titel, text, an, am FROM akte_ereignisse WHERE tagesliste_id = ? AND art = 'mail' ORDER BY id", (tid,)).fetchall()
    nach_angebot = conn.execute("SELECT art, titel, text, von, am FROM akte_ereignisse WHERE tagesliste_id = ? "
                                "AND art IN ('angebot_abgelehnt', 'angebot_wieder_offen', 'nachgefasst') ORDER BY id", (tid,)).fetchall()
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
    if kunde:
        eintrag("kunde", kunde["am"], ("Kunde angelegt" if kunde["art"] == "neu" else "Mit Kunde verknüpft")
                + (f" · Kd.-Nr. {kunde['kundennummer']}" if kunde["kundennummer"] else ""), kunde["firma"], kunde["von"], [{"text": "✓ Kundenliste", "farbe": "g"}])
    for n in notizen:
        eintrag("notiz", _zeit(n["am"]), "Notiz", _txt(n["text"]), _txt(n["von"]))
    for e in nach_angebot:
        if e["art"] == "angebot_abgelehnt":
            eintrag("verloren", _zeit(e["am"]), "Angebot abgelehnt", _txt(e["text"]), _txt(e["von"]), [{"text": _txt(e["titel"]) or "ohne Grund", "farbe": "r"}])
        elif e["art"] == "angebot_wieder_offen":
            eintrag("angebot", _zeit(e["am"]), "Absage zurückgenommen – Angebot wieder offen", "", _txt(e["von"]))
        else:
            eintrag("nachgefasst", _zeit(e["am"]), "Nachgefasst per " + (_txt(e["titel"]) or "Anruf"), _txt(e["text"]), _txt(e["von"]))

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
    if kunde:
        stufe = 6
    besichtigung_weg = stufe >= 3 and not ("besichtigung" in status_alle or bes_echt)
    interesse_zeit = zeit_feld.get("interessiert") or next((_zeit(h["datum"]) for h in historie if str(h["status"]).lower() == "interessiert"), None)
    info = {
        0: _anz(erstellt, True),
        1: _anz(interesse_zeit, True),
        2: (f"{bes['m2']:g} m²".replace(".", ",") + (" · KG Scan" if bes["scan"] else "")) if bes_echt else ("vorgemerkt" if "besichtigung" in status_alle else ""),
        3: (f"AN-{angebot_nr or (nummern[-1]['nr'] if nummern else '')}" if hat_angebot else "Entwurf") if stufe >= 3 else "",
        4: ("gesendet" if vertrag and _txt(vertrag["gesendet_am"]) else "vorbereitet") if vertrag else "",
        5: (f"Kd.-Nr. {kunde['kundennummer']}" if kunde["kundennummer"] else "angelegt") if kunde else "",
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
    if kunde and not vertrag:  # ohne Vertrag-Editor verknüpft: Vertrag nicht als erledigt vortäuschen
        schritte[4].update(zustand="weg", text="ohne Vertrag-Editor")
    lage = angebot_lage(conn, tid, angebot_zeit, bool(vertrag), jetzt) if hat_angebot else None
    abgelehnt = bool(lage and lage["status"] == "abgelehnt" and stufe == 3)
    if abgelehnt:
        schritte[3].update(zustand="nein", text="abgelehnt")

    # ---- Nächster Schritt
    k_editor = {"text": "Angebot öffnen", "href": f"/angebot/editor?id={tid}", "art": "hell"}
    verloren = "verloren" in status_alle and stufe < 3
    tage = (jetzt.date() - angebot_zeit.date()).days if angebot_zeit else 0
    if kunde:
        naechst = {"titel": f"Kunde angelegt" + (f" – Kd.-Nr. {kunde['kundennummer']}" if kunde["kundennummer"] else ""),
                   "text": "Jetzt in der Kundenliste feste Zeiten und Mitarbeiter eintragen.",
                   "knoepfe": [{"text": "Zu den Kunden", "href": "/kunden", "art": ""}]}
    elif verloren:
        naechst = {"titel": "Kein Interesse gemeldet", "text": "Die Firma hat abgesagt. Alles bleibt hier gespeichert.", "knoepfe": []}
    elif stufe == 5:
        naechst = {"titel": "Vertrag ist verschickt – Kunde anlegen", "text": "Wenn der Vertrag unterschrieben zurück ist: Kunde anlegen, feste Zeiten und Mitarbeiter eintragen.",
                   "knoepfe": [{"text": "Kunde anlegen", "aktion": "kunde", "art": "gruen"}]}
    elif stufe == 4:
        naechst = {"titel": "Vertrag prüfen und senden", "text": "Das Angebot ist angenommen. Der Vertrag ist vorbereitet und wartet.",
                   "knoepfe": [{"text": "Vertrag öffnen", "href": f"/vertrag/editor?angebot={tid}", "art": ""},
                               {"text": "Kunde anlegen", "aktion": "kunde", "art": "hell"}]}
    elif abgelehnt:
        a = lage["absage"]
        naechst = {"titel": f"Angebot AN-{angebot_nr or nummern[-1]['nr']} abgelehnt",
                   "text": f"Grund: {a['grund'] or 'ohne Grund'}" + (f" – {a['text']}" if a["text"] else "") + f" (am {_anz(a['am'], True)}"
                           + (f", {a['von']}" if a["von"] else "") + "). Alles bleibt hier gespeichert.",
                   "knoepfe": [{"text": "Absage zurücknehmen", "aktion": "wieder_offen", "art": "hell"}], "rot": True}
    elif stufe == 3 and hat_angebot:
        text = f"Seit {tage} {'Tag' if tage == 1 else 'Tagen'} keine Antwort." if tage else "Heute fertiggestellt."
        if lage["nachgefasst"]:
            n = lage["nachgefasst"]
            text += f" Zuletzt nachgefasst am {n['am'].strftime('%d.%m.')} per {n['wie']}" + (f" ({n['text']})" if n["text"] else "") + "."
        if lage["faellig"]:
            text += " Jetzt nachfassen (anrufen oder Mail)." if not lage["nachgefasst"] else " Jetzt wieder nachfassen."
        elif lage["faellig_am"]:
            text += f" {'Nächstes Nachfassen' if lage['nachgefasst'] else 'Nachfassen'} ab {lage['faellig_am'].strftime('%d.%m.')} empfohlen."
        naechst = {"titel": f"Angebot AN-{angebot_nr or nummern[-1]['nr']} wartet auf Antwort", "text": text,
                   "knoepfe": [k_editor, {"text": "↻ Nachgefasst", "aktion": "nachgefasst", "art": "hell"},
                               {"text": "✕ Abgelehnt", "aktion": "abgelehnt", "art": "rot"},
                               {"text": "✓ Angenommen → Vertrag", "aktion": "angenommen", "art": "gruen"}]}
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

    if stufe == 3 and hat_angebot and not vertrag and not abgelehnt and lage["faellig_am"]:
        nf = lage["faellig_am"]
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

    kalk = None
    if entwurf:
        e = _json(entwurf["kalkulation_json"]).get("ergebnis")
        if isinstance(e, dict) and _zahl(e.get("kosten")) > 0:
            kalk = {k: _euro(_zahl(e.get(k))) for k in ("kosten", "preis", "gewinn", "richtpreis")}
            kalk["prozent"] = round(_zahl(e.get("prozent")), 1)
            kalk["stunden"] = round(_zahl(e.get("stunden")), 1)
    positionen = []
    for p in (ef.get("positionen") or [])[:8]:  # Angebot: Leistung 1–8
        if isinstance(p, dict) and (_txt(p.get("text")) or _zahl(p.get("preis"))):
            r = ae._rabatt(p.get("rabatt")) if _zahl(p.get("preis")) else 0  # Rabatt: Preis danach, mit Hinweis
            positionen.append({"text": _txt(p.get("text")), "einheit": _txt(p.get("einheit")),
                               "preis": (_euro(ae.endpreis(p)) + (f" (−{r} %)" if r else "")) if _zahl(p.get("preis")) else ""})
    stamm = {k: feld(k) for k in ("firma", "ansprechpartner", "strasse", "plz", "ort", "telefon", "email", "website", "branche")}
    for k, ek in (("ansprechpartner", "ansprechpartner"), ("email", "email"), ("telefon", "telefon")):
        if not stamm[k] and _txt(ef.get(ek)):
            stamm[k] = _txt(ef.get(ek))
    stamm.update({"quelle": quelle, "seit": _anz(erstellt, True), "in_tagesliste": bool(zeile) and live != "angebot",
                  "leon_kampagne": next((l["kampagne_id"] for l in leon if l["kampagne_id"]), None)})
    return {
        "ok": True, "id": tid, "stamm": stamm, "schritte": schritte, "stufe": stufe, "naechst": naechst, "verlauf": verlauf,
        "mails": mail_liste, "verloren": verloren, "abgelehnt": abgelehnt,
        "kunde": dict(kunde, am=_anz(kunde["am"])) if kunde else None, "haeufigkeiten": list(HAEUFIGKEITEN),
        "absage_gruende": list(ABSAGE_GRUENDE), "nachfass_wege": list(NACHFASS_WEGE),
        "besichtigung": dict(bes, gespeichert=bes_echt, versionen=len(versionen),
                             gespeichert_am=_anz(_zeit(versionen[-1]["gespeichert_am"], utc=True)) if versionen else "") if bes else None,
        "angebot": {"nr": angebot_nr or (str(nummern[-1]["nr"]) if nummern else ""), "datum": _anz(angebot_zeit, True),
                    "netto": _euro(netto) if netto else "", "positionen": positionen, "entwurf": bool(entwurf), "kalk": kalk,
                    "lage": _lage_json(lage) if lage else None} if (hat_angebot or entwurf) else None,
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

        @staticmethod
        def lage(conn, tid):
            return lage_kurz(conn, tid)

    app.extensions["kg_akte"] = _Akte

    try:  # Übersicht aller Firmen (/akte) – eigenes Modul, darf die Kundenakte nie stören
        from kundenweg import register_kundenweg
        register_kundenweg(app, login_required, get_db_connection)
    except Exception as exc:
        print("Kundenweg nicht geladen:", exc)

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

    @app.route("/api/akte/<int:tid>/besichtigung", methods=["POST"])
    @login_required
    def kundenakte_besichtigung(tid):
        """Besichtigung-Seite → Angebot: Eingaben sichern, ohne den Status der Firma zu ändern."""
        daten = (request.get_json(silent=True) or {}).get("daten")
        if not isinstance(daten, dict) or not daten:
            return jsonify({"ok": False, "error": "Keine Daten."}), 400
        text = json.dumps(daten, ensure_ascii=False)
        conn = get_db_connection()
        try:
            tabellen(conn)
            text = kg_scan_behalten(conn, tid, text)
            zeile = conn.execute("SELECT id FROM tagesliste_leads WHERE id = ?", (tid,)).fetchone()
            if zeile:
                conn.execute("UPDATE tagesliste_leads SET besichtigung_data_json = ? WHERE id = ?", (text, tid))  # Trigger sichert die Version
            else:
                sichern(conn, tid, text, "seite")
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    @app.route("/api/akte/<int:tid>/angebot", methods=["POST"])
    @login_required
    def kundenakte_angebot(tid):
        """Nach dem Angebot: „Abgelehnt“ (mit Grund), „Absage zurücknehmen“ oder „Nachgefasst“.
        Schreibt nur ins Protokoll der Kundenakte – Tagesliste, Angebot und Status der Firma bleiben unverändert."""
        d = request.get_json(silent=True) or {}
        aktion = str(d.get("aktion") or "")
        von = _txt(d.get("von"))[:40]
        text = str(d.get("text") or "").strip()[:1000]
        conn = get_db_connection()
        try:
            tabellen(conn)
            o = _offenes_angebot(conn, tid)
            if not o:
                return jsonify({"ok": False, "error": "Zu dieser Firma gibt es kein verschicktes Angebot."}), 404
            if o[2]:
                return jsonify({"ok": False, "error": "Das Angebot ist schon angenommen (Vertrag vorhanden)."}), 409
            status = angebot_lage(conn, tid, o[1])["status"]
            if aktion == "abgelehnt":
                grund = _txt(d.get("grund"))
                if grund not in ABSAGE_GRUENDE:
                    return jsonify({"ok": False, "error": "Bitte einen Grund wählen."}), 400
                if grund == "Sonstiges" and not text:
                    return jsonify({"ok": False, "error": "Bitte kurz schreiben, warum."}), 400
                if status != "abgelehnt":
                    ereignis_merken(conn, tid, "angebot_abgelehnt", grund, text, von)
            elif aktion == "wieder_offen":
                if status == "abgelehnt":
                    ereignis_merken(conn, tid, "angebot_wieder_offen", "", "", von)
            elif aktion == "nachgefasst":
                if status == "abgelehnt":
                    return jsonify({"ok": False, "error": "Das Angebot ist als abgelehnt eingetragen. Erst „Absage zurücknehmen“."}), 409
                wie = _txt(d.get("wie"))
                ereignis_merken(conn, tid, "nachgefasst", wie if wie in NACHFASS_WEGE else "Anruf", text, von)
            else:
                return jsonify({"ok": False, "error": "Unbekannte Aktion."}), 400
            conn.commit()
            return jsonify({"ok": True})
        finally:
            conn.close()

    # ---------- Vertrag → Kunde
    def _kunde_ueber_formular(form):
        """Über die bestehende Kunden-Seite speichern (POST /kunden) – gleiche Prüfung, Kundennummer und Lexware-Abgleich
        wie beim Anlegen von Hand."""
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        return client.post("/kunden", data=form).status_code

    @app.route("/api/akte/<int:tid>/kunde")
    @login_required
    def kundenakte_kunde_vorschlag(tid):
        conn = get_db_connection()
        try:
            tabellen(conn)
            f = kunde_vorschlag(conn, tid)
            if not f:
                return jsonify({"ok": False, "error": "Diese Firma wurde nicht gefunden."}), 404
            k = kunde_link(conn, tid)
            return jsonify({"ok": True, "felder": f, "haeufigkeiten": list(HAEUFIGKEITEN),
                            "verknuepft": dict(k, am=_anz(k["am"])) if k else None,
                            "doppelt": [d for d in doppelte(conn, f["firma"], f["telefon"], f["email"], ohne=tid) if d["art"] == "kunde"]})
        finally:
            conn.close()

    @app.route("/api/akte/<int:tid>/kunde", methods=["POST"])
    @login_required
    def kundenakte_kunde(tid):
        """„Kunde anlegen“ (neuer Eintrag in der Kundenliste) oder „Mit vorhandenem Kunden verknüpfen“. Doppelte Kunden werden
        gemeldet; anlegen trotzdem nur mit „trotzdem“."""
        d = request.get_json(silent=True) or {}
        von = _txt(d.get("von"))[:40]
        conn = get_db_connection()
        try:
            tabellen(conn)
            if not kunde_vorschlag(conn, tid):
                return jsonify({"ok": False, "error": "Diese Firma wurde nicht gefunden."}), 404
            schon = kunde_link(conn, tid)
            if schon:
                return jsonify({"ok": True, "kunde_id": schon["id"], "schon": True})
            if d.get("aktion") == "verknuepfen":
                kid = int(d.get("kunde_id") or 0)
                if not conn.execute("SELECT 1 FROM kunden WHERE id = ?", (kid,)).fetchone():
                    return jsonify({"ok": False, "error": "Kunde nicht gefunden."}), 404
                conn.execute("INSERT OR REPLACE INTO akte_kunde (tagesliste_id, kunde_id, art, am, von) VALUES (?, ?, 'verknuepft', ?, ?)", (tid, kid, _jetzt(), von))
                conn.commit()
                return jsonify({"ok": True, "kunde_id": kid})
            f = d.get("felder") if isinstance(d.get("felder"), dict) else {}
            form = {k: _txt(f.get(k))[:200] for k in ("firma", "strasse", "plz", "stadt", "anrede", "name", "telefon", "email", "rechnung_email",
                                                      "kundennummer", "beginn", "haeufigkeit")}
            if not form["firma"] or not form["stadt"]:
                return jsonify({"ok": False, "error": "Bitte Firma und Stadt eintragen."}), 400
            betrag = _zahl(f.get("betrag"))
            if betrag <= 0:
                return jsonify({"ok": False, "error": "Bitte den Betrag pro Monat (netto) eintragen."}), 400
            if form["haeufigkeit"] not in HAEUFIGKEITEN:
                form["haeufigkeit"] = "2x w"
            if form["anrede"] not in ("Herr", "Frau"):
                form["anrede"] = ""
            if form["kundennummer"] and conn.execute("SELECT 1 FROM kunden WHERE kundennummer = ?", (form["kundennummer"],)).fetchone():
                return jsonify({"ok": False, "error": f"Die Kundennummer {form['kundennummer']} ist schon vergeben."}), 409
            doppelt = [x for x in doppelte(conn, form["firma"], form["telefon"], form["email"], ohne=tid) if x["art"] == "kunde"]
            if doppelt and not d.get("trotzdem"):
                return jsonify({"ok": False, "doppelt": doppelt, "error": "Diesen Kunden gibt es vielleicht schon."}), 409
            vorher = conn.execute("SELECT COALESCE(MAX(id), 0) FROM kunden").fetchone()[0]
            conn.commit()
        finally:
            conn.close()
        form.update(betrag=f"{betrag:.2f}", ende="", laufzeit="unbefristet", status="aktuell", fenster_betrag="", fenster_mal="", fenster_notiz="")
        code = _kunde_ueber_formular(form)
        conn = get_db_connection()
        try:
            neu = conn.execute("SELECT id FROM kunden WHERE id > ? AND firma = ? ORDER BY id DESC LIMIT 1", (vorher, form["firma"])).fetchone()
            if not neu:
                return jsonify({"ok": False, "error": f"Kunde wurde nicht gespeichert (Kunden-Seite antwortet {code})."}), 500
            conn.execute("INSERT OR REPLACE INTO akte_kunde (tagesliste_id, kunde_id, art, am, von) VALUES (?, ?, 'neu', ?, ?)", (tid, neu["id"], _jetzt(), von))
            conn.commit()
            return jsonify({"ok": True, "kunde_id": neu["id"]})
        finally:
            conn.close()

    # ---------- Doppelte Firmen (Schnell-Angebot im Angebot-Editor)
    @app.route("/api/akte/doppelt")
    @login_required
    def kundenakte_doppelt():
        conn = get_db_connection()
        try:
            return jsonify({"ok": True, "treffer": doppelte(conn, request.args.get("firma"), request.args.get("telefon"), request.args.get("email"))})
        finally:
            conn.close()

    @app.route("/api/akte/<int:tid>/schnell", methods=["POST"])
    @login_required
    def kundenakte_schnell(tid):
        """Schnell-Angebot für eine Firma, die es schon gibt: dieselbe Firma (gleiche ID, gleiche Akte) für das Angebot vormerken,
        statt einen zweiten Eintrag anzulegen. Wie „Besichtigung“ in der Datenbank (Status-Sicherung + Verlauf)."""
        conn = get_db_connection()
        try:
            tabellen(conn)
            zeile = conn.execute("SELECT id, status FROM tagesliste_leads WHERE id = ?", (tid,)).fetchone()
            if zeile and str(zeile["status"] or "").lower() in ("besichtigung", "angebot"):
                return jsonify({"ok": True, "id": tid})
            if conn.execute("SELECT 1 FROM tagesliste_status_backup WHERE tagesliste_id = ? AND status = 'besichtigung'", (tid,)).fetchone():
                return jsonify({"ok": True, "id": tid})
            if not zeile:
                b = conn.execute("SELECT * FROM tagesliste_status_backup WHERE tagesliste_id = ? ORDER BY backup_am DESC LIMIT 1", (tid,)).fetchone()
                if not b:
                    return jsonify({"ok": False, "error": "Firma nicht gefunden."}), 404
                spalten = [c for c in b.keys() if c not in ("id", "status", "backup_am")]
                conn.execute(f"INSERT OR REPLACE INTO tagesliste_status_backup ({', '.join(spalten)}, status, backup_am) "
                             f"SELECT {', '.join(spalten)}, 'besichtigung', CURRENT_TIMESTAMP FROM tagesliste_status_backup WHERE id = ?", (b["id"],))
                conn.execute("INSERT OR IGNORE INTO tagesliste_status_history (tagesliste_id, status, datum) VALUES (?, 'besichtigung', date('now','localtime'))", (tid,))
                conn.commit()
                return jsonify({"ok": True, "id": tid})
            conn.commit()
        finally:
            conn.close()
        # Zeile ist in der Tagesliste: genau wie der Knopf „Besichtigung“ in der Datenbank
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        r = client.post("/datenbank/tagesliste-status", json={"id": tid, "status": "besichtigung"})
        d = r.get_json(silent=True) or {}
        if r.status_code != 200 or d.get("ok") is False:
            return jsonify({"ok": False, "error": d.get("message") or f"Status nicht gesetzt ({r.status_code})."}), 500
        return jsonify({"ok": True, "id": tid})

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
