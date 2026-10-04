# =====================================================
# KAMPAGNEN-BERICHT (Leon Reinigung)
# Sobald eine Leon-Kampagne fertig ist (niemand wartet mehr), entsteht
# automatisch ein Bericht und eine To-Do-Karte „Kampagne … fertig“ – auf
# Wunsch auch eine kurze WhatsApp an den Chef (siehe kg_meldungen.py).
#
# - Gleicher Bericht wie in KG Business (Vorlage _kampagnen_bericht_inhalt.html).
# - Daten kommen aus dem Leon-Motor (nur lesen); Kampagnen werden nicht verändert.
# - Jeder Lauf einer Kampagne bekommt einen eigenen Bericht (Auto-Kampagne: je Tag).
# - Erster Start: alte Ergebnisse erzeugen keine Berichte („Bericht erstellen“
#   fasst eine ganze Kampagne auf Knopfdruck zusammen).
# - Leon Reinigung: Ergebnis „Rechnung“ bedeutet Besichtigung → zählt als Termin.
# - Abschalten: KAMPAGNEN_BERICHT_LAUF=0.
# =====================================================
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timezone

from flask import jsonify, render_template, request

import kg_meldungen

try:
    from zoneinfo import ZoneInfo
    BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # pragma: no cover
    BERLIN = None

TAKT_SEKUNDEN = 600
OFFEN = ("Wartet", "Reserviert", "Läuft")
STUNDENZETTEL = (os.getenv("LEON_STZ_KAMPAGNE") or "Stundenzettel-Kontrolle").strip().lower()
TODO_QUELLE = "kampagnen-bericht"


# =====================================================
# Kern (gleich in KG CRM und KG Business)
# =====================================================

KATEGORIEN = [
    # schluessel, Bezeichnung, Symbol, Farbe
    ("termin", "Termin / Besichtigung", "📅", "#7c3aed"),
    ("heiss", "Interessiert", "🔥", "#ea580c"),
    ("rueckruf", "Rückruf", "📞", "#0284c7"),
    ("gesprochen", "Gesprochen", "💬", "#0f766e"),
    ("kein", "Kein Interesse", "✋", "#dc2626"),
    ("nicht", "Nicht erreicht", "📵", "#94a3b8"),
]
KATEGORIE_INFO = {k: {"name": n, "symbol": s, "farbe": f} for k, n, s, f in KATEGORIEN}
GESPROCHEN_STATUS = {"erledigt", "beendet"}


def kategorie(z):
    """Ergebnis einer Firma in eine der sechs Gruppen einordnen."""
    werte = [str(z.get(k) or "").strip().lower() for k in ("call_result", "lead_status", "cl_result")]
    if any("termin" in w or "besichtigung" in w for w in werte):
        return "termin"
    if any(w in ("interessiert", "qualifiziert", "rechnung", "angebot", "abschluss", "kunde") for w in werte):
        return "heiss"
    if any(w in ("rückruf", "rueckruf") for w in werte):
        return "rueckruf"
    if int(z.get("do_not_call") or 0) or any(w in ("kein interesse", "gesperrt", "nicht anrufen") for w in werte):
        return "kein"
    if str(z.get("cl_status") or "").strip().lower() in GESPROCHEN_STATUS or int(z.get("dauer") or 0) >= 20:
        return "gesprochen"
    return "nicht"


def _berlin(text):
    """UTC-Zeit aus der Datenbank ('YYYY-MM-DD HH:MM:SS') → datetime in deutscher Zeit."""
    s = str(text or "").strip().replace("T", " ")[:19]
    if not s:
        return None
    try:
        d = datetime.strptime(s, "%Y-%m-%d %H:%M:%S") if len(s) > 16 else datetime.strptime(s[:16], "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    if BERLIN is None:
        return d
    return d.replace(tzinfo=timezone.utc).astimezone(BERLIN).replace(tzinfo=None)


TAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def zeit_text(text, utc=True):
    d = _berlin(text) if utc else None
    if d is None and not utc:
        try:
            d = datetime.fromisoformat(str(text or "").replace(" ", "T")[:19])
        except ValueError:
            d = None
    if d is None:
        return ""
    return f"{TAGE[d.weekday()]} {d.strftime('%d.%m. %H:%M')}"


def bericht_bauen(kampagne_id, kampagne_name, zeilen):
    """zeilen: je Firma ein dict (siehe _zeilen_laden) → fertiger Bericht (wird so gespeichert)."""
    firmen = []
    for z in zeilen:
        kat = kategorie(z)
        firmen.append({
            "lead_id": z.get("lead_id"),
            "firma": z.get("firma") or "Unbekannte Firma",
            "ansprechpartner": z.get("ansprechpartner") or "",
            "telefon": z.get("telefon") or "",
            "stadt": " ".join(x for x in (str(z.get("plz") or "").strip(), str(z.get("stadt") or "").strip()) if x),
            "kategorie": kat,
            "ergebnis": z.get("call_result") or z.get("lead_status") or z.get("cl_result") or z.get("cl_status") or "",
            "versuche": int(z.get("versuche") or 0),
            "zeit": zeit_text(z.get("finished_at") or z.get("attempted_at")),
            "call_id": z.get("call_id"),
            "dauer": int(z.get("dauer") or 0),
            "zusammenfassung": str(z.get("summary") or "").strip()[:700],
            "rueckruf_am": zeit_text(z.get("callback_at"), utc=False) if z.get("callback_at") else "",
            "rueckruf_iso": str(z.get("callback_at") or ""),
            "punkte": z.get("ls_punkte"),
        })
    zahl = {k: sum(1 for f in firmen if f["kategorie"] == k) for k, *_ in KATEGORIEN}
    erreicht = zahl["termin"] + zahl["heiss"] + zahl["rueckruf"] + zahl["gesprochen"] + zahl["kein"]
    positiv = zahl["termin"] + zahl["heiss"] + zahl["rueckruf"]
    angerufen = sum(1 for f in firmen if f["versuche"] or f["call_id"] or f["kategorie"] != "nicht")
    zeiten = [z.get("attempted_at") for z in zeilen if z.get("attempted_at")] + \
             [z.get("finished_at") for z in zeilen if z.get("finished_at")]
    rang = {"termin": 0, "heiss": 1, "rueckruf": 2, "gesprochen": 3, "kein": 4, "nicht": 5}
    firmen.sort(key=lambda f: (rang[f["kategorie"]], f["rueckruf_iso"] or "9999", f["firma"].lower()))
    return {
        "kampagne_id": kampagne_id,
        "kampagne": kampagne_name,
        "von": zeit_text(min(zeiten)) if zeiten else "",
        "bis": zeit_text(max(zeiten)) if zeiten else "",
        "zahlen": dict(zahl, firmen=len(firmen), angerufen=angerufen, erreicht=erreicht, positiv=positiv,
                       versuche=sum(f["versuche"] for f in firmen),
                       minuten=round(sum(f["dauer"] for f in firmen) / 60)),
        "quote_erreicht": round(100 * erreicht / angerufen) if angerufen else 0,
        "quote_positiv": round(100 * positiv / erreicht) if erreicht else 0,
        "firmen": firmen,
    }


def titel_text(b):
    z = b["zahlen"]
    teile = [f"{z['angerufen']} angerufen"]
    if z["heiss"]:
        teile.append(f"{z['heiss']} interessiert")
    if z["termin"]:
        teile.append(f"{z['termin']} Termin")
    if z["rueckruf"]:
        teile.append(f"{z['rueckruf']} Rückruf")
    return f"📊 Kampagne „{b['kampagne']}“ fertig: " + " · ".join(teile)


def kurz_text(b, link=""):
    """Kurzfassung für To-Do-Notiz und WhatsApp."""
    z = b["zahlen"]
    zeilen = [f"📊 Kampagne „{b['kampagne']}“ ist fertig ({b['von']} – {b['bis']})",
              f"{z['angerufen']} angerufen · {z['erreicht']} erreicht ({b['quote_erreicht']} %)",
              f"🔥 {z['heiss']} interessiert · 📅 {z['termin']} Termin · 📞 {z['rueckruf']} Rückruf · ✋ {z['kein']} kein Interesse"]
    wichtig = [f for f in b["firmen"] if f["kategorie"] in ("termin", "heiss", "rueckruf")][:5]
    if wichtig:
        zeilen.append("Jetzt wichtig:")
        for f in wichtig:
            info = KATEGORIE_INFO[f["kategorie"]]
            extra = f" ({f['rueckruf_am']})" if f["kategorie"] == "rueckruf" and f["rueckruf_am"] else ""
            zeilen.append(f"{info['symbol']} {f['firma']}{extra}" + (f" – {f['telefon']}" if f["telefon"] else ""))
    if link:
        zeilen.append(f"Bericht: {link}")
    return "\n".join(zeilen)


def ensure_bericht_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kampagnen_berichte (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kampagne_id INTEGER NOT NULL,
            kampagne_name TEXT,
            bis_marke TEXT,
            art TEXT NOT NULL DEFAULT 'auto',
            daten_json TEXT NOT NULL,
            erledigt_json TEXT NOT NULL DEFAULT '[]',
            erstellt_am TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kampagnen_bericht_stand (
            kampagne_id INTEGER PRIMARY KEY,
            bis_marke TEXT NOT NULL DEFAULT ''
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kampagnen_bericht_takt (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            gestartet INTEGER NOT NULL DEFAULT 0,
            sperre_bis REAL NOT NULL DEFAULT 0
        )
    """)
    conn.execute("INSERT OR IGNORE INTO kampagnen_bericht_takt (id) VALUES (1)")
    conn.commit()


def bericht_laden(conn, bericht_id):
    row = conn.execute("SELECT * FROM kampagnen_berichte WHERE id = ?", (bericht_id,)).fetchone()
    if not row:
        return None
    b = json.loads(row["daten_json"])
    b.update(id=row["id"], erstellt=zeit_text(row["erstellt_am"]), art=row["art"])
    try:
        erledigt = set(json.loads(row["erledigt_json"] or "[]"))
    except ValueError:
        erledigt = set()
    for f in b["firmen"]:
        f["erledigt"] = f["lead_id"] in erledigt
    b["offen"] = sum(1 for f in b["firmen"] if f["kategorie"] in ("termin", "heiss", "rueckruf") and not f["erledigt"])
    return b


def berichte_liste(conn, anzahl=60):
    out = []
    for row in conn.execute("SELECT id FROM kampagnen_berichte ORDER BY id DESC LIMIT ?", (anzahl,)).fetchall():
        b = bericht_laden(conn, row["id"])
        b.pop("firmen", None)
        out.append(b)
    return out


def erledigt_setzen(conn, bericht_id, lead_id, erledigt):
    row = conn.execute("SELECT erledigt_json FROM kampagnen_berichte WHERE id = ?", (bericht_id,)).fetchone()
    if not row:
        return None
    try:
        menge = set(json.loads(row["erledigt_json"] or "[]"))
    except ValueError:
        menge = set()
    (menge.add if erledigt else menge.discard)(lead_id)
    conn.execute("UPDATE kampagnen_berichte SET erledigt_json = ? WHERE id = ?", (json.dumps(sorted(menge)), bericht_id))
    conn.commit()
    return bericht_laden(conn, bericht_id)


# =====================================================
# KG CRM: Daten aus dem Leon-Motor
# =====================================================

def _crm_tabellen(conn):
    ensure_bericht_tables(conn)
    if "verarbeitet" not in {r[1] for r in conn.execute("PRAGMA table_info(kampagnen_bericht_stand)")}:
        try:
            conn.execute("ALTER TABLE kampagnen_bericht_stand ADD COLUMN verarbeitet INTEGER NOT NULL DEFAULT -1")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # ein anderer Prozess war schneller


def _utc_jetzt():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _zeilen_laden(conn, client, kampagne_id, seit=""):
    _code, data = client.request("GET", f"/api/campaigns/{kampagne_id}/leads", timeout=60)
    fertig = [l for l in (data or {}).get("selected_leads", [])
              if str(l.get("finished_at") or "") > (seit or "") and (l.get("campaign_status") or "") not in OFFEN]
    if not fertig:
        return []
    _code, anrufe = client.request("GET", "/api/calls", timeout=90)
    letzter = {}
    for a in (anrufe or {}).get("calls", []):
        if int(a.get("campaign_id") or 0) == int(kampagne_id):
            lid = int(a.get("lead_id") or 0)
            if lid and int(a.get("id") or 0) > int((letzter.get(lid) or {}).get("id") or 0):
                letzter[lid] = a
    ergebnis_crm = {}
    try:
        for r in conn.execute("SELECT leon_lead_id, letztes_ergebnis FROM leon_links WHERE leon_lead_id IS NOT NULL"):
            if r[1]:
                ergebnis_crm[int(r[0])] = r[1]
    except sqlite3.OperationalError:
        pass  # noch nie etwas an Leon übergeben
    zeilen = []
    for l in fertig:
        lid = int(l.get("lead_id") or 0)
        a = letzter.get(lid) or {}
        ergebnis = a.get("result") or ""
        if ergebnis == "Rechnung":  # Leon Reinigung: „Rechnung“ = Besichtigung vereinbart
            ergebnis = "Besichtigung"
        rueckruf = l.get("callback_at") or a.get("callback_at")
        zeilen.append({
            "lead_id": lid, "cl_status": l.get("campaign_status"), "cl_result": l.get("last_result"),
            "versuche": l.get("attempt_count"), "attempted_at": l.get("attempted_at"), "finished_at": l.get("finished_at"),
            "firma": l.get("firma"), "ansprechpartner": l.get("ansprechpartner"), "telefon": l.get("telefon"),
            "plz": "", "stadt": l.get("stadt"),
            "lead_status": ergebnis_crm.get(lid) or ("Rückruf" if rueckruf and not int(l.get("do_not_call") or 0) else l.get("last_call_result")),
            "do_not_call": l.get("do_not_call"), "callback_at": rueckruf,
            "call_id": a.get("id"), "call_result": ergebnis, "summary": a.get("summary"), "dauer": a.get("duration_seconds"),
        })
    return zeilen


def bericht_erstellen(conn, client, kampagne, art="auto", seit=""):
    zeilen = _zeilen_laden(conn, client, kampagne["id"], seit)
    if not zeilen:
        return None
    b = bericht_bauen(kampagne["id"], kampagne.get("name") or f"Kampagne {kampagne['id']}", zeilen)
    marke = max(str(z["finished_at"] or "") for z in zeilen)
    cur = conn.execute(
        "INSERT INTO kampagnen_berichte (kampagne_id, kampagne_name, bis_marke, art, daten_json, erstellt_am) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (kampagne["id"], b["kampagne"], marke, art, json.dumps(b, ensure_ascii=False), _utc_jetzt()))
    bericht_id = cur.lastrowid
    conn.commit()
    pfad = f"/leon/bericht/{bericht_id}"
    kg_meldungen.todo_anlegen(conn, titel_text(b), kurz_text(b), pfad, TODO_QUELLE,
                              "hoch" if b["zahlen"]["positiv"] else "normal")
    if art == "auto" and kg_meldungen.einstellungen(conn).get("bericht_whatsapp"):
        kg_meldungen.whatsapp_an_chef(conn, kurz_text(b, kg_meldungen.link(conn, pfad)), "kampagnen_bericht")
    return bericht_id


def fertige_pruefen(conn, client):
    """Ein Durchgang: für jede fertige Kampagne mit neuen Ergebnissen einen Bericht anlegen."""
    _crm_tabellen(conn)
    _code, data = client.request("GET", "/api/campaigns", timeout=30)
    kampagnen = [k for k in (data or {}).get("campaigns", [])
                 if (k.get("name") or "").strip().lower() != STUNDENZETTEL and (k.get("status") or "") != "Entwurf"]
    takt = conn.execute("SELECT gestartet FROM kampagnen_bericht_takt WHERE id = 1").fetchone()
    if not takt[0]:
        # Allererster Start: bisherige Ergebnisse gelten als bekannt (keine Flut alter Berichte)
        jetzt = _utc_jetzt()
        for k in kampagnen:
            conn.execute("INSERT OR IGNORE INTO kampagnen_bericht_stand (kampagne_id, bis_marke, verarbeitet) VALUES (?, ?, ?)",
                         (k["id"], jetzt, int(k.get("processed_count") or 0)))
        conn.execute("UPDATE kampagnen_bericht_takt SET gestartet = 1 WHERE id = 1")
        conn.commit()
        return []
    neu = []
    for k in kampagnen:
        offen = sum(int(k.get(f) or 0) for f in ("waiting_count", "running_count", "retry_waiting_count"))
        if offen:
            continue
        stand = conn.execute("SELECT bis_marke, verarbeitet FROM kampagnen_bericht_stand WHERE kampagne_id = ?", (k["id"],)).fetchone()
        verarbeitet = int(k.get("processed_count") or 0)
        if stand and int(stand[1]) == verarbeitet:
            continue  # seit dem letzten Blick nichts Neues
        bericht_id = bericht_erstellen(conn, client, k, "auto", stand[0] if stand else "")
        if bericht_id:
            neu.append(bericht_id)
            marke = conn.execute("SELECT bis_marke FROM kampagnen_berichte WHERE id = ?", (bericht_id,)).fetchone()[0]
        else:
            marke = stand[0] if stand else ""
        conn.execute("INSERT INTO kampagnen_bericht_stand (kampagne_id, bis_marke, verarbeitet) VALUES (?, ?, ?) "
                     "ON CONFLICT(kampagne_id) DO UPDATE SET bis_marke = excluded.bis_marke, verarbeitet = excluded.verarbeitet",
                     (k["id"], marke, verarbeitet))
        conn.commit()
    return neu


def durchgang(conn, client):
    """Mit Sperre (nie zwei gleichzeitig, auch bei mehreren Prozessen)."""
    _crm_tabellen(conn)
    jetzt = time.time()
    cur = conn.execute("UPDATE kampagnen_bericht_takt SET sperre_bis = ? WHERE id = 1 AND sperre_bis < ?", (jetzt + 300, jetzt))
    conn.commit()
    if cur.rowcount != 1:
        return []
    try:
        return fertige_pruefen(conn, client)
    finally:
        conn.execute("UPDATE kampagnen_bericht_takt SET sperre_bis = 0 WHERE id = 1")
        conn.commit()


_gestartet = False
_start_lock = threading.Lock()


def _starten(get_db_connection, leon_client_factory):
    global _gestartet
    if os.getenv("KAMPAGNEN_BERICHT_LAUF", "1").strip() == "0":
        return
    with _start_lock:
        if _gestartet:
            return
        _gestartet = True

    def schleife():
        time.sleep(75)
        while True:
            conn = None
            try:
                client = leon_client_factory()
                if client is not None and (not hasattr(client, "configured") or client.configured()):
                    conn = get_db_connection()
                    for bid in durchgang(conn, client):
                        print("[KAMPAGNEN-BERICHT] neuer Bericht", bid)
            except Exception as exc:  # darf das CRM nie stören
                print("[KAMPAGNEN-BERICHT] Fehler:", exc)
            finally:
                if conn is not None:
                    conn.close()
            time.sleep(TAKT_SEKUNDEN)

    threading.Thread(target=schleife, daemon=True, name="kg-kampagnen-bericht").start()


# =====================================================
# Routen
# =====================================================

def register_kampagnen_bericht(app, login_required, get_db_connection, leon_client_factory):

    def _conn():
        conn = get_db_connection()
        _crm_tabellen(conn)
        return conn

    def _tabs():
        from leon_routes import LEON_TABS
        return LEON_TABS

    @app.route("/leon/berichte")
    @login_required
    def kampagnen_berichte_seite():
        conn = _conn()
        try:
            e = kg_meldungen.einstellungen(conn)
        finally:
            conn.close()
        return render_template("kampagnen_berichte.html", leon_tab="berichte", leon_tabs=_tabs(), meldungen=e)

    @app.route("/leon/bericht/<int:bericht_id>")
    @login_required
    def kampagnen_bericht_seite(bericht_id):
        conn = _conn()
        try:
            b = bericht_laden(conn, bericht_id)
        finally:
            conn.close()
        if not b:
            return render_template("kampagnen_berichte.html", leon_tab="berichte", leon_tabs=_tabs(),
                                   fehler="Bericht nicht gefunden."), 404
        return render_template("kampagnen_bericht.html", leon_tab="berichte", leon_tabs=_tabs(), b=b,
                               kategorien=KATEGORIEN, info=KATEGORIE_INFO,
                               gespraech_link="/leon/gespraech/", aufnahme_link="/leon-api/calls/{id}/recording")

    @app.route("/api/kampagnen-berichte")
    @login_required
    def kampagnen_berichte_api():
        conn = _conn()
        try:
            kampagnen = []
            try:
                _code, data = leon_client_factory().request("GET", "/api/campaigns", timeout=30)
                for k in (data or {}).get("campaigns", []):
                    if (k.get("name") or "").strip().lower() != STUNDENZETTEL:
                        offen = sum(int(k.get(f) or 0) for f in ("waiting_count", "running_count", "retry_waiting_count"))
                        kampagnen.append({"id": k["id"], "name": k.get("name"), "status": k.get("status"),
                                          "offen": offen, "fertig": int(k.get("processed_count") or 0)})
            except Exception:
                pass  # Leon nicht erreichbar – Berichte trotzdem zeigen
            return jsonify({"success": True, "berichte": berichte_liste(conn), "kampagnen": kampagnen})
        finally:
            conn.close()

    @app.route("/api/kampagnen-berichte/erstellen", methods=["POST"])
    @login_required
    def kampagnen_bericht_erstellen_api():
        try:
            kampagne_id = int((request.get_json(silent=True) or {}).get("kampagne_id") or 0)
        except (TypeError, ValueError):
            kampagne_id = 0
        conn = _conn()
        try:
            client = leon_client_factory()
            _code, data = client.request("GET", "/api/campaigns", timeout=30)
            k = next((x for x in (data or {}).get("campaigns", []) if int(x.get("id") or 0) == kampagne_id), None)
            if not k:
                return jsonify({"success": False, "error": "Kampagne nicht gefunden."}), 404
            bericht_id = bericht_erstellen(conn, client, k, "manuell", "")
            if not bericht_id:
                return jsonify({"success": False, "error": "In dieser Kampagne ist noch keine Firma fertig angerufen."}), 400
            return jsonify({"success": True, "id": bericht_id})
        except Exception as exc:
            return jsonify({"success": False, "error": str(exc)}), 502
        finally:
            conn.close()

    @app.route("/api/kampagnen-bericht/<int:bericht_id>/erledigt", methods=["POST"])
    @login_required
    def kampagnen_bericht_erledigt_api(bericht_id):
        d = request.get_json(silent=True) or {}
        try:
            lead_id = int(d.get("lead_id"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "lead_id fehlt"}), 400
        conn = _conn()
        try:
            b = erledigt_setzen(conn, bericht_id, lead_id, bool(d.get("erledigt")))
            if not b:
                return jsonify({"success": False, "error": "Bericht nicht gefunden."}), 404
            # alles Wichtige erledigt → To-Do-Karte abhaken (und wieder öffnen, wenn doch nicht)
            kg_meldungen.todo_erledigt(conn, TODO_QUELLE, f"/leon/bericht/{bericht_id}", b["offen"] == 0)
            return jsonify({"success": True, "offen": b["offen"]})
        finally:
            conn.close()

    @app.route("/api/kg-meldungen", methods=["GET", "POST"])
    @login_required
    def kg_meldungen_api():
        conn = _conn()
        try:
            if request.method == "POST":
                try:
                    kg_meldungen.speichern(conn, request.get_json(silent=True) or {}, request.url_root)
                except ValueError as exc:
                    return jsonify({"success": False, "error": str(exc)}), 400
            return jsonify({"success": True, "einstellungen": kg_meldungen.einstellungen(conn)})
        finally:
            conn.close()

    @app.route("/api/kg-meldungen/test", methods=["POST"])
    @login_required
    def kg_meldungen_test():
        conn = _conn()
        try:
            ok = kg_meldungen.whatsapp_an_chef(conn, "✅ Test vom KG CRM: Kampagnen-Berichte und die Arbeitsliste kommen ab jetzt hierher.", "kg_meldung_test")
            if not ok:
                return jsonify({"success": False, "error": "Keine WhatsApp-Nummer gespeichert."}), 400
            return jsonify({"success": True})
        finally:
            conn.close()

    _starten(get_db_connection, leon_client_factory)
