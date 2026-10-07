# =====================================================
# STUNDENZETTEL-AUTOMATIK
# Feste Arbeitszeiten je Mitarbeiter → ganzen Monat ausfüllen →
# Leon ruft den Mitarbeiter an („Stimmt das? Fehlt etwas?“) →
# Chef bestätigt → Monat gesperrt (Mitarbeiter-Link kann nichts mehr ändern).
#
# Bestehende Stundenzettel-Funktionen bleiben unverändert. Ausgefüllt wird
# nur an Tagen OHNE Eintrag (gleich unterschrieben ✓); automatisch angelegte
# Tage sind gemerkt und lassen sich zurücknehmen, solange sie nicht verändert wurden.
#
# Leon: eigenes Profil „Leon Stundenzettel“ und Kampagne
# „Stundenzettel-Kontrolle“ im Leon-Motor (einmalig einrichten mit
# tools/stundenzettel_agent.py im Leon-Repo). Name änderbar über
# LEON_STZ_KAMPAGNE in tokenlar.env / Render Environment.
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
    """Kurzer deutscher Text über den Monat – für Chef-Ansicht und Leon."""
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
                    # gleich unterschrieben (✓) – die Bestätigung holt Leon beim Kontrollanruf ein
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


# ----------------------------------------------------- Leon

def _leon_kampagne(client):
    name = (os.getenv("LEON_STZ_KAMPAGNE") or "Stundenzettel-Kontrolle").strip()
    _code, data = client.request("GET", "/api/campaigns")
    for c in (data or {}).get("campaigns", []):
        if (c.get("name") or "").strip().lower() == name.lower():
            return c
    return None


def leon_anruf_starten(conn, client, worker_id, monat):
    _s, _e, monat = _monat_param(monat)
    ensure_tables(conn)
    w = _worker(conn, worker_id)
    if not w:
        raise ValueError("Mitarbeiter nicht gefunden.")
    if not (w["telefon"] or "").strip():
        raise ValueError("Für diesen Mitarbeiter ist keine Telefonnummer gespeichert.")
    kampagne = _leon_kampagne(client)
    if not kampagne:
        raise ValueError("Im Leon-Motor fehlt die Kampagne „Stundenzettel-Kontrolle“. "
                         "Einmalig tools/stundenzettel_agent.py im Leon-Repo ausführen.")
    text, _stunden = monat_zusammenfassung(conn, worker_id, monat)
    lead = {
        "firma": f"KG Mitarbeiter {_name(w)}",
        "ansprechpartner": _name(w),
        "telefon": w["telefon"],
        "branche": text[:900],
    }
    code, resp = client.request("POST", "/api/leads", lead, timeout=30)
    lead_id = None
    if code == 409 and resp.get("duplicate_id"):
        lead_id = resp["duplicate_id"]
        client.request("PUT", f"/api/leads/{lead_id}", lead, timeout=30)
    elif resp.get("success"):
        lead_id = (resp.get("lead") or {}).get("id") or resp.get("id") or resp.get("lead_id")
    if not lead_id:
        raise ValueError(resp.get("error") or "Leon konnte den Mitarbeiter nicht anlegen.")
    cid = kampagne["id"]
    # Vom Vormonat noch in der Kampagne? Herausnehmen und neu einreihen.
    try:
        client.request("DELETE", f"/api/campaigns/{cid}/leads/{lead_id}", timeout=30)
    except Exception:  # war nicht in der Kampagne – egal
        pass
    code, resp = client.request("POST", f"/api/campaigns/{cid}/leads", {"lead_ids": [lead_id]}, timeout=30)
    if not resp.get("success", code < 400):
        raise ValueError(resp.get("error") or "Mitarbeiter konnte nicht in die Kampagne.")
    if kampagne.get("status") != "Aktiv":
        code, resp = client.request("POST", f"/api/campaigns/{cid}/control", {"action": "start"}, timeout=30)
        if code == 409 and resp.get("active_campaign_id"):
            raise ValueError("Im Leon-Motor läuft gerade eine andere Kampagne. "
                             "Bitte diese kurz pausieren, dann erneut „Leon anrufen lassen“.")
        if code >= 400 and not resp.get("success"):
            raise ValueError(resp.get("error") or "Kampagne konnte nicht gestartet werden.")
    _monat_speichern(conn, worker_id, monat, status="leon_wartet", leon_lead_id=lead_id, leon_call_id=None,
                     leon_status="Wartet auf Anruf", leon_ergebnis=None, leon_zusammenfassung=None,
                     leon_transkript=None, leon_fehler=None, angerufen_am=datetime.now().isoformat(timespec="seconds"),
                     zusammenfassung=text)
    conn.commit()
    return {"lead_id": lead_id, "kampagne_id": cid}


def leon_status_aktualisieren(conn, client, worker_id, monat):
    """Stand des Leon-Kontrollanrufs aus dem Motor holen und speichern."""
    _s, _e, monat = _monat_param(monat)
    row = _monat_row(conn, worker_id, monat)
    lead_id = row.get("leon_lead_id")
    if not lead_id or row.get("status") not in ("leon_wartet", "leon_fertig"):
        return row
    lead_id = int(lead_id)
    kampagne = _leon_kampagne(client)
    felder = {}
    eintrag = None
    if kampagne:
        _code, data = client.request("GET", f"/api/campaigns/{kampagne['id']}/leads")
        for cl in (data or {}).get("selected_leads", []):
            if int(cl.get("lead_id") or 0) == lead_id:
                eintrag = cl
                break
    # neuester Anruf dieses Mitarbeiters seit dem Auftrag
    seit = str(row.get("angerufen_am") or "").replace("T", " ")[:19]
    _code, data = client.request("GET", "/api/calls")
    anrufe = [c for c in (data or {}).get("calls", []) if int(c.get("lead_id") or 0) == lead_id
              and str(c.get("created_at") or "")[:19] >= seit[:19]]
    anrufe.sort(key=lambda c: int(c.get("id") or 0), reverse=True)
    if anrufe:
        call_id = anrufe[0]["id"]
        _code, detail = client.request("GET", f"/api/calls/{call_id}")
        call = (detail or {}).get("call") or anrufe[0]
        felder.update(leon_call_id=call_id, leon_status=call.get("status"), leon_ergebnis=call.get("result"),
                      leon_zusammenfassung=call.get("summary"), leon_transkript=call.get("transcript"))
    ende_status = {"Beendet", "Nicht erreicht", "Gesperrt"}
    if eintrag:
        st = eintrag.get("campaign_status") or "Wartet"
        info = f"Kampagne: {st}"
        if eintrag.get("attempt_count"):
            info += f", Versuch {eintrag['attempt_count']}"
        if st == "Wartet" and eintrag.get("next_attempt_at"):
            info += f", nächster Versuch {str(eintrag['next_attempt_at'])[:16]}"
        felder["leon_info"] = info
        if not anrufe:
            felder["leon_status"] = "Wartet auf Anruf"
        if st in ende_status:
            felder["status"] = "leon_fertig"
        elif (st == "Wartet" and kampagne.get("status") != "Aktiv"
              and int(eintrag.get("attempt_count") or 0) < int(kampagne.get("max_attempts") or 3)
              and str(eintrag.get("next_attempt_at") or "")[:19] <= datetime.now().strftime("%Y-%m-%d %H:%M:%S")):
            # Motor pausiert die Kampagne, wenn gerade niemand fällig ist –
            # ist der nächste Versuch fällig, Kampagne wieder starten.
            code, resp = client.request("POST", f"/api/campaigns/{kampagne['id']}/control", {"action": "start"}, timeout=30)
            if code == 409 and resp.get("active_campaign_id"):
                felder["leon_info"] = info + " – wartet, bis die laufende Verkaufskampagne pausiert ist"
            elif resp.get("success"):
                felder["leon_info"] = info + " – nächster Versuch gestartet"
    elif anrufe and felder.get("leon_status") in {"Beendet", "Fehler", "Nicht erreichbar", "Besetzt", "Abgebrochen", "Anrufbeantworter"}:
        felder["status"] = "leon_fertig"
    if felder:
        _monat_speichern(conn, worker_id, monat, **felder)
    # Kampagne pausieren, sobald niemand mehr wartet – sonst blockiert sie
    # Verkaufskampagnen (im Motor darf nur eine Kampagne aktiv sein).
    if kampagne:
        _code, prog = client.request("GET", f"/api/campaigns/{kampagne['id']}/progress")
        prog = prog or {}
        offen = sum(int(prog.get(k) or 0) for k in ("waiting", "reserved", "running", "retry_waiting", "active_call_count"))
        if prog.get("success") and prog.get("status") == "Aktiv" and offen == 0:
            client.request("POST", f"/api/campaigns/{kampagne['id']}/control", {"action": "pause"}, timeout=30)
    conn.commit()
    return _monat_row(conn, worker_id, monat)


def leon_abbrechen(conn, client, worker_id, monat):
    """Mitarbeiter aus der Stundenzettel-Kampagne nehmen und Kampagne pausieren, wenn leer."""
    _s, _e, monat = _monat_param(monat)
    row = _monat_row(conn, worker_id, monat)
    kampagne = _leon_kampagne(client)
    if kampagne and row.get("leon_lead_id"):
        try:
            client.request("DELETE", f"/api/campaigns/{kampagne['id']}/leads/{int(row['leon_lead_id'])}", timeout=30)
        except Exception:
            pass
        _code, prog = client.request("GET", f"/api/campaigns/{kampagne['id']}/progress")
        prog = prog or {}
        offen = sum(int(prog.get(k) or 0) for k in ("waiting", "reserved", "running", "retry_waiting", "active_call_count"))
        if prog.get("status") == "Aktiv" and offen == 0:
            client.request("POST", f"/api/campaigns/{kampagne['id']}/control", {"action": "pause"}, timeout=30)
    neuer_status = "ausgefuellt" if row.get("gefuellt_am") else "offen"
    _monat_speichern(conn, worker_id, monat, status=neuer_status, leon_info="Leon-Kontrolle abgebrochen")
    conn.commit()
    return _monat_row(conn, worker_id, monat)


# ----------------------------------------------------- Automatik
# Läuft im CRM selbst (kein Render-Cron nötig), alle 10 Minuten:
#  1. Ab Tag „fuell_tag“ (Standard 1.): Monat für alle aktiven Mitarbeiter mit
#     festen Zeiten ausfüllen – je Mitarbeiter und Monat höchstens einmal.
#  2. Ab Tag „anruf_tag“ (Standard 20., Mo–Fr, kein Feiertag, ab „anruf_stunde“
#     bis 18 Uhr): Leon ruft jeden ausgefüllten Mitarbeiter an – je Monat
#     höchstens einmal. Läuft gerade eine andere Leon-Kampagne, neuer Versuch
#     nach 30 Minuten.
#  3. Laufende Leon-Kontrollen nachhalten (Ergebnis holen, Kampagne pausieren,
#     sobald niemand mehr wartet – sonst blockiert sie Verkaufskampagnen).
# Standard: beides AUS. Bestätigte Monate, „Rückgängig“ und von Hand
# gestartete Leon-Kontrollen werden nie überschrieben.
# Für eine Umgebung ganz abschalten: STZ_AUTOMATIK_AUS=1.

AUTOMATIK_STANDARD = {"fuellen_an": False, "fuell_tag": 1, "anruf_an": False, "anruf_tag": 20, "anruf_stunde": 10}
ANRUF_BIS_STUNDE = 18
TAKT_SEKUNDEN = 600
ERNEUT_MINUTEN = 30
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

    # 2. Leon-Kontrollanruf
    werktag = jetzt.weekday() < 5 and jetzt.date() not in feiertage_nrw(jetzt.year)
    client = None
    if e["anruf_an"] and jetzt.day >= e["anruf_tag"] and werktag and e["anruf_stunde"] <= jetzt.hour < ANRUF_BIS_STUNDE:
        for w in workers:
            if _schritt(conn, monat, f"anruf:{w['id']}"):
                continue
            if _monat_row(conn, w["id"], monat).get("status") == "offen":
                fuellen(w, " (vor dem Leon-Anruf)")
            row = _monat_row(conn, w["id"], monat)
            if row.get("status") != "ausgefuellt" or row.get("leon_lead_id"):
                continue  # bestätigt, zurückgenommen oder Leon schon von Hand beauftragt
            versuch = _schritt(conn, monat, f"anruf_versuch:{w['id']}")
            if versuch and str(versuch["zeit"]) > (jetzt - timedelta(minutes=ERNEUT_MINUTEN)).isoformat(timespec="seconds"):
                continue
            try:
                client = client or leon_client_factory()
                leon_anruf_starten(conn, client, w["id"], monat)
                text = f"{_name(w)}: Leon-Kontrollanruf gestartet"
                _schritt_merken(conn, monat, f"anruf:{w['id']}", w["id"], jetzt, text)
            except Exception as exc:  # andere Kampagne aktiv, Leon nicht erreichbar, …
                text = f"{_name(w)}: Leon-Anruf noch nicht möglich – {exc}"
                _monat_speichern(conn, w["id"], monat, leon_info=("Automatik: " + str(exc))[:500])
                conn.commit()
                _schritt_merken(conn, monat, f"anruf_versuch:{w['id']}", w["id"], jetzt, text)
            aktionen.append(text)

    # 3. Laufende Leon-Kontrollen nachhalten
    if e["anruf_an"]:
        wartend = conn.execute(
            "SELECT worker_id, monat FROM stundenzettel_monate WHERE status = 'leon_wartet' ORDER BY monat, worker_id"
        ).fetchall()
        for r in wartend:
            try:
                client = client or leon_client_factory()
                neu = leon_status_aktualisieren(conn, client, r["worker_id"], r["monat"])
            except Exception:
                continue  # Leon gerade nicht erreichbar – nächster Takt
            if neu.get("status") == "leon_fertig":
                w = _worker(conn, r["worker_id"])
                text = f"{_name(w) if w else r['worker_id']}: Leon-Gespräch beendet – bitte prüfen und bestätigen"
                _schritt_merken(conn, r["monat"], f"leon_fertig:{r['worker_id']}", r["worker_id"], jetzt, text)
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

    @app.route("/api/stz-auto/leon/<int:worker_id>", methods=["POST"])
    @login_required
    def stz_auto_leon(worker_id):
        conn = _conn()
        try:
            client = leon_client_factory()
            return jsonify({"success": True, **leon_anruf_starten(conn, client, worker_id, _json().get("monat"))})
        except ValueError as exc:
            return _fehler(exc)
        except Exception as exc:  # LeonError, Netzwerk
            return _fehler(exc, 502)
        finally:
            conn.close()

    @app.route("/api/stz-auto/leon-status/<int:worker_id>")
    @login_required
    def stz_auto_leon_status(worker_id):
        conn = _conn()
        try:
            client = leon_client_factory()
            return jsonify({"success": True, "monat": leon_status_aktualisieren(conn, client, worker_id, request.args.get("monat"))})
        except Exception as exc:
            return _fehler(exc, 502)
        finally:
            conn.close()

    @app.route("/api/stz-auto/leon-stopp/<int:worker_id>", methods=["POST"])
    @login_required
    def stz_auto_leon_stopp(worker_id):
        conn = _conn()
        try:
            return jsonify({"success": True, "monat": leon_abbrechen(conn, leon_client_factory(), worker_id, _json().get("monat"))})
        except Exception as exc:
            return _fehler(exc, 502)
        finally:
            conn.close()

    # Für Render Cron (z. B. täglich 7:00): ab dem 20. den laufenden Monat für
    # alle Mitarbeiter mit festen Zeiten ausfüllen; mit &leon=1 auch anrufen.
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
                    if request.args.get("leon") == "1":
                        leon_anruf_starten(conn, leon_client_factory(), wid, monat)
                        eintrag["leon"] = "gestartet"
                    bericht.append(eintrag)
                except Exception as exc:
                    bericht.append({"worker_id": wid, "fehler": str(exc)})
            return jsonify({"success": True, "monat": monat, "bericht": bericht})
        finally:
            conn.close()
