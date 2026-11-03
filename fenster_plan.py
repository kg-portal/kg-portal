# Fensterreinigung (/fensterreinigung): feste Fenster-Kunden mit eigenem Rhythmus planen.
# Je Kunde: EIN wiederkehrender Termin im gemeinsamen Google-Kalender „Fensterreinigung“ (mit Uhrzeit, alle Termine –
# Handy, CRM-Kalender und über den Abgleich auch der LifeHub-Kalender) und EINE wiederkehrende Aufgabe im KG LifeHub
# („Aufgaben“, fester Plan, ohne Uhrzeit – sonst stünde der Termin im LifeHub-Kalender doppelt); nach „Erledigt“ legt
# LifeHub die nächste Aufgabe selbst an.
# Das CRM speichert nur den Plan (fenster_plan) und je Einsatz Mitarbeiter, Dauer, Notiz, Rechnung (fenster_einsatz).
# „Pausieren“ / neuer Rhythmus: LifeHub-Serie stoppen (offene Aufgabe archiviert), Google-Serie endet gestern –
# vergangene Termine bleiben im Kalender (eine Serie, die erst in der Zukunft beginnt, wird entfernt).
# LifeHub-Zugang wie /aufgaben (lifehub_bruecke.ZUGANG). Ohne LifeHub: Pläne und Termine werden nur im CRM berechnet.
import calendar
import json
import re
from datetime import date, datetime, time, timedelta, timezone
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
# Google-Kalender „Fensterreinigung“ (gleiche ID wie im CRM-Kalender, app.py → google_calendar_events_save)
G_KALENDER = "f678773df8f3612fe9c77aff8c35941bf8ece7eb7d6e6f503c85d277b4e190b0@group.calendar.google.com"
G_ZONE = "Europe/Berlin"
# Erledigt = grün: Einzeltermin im Google-Kalender (Farbe 10 „Basilikum“ – so auch im LifeHub-, CRM- und Handy-Kalender)
# und die LifeHub-Aufgabe (meta.farbe). Zurückgenommen → wieder die normale Farbe.
G_GRUEN, LH_FARBE_ERLEDIGT = "10", "basilikum"
KALENDER_DIENST = None  # beim Start gesetzt: app.get_calendar_service
FUNKTIONEN = {}         # beim Start gefüllt: dieselben Funktionen wie die Seite – für ChatGPT (fenster_chatgpt.py)


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
    # Knopf „Fenster ja / nein“ auf der Kundenkarte: nur „ja“ steht in den Auswahl-Listen (Kunden → Fensterreinigung, Formular).
    # Ohne Eintrag gilt „ja“, wenn der Kunde einen Plan oder Fenster-Angaben im Kundenformular hat. Der Kunde selbst bleibt unverändert.
    conn.execute("CREATE TABLE IF NOT EXISTS fenster_kunde (kunde_id INTEGER PRIMARY KEY, fenster INTEGER NOT NULL, firma TEXT, geaendert_am TEXT)")
    spalten = {r[1] for r in conn.execute("PRAGMA table_info(fenster_plan)")}
    for neu in ("google_termin", "google_ab", "google_fehler"):
        if neu not in spalten:
            conn.execute(f"ALTER TABLE fenster_plan ADD COLUMN {neu} TEXT")
    # „Erledigt“ zurückgenommen: der Einsatz bleibt gespeichert (storniert_am), die LifeHub-Aufgabe steht in storniert_aufgabe
    spalten = {r[1] for r in conn.execute("PRAGMA table_info(fenster_einsatz)")}
    for neu in ("storniert_am", "storniert_aufgabe", "google_gruen"):
        if neu not in spalten:
            conn.execute(f"ALTER TABLE fenster_einsatz ADD COLUMN {neu} TEXT")
    conn.commit()


def _jetzt():
    return datetime.now(BERLIN).strftime("%Y-%m-%d %H:%M:%S")


def _heute():
    return datetime.now(BERLIN).date()


def _fenster_angaben(d):
    """Fenster-Angaben im Kundenformular (fenster_betrag und fenster_mal > 0) – wie fensterLesen() auf der Kunden-Seite."""
    try:
        return (_zahl(d.get("fenster_betrag")) or 0) > 0 and int(str(d.get("fenster_mal") or "0").strip() or 0) > 0
    except (TypeError, ValueError):
        return False


def _fenster_markieren(conn, kunde_id, firma, ja):
    conn.execute("""INSERT INTO fenster_kunde (kunde_id, fenster, firma, geaendert_am) VALUES (?, ?, ?, ?)
                    ON CONFLICT(kunde_id) DO UPDATE SET fenster = excluded.fenster, firma = excluded.firma,
                    geaendert_am = excluded.geaendert_am""", (kunde_id, 1 if ja else 0, firma, _jetzt()))


def _berlin_text(iso):
    """Zeitstempel aus dem LifeHub (ISO, UTC) → „JJJJ-MM-TT HH:MM:SS“ Berliner Zeit wie _jetzt(); unlesbar → ""."""
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(BERLIN).strftime("%Y-%m-%d %H:%M:%S")


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

def _dauer_text(minuten):
    h, m = divmod(int(minuten or 0), 60)
    return (f"{h}:{m:02d} Std" if m else f"{h} Std") if h else (f"{m} Min" if m else "")


def _adresse(p):
    return " ".join(x for x in (p["strasse"], " ".join(y for y in (p["plz"], p["ort"]) if y)) if x)


def _lh_beschreibung(p):
    # Rhythmus als Text dazu: LifeHub beschreibt manche Regeln (z. B. mehrere Monate) nur ungenau, die Termine stimmen
    zeit = " · ".join(x for x in ((p["uhrzeit"] + " Uhr") if p["uhrzeit"] else "", _dauer_text(p["dauer_min"])) if x)
    zeilen = ["Rhythmus: " + beschreibung(_r_aus_zeile(p)) + " (fester Plan)" + (" · " + zeit if zeit else ""),
              _adresse(p)]
    if p["ansprechpartner"] or p["telefon"]:
        zeilen.append(" · ".join(x for x in (p["ansprechpartner"], p["telefon"]) if x))
    if p["betrag"]:
        zeilen.append("Betrag je Einsatz: " + _euro(p["betrag"]) + " netto")
    if p["notiz"]:
        zeilen.append("Notiz: " + p["notiz"])
    zeilen.append("Aus KG CRM → Fensterreinigung. Nach dem Einsatz bitte im CRM „Erledigt“ drücken "
                  "(Mitarbeiter, Dauer, Rechnung).")
    return "\n".join(z for z in zeilen if z)


def lh_serie_anlegen(p, ab=None):
    """Neue wiederkehrende Aufgabe im LifeHub ab dem Datum ab (sonst erster Termin); gibt die Serien-ID zurück."""
    neu = {"title": TITEL + p["firma"], "description": _lh_beschreibung(p), "planned_date": (ab.isoformat() if ab else p["erster_termin"]),
           "recurrence": {"rule": p["rrule"], "mode": "schedule"},
           "meta": {"gruppe": LH_GRUPPE, "farbe": LH_FARBE, "quelle": "kg-crm-fenster", "fenster_plan": p["id"]},
           # Erinnerung im LifeHub (Glocke) am Vortag 17:00 – gilt für jeden Termin der Serie
           "reminders": [{"anchor": "planned", "offset_days": -1, "at_time": "17:00", "level": "prep",
                          "label": "Morgen Fensterreinigung: " + p["firma"]}]}
    # ohne Uhrzeit: der Termin mit Uhrzeit kommt aus dem Google-Kalender in den LifeHub-Kalender
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
    """Offener Termin (Aufgabe) der Serie: (id, Termin laut Regel, geplantes Datum) oder (None, None, None)."""
    tid = serie_daten.get("open_instance_id")
    if not tid:
        return None, None, None
    t = ZUGANG.anfrage("GET", f"/tasks/{tid}")
    return tid, _datum(t.get("occurrence_date") or t.get("planned_date")), _datum(t.get("planned_date"))


# ---------------------------------------------------------------- Google-Kalender

class GoogleFehler(Exception):
    pass


def _g_ausfuehren(anfrage_bauen):
    if KALENDER_DIENST is None:
        raise GoogleFehler("Google-Kalender nicht eingerichtet.")
    try:
        return anfrage_bauen(KALENDER_DIENST().events()).execute()
    except Exception as exc:  # Schlüssel fehlt, keine Schreibrechte, Netz …
        status = getattr(getattr(exc, "resp", None), "status", "")
        raise GoogleFehler(f"Google-Kalender: {type(exc).__name__} {status}".strip())


def _g_body(p, ab):
    """Wiederkehrender Termin ab dem Datum ab (ab = erster Termin der Serie, dieselbe Regel wie im LifeHub)."""
    body = {"summary": TITEL + p["firma"], "location": _adresse(p), "description": _lh_beschreibung(p),
            "recurrence": ["RRULE:" + p["rrule"]],
            "extendedProperties": {"private": {"kg_fenster_plan": str(p["id"])}}}
    if p["uhrzeit"]:
        beginn = datetime.combine(ab, time.fromisoformat(p["uhrzeit"]))
        ende = beginn + timedelta(minutes=p["dauer_min"] or 60)
        body["start"] = {"dateTime": beginn.isoformat(), "timeZone": G_ZONE}
        body["end"] = {"dateTime": ende.isoformat(), "timeZone": G_ZONE}
    else:
        body["start"] = {"date": ab.isoformat()}
        body["end"] = {"date": (ab + timedelta(days=1)).isoformat()}
    return body


def g_serie_anlegen(p, ab):
    t = _g_ausfuehren(lambda ev: ev.insert(calendarId=G_KALENDER, body=_g_body(p, ab)))
    return t.get("id")


def g_serie_beenden(termin_id):
    """Serie endet gestern (vergangene Termine bleiben). Beginnt sie erst heute oder später, wird sie entfernt."""
    try:
        ev = _g_ausfuehren(lambda e: e.get(calendarId=G_KALENDER, eventId=termin_id))
    except GoogleFehler as exc:
        if str(exc).endswith((" 404", " 410")):  # im Kalender schon entfernt
            return
        raise
    if ev.get("status") == "cancelled":
        return
    start = ev.get("start") or {}
    heute = _heute()
    beginn = _datum(start.get("date") or start.get("dateTime"))
    if not beginn or beginn >= heute:
        _g_ausfuehren(lambda e: e.delete(calendarId=G_KALENDER, eventId=termin_id))
        return
    if start.get("dateTime"):  # UNTIL in UTC: gestern 23:59:59 (Berlin)
        bis = datetime.combine(heute, time(0), tzinfo=BERLIN).astimezone(timezone.utc) - timedelta(seconds=1)
        until = bis.strftime("%Y%m%dT%H%M%SZ")
    else:
        until = (heute - timedelta(days=1)).strftime("%Y%m%d")
    regeln = []
    for zeile in ev.get("recurrence") or []:
        if zeile.upper().startswith("RRULE:"):
            teile = [x for x in zeile[6:].split(";") if x and not x.upper().startswith(("UNTIL=", "COUNT="))]
            zeile = "RRULE:" + ";".join(teile + ["UNTIL=" + until])
        regeln.append(zeile)
    _g_ausfuehren(lambda e: e.patch(calendarId=G_KALENDER, eventId=termin_id, body={"recurrence": regeln}))


def _g_plan_von(it):
    return str(((it.get("extendedProperties") or {}).get("private") or {}).get("kg_fenster_plan") or "")


def g_einsaetze_faerben(liste, gruen=True):
    """Einzeltermine [(plan_id, datum), …] so, wie sie im Kalender stehen, grün färben (erledigt) bzw. die Farbe zurücknehmen.
    Eine Abfrage für alle; gesucht wird über die Plan-Nummer am Termin (auch in älteren Serien desselben Plans).
    → {(plan_id, datum): True = gesetzt/schon so, False = an diesem Tag kein Termin}"""
    if not liste:
        return {}
    von, bis = min(d for _, d in liste), max(d for _, d in liste) + timedelta(days=1)
    t_von = datetime.combine(von, time(0), tzinfo=BERLIN).isoformat()
    t_bis = datetime.combine(bis, time(0), tzinfo=BERLIN).isoformat()
    gesucht = {(str(pid), d): (pid, d) for pid, d in liste}
    aus, seite = {schl: False for schl in gesucht.values()}, None
    for _ in range(10):
        r = _g_ausfuehren(lambda ev, seite=seite: ev.list(calendarId=G_KALENDER, timeMin=t_von, timeMax=t_bis, singleEvents=True,
                                                         maxResults=2500, pageToken=seite))
        for it in r.get("items") or []:
            st = it.get("start") or {}
            schl = gesucht.get((_g_plan_von(it), _datum(st.get("date") or st.get("dateTime"))))
            if not schl or it.get("status") == "cancelled" or aus[schl]:
                continue
            ziel = G_GRUEN if gruen else None
            if (it.get("colorId") or None) != ziel:
                _g_ausfuehren(lambda ev, i=it["id"]: ev.patch(calendarId=G_KALENDER, eventId=i, body={"colorId": ziel}))
            aus[schl] = True
        seite = r.get("nextPageToken")
        if not seite:
            break
    return aus


def lh_faerben(aufgabe, farbe):
    """Farbe der LifeHub-Aufgabe (meta.farbe) setzen, die übrigen Angaben bleiben."""
    t = ZUGANG.anfrage("GET", f"/tasks/{aufgabe}")
    meta = dict(t.get("meta") or {})
    if meta.get("farbe") != farbe:
        meta["farbe"] = farbe
        ZUGANG.anfrage("PATCH", f"/tasks/{aufgabe}", json={"meta": meta})


def g_tatsaechlich(von, bis):
    """Einzeltermine im Google-Kalender „Fensterreinigung“, so wie sie dort stehen (auch verschoben oder gelöscht).
    → {Serien-ID: {Datum laut Regel: (Datum im Kalender oder None = gelöscht, Uhrzeit)}}"""
    aus, seite = {}, None
    t_von = datetime.combine(von, time(0), tzinfo=BERLIN).isoformat()
    t_bis = datetime.combine(bis, time(0), tzinfo=BERLIN).isoformat()
    for _ in range(20):
        d = _g_ausfuehren(lambda ev, seite=seite: ev.list(calendarId=G_KALENDER, timeMin=t_von, timeMax=t_bis, singleEvents=True,
                                                         showDeleted=True, maxResults=2500, pageToken=seite))
        for it in d.get("items") or []:
            serie, orig = it.get("recurringEventId"), it.get("originalStartTime") or {}
            regel = _datum(orig.get("date") or orig.get("dateTime"))
            if not serie or not regel:
                continue
            if it.get("status") == "cancelled":
                aus.setdefault(serie, {})[regel] = (None, "")
                continue
            st = it.get("start") or {}
            aus.setdefault(serie, {})[regel] = (_datum(st.get("date") or st.get("dateTime")), str(st.get("dateTime") or "")[11:16])
        seite = d.get("nextPageToken")
        if not seite:
            break
    return aus


# ---------------------------------------------------------------- Routen

def register_fenster_plan(app, login_required, get_db_connection, kalender_dienst=None):
    global KALENDER_DIENST
    KALENDER_DIENST = kalender_dienst

    conn = get_db_connection()
    try:
        _tabellen(conn)
    finally:
        conn.close()

    def plan_laden(conn, pid):
        return conn.execute("SELECT * FROM fenster_plan WHERE id = ?", (pid,)).fetchone()

    def serien_anlegen(conn, p, alt, lifehub, google):
        """LifeHub-Aufgabe und/oder Google-Termin (neu) anlegen; alt = vorheriger Stand, dessen Serien enden.
        Gibt zurück, was nicht geklappt hat (Text für die Meldung); Fehler stehen auch am Plan."""
        fehlt = []
        ab = _datum(p["erster_termin"])
        if alt:  # geänderter Plan: Vergangenes gehört zur alten Serie – die neue beginnt mit dem ersten Termin ab heute
            neu_ab = termine(_r_aus_zeile(p), ab, max(ab, _heute()) + timedelta(days=800), ab=max(ab, _heute()))
            ab = neu_ab[0] if neu_ab else ab
        if lifehub:
            try:
                if alt and alt["lifehub_serie"]:
                    lh_serie_beenden(alt["lifehub_serie"])
                serie = lh_serie_anlegen(p, ab)
                conn.execute("UPDATE fenster_plan SET lifehub_serie = ?, lifehub_fehler = NULL WHERE id = ?", (serie, p["id"]))
            except LifeHubFehler as exc:
                fehlt.append(f"nicht im LifeHub ({exc})")
                conn.execute("UPDATE fenster_plan SET lifehub_fehler = ? WHERE id = ?", (str(exc), p["id"]))
            conn.commit()  # sofort schreiben – während der Google-Abfrage bleibt die Datenbank frei
        if google:
            try:
                if alt and alt["google_termin"]:
                    g_serie_beenden(alt["google_termin"])
                termin = g_serie_anlegen(p, ab)
                conn.execute("UPDATE fenster_plan SET google_termin = ?, google_ab = ?, google_fehler = NULL WHERE id = ?",
                             (termin, ab.isoformat(), p["id"]))
            except GoogleFehler as exc:
                fehlt.append(f"nicht im Google-Kalender ({exc})")
                conn.execute("UPDATE fenster_plan SET google_fehler = ? WHERE id = ?", (str(exc), p["id"]))
        conn.commit()
        return fehlt

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

    def stammdaten_daten():
        """Für das Formular: Kunden (ohne gekündigte; fenster = Knopf „Fenster ja/nein“ der Kundenkarte) und aktive Mitarbeiter."""
        conn = get_db_connection()
        try:
            _tabellen(conn)
            wahl = {z[0]: bool(z[1]) for z in conn.execute("SELECT kunde_id, fenster FROM fenster_kunde")}
            mit_plan = {z[0] for z in conn.execute("SELECT kunde_id FROM fenster_plan WHERE aktiv = 1 AND kunde_id IS NOT NULL")}
            kunden = []
            for k in conn.execute("SELECT id, firma, strasse, plz, ort, ansprechpartner_name, telefon, data_json, vertragsstatus "
                                  "FROM kunden ORDER BY firma COLLATE NOCASE").fetchall():
                if (k["vertragsstatus"] or "").strip().lower() == "gekuendigt":
                    continue
                try:
                    d = json.loads(k["data_json"] or "{}")
                except ValueError:
                    d = {}
                d = d if isinstance(d, dict) else {}
                kunden.append({"id": k["id"], "firma": k["firma"] or "", "strasse": k["strasse"] or "", "plz": k["plz"] or "",
                               "ort": k["ort"] or "", "ansprechpartner": k["ansprechpartner_name"] or "", "telefon": k["telefon"] or "",
                               "fenster_betrag": d.get("fenster_betrag") or "", "fenster_mal": d.get("fenster_mal") or "",
                               "fenster_notiz": d.get("fenster_notiz") or "",
                               "fenster": wahl[k["id"]] if k["id"] in wahl else (k["id"] in mit_plan or _fenster_angaben(d))})
            ma = [_txt(f"{m['vorname'] or ''} {m['nachname'] or ''}", 120) for m in
                  conn.execute("SELECT vorname, nachname FROM mitarbeiter WHERE status = 'aktiv' ORDER BY sort_order, vorname").fetchall()]
        finally:
            conn.close()
        kunden.sort(key=lambda k: (not k["fenster_mal"], k["firma"].lower()))
        return dict({"ok": True, "kunden": kunden, "mitarbeiter": [m for m in ma if m]})

    def uebersicht_daten():
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
                    # im CRM zurückgenommen: dieselbe Aufgabe nicht wieder aufnehmen – außer sie wurde danach erneut abgehakt
                    storniert = {z["storniert_aufgabe"]: z["storniert_am"] for z in conn.execute(
                        "SELECT storniert_aufgabe, storniert_am FROM fenster_einsatz WHERE storniert_aufgabe IS NOT NULL ORDER BY id")}
                    for t in fertig:
                        pid = ids.get(t.get("recurrence_id"))
                        if t.get("id") in storniert and _berlin_text(t.get("completed_at")) <= (storniert[t["id"]] or ""):
                            continue
                        if pid and t.get("id"):
                            conn.execute("""INSERT OR IGNORE INTO fenster_einsatz (plan_id, datum, lifehub_aufgabe, erledigt_am, quelle)
                                            VALUES (?, ?, ?, ?, 'lifehub')""",
                                         (pid, t.get("occurrence_date") or t.get("planned_date") or "", t["id"], _jetzt()))
                        meta = t.get("meta") or {}  # erledigt = grün – alle Fenster-Aufgaben aus dem CRM, auch aus älteren Serien
                        if t.get("id") and meta.get("quelle") == "kg-crm-fenster" and meta.get("farbe") != LH_FARBE_ERLEDIGT:
                            lh_faerben(t["id"], LH_FARBE_ERLEDIGT)
                    conn.commit()
            except LifeHubFehler as exc:
                lh_ok, lh_meldung = False, str(exc)
            einsaetze = conn.execute("SELECT * FROM fenster_einsatz WHERE storniert_am IS NULL ORDER BY datum DESC, id DESC").fetchall()
            # erledigte Termine im Google-Kalender grün (einmal je Einsatz; „fehlt“ = an dem Tag kein Kalendertermin)
            offen_gruen = [e for e in einsaetze if not e["google_gruen"] and _datum(e["datum"])][:60]
            if offen_gruen and KALENDER_DIENST is not None:
                try:
                    ergebnis = g_einsaetze_faerben([(e["plan_id"], _datum(e["datum"])) for e in offen_gruen])
                    for e in offen_gruen:
                        conn.execute("UPDATE fenster_einsatz SET google_gruen = ? WHERE id = ?",
                                     (_jetzt() if ergebnis.get((e["plan_id"], _datum(e["datum"]))) else "fehlt", e["id"]))
                    conn.commit()
                except GoogleFehler as exc:
                    print(f"Fensterreinigung: erledigte Termine nicht grün gefärbt ({exc})")
        finally:
            conn.close()

        letzte = {}
        for e in einsaetze:
            letzte.setdefault(e["plan_id"], e)
        # im Google-Kalender verschobene oder gelöschte Einzeltermine (der Plan selbst bleibt, wie er ist)
        g_karte, g_meldung = {}, ""
        if any(p["aktiv"] and p["google_termin"] for p in plaene):
            try:
                # 13 Monate: auch ein fälliger Termin weit nach den 8 Wochen (z. B. alle 3 Monate) ist dabei
                g_karte = g_tatsaechlich(heute - timedelta(days=70), heute + timedelta(days=400))
            except GoogleFehler as exc:
                g_meldung = str(exc)
        aus_plaene, kalender, monat_summe = [], [], 0.0
        for p in plaene:
            r = _r_aus_zeile(p)
            start = _datum(p["erster_termin"]) or _datum(p["start"])
            s = serien.get(p["lifehub_serie"]) if p["lifehub_serie"] else None
            offen_id, offen_datum, offen_geplant = None, None, None
            if p["aktiv"] and s:
                try:
                    offen_id, offen_datum, offen_geplant = lh_offen(s)
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
            # Termine so, wie sie im Google-Kalender stehen: verschoben → neues Datum/Uhrzeit, gelöscht → fällt weg
            karte = g_karte.get(p["google_termin"], {}) if p["google_termin"] else {}
            echte = []
            for i, d in enumerate(naechste):
                neu, zeit = karte.get(d, (d, ""))
                if neu is not None:
                    echte.append({"datum": neu, "regel": d, "uhrzeit": zeit or p["uhrzeit"] or "", "faellig": i == 0})
            if p["aktiv"] and naechste and not echte:  # alle gezeigten im Kalender gelöscht → nächster Termin laut Plan danach
                for d in termine(r, start, naechste[-1] + timedelta(days=800), ab=naechste[-1] + timedelta(days=1))[:24]:
                    neu, zeit = karte.get(d, (d, ""))
                    if neu is not None:
                        echte.append({"datum": neu, "regel": d, "uhrzeit": zeit or p["uhrzeit"] or "", "faellig": True})
                        break
            if echte and not any(x["faellig"] for x in echte):
                echte[0]["faellig"] = True
            echte.sort(key=lambda x: x["datum"])
            # LifeHub-Aufgabe zum fälligen Termin nachziehen (im Kalender verschoben → Datum, gelöscht → überspringen)
            if offen_id and offen_datum in karte:
                neu = karte[offen_datum][0]
                try:
                    if neu is None:
                        ZUGANG.anfrage("POST", f"/tasks/{offen_id}/archive")  # archiviert, nicht gelöscht; LifeHub legt den nächsten an
                    elif neu != offen_geplant:
                        ZUGANG.anfrage("PATCH", f"/tasks/{offen_id}", json={"planned_date": neu.isoformat()})
                except LifeHubFehler as exc:
                    lh_ok, lh_meldung = False, str(exc)
            faellig = next((x for x in echte if x["faellig"]), None)
            e_letzt = letzte.get(p["id"])
            eintrag = {
                "id": p["id"], "firma": p["firma"], "kunde_id": p["kunde_id"], "ansprechpartner": p["ansprechpartner"] or "",
                "telefon": p["telefon"] or "", "strasse": p["strasse"] or "", "plz": p["plz"] or "", "ort": p["ort"] or "",
                "rhythmus": r, "rhythmus_text": beschreibung(r), "start": p["start"], "uhrzeit": p["uhrzeit"] or "",
                "dauer_min": p["dauer_min"] or 0, "betrag": p["betrag"] or 0, "notiz": p["notiz"] or "", "aktiv": bool(p["aktiv"]),
                "pausiert_am": p["pausiert_am"] or "", "im_jahr": im_jahr, "lifehub": bool(p["lifehub_serie"]),
                "lifehub_fehler": p["lifehub_fehler"] or "", "offen_aufgabe": offen_id,
                "google": bool(p["google_termin"]), "google_fehler": p["google_fehler"] or "",
                "naechster": echte[0]["datum"].isoformat() if echte else "",
                "ueberfaellig": bool(faellig and faellig["datum"] < heute),
                "letzter": {"datum": e_letzt["datum"], "mitarbeiter": e_letzt["mitarbeiter"] or ""} if e_letzt else None,
            }
            aus_plaene.append(eintrag)
            for x in echte:
                if x["datum"] <= bis:
                    kalender.append({"plan_id": p["id"], "datum": x["datum"].isoformat(), "firma": p["firma"], "ort": p["ort"] or "",
                                     "uhrzeit": x["uhrzeit"], "dauer_min": p["dauer_min"] or 0, "betrag": p["betrag"] or 0,
                                     "faellig": x["faellig"], "ueberfaellig": x["datum"] < heute,
                                     "verschoben_von": x["regel"].isoformat() if x["regel"] != x["datum"] else ""})
        kalender.sort(key=lambda k: (k["datum"], k["uhrzeit"] or "99:99", k["firma"].lower()))
        namen = {p["id"]: p["firma"] for p in plaene}
        einsatz_liste = [{"id": e["id"], "plan_id": e["plan_id"], "firma": namen.get(e["plan_id"], "?"), "datum": e["datum"],
                          "mitarbeiter": e["mitarbeiter"] or "", "dauer_min": e["dauer_min"] or 0, "notiz": e["notiz"] or "",
                          "rechnung": bool(e["rechnung"]), "rechnung_am": e["rechnung_am"] or "", "quelle": e["quelle"] or "",
                          "details_fehlen": e["quelle"] == "lifehub" and not e["mitarbeiter"],
                          "rueckgaengig": letzte.get(e["plan_id"]) is not None and letzte[e["plan_id"]]["id"] == e["id"],
                          "betrag": next((p["betrag"] for p in plaene if p["id"] == e["plan_id"]), 0) or 0}
                         for e in einsaetze]
        woche_ende = heute + timedelta(days=6 - heute.weekday())
        return dict({
            "ok": True, "heute": heute.isoformat(), "lifehub_ok": lh_ok, "lifehub_meldung": lh_meldung, "google_meldung": g_meldung,
            "plaene": aus_plaene, "termine": kalender, "einsaetze": einsatz_liste[:200],
            "kpi": {"aktiv": sum(1 for p in aus_plaene if p["aktiv"]),
                    "woche": sum(1 for k in kalender if heute <= _datum(k["datum"]) <= woche_ende),
                    "ueberfaellig": sum(1 for p in aus_plaene if p["aktiv"] and p["ueberfaellig"]),
                    "rechnung_offen": sum(1 for e in einsatz_liste if not e["rechnung"]),
                    "rechnung_offen_summe": round(sum(e["betrag"] for e in einsatz_liste if not e["rechnung"]), 2),
                    "monat": round(monat_summe, 2)},
        })

    def plan_speichern(e):
        """Neu anlegen oder ändern. Bei geändertem Rhythmus/Start/Uhrzeit/Dauer/Firma: alte LifeHub-Serie stoppen, neue anlegen."""
        f, r, fehler = plan_aus_eingabe(e)
        if fehler:
            return dict({"ok": False, "fehler": fehler}), 400
        pid = _int(e.get("id"), 1, 10 ** 9)
        conn = get_db_connection()
        try:
            _tabellen(conn)
            alt = plan_laden(conn, pid) if pid else None
            if pid and not alt:
                return dict({"ok": False, "fehler": "Plan nicht gefunden."}), 404
            if not pid:  # zweimal gedrückt / Antwort nicht angekommen: denselben neuen Plan nicht doppelt anlegen
                kurz_vorher = (datetime.now(BERLIN) - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
                gleich = conn.execute("""SELECT * FROM fenster_plan WHERE aktiv = 1 AND firma = ? AND rrule = ? AND erster_termin = ?
                                         AND erstellt_am >= ? ORDER BY id DESC LIMIT 1""",
                                      (f["firma"], f["rrule"], f["erster_termin"], kurz_vorher)).fetchone()
                if gleich:
                    pid, alt = gleich["id"], gleich
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
            if not alt and f["kunde_id"]:  # neuer Plan für einen Kunden aus der Liste → Knopf „Fenster ja“
                _fenster_markieren(conn, f["kunde_id"], f["firma"], True)
            conn.commit()
            p = dict(plan_laden(conn, pid))
            fehlt = []
            if p["aktiv"]:
                # neu anlegen, was geändert ist oder noch fehlt (z. B. beim ersten Mal nicht erreichbar)
                fehlt = serien_anlegen(conn, p, alt if neu_serie else None,
                                       lifehub=neu_serie or not p["lifehub_serie"], google=neu_serie or not p["google_termin"])
        finally:
            conn.close()
        return dict({"ok": True, "id": pid, "meldung": ("Gespeichert, aber " + " und ".join(fehlt)) if fehlt else
                        "Gespeichert – im Google-Kalender „Fensterreinigung“ und in den LifeHub-Aufgaben."})

    def plan_pause(pid, weiter):
        """Pausieren (Serie im LifeHub stoppen) oder fortsetzen (neue Serie ab heute). Nichts wird gelöscht."""
        conn = get_db_connection()
        try:
            p = plan_laden(conn, pid)
            if not p:
                return dict({"ok": False, "fehler": "Plan nicht gefunden."}), 404
            fehlt = []
            if weiter:
                r = _r_aus_zeile(p)
                ab = max(_heute(), _datum(p["start"]) or _heute())
                erste = termine(r, ab, ab + timedelta(days=800))
                if not erste:
                    return dict({"ok": False, "fehler": "Dieser Rhythmus ergibt keinen Termin."}), 400
                conn.execute("UPDATE fenster_plan SET aktiv = 1, pausiert_am = NULL, erster_termin = ?, google_termin = NULL, "
                             "geaendert_am = ? WHERE id = ?", (erste[0].isoformat(), _jetzt(), pid))
                conn.commit()
                fehlt = serien_anlegen(conn, dict(plan_laden(conn, pid)), None, lifehub=True, google=True)
            else:
                if p["google_termin"]:
                    try:
                        g_serie_beenden(p["google_termin"])
                        conn.execute("UPDATE fenster_plan SET google_termin = NULL, google_fehler = NULL WHERE id = ?", (pid,))
                    except GoogleFehler as exc:
                        fehlt.append(f"im Google-Kalender nicht beendet ({exc})")
                        conn.execute("UPDATE fenster_plan SET google_fehler = ? WHERE id = ?", (str(exc), pid))
                if p["lifehub_serie"]:
                    try:
                        lh_serie_beenden(p["lifehub_serie"])
                    except LifeHubFehler as exc:
                        fehlt.append(f"im LifeHub nicht gestoppt ({exc})")
                conn.execute("UPDATE fenster_plan SET aktiv = 0, pausiert_am = ?, geaendert_am = ? WHERE id = ?", (_jetzt(), _jetzt(), pid))
                conn.commit()
        finally:
            conn.close()
        return dict({"ok": True, "meldung": ("Achtung: " + " und ".join(fehlt)) if fehlt else ""})

    def plan_erledigt(pid, e):
        """Fälligen Termin erledigen: Angaben speichern, Aufgabe im LifeHub abhaken (LifeHub legt den nächsten an)."""
        conn = get_db_connection()
        try:
            p = plan_laden(conn, pid)
            if not p or not p["aktiv"]:
                return dict({"ok": False, "fehler": "Plan nicht gefunden oder pausiert."}), 404
            datum, aufgabe = _datum(e.get("datum")), None
            if p["lifehub_serie"]:
                try:
                    aufgabe, lh_datum, lh_geplant = lh_offen(ZUGANG.anfrage("GET", f"/recurrences/{p['lifehub_serie']}"))
                    angezeigt = datum
                    datum = lh_geplant or lh_datum or datum
                    if angezeigt and lh_datum and abs((angezeigt - lh_datum).days) <= 45:
                        datum = angezeigt  # so, wie der Termin auf der Seite/im Kalender stand
                except LifeHubFehler as exc:
                    return dict({"ok": False, "fehler": f"LifeHub: {exc}"}), 503
            if not datum:
                return dict({"ok": False, "fehler": "Kein fälliger Termin."}), 400
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
                    return dict({"ok": False, "fehler": f"LifeHub: {exc}"}), 503
            conn.commit()
            eid = cur.lastrowid
            # erledigt = grün (LifeHub-Aufgabe und Termin im Google-Kalender); klappt das nicht, holt es die Übersicht nach
            if aufgabe:
                try:
                    lh_faerben(aufgabe, LH_FARBE_ERLEDIGT)
                except LifeHubFehler as exc:
                    print(f"Fensterreinigung: LifeHub-Aufgabe nicht grün ({exc})")
            if KALENDER_DIENST is not None:
                try:
                    if g_einsaetze_faerben([(pid, datum)]).get((pid, datum)):
                        conn.execute("UPDATE fenster_einsatz SET google_gruen = ? WHERE id = ?", (_jetzt(), eid))
                        conn.commit()
                except GoogleFehler as exc:
                    print(f"Fensterreinigung: Termin nicht grün gefärbt ({exc})")
        finally:
            conn.close()
        return dict({"ok": True, "id": eid})

    def einsatz_aendern(eid, e):
        """Angaben eines Einsatzes ergänzen (z. B. im LifeHub abgehakt) oder „Rechnung geschrieben“ setzen."""
        conn = get_db_connection()
        try:
            _tabellen(conn)
            alt = conn.execute("SELECT * FROM fenster_einsatz WHERE id = ? AND storniert_am IS NULL", (eid,)).fetchone()
            if not alt:
                return dict({"ok": False, "fehler": "Einsatz nicht gefunden."}), 404
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
        return dict({"ok": True})

    def einsatz_rueckgaengig(eid):
        """„Erledigt“ zurücknehmen – nur der letzte Einsatz eines Kunden. Im LifeHub wird die abgehakte Aufgabe wieder geöffnet;
        sie ist der früheste offene Termin der Serie und damit wieder fällig. Die schon angelegte nächste Aufgabe bleibt offen
        stehen (nicht archivieren: sonst legt LifeHub nach dem nächsten „Erledigt“ keinen neuen Termin an). Im CRM bleibt der
        Einsatz als „storniert“ gespeichert und verschwindet aus den Listen. Der Google-Kalender bleibt unverändert."""
        conn = get_db_connection()
        try:
            _tabellen(conn)
            e = conn.execute("SELECT * FROM fenster_einsatz WHERE id = ? AND storniert_am IS NULL", (eid,)).fetchone()
            if not e:
                return dict({"ok": False, "fehler": "Einsatz nicht gefunden (schon zurückgenommen?)."}), 404
            letzter = conn.execute("SELECT id FROM fenster_einsatz WHERE plan_id = ? AND storniert_am IS NULL "
                                   "ORDER BY datum DESC, id DESC LIMIT 1", (e["plan_id"],)).fetchone()
            if letzter["id"] != eid:
                return dict({"ok": False, "fehler": "Nur der letzte erledigte Termin dieses Kunden kann zurückgenommen werden."}), 400
            p = plan_laden(conn, e["plan_id"])
            aufgabe = e["lifehub_aufgabe"]
            if aufgabe and p and p["aktiv"] and p["lifehub_serie"]:
                try:
                    t = ZUGANG.anfrage("GET", f"/tasks/{aufgabe}")
                    if t.get("recurrence_id") == p["lifehub_serie"] and t.get("status") == "done":
                        ZUGANG.anfrage("POST", f"/tasks/{aufgabe}/reopen")
                except LifeHubFehler as exc:
                    return dict({"ok": False, "fehler": f"LifeHub: {exc}"}), 503
            conn.execute("UPDATE fenster_einsatz SET storniert_am = ?, storniert_aufgabe = lifehub_aufgabe, lifehub_aufgabe = NULL "
                         "WHERE id = ?", (_jetzt(), eid))
            conn.commit()
            # wieder offen → wieder die normale Farbe (LifeHub und Google-Kalender)
            if aufgabe:
                try:
                    lh_faerben(aufgabe, LH_FARBE)
                except LifeHubFehler as exc:
                    print(f"Fensterreinigung: LifeHub-Farbe nicht zurückgesetzt ({exc})")
            if e["google_gruen"] and e["google_gruen"] != "fehlt" and KALENDER_DIENST is not None and _datum(e["datum"]):
                try:
                    g_einsaetze_faerben([(e["plan_id"], _datum(e["datum"]))], gruen=False)
                except GoogleFehler as exc:
                    print(f"Fensterreinigung: Termin-Farbe nicht zurückgesetzt ({exc})")
        finally:
            conn.close()
        return dict({"ok": True})

    # ---- Routen der Seite (gleiche Funktionen wie für ChatGPT)
    def _http(r):
        antwort, status = r if isinstance(r, tuple) else (r, 200)
        return jsonify(antwort), status

    @app.route("/api/fenster/kunden")
    @login_required
    def fenster_kunden():
        """Für die Kunden-Seite (Kachel „Fensterreinigung“): aktive Pläne, nur aus der Datenbank (schnell, ohne LifeHub)."""
        conn = get_db_connection()
        try:
            _tabellen(conn)
            zeilen = conn.execute("SELECT * FROM fenster_plan WHERE aktiv = 1 ORDER BY firma COLLATE NOCASE").fetchall()
            wahl = conn.execute("SELECT kunde_id, fenster FROM fenster_kunde ORDER BY kunde_id").fetchall()
        finally:
            conn.close()
        return jsonify({"ok": True, "fenster_ja": [z[0] for z in wahl if z[1]], "fenster_nein": [z[0] for z in wahl if not z[1]], "plaene": [{"id": z["id"], "kunde_id": z["kunde_id"], "firma": z["firma"], "betrag": z["betrag"] or 0,
                                                "im_jahr": einsaetze_im_jahr(_r_aus_zeile(z)), "rhythmus_text": beschreibung(_r_aus_zeile(z))}
                                               for z in zeilen]})

    @app.route("/api/fenster/kunde-fenster", methods=["POST"])
    @login_required
    def fenster_kunde_wahl():
        """Knopf „Fenster ja / nein“ auf der Kundenkarte. Nur diese Markierung ändert sich – der Kunde selbst bleibt, wie er ist."""
        e = request.get_json(silent=True) or {}
        kid = _int(e.get("kunde_id"), 1, 10 ** 9)
        if not kid:
            return jsonify({"ok": False, "fehler": "Kunde fehlt."}), 400
        conn = get_db_connection()
        try:
            _tabellen(conn)
            k = conn.execute("SELECT id, firma FROM kunden WHERE id = ?", (kid,)).fetchone()
            if not k:
                return jsonify({"ok": False, "fehler": "Kunde nicht gefunden."}), 404
            _fenster_markieren(conn, kid, k["firma"], bool(e.get("fenster")))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"ok": True, "fenster": bool(e.get("fenster"))})

    @app.route("/api/fenster/stammdaten")
    @login_required
    def fenster_stammdaten():
        return _http(stammdaten_daten())

    @app.route("/api/fenster/uebersicht")
    @login_required
    def fenster_uebersicht():
        return _http(uebersicht_daten())

    @app.route("/api/fenster/plan", methods=["POST"])
    @login_required
    def fenster_plan_speichern():
        return _http(plan_speichern(request.get_json(silent=True) or {}))

    @app.route("/api/fenster/plan/<int:pid>/pause", methods=["POST"])
    @login_required
    def fenster_plan_pause(pid):
        return _http(plan_pause(pid, bool((request.get_json(silent=True) or {}).get("fortsetzen"))))

    @app.route("/api/fenster/plan/<int:pid>/erledigt", methods=["POST"])
    @login_required
    def fenster_erledigt(pid):
        return _http(plan_erledigt(pid, request.get_json(silent=True) or {}))

    @app.route("/api/fenster/einsatz/<int:eid>", methods=["POST"])
    @login_required
    def fenster_einsatz_aendern(eid):
        return _http(einsatz_aendern(eid, request.get_json(silent=True) or {}))

    @app.route("/api/fenster/einsatz/<int:eid>/rueckgaengig", methods=["POST"])
    @login_required
    def fenster_einsatz_rueckgaengig(eid):
        return _http(einsatz_rueckgaengig(eid))

    FUNKTIONEN.update(stammdaten=stammdaten_daten, uebersicht=uebersicht_daten, speichern=plan_speichern, pause=plan_pause,
                      erledigt=plan_erledigt, einsatz=einsatz_aendern, rueckgaengig=einsatz_rueckgaengig, vorschau=lambda r, start: termine(r, start, start + timedelta(days=800))[:6])
