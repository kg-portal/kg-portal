# Schlüsselübergabeprotokoll (oranger Knopf „Schlüsselübergabeprotokoll“ im Vertrag-Editor).
# Kundendaten kommen aus dem Vertrag-Formular (Adresse), Schlüssel/Chips/Codes werden von Hand eingetragen.
#   /schluessel/protokoll            Bearbeiten-Seite (Felder links, Live-Vorschau rechts, PDF-Knopf)
#   /schluessel/protokoll/vorschau   die Vorlage templates/schluessel_vorlage.html (eine A4-Seite)
#   /schluessel/protokoll/pdf        dieselbe Seite als PDF (Chromium wie beim Angebot-PDF)
# Nur lesen: es wird nichts gespeichert und nichts verschickt.
import io
import re
from urllib.parse import urlencode

from flask import jsonify, render_template, request, send_file, url_for

FELDER = ("firma", "adresse", "plz", "ort", "name", "ort_datum")
ZEILEN = 9
SPALTEN = ("anw", "art", "hinweis", "anzahl")


def _txt(v, laenge=200):
    s = str(v if v is not None else "").strip()
    return ("" if s.lower() == "none" else s)[:laenge]


def felder_aus_url(args):
    f = {k: _txt(args.get(k)) for k in FELDER}
    f["zeilen"] = [{s: _txt(args.get(f"{s}{i}"), 120) for s in SPALTEN} for i in range(1, ZEILEN + 1)]
    return f


def url_werte(f):
    werte = [(k, f.get(k, "")) for k in FELDER]
    for i, z in enumerate(f["zeilen"], 1):
        werte += [(f"{s}{i}", z[s]) for s in SPALTEN if z[s]]
    return urlencode(werte)


def dateiname(f):
    name = re.sub(r"[^A-Za-z0-9ÄÖÜäöüß \-]+", "", f.get("firma", ""))
    name = re.sub(r"\s+", " ", name).strip()[:60]
    return f"Schlüsselübergabe {name}.pdf" if name else "Schlüsselübergabe.pdf"


def register_schluessel(app, login_required):

    @app.route("/schluessel/protokoll")
    @login_required
    def schluessel_seite():
        f = felder_aus_url(request.args)
        return render_template("schluessel_editor.html", f=f, spalten=SPALTEN)

    @app.route("/schluessel/protokoll/vorschau")
    @login_required
    def schluessel_vorschau():
        return render_template("schluessel_vorlage.html", logo=url_for("static", filename="kg_logo_farbe.png"), **felder_aus_url(request.args))

    @app.route("/schluessel/protokoll/pdf")
    @login_required
    def schluessel_pdf():
        f = felder_aus_url(request.args)
        basis = request.host_url.rstrip("/")
        url = f"{basis}/schluessel/protokoll/vorschau?" + url_werte(f)
        try:
            from playwright.sync_api import sync_playwright
            keks_name = app.config.get("SESSION_COOKIE_NAME", "session")
            keks = request.cookies.get(keks_name, "")
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
                try:
                    ctx = browser.new_context(viewport={"width": 1240, "height": 1754}, device_scale_factor=1)
                    if keks:
                        ctx.add_cookies([{"name": keks_name, "value": keks, "url": basis}])
                    seite = ctx.new_page()
                    seite.goto(url, wait_until="networkidle", timeout=25000)
                    seite.emulate_media(media="print")
                    pdf = seite.pdf(format="A4", print_background=True, prefer_css_page_size=True, scale=1,
                                    margin={"top": "0mm", "right": "0mm", "bottom": "0mm", "left": "0mm"})
                finally:
                    browser.close()
        except Exception as exc:
            return jsonify({"ok": False, "error": "PDF konnte nicht erstellt werden: " + str(exc)}), 500
        return send_file(io.BytesIO(pdf), mimetype="application/pdf", as_attachment=request.args.get("ansehen") != "1",
                         download_name=dateiname(f))
