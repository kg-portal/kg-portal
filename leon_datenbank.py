# =====================================================
# LEON <-> DATENBANK / TAGESLISTE
#
# 1) Übergabe: Firmen aus Tagesliste oder Datenbank (leads) werden als
#    Leads zum Leon-Motor geschickt und einer Kampagne zugeordnet.
# 2) Rückmeldung: Nach Leons Anruf wird der Status in der Tagesliste
#    genau so gesetzt, wie Damla es per Klick macht (gleiche Route):
#       Besichtigung -> besichtigung (mit vorbefüllten Besichtigungsdaten)
#       Interessiert -> interessiert
#       Rückruf      -> spaeter (mit Datum)
#       Kein Interesse / Nicht anrufen -> verloren
#       erreicht, sonst nichts -> angerufen
#    Nicht erreicht (Besetzt, AB, …) ändert nichts an der Tagesliste.
# =====================================================

import json
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import jsonify, render_template, request

NICHT_ERREICHT = {"Besetzt", "Nicht erreichbar", "Anrufbeantworter", "Fax", "Fehler", "OpenAI Fehler", "Abgebrochen"}

BEREICH_TYP = {
    "büro": "Büro",
    "buero": "Büro",
    "wc/sanitär": "WC",
    "wc": "WC",
    "sanitär": "WC",
    "küche": "Küche",
    "kueche": "Küche",
    "flur": "Flur",
}

WOCHENTAGE = {
    "mo": "Mo", "montag": "Mo", "di": "Di", "dienstag": "Di", "mi": "Mi", "mittwoch": "Mi",
    "do": "Do", "donnerstag": "Do", "fr": "Fr", "freitag": "Fr", "sa": "Sa", "samstag": "Sa",
    "so": "So", "sonntag": "So",
}

_sync_lock = threading.Lock()


def ensure_leon_links(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS leon_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            quelle TEXT NOT NULL,
            crm_id INTEGER NOT NULL,
            tagesliste_id INTEGER,
            leon_lead_id INTEGER,
            kampagne_id INTEGER,
            firma TEXT,
            telefon TEXT,
            letzter_call_id INTEGER,
            letzter_status TEXT,
            letztes_ergebnis TEXT,
            aktualisiert_am TEXT,
            erstellt_am TEXT,
            UNIQUE(quelle, crm_id)
        )
        """
    )
    conn.commit()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _clean(value, limit=300):
    return str(value or "").strip()[:limit]


def _besichtigung_data(link, reinigung, summary):
    """Leons Angaben im Format der Besichtigungsseite (raeume, einsatzzeiten, notiz)."""
    raeume = []
    for bereich in reinigung.get("bereiche") or []:
        if not isinstance(bereich, dict):
            continue
        name = _clean(bereich.get("bereich")) or "Sonstiges"
        typ = BEREICH_TYP.get(name.lower(), name)
        raeume.append({
            "section": "Unterhaltsreinigung",
            "typ": typ,
            "name": typ,
            "m2": _clean(bereich.get("m2")),
            "haeufigkeit": _clean(bereich.get("haeufigkeit")),
        })

    einsatzzeiten = []
    for teil in re.split(r"[,/;]|\bund\b", _clean(reinigung.get("wunschtage")).lower()):
        tag = WOCHENTAGE.get(teil.strip().rstrip("."))
        if tag and tag not in [e["tag"] for e in einsatzzeiten]:
            einsatzzeiten.append({"tag": tag, "uhrzeit": ""})

    termin = " ".join(p for p in (_clean(reinigung.get("besichtigung_datum")), _clean(reinigung.get("besichtigung_uhrzeit"))) if p)
    notiz_teile = ["Von Leon am Telefon erfasst."]
    if termin:
        notiz_teile.append("Besichtigungstermin: " + termin + (" – " + _clean(reinigung.get("besichtigung_adresse")) if _clean(reinigung.get("besichtigung_adresse")) else ""))
    if _clean(reinigung.get("uhrzeit_wunsch")):
        notiz_teile.append("Uhrzeit-Wunsch: " + _clean(reinigung.get("uhrzeit_wunsch")))
    if _clean(reinigung.get("aktuelle_firma")):
        notiz_teile.append("Bisherige Firma: " + _clean(reinigung.get("aktuelle_firma")) + (" (" + _clean(reinigung.get("zufriedenheit")) + ")" if _clean(reinigung.get("zufriedenheit")) else ""))
    if summary:
        notiz_teile.append("Gespräch: " + _clean(summary, 1000))

    leistungen = reinigung.get("leistungen") or []
    if not isinstance(leistungen, list):
        leistungen = [leistungen]

    return {
        "kunde": {
            "firma": link["firma"] or "",
            "ansprechpartner": _clean(reinigung.get("ansprechpartner")),
            "telefon": link["telefon"] or "",
        },
        "leistungen": [_clean(l) for l in leistungen if _clean(l)] or ["Unterhaltsreinigung"],
        "raeume": raeume,
        "elemente": {"arbeitstische": "0", "pc_monitor": "0", "muellbehaelter": "0"},
        "sonstiges": {},
        "einsatzzeiten": einsatzzeiten,
        "notiz": "\n".join(notiz_teile),
        "quelle": "Leon",
        "gespeichert_am": datetime.now().isoformat(),
    }


def register_leon_datenbank(app, login_required, get_db_connection, leon_client, LeonError):

    conn = get_db_connection()
    try:
        ensure_leon_links(conn)
    finally:
        conn.close()

    def crm_client():
        """Interner Aufruf der bestehenden Tagesliste-Routen (gleiche Logik wie Damlas Klick)."""
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        return client

    # -------------------------------------------------
    # Seite
    # -------------------------------------------------

    @app.route("/api/leon/datenbank/branchen")
    @login_required
    def leon_db_branchen():
        conn = get_db_connection()
        try:
            rows = conn.execute(
                """
                SELECT b FROM (
                    SELECT TRIM(branche_name) AS b FROM leads
                    UNION
                    SELECT TRIM(branche) AS b FROM tagesliste_leads
                )
                WHERE COALESCE(b, '') <> ''
                ORDER BY b COLLATE NOCASE
                """
            ).fetchall()
        finally:
            conn.close()
        return jsonify({"success": True, "branchen": [r["b"] for r in rows]})

    @app.route("/api/leon/datenbank/kandidaten")
    @login_required
    def leon_db_kandidaten():
        quelle = request.args.get("quelle", "tagesliste")
        branche = _clean(request.args.get("branche"))
        ort = _clean(request.args.get("ort"))
        suche = _clean(request.args.get("q"))
        nur_neu = request.args.get("nur_neu", "1") == "1"
        try:
            limit = max(1, min(500, int(request.args.get("limit") or 200)))
        except ValueError:
            limit = 200

        params = []
        if quelle == "leads":
            sql = """
                SELECT l.id, l.firma, l.branche_name AS branche, '' AS ansprechpartner,
                       l.strasse, l.plz, l.stadt AS ort, l.telefon, l.email, l.website,
                       l.status AS crm_status, k.letztes_ergebnis, k.letzter_status
                FROM leads l
                LEFT JOIN leon_links k ON k.quelle = 'leads' AND k.crm_id = l.id
                WHERE COALESCE(TRIM(l.telefon), '') <> ''
            """
            if branche:
                sql += " AND l.branche_name = ?"
                params.append(branche)
            if ort:
                sql += " AND (l.stadt LIKE ? OR l.plz LIKE ?)"
                params += [f"%{ort}%", f"{ort}%"]
            if suche:
                sql += " AND l.firma LIKE ?"
                params.append(f"%{suche}%")
        else:
            sql = """
                SELECT t.id, t.firma, t.branche, t.ansprechpartner, t.strasse, t.plz, t.ort,
                       t.telefon, t.email, t.website,
                       CASE
                           WHEN t.interessiert_am IS NOT NULL THEN 'interessiert'
                           WHEN t.angerufen_am IS NOT NULL THEN 'angerufen'
                           ELSE t.status
                       END AS crm_status,
                       k.letztes_ergebnis, k.letzter_status
                FROM tagesliste_leads t
                LEFT JOIN leon_links k ON k.tagesliste_id = t.id
                WHERE COALESCE(TRIM(t.telefon), '') <> ''
                  AND COALESCE(t.status, 'offen') = 'offen'
                  AND NOT EXISTS (
                      SELECT 1 FROM tagesliste_status_history h
                      WHERE h.tagesliste_id = t.id AND h.status IN ('verloren', 'besichtigung', 'angebot')
                  )
            """
            if branche:
                sql += " AND t.branche = ?"
                params.append(branche)
            if ort:
                sql += " AND (t.ort LIKE ? OR t.plz LIKE ?)"
                params += [f"%{ort}%", f"{ort}%"]
            if suche:
                sql += " AND t.firma LIKE ?"
                params.append(f"%{suche}%")
        if nur_neu:
            sql += " AND k.id IS NULL"
        sql += " ORDER BY 1 DESC LIMIT ?"
        params.append(limit)

        conn = get_db_connection()
        try:
            rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()
        return jsonify({"success": True, "kandidaten": rows})

    # -------------------------------------------------
    # Übergabe an Leon
    # -------------------------------------------------

    @app.route("/api/leon/datenbank/uebergeben", methods=["POST"])
    @login_required
    def leon_db_uebergeben():
        data = request.get_json(silent=True) or {}
        quelle = "leads" if data.get("quelle") == "leads" else "tagesliste"
        ids = []
        for value in data.get("ids") or []:
            try:
                ids.append(int(value))
            except (TypeError, ValueError):
                continue
        ids = ids[:500]
        if not ids:
            return jsonify({"success": False, "error": "Bitte mindestens eine Firma auswählen."}), 400

        try:
            kampagne_id = int(data.get("kampagne_id") or 0)
        except (TypeError, ValueError):
            kampagne_id = 0
        neue_kampagne = _clean(data.get("neue_kampagne"), 120)

        try:
            if not kampagne_id:
                if not neue_kampagne:
                    return jsonify({"success": False, "error": "Bitte Kampagne wählen oder neuen Namen eingeben."}), 400
                code, created = leon_client.request("POST", "/api/campaigns", {"name": neue_kampagne, "agent_id": 1})
                kampagne_id = created.get("id") or (created.get("campaign") or {}).get("id")
                if not kampagne_id:
                    return jsonify({"success": False, "error": created.get("error") or "Kampagne konnte nicht angelegt werden."}), 502
        except LeonError as exc:
            return jsonify({"success": False, "error": str(exc)}), 502

        conn = get_db_connection()
        try:
            placeholders = ",".join("?" for _ in ids)
            if quelle == "leads":
                rows = conn.execute(
                    f"""SELECT id, firma, branche_name AS branche, '' AS ansprechpartner, strasse, plz,
                               stadt AS ort, telefon, email, website
                        FROM leads WHERE id IN ({placeholders})""",
                    ids,
                ).fetchall()
            else:
                rows = conn.execute(
                    f"""SELECT id, firma, branche, ansprechpartner, strasse, plz, ort, telefon, email, website
                        FROM tagesliste_leads WHERE id IN ({placeholders})""",
                    ids,
                ).fetchall()

            leon_ids = []
            fehler = []
            for row in rows:
                if not _clean(row["telefon"]):
                    fehler.append(f"{row['firma']}: keine Telefonnummer")
                    continue
                try:
                    code, resp = leon_client.request(
                        "POST",
                        "/api/leads",
                        {
                            "firma": row["firma"],
                            "telefon": row["telefon"],
                            "ansprechpartner": row["ansprechpartner"] or "",
                            "strasse": row["strasse"] or "",
                            "plz": row["plz"] or "",
                            "stadt": row["ort"] or "",
                            "branche": row["branche"] or "",
                            "email": row["email"] or "",
                            "website": row["website"] or "",
                            "source": "KG CRM " + ("Tagesliste" if quelle == "tagesliste" else "Datenbank"),
                            "notes": f"CRM-Ref {quelle}:{row['id']}",
                        },
                    )
                except LeonError as exc:
                    return jsonify({"success": False, "error": str(exc)}), 502
                leon_lead_id = resp.get("id") or resp.get("duplicate_id")
                if not leon_lead_id:
                    fehler.append(f"{row['firma']}: {resp.get('error') or 'nicht übernommen'}")
                    continue
                leon_ids.append(leon_lead_id)
                conn.execute(
                    """
                    INSERT INTO leon_links (quelle, crm_id, tagesliste_id, leon_lead_id, kampagne_id, firma, telefon, erstellt_am, aktualisiert_am)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(quelle, crm_id) DO UPDATE SET
                        leon_lead_id = excluded.leon_lead_id,
                        kampagne_id = excluded.kampagne_id,
                        aktualisiert_am = excluded.aktualisiert_am
                    """,
                    (
                        quelle, row["id"], row["id"] if quelle == "tagesliste" else None,
                        leon_lead_id, kampagne_id, row["firma"], row["telefon"], _now(), _now(),
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        hinzugefuegt = 0
        if leon_ids:
            try:
                code, added = leon_client.request("POST", f"/api/campaigns/{kampagne_id}/leads", {"lead_ids": leon_ids})
                hinzugefuegt = added.get("added") or 0
                if not added.get("success"):
                    fehler.append(added.get("error") or "Zuordnung zur Kampagne fehlgeschlagen.")
            except LeonError as exc:
                fehler.append(str(exc))

        return jsonify({
            "success": True,
            "kampagne_id": kampagne_id,
            "uebernommen": len(leon_ids),
            "in_kampagne": hinzugefuegt,
            "hinweise": fehler[:20],
        })

    # -------------------------------------------------
    # Rückmeldung in die Tagesliste
    # -------------------------------------------------

    def _tagesliste_id_fuer(conn, client, link):
        if link["tagesliste_id"]:
            return link["tagesliste_id"]
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (link["crm_id"],)).fetchone()
        if not lead:
            return None
        resp = client.post("/datenbank/tagesliste-add", json={
            "firma": lead["firma"] or "",
            "branche": lead["branche_name"] or "",
            "strasse": lead["strasse"] or "",
            "plz": lead["plz"] or "",
            "ort": lead["stadt"] or "",
            "telefon": lead["telefon"] or "",
            "email": lead["email"] or "",
            "website": lead["website"] or "",
            "quelle": "Leon · " + (lead["branche_name"] or "Datenbank"),
            "source_lead_id": lead["id"],
        }).get_json() or {}
        tl_id = resp.get("id")
        if not tl_id:
            row = conn.execute(
                "SELECT id FROM tagesliste_leads WHERE source_lead_id = ? ORDER BY id DESC LIMIT 1",
                (lead["id"],),
            ).fetchone()
            tl_id = row["id"] if row else None
        if tl_id:
            conn.execute("UPDATE leon_links SET tagesliste_id = ? WHERE id = ?", (tl_id, link["id"]))
        return tl_id

    def _notiz_anhaengen(conn, tagesliste_id, text):
        row = conn.execute("SELECT notiz FROM tagesliste_leads WHERE id = ?", (tagesliste_id,)).fetchone()
        alt = (row["notiz"] or "") if row else ""
        neu = (alt.rstrip() + "\n" if alt.strip() else "") + text
        conn.execute("UPDATE tagesliste_leads SET notiz = ? WHERE id = ?", (neu, tagesliste_id))

    def sync_ergebnisse():
        """Überträgt neue Leon-Ergebnisse in die Tagesliste. Gibt eine kurze Bilanz zurück."""
        if not leon_client.configured():
            return {"ok": False, "error": "Leon nicht eingerichtet."}
        if not _sync_lock.acquire(blocking=False):
            return {"ok": True, "laeuft_bereits": True}
        bilanz = {"ok": True, "geprueft": 0, "aktualisiert": 0, "nicht_erreicht": 0}
        try:
            calls = []
            for archived in ("0", "1"):
                code, data = leon_client.request("GET", f"/api/calls?archived={archived}", timeout=60)
                calls += (data or {}).get("calls") or []
            neueste = {}
            for call in calls:
                lead_id = call.get("lead_id")
                if not lead_id:
                    continue
                if lead_id not in neueste or (call.get("id") or 0) > (neueste[lead_id].get("id") or 0):
                    neueste[lead_id] = call

            conn = get_db_connection()
            client = crm_client()
            try:
                links = conn.execute("SELECT * FROM leon_links WHERE leon_lead_id IS NOT NULL").fetchall()
                for link in links:
                    call = neueste.get(link["leon_lead_id"])
                    if not call or (link["letzter_call_id"] or 0) >= (call.get("id") or 0):
                        continue
                    bilanz["geprueft"] += 1
                    status = call.get("status") or ""
                    ergebnis = call.get("result") or ""
                    summary = call.get("summary") or ""
                    beendet = status in NICHT_ERREICHT or status == "Beendet"
                    if not beendet:
                        continue  # läuft noch
                    if status == "Beendet" and not summary and not ergebnis:
                        continue  # Auswertung kommt noch

                    # Atomar "reservieren", damit parallele Prozesse nichts doppelt eintragen
                    claimed = conn.execute(
                        "UPDATE leon_links SET letzter_call_id = ? WHERE id = ? AND COALESCE(letzter_call_id, 0) < ?",
                        (call.get("id"), link["id"], call.get("id")),
                    ).rowcount
                    conn.commit()
                    if claimed != 1:
                        continue

                    erreicht = status == "Beendet" and (ergebnis or (call.get("duration_seconds") or 0) >= 15)
                    if not erreicht:
                        bilanz["nicht_erreicht"] += 1
                        conn.execute(
                            "UPDATE leon_links SET letzter_call_id = ?, letzter_status = ?, aktualisiert_am = ? WHERE id = ?",
                            (call.get("id"), status, _now(), link["id"]),
                        )
                        continue

                    tl_id = _tagesliste_id_fuer(conn, client, link)
                    if not tl_id:
                        continue
                    conn.commit()

                    try:
                        reinigung = json.loads(call.get("reinigung_json") or "{}") or {}
                    except ValueError:
                        reinigung = {}

                    payload = {"id": tl_id}
                    anzeige = ergebnis
                    if int(call.get("do_not_call") or 0) == 1 or ergebnis == "Kein Interesse":
                        payload["status"] = "verloren"
                        anzeige = "Kein Interesse"
                    elif ergebnis == "Rechnung":
                        payload["status"] = "besichtigung"
                        payload["besichtigung_data_json"] = _besichtigung_data(link, reinigung, summary)
                        anzeige = "Besichtigung"
                    elif ergebnis == "Interessiert":
                        payload["status"] = "interessiert"
                    elif ergebnis == "Rückruf":
                        payload["status"] = "spaeter"
                        payload["spaeter_datum"] = _clean(call.get("callback_at"))[:10]
                    else:
                        payload["status"] = "angerufen"
                        anzeige = "Angerufen"

                    # Zuerst 'angerufen' festhalten (wie beim manuellen Anruf), dann das Ergebnis
                    if payload["status"] != "angerufen":
                        client.post("/datenbank/tagesliste-status", json={"id": tl_id, "status": "angerufen"})
                    client.post("/datenbank/tagesliste-status", json=payload)

                    zeit = datetime.now(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y %H:%M")
                    _notiz_anhaengen(conn, tl_id, f"[Leon {zeit}] {anzeige}: {_clean(summary, 600)}".rstrip(": "))
                    conn.execute(
                        """UPDATE leon_links SET letzter_call_id = ?, letzter_status = ?, letztes_ergebnis = ?,
                           aktualisiert_am = ? WHERE id = ?""",
                        (call.get("id"), status, anzeige, _now(), link["id"]),
                    )
                    conn.commit()
                    bilanz["aktualisiert"] += 1
                conn.commit()
            finally:
                conn.close()
        except LeonError as exc:
            bilanz = {"ok": False, "error": str(exc)}
        finally:
            _sync_lock.release()
        return bilanz

    @app.route("/api/leon/datenbank/sync", methods=["POST"])
    @login_required
    def leon_db_sync():
        return jsonify(sync_ergebnisse())

    @app.route("/api/leon/datenbank/verknuepfungen")
    @login_required
    def leon_db_verknuepfungen():
        conn = get_db_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM leon_links ORDER BY COALESCE(aktualisiert_am, erstellt_am) DESC LIMIT 300"
            ).fetchall()
        finally:
            conn.close()
        return jsonify({"success": True, "links": [dict(r) for r in rows]})

    def hintergrund():
        time.sleep(60)
        while True:
            try:
                if leon_client.configured():
                    sync_ergebnisse()
            except Exception as exc:  # Hintergrund darf nie abstürzen
                print("Leon-Sync Fehler:", exc)
            time.sleep(300)

    threading.Thread(target=hintergrund, daemon=True, name="leon-datenbank-sync").start()

    return sync_ergebnisse
