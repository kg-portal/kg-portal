# Datenbank-Seite: Leon-Zahlen oben (nur lesend, ändert nichts).
# Quelle: leon_links + tagesliste_status_history (CRM) und die Kampagnen-Liste vom Leon-Motor.
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import jsonify

BERLIN = ZoneInfo("Europe/Berlin")
FEHLER = ("Fehler", "OpenAI Fehler")

_kampagnen_cache = {"zeit": 0.0, "daten": None}


def _berlin_datum(text):
    # created_at im Motor = SQLite CURRENT_TIMESTAMP (UTC)
    try:
        dt = datetime.strptime(str(text or "")[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(BERLIN).date()


def _kampagnen_heute(leon_client):
    if time.time() - _kampagnen_cache["zeit"] < 60:
        return _kampagnen_cache["daten"]
    daten = None
    try:
        if leon_client.configured():
            resp = leon_client.raw("GET", "/api/campaigns", timeout=10)
            try:
                if resp.status_code == 200:
                    liste = (resp.json() or {}).get("campaigns") or []
                    heute = datetime.now(BERLIN).date()
                    von_heute = [k for k in liste if _berlin_datum(k.get("created_at")) == heute]
                    daten = {
                        "kampagnen": len(von_heute),
                        "firmen": sum(int(k.get("lead_count") or 0) for k in von_heute),
                        "wartend": sum(int(k.get("waiting_count") or 0) for k in von_heute),
                        "heute_angerufen": sum(int(k.get("today_call_count") or 0) for k in liste),
                        "liste": [{
                            "name": k.get("name") or "",
                            "status": k.get("status") or "",
                            "firmen": int(k.get("lead_count") or 0),
                            "wartend": int(k.get("waiting_count") or 0),
                            "heute_angerufen": int(k.get("today_call_count") or 0),
                        } for k in von_heute],
                    }
            finally:
                resp.close()
    except Exception as exc:  # Motor nicht erreichbar → Kästen zeigen „–“
        print("Leon-Übersicht: Kampagnen nicht geladen:", exc)
        daten = None
    _kampagnen_cache["zeit"] = time.time()
    _kampagnen_cache["daten"] = daten
    return daten


def register_leon_uebersicht(app, login_required, get_db_connection, get_leon_client):

    @app.route("/api/datenbank/leon-uebersicht")
    @login_required
    def leon_uebersicht_daten():
        zahlen = {}
        conn = get_db_connection()
        try:
            z = conn.execute(
                """
                SELECT
                    COUNT(*) AS gesendet,
                    SUM(CASE WHEN COALESCE(letzter_status, '') = '' THEN 1 ELSE 0 END) AS offen,
                    SUM(CASE WHEN COALESCE(letzter_status, '') != '' THEN 1 ELSE 0 END) AS angerufen,
                    SUM(CASE WHEN letzter_status = 'Beendet' THEN 1 ELSE 0 END) AS gespraech,
                    SUM(CASE WHEN letzter_status IN (?, ?) THEN 1 ELSE 0 END) AS fehler,
                    SUM(CASE WHEN letztes_ergebnis = 'Interessiert' THEN 1 ELSE 0 END) AS interessiert,
                    SUM(CASE WHEN letztes_ergebnis = 'Besichtigung' THEN 1 ELSE 0 END) AS besichtigung,
                    SUM(CASE WHEN letztes_ergebnis = 'Rückruf' THEN 1 ELSE 0 END) AS rueckruf,
                    SUM(CASE WHEN letztes_ergebnis = 'Kein Interesse' THEN 1 ELSE 0 END) AS kein_interesse
                FROM leon_links
                """,
                FEHLER,
            ).fetchone()
            zahlen = {k: int(z[k] or 0) for k in z.keys()}
            zahlen["nicht_erreicht"] = max(0, zahlen["angerufen"] - zahlen["gespraech"] - zahlen["fehler"])

            # dieser Monat: Firmen, die Leon angerufen hat (aktualisiert_am = Serverzeit, siehe leon_datenbank._now)
            monat_start = datetime.now(BERLIN).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            zahlen["monat_angerufen"] = int(conn.execute(
                "SELECT COUNT(*) FROM leon_links WHERE COALESCE(letzter_status, '') != '' AND aktualisiert_am >= ?",
                (monat_start.astimezone().strftime("%Y-%m-%d %H:%M:%S"),),
            ).fetchone()[0] or 0)
        except Exception as exc:
            conn.close()
            return jsonify({"ok": False, "message": str(exc)})

        # heute: so wie die Status-Zähler der Datenbank (Datum beim Speichern)
        heute = {}
        try:
            heute = {r[0]: r[1] for r in conn.execute(
                """
                SELECT status, COUNT(*) FROM tagesliste_status_history
                WHERE datum = date('now', 'localtime')
                  AND tagesliste_id IN (SELECT tagesliste_id FROM leon_links WHERE tagesliste_id IS NOT NULL)
                GROUP BY status
                """
            ).fetchall()}
        except Exception as exc:
            print("Leon-Übersicht: heute nicht geladen:", exc)
        finally:
            conn.close()
        zahlen["heute_interessiert"] = int(heute.get("interessiert") or 0)
        zahlen["heute_besichtigung"] = int(heute.get("besichtigung") or 0)

        return jsonify({
            "ok": True,
            "zahlen": zahlen,
            "kampagnen_heute": _kampagnen_heute(get_leon_client()),
        })
