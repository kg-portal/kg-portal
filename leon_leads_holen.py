# =====================================================
# LEON KAMPAGNE: „Leads holen“
# Im Kampagnen-Fenster „Leads“: Anzahl → Branche → Stadt (oder gemischt
# 30 km um Duisburg) → die besten Firmen aus der Datenbank vorschlagen.
#
# - Nur lesend. Hinzufügen läuft unverändert über die bestehende Route
#   /api/leon/datenbank/uebergeben (wie „Aus Datenbank“).
# - Bewertung ohne KI, wie Auto-Kampagne: Lead-Sammler-Punkte, Nähe zu
#   Duisburg, Website, E-Mail.
# - Nie vorgeschlagen: ohne Telefon, Besichtigung/Angebot/Kunde/Verloren,
#   Kein Interesse/Nicht anrufen, schon an Leon gegeben (außer nicht
#   erreicht und älter als 21 Tage).
#
# ChatGPT (KG Daten, kg_mcp): /internal/mcp/leon-kampagne mit Token
# (KG_MCP_TOKEN in tokenlar.env) – Branchen/Städte/Vorschlag ansehen und
# eine Kampagne als Entwurf anlegen. Gestartet wird NIE automatisch.
# =====================================================
import hmac
import os
from datetime import datetime, timedelta

from flask import jsonify, request

from leon_auto_kampagne import (CRM_ENDE, ERREICHT_ENDE, NICHT_ERREICHT, PAUSE_TAGE, PLZ_BEREICH,
                                ZENTRUM_STANDARD, _haversine, ensure_tables, plz_koordinaten)
from leon_datenbank import ensure_leon_links

UMKREIS_KM = 30
MCP_MAX_ANZAHL = 100


def _branchen_param():
    return [b.strip()[:120] for b in request.args.getlist("branche") if b.strip()][:10]


def _basis_where(branchen):
    marks = ",".join("?" for _ in branchen)
    return f"""
        COALESCE(TRIM(l.telefon), '') <> '' AND TRIM(l.branche_name) IN ({marks})
        AND NOT EXISTS (
            SELECT 1 FROM tagesliste_leads t
            JOIN tagesliste_status_history h ON h.tagesliste_id = t.id
            WHERE t.source_lead_id = l.id AND h.status IN ('verloren', 'besichtigung', 'angebot'))
    """


def _frei(r, grenze):
    """Darf diese Firma vorgeschlagen werden?"""
    if str(r.get("crm_status") or "").strip().lower() in CRM_ENDE:
        return False
    if str(r.get("letztes_ergebnis") or "").strip().lower() in ERREICHT_ENDE:
        return False
    if r.get("link_id"):  # schon einmal an Leon gegeben
        nicht_erreicht = (r.get("letzter_status") in NICHT_ERREICHT) or not r.get("letztes_ergebnis")
        return nicht_erreicht and str(r.get("leon_am") or "") < grenze
    return True


def _branchen_liste(conn):
    rows = conn.execute("""
        SELECT TRIM(branche_name) AS name, COUNT(*) AS anzahl FROM leads
        WHERE COALESCE(TRIM(telefon), '') <> '' AND COALESCE(TRIM(branche_name), '') <> ''
        GROUP BY TRIM(branche_name) ORDER BY anzahl DESC, name COLLATE NOCASE
    """).fetchall()
    return [dict(r) for r in rows]


def _staedte(conn, branchen):
    rows = conn.execute(f"""
        SELECT TRIM(l.stadt) AS ort, COUNT(*) AS anzahl FROM leads l
        WHERE {_basis_where(branchen)} AND COALESCE(TRIM(l.stadt), '') <> ''
        GROUP BY TRIM(l.stadt) COLLATE NOCASE ORDER BY anzahl DESC, ort COLLATE NOCASE LIMIT 60
    """, branchen).fetchall()
    return [dict(r) for r in rows]


def _vorschlag(conn, branchen, stadt, anzahl):
    """Die besten freien Firmen: (Auswahl, Anzahl möglicher Firmen)."""
    gemischt = not stadt or stadt == "gemischt"
    grenze = (datetime.now() - timedelta(days=PAUSE_TAGE)).strftime("%Y-%m-%d")
    sql = f"""
        SELECT l.id, l.firma, l.branche_name AS branche, l.plz, l.stadt AS ort, l.telefon, l.email,
               l.website, l.status AS crm_status, l.ls_punkte,
               k.id AS link_id, k.letztes_ergebnis, k.letzter_status, k.aktualisiert_am AS leon_am
        FROM leads l
        LEFT JOIN leon_links k ON k.quelle = 'leads' AND k.crm_id = l.id
        WHERE {_basis_where(branchen)}
    """
    params = list(branchen)
    if not gemischt:
        sql += " AND TRIM(l.stadt) = ? COLLATE NOCASE"
        params.append(stadt)

    zentrum = plz_koordinaten(conn, ZENTRUM_STANDARD, online=False) or PLZ_BEREICH["470"]
    liste = []
    for r in conn.execute(sql, params).fetchall():
        r = dict(r)
        if not _frei(r, grenze):
            continue
        geo = plz_koordinaten(conn, r.get("plz"), online=False)
        km = _haversine(zentrum, geo) if geo else None
        if gemischt and (km is None or km > UMKREIS_KM):
            continue
        punkte = int(r.get("ls_punkte") or 0) / 2
        punkte += 30 * max(0.0, 1 - km / UMKREIS_KM) if km is not None else 0
        punkte += 5 if r.get("website") else 0
        punkte += 5 if r.get("email") else 0
        liste.append({
            "id": r["id"], "firma": r["firma"], "branche": r["branche"], "ort": r["ort"],
            "plz": r["plz"], "telefon": r["telefon"], "punkte": round(punkte, 1),
            "entfernung_km": round(km, 1) if km is not None else None,
        })
    liste.sort(key=lambda x: (-x["punkte"], x["entfernung_km"] if x["entfernung_km"] is not None else 999))
    return liste[:anzahl], len(liste)


def register_leon_leads_holen(app, login_required, get_db_connection):

    def _conn():
        conn = get_db_connection()
        ensure_tables(conn)  # ls_punkte, plz_geo … (wie Auto-Kampagne)
        ensure_leon_links(conn)
        return conn

    @app.route("/api/leon/leads-holen/branchen")
    @login_required
    def leon_leads_holen_branchen():
        conn = _conn()
        try:
            return jsonify({"success": True, "branchen": _branchen_liste(conn)})
        finally:
            conn.close()

    @app.route("/api/leon/leads-holen/staedte")
    @login_required
    def leon_leads_holen_staedte():
        branchen = _branchen_param()
        if not branchen:
            return jsonify({"success": False, "error": "Bitte eine Branche wählen."}), 400
        conn = _conn()
        try:
            return jsonify({"success": True, "staedte": _staedte(conn, branchen)})
        finally:
            conn.close()

    @app.route("/api/leon/leads-holen/vorschau")
    @login_required
    def leon_leads_holen_vorschau():
        branchen = _branchen_param()
        if not branchen:
            return jsonify({"success": False, "error": "Bitte eine Branche wählen."}), 400
        stadt = (request.args.get("stadt") or "").strip()[:120]
        try:
            anzahl = max(1, min(500, int(request.args.get("anzahl") or 10)))
        except ValueError:
            anzahl = 10
        conn = _conn()
        try:
            auswahl, moeglich = _vorschlag(conn, branchen, stadt, anzahl)
        finally:
            conn.close()
        return jsonify({"success": True, "auswahl": auswahl, "moeglich": moeglich})

    # ---------------- ChatGPT (KG Daten): nur mit Token, legt höchstens einen Entwurf an ----------------
    @app.route("/internal/mcp/leon-kampagne", methods=["POST"])
    def mcp_leon_kampagne():
        token = (os.getenv("KG_MCP_TOKEN") or "").strip()
        given = (request.headers.get("X-KG-MCP-Token") or "").strip()
        if not token or not hmac.compare_digest(given.encode(), token.encode()):
            return jsonify({"success": False, "error": "Zugriff verweigert"}), 403

        d = request.get_json(silent=True) or {}
        aktion = str(d.get("aktion") or "").strip()
        branchen = [str(b).strip()[:120] for b in (d.get("branchen") or []) if str(b).strip()][:10]
        stadt = str(d.get("stadt") or "gemischt").strip()[:120]
        try:
            anzahl = max(1, min(MCP_MAX_ANZAHL, int(d.get("anzahl") or 10)))
        except (TypeError, ValueError):
            anzahl = 10

        conn = _conn()
        try:
            if aktion == "branchen":
                return jsonify({"success": True, "branchen": _branchen_liste(conn)})
            if aktion not in ("staedte", "vorschau", "anlegen"):
                return jsonify({"success": False, "error": "aktion: branchen, staedte, vorschau oder anlegen"}), 400
            if not branchen:
                return jsonify({"success": False, "error": "Bitte mindestens eine Branche angeben (aktion=branchen zeigt alle)."}), 400
            if aktion == "staedte":
                return jsonify({"success": True, "staedte": _staedte(conn, branchen)})
            auswahl, moeglich = _vorschlag(conn, branchen, stadt, anzahl)
        finally:
            conn.close()

        if aktion == "vorschau":
            return jsonify({"success": True, "auswahl": auswahl, "moeglich": moeglich})

        name = str(d.get("name") or "").strip()[:120]
        if not name:
            return jsonify({"success": False, "error": "Bitte einen Kampagnen-Namen angeben."}), 400
        if not auswahl:
            return jsonify({"success": False, "error": "Keine passenden Firmen gefunden – nichts angelegt."}), 404

        # gleiche Übergabe wie „Aus Datenbank“ / Auto-Kampagne (legt Kampagne als Entwurf an, startet nicht)
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        resp = client.post("/api/leon/datenbank/uebergeben",
                           json={"quelle": "leads", "ids": [f["id"] for f in auswahl], "neue_kampagne": name})
        result = resp.get_json(silent=True) or {}
        if not result.get("success"):
            return jsonify({"success": False, "error": result.get("error") or f"Übergabe fehlgeschlagen (HTTP {resp.status_code})."}), 502
        return jsonify({
            "success": True,
            "kampagne_id": result.get("kampagne_id"),
            "name": name,
            "in_kampagne": result.get("in_kampagne", 0),
            "hinweise": result.get("hinweise") or [],
            "firmen": [{"firma": f["firma"], "ort": f["ort"], "punkte": f["punkte"]} for f in auswahl],
            "gestartet": False,
            "info": "Kampagne ist als Entwurf angelegt. Starten nur im CRM unter Leon → Kampagnen.",
        })
