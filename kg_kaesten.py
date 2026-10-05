# =====================================================
# DIE 12 BRANCHEN-KÄSTEN (gleich in KG CRM und KG Business)
#
# - "ids": welche branche_id in der Tabelle leads zu diesem Kasten gehören.
#   Alte Einträge bleiben, wie sie sind: alte 10 (Finanzen, Versicherung &
#   Beratung) erscheint im Kasten 1, alte 11 (Handwerk, Technik & Service)
#   im Kasten 11, alte 12 (Sonstige Gewerbe) im Kasten 12.
# - "id": unter dieser branche_id werden neue Firmen gespeichert.
#   Gastronomie & Hotel ist neu und bekommt die 13 (die 10 gehört den alten
#   Finanz-Einträgen).
# - "alt": frühere Adressen (slug) dieses Kastens – alte Links gehen weiter.
# =====================================================

KAESTEN = [
    {"nr": 1, "slug": "buero-kanzlei-beratung", "id": "1", "ids": ["1", "10"],
     "alt": ["buero-verwaltung", "finanzen-versicherung-beratung", "it-medien-kommunikation"],
     "title": "Büro, Kanzlei & Beratung",
     "text": "Bürogebäude, Verwaltung, Rechtsanwalt, Steuerberater, Wirtschaftsprüfer, Notar, Unternehmensberatung, "
             "Versicherung, Versicherungsmakler, Bank, Finanzberatung, Architekt, Ingenieurbüro, IT-Dienstleister, "
             "Agentur, Callcenter, Coworking Space",
     "count": "Büro- und Beratungsflächen"},
    {"nr": 2, "slug": "medizin-gesundheit", "id": "2", "ids": ["2"], "alt": [],
     "title": "Medizin & Gesundheit",
     "text": "Arztpraxis, Zahnarztpraxis, Facharztpraxis, Gemeinschaftspraxis, Medizinisches Versorgungszentrum, "
             "Physiotherapie, Ergotherapie, Logopädie, Psychotherapie, Radiologie, Labor, Dialysezentrum, Apotheke, "
             "Sanitätshaus, Heilpraktiker, Tierarzt",
     "count": "Praxis- und Gesundheitsflächen"},
    {"nr": 3, "slug": "pflege-soziales", "id": "3", "ids": ["3"], "alt": [],
     "title": "Pflege & Soziales",
     "text": "Pflegedienst, Tagespflege, Seniorenbetreuung, Pflegeheim, Seniorenheim, Betreutes Wohnen, Sozialstation, "
             "Wohngruppe, Behindertenhilfe, Jugendhilfe, Familienhilfe, Beratungsstelle, Hilfsorganisation, Caritas, "
             "Diakonie, AWO",
     "count": "Pflege- und Sozialflächen"},
    {"nr": 4, "slug": "bildung-betreuung", "id": "4", "ids": ["4"], "alt": [],
     "title": "Bildung & Betreuung",
     "text": "Kindergarten, Kita, Kindertagespflege, Schule, Privatschule, Nachhilfeinstitut, Sprachschule, Musikschule, "
             "Fahrschule, Berufsschule, Weiterbildungsträger, Schulungszentrum, Akademie, Hochschule",
     "count": "Bildungs- und Betreuungsflächen"},
    {"nr": 5, "slug": "einzelhandel-lebensmittel", "id": "5", "ids": ["5"], "alt": ["einzelhandel-verkaufsflaechen"],
     "title": "Einzelhandel & Lebensmittel",
     "text": "Geschäfte, Drogerie, Mode, Schuhe, Möbel, Elektronik, Optiker, Hörgeräteakustiker, Blumenladen, "
             "Buchhandlung, Juwelier, Handyshop, Bäckerei, Metzgerei, Supermarkt, Getränkemarkt, Kiosk, Showroom",
     "count": "Verkaufs- und Ladenflächen"},
    {"nr": 6, "slug": "fitness-sport-freizeit", "id": "6", "ids": ["6"], "alt": [],
     "title": "Fitness, Sport & Freizeit",
     "text": "Fitnessstudio, EMS Studio, Yoga Studio, Pilates Studio, Tanzschule, Kampfsportschule, Sportverein, "
             "Tennisclub, Sporthalle, Schwimmschule, Freizeitcenter, Bowlingcenter, Indoor-Spielplatz, Kletterhalle",
     "count": "Sport- und Freizeitflächen"},
    {"nr": 7, "slug": "industrie-produktion", "id": "7", "ids": ["7"], "alt": [],
     "title": "Industrie & Produktion",
     "text": "Produktionshalle, Maschinenbau, Metallverarbeitung, Kunststoffverarbeitung, Elektrotechnik Produktion, "
             "Verpackungsproduktion, Druckerei, Textilproduktion, Möbelproduktion, Lebensmittelherstellung, "
             "Fertigungsbetrieb, Werkhalle, Industrieunternehmen",
     "count": "Produktions- und Industrieflächen"},
    {"nr": 8, "slug": "lager-logistik-grosshandel", "id": "8", "ids": ["8"], "alt": [],
     "title": "Lager, Logistik & Großhandel",
     "text": "Lagerhalle, Versandlager, Logistikzentrum, Spedition, Paketdienst Standort, Kühlhaus, Großhandel, "
             "Baustoffhandel, Elektrogroßhandel, Sanitärgroßhandel, Werkzeughandel, Import / Export, Fulfillment",
     "count": "Lager- und Logistikflächen"},
    {"nr": 9, "slug": "immobilien-hausverwaltung", "id": "9", "ids": ["9"], "alt": [],
     "title": "Immobilien & Hausverwaltung",
     "text": "Hausverwaltung, WEG-Verwaltung, Mietverwaltung, Immobilienverwaltung, Wohnungsbaugesellschaft, Bauträger, "
             "Projektentwickler, Immobilienmakler, Gewerbeobjekt Verwaltung, Ärztehaus Verwaltung, Wohnanlage, "
             "Objektverwaltung",
     "count": "Objekt- und Verwaltungsflächen"},
    {"nr": 10, "slug": "gastronomie-hotel", "id": "13", "ids": ["13"], "alt": [],
     "title": "Gastronomie & Hotel",
     "text": "Restaurant, Café, Bistro, Imbiss, Bar, Kneipe, Hotel, Pension, Ferienwohnung, Catering, Eventlocation, "
             "Kantine, Eisdiele",
     "count": "Gastronomie- und Hotelflächen"},
    {"nr": 11, "slug": "handwerk-bau-kfz", "id": "11", "ids": ["11"], "alt": ["handwerk-technik-service"],
     "title": "Handwerk, Bau & Kfz",
     "text": "Elektriker, Heizung Sanitär, Maler, Dachdecker, Tischlerei, Schreinerei, Bauunternehmen, Fliesenleger, "
             "Garten- und Landschaftsbau, Schlüsseldienst, Gebäudetechnik, Kfz-Werkstatt, Autohaus, Reifenservice",
     "count": "Werkstatt-, Bau- und Kfz-Betriebe"},
    {"nr": 12, "slug": "friseur-kosmetik-sonstige", "id": "12", "ids": ["12"],
     "alt": ["sonstige-gewerbe-dienstleister", "sonstige"],
     "title": "Friseur, Kosmetik & Sonstige",
     "text": "Friseur, Kosmetikstudio, Nagelstudio, Massage, Tattoo, Textilreinigung, Sicherheitsdienst, Fotostudio, "
             "Verein, Verband, sonstige Dienstleister, nicht eindeutig zugeordnete Firmen",
     "count": "Dienstleister und Restgruppe"},
]

# branche_id → Name, unter dem neue Firmen gespeichert werden (Lead-Sammler-Eingang)
KASTEN_NAME = {k["id"]: k["title"] for k in KAESTEN}


def kasten_fuer_slug(slug):
    slug = (slug or "").strip()
    for k in KAESTEN:
        if slug == k["slug"] or slug in k["alt"]:
            return k
    return None
