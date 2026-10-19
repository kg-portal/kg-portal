# Kündigung für Mitarbeiter (roter Knopf „Kündigung“ auf /mitarbeiter).
# Vorschlag aus den Mitarbeiter-Daten: Anschrift, Vertrag vom = Eintrittsdatum, Kündigung zum = Ende des laufenden Monats.
#   /mitarbeiter/kuendigung/<id>            Bearbeiten-Seite (Felder links, Live-Vorschau rechts, PDF-Knopf)
#   /mitarbeiter/kuendigung/<id>/vorschau   die Vorlage templates/kuendigung_vorlage.html (eine A4-Seite)
#   /mitarbeiter/kuendigung/<id>/pdf        dieselbe Seite als PDF (Chromium wie beim Angebot-PDF)
# Nur lesen: Die Tabelle mitarbeiter und alle anderen Daten werden NICHT verändert, es wird nichts gespeichert, keine Mail.
import calendar
import io
import re
from datetime import date, datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from flask import jsonify, render_template, request, send_file, url_for

BERLIN = ZoneInfo("Europe/Berlin")
FELDER = ("anrede", "vorname", "nachname", "strasse", "plz", "ort", "datum", "beginn", "ende", "paragraph", "vertrag")
SCHALTER = ("empfang", "hinweis_afa")
VERTRAGSARTEN = ("Aushilfsarbeitsvertrag", "Arbeitsvertrag")


def _txt(v):
    s = str(v if v is not None else "").strip()
    return "" if s.lower() == "none" else s


def _datum_de(v):
    """„2025-11-15“ / „15.11.2025“ / „15.11.25“ → „15.11.2025“; Unbekanntes bleibt wie es ist."""
    s = _txt(v)
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%d.%m.%Y")
        except ValueError:
            pass
    return s


def heute():
    return datetime.now(BERLIN).date()


def monatsende(tag=None):
    t = tag or heute()
    return date(t.year, t.month, calendar.monthrange(t.year, t.month)[1])


def vorschlag(row, tag=None):
    """Startwerte für eine Kündigung aus der Zeile der Tabelle mitarbeiter."""
    d = dict(row)
    t = tag or heute()
    return {
        "anrede": _txt(d.get("anrede")),
        "vorname": _txt(d.get("vorname")),
        "nachname": _txt(d.get("nachname")),
        "strasse": _txt(d.get("strasse")),
        "plz": _txt(d.get("plz")),
        "ort": _txt(d.get("ort")),
        "datum": t.strftime("%d.%m.%Y"),
        "beginn": _datum_de(d.get("eintrittsdatum")),
        "ende": monatsende(t).strftime("%d.%m.%Y"),
        "paragraph": "1.6",
        "vertrag": VERTRAGSARTEN[0],
        "empfang": False,
        "hinweis_afa": False,
    }


def felder_aus_url(basis, args):
    """Werte aus der Adresse über den Vorschlag legen (nur bekannte Felder)."""
    f = dict(basis)
    for k in FELDER:
        if k in args:
            f[k] = _txt(args.get(k))
    for k in SCHALTER:
        if k in args:
            f[k] = args.get(k) in ("1", "on", "true", "ja")
    if f.get("vertrag") not in VERTRAGSARTEN:
        f["vertrag"] = VERTRAGSARTEN[0]
    return f


def url_werte(f):
    werte = [(k, f.get(k, "")) for k in FELDER]
    werte += [(k, "1" if f.get(k) else "0") for k in SCHALTER]
    return urlencode(werte)


def dateiname(f):
    name = re.sub(r"[^A-Za-z0-9ÄÖÜäöüß \-]+", "", f"{f.get('vorname', '')} {f.get('nachname', '')}")
    name = re.sub(r"\s+", " ", name).strip()[:60]
    return f"Kündigung {name}.pdf" if name else "Kündigung.pdf"


def register_kuendigung(app, login_required, get_db_connection):

    def mitarbeiter(mid):
        conn = get_db_connection()
        try:
            return conn.execute("SELECT * FROM mitarbeiter WHERE id = ?", (mid,)).fetchone()
        finally:
            conn.close()

    @app.route("/mitarbeiter/kuendigung/<int:mid>")
    @login_required
    def kuendigung_seite(mid):
        row = mitarbeiter(mid)
        if not row:
            return "Mitarbeiter nicht gefunden.", 404
        f = felder_aus_url(vorschlag(row), request.args)
        return render_template("kuendigung_editor.html", mid=mid, f=f, vertragsarten=VERTRAGSARTEN,
                               eintritt=_datum_de(row["eintrittsdatum"]), status=_txt(row["status"]))

    @app.route("/mitarbeiter/kuendigung/<int:mid>/vorschau")
    @login_required
    def kuendigung_vorschau(mid):
        row = mitarbeiter(mid)
        if not row:
            return "Mitarbeiter nicht gefunden.", 404
        f = felder_aus_url(vorschlag(row), request.args)
        return render_template("kuendigung_vorlage.html", logo=url_for("static", filename="KG Yeni Logo.png"), **f)

    @app.route("/mitarbeiter/kuendigung/<int:mid>/pdf")
    @login_required
    def kuendigung_pdf(mid):
        row = mitarbeiter(mid)
        if not row:
            return "Mitarbeiter nicht gefunden.", 404
        f = felder_aus_url(vorschlag(row), request.args)
        basis = request.host_url.rstrip("/")
        url = f"{basis}/mitarbeiter/kuendigung/{mid}/vorschau?" + url_werte(f)
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
