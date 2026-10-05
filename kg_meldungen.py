# =====================================================
# KG MELDUNGEN – Einstellungen und WhatsApp an den Chef
# Kampagnen-Berichte und die tägliche Arbeitsliste können zusätzlich als
# kurze WhatsApp-Nachricht an EINE Nummer gehen (die Nummer des Chefs).
# Gesendet wird über den bestehenden WhatsApp-Connector (Tabelle
# whatsapp_outbox) – dieselbe Leitung wie alle anderen Nachrichten.
#
# Standard: keine Nummer → es wird nichts per WhatsApp gesendet.
# =====================================================
import json
import re
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    _BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # pragma: no cover
    _BERLIN = None


def heute_berlin():
    """Heutiges Datum in Deutschland (der Server läuft in UTC)."""
    return (datetime.now(_BERLIN) if _BERLIN else datetime.now()).date()

STANDARD = {
    "whatsapp_nummer": "",      # Chef-Nummer, z. B. 0176 1234567 (mehrere mit Komma: erste bekommt die Nachrichten)
    "crm_url": "",              # Adresse des CRM für Links in Nachrichten (wird beim Speichern selbst erkannt)
    "bericht_whatsapp": True,   # Kampagnen-Bericht per WhatsApp
    "tagesliste_an": True,      # Arbeitsliste jeden Werktag als To-Do-Karte
    "tagesliste_whatsapp": True,
    "tagesliste_uhrzeit": "07:30",
    "agent_antwortet": True,    # Nachrichten von der Chef-Nummer beantwortet der Agent
}


def ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_meldungen_einstellungen (
            schluessel TEXT PRIMARY KEY,
            wert TEXT
        )
    """)
    conn.commit()


def einstellungen(conn):
    ensure_tables(conn)
    e = dict(STANDARD)
    for r in conn.execute("SELECT schluessel, wert FROM kg_meldungen_einstellungen"):
        if r[0] in STANDARD:
            try:
                e[r[0]] = json.loads(r[1])
            except (TypeError, ValueError):
                pass
    return e


def _uhrzeit(text):
    m = re.match(r"^\s*(\d{1,2})[:.](\d{2})\s*$", str(text or ""))
    if not m or not (5 <= int(m.group(1)) <= 11) or int(m.group(2)) > 59:
        raise ValueError("Uhrzeit für die Arbeitsliste bitte zwischen 05:00 und 11:59.")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def speichern(conn, daten, crm_url=""):
    e = einstellungen(conn)
    neu = dict(e)
    if "whatsapp_nummer" in daten:
        nummern = [n for n in (nummer_sauber(x) for x in str(daten.get("whatsapp_nummer") or "").split(",")) if n]
        if str(daten.get("whatsapp_nummer") or "").strip() and not nummern:
            raise ValueError("Bitte eine gültige Handynummer eintragen (z. B. 0176 1234567).")
        neu["whatsapp_nummer"] = ", ".join(nummern)
    for k in ("bericht_whatsapp", "tagesliste_an", "tagesliste_whatsapp", "agent_antwortet"):
        if k in daten:
            neu[k] = bool(daten.get(k))
    if "tagesliste_uhrzeit" in daten:
        neu["tagesliste_uhrzeit"] = _uhrzeit(daten.get("tagesliste_uhrzeit"))
    if crm_url:
        neu["crm_url"] = crm_url.rstrip("/")
    ensure_tables(conn)
    for k, v in neu.items():
        conn.execute("INSERT INTO kg_meldungen_einstellungen (schluessel, wert) VALUES (?, ?) "
                     "ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert", (k, json.dumps(v)))
    conn.commit()
    return neu


def nummer_sauber(text):
    """0176 123… / +49 176 … / 0049 … → 49176… (nur Ziffern, wie der Connector sie braucht)."""
    z = re.sub(r"\D", "", str(text or ""))
    if z.startswith("00"):
        z = z[2:]
    elif z.startswith("0"):
        z = "49" + z[1:]
    return z if 10 <= len(z) <= 15 else ""


def chef_nummern(conn):
    return [n.strip() for n in str(einstellungen(conn).get("whatsapp_nummer") or "").split(",") if n.strip()]


def ist_chef(conn, *kennungen):
    """True, wenn eine der Absender-Kennungen (Telefon, from-ID) zur Chef-Nummer gehört."""
    nummern = set(chef_nummern(conn))
    if not nummern:
        return False
    for k in kennungen:
        z = re.sub(r"\D", "", str(k or "").split("@")[0])
        if z and (z in nummern or nummer_sauber(z) in nummern):
            return True
    return False


def link(conn, pfad):
    basis = str(einstellungen(conn).get("crm_url") or "").rstrip("/")
    return basis + pfad if basis else ""


def whatsapp_an_chef(conn, text, quelle="kg_meldung"):
    """Nachricht in die Warteschlange des WhatsApp-Connectors legen. Gibt True zurück, wenn eingereiht."""
    nummern = chef_nummern(conn)
    if not nummern or not str(text or "").strip():
        return False
    try:
        from whatsapp_connector_routes import wa_conn, wa_ensure_tables
        wa_ensure_tables()
        wc = wa_conn()
        try:
            wc.execute("INSERT INTO whatsapp_outbox (phone, text, status, source) VALUES (?, ?, 'pending', ?)",
                       (nummern[0], str(text).strip()[:3500], quelle))
            wc.commit()
        finally:
            wc.close()
        return True
    except Exception as exc:  # WhatsApp darf nie etwas anderes stören
        print("[KG-MELDUNGEN] WhatsApp nicht eingereiht:", exc)
        return False


def todo_anlegen(conn, titel, beschreibung, link_pfad, quelle, prioritaet="normal", kategorie="Leon"):
    """To-Do-Karte im CRM (Tabelle todos, wie /todo). link_pfad steht in ai_note (Knopf „Öffnen“)."""
    try:
        from kg_todo_routes import ensure_todo_tables
        ensure_todo_tables(conn)
    except Exception:
        pass
    vorhanden = conn.execute("SELECT id FROM todos WHERE source = ? AND ai_note = ? LIMIT 1", (quelle, link_pfad)).fetchone()
    if vorhanden:
        return vorhanden[0]
    cur = conn.execute("""
        INSERT INTO todos (task, description, deadline, category, priority, status, period_type, source, created_by,
                           ai_note, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'open', 'once', ?, 'KG Agent', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
    """, (titel[:300], beschreibung, heute_berlin().isoformat(), kategorie, prioritaet, quelle, link_pfad))
    conn.commit()
    return cur.lastrowid


def todo_erledigt(conn, quelle, link_pfad, erledigt=True):
    try:
        if erledigt:
            conn.execute("UPDATE todos SET done = 1, status = 'done', completed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
                         "WHERE source = ? AND ai_note = ? AND COALESCE(done, 0) = 0", (quelle, link_pfad))
        else:
            conn.execute("UPDATE todos SET done = 0, status = 'open', completed_at = NULL, updated_at = CURRENT_TIMESTAMP "
                         "WHERE source = ? AND ai_note = ? AND COALESCE(done, 0) = 1", (quelle, link_pfad))
        conn.commit()
    except Exception:
        pass
