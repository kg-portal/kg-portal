# =====================================================
# STUNDENZETTEL-AUTOMATIK
# Feste Arbeitszeiten je Mitarbeiter → ganzen Monat ausfüllen →
# WhatsApp an den Mitarbeiter („Stimmt das? 1 = Ja, 2 = Nein“) →
# Chef bestätigt → Monat gesperrt (Mitarbeiter-Link kann nichts mehr ändern).
#
# Bestehende Stundenzettel-Funktionen bleiben unverändert. Ausgefüllt wird
# nur an Tagen OHNE Eintrag (gleich unterschrieben ✓); automatisch angelegte
# Tage sind gemerkt und lassen sich zurücknehmen, solange sie nicht verändert wurden.
#
# WhatsApp geht über den WhatsApp-Connector (whatsapp_outbox, Damlas Diensthandy).
# Leon ruft für den Stundenzettel nicht mehr an (leon_*-Spalten bleiben nur als alte Daten).
# =====================================================
import json
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone

from flask import jsonify, render_template, request, session

WOCHENTAGE = ["mo", "di", "mi", "do", "fr", "sa", "so"]
WOCHENTAGE_LANG = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]
SONDER_ORTE = {"Urlaub", "Krank", "Feiertag"}


# ----------------------------------------------------- Feiertage NRW

def _ostern(jahr):
    a = jahr % 19
    b, c = divmod(jahr, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    monat, tag = divmod(h + l - 7 * m + 114, 31)
    return date(jahr, monat, tag + 1)


def feiertage_nrw(jahr):
    o = _ostern(jahr)
    return {
        date(jahr, 1, 1): "Neujahr",
        o - timedelta(days=2): "Karfreitag",
        o + timedelta(days=1): "Ostermontag",
        date(jahr, 5, 1): "Tag der Arbeit",
        o + timedelta(days=39): "Christi Himmelfahrt",
        o + timedelta(days=50): "Pfingstmontag",
        o + timedelta(days=60): "Fronleichnam",
        date(jahr, 10, 3): "Tag der Deutschen Einheit",
        date(jahr, 11, 1): "Allerheiligen",
        date(jahr, 12, 25): "1. Weihnachtstag",
        date(jahr, 12, 26): "2. Weihnachtstag",
    }


# ----------------------------------------------------- Hilfen

def _monat_param(value=None):
    """'YYYY-MM' → (start, ende_exklusiv, 'YYYY-MM')."""
    raw = (value or "").strip()
    try:
        jahr, monat = [int(x) for x in raw.split("-")[:2]]
        start = date(jahr, monat, 1)
    except (ValueError, TypeError):
        heute = date.today()
        start = date(heute.year, heute.month, 1)
    ende = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    return start, ende, f"{start.year:04d}-{start.month:02d}"


def _stunden(start, ende):
    try:
        h1, m1 = [int(x) for x in str(start)[:5].split(":")]
        h2, m2 = [int(x) for x in str(ende)[:5].split(":")]
    except ValueError:
        return 0.0
    minuten = (h2 * 60 + m2) - (h1 * 60 + m1)
    if minuten < 0:
        minuten += 24 * 60
    return minuten / 60.0


def _hhmm(stunden):
    ganz = int(stunden)
    minuten = round((stunden - ganz) * 60)
    if minuten == 60:
        ganz, minuten = ganz + 1, 0
    return f"{ganz}:{minuten:02d}"


def _zeit_ok(value):
    try:
        h, m = [int(x) for x in str(value).split(":")]
        return 0 <= h < 24 and 0 <= m < 60
    except ValueError:
        return False


def _leerer_plan():
    return {tag: {"aktiv": False, "start": "", "ende": "", "ort": ""} for tag in WOCHENTAGE}


def _plan_pruefen(plan):
    sauber = _leerer_plan()
    for tag in WOCHENTAGE:
        p = (plan or {}).get(tag) or {}
        aktiv = bool(p.get("aktiv"))
        start = str(p.get("start") or "").strip()[:5]
        ende = str(p.get("ende") or "").strip()[:5]
        ort = str(p.get("ort") or "").strip()[:120]
        if aktiv and (not _zeit_ok(start) or not _zeit_ok(ende) or not ort):
            raise ValueError(f"{WOCHENTAGE_LANG[WOCHENTAGE.index(tag)]}: Beginn, Ende und Objekt ausfüllen.")
        sauber[tag] = {"aktiv": aktiv, "start": start, "ende": ende, "ort": ort}
    return sauber


def ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stundenzettel_vorlagen (
            worker_id INTEGER PRIMARY KEY,
            plan_json TEXT NOT NULL,
            aktualisiert_am TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stundenzettel_monate (
            worker_id INTEGER NOT NULL,
            monat TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'offen',
            gefuellt_am TEXT,
            zusammenfassung TEXT,
            leon_lead_id INTEGER,
            leon_call_id INTEGER,
            leon_status TEXT,
            leon_ergebnis TEXT,
            leon_zusammenfassung TEXT,
            leon_transkript TEXT,
            leon_fehler TEXT,
            leon_info TEXT,
            angerufen_am TEXT,
            bestaetigt_am TEXT,
            notiz TEXT,
            PRIMARY KEY (worker_id, monat)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stundenzettel_auto_eintraege (
            worker_id INTEGER NOT NULL,
            datum TEXT NOT NULL,
            monat TEXT NOT NULL,
            start_time TEXT,
            end_time TEXT,
            place TEXT,
            PRIMARY KEY (worker_id, datum)
        )
    """)
    spalten = {r[1] for r in conn.execute("PRAGMA table_info(stundenzettel_monate)")}
    if "leon_info" not in spalten:
        conn.execute("ALTER TABLE stundenzettel_monate ADD COLUMN leon_info TEXT")
    for spalte, art in (("wa_gesendet_am", "TEXT"), ("wa_outbox_id", "INTEGER"), ("wa_antwort", "TEXT"), ("wa_antwort_am", "TEXT")):
        if spalte not in spalten:
            conn.execute(f"ALTER TABLE stundenzettel_monate ADD COLUMN {spalte} {art}")
    conn.commit()


def monat_gesperrt(conn, worker_id, datum):
    """True, wenn der Monat dieses Datums bestätigt (gesperrt) ist."""
    try:
        ensure_tables(conn)
        row = conn.execute(
            "SELECT status FROM stundenzettel_monate WHERE worker_id = ? AND monat = ?",
            (worker_id, str(datum or "")[:7]),
        ).fetchone()
    except Exception:
        return False
    return bool(row) and row["status"] == "bestaetigt"


def _plan_laden(conn, worker_id):
    row = conn.execute("SELECT plan_json FROM stundenzettel_vorlagen WHERE worker_id = ?", (worker_id,)).fetchone()
    if not row:
        return _leerer_plan()
    try:
        return _plan_pruefen(json.loads(row["plan_json"]))
    except (ValueError, TypeError):
        return _leerer_plan()


def _monat_row(conn, worker_id, monat):
    row = conn.execute("SELECT * FROM stundenzettel_monate WHERE worker_id = ? AND monat = ?", (worker_id, monat)).fetchone()
    return dict(row) if row else {"worker_id": worker_id, "monat": monat, "status": "offen"}


def _monat_speichern(conn, worker_id, monat, **felder):
    conn.execute("INSERT OR IGNORE INTO stundenzettel_monate (worker_id, monat, status) VALUES (?, ?, 'offen')", (worker_id, monat))
    if felder:
        sets = ", ".join(f"{k} = ?" for k in felder)
        conn.execute(f"UPDATE stundenzettel_monate SET {sets} WHERE worker_id = ? AND monat = ?",
                     list(felder.values()) + [worker_id, monat])


def _worker(conn, worker_id):
    return conn.execute(
        "SELECT id, vorname, nachname, telefon, eintrittsdatum, status, access_code FROM mitarbeiter WHERE id = ?",
        (worker_id,),
    ).fetchone()


def _name(w):
    return f"{w['vorname'] or ''} {w['nachname'] or ''}".strip()


def monat_zusammenfassung(conn, worker_id, monat):
    """Kurzer deutscher Text über den Monat – für die Chef-Ansicht."""
    start, ende, monat = _monat_param(monat)
    w = _worker(conn, worker_id)
    logs = conn.execute(
        "SELECT datum, start_time, end_time, place FROM work_logs WHERE worker_id = ? AND datum >= ? AND datum < ? ORDER BY datum",
        (worker_id, start.isoformat(), ende.isoformat()),
    ).fetchall()
    arbeit = [l for l in logs if (l["place"] or "") not in SONDER_ORTE]
    stunden = sum(_stunden(l["start_time"], l["end_time"]) for l in arbeit)
    muster = {}
    for l in arbeit:
        tag = WOCHENTAGE_LANG[date.fromisoformat(l["datum"]).weekday()]
        key = (l["start_time"], l["end_time"], l["place"])
        muster.setdefault(key, []).append(tag)
    teile = []
    for (s, e, ort), tage in sorted(muster.items(), key=lambda x: -len(x[1])):
        einzig = sorted(set(tage), key=WOCHENTAGE_LANG.index)
        teile.append(f"{', '.join(einzig)} {s}–{e} Uhr bei {ort} ({len(tage)}×)")
    sonder = {}
    for l in logs:
        if (l["place"] or "") in SONDER_ORTE:
            sonder.setdefault(l["place"], []).append(date.fromisoformat(l["datum"]).strftime("%d.%m."))
    feiertage = [f"{d.strftime('%d.%m.')} {n}" for d, n in sorted(feiertage_nrw(start.year).items()) if start <= d < ende]
    text = (f"Stundenzettel {MONATE[start.month - 1]} {start.year} für {_name(w) if w else worker_id}: "
            f"{len({l['datum'] for l in arbeit})} Arbeitstage, zusammen {_hhmm(stunden)} Stunden.")
    if teile:
        text += " " + "; ".join(teile) + "."
    for ort, tage in sonder.items():
        text += f" {ort}: {', '.join(tage)}."
    if feiertage:
        text += f" Feiertage (nicht eingetragen): {', '.join(feiertage)}."
    return text, round(stunden, 2)


def monat_fuellen(conn, worker_id, monat):
    """Leere Tage des Monats nach dem festen Plan füllen. Gibt Bericht zurück."""
    start, ende, monat = _monat_param(monat)
    ensure_tables(conn)
    w = _worker(conn, worker_id)
    if not w:
        raise ValueError("Mitarbeiter nicht gefunden.")
    if _monat_row(conn, worker_id, monat).get("status") == "bestaetigt":
        raise ValueError("Monat ist bereits bestätigt und gesperrt.")
    plan = _plan_laden(conn, worker_id)
    if not any(p["aktiv"] for p in plan.values()):
        raise ValueError("Für diesen Mitarbeiter sind noch keine festen Zeiten gespeichert.")
    feiertage = feiertage_nrw(start.year)
    eintritt = None
    try:
        eintritt = date.fromisoformat(str(w["eintrittsdatum"] or "")[:10])
    except ValueError:
        eintritt = None
    vorhanden = {
        r["datum"] for r in conn.execute(
            "SELECT datum FROM work_logs WHERE worker_id = ? AND datum >= ? AND datum < ?",
            (worker_id, start.isoformat(), ende.isoformat()),
        )
    }
    neu, uebersprungen = [], []
    tag = start
    while tag < ende:
        p = plan[WOCHENTAGE[tag.weekday()]]
        iso = tag.isoformat()
        if p["aktiv"]:
            if iso in vorhanden:
                uebersprungen.append(f"{tag.strftime('%d.%m.')} schon eingetragen")
            elif tag in feiertage:
                uebersprungen.append(f"{tag.strftime('%d.%m.')} {feiertage[tag]}")
            elif eintritt and tag < eintritt:
                uebersprungen.append(f"{tag.strftime('%d.%m.')} vor Eintritt")
            else:
                conn.execute(
                    # gleich unterschrieben (✓) – die Bestätigung holt die WhatsApp „1 = Ja / 2 = Nein“ ein
                    "INSERT INTO work_logs (worker_id, datum, start_time, end_time, place, signed) VALUES (?, ?, ?, ?, ?, 1)",
                    (worker_id, iso, p["start"], p["ende"], p["ort"]),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO stundenzettel_auto_eintraege (worker_id, datum, monat, start_time, end_time, place) VALUES (?, ?, ?, ?, ?, ?)",
                    (worker_id, iso, monat, p["start"], p["ende"], p["ort"]),
                )
                neu.append(iso)
        tag += timedelta(days=1)
    text, stunden = monat_zusammenfassung(conn, worker_id, monat)
    _monat_speichern(conn, worker_id, monat, status="ausgefuellt", gefuellt_am=datetime.now().isoformat(timespec="seconds"),
                     zusammenfassung=text)
    conn.commit()
    return {"neu": len(neu), "uebersprungen": uebersprungen, "zusammenfassung": text, "stunden": stunden}


def monat_rueckgaengig(conn, worker_id, monat):
    """Nur automatisch angelegte und unveränderte Tage wieder löschen."""
    _s, _e, monat = _monat_param(monat)
    ensure_tables(conn)
    if _monat_row(conn, worker_id, monat).get("status") == "bestaetigt":
        raise ValueError("Monat ist bestätigt und gesperrt.")
    auto = conn.execute(
        "SELECT datum, start_time, end_time, place FROM stundenzettel_auto_eintraege WHERE worker_id = ? AND monat = ?",
        (worker_id, monat),
    ).fetchall()
    geloescht, geaendert = 0, 0
    for a in auto:
        log = conn.execute(
            "SELECT start_time, end_time, place, signed FROM work_logs WHERE worker_id = ? AND datum = ?",
            (worker_id, a["datum"]),
        ).fetchone()
        # unverändert = gleiche Zeiten und gleicher Ort (automatische Tage sind ohnehin unterschrieben)
        if log and (log["start_time"], log["end_time"], log["place"]) == (a["start_time"], a["end_time"], a["place"]):
            conn.execute("DELETE FROM work_logs WHERE worker_id = ? AND datum = ?", (worker_id, a["datum"]))
            geloescht += 1
        else:
            geaendert += 1
        conn.execute("DELETE FROM stundenzettel_auto_eintraege WHERE worker_id = ? AND datum = ?", (worker_id, a["datum"]))
    _monat_speichern(conn, worker_id, monat, status="offen", gefuellt_am=None)
    conn.commit()
    return {"geloescht": geloescht, "behalten": geaendert}


# ----------------------------------------------------- WhatsApp-Bestätigung
# Kurze Nachricht (Deutsch + Türkisch) über den WhatsApp-Connector (Damlas Diensthandy).
# Antwort 1 = Ja → „✓ Ja“. 2, anderer Text oder 2 Tage keine Antwort → rot, das Büro ruft an.
# Antworten erkennt der WhatsApp-Eingang – auch wenn „Automatische Antworten“ AUS ist.

MONATE_TR = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos",
             "Eylül", "Ekim", "Kasım", "Aralık"]
TAGE_KURZ = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
WA_FRIST_TAGE = 2      # so lange auf Antwort warten, dann rot
WA_ANTWORT_TAGE = 10   # so lange nach dem Senden werden Antworten zugeordnet
# „Ja“ nur, wenn die Antwort ein Ja enthält und sonst nur Füllwörter – alles andere geht ans Büro
WA_JA = {"1", "ja", "jo", "jap", "evet", "ok", "okay", "okey", "oke", "tamam", "tamamdır", "tamamdir",
         "stimmt", "richtig", "passt", "doğru", "dogru", "👍", "👌", "✅"}
WA_FUELLWORT = {"alles", "gut", "danke", "dankeschön", "vielen", "teşekkürler", "tesekkurler", "teşekkür",
                "tesekkur", "ederim", "sağol", "sagol", "sağolun", "sagolun", "abla", "abi", "hocam", "frau",
                "kicci", "damla", "hanım", "hanim", "das", "es", "ist", "yes", "çok", "cok", "her", "şey", "sey"}


def _jetzt_text():
    return berlin_jetzt().isoformat(timespec="seconds")


def _tel_schluessel(value):
    """Nummer vergleichbar machen: nur Ziffern, ohne 00 / 49 / 0 vorne."""
    t = "".join(ch for ch in str(value or "") if ch.isdigit())
    if t.startswith("00"):
        t = t[2:]
    if t.startswith("49"):
        t = t[2:]
    return t.lstrip("0")


def _tel_whatsapp(value):
    """Wie normalize_phone_for_whatsapp im CRM: Ziffern mit Ländervorwahl (0163… → 49163…)."""
    t = "".join(ch for ch in str(value or "") if ch.isdigit())
    if t.startswith("00"):
        t = t[2:]
    if t.startswith("0"):
        t = "49" + t[1:]
    if t.startswith("490"):  # +49 (0) 163…
        t = "49" + t[3:]
    return t


def _wa_woerter(text):
    t = str(text or "").lower()
    for zeichen in ["\ufe0f", "\u20e3"] + [chr(c) for c in range(0x1F3FB, 0x1F400)]:  # Emoji-Varianten, Hautfarben
        t = t.replace(zeichen, "")
    for zeichen in ".,;:!?()[]-–_*\"'+/":
        t = t.replace(zeichen, " ")
    woerter = []
    for w in t.split():
        if set(w) <= {"👍", "👌", "✅"}:
            w = "👍"
        kurz = "".join(ch for i, ch in enumerate(w) if i == 0 or ch != w[i - 1])  # jaaa → ja, 11 → 1
        woerter.append(w if w in WA_JA or w in WA_FUELLWORT else kurz)
    return woerter


def wa_ist_ja(text):
    woerter = _wa_woerter(text)
    return any(w in WA_JA for w in woerter) and all(w in WA_JA or w in WA_FUELLWORT for w in woerter)


def _zeit_kurz(value):
    t = str(value or "")[:5]
    return t[:2] if t.endswith(":00") else t


def _zahl(x):
    return f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def wa_nachricht(conn, worker_id, monat):
    """Kurze WhatsApp-Nachricht über den Monat (Deutsch + Türkisch, Antwort 1 / 2)."""
    start, ende, monat = _monat_param(monat)
    w = _worker(conn, worker_id)
    logs = conn.execute(
        "SELECT datum, start_time, end_time, place FROM work_logs WHERE worker_id = ? AND datum >= ? AND datum < ? ORDER BY datum",
        (worker_id, start.isoformat(), ende.isoformat()),
    ).fetchall()
    arbeit = [l for l in logs if (l["place"] or "") not in SONDER_ORTE]
    stunden = sum(_stunden(l["start_time"], l["end_time"]) for l in arbeit)
    tage = len({l["datum"] for l in arbeit})
    muster = {}
    for l in arbeit:
        key = (l["start_time"], l["end_time"], (l["place"] or "").strip())
        muster.setdefault(key, []).append(date.fromisoformat(l["datum"]))
    zeilen = []
    for (s, e, ort), daten in sorted(muster.items(), key=lambda x: (-len(x[1]), x[1][0])):
        if len(daten) <= 2:  # einzelne Tage mit Datum, sonst Wochentage
            wann = ", ".join(d.strftime("%d.%m.") for d in daten)
        else:
            wann = ", ".join(TAGE_KURZ[i] for i in sorted({d.weekday() for d in daten}))
        zeilen.append(f"({wann} {_zeit_kurz(s)}–{_zeit_kurz(e)} Uhr" + (f", {ort})" if ort else ")"))
    sonder = {}
    for l in logs:
        if (l["place"] or "") in SONDER_ORTE:
            sonder.setdefault(l["place"], []).append(date.fromisoformat(l["datum"]).strftime("%d.%m."))
    for ort, daten in sonder.items():
        zeilen.append(f"{ort}: {', '.join(daten)}")
    vorname = ((w["vorname"] or "").strip() or _name(w)) if w else str(worker_id)
    text = (f"🤖 KG – Stundenzettel {MONATE[start.month - 1]}\n\n"
            f"{vorname}: {tage} {'Tag' if tage == 1 else 'Tage'}, {_zahl(stunden)} {'Stunde' if stunden == 1 else 'Stunden'}")
    if zeilen:
        text += "\n" + "\n".join(zeilen)
    return text + "\n\nStimmt das?  1 = Ja   2 = Nein\nDoğru mu?   1 = Evet   2 = Hayır"


def whatsapp_senden(conn, worker_id, monat):
    """Monatsübersicht per WhatsApp an den Mitarbeiter (über die Outbox des Connectors)."""
    start, ende, monat = _monat_param(monat)
    ensure_tables(conn)
    w = _worker(conn, worker_id)
    if not w:
        raise ValueError("Mitarbeiter nicht gefunden.")
    if _monat_row(conn, worker_id, monat).get("status") == "bestaetigt":
        raise ValueError("Monat ist bereits bestätigt und gesperrt.")
    nummer = _tel_whatsapp(w["telefon"])
    if len(nummer) < 9:
        raise ValueError("Für diesen Mitarbeiter ist keine gültige Telefonnummer gespeichert.")
    if not conn.execute("SELECT 1 FROM work_logs WHERE worker_id = ? AND datum >= ? AND datum < ? LIMIT 1",
                        (worker_id, start.isoformat(), ende.isoformat())).fetchone():
        raise ValueError("Im Monat ist noch nichts eingetragen – zuerst „Monat ausfüllen“.")
    text = wa_nachricht(conn, worker_id, monat)
    cur = conn.execute(
        "INSERT INTO whatsapp_outbox (phone, text, status, source) VALUES (?, ?, 'pending', 'stundenzettel')",
        (nummer, text),
    )
    zusammenfassung, _stunden_summe = monat_zusammenfassung(conn, worker_id, monat)
    _monat_speichern(conn, worker_id, monat, status="wa_wartet", wa_gesendet_am=_jetzt_text(), wa_outbox_id=cur.lastrowid,
                     wa_antwort=None, wa_antwort_am=None, zusammenfassung=zusammenfassung)
    conn.commit()
    return {"nummer": nummer, "text": text}


def whatsapp_antwort(conn, phone, raw_from, body):
    """Antwort eines Mitarbeiters zuordnen. True = Antwort auf die Stundenzettel-WhatsApp (keine KI-Antwort mehr)."""
    text = str(body or "").strip()
    absender = str(raw_from or "")
    schluessel = {_tel_schluessel(phone)}
    if absender.endswith("@c.us") or "@" not in absender:
        schluessel.add(_tel_schluessel(absender.split("@")[0]))
    schluessel = {k for k in schluessel if len(k) >= 6}
    if not text or not schluessel:
        return False
    ensure_tables(conn)
    jetzt = berlin_jetzt()
    rows = conn.execute(
        "SELECT m.worker_id, m.monat, m.status, m.wa_antwort, m.wa_antwort_am, w.telefon FROM stundenzettel_monate m "
        "JOIN mitarbeiter w ON w.id = m.worker_id "
        "WHERE (m.status = 'wa_wartet' AND m.wa_gesendet_am >= ?) OR (m.status = 'wa_nein' AND m.wa_antwort_am >= ?) "
        "ORDER BY m.status DESC, m.monat DESC",  # zuerst die wartenden (wa_wartet), davon der neueste Monat
        ((jetzt - timedelta(days=WA_ANTWORT_TAGE)).isoformat(timespec="seconds"),
         (jetzt - timedelta(days=WA_FRIST_TAGE)).isoformat(timespec="seconds")),
    ).fetchall()
    treffer = [r for r in rows if _tel_schluessel(r["telefon"]) in schluessel]
    if not treffer:
        return False
    r = treffer[0]
    if r["status"] == "wa_nein":
        # Nachtrag nach einem Nein („am 15. war ich krank“) – fürs Büro dazuschreiben, keine neue Quittung.
        # False: die Nachricht läuft danach normal weiter (z. B. der Chef schreibt dem KG Agent).
        _monat_speichern(conn, r["worker_id"], r["monat"], wa_antwort=(f"{r['wa_antwort'] or ''}\n{text}").strip()[:2000])
        conn.commit()
        return False
    ja = wa_ist_ja(text)
    _monat_speichern(conn, r["worker_id"], r["monat"], status="wa_ja" if ja else "wa_nein",
                     wa_antwort=text[:2000], wa_antwort_am=jetzt.isoformat(timespec="seconds"))
    m = _monat_param(r["monat"])[0].month - 1
    if ja:
        quittung = (f"🤖 KG – Danke! Stundenzettel {MONATE[m]} ist bestätigt ✓\n"
                    f"Teşekkürler! {MONATE_TR[m]} saatleri onaylandı ✓")
    else:
        quittung = "🤖 KG – Danke. Das Büro ruft Sie an.\nTeşekkürler. Büro sizi arayacak."
    conn.execute(
        "INSERT INTO whatsapp_outbox (phone, text, status, source) VALUES (?, ?, 'pending', 'stundenzettel')",
        (_tel_whatsapp(phone) or _tel_whatsapp(r["telefon"]), quittung),
    )
    conn.commit()
    return True


def wa_info(conn, row):
    """Stand der WhatsApp für die Übersicht: Versand, Nachricht, „keine Antwort seit 2 Tagen“."""
    if not row.get("wa_gesendet_am"):
        return None
    info = {"versand": "", "text": "", "ueberfaellig": False}
    try:
        o = conn.execute(
            "SELECT status, text, error, created_at < datetime('now', '-1 day') AS alt FROM whatsapp_outbox WHERE id = ?",
            (row.get("wa_outbox_id"),),
        ).fetchone()
    except Exception:
        o = None
    if o:
        info["text"] = o["text"]
        if o["status"] == "sent":
            info["versand"] = "gesendet ✓"
        elif o["status"] == "error":
            info["versand"] = "Fehler beim Senden: " + str(o["error"] or "")[:200]
        elif o["alt"]:
            info["versand"] = "nicht gesendet (Handy/Connector war aus) – bitte erneut senden"
        else:
            info["versand"] = "wartet auf Damlas Handy"
    if row.get("status") == "wa_wartet":
        try:
            gesendet = datetime.fromisoformat(str(row["wa_gesendet_am"]))
            info["ueberfaellig"] = berlin_jetzt() - gesendet > timedelta(days=WA_FRIST_TAGE)
        except ValueError:
            pass
    return info


# ----------------------------------------------------- Automatik
# Läuft im CRM selbst (kein Render-Cron nötig), alle 10 Minuten:
#  1. Ab Tag „fuell_tag“ (Standard 1.): Monat für alle aktiven Mitarbeiter mit
#     festen Zeiten ausfüllen – je Mitarbeiter und Monat höchstens einmal.
#  2. Ab Tag „anruf_tag“ (Standard 20., Mo–Fr, kein Feiertag, ab „anruf_stunde“
#     bis 18 Uhr): WhatsApp an jeden ausgefüllten Mitarbeiter („1 = Ja, 2 = Nein“)
#     – je Monat höchstens einmal. (Schlüssel heißen weiter „anruf_…“, damit
#     gespeicherte Einstellungen bleiben.)
# Standard: beides AUS. Bestätigte Monate, „Rückgängig“ und von Hand
# gesendete WhatsApps werden nie überschrieben.
# Für eine Umgebung ganz abschalten: STZ_AUTOMATIK_AUS=1.

AUTOMATIK_STANDARD = {"fuellen_an": False, "fuell_tag": 1, "anruf_an": False, "anruf_tag": 20, "anruf_stunde": 10}
ANRUF_BIS_STUNDE = 18
TAKT_SEKUNDEN = 600
_automatik_gestartet = False
_automatik_lock = threading.Lock()


def berlin_jetzt():
    """Uhrzeit in Deutschland (der Server läuft in UTC)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Europe/Berlin")).replace(tzinfo=None)
    except Exception:
        utc = datetime.now(timezone.utc).replace(tzinfo=None)
        # Sommerzeit: letzter Sonntag im März bis letzter Sonntag im Oktober, jeweils 01:00 UTC
        maerz = datetime(utc.year, 3, 31, 1) - timedelta(days=(date(utc.year, 3, 31).weekday() + 1) % 7)
        oktober = datetime(utc.year, 10, 31, 1) - timedelta(days=(date(utc.year, 10, 31).weekday() + 1) % 7)
        return utc + timedelta(hours=2 if maerz <= utc < oktober else 1)


def _automatik_tabellen(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stundenzettel_automatik (
            schluessel TEXT PRIMARY KEY,
            wert TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stundenzettel_automatik_laeufe (
            monat TEXT NOT NULL,
            schritt TEXT NOT NULL,
            worker_id INTEGER,
            zeit TEXT,
            bericht TEXT,
            PRIMARY KEY (monat, schritt)
        )
    """)
    conn.commit()


def automatik_einstellungen(conn):
    _automatik_tabellen(conn)
    e = dict(AUTOMATIK_STANDARD)
    row = conn.execute("SELECT wert FROM stundenzettel_automatik WHERE schluessel = 'einstellungen'").fetchone()
    if row:
        try:
            e.update({k: v for k, v in json.loads(row["wert"]).items() if k in AUTOMATIK_STANDARD})
        except (ValueError, TypeError, AttributeError):
            pass
    return e


def automatik_speichern(conn, daten):
    e = automatik_einstellungen(conn)
    try:
        neu = {
            "fuellen_an": bool(daten.get("fuellen_an", e["fuellen_an"])),
            "fuell_tag": max(1, min(28, int(daten.get("fuell_tag", e["fuell_tag"])))),
            "anruf_an": bool(daten.get("anruf_an", e["anruf_an"])),
            "anruf_tag": max(1, min(28, int(daten.get("anruf_tag", e["anruf_tag"])))),
            "anruf_stunde": max(8, min(17, int(daten.get("anruf_stunde", e["anruf_stunde"])))),
        }
    except (TypeError, ValueError):
        raise ValueError("Bitte Zahlen prüfen (Tag 1–28, Uhrzeit 8–17).")
    conn.execute(
        "INSERT INTO stundenzettel_automatik (schluessel, wert) VALUES ('einstellungen', ?) "
        "ON CONFLICT(schluessel) DO UPDATE SET wert = excluded.wert",
        (json.dumps(neu),),
    )
    conn.commit()
    return neu


def _schritt(conn, monat, schritt):
    return conn.execute(
        "SELECT * FROM stundenzettel_automatik_laeufe WHERE monat = ? AND schritt = ?", (monat, schritt)
    ).fetchone()


def _schritt_merken(conn, monat, schritt, worker_id, zeit, bericht):
    conn.execute(
        "INSERT OR REPLACE INTO stundenzettel_automatik_laeufe (monat, schritt, worker_id, zeit, bericht) VALUES (?, ?, ?, ?, ?)",
        (monat, schritt, worker_id, zeit.isoformat(timespec="seconds"), str(bericht)[:500]),
    )
    conn.commit()


def automatik_letzte(conn, anzahl=12):
    """Letzte Schritte der Automatik (neueste zuerst) für die Übersicht."""
    _automatik_tabellen(conn)
    return [dict(r) for r in conn.execute(
        "SELECT monat, schritt, worker_id, zeit, bericht FROM stundenzettel_automatik_laeufe ORDER BY zeit DESC LIMIT ?",
        (anzahl,),
    )]


def _wert(conn, schluessel, standard="0"):
    row = conn.execute("SELECT wert FROM stundenzettel_automatik WHERE schluessel = ?", (schluessel,)).fetchone()
    return row["wert"] if row and row["wert"] is not None else standard


def automatik_durchgang(conn, leon_client_factory, sofort=False):
    """Ein Durchgang mit Sperre: nie zwei gleichzeitig (Render kann mehrere Prozesse
    starten) und im Hintergrund höchstens einmal je Takt. → (gelaufen, aktionen)"""
    _automatik_tabellen(conn)
    jetzt = time.time()
    if not sofort and jetzt - float(_wert(conn, "letzter_lauf")) < TAKT_SEKUNDEN - 120:
        return False, []
    conn.execute("INSERT OR IGNORE INTO stundenzettel_automatik (schluessel, wert) VALUES ('sperre_bis', '0')")
    cur = conn.execute(
        "UPDATE stundenzettel_automatik SET wert = ? WHERE schluessel = 'sperre_bis' AND CAST(wert AS REAL) < ?",
        (str(jetzt + 300), jetzt),
    )
    conn.commit()
    if cur.rowcount != 1:
        return False, []
    try:
        return True, automatik_lauf(conn, leon_client_factory)
    finally:
        conn.execute(
            "INSERT OR REPLACE INTO stundenzettel_automatik (schluessel, wert) VALUES ('letzter_lauf', ?)",
            (str(time.time()),),
        )
        conn.execute("UPDATE stundenzettel_automatik SET wert = '0' WHERE schluessel = 'sperre_bis'")
        conn.commit()


def automatik_lauf(conn, leon_client_factory, jetzt=None):
    """Ein Durchgang der Automatik. Gibt die erledigten Schritte als Texte zurück."""
    ensure_tables(conn)
    e = automatik_einstellungen(conn)
    jetzt = jetzt or berlin_jetzt()
    monat = f"{jetzt.year:04d}-{jetzt.month:02d}"
    aktionen = []
    if not (e["fuellen_an"] or e["anruf_an"]):
        return aktionen
    workers = [
        w for w in conn.execute(
            "SELECT m.id, m.vorname, m.nachname, m.telefon FROM mitarbeiter m "
            "JOIN stundenzettel_vorlagen v ON v.worker_id = m.id WHERE m.status = 'aktiv' ORDER BY m.id"
        ).fetchall()
        if any(p["aktiv"] for p in _plan_laden(conn, w["id"]).values())
    ]

    def fuellen(w, zusatz=""):
        if _schritt(conn, monat, f"fuellen:{w['id']}"):
            return  # schon einmal ausgefüllt (oder danach zurückgenommen) – nicht noch einmal
        try:
            r = monat_fuellen(conn, w["id"], monat)
            text = f"{_name(w)}: Monat ausgefüllt, {r['neu']} Tage eingetragen{zusatz}"
        except ValueError as exc:
            text = f"{_name(w)}: nicht ausgefüllt – {exc}"
        _schritt_merken(conn, monat, f"fuellen:{w['id']}", w["id"], jetzt, text)
        aktionen.append(text)

    # 1. Monatsanfang: ausfüllen
    if e["fuellen_an"] and jetzt.day >= e["fuell_tag"]:
        for w in workers:
            if _monat_row(conn, w["id"], monat).get("status") == "offen":
                fuellen(w)

    # 2. WhatsApp-Bestätigung
    werktag = jetzt.weekday() < 5 and jetzt.date() not in feiertage_nrw(jetzt.year)
    if e["anruf_an"] and jetzt.day >= e["anruf_tag"] and werktag and e["anruf_stunde"] <= jetzt.hour < ANRUF_BIS_STUNDE:
        for w in workers:
            if _schritt(conn, monat, f"whatsapp:{w['id']}"):
                continue
            if _monat_row(conn, w["id"], monat).get("status") == "offen":
                fuellen(w, " (vor der WhatsApp)")
            row = _monat_row(conn, w["id"], monat)
            if row.get("status") not in ("ausgefuellt", "leon_wartet", "leon_fertig") or row.get("wa_gesendet_am"):
                continue  # bestätigt, zurückgenommen oder schon von Hand gesendet
            try:
                whatsapp_senden(conn, w["id"], monat)
                text = f"{_name(w)}: WhatsApp zur Bestätigung gesendet"
            except ValueError as exc:  # keine Nummer, nichts eingetragen – nicht jedes Mal neu versuchen
                text = f"{_name(w)}: keine WhatsApp – {exc}"
            except Exception as exc:  # z. B. Datenbank kurz gesperrt – nächster Takt
                aktionen.append(f"{_name(w)}: WhatsApp noch nicht möglich – {exc}")
                continue
            _schritt_merken(conn, monat, f"whatsapp:{w['id']}", w["id"], jetzt, text)
            aktionen.append(text)
    return aktionen


def automatik_starten(get_db_connection, leon_client_factory):
    """Hintergrund-Takt einmal je Prozess starten."""
    global _automatik_gestartet
    if os.getenv("STZ_AUTOMATIK_AUS", "").strip() == "1":
        return
    with _automatik_lock:
        if _automatik_gestartet:
            return
        _automatik_gestartet = True

    def schleife():
        time.sleep(90)
        while True:
            try:
                conn = get_db_connection()
                try:
                    _gelaufen, aktionen = automatik_durchgang(conn, leon_client_factory)
                    for text in aktionen:
                        print("STUNDENZETTEL-AUTOMATIK:", text)
                finally:
                    conn.close()
            except Exception as exc:
                print("STUNDENZETTEL-AUTOMATIK FEHLER:", exc)
            time.sleep(TAKT_SEKUNDEN)

    threading.Thread(target=schleife, name="stundenzettel-automatik", daemon=True).start()


# ----------------------------------------------------- Routen

def register_stundenzettel_auto(app, login_required, get_db_connection, leon_client_factory):
    # leon_client_factory wird nicht mehr gebraucht (Leon ruft nicht mehr an) – bleibt, damit app.py unverändert bleibt

    def _conn():
        conn = get_db_connection()
        ensure_tables(conn)
        return conn

    def _json():
        return request.get_json(silent=True) or {}

    def _fehler(exc, code=400):
        return jsonify({"success": False, "error": str(exc)}), code

    @app.route("/stundenzettel/automatik")
    @login_required
    def stundenzettel_automatik_seite():
        return render_template("stundenzettel_auto.html")

    @app.route("/api/stz-auto/automatik", methods=["GET", "POST"])
    @login_required
    def stz_auto_automatik():
        conn = _conn()
        try:
            if request.method == "POST":
                try:
                    automatik_speichern(conn, _json())
                except ValueError as exc:
                    return _fehler(exc)
            jetzt = berlin_jetzt()
            return jsonify({
                "success": True,
                "einstellungen": automatik_einstellungen(conn),
                "letzte": automatik_letzte(conn),
                "jetzt": jetzt.isoformat(timespec="minutes"),
                "laeuft": _automatik_gestartet,
            })
        finally:
            conn.close()

    @app.route("/api/stz-auto/automatik/jetzt", methods=["POST"])
    @login_required
    def stz_auto_automatik_jetzt():
        conn = _conn()
        try:
            gelaufen, aktionen = automatik_durchgang(conn, leon_client_factory, sofort=True)
            if not gelaufen:
                return jsonify({"success": True, "aktionen": [], "info": "Die Automatik arbeitet gerade – bitte gleich noch einmal."})
            return jsonify({"success": True, "aktionen": aktionen})
        except Exception as exc:
            return _fehler(exc, 502)
        finally:
            conn.close()

    automatik_starten(get_db_connection, leon_client_factory)

    @app.route("/api/stz-auto/uebersicht")
    @login_required
    def stz_auto_uebersicht():
        _s, _e, monat = _monat_param(request.args.get("monat"))
        conn = _conn()
        try:
            workers = conn.execute(
                "SELECT id, vorname, nachname, telefon, access_code FROM mitarbeiter WHERE status = 'aktiv' ORDER BY sort_order, vorname"
            ).fetchall()
            out = []
            for w in workers:
                row = _monat_row(conn, w["id"], monat)
                text, stunden = monat_zusammenfassung(conn, w["id"], monat)
                out.append({
                    "id": w["id"], "name": _name(w), "telefon": w["telefon"] or "",
                    "plan": _plan_laden(conn, w["id"]), "monat": row, "zusammenfassung": text, "stunden": stunden,
                    "wa": wa_info(conn, row),
                })
            return jsonify({"success": True, "monat": monat, "mitarbeiter": out})
        finally:
            conn.close()

    @app.route("/api/stz-auto/plan/<int:worker_id>", methods=["POST"])
    @login_required
    def stz_auto_plan(worker_id):
        try:
            plan = _plan_pruefen(_json().get("plan"))
        except ValueError as exc:
            return _fehler(exc)
        conn = _conn()
        try:
            conn.execute(
                "INSERT INTO stundenzettel_vorlagen (worker_id, plan_json, aktualisiert_am) VALUES (?, ?, ?) "
                "ON CONFLICT(worker_id) DO UPDATE SET plan_json = excluded.plan_json, aktualisiert_am = excluded.aktualisiert_am",
                (worker_id, json.dumps(plan, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
            return jsonify({"success": True, "plan": plan})
        finally:
            conn.close()

    @app.route("/api/stz-auto/fuellen/<int:worker_id>", methods=["POST"])
    @login_required
    def stz_auto_fuellen(worker_id):
        conn = _conn()
        try:
            return jsonify({"success": True, **monat_fuellen(conn, worker_id, _json().get("monat"))})
        except ValueError as exc:
            return _fehler(exc)
        finally:
            conn.close()

    @app.route("/api/stz-auto/rueckgaengig/<int:worker_id>", methods=["POST"])
    @login_required
    def stz_auto_rueckgaengig(worker_id):
        conn = _conn()
        try:
            return jsonify({"success": True, **monat_rueckgaengig(conn, worker_id, _json().get("monat"))})
        except ValueError as exc:
            return _fehler(exc)
        finally:
            conn.close()

    @app.route("/api/stz-auto/bestaetigen/<int:worker_id>", methods=["POST"])
    @login_required
    def stz_auto_bestaetigen(worker_id):
        data = _json()
        _s, _e, monat = _monat_param(data.get("monat"))
        conn = _conn()
        try:
            text, _st = monat_zusammenfassung(conn, worker_id, monat)
            _monat_speichern(conn, worker_id, monat, status="bestaetigt", bestaetigt_am=datetime.now().isoformat(timespec="seconds"),
                             notiz=str(data.get("notiz") or "")[:1000] or None, zusammenfassung=text)
            conn.commit()
            return jsonify({"success": True, "monat": _monat_row(conn, worker_id, monat)})
        finally:
            conn.close()

    @app.route("/api/stz-auto/entsperren/<int:worker_id>", methods=["POST"])
    @login_required
    def stz_auto_entsperren(worker_id):
        _s, _e, monat = _monat_param(_json().get("monat"))
        conn = _conn()
        try:
            _monat_speichern(conn, worker_id, monat, status="ausgefuellt", bestaetigt_am=None)
            conn.commit()
            return jsonify({"success": True, "monat": _monat_row(conn, worker_id, monat)})
        finally:
            conn.close()

    @app.route("/api/stz-auto/whatsapp/<int:worker_id>", methods=["GET", "POST"])
    @login_required
    def stz_auto_whatsapp(worker_id):
        """GET = Vorschau der Nachricht, POST = senden."""
        conn = _conn()
        try:
            if request.method == "GET":
                _s, _e, monat = _monat_param(request.args.get("monat"))
                if not _worker(conn, worker_id):
                    return _fehler("Mitarbeiter nicht gefunden.", 404)
                return jsonify({"success": True, "text": wa_nachricht(conn, worker_id, monat)})
            return jsonify({"success": True, **whatsapp_senden(conn, worker_id, _json().get("monat"))})
        except ValueError as exc:
            return _fehler(exc)
        finally:
            conn.close()

    # Für einen Cron (z. B. täglich 7:00): ab dem 20. den laufenden Monat für
    # alle Mitarbeiter mit festen Zeiten ausfüllen; mit &whatsapp=1 auch die WhatsApp senden.
    @app.route("/internal/stundenzettel-auto", methods=["GET", "POST"])
    def stz_auto_cron():
        token = (os.getenv("STZ_CRON_TOKEN") or os.getenv("INTERNAL_CRON_TOKEN") or "").strip()
        given = (request.args.get("token") or request.headers.get("X-Cron-Token") or "").strip()
        if not token or given != token:
            return jsonify({"success": False, "error": "Zugriff verweigert"}), 403
        heute = date.today()
        if heute.day < int(os.getenv("STZ_AB_TAG", "20")) and request.args.get("force") != "1":
            return jsonify({"success": True, "info": "Noch nicht der 20. – nichts getan."})
        _s, _e, monat = _monat_param(f"{heute.year}-{heute.month:02d}")
        conn = _conn()
        bericht = []
        try:
            ids = [r["worker_id"] for r in conn.execute("SELECT worker_id FROM stundenzettel_vorlagen")]
            for wid in ids:
                row = _monat_row(conn, wid, monat)
                if row.get("status") not in (None, "offen"):
                    continue
                try:
                    r = monat_fuellen(conn, wid, monat)
                    eintrag = {"worker_id": wid, "neu": r["neu"]}
                    if request.args.get("whatsapp") == "1":
                        whatsapp_senden(conn, wid, monat)
                        eintrag["whatsapp"] = "gesendet"
                    bericht.append(eintrag)
                except Exception as exc:
                    bericht.append({"worker_id": wid, "fehler": str(exc)})
            return jsonify({"success": True, "monat": monat, "bericht": bericht})
        finally:
            conn.close()
