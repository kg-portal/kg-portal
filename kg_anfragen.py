# =====================================================
# NEUE ANFRAGE + „→ Interesse“ (KG Gebäudereinigung)
#
# - Neue Anfrage (Leon-Reiter): Firma ruft selbst an → Firma erfassen (Datenbank-Format), Dubletten-Warnung
#   (Datenbank, Tagesliste, Bearbeitete, Kunden, frühere Anfragen), danach „Portfolio senden“ oder
#   „Besichtigung eintragen + bestätigen“ über die vorhandenen Fenster (KG-Mail, KG-Kalender).
#   Eintrag in die Datenbank (Tagesliste, Status „Hat Interesse“) über die vorhandenen Routen im Browser.
# - „→ Interesse“ (Leon › Anrufe): ein Gespräch von Hand zur Seite „Interesse“ holen.
#   Leons Ergebnis und Bewertung bleiben unverändert (eigene Tabelle im CRM).
# =====================================================

import difflib
import json
from datetime import datetime, timezone

from flask import jsonify, render_template, request

from lead_kern import domain, firma_norm, ort_norm, strasse_norm, telefon_norm


def _clean(value, limit=300):
    return str(value or "").strip()[:limit]


def ensure_anfragen_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neue_anfragen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma TEXT NOT NULL,
            branche TEXT,
            anrede TEXT,
            ansprechpartner TEXT,
            strasse TEXT,
            plz TEXT,
            ort TEXT,
            telefon TEXT,
            email TEXT,
            website TEXT,
            notiz TEXT,
            tagesliste_id INTEGER,
            portfolio_am TEXT,
            portfolio_an TEXT,
            termin_text TEXT,
            termin_am TEXT,
            terminmail_am TEXT,
            terminmail_an TEXT,
            erstellt_am TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS interesse_manuell (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id INTEGER NOT NULL UNIQUE,
            lead_id INTEGER,
            daten_json TEXT NOT NULL,
            erstellt_am TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def kontakt_mit_anrede(anrede, name):
    """„Herr Müller“ – so erkennt das KG-Mail-Fenster die passende Anrede."""
    anrede, name = _clean(anrede), _clean(name)
    if anrede in ("Herr", "Frau") and name and not name.lower().startswith(anrede.lower() + " "):
        return f"{anrede} {name}"
    return name


# ---------------- Dubletten ----------------

def _tabelle_da(conn, name):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _spalten(conn, tabelle):
    return {r[1] for r in conn.execute(f'PRAGMA table_info("{tabelle}")')}


def _kandidaten(conn):
    """Alle Firmen aus Datenbank, Tagesliste, bearbeiteten Tagesliste-Einträgen, Kunden und früheren Anfragen."""
    quellen = [
        ("leads", "Datenbank", "stadt", "status"),
        ("tagesliste_leads", "Tagesliste", "ort", "status"),
        ("tagesliste_status_backup", "Datenbank (bearbeitet)", "ort", "status"),
        ("kunden", "Kunde", "ort", None),
        ("neue_anfragen", "Neue Anfrage", "ort", None),
    ]
    for tabelle, wo, ort_spalte, status_spalte in quellen:
        if not _tabelle_da(conn, tabelle):
            continue
        da = _spalten(conn, tabelle)
        felder = ["id", "firma"] + [s for s in ("telefon", "email", "website", "strasse", "plz") if s in da]
        felder += [f"{ort_spalte} AS ort"] if ort_spalte in da else []
        felder += [f"{status_spalte} AS status"] if status_spalte and status_spalte in da else []
        for r in conn.execute(f"SELECT {', '.join(felder)} FROM {tabelle}"):
            yield wo, dict(r)


def dubletten(conn, f, limit=6):
    """→ Liste {wo, id, firma, ort, status, grund, sicher} – ähnlich wie die Prüfung beim Import (lead_kern)."""
    n = firma_norm(f.get("firma"))
    t = telefon_norm(f.get("telefon"))
    m = _clean(f.get("email")).lower()
    d = domain(f.get("website"), f.get("email"))
    o = ort_norm(f.get("ort"))
    p = "".join(ch for ch in _clean(f.get("plz")) if ch.isdigit())
    s = strasse_norm(f.get("strasse"))
    treffer, gesehen = [], set()
    for wo, r in _kandidaten(conn):
        rn = firma_norm(r.get("firma"))
        grund, sicher = "", True
        if t and telefon_norm(r.get("telefon")) == t:
            grund = "gleiche Telefonnummer"
        elif m and _clean(r.get("email")).lower() == m:
            grund = "gleiche E-Mail"
        elif d and domain(r.get("website"), r.get("email")) == d:
            grund = "gleiche Webseite/Domain"
        elif n and rn == n:
            r_plz = "".join(ch for ch in _clean(r.get("plz")) if ch.isdigit())
            if (o and ort_norm(r.get("ort")) == o) or (p and r_plz == p):
                grund = "gleiche Firma am gleichen Ort"
            elif s and strasse_norm(r.get("strasse")) == s:
                grund = "gleiche Firma, gleiche Straße"
            else:
                grund, sicher = "gleicher Firmenname, anderer Ort", False
        elif n and rn and len(n) >= 6 and difflib.SequenceMatcher(None, n, rn).ratio() >= 0.9:
            if not o or ort_norm(r.get("ort")) == o:
                grund, sicher = "sehr ähnlicher Firmenname", False
        if not grund:
            continue
        schluessel = (wo, r.get("id"))
        if schluessel in gesehen:
            continue
        gesehen.add(schluessel)
        treffer.append({"wo": wo, "id": r.get("id"), "firma": _clean(r.get("firma")), "ort": _clean(r.get("ort")),
                        "status": _clean(r.get("status")), "grund": grund, "sicher": sicher})
    treffer.sort(key=lambda x: (not x["sicher"], x["wo"] != "Kunde"))
    return treffer[:limit]


# ---------------- Einträge für „Interesse“ ----------------

def anfrage_aktionen(a):
    liste = []
    if a.get("portfolio_am"):
        liste.append({"art": "email", "ok": True, "text": f"Portfolio per E-Mail an {a.get('portfolio_an') or '?'}"})
    if a.get("termin_am"):
        liste.append({"art": "besichtigung", "ok": True, "text": f"Besichtigung im Kalender: {a.get('termin_text') or ''}".strip()})
    if a.get("terminmail_am"):
        liste.append({"art": "email", "ok": True, "text": f"Terminbestätigung an {a.get('terminmail_an') or '?'}"})
    return liste


def anfrage_als_zeile(a):
    """Neue Anfrage im Format einer Leon-Zeile (für die Aufbereitung auf der Seite „Interesse“)."""
    return {
        "firma": a.get("firma"), "branche": a.get("branche"),
        "ansprechpartner": kontakt_mit_anrede(a.get("anrede"), a.get("ansprechpartner")),
        "strasse": a.get("strasse"), "plz": a.get("plz"), "stadt": a.get("ort"),
        "telefon": a.get("telefon"), "call_email": a.get("email"), "website": a.get("website"),
        "summary": a.get("notiz"), "created_at": a.get("erstellt_am"), "result": "Interessiert",
        "aktionen": anfrage_aktionen(a), "reinigung_json": "",
    }


def interesse_zusatz(conn):
    """Zusätzliche Einträge für „Interesse“: von Hand geholte Gespräche und Neue Anfragen."""
    ensure_anfragen_tables(conn)
    manuell = []
    for r in conn.execute("SELECT call_id, lead_id, daten_json, erstellt_am FROM interesse_manuell ORDER BY id DESC"):
        try:
            daten = json.loads(r["daten_json"] or "{}")
        except Exception:
            daten = {}
        manuell.append({**daten, "call_id": r["call_id"], "lead_id": r["lead_id"], "_hinzugefuegt_am": r["erstellt_am"]})
    anfragen = [dict(r) for r in conn.execute("SELECT * FROM neue_anfragen ORDER BY id DESC")]
    return manuell, anfragen


def anruf_schnappschuss(call):
    """Was von einem Leon-Anruf für „Interesse“ gespeichert wird (Leon selbst bleibt unverändert)."""
    felder = ("firma", "ansprechpartner", "phone_number", "summary", "score", "reinigung_json", "created_at",
              "status", "result", "duration_seconds")
    daten = {k: call.get(k) for k in felder}
    daten["call_email"] = call.get("captured_email") or ""
    daten["lead_email"] = call.get("lead_email") or ""
    daten["telefon"] = call.get("lead_telefon") or ""
    return daten


# ---------------- Routen ----------------

ANFRAGE_FELDER = ("firma", "branche", "anrede", "ansprechpartner", "strasse", "plz", "ort", "telefon", "email",
                  "website", "notiz")


def register_kg_anfragen(app, login_required, get_db_connection, leon_client, LeonError, leon_tabs):

    conn = get_db_connection()
    try:
        ensure_anfragen_tables(conn)
    finally:
        conn.close()

    def _formular():
        daten = request.get_json(silent=True) or {}
        f = {k: _clean(daten.get(k), 2000 if k == "notiz" else 300) for k in ANFRAGE_FELDER}
        if f["anrede"] not in ("Herr", "Frau"):
            f["anrede"] = ""
        return f, daten

    @app.route("/leon/anfrage")
    @login_required
    def kg_neue_anfrage_seite():
        conn = get_db_connection()
        try:
            ensure_anfragen_tables(conn)
            anfragen = [dict(r) for r in conn.execute("SELECT * FROM neue_anfragen ORDER BY id DESC LIMIT 100")]
        finally:
            conn.close()
        for a in anfragen:
            a["kontakt"] = kontakt_mit_anrede(a.get("anrede"), a.get("ansprechpartner"))
        try:
            neu = int(request.args.get("neu") or 0)
        except ValueError:
            neu = 0
        return render_template("neue_anfrage.html", leon_tab="anfrage", leon_tabs=leon_tabs, anfragen=anfragen, neu=neu)

    @app.route("/api/neue-anfrage/pruefen", methods=["POST"])
    @login_required
    def kg_neue_anfrage_pruefen():
        f, _ = _formular()
        conn = get_db_connection()
        try:
            ensure_anfragen_tables(conn)
            treffer = dubletten(conn, f)
        finally:
            conn.close()
        return jsonify({"success": True, "treffer": treffer})

    @app.route("/api/neue-anfrage", methods=["POST"])
    @login_required
    def kg_neue_anfrage_anlegen():
        f, daten = _formular()
        if not f["firma"]:
            return jsonify({"success": False, "error": "Bitte den Firmennamen eintragen."}), 400
        conn = get_db_connection()
        try:
            ensure_anfragen_tables(conn)
            treffer = dubletten(conn, f)
            if treffer and not daten.get("trotzdem"):
                return jsonify({"success": False, "dublette": True, "treffer": treffer}), 409
            cur = conn.execute(
                f"INSERT INTO neue_anfragen ({', '.join(ANFRAGE_FELDER)}) VALUES ({', '.join('?' * len(ANFRAGE_FELDER))})",
                [f[k] for k in ANFRAGE_FELDER],
            )
            conn.commit()
            neue_id = cur.lastrowid
        finally:
            conn.close()
        f["kontakt"] = kontakt_mit_anrede(f["anrede"], f["ansprechpartner"])
        return jsonify({"success": True, "id": neue_id, "anfrage": f})

    @app.route("/api/neue-anfrage/<int:anfrage_id>/tagesliste", methods=["POST"])
    @login_required
    def kg_neue_anfrage_tagesliste(anfrage_id):
        """Merkt sich den Tagesliste-Eintrag. Ohne ID (war schon in der Tagesliste): über denselben Schlüssel suchen."""
        daten = request.get_json(silent=True) or {}
        conn = get_db_connection()
        try:
            a = conn.execute("SELECT * FROM neue_anfragen WHERE id = ?", (anfrage_id,)).fetchone()
            if not a:
                return jsonify({"success": False, "error": "Anfrage nicht gefunden."}), 404
            try:
                tid = int(daten.get("tagesliste_id") or 0)
            except (TypeError, ValueError):
                tid = 0
            vorhanden = False
            if not tid and _tabelle_da(conn, "tagesliste_leads"):
                # gleicher Schlüssel wie /datenbank/tagesliste-add: firma|telefon|email
                key = f"{a['firma'] or ''}|{a['telefon'] or ''}|{a['email'] or ''}".lower()
                row = conn.execute("SELECT id FROM tagesliste_leads WHERE company_key = ?", (key,)).fetchone()
                tid, vorhanden = (row["id"] if row else 0), bool(row)
            if tid:
                conn.execute("UPDATE neue_anfragen SET tagesliste_id = ? WHERE id = ?", (tid, anfrage_id))
                conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True, "tagesliste_id": tid, "war_schon_da": vorhanden})

    @app.route("/api/neue-anfrage/<int:anfrage_id>/vermerk", methods=["POST"])
    @login_required
    def kg_neue_anfrage_vermerk(anfrage_id):
        """Vermerkt, was erledigt wurde: Portfolio verschickt, Besichtigung im Kalender, Terminbestätigung verschickt."""
        daten = request.get_json(silent=True) or {}
        art = _clean(daten.get("art"))
        jetzt = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")  # wie CURRENT_TIMESTAMP (UTC)
        spalten = {
            "portfolio": ("portfolio_am = ?, portfolio_an = ?", (jetzt, _clean(daten.get("an")))),
            "termin": ("termin_am = ?, termin_text = ?", (jetzt, _clean(daten.get("termin")))),
            "terminmail": ("terminmail_am = ?, terminmail_an = ?", (jetzt, _clean(daten.get("an")))),
        }
        if art not in spalten:
            return jsonify({"success": False, "error": "Unbekannter Vermerk."}), 400
        sql, werte = spalten[art]
        conn = get_db_connection()
        try:
            cur = conn.execute(f"UPDATE neue_anfragen SET {sql} WHERE id = ?", (*werte, anfrage_id))
            conn.commit()
        finally:
            conn.close()
        if not cur.rowcount:
            return jsonify({"success": False, "error": "Anfrage nicht gefunden."}), 404
        return jsonify({"success": True})

    @app.route("/api/interesse/manuell/ids")
    @login_required
    def kg_interesse_manuell_ids():
        conn = get_db_connection()
        try:
            ensure_anfragen_tables(conn)
            ids = [r["call_id"] for r in conn.execute("SELECT call_id FROM interesse_manuell")]
        finally:
            conn.close()
        return jsonify({"success": True, "ids": ids})

    @app.route("/api/interesse/manuell", methods=["POST"])
    @login_required
    def kg_interesse_manuell_hinzu():
        """Gespräch aus „Anrufe“ zu „Interesse“ holen – Leon (Ergebnis, Bewertung) bleibt unverändert."""
        daten = request.get_json(silent=True) or {}
        try:
            call_id = int(daten.get("call_id") or 0)
        except (TypeError, ValueError):
            call_id = 0
        if call_id <= 0:
            return jsonify({"success": False, "error": "Anruf fehlt."}), 400
        try:
            code, antwort = leon_client.request("GET", f"/api/calls/{call_id}", timeout=30)
        except LeonError as exc:
            return jsonify({"success": False, "error": str(exc)}), 502
        call = (antwort or {}).get("call") if isinstance(antwort, dict) else None
        if code != 200 or not call:
            fehler = (antwort.get("error") if isinstance(antwort, dict) else "") or f"HTTP {code}"
            return jsonify({"success": False, "error": fehler}), 404 if code == 404 else 502
        conn = get_db_connection()
        try:
            ensure_anfragen_tables(conn)
            cur = conn.execute(
                "INSERT OR IGNORE INTO interesse_manuell (call_id, lead_id, daten_json) VALUES (?, ?, ?)",
                (call_id, call.get("lead_id"), json.dumps(anruf_schnappschuss(call), ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True, "schon_da": cur.rowcount == 0, "firma": _clean(call.get("firma"))})
