# Datenbank-Seite: Grafik „Kundenweg-Fluss“ (nur lesend, ändert nichts).
# Je Firma (Tagesliste-Zeile) der Weg im Zeitraum laut Status-Verlauf:
# angerufen → Ergebnis (Interessiert / Kontaktformular / Später / Verloren / ohne Ergebnis) → Besichtigung → Angebot.
# Interessiert zählt auch, wer direkt zur Besichtigung ging; Besichtigung ohne Anruf (Direkt / Anfrage) kommt als eigene Quelle.
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import jsonify, request

BERLIN = ZoneInfo("Europe/Berlin")
ANRUF = ("angerufen", "interessiert", "kontaktformular", "spaeter", "verloren")
ENDE = ("kontaktformular", "spaeter", "verloren")


def register_kundenweg_fluss(app, login_required, get_db_connection):

    @app.route("/api/datenbank/kundenweg-fluss")
    @login_required
    def kundenweg_fluss_daten():
        zeitraum = request.args.get("zeitraum", "monat")
        heute = datetime.now(BERLIN).date()
        if zeitraum in ("7", "30", "90"):
            von = heute - timedelta(days=int(zeitraum) - 1)
        else:
            zeitraum = "monat"
            von = heute.replace(day=1)

        conn = get_db_connection()
        try:
            verlauf = {}
            for tid, status in conn.execute(
                "SELECT tagesliste_id, status FROM tagesliste_status_history "
                "WHERE datum >= ? AND status IN ('angerufen', 'interessiert', 'besichtigung', 'kontaktformular', 'spaeter', 'verloren') "
                "ORDER BY datum, erstellt_am, id",
                (von.isoformat(),),
            ).fetchall():
                verlauf.setdefault(tid, []).append(status)

            mit_angebot = {r[0] for r in conn.execute(
                "SELECT id FROM tagesliste_leads WHERE COALESCE(angebot_nr, '') != '' "
                "UNION SELECT tagesliste_id FROM tagesliste_status_history WHERE status = 'angebot' AND datum >= ?",
                (von.isoformat(),),
            ).fetchall()}
        except Exception as exc:
            conn.close()
            return jsonify({"ok": False, "message": str(exc)})
        conn.close()

        ergebnis = {"interessiert": 0, "kontaktformular": 0, "spaeter": 0, "verloren": 0, "ohne": 0}
        angerufen = 0
        besichtigung_aus_interessiert = 0
        besichtigung_direkt = 0
        besichtigung_ids = []
        for tid, staende in verlauf.items():
            if any(s in ANRUF for s in staende):
                angerufen += 1
                if "besichtigung" in staende or "interessiert" in staende:
                    ergebnis["interessiert"] += 1
                    if "besichtigung" in staende:
                        besichtigung_aus_interessiert += 1
                        besichtigung_ids.append(tid)
                else:
                    letzte = [s for s in staende if s in ENDE]
                    ergebnis[letzte[-1] if letzte else "ohne"] += 1
            elif "besichtigung" in staende:
                besichtigung_direkt += 1
                besichtigung_ids.append(tid)

        return jsonify({
            "ok": True,
            "zeitraum": zeitraum,
            "von": von.isoformat(),
            "angerufen": angerufen,
            "ergebnis": ergebnis,
            "besichtigung": {
                "aus_interessiert": besichtigung_aus_interessiert,
                "direkt": besichtigung_direkt,
                "gesamt": besichtigung_aus_interessiert + besichtigung_direkt,
            },
            "angebot": sum(1 for tid in besichtigung_ids if tid in mit_angebot),
            "kunde": 0,
        })
