# =====================================================
# APP2.PY
# DATENBANK / BESICHTIGUNG / ANGEBOTVORLAGE / LEISTUNGSVERZEICHNIS
# =====================================================

from flask import render_template, request


def register_app2_routes(app, login_required):

# =====================================================
# APP2 - BÖLÜM 1 - DATENBANK
# =====================================================

    @app.route("/datenbank")
    @app.route("/datenbank.html")
    @login_required
    def app2_datenbank():
        return render_template("datenbank.html")


# =====================================================
# APP2 - BÖLÜM 2 - BESICHTIGUNG
# =====================================================

    @app.route("/besichtigung")
    @app.route("/besichtigung.html")
    @login_required
    def app2_besichtigung():
        return render_template(
            "besichtigung.html",
            Kunde=request.args.get("Kunde", ""),
            Adresse=request.args.get("Adresse", ""),
            Plz=request.args.get("Plz", ""),
            Ort=request.args.get("Ort", ""),
            Leistungsart=request.args.get("Leistungsart", ""),
            Ansprechpartner=request.args.get("Ansprechpartner", ""),
            Telefon=request.args.get("Telefon", ""),
            Email=request.args.get("Email", "")
        )


# =====================================================
# APP2 - BÖLÜM 3 - ANGEBOTVORLAGE
# =====================================================

    @app.route("/angebotvorlage")
    @app.route("/angebotvorlage.html")
    @login_required
    def app2_angebotvorlage():
        return render_template(
            "angebotvorlage.html",
            Kunde=request.args.get("Kunde", ""),
            Objekt=request.args.get("Objekt", ""),
            Adresse=request.args.get("Adresse", ""),
            Plz=request.args.get("Plz", ""),
            Ort=request.args.get("Ort", ""),
            Leistungsart=request.args.get("Leistungsart", ""),
            Nr=request.args.get("Nr", ""),
            Datum=request.args.get("Datum", "")
        )


# =====================================================
# APP2 - BÖLÜM 4 - LEISTUNGSVERZEICHNIS
# =====================================================

    @app.route("/leistungsverzeichnis")
    @app.route("/Leistungsverzeichnis.html")
    @app.route("/leistungsverzeichnis.html")
    @login_required
    def app2_leistungsverzeichnis():
        return render_template(
            "Leistungsverzeichnis.html",
            Kunde=request.args.get("Kunde", ""),
            Objekt=request.args.get("Objekt", ""),
            Adresse=request.args.get("Adresse", ""),
            Plz=request.args.get("Plz", ""),
            Ort=request.args.get("Ort", ""),
            Leistungsart=request.args.get("Leistungsart", ""),
            Datum=request.args.get("Datum", "")
        )