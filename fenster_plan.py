# Fensterreinigung (/fensterreinigung): feste Fenster-Kunden mit eigenem Rhythmus planen.
# Jeder Kunde wird im KG LifeHub als EINE wiederkehrende Aufgabe angelegt („Fensterreinigung – Firma“, fester Plan) –
# sie steht dort in „Aufgaben“ und im Kalender; nach „Erledigt“ legt LifeHub den nächsten Termin selbst an.
# Das CRM speichert nur den Plan (fenster_plan) und je Einsatz Mitarbeiter, Dauer, Notiz, Rechnung (fenster_einsatz).
# Es wird nichts gelöscht: „Pausieren“ stoppt die Serie im LifeHub und archiviert den offenen Termin.
# LifeHub-Zugang wie /aufgaben (lifehub_bruecke.ZUGANG). Ohne LifeHub: Pläne und Termine werden nur im CRM berechnet.
import calendar
import json
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import jsonify, render_template, request

from lifehub_bruecke import ZUGANG, LifeHubFehler

BERLIN = ZoneInfo("Europe/Berlin")
WOCHENTAGE = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
TAG_NAME = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")
TAG_KURZ = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
MONAT_NAME = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember")
ARTEN = ("wochen", "monate", "bestimmte")
TITEL = "Fensterreinigung – "
LH_GRUPPE, LH_FARBE = "Kunden & Angebote", "pfau"


def _tabellen(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fenster_plan (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kunde_id INTEGER, firma TEXT NOT NULL, ansprechpartner TEXT, telefon TEXT,
            strasse TEXT, plz TEXT, ort TEXT,
            art TEXT NOT NULL, intervall INTEGER NOT NULL DEFAULT 1, wochentag INTEGER, monatstag INTEGER,
            nte INTEGER, monate TEXT, start TEXT NOT NULL, uhrzeit TEXT, dauer_min INTEGER, betrag REAL, notiz TEXT,
            rrule TEXT, erster_termin TEXT, lifehub_serie TEXT, lifehub_fehler TEXT,
            aktiv INTEGER NOT NULL DEFAULT 1, erstellt_am TEXT, geaendert_am TEXT, pausiert_am TEXT
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fenster_einsatz (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            plan_id INTEGER NOT NULL, datum TEXT NOT NULL, lifehub_aufgabe TEXT,
            erledigt_am TEXT, mitarbeiter TEXT, dauer_min INTEGER, notiz TEXT,
            rechnung INTEGER NOT NULL DEFAULT 0, rechnung_am TEXT, quelle TEXT
        )""")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS fenster_einsatz_aufgabe ON fenster_einsatz(lifehub_aufgabe)")
    conn.commit()


def _jetzt():
    return datetime.now(BERLIN).strftime("%Y-%m-%d %H:%M:%S")


def _heute():
    return datetime.now(BERLIN).date()


def _txt(v, n=300):
    return " ".join(str(v or "").split())[:n]


def _int(v, lo, hi, leer=None):
    try:
        i = int(str(v).strip())
    except (TypeError, ValueError):
        return leer
    return i if lo <= i <= hi else leer


def _zahl(v):
    t = str(v or "").replace("€", "").replace(" ", "").strip()
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return round(float(t), 2)
    except ValueError:
        return None


def _euro(v):
    return f"{float(v or 0):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def _datum(v):
    try:
        return date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None


# ---------------------------------------------------------------- Rhythmus
# Drei Arten (alles kombinierbar über Intervall, Tag und Monate):
#   wochen    – alle N Wochen am Wochentag                     → FREQ=WEEKLY;INTERVAL=N;BYDAY=TU
#   monate    – alle N Monate am Tag X / am n-ten Wochentag      → FREQ=MONTHLY;INTERVAL=N;BYMONTHDAY=15 | BYDAY=2TU
#   bestimmte – in gewählten Monaten am Tag X / n-ten Wochentag  → FREQ=YEARLY;BYMONTH=3,9;BYMONTHDAY=15 | BYDAY=2TU
# Tag X: 1–28 oder -1 (letzter Tag); n-ter Wochentag: 1–4 oder -1 (letzter).
# Der erste Termin (frühestes passendes Datum ab „Start“) ist auch der Serienbeginn im LifeHub – so rechnen CRM und
# LifeHub (RFC-5545-Regel) dieselben Termine.

def rhythmus(d):
    """Eingabe prüfen → dict mit art, intervall, wochentag, monatstag, nte, monate; sonst Fehlertext."""
    d = d if isinstance(d, dict) else {}
    art = d.get("art")
    if art not in ARTEN:
        return None, "Bitte einen Rhythmus wählen."
    r = {"art": art, "intervall": 1, "wochentag": None, "monatstag": None, "nte": None, "monate": ""}
    if art in ("wochen", "monate"):
        r["intervall"] = _int(d.get("intervall"), 1, 52 if art == "wochen" else 24)
        if not r["intervall"]:
            return None, "Bitte angeben, alle wie viele " + ("Wochen" if art == "wochen" else "Monate") + "."
    if art == "bestimmte":
        monate = sorted({m for m in (_int(x, 1, 12) for x in (d.get("monate") or [])) if m})
        if not monate:
            return None, "Bitte mindestens einen Monat wählen."
        r["monate"] = ",".join(str(m) for m in monate)
    if art == "wochen" or d.get("tagart") == "wochentag":
        r["wochentag"] = _int(d.get("wochentag"), 0, 6)
        if r["wochentag"] is None:
            return None, "Bitte einen Wochentag wählen."
        if art != "wochen":
            r["nte"] = _int(d.get("nte"), -1, 4)
            if not r["nte"]:
                return None, "Bitte wählen: 1., 2., 3., 4. oder letzter Wochentag."
    else:
        r["monatstag"] = _int(d.get("monatstag"), -1, 28)
        if not r["monatstag"]:
            return None, "Bitte den Tag im Monat wählen (1–28 oder letzter Tag)."
    return r, ""


def rrule(r):
    if r["art"] == "wochen":
        return f"FREQ=WEEKLY;INTERVAL={r['intervall']};BYDAY={WOCHENTAGE[r['wochentag']]}"
    tag = f"BYDAY={r['nte']}{WOCHENTAGE[r['wochentag']]}" if r["wochentag"] is not None else f"BYMONTHDAY={r['monatstag']}"
    if r["art"] == "monate":
        return f"FREQ=MONTHLY;INTERVAL={r['intervall']};{tag}"
    return f"FREQ=YEARLY;BYMONTH={r['monate']};{tag}"


def beschreibung(r):
    if r["art"] == "wochen":
        n = r["intervall"]
        return ("Jede Woche" if n == 1 else f"Alle {n} Wochen") + f", {TAG_NAME[r['wochentag']].lower()}s"
    if r["wochentag"] is not None:
        tag = ("am letzten " if r["nte"] == -1 else f"am {r['nte']}. ") + TAG_NAME[r["wochentag"]]
    else:
        tag = "am letzten Tag" if r["monatstag"] == -1 else f"am {r['monatstag']}."
    if r["art"] == "monate":
        n = r["intervall"]
        return ("Jeden Monat" if n == 1 else f"Alle {n} Monate") + " " + tag
    monate = [MONAT_NAME[int(m) - 1] for m in r["monate"].split(",")]
    liste = monate[0] if len(monate) == 1 else ", ".join(monate[:-1]) + " und " + monate[-1]
    return f"Im {liste} {tag}"


def _tag_im_monat(r, jahr, monat):
    letzter = calendar.monthrange(jahr, monat)[1]
    if r["wochentag"] is None:
        return date(jahr, monat, letzter if r["monatstag"] == -1 else r["monatstag"])
    if r["nte"] == -1:
        d = date(jahr, monat, letzter)
        return d - timedelta(days=(d.weekday() - r["wochentag"]) % 7)
    d = date(jahr, monat, 1)
    return d + timedelta(days=(r["wochentag"] - d.weekday()) % 7 + 7 * (r["nte"] - 1))


def _monat_plus(jahr, monat, n):
    m = monat - 1 + n
    return jahr + m // 12, m % 12 + 1


def termine(r, start, bis, ab=None):
    """Alle Termine der Serie (Serienbeginn = erster Termin ab start) mit ab <= Datum <= bis."""
    ab = ab or start
    aus = []
    if r["art"] == "wochen":
        d = start + timedelta(days=(r["wochentag"] - start.weekday()) % 7)
        while d <= bis:
            if d >= ab:
                aus.append(d)
            d += timedelta(weeks=r["intervall"])
        return aus
    monate = {int(m) for m in r["monate"].split(",")} if r["art"] == "bestimmte" else None
    j, m = start.year, start.month
    erster = None
    for _ in range(48):  # erster passender Monat (bei „bestimmte“ höchstens 12 Monate weiter)
        if monate is None or m in monate:
            d = _tag_im_monat(r, j, m)
            if d >= start:
                erster = (j, m)
                break
        j, m = _monat_plus(j, m, 1)
    if not erster:
        return aus
    j, m = erster
    while True:
        d = _tag_im_monat(r, j, m)
        if d > bis:
            return aus
        if d >= ab:
            aus.append(d)
        if monate is None:
            j, m = _monat_plus(j, m, r["intervall"])
        else:
            j, m = _monat_plus(j, m, 1)
            while m not in monate:
                j, m = _monat_plus(j, m, 1)


def _r_aus_zeile(z):
    return {k: z[k] for k in ("art", "intervall", "wochentag", "monatstag", "nte")} | {"monate": z["monate"] or ""}


def einsaetze_im_jahr(r):
    """Durchschnittliche Einsätze pro Jahr (für Umsatz/Monat)."""
    if r["art"] == "wochen":
        return round(365.25 / 7 / r["intervall"], 1)
    if r["art"] == "monate":
        return round(12 / r["intervall"], 1)
    return len(r["monate"].split(","))


# ---------------------------------------------------------------- LifeHub

def _lh_beschreibung(p):
    # Rhythmus als Text dazu: LifeHub beschreibt manche Regeln (z. B. mehrere Monate) nur ungenau, die Termine stimmen
    zeilen = ["Rhythmus: " + beschreibung(_r_aus_zeile(p)) + " (fester Plan)",
              " ".join(x for x in (p["strasse"], " ".join(y for y in (p["plz"], p["ort"]) if y)) if x)]
    if p["ansprechpartner"] or p["telefon"]:
        zeilen.append(" · ".join(x for x in (p["ansprechpartner"], p["telefon"]) if x))
    if p["betrag"]:
        zeilen.append("Betrag je Einsatz: " + _euro(p["betrag"]) + " netto")
    if p["notiz"]:
        zeilen.append("Notiz: " + p["notiz"])
    zeilen.append("Aus KG CRM → Fensterreinigung. Nach dem Einsatz bitte im CRM „Erledigt“ drücken "
                  "(Mitarbeiter, Dauer, Rechnung).")
    return "\n".join(z for z in zeilen if z)


def lh_serie_anlegen(p):
    """Neue wiederkehrende Aufgabe im LifeHub; gibt die Serien-ID zurück."""
    neu = {"title": TITEL + p["firma"], "description": _lh_beschreibung(p), "planned_date": p["erster_termin"],
           "recurrence": {"rule": p["rrule"], "mode": "schedule"},
           "meta": {"gruppe": LH_GRUPPE, "farbe": LH_FARBE, "quelle": "kg-crm-fenster", "fenster_plan": p["id"]},
           # Erinnerung im LifeHub (Glocke) am Vortag 17:00 – gilt für jeden Termin der Serie
           "reminders": [{"anchor": "planned", "offset_days": -1, "at_time": "17:00", "level": "prep",
                          "label": "Morgen Fensterreinigung: " + p["firma"]}]}
    if p["uhrzeit"]:
        neu["planned_time"] = p["uhrzeit"]
    if p["dauer_min"]:
        neu["estimated_minutes"] = p["dauer_min"]
    t = ZUGANG.anfrage("POST", "/tasks", json=neu)
    return t.get("recurrence_id")


def lh_serie_beenden(serie):
    """Serie stoppen und den offenen Termin archivieren (im LifeHub wiederherstellbar, nichts gelöscht)."""
    s = ZUGANG.anfrage("POST", f"/recurrences/{serie}/stop")
    offen = s.get("open_instance_id")
    if offen:
        ZUGANG.anfrage("POST", f"/tasks/{offen}/archive")


def lh_serien():
    return {s["id"]: s for s in ZUGANG.anfrage("GET", "/recurrences")}


def lh_offen(serie_daten):
    """Offener Termin (Aufgabe) der Serie: (id, datum) oder (None, None)."""
    tid = serie_daten.get("open_instance_id")
    if not tid:
        return None, None
    t = ZUGANG.anfrage("GET", f"/tasks/{tid}")
    return tid, _datum(t.get("occurrence_date") or t.get("planned_date"))


# ---------------------------------------------------------------- Routen

def register_fenster_plan(app, login_required, get_db_connection):

    conn = get_db_connection()
    try:
        _tabellen(conn)
    finally:
        conn.close()

    def plan_laden(conn, pid):
        return conn.execute("SELECT * FROM fenster_plan WHERE id = ?", (pid,)).fetchone()

    def plan_aus_eingabe(e):
        """Formular → (felder, rhythmus, fehler)."""
        firma = _txt(e.get("firma"), 200)
        if not firma:
            return None, None, "Bitte die Firma eintragen."
        r, fehler = rhythmus(e.get("rhythmus"))
        if fehler:
            return None, None, fehler
        start = _datum(e.get("start"))
        if not start:
            return None, None, "Bitte das Datum „ab“ wählen."
        uhrzeit = str(e.get("uhrzeit") or "").strip()
        if uhrzeit and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", uhrzeit):
            return None, None, "Uhrzeit bitte als HH:MM."
        f = {"firma": firma, "kunde_id": _int(e.get("kunde_id"), 1, 10 ** 9),
             "ansprechpartner": _txt(e.get("ansprechpartner"), 200), "telefon": _txt(e.get("telefon"), 60),
             "strasse": _txt(e.get("strasse"), 200), "plz": _txt(e.get("plz"), 10), "ort": _txt(e.get("ort"), 100),
             "start": start.isoformat(), "uhrzeit": uhrzeit, "dauer_min": _int(e.get("dauer_min"), 0, 24 * 60, 0),
             "betrag": _zahl(e.get("betrag")), "notiz": _txt(e.get("notiz"), 1000)}
        erste = termine(r, start, start + timedelta(days=800))
        if not erste:
            return None, None, "Dieser Rhythmus ergibt keinen Termin."
        f.update(r)
        f["rrule"], f["erster_termin"] = rrule(r), erste[0].isoformat()
        return f, r, ""

    @app.route("/fensterreinigung")
    @login_required
    def fenster_seite():
        return render_template("fensterreinigung.html")

    @app.route("/api/fenster/vorschau", methods=["POST"])
    @login_required
    def fenster_vorschau():
        """Nächste 6 Termine für das Formular (nur rechnen)."""
        e = request.get_json(silent=True) or {}
        r, fehler = rhythmus(e.get("rhythmus"))
        start = _datum(e.get("start")) or _heute()
        if fehler:
            return jsonify({"ok": False, "fehler": fehler})
        liste = termine(r, start, start + timedelta(days=800))[:6]
        return jsonify({"ok": True, "text": beschreibung(r), "termine": [d.isoformat() for d in liste],
                        "im_jahr": einsaetze_im_jahr(r)})

    @app.route("/api/fenster/stammdaten")
    @login_required
    def fenster_stammdaten():
        """Für das Formular: Kunden (mit Fenster-Angaben aus der Kundenliste) und aktive Mitarbeiter."""
        conn = get_db_connection()
        try:
            kunden = []
            for k in conn.execute("SELECT id, firma, strasse, plz, ort, ansprechpartner_name, telefon, data_json, vertragsstatus "
                                  "FROM kunden ORDER BY firma COLLATE NOCASE").fetchall():
                try:
                    d = json.loads(k["data_json"] or "{}")
                except ValueError:
                    d = {}
                d = d if isinstance(d, dict) else {}
                kunden.append({"id": k["id"], "firma": k["firma"] or "", "strasse": k["strasse"] or "", "plz": k["plz"] or "",
                               "ort": k["ort"] or "", "ansprechpartner": k["ansprechpartner_name"] or "", "telefon": k["telefon"] or "",
                               "fenster_betrag": d.get("fenster_betrag") or "", "fenster_mal": d.get("fenster_mal") or "",
                               "fenster_notiz": d.get("fenster_notiz") or ""})
            ma = [_txt(f"{m['vorname'] or ''} {m['nachname'] or ''}", 120) for m in
                  conn.execute("SELECT vorname, nachname FROM mitarbeiter WHERE status = 'aktiv' ORDER BY sort_order, vorname").fetchall()]
        finally:
            conn.close()
        kunden.sort(key=lambda k: (not k["fenster_mal"], k["firma"].lower()))
        return jsonify({"ok": True, "kunden": kunden, "mitarbeiter": [m for m in ma if m]})

    @app.route("/api/fenster/uebersicht")
    @login_required
    def fenster_uebersicht():
        heute = _heute()
        bis = heute + timedelta(days=55)
        conn = get_db_connection()
        try:
            _tabellen(conn)
            plaene = conn.execute("SELECT * FROM fenster_plan ORDER BY aktiv DESC, firma COLLATE NOCASE").fetchall()
            lh_ok, lh_meldung, serien = True, "", {}
            try:
                serien = lh_serien() if any(p["lifehub_serie"] for p in plaene) else {}
                # Im LifeHub abgehakte Termine ohne CRM-Angaben übernehmen („Details fehlen“)
                ids = {p["lifehub_serie"]: p["id"] for p in plaene if p["lifehub_serie"]}
                if ids:
                    fertig = ZUGANG.anfrage("GET", "/tasks", params={"recurring": "true", "status": "done", "q": "Fensterreinigung",
                                                                      "limit": 500, "sort": "updated"}).get("items") or []
                    for t in fertig:
                        pid = ids.get(t.get("recurrence_id"))
                        if pid and t.get("id"):
                            conn.execute("""INSERT OR IGNORE INTO fenster_einsatz (plan_id, datum, lifehub_aufgabe, erledigt_am, quelle)
                                            VALUES (?, ?, ?, ?, 'lifehub')""",
                                         (pid, t.get("occurrence_date") or t.get("planned_date") or "", t["id"], _jetzt()))
                    conn.commit()
            except LifeHubFehler as exc:
                lh_ok, lh_meldung = False, str(exc)
            einsaetze = conn.execute("SELECT * FROM fenster_einsatz ORDER BY datum DESC, id DESC").fetchall()
        finally:
            conn.close()

        letzte = {}
        for e in einsaetze:
            letzte.setdefault(e["plan_id"], e)
        aus_plaene, kalender, monat_summe = [], [], 0.0
        for p in plaene:
            r = _r_aus_zeile(p)
            start = _datum(p["erster_termin"]) or _datum(p["start"])
            s = serien.get(p["lifehub_serie"]) if p["lifehub_serie"] else None
            offen_id, offen_datum = None, None
            if p["aktiv"] and s:
                try:
                    offen_id, offen_datum = lh_offen(s)
                except LifeHubFehler as exc:
                    lh_ok, lh_meldung = False, str(exc)
            erledigt_bis = max((_datum(e["datum"]) for e in einsaetze if e["plan_id"] == p["id"] and _datum(e["datum"])), default=None)
            naechste = []
            if p["aktiv"]:
                if offen_datum:
                    naechste = [offen_datum] + termine(r, start, bis, ab=offen_datum + timedelta(days=1))
                else:  # ohne LifeHub: ab dem ersten Termin nach dem letzten erledigten
                    ab = (erledigt_bis + timedelta(days=1)) if erledigt_bis else start
                    naechste = termine(r, start, max(bis, ab + timedelta(days=800)), ab=ab)[:1]
                    naechste += [d for d in termine(r, start, bis, ab=ab) if d not in naechste]
                im_jahr = einsaetze_im_jahr(r)
                monat_summe += (p["betrag"] or 0) * im_jahr / 12
            else:
                im_jahr = 0
            e_letzt = letzte.get(p["id"])
            eintrag = {
                "id": p["id"], "firma": p["firma"], "kunde_id": p["kunde_id"], "ansprechpartner": p["ansprechpartner"] or "",
                "telefon": p["telefon"] or "", "strasse": p["strasse"] or "", "plz": p["plz"] or "", "ort": p["ort"] or "",
                "rhythmus": r, "rhythmus_text": beschreibung(r), "start": p["start"], "uhrzeit": p["uhrzeit"] or "",
                "dauer_min": p["dauer_min"] or 0, "betrag": p["betrag"] or 0, "notiz": p["notiz"] or "", "aktiv": bool(p["aktiv"]),
                "pausiert_am": p["pausiert_am"] or "", "im_jahr": im_jahr, "lifehub": bool(p["lifehub_serie"]),
                "lifehub_fehler": p["lifehub_fehler"] or "", "offen_aufgabe": offen_id,
                "naechster": naechste[0].isoformat() if naechste else "",
                "ueberfaellig": bool(naechste and naechste[0] < heute),
                "letzter": {"datum": e_letzt["datum"], "mitarbeiter": e_letzt["mitarbeiter"] or ""} if e_letzt else None,
            }
            aus_plaene.append(eintrag)
            for i, d in enumerate(naechste):
                if d <= bis:
                    kalender.append({"plan_id": p["id"], "datum": d.isoformat(), "firma": p["firma"], "ort": p["ort"] or "",
                                     "uhrzeit": p["uhrzeit"] or "", "dauer_min": p["dauer_min"] or 0, "betrag": p["betrag"] or 0,
                                     "faellig": i == 0, "ueberfaellig": d < heute})
        kalender.sort(key=lambda k: (k["datum"], k["uhrzeit"] or "99:99", k["firma"].lower()))
        namen = {p["id"]: p["firma"] for p in plaene}
        einsatz_liste = [{"id": e["id"], "plan_id": e["plan_id"], "firma": namen.get(e["plan_id"], "?"), "datum": e["datum"],
                          "mitarbeiter": e["mitarbeiter"] or "", "dauer_min": e["dauer_min"] or 0, "notiz": e["notiz"] or "",
                          "rechnung": bool(e["rechnung"]), "rechnung_am": e["rechnung_am"] or "", "quelle": e["quelle"] or "",
                          "details_fehlen": e["quelle"] == "lifehub" and not e["mitarbeiter"],
                          "betrag": next((p["betrag"] for p in plaene if p["id"] == e["plan_id"]), 0) or 0}
                         for e in einsaetze]
        woche_ende = heute + timedelta(days=6 - heute.weekday())
        return jsonify({
            "ok": True, "heute": heute.isoformat(), "lifehub_ok": lh_ok, "lifehub_meldung": lh_meldung,
            "plaene": aus_plaene, "termine": kalender, "einsaetze": einsatz_liste[:200],
            "kpi": {"aktiv": sum(1 for p in aus_plaene if p["aktiv"]),
                    "woche": sum(1 for k in kalender if heute <= _datum(k["datum"]) <= woche_ende),
                    "ueberfaellig": sum(1 for p in aus_plaene if p["aktiv"] and p["ueberfaellig"]),
                    "rechnung_offen": sum(1 for e in einsatz_liste if not e["rechnung"]),
                    "rechnung_offen_summe": round(sum(e["betrag"] for e in einsatz_liste if not e["rechnung"]), 2),
                    "monat": round(monat_summe, 2)},
        })

    @app.route("/api/fenster/plan", methods=["POST"])
    @login_required
    def fenster_plan_speichern():
        """Neu anlegen oder ändern. Bei geändertem Rhythmus/Start/Uhrzeit/Dauer/Firma: alte LifeHub-Serie stoppen, neue anlegen."""
        e = request.get_json(silent=True) or {}
        f, r, fehler = plan_aus_eingabe(e)
        if fehler:
            return jsonify({"ok": False, "fehler": fehler}), 400
        pid = _int(e.get("id"), 1, 10 ** 9)
        conn = get_db_connection()
        try:
            _tabellen(conn)
            alt = plan_laden(conn, pid) if pid else None
            if pid and not alt:
                return jsonify({"ok": False, "fehler": "Plan nicht gefunden."}), 404
            spalten = ("kunde_id", "firma", "ansprechpartner", "telefon", "strasse", "plz", "ort", "art", "intervall", "wochentag",
                       "monatstag", "nte", "monate", "start", "uhrzeit", "dauer_min", "betrag", "notiz", "rrule", "erster_termin")
            if alt:
                neu_serie = alt["aktiv"] and any(str(alt[k] or "") != str(f[k] or "") for k in
                                                 ("firma", "rrule", "erster_termin", "uhrzeit", "dauer_min", "strasse", "plz",
                                                  "ort", "betrag", "notiz", "ansprechpartner", "telefon"))
                conn.execute("UPDATE fenster_plan SET " + ", ".join(f"{k} = ?" for k in spalten) + ", geaendert_am = ? WHERE id = ?",
                             tuple(f[k] for k in spalten) + (_jetzt(), pid))
            else:
                neu_serie = True
                pid = conn.execute("INSERT INTO fenster_plan (" + ", ".join(spalten) + ", aktiv, erstellt_am, geaendert_am) VALUES (" +
                                   ", ".join("?" for _ in spalten) + ", 1, ?, ?)", tuple(f[k] for k in spalten) + (_jetzt(), _jetzt())).lastrowid
            conn.commit()
            p = dict(plan_laden(conn, pid))
            meldung = ""
            if neu_serie:
                try:
                    if alt and alt["lifehub_serie"]:
                        lh_serie_beenden(alt["lifehub_serie"])
                    serie = lh_serie_anlegen(p)
                    conn.execute("UPDATE fenster_plan SET lifehub_serie = ?, lifehub_fehler = NULL WHERE id = ?", (serie, pid))
                except LifeHubFehler as exc:
                    meldung = f"Gespeichert, aber nicht im LifeHub: {exc}"
                    conn.execute("UPDATE fenster_plan SET lifehub_fehler = ? WHERE id = ?", (str(exc), pid))
                conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "id": pid, "meldung": meldung or "Gespeichert – steht im LifeHub (Aufgaben und Kalender)."})

    @app.route("/api/fenster/plan/<int:pid>/pause", methods=["POST"])
    @login_required
    def fenster_plan_pause(pid):
        """Pausieren (Serie im LifeHub stoppen) oder fortsetzen (neue Serie ab heute). Nichts wird gelöscht."""
        weiter = bool((request.get_json(silent=True) or {}).get("fortsetzen"))
        conn = get_db_connection()
        try:
            p = plan_laden(conn, pid)
            if not p:
                return jsonify({"ok": False, "fehler": "Plan nicht gefunden."}), 404
            try:
                if weiter:
                    r = _r_aus_zeile(p)
                    ab = max(_heute(), _datum(p["start"]) or _heute())
                    erste = termine(r, ab, ab + timedelta(days=800))
                    if not erste:
                        return jsonify({"ok": False, "fehler": "Dieser Rhythmus ergibt keinen Termin."}), 400
                    conn.execute("UPDATE fenster_plan SET aktiv = 1, pausiert_am = NULL, erster_termin = ?, geaendert_am = ? WHERE id = ?",
                                 (erste[0].isoformat(), _jetzt(), pid))
                    serie = lh_serie_anlegen(dict(plan_laden(conn, pid)))
                    conn.execute("UPDATE fenster_plan SET lifehub_serie = ?, lifehub_fehler = NULL WHERE id = ?", (serie, pid))
                else:
                    if p["lifehub_serie"]:
                        lh_serie_beenden(p["lifehub_serie"])
                    conn.execute("UPDATE fenster_plan SET aktiv = 0, pausiert_am = ?, geaendert_am = ? WHERE id = ?", (_jetzt(), _jetzt(), pid))
            except LifeHubFehler as exc:
                conn.rollback()
                return jsonify({"ok": False, "fehler": f"LifeHub: {exc}"}), 503
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.route("/api/fenster/plan/<int:pid>/erledigt", methods=["POST"])
    @login_required
    def fenster_erledigt(pid):
        """Fälligen Termin erledigen: Angaben speichern, Aufgabe im LifeHub abhaken (LifeHub legt den nächsten an)."""
        e = request.get_json(silent=True) or {}
        conn = get_db_connection()
        try:
            p = plan_laden(conn, pid)
            if not p or not p["aktiv"]:
                return jsonify({"ok": False, "fehler": "Plan nicht gefunden oder pausiert."}), 404
            datum, aufgabe = _datum(e.get("datum")), None
            if p["lifehub_serie"]:
                try:
                    aufgabe, lh_datum = lh_offen(ZUGANG.anfrage("GET", f"/recurrences/{p['lifehub_serie']}"))
                    datum = lh_datum or datum
                except LifeHubFehler as exc:
                    return jsonify({"ok": False, "fehler": f"LifeHub: {exc}"}), 503
            if not datum:
                return jsonify({"ok": False, "fehler": "Kein fälliger Termin."}), 400
            werte = (_txt(e.get("mitarbeiter"), 200), _int(e.get("dauer_min"), 0, 24 * 60, 0), _txt(e.get("notiz"), 1000),
                     1 if e.get("rechnung") else 0, _jetzt() if e.get("rechnung") else None)
            cur = conn.execute("""INSERT INTO fenster_einsatz (plan_id, datum, lifehub_aufgabe, erledigt_am, mitarbeiter, dauer_min, notiz,
                                  rechnung, rechnung_am, quelle) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'crm')""",
                               (pid, datum.isoformat(), aufgabe, _jetzt()) + werte)
            if aufgabe:
                try:
                    ZUGANG.anfrage("POST", f"/tasks/{aufgabe}/complete")
                except LifeHubFehler as exc:
                    conn.rollback()
                    return jsonify({"ok": False, "fehler": f"LifeHub: {exc}"}), 503
            conn.commit()
            eid = cur.lastrowid
        finally:
            conn.close()
        return jsonify({"ok": True, "id": eid})

    @app.route("/api/fenster/einsatz/<int:eid>", methods=["POST"])
    @login_required
    def fenster_einsatz_aendern(eid):
        """Angaben eines Einsatzes ergänzen (z. B. im LifeHub abgehakt) oder „Rechnung geschrieben“ setzen."""
        e = request.get_json(silent=True) or {}
        conn = get_db_connection()
        try:
            alt = conn.execute("SELECT * FROM fenster_einsatz WHERE id = ?", (eid,)).fetchone()
            if not alt:
                return jsonify({"ok": False, "fehler": "Einsatz nicht gefunden."}), 404
            neu = {k: alt[k] for k in ("mitarbeiter", "dauer_min", "notiz", "rechnung", "rechnung_am")}
            if "mitarbeiter" in e:
                neu["mitarbeiter"] = _txt(e.get("mitarbeiter"), 200)
            if "dauer_min" in e:
                neu["dauer_min"] = _int(e.get("dauer_min"), 0, 24 * 60, 0)
            if "notiz" in e:
                neu["notiz"] = _txt(e.get("notiz"), 1000)
            if "rechnung" in e:
                neu["rechnung"] = 1 if e.get("rechnung") else 0
                neu["rechnung_am"] = (alt["rechnung_am"] or _jetzt()) if neu["rechnung"] else None
            conn.execute("UPDATE fenster_einsatz SET mitarbeiter = ?, dauer_min = ?, notiz = ?, rechnung = ?, rechnung_am = ? WHERE id = ?",
                         (neu["mitarbeiter"], neu["dauer_min"], neu["notiz"], neu["rechnung"], neu["rechnung_am"], eid))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True})
