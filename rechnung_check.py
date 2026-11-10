# Buchhaltung → Reiter „Rechnungen-Check“
# Je Monat: welche Kunden bekommen eine Rechnung, und ist sie schon gesendet?
# - Liste kommt aus den Kunden (Vertragsbeginn / Vertragsende / Status), nichts wird an Kunden geändert.
# - „Neu“: Vertrag beginnt in diesem Monat. „Letzter Monat“: gekündigt, Vertragsende in diesem Monat.
# - Einmalige und jährliche Kunden stehen nicht in der Liste (keine Monatsrechnung).
# - „Gesendet“ wird nur in der eigenen Tabelle rechnung_check gespeichert (Kunde + Monat), Rückgängig möglich.
import calendar
import datetime
import re

from flask import jsonify, request

MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember"]


def ensure_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rechnung_check (
            kunde_id INTEGER NOT NULL,
            monat TEXT NOT NULL,
            gesendet_am TEXT,
            geaendert_am TEXT,
            PRIMARY KEY (kunde_id, monat)
        )
    """)
    conn.commit()


def _datum(text):
    s = str(text or "").strip()
    if not s:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d.%m.%y"):
        try:
            return datetime.datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    return None


def _betrag(wert):
    if isinstance(wert, (int, float)):
        return float(wert)
    s = re.sub(r"[^0-9,.\-]", "", str(wert or ""))
    if not s:
        return 0.0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _monat_ok(monat):
    return bool(re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(monat or "")))


def _ohne_monatsrechnung(haeufigkeit):
    f = str(haeufigkeit or "").lower()
    return "einmal" in f or "jährlich" in f or "jaehrlich" in f


def monatsliste(conn, monat):
    jahr, mon = int(monat[:4]), int(monat[5:7])
    anfang = datetime.date(jahr, mon, 1)
    ende_monat = datetime.date(jahr, mon, calendar.monthrange(jahr, mon)[1])

    gesendet = {}
    for r in conn.execute("SELECT kunde_id, gesendet_am FROM rechnung_check WHERE monat = ? AND gesendet_am IS NOT NULL", (monat,)):
        gesendet[r["kunde_id"]] = r["gesendet_am"]

    kunden, ohne_ende = [], 0
    for r in conn.execute("SELECT id, firma, kundennummer, monat, vertrag_beginn, vertrag_ende, haeufigkeit, "
                          "vertragsstatus, created_at FROM kunden").fetchall():
        if _ohne_monatsrechnung(r["haeufigkeit"]):
            continue
        beginn = _datum(r["vertrag_beginn"])
        start = beginn or _datum(str(r["created_at"] or "")[:10]) or datetime.date.min
        gekuendigt = str(r["vertragsstatus"] or "").strip().lower() == "gekuendigt"
        ende = _datum(r["vertrag_ende"]) if gekuendigt else None
        if gekuendigt and ende is None:
            # Gekündigt ohne Vertragsende: unklar bis wann → nicht in der Liste, nur gezählt
            if start <= ende_monat:
                ohne_ende += 1
            continue
        if start > ende_monat or (ende is not None and ende < anfang):
            continue
        kunden.append({
            "id": r["id"],
            "firma": str(r["firma"] or "").strip() or "—",
            "kundennummer": str(r["kundennummer"] or "").strip(),
            "betrag": _betrag(r["monat"]),
            "haeufigkeit": str(r["haeufigkeit"] or "").strip(),
            "neu": bool(beginn and beginn.year == jahr and beginn.month == mon),
            "letzter_monat": bool(ende and anfang <= ende <= ende_monat),
            "vertrag_ende": ende.strftime("%d.%m.%Y") if ende else "",
            "gesendet_am": gesendet.get(r["id"]) or "",
        })

    # Noch nicht gesendete zuerst, dann nach Firmenname
    def _name(t):
        t = t.lower()
        for x, y in (("ä", "a"), ("ö", "o"), ("ü", "u"), ("ß", "ss")):
            t = t.replace(x, y)
        return t
    kunden.sort(key=lambda k: (1 if k["gesendet_am"] else 0, _name(k["firma"])))
    return {
        "monat": monat,
        "monat_name": f"{MONATE[mon - 1]} {jahr}",
        "kunden": kunden,
        "gesamt": len(kunden),
        "gesendet": sum(1 for k in kunden if k["gesendet_am"]),
        "ohne_ende": ohne_ende,
    }


def register_rechnung_check(app, login_required, get_db_connection):

    def _conn():
        conn = get_db_connection()
        ensure_tables(conn)
        return conn

    @app.route("/api/rechnung-check")
    @login_required
    def rechnung_check_liste():
        monat = request.args.get("monat", "")
        if not _monat_ok(monat):
            return jsonify({"success": False, "error": "Monat fehlt."}), 400
        conn = _conn()
        try:
            return jsonify({"success": True, **monatsliste(conn, monat)})
        finally:
            conn.close()

    @app.route("/api/rechnung-check/gesendet", methods=["POST"])
    @login_required
    def rechnung_check_gesendet():
        d = request.get_json(silent=True) or {}
        monat = str(d.get("monat") or "")
        try:
            kunde_id = int(d.get("kunde_id"))
        except (TypeError, ValueError):
            kunde_id = 0
        if not _monat_ok(monat) or kunde_id <= 0:
            return jsonify({"success": False, "error": "Angaben fehlen."}), 400
        jetzt = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        conn = _conn()
        try:
            if not conn.execute("SELECT 1 FROM kunden WHERE id = ?", (kunde_id,)).fetchone():
                return jsonify({"success": False, "error": "Kunde nicht gefunden."}), 404
            wert = jetzt if d.get("gesendet") else None
            conn.execute("""
                INSERT INTO rechnung_check (kunde_id, monat, gesendet_am, geaendert_am) VALUES (?, ?, ?, ?)
                ON CONFLICT(kunde_id, monat) DO UPDATE SET gesendet_am = excluded.gesendet_am, geaendert_am = excluded.geaendert_am
            """, (kunde_id, monat, wert, jetzt))
            conn.commit()
            return jsonify({"success": True, "gesendet_am": wert or ""})
        finally:
            conn.close()
