# =====================================================
# NUR FÜR RENDER NACH DEM UMZUG
# Start Command in Render:  gunicorn umzug_weiterleitung:app
# Jede Anfrage an die alte Adresse geht mit gleichem Pfad an den neuen Server:
#   GET/HEAD -> 302, alles andere (POST usw.) -> 307 (Methode und Inhalt bleiben).
# Kein CRM, keine Datenbank, keine Automatik läuft hier.
# =====================================================

import os
from urllib.parse import quote

ZIEL = (os.getenv("UMZUG_ZIEL") or "https://portal.kg-reinigung.de").strip().rstrip("/")


def app(environ, start_response):
    ziel_pfad = environ.get("RAW_URI") or ""
    if not ziel_pfad.startswith("/"):
        pfad = environ.get("PATH_INFO") or "/"
        try:
            pfad = pfad.encode("latin-1").decode("utf-8")
        except UnicodeError:
            pass
        ziel_pfad = quote(pfad, safe="/:@!$&'()*+,;=-._~%")
        if environ.get("QUERY_STRING"):
            ziel_pfad += "?" + environ["QUERY_STRING"]
    methode = environ.get("REQUEST_METHOD", "GET").upper()
    status = "302 Found" if methode in ("GET", "HEAD") else "307 Temporary Redirect"
    ort = ZIEL + ziel_pfad
    text = ("Umgezogen: " + ort + "\n").encode("utf-8")
    start_response(status, [
        ("Location", ort),
        ("Cache-Control", "no-store"),
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Content-Length", str(len(text))),
    ])
    return [] if methode == "HEAD" else [text]
