# =====================================================
# REITER „LEAD-SAMMLER“ (unter Datenbank)
# Zeigt die Übersicht des Lead-Sammlers, der auf dem Server von KG Business
# läuft – genau dieselbe Seite wie in KG Business unter Datenbank → Lead-Sammler.
# Adresse änderbar über LEAD_SAMMLER_URL (Standard: KG Business).
# =====================================================
import os

from flask import render_template

STANDARD_URL = "https://portal.kg-business.de/lead-sammler/"


def register_lead_sammler_reiter(app, login_required):

    @app.route("/lead-sammler")
    @login_required
    def lead_sammler_reiter():
        url = (os.getenv("LEAD_SAMMLER_URL") or STANDARD_URL).strip()
        return render_template("lead_sammler_reiter.html", lead_sammler_url=url)
