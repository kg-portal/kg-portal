# Datenbank-Seite: alle Besichtigungen (gesamt / dieser Monat) für Karte und Panel (nur lesend).
# Eine Besichtigung = eine Tagesliste-Zeile, die je Besichtigung war: Status-Verlauf, Status-Backup,
# heute im Status „besichtigung“ (KG Scan / Direktes Angebot) oder schon mit Angebot-Nummer.
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import jsonify

from besichtigung_entfernen import AUSGEBLENDET_SQL

BERLIN = ZoneInfo("Europe/Berlin")


def _tag(text):
    return str(text or "")[:10]


def register_besichtigung_uebersicht(app, login_required, get_db_connection):

    @app.route("/api/datenbank/besichtigung-uebersicht")
    @login_required
    def besichtigung_uebersicht_daten():
        conn = get_db_connection()
        try:
            # Datum je Zeile: Verlauf vor Backup vor Anlage-Datum
            datum = {}
            for tid, d in conn.execute(
                "SELECT tagesliste_id, MIN(datum) FROM tagesliste_status_history "
                "WHERE status = 'besichtigung' GROUP BY tagesliste_id"
            ).fetchall():
                datum[tid] = _tag(d)
            backup = {}
            for r in conn.execute(
                "SELECT * FROM tagesliste_status_backup WHERE status = 'besichtigung'"
            ).fetchall():
                backup[r["tagesliste_id"]] = r
                datum.setdefault(r["tagesliste_id"], _tag(r["backup_am"]))
            live = {}
            for r in conn.execute(
                "SELECT * FROM tagesliste_leads "
                "WHERE status = 'besichtigung' OR COALESCE(angebot_nr, '') != '' OR id IN ("
                "  SELECT tagesliste_id FROM tagesliste_status_history WHERE status = 'besichtigung'"
                "  UNION SELECT tagesliste_id FROM tagesliste_status_backup WHERE status = 'besichtigung')"
            ).fetchall():
                live[r["id"]] = r
                datum.setdefault(r["id"], _tag(r["erstellt_am"]))
            # auf der Besichtigung-Seite „Entfernt“ (nur ausgeblendet, siehe besichtigung_entfernen.py)
            try:
                entfernt = {r[0] for r in conn.execute(AUSGEBLENDET_SQL).fetchall()}
            except Exception:
                entfernt = set()
        except Exception as exc:
            conn.close()
            return jsonify({"ok": False, "message": str(exc)})
        conn.close()

        eintraege = []
        for tid, tag in datum.items():
            r = live.get(tid) or backup.get(tid)
            if r is None:
                continue
            if tid in live:
                status = (live[tid]["status"] or "").lower()
                nr = live[tid]["angebot_nr"] or ""
            else:
                status, nr = "", ""
            if tid in entfernt and not nr:
                continue
            if status == "besichtigung":
                stand = "offen"
            elif nr:
                stand = "Angebot AN-" + str(nr)
            else:
                stand = "ohne Angebot"
            eintraege.append({
                "id": tid,
                "firma": r["firma"] or "",
                "ort": " ".join(x for x in ((r["plz"] or ""), (r["ort"] or "")) if x),
                "quelle": r["quelle"] or "",
                "datum": tag,
                "stand": stand,
            })
        eintraege.sort(key=lambda e: (e["datum"], e["id"]), reverse=True)

        monat = datetime.now(BERLIN).strftime("%Y-%m")
        return jsonify({
            "ok": True,
            "gesamt": len(eintraege),
            "monat": sum(1 for e in eintraege if e["datum"].startswith(monat)),
            "offen": sum(1 for e in eintraege if e["stand"] == "offen"),
            "eintraege": eintraege,
        })
