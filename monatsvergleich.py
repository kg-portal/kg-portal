# Datenbank-Seite: Grafik „Monat gegen Vormonat“ unter den Kästen (nur lesend, ändert nichts).
# Tageswerte ab dem gleichen Monat im Vorjahr: neue Leads, Leon-Anrufe (erreicht), Interessiert.
# Besichtigungen rechnet die Seite selbst aus /api/datenbank/besichtigung-uebersicht (gleiche Zahl wie die Karte).
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import jsonify

BERLIN = ZoneInfo("Europe/Berlin")
LEON = "tagesliste_id IN (SELECT tagesliste_id FROM leon_links WHERE tagesliste_id IS NOT NULL)"


def register_monatsvergleich(app, login_required, get_db_connection):

    @app.route("/api/datenbank/monatsvergleich")
    @login_required
    def monatsvergleich_daten():
        heute = datetime.now(BERLIN).date()
        ab = heute.replace(year=heute.year - 1, day=1).isoformat()
        conn = get_db_connection()

        def je_tag(sql):
            try:
                return {str(r[0])[:10]: int(r[1] or 0) for r in conn.execute(sql, (ab,)).fetchall() if r[0]}
            except Exception as exc:
                print("Monatsvergleich: nicht geladen:", exc)
                return {}

        try:
            daten = {
                "leads": je_tag("SELECT date(created_at), COUNT(*) FROM leads WHERE created_at >= ? GROUP BY date(created_at)"),
                "angerufen": je_tag(f"SELECT datum, COUNT(*) FROM tagesliste_status_history WHERE status = 'angerufen' AND datum >= ? AND {LEON} GROUP BY datum"),
                "interessiert": je_tag("SELECT datum, COUNT(*) FROM tagesliste_status_history WHERE status = 'interessiert' AND datum >= ? GROUP BY datum"),
            }
        finally:
            conn.close()

        return jsonify({"ok": True, "heute": heute.isoformat(), "ab": ab, **daten})
