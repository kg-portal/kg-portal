# =====================================================
# LEON AUTO-KAMPAGNE
# Jeden Morgen die besten Firmen aus den ausgewählten Datenbanken
# (Branchen) im Umkreis automatisch an Leon übergeben.
#
# - Standard: AUS. Nichts wird angerufen, bevor es eingeschaltet wird.
# - Auswahl: nur gewählte Branchen, nur mit Telefonnummer, nur im Umkreis
#   (Standard 30 km um Fliederstraße 59, 47055 Duisburg).
# - Nie erneut: Besichtigung, Interessiert, Rückruf, Kein Interesse,
#   Nicht anrufen, verloren/Angebot/Kunde (das läuft über Tagesliste/Rückruf).
# - Wiederanruf: Tag 1 versucht der Leon-Motor bis zu 3-mal; nicht erreicht →
#   am nächsten Tag eine zweite Runde; danach 3 Wochen Pause.
# - Übergabe nutzt unverändert die bestehende Route
#   /api/leon/datenbank/uebergeben (gleiche Logik wie „Aus Datenbank“).
# =====================================================
import json
import math
import os
from datetime import date, datetime, timedelta

import requests
from flask import jsonify, render_template, request

NICHT_ERREICHT = {"Besetzt", "Nicht erreichbar", "Nicht erreicht", "Anrufbeantworter", "Fax", "Fehler",
                  "OpenAI Fehler", "Abgebrochen"}
ERREICHT_ENDE = {"besichtigung", "interessiert", "rückruf", "rueckruf", "kein interesse", "nicht anrufen",
                 "gesperrt", "termin"}
CRM_ENDE = {"verloren", "kunde", "kunden", "besichtigung", "angebot", "interessiert", "gesperrt",
            "nicht anrufen", "kein interesse", "spaeter"}
PAUSE_TAGE = 21

ZENTRUM_STANDARD = "47055"
# Näherung je PLZ-Bereich (3 Ziffern) für den Fall, dass der PLZ-Dienst nicht erreichbar ist.
PLZ_BEREICH = {
    "470": (51.43, 6.76), "471": (51.47, 6.78), "472": (51.40, 6.74),
    "474": (51.46, 6.62), "475": (51.44, 6.55), "476": (51.52, 6.33), "477": (51.33, 6.56),
    "478": (51.32, 6.55), "479": (51.36, 6.42),
    "460": (51.48, 6.86), "461": (51.50, 6.85), "462": (51.52, 6.93), "463": (51.66, 6.96),
    "464": (51.66, 6.62), "465": (51.57, 6.74),
    "450": (51.45, 7.01), "451": (51.45, 7.01), "452": (51.44, 7.03), "453": (51.47, 6.98),
    "454": (51.43, 6.88), "455": (51.40, 7.19), "456": (51.61, 7.20), "457": (51.66, 7.09),
    "458": (51.52, 7.09), "459": (51.57, 6.99),
    "446": (51.54, 7.22), "447": (51.48, 7.22), "448": (51.47, 7.24),
    "400": (51.23, 6.78), "401": (51.23, 6.78), "402": (51.22, 6.79), "404": (51.25, 6.77),
    "405": (51.20, 6.80), "406": (51.24, 6.86), "407": (51.14, 6.92), "408": (51.29, 6.87),
    "410": (51.19, 6.44), "411": (51.19, 6.44), "412": (51.17, 6.41), "414": (51.20, 6.69),
    "415": (51.20, 6.67), "417": (51.25, 6.39),
    "420": (51.26, 7.15), "421": (51.26, 7.15), "422": (51.27, 7.18), "425": (51.34, 7.04),
    "426": (51.17, 7.08), "427": (51.17, 7.04), "428": (51.18, 7.19),
}


def _jetzt():
    return datetime.now().isoformat(timespec="seconds")


def ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leon_auto_einstellungen (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            aktiv INTEGER NOT NULL DEFAULT 0,
            quelle TEXT NOT NULL DEFAULT 'leads',
            branchen_json TEXT NOT NULL DEFAULT '[]',
            zentrum_plz TEXT NOT NULL DEFAULT '47055',
            radius_km REAL NOT NULL DEFAULT 30,
            tageslimit INTEGER NOT NULL DEFAULT 50,
            startstunde INTEGER NOT NULL DEFAULT 8,
            kampagne_name TEXT NOT NULL DEFAULT 'Leon Auto-Kampagne',
            letzte_ausfuehrung TEXT,
            letzter_bericht TEXT
        )
    """)
    conn.execute("INSERT OR IGNORE INTO leon_auto_einstellungen (id) VALUES (1)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leon_auto_runden (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quelle TEXT NOT NULL,
            crm_id INTEGER NOT NULL,
            datum TEXT NOT NULL,
            runde INTEGER NOT NULL,
            punkte REAL,
            entfernung_km REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_leon_auto_runden ON leon_auto_runden(quelle, crm_id, datum)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS plz_geo (
            plz TEXT PRIMARY KEY,
            lat REAL,
            lon REAL,
            quelle TEXT,
            aktualisiert_am TEXT
        )
    """)
    conn.commit()


def einstellungen(conn):
    ensure_tables(conn)
    row = dict(conn.execute("SELECT * FROM leon_auto_einstellungen WHERE id = 1").fetchone())
    try:
        row["branchen"] = json.loads(row.get("branchen_json") or "[]")
    except ValueError:
        row["branchen"] = []
    try:
        row["letzter_bericht"] = json.loads(row["letzter_bericht"]) if row.get("letzter_bericht") else None
    except ValueError:
        pass
    return row


# ----------------------------------------------------- Entfernung

def _haversine(a, b):
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def plz_koordinaten(conn, plz, online=True):
    plz = str(plz or "").strip()[:5]
    if len(plz) != 5 or not plz.isdigit():
        return None
    row = conn.execute("SELECT lat, lon FROM plz_geo WHERE plz = ?", (plz,)).fetchone()
    if row and row["lat"] is not None:
        return (row["lat"], row["lon"])
    if online:
        try:
            r = requests.get(f"https://api.zippopotam.us/de/{plz}", timeout=3)
            if r.status_code == 200:
                place = (r.json().get("places") or [{}])[0]
                lat, lon = float(place["latitude"]), float(place["longitude"])
                conn.execute("INSERT OR REPLACE INTO plz_geo (plz, lat, lon, quelle, aktualisiert_am) VALUES (?, ?, ?, 'zippopotam', ?)",
                             (plz, lat, lon, _jetzt()))
                conn.commit()
                return (lat, lon)
        except (requests.RequestException, ValueError, KeyError, IndexError):
            pass
    return PLZ_BEREICH.get(plz[:3])


# ----------------------------------------------------- Auswahl

def _letzte_runde(conn, quelle, crm_id):
    return conn.execute(
        "SELECT datum, runde FROM leon_auto_runden WHERE quelle = ? AND crm_id = ? ORDER BY datum DESC, id DESC LIMIT 1",
        (quelle, crm_id),
    ).fetchone()


def kandidaten(conn, e, heute=None, online=True):
    """Bewertete Kandidatenliste (ohne Seiteneffekte außer PLZ-Cache)."""
    heute = heute or date.today()
    branchen = [b for b in e["branchen"] if b][:6]
    if not branchen:
        return [], {"hinweis": "Bitte zuerst 3–4 Datenbanken (Branchen) auswählen."}
    quelle = "tagesliste" if e["quelle"] == "tagesliste" else "leads"
    marks = ",".join("?" for _ in branchen)
    if quelle == "leads":
        rows = conn.execute(f"""
            SELECT l.id, l.firma, l.branche_name AS branche, '' AS ansprechpartner, l.plz, l.stadt AS ort,
                   l.telefon, l.email, l.website, l.status AS crm_status,
                   k.letztes_ergebnis, k.letzter_status, k.aktualisiert_am AS leon_am
            FROM leads l
            LEFT JOIN leon_links k ON k.quelle = 'leads' AND k.crm_id = l.id
            WHERE COALESCE(TRIM(l.telefon), '') <> '' AND TRIM(l.branche_name) IN ({marks})
              AND NOT EXISTS (
                  SELECT 1 FROM tagesliste_leads t
                  JOIN tagesliste_status_history h ON h.tagesliste_id = t.id
                  WHERE t.source_lead_id = l.id AND h.status IN ('verloren', 'besichtigung', 'angebot'))
        """, branchen).fetchall()
    else:
        rows = conn.execute(f"""
            SELECT t.id, t.firma, t.branche, t.ansprechpartner, t.plz, t.ort, t.telefon, t.email, t.website,
                   t.status AS crm_status, k.letztes_ergebnis, k.letzter_status, k.aktualisiert_am AS leon_am
            FROM tagesliste_leads t
            LEFT JOIN leon_links k ON k.tagesliste_id = t.id
            WHERE COALESCE(TRIM(t.telefon), '') <> '' AND TRIM(t.branche) IN ({marks})
              AND COALESCE(t.status, 'offen') IN ('offen', 'neu', 'angerufen')
              AND NOT EXISTS (SELECT 1 FROM tagesliste_status_history h
                              WHERE h.tagesliste_id = t.id AND h.status IN ('verloren', 'besichtigung', 'angebot'))
        """, branchen).fetchall()

    zentrum = plz_koordinaten(conn, e["zentrum_plz"], online) or PLZ_BEREICH["470"]
    radius = float(e["radius_km"] or 30)
    stat = {"gesamt": len(rows), "zu_weit": 0, "ohne_ort": 0, "erledigt": 0, "pause": 0, "heute_schon": 0}
    ergebnis = []
    for r in rows:
        r = dict(r)
        if str(r.get("crm_status") or "").strip().lower() in CRM_ENDE:
            stat["erledigt"] += 1
            continue
        if str(r.get("letztes_ergebnis") or "").strip().lower() in ERREICHT_ENDE:
            stat["erledigt"] += 1
            continue
        lr = _letzte_runde(conn, quelle, r["id"])
        runde, bonus = 1, 15
        if lr:
            letzte = date.fromisoformat(lr["datum"])
            nicht_erreicht = (r.get("letzter_status") in NICHT_ERREICHT) or not r.get("letztes_ergebnis")
            if letzte >= heute:
                stat["heute_schon"] += 1
                continue
            if not nicht_erreicht:
                stat["erledigt"] += 1
                continue
            if lr["runde"] == 1:
                runde, bonus = 2, 10
            elif (heute - letzte).days >= PAUSE_TAGE:
                runde, bonus = 1, 5
            else:
                stat["pause"] += 1
                continue
        geo = plz_koordinaten(conn, r.get("plz"), online)
        if not geo:
            stat["ohne_ort"] += 1
            continue
        km = _haversine(zentrum, geo)
        if km > radius:
            stat["zu_weit"] += 1
            continue
        prio = 30 - 5 * branchen.index((r.get("branche") or "").strip()) if (r.get("branche") or "").strip() in branchen else 10
        punkte = prio + 30 * (1 - km / radius) + bonus
        punkte += 5 if r.get("website") else 0
        punkte += 5 if r.get("email") else 0
        punkte += 5 if r.get("ansprechpartner") else 0
        r.update(runde=runde, entfernung_km=round(km, 1), punkte=round(punkte, 1))
        ergebnis.append(r)
    ergebnis.sort(key=lambda x: (-x["punkte"], x["entfernung_km"]))
    stat["moeglich"] = len(ergebnis)
    return ergebnis, stat


# ----------------------------------------------------- Ausführen

def ausfuehren(app, conn, leon_client, e, heute=None):
    heute = heute or date.today()
    quelle = "tagesliste" if e["quelle"] == "tagesliste" else "leads"
    liste, stat = kandidaten(conn, e, heute)
    auswahl = liste[: int(e["tageslimit"] or 50)]
    bericht = {"datum": heute.isoformat(), "zeit": _jetzt(), "statistik": stat, "uebergeben": 0, "hinweise": []}
    if not auswahl:
        bericht["hinweise"].append(stat.get("hinweis") or "Keine passenden Firmen gefunden.")
        return bericht

    # Kampagne finden (oder von der Übergabe-Route neu anlegen lassen)
    _code, data = leon_client.request("GET", "/api/campaigns")
    kampagne = next((c for c in (data or {}).get("campaigns", [])
                     if (c.get("name") or "").strip().lower() == e["kampagne_name"].strip().lower()), None)

    # Runde 2: Lead ist noch in der Kampagne → herausnehmen, damit er neu eingereiht wird
    if kampagne:
        for r in auswahl:
            if r["runde"] == 2:
                if quelle == "leads":
                    link = conn.execute("SELECT leon_lead_id FROM leon_links WHERE quelle = 'leads' AND crm_id = ?", (r["id"],)).fetchone()
                else:
                    link = conn.execute("SELECT leon_lead_id FROM leon_links WHERE tagesliste_id = ?", (r["id"],)).fetchone()
                if link and link["leon_lead_id"]:
                    try:
                        leon_client.request("DELETE", f"/api/campaigns/{kampagne['id']}/leads/{int(link['leon_lead_id'])}", timeout=30)
                    except Exception:
                        pass

    client = app.test_client()
    with client.session_transaction() as sess:
        sess["logged_in"] = True
    body = {"quelle": quelle, "ids": [r["id"] for r in auswahl]}
    if kampagne:
        body["kampagne_id"] = kampagne["id"]
    else:
        body["neue_kampagne"] = e["kampagne_name"]
    resp = client.post("/api/leon/datenbank/uebergeben", json=body)
    result = resp.get_json(silent=True) or {}
    if not result.get("success"):
        bericht["hinweise"].append(result.get("error") or f"Übergabe fehlgeschlagen (HTTP {resp.status_code}).")
        return bericht
    bericht["uebergeben"] = result.get("uebernommen", 0)
    bericht["in_kampagne"] = result.get("in_kampagne", 0)
    bericht["kampagne_id"] = result.get("kampagne_id")
    bericht["hinweise"] += result.get("hinweise") or []
    for r in auswahl:
        conn.execute("INSERT INTO leon_auto_runden (quelle, crm_id, datum, runde, punkte, entfernung_km) VALUES (?, ?, ?, ?, ?, ?)",
                     (quelle, r["id"], heute.isoformat(), r["runde"], r["punkte"], r["entfernung_km"]))
    conn.commit()
    bericht["firmen"] = [{"firma": r["firma"], "punkte": r["punkte"], "km": r["entfernung_km"], "runde": r["runde"]} for r in auswahl]

    # Kampagne starten (im Motor darf nur eine Kampagne aktiv sein)
    kid = result.get("kampagne_id")
    if kid:
        code, ctl = leon_client.request("POST", f"/api/campaigns/{kid}/control", {"action": "start"}, timeout=30)
        if code == 409 and ctl.get("active_campaign_id") and int(ctl["active_campaign_id"]) != int(kid):
            bericht["hinweise"].append("Eine andere Leon-Kampagne ist aktiv – die Auto-Kampagne startet, sobald sie pausiert ist.")
        elif not ctl.get("success") and code >= 400:
            bericht["hinweise"].append(ctl.get("error") or "Kampagne konnte nicht gestartet werden.")
        else:
            bericht["gestartet"] = True
    return bericht


# ----------------------------------------------------- Routen

def register_leon_auto_kampagne(app, login_required, get_db_connection, leon_client_factory):

    def _conn():
        conn = get_db_connection()
        ensure_tables(conn)
        return conn

    @app.route("/leon/auto")
    @login_required
    def leon_auto_seite():
        from leon_routes import LEON_TABS
        return render_template("leon_auto.html", leon_tab="auto", leon_tabs=LEON_TABS)

    @app.route("/api/leon/auto/einstellungen", methods=["GET", "POST"])
    @login_required
    def leon_auto_einstellungen():
        conn = _conn()
        try:
            if request.method == "POST":
                d = request.get_json(silent=True) or {}
                branchen = [str(b).strip()[:120] for b in (d.get("branchen") or []) if str(b).strip()][:6]
                plz = str(d.get("zentrum_plz") or ZENTRUM_STANDARD).strip()[:5]
                if not (len(plz) == 5 and plz.isdigit()):
                    return jsonify({"success": False, "error": "Zentrum: bitte eine 5-stellige PLZ."}), 400
                try:
                    radius = max(1.0, min(150.0, float(d.get("radius_km") or 30)))
                    limit = max(1, min(300, int(d.get("tageslimit") or 50)))
                    stunde = max(6, min(18, int(d.get("startstunde") or 8)))
                except (TypeError, ValueError):
                    return jsonify({"success": False, "error": "Zahlen prüfen (Radius, Tageslimit, Startstunde)."}), 400
                conn.execute("""
                    UPDATE leon_auto_einstellungen SET aktiv = ?, quelle = ?, branchen_json = ?, zentrum_plz = ?,
                        radius_km = ?, tageslimit = ?, startstunde = ?, kampagne_name = ? WHERE id = 1
                """, (1 if d.get("aktiv") else 0, "tagesliste" if d.get("quelle") == "tagesliste" else "leads",
                      json.dumps(branchen, ensure_ascii=False), plz, radius, limit, stunde,
                      (str(d.get("kampagne_name") or "Leon Auto-Kampagne").strip()[:120] or "Leon Auto-Kampagne")))
                conn.commit()
            return jsonify({"success": True, "einstellungen": einstellungen(conn)})
        finally:
            conn.close()

    @app.route("/api/leon/auto/vorschau")
    @login_required
    def leon_auto_vorschau():
        conn = _conn()
        try:
            e = einstellungen(conn)
            liste, stat = kandidaten(conn, e)
            limit = int(e["tageslimit"] or 50)
            return jsonify({"success": True, "statistik": stat, "auswahl": liste[:limit], "weitere": max(0, len(liste) - limit)})
        finally:
            conn.close()

    @app.route("/api/leon/auto/ausfuehren", methods=["POST"])
    @login_required
    def leon_auto_ausfuehren():
        conn = _conn()
        try:
            e = einstellungen(conn)
            bericht = ausfuehren(app, conn, leon_client_factory(), e)
            conn.execute("UPDATE leon_auto_einstellungen SET letzte_ausfuehrung = ?, letzter_bericht = ? WHERE id = 1",
                         (_jetzt(), json.dumps(bericht, ensure_ascii=False)))
            conn.commit()
            return jsonify({"success": True, "bericht": bericht})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 502
        finally:
            conn.close()

    # Render Cron, z. B. stündlich zwischen 7 und 11 Uhr:
    #   /internal/leon-auto-kampagne?token=…  (LEON_AUTO_TOKEN oder INTERNAL_CRON_TOKEN)
    # Läuft nur, wenn eingeschaltet, höchstens einmal pro Tag, ab der Startstunde, Mo–Fr.
    @app.route("/internal/leon-auto-kampagne", methods=["GET", "POST"])
    def leon_auto_cron():
        token = (os.getenv("LEON_AUTO_TOKEN") or os.getenv("INTERNAL_CRON_TOKEN") or "").strip()
        given = (request.args.get("token") or request.headers.get("X-Cron-Token") or "").strip()
        if not token or given != token:
            return jsonify({"success": False, "error": "Zugriff verweigert"}), 403
        conn = _conn()
        try:
            e = einstellungen(conn)
            jetzt = datetime.now()
            if not e["aktiv"]:
                return jsonify({"success": True, "info": "Auto-Kampagne ist ausgeschaltet."})
            if jetzt.weekday() >= 5:
                return jsonify({"success": True, "info": "Wochenende – nichts getan."})
            if jetzt.hour < int(e["startstunde"] or 8):
                return jsonify({"success": True, "info": "Noch vor der Startstunde."})
            if str(e.get("letzte_ausfuehrung") or "")[:10] == jetzt.date().isoformat():
                return jsonify({"success": True, "info": "Heute schon ausgeführt."})
            bericht = ausfuehren(app, conn, leon_client_factory(), e)
            conn.execute("UPDATE leon_auto_einstellungen SET letzte_ausfuehrung = ?, letzter_bericht = ? WHERE id = 1",
                         (_jetzt(), json.dumps(bericht, ensure_ascii=False)))
            conn.commit()
            return jsonify({"success": True, "bericht": bericht})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 502
        finally:
            conn.close()
