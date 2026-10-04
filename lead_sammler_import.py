# =====================================================
# LEAD-SAMMLER → KG CRM
# Der Lead-Sammler (läuft auf dem PC) schickt geprüfte Firmen aus dem
# Umkreis hierher. Sie landen in der Datenbank (Tabelle leads) unter der
# passenden Branche, Quelle „Lead-Sammler“, Status „Neu“.
#
# - Nur nach Anmeldung (gleiches Login wie das CRM).
# - Nie doppelt: gleiche Pool-ID, gleiche Telefonnummer (nur Ziffern)
#   oder gleiche Firma + Webseite → wird übersprungen.
# - Bestehende Leads werden nie geändert.
# =====================================================
import re

from flask import jsonify, request

MAX_FIRMEN = 1000
CRM_BRANCHEN = {
    "1": "Büro & Verwaltung", "2": "Medizin & Gesundheit", "3": "Pflege & Soziales", "4": "Bildung & Betreuung",
    "5": "Einzelhandel & Verkaufsflächen", "6": "Fitness, Sport & Freizeit", "7": "Industrie & Produktion",
    "8": "Lager, Logistik & Großhandel", "9": "Immobilien & Hausverwaltung", "10": "Finanzen, Versicherung & Beratung",
    "11": "Handwerk, Technik & Service", "12": "Sonstige Gewerbe & Dienstleister",
}
SPALTEN = {
    "branche_id": "TEXT", "branche_name": "TEXT", "suchwort": "TEXT", "ansprechpartner": "TEXT",
    "sort_order": "INTEGER DEFAULT 0", "unique_key": "TEXT", "erstellt_am": "TEXT",
    "email": "TEXT", "quelle": "TEXT", "status": "TEXT",
}


def _text(wert, laenge=200):
    return re.sub(r"\s+", " ", str(wert or "")).strip()[:laenge]


def _ziffern(telefon):
    t = re.sub(r"\D", "", telefon or "")
    if t.startswith("0049"):
        t = "0" + t[4:].lstrip("0")
    elif t.startswith("49") and len(t) > 10:
        t = "0" + t[2:].lstrip("0")
    return t


def register_lead_sammler_import(app, login_required, get_db_connection):

    @app.route("/api/lead-sammler/import", methods=["GET", "POST"])
    @login_required
    def lead_sammler_import():
        if request.method == "GET":  # Verbindungstest des Lead-Sammlers
            return jsonify({"success": True, "bereit": True})

        firmen = (request.get_json(silent=True) or {}).get("firmen")
        if not isinstance(firmen, list):
            return jsonify({"success": False, "error": "firmen muss eine Liste sein."}), 400
        if len(firmen) > MAX_FIRMEN:
            return jsonify({"success": False, "error": f"Höchstens {MAX_FIRMEN} Firmen pro Sendung."}), 400

        conn = get_db_connection()
        try:
            vorhanden = {r[1] for r in conn.execute("PRAGMA table_info(leads)")}
            for name, typ in SPALTEN.items():
                if name not in vorhanden:
                    try:
                        conn.execute(f"ALTER TABLE leads ADD COLUMN {name} {typ}")
                    except Exception:
                        pass
            telefone = {_ziffern(r[0]) for r in conn.execute("SELECT telefon FROM leads WHERE COALESCE(telefon, '') <> ''")}
            schluessel = {r[0] for r in conn.execute("SELECT unique_key FROM leads WHERE COALESCE(unique_key, '') <> ''")}

            importiert = doppelt = uebersprungen = 0
            for f in firmen:
                if not isinstance(f, dict):
                    uebersprungen += 1
                    continue
                firma = _text(f.get("firma"))
                telefon = _text(f.get("telefon"), 60)
                bid = str(f.get("branche_id") or "").strip()
                if not firma or not _ziffern(telefon) or bid not in CRM_BRANCHEN:
                    uebersprungen += 1
                    continue
                key = f"lead-sammler:{_text(f.get('pool_id'), 40)}" if f.get("pool_id") else ""
                website = _text(f.get("website"), 300)
                if (key and key in schluessel) or _ziffern(telefon) in telefone:
                    doppelt += 1
                    continue
                if website and conn.execute(
                        "SELECT 1 FROM leads WHERE lower(COALESCE(firma, '')) = lower(?) AND COALESCE(website, '') = ? LIMIT 1",
                        (firma, website)).fetchone():
                    doppelt += 1
                    continue
                sort = conn.execute("SELECT COALESCE(MAX(sort_order), 0) FROM leads WHERE branche_id = ?", (bid,)).fetchone()[0]
                conn.execute("""
                    INSERT INTO leads (unique_key, branche_id, branche_name, suchwort, firma, strasse, plz, stadt,
                                       telefon, email, website, ansprechpartner, quelle, status, sort_order, erstellt_am)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Lead-Sammler', 'Neu', ?, CURRENT_TIMESTAMP)
                """, (key or None, bid, CRM_BRANCHEN[bid], _text(f.get("suchwort"), 80), firma,
                      _text(f.get("strasse"), 120), _text(f.get("plz"), 5), _text(f.get("stadt"), 80), telefon,
                      _text(f.get("email"), 120), website, _text(f.get("ansprechpartner"), 120), int(sort or 0) + 1))
                telefone.add(_ziffern(telefon))
                if key:
                    schluessel.add(key)
                importiert += 1
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True, "importiert": importiert, "doppelt": doppelt, "uebersprungen": uebersprungen})
