# =====================================================
# ChatGPT (KG Daten) → KG CRM: Firmen (leads) und Leon-Reinigung-Kampagnen-Entwürfe
#
# Eine interne Route mit Token (KG_MCP_TOKEN aus geheim/crm_env.json):
#   POST /internal/mcp/crm   {"aktion": …}
#     leads_suchen, lead_erstellen, leads_importieren (JSON oder CSV), lead_aktualisieren,
#     leads_kampagne (Entwurf anlegen oder in einen Entwurf legen), kampagne_pruefen
#     loeschen_vorschau → loeschen_bestaetigen (zwei Schritte, Papierkorb), papierkorb, wiederherstellen,
#     endgueltig_loeschen (nur mit KG_MCP_ENDGUELTIG=1 in crm_env.json + „ENDGÜLTIG LÖSCHEN“)
#
# - Nur KG CRM (Tabelle leads) und Leon Reinigung. KG Business wird nie angefasst.
# - Nie doppelt: Telefon (normalisiert), E-Mail, Webseiten-Domain, Firma+Ort, Firma+Straße.
#   Ähnliche Treffer werden gemeldet, nie automatisch zusammengeführt.
# - Geschützt: Kunden, Nicht anrufen, Kein Interesse, Gesperrt, verloren, laufende Verkaufsvorgänge.
# - Kampagnen werden nur als Entwurf angelegt – gestartet wird nie. Keine E-Mails.
# - Jede Änderung steht in der Tabelle mcp_protokoll.
# =====================================================
import csv
import difflib
import hmac
import io
import json
import os
import re
import unicodedata
from datetime import datetime

from flask import jsonify, request

from kg_kaesten import KAESTEN, KASTEN_NAME
from leon_auto_kampagne import CRM_ENDE, ERREICHT_ENDE
from leon_datenbank import ensure_leon_links
from lead_kern import Papierkorb, PapierkorbIndex, eigene_firma, reinigungsfirma, tabelle_da, MAX_VORSCHAU

MAX_IMPORT = 100
GESCHUETZT_STATUS = {"kunde", "kunden", "nicht anrufen", "kein interesse", "gesperrt", "verloren"}
GESCHUETZT_ERGEBNIS = {"kein interesse", "nicht anrufen", "gesperrt"}
ERLAUBTE_STATUS = {"Neu", "Kontaktiert", "Interessiert", "Kein Interesse", "Nicht anrufen", "Verloren"}
FREEMAIL = {"gmail.com", "googlemail.com", "web.de", "gmx.de", "gmx.net", "t-online.de", "outlook.com", "outlook.de",
            "hotmail.com", "hotmail.de", "yahoo.com", "yahoo.de", "icloud.com", "aol.com", "freenet.de", "live.de"}
RECHTSFORM = re.compile(r"\b(gmbh|mbh|ug|haftungsbeschraenkt|ag|kg|gbr|ohg|ek|e k|eg|ev|e v|co|und co|inh|inhaber)\b")
NEUE_SPALTEN = {"notiz": "TEXT", "quelle_url": "TEXT", "ansprechpartner_geprueft": "INTEGER", "ls_punkte": "INTEGER",
                "ansprechpartner": "TEXT", "erstellt_am": "TEXT", "sort_order": "INTEGER DEFAULT 0", "quelle": "TEXT"}
CSV_SPALTEN = {
    "firma": ("firma", "firmenname", "name", "unternehmen", "company"),
    "telefon": ("telefon", "tel", "telefonnummer", "phone", "rufnummer"),
    "email": ("email", "e-mail", "mail", "e_mail"),
    "website": ("website", "webseite", "url", "homepage", "web"),
    "strasse": ("strasse", "straße", "str", "adresse", "address", "anschrift"),
    "plz": ("plz", "postleitzahl", "zip"),
    "stadt": ("stadt", "ort", "city"),
    "branche": ("branche", "sektor", "kategorie", "industry"),
    "branche_id": ("branche_id", "kasten"),
    "ansprechpartner": ("ansprechpartner", "kontakt", "kontaktperson", "contact"),
    "quelle_url": ("quelle_url", "quelle", "source", "source_url", "link"),
    "punkte": ("punkte", "score", "lead_punkte", "potenzial", "potential"),
    "notiz": ("notiz", "grund", "begruendung", "kommentar", "note"),
}


# ---------------- Normalisieren ----------------
def _text(wert, laenge=200):
    return re.sub(r"\s+", " ", str(wert or "")).strip()[:laenge]


def _ascii(text):
    text = (text or "").lower().replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def telefon_norm(telefon):
    t = re.sub(r"\D", "", telefon or "")
    if t.startswith("0049"):
        t = "0" + t[4:].lstrip("0")
    elif t.startswith("49") and len(t) > 10:
        t = "0" + t[2:].lstrip("0")
    return t if len(t) >= 6 else ""


def firma_norm(firma):
    t = re.sub(r"[^a-z0-9 ]", " ", _ascii(firma).replace("&", " und "))
    t = RECHTSFORM.sub(" ", t)
    return re.sub(r"\s+", "", t)


def ort_norm(ort):
    return re.sub(r"[^a-z]", "", _ascii(ort))


def strasse_norm(strasse):
    t = _ascii(strasse).replace("strasse", "str").replace("str.", "str")
    return re.sub(r"[^a-z0-9]", "", t)


def domain(website="", email=""):
    if website:
        d = re.sub(r"^[a-z]+://", "", website.strip().lower()).split("/")[0].split("?")[0]
        d = d[4:] if d.startswith("www.") else d
        if "." in d:
            return d
    if email and "@" in email:
        d = email.strip().lower().split("@", 1)[1]
        if d not in FREEMAIL:
            return d
    return ""


def kasten_fuer(branche_id, branche):
    bid = str(branche_id or "").strip()
    if bid in KASTEN_NAME:
        return bid
    text = _ascii(branche)
    if text:
        for k in KAESTEN:
            if _ascii(k["title"]) == text:
                return k["id"]
        for k in KAESTEN:
            woerter = [_ascii(w.strip()) for w in (k["title"].replace("&", ",") + "," + k["text"]).split(",") if w.strip()]
            if any(w and (w in text or text in w) for w in woerter):
                return k["id"]
    return "12"


def _punkte(wert):
    try:
        return max(0, min(100, int(float(wert))))
    except (TypeError, ValueError):
        return None


# ---------------- Dubletten ----------------
class Bestand:
    """Alle CRM-Firmen einmal laden und für die Prüfung indizieren (22 000 Zeilen sind schnell)."""

    def __init__(self, conn):
        self.zeilen = {}
        self.tel, self.mail, self.dom, self.name_ort, self.name_str, self.namen = {}, {}, {}, {}, {}, {}
        for r in conn.execute("SELECT id, firma, telefon, email, website, strasse, plz, stadt, status FROM leads"):
            self.hinzu(dict(r))

    def hinzu(self, r):
        self.zeilen[r["id"]] = r
        n = firma_norm(r.get("firma"))
        for wert, index in ((telefon_norm(r.get("telefon")), self.tel), ((r.get("email") or "").strip().lower(), self.mail),
                            (domain(r.get("website"), r.get("email")), self.dom)):
            if wert:
                index.setdefault(wert, r["id"])
        if n:
            for ort in {ort_norm(r.get("stadt")), re.sub(r"\D", "", r.get("plz") or "")} - {""}:
                self.name_ort.setdefault((n, ort), r["id"])
            if strasse_norm(r.get("strasse")):
                self.name_str.setdefault((n, strasse_norm(r.get("strasse"))), r["id"])
            self.namen.setdefault(n, []).append(r["id"])

    def pruefen(self, f):
        """→ ("vorhanden", id, grund) | ("unklar", id, grund) | (None, None, "")"""
        n = firma_norm(f.get("firma"))
        t = telefon_norm(f.get("telefon"))
        if t and t in self.tel:
            return "vorhanden", self.tel[t], "gleiche Telefonnummer"
        m = (f.get("email") or "").strip().lower()
        if m and m in self.mail:
            return "vorhanden", self.mail[m], "gleiche E-Mail"
        d = domain(f.get("website"), f.get("email"))
        if d and d in self.dom:
            return "vorhanden", self.dom[d], "gleiche Webseite/Domain"
        if n:
            for ort in (ort_norm(f.get("stadt")), re.sub(r"\D", "", f.get("plz") or "")):
                if ort and (n, ort) in self.name_ort:
                    return "vorhanden", self.name_ort[(n, ort)], "gleiche Firma am gleichen Ort"
            s = strasse_norm(f.get("strasse"))
            if s and (n, s) in self.name_str:
                return "vorhanden", self.name_str[(n, s)], "gleiche Firma, gleiche Straße"
            if n in self.namen:
                return "unklar", self.namen[n][0], "gleicher Firmenname, anderer Ort"
            if len(n) >= 6:
                ort = ort_norm(f.get("stadt"))
                for kandidat in difflib.get_close_matches(n, list(self.namen.keys()), n=1, cutoff=0.9):
                    vid = self.namen[kandidat][0]
                    if not ort or ort_norm(self.zeilen[vid].get("stadt")) == ort:
                        return "unklar", vid, "sehr ähnlicher Firmenname"
        return None, None, ""


def _kurz(r):
    return {"id": r["id"], "firma": r.get("firma"), "telefon": r.get("telefon"), "stadt": r.get("stadt"),
            "status": r.get("status")}


# ---------------- Schutz ----------------
def schutz_grund(conn, lead_id):
    try:
        return _schutz_grund(conn, lead_id)
    except Exception:  # z. B. Tagesliste-Tabellen fehlen – dann nur Status prüfen
        r = conn.execute("SELECT status FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not r:
            return "nicht gefunden"
        status = str(r["status"] or "").strip().lower()
        return f"geschützt (Status {r['status']})" if status in GESCHUETZT_STATUS | CRM_ENDE else ""


def _schutz_grund(conn, lead_id):
    r = conn.execute("""
        SELECT l.status, k.letztes_ergebnis,
               (SELECT h.status FROM tagesliste_leads t JOIN tagesliste_status_history h ON h.tagesliste_id = t.id
                 WHERE t.source_lead_id = l.id AND h.status IN ('verloren','besichtigung','angebot') LIMIT 1) AS tl
        FROM leads l LEFT JOIN leon_links k ON k.quelle = 'leads' AND k.crm_id = l.id WHERE l.id = ?
    """, (lead_id,)).fetchone()
    if not r:
        return "nicht gefunden"
    status = str(r["status"] or "").strip().lower()
    if status in GESCHUETZT_STATUS:
        return f"geschützt (Status {r['status']})"
    if status in CRM_ENDE:
        return f"läuft bereits im Verkauf (Status {r['status']})"
    if str(r["letztes_ergebnis"] or "").strip().lower() in GESCHUETZT_ERGEBNIS | ERREICHT_ENDE:
        return f"Leon-Ergebnis „{r['letztes_ergebnis']}“"
    if r["tl"]:
        return f"Tagesliste: {r['tl']}"
    return ""


# ---------------- Löschen (Papierkorb) ----------------
LOESCH_STATUS = GESCHUETZT_STATUS | CRM_ENDE | {"rückruf", "rueckruf", "termin", "besichtigung", "angebot"}


def loesch_schutz(conn, ids, kampagnen):
    """{id: grund} – Kunden, Sperren (Nicht anrufen/Gesperrt/Kein Interesse), laufender Verkauf, Tagesliste,
    Leon-Kampagne nicht beendet, eigene Firma. kampagnen = {Leon-Kampagnen-ID: Status} oder None (Leon nicht erreichbar)."""
    if not ids:
        return {}
    kunden_tel, kunden_namen = set(), set()
    if tabelle_da(conn, "kunden"):
        for k in conn.execute("SELECT firma, telefon FROM kunden"):
            if telefon_norm(k["telefon"]):
                kunden_tel.add(telefon_norm(k["telefon"]))
            if firma_norm(k["firma"]):
                kunden_namen.add(firma_norm(k["firma"]))
    tl = ("(SELECT t.status FROM tagesliste_leads t WHERE t.source_lead_id = l.id ORDER BY t.id DESC LIMIT 1)"
          if tabelle_da(conn, "tagesliste_leads") else "NULL")
    marks = ",".join("?" * len(ids))
    schutz = {}
    for r in conn.execute(f"""
            SELECT l.id, l.firma, l.telefon, l.status, k.letztes_ergebnis, k.kampagne_id, {tl} AS tl_status
            FROM leads l LEFT JOIN leon_links k ON k.quelle = 'leads' AND k.crm_id = l.id WHERE l.id IN ({marks})""",
                          [int(i) for i in ids]):
        status = str(r["status"] or "").strip().lower()
        ergebnis = str(r["letztes_ergebnis"] or "").strip().lower()
        if eigene_firma(r["firma"]):
            grund = "eigene Firma / Testeintrag"
        elif status in ("kunde", "kunden") or telefon_norm(r["telefon"]) in kunden_tel or firma_norm(r["firma"]) in kunden_namen:
            grund = "Kunde"
        elif status in ("nicht anrufen", "gesperrt", "kein interesse") or ergebnis in GESCHUETZT_ERGEBNIS:
            grund = f"Sperre bleibt erhalten ({r['status'] if status else r['letztes_ergebnis']})"
        elif status in LOESCH_STATUS:
            grund = f"Verkaufsvorgang (Status {r['status']})"
        elif ergebnis in ERREICHT_ENDE:
            grund = f"Leon-Ergebnis „{r['letztes_ergebnis']}“ (Rückruf/Termin/Interesse)"
        elif r["tl_status"] is not None:
            grund = f"steht in der Tagesliste (Status {r['tl_status']})"
        elif r["kampagne_id"] and kampagnen is None:
            grund = "an Leon übergeben – Kampagnen-Status gerade nicht prüfbar"
        elif r["kampagne_id"] and (kampagnen or {}).get(int(r["kampagne_id"]), "Beendet") != "Beendet":
            grund = f"in Leon-Kampagne {r['kampagne_id']} ({kampagnen[int(r['kampagne_id'])]})"
        else:
            continue
        schutz[r["id"]] = grund
    return schutz


def loesch_verknuepfungen(conn, ids):
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    links = {}
    for r in conn.execute(f"SELECT crm_id, leon_lead_id, kampagne_id, letztes_ergebnis FROM leon_links "
                          f"WHERE quelle = 'leads' AND crm_id IN ({marks})", [int(i) for i in ids]):
        links[r["crm_id"]] = [f"an Leon übergeben (Leon-Lead {r['leon_lead_id']}, Kampagne {r['kampagne_id'] or '–'}, "
                              f"Ergebnis {r['letztes_ergebnis'] or '–'}) – Leon-Verlauf bleibt unverändert"]
    return links


FILTER_FELDER = ("ids", "suche", "stadt", "plz", "status", "branche", "branche_id", "quelle", "typ")


def loesch_kandidaten(conn, d):
    """→ (kandidaten, unklar [(zeile, grund)], weitere, kriterien) oder Fehlertext."""
    kriterien = {k: d.get(k) for k in FILTER_FELDER if d.get(k) not in (None, "", [])}
    if not kriterien:
        return "Bitte mindestens einen Filter (ids, suche, stadt, plz, status, branche, quelle, typ) – alles löschen geht nie."
    bed, par = [], []
    if d.get("ids"):
        try:
            ids = list(dict.fromkeys(int(x) for x in d["ids"]))[:MAX_VORSCHAU]
        except (TypeError, ValueError):
            return "ids müssen Zahlen sein."
        bed.append(f"id IN ({','.join('?' * len(ids))})")
        par += ids
    if d.get("suche"):
        bed.append("firma LIKE ?")
        par.append(f"%{_text(d['suche'], 120)}%")
    if d.get("stadt"):
        bed.append("stadt LIKE ?")
        par.append(f"%{_text(d['stadt'], 80)}%")
    if d.get("plz"):
        bed.append("plz LIKE ?")
        par.append(f"{re.sub(r'[^0-9]', '', str(d['plz']))[:5]}%")
    if d.get("status"):
        bed.append("LOWER(TRIM(COALESCE(status, ''))) = ?")
        par.append(_text(d["status"], 40).lower())
    if d.get("branche") or d.get("branche_id"):
        bed.append("branche_id = ?")
        par.append(kasten_fuer(d.get("branche_id"), d.get("branche")))
    if d.get("quelle"):
        bed.append("quelle = ?")
        par.append(_text(d["quelle"], 40))
    typ = str(d.get("typ") or "").strip().lower()
    if typ and typ != "reinigungsfirma":
        return "typ kann nur „reinigungsfirma“ sein."
    zeilen = [dict(r) for r in conn.execute(
        f"SELECT * FROM leads {('WHERE ' + ' AND '.join(bed)) if bed else ''} ORDER BY id", par)]
    unklar = []
    if typ == "reinigungsfirma":
        sicher = []
        for r in zeilen:
            art, warum = reinigungsfirma(r.get("firma"), r.get("branche_name"), r.get("suchwort"))
            if art == "sicher":
                sicher.append(r)
            elif art == "unklar":
                unklar.append((r, warum))
        zeilen = sicher
    weitere = max(0, len(zeilen) - MAX_VORSCHAU)
    return zeilen[:MAX_VORSCHAU], unklar, weitere, kriterien


# ---------------- Routen ----------------
def register_mcp_crm_leads(app, get_db_connection, leon_client_factory):

    def _conn():
        conn = get_db_connection()
        vorhanden = {r[1] for r in conn.execute("PRAGMA table_info(leads)")}
        for name, typ in NEUE_SPALTEN.items():
            if name not in vorhanden:
                try:
                    conn.execute(f"ALTER TABLE leads ADD COLUMN {name} {typ}")
                except Exception:
                    pass
        conn.execute("""CREATE TABLE IF NOT EXISTS mcp_protokoll (
            id INTEGER PRIMARY KEY AUTOINCREMENT, zeit TEXT, aktion TEXT, details TEXT)""")
        ensure_leon_links(conn)
        conn.commit()
        return conn

    def _protokoll(conn, aktion, details):
        conn.execute("INSERT INTO mcp_protokoll (zeit, aktion, details) VALUES (?, ?, ?)",
                     (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), aktion, json.dumps(details, ensure_ascii=False)[:20000]))

    def _papierkorb(conn):
        kampagnen = {}

        def leon_kampagnen():
            if "werte" not in kampagnen:
                try:
                    code, daten = leon_client_factory().request("GET", "/api/campaigns", timeout=30)
                    kampagnen["werte"] = ({int(c["id"]): str(c.get("status") or "") for c in (daten or {}).get("campaigns", [])}
                                          if code < 400 else None)
                except Exception:  # Leon nicht erreichbar → übergebene Firmen gelten als geschützt
                    kampagnen["werte"] = None
            return kampagnen["werte"]

        return Papierkorb(conn, schutz=lambda c, ids: loesch_schutz(c, ids, leon_kampagnen()),
                          verknuepfungen=loesch_verknuepfungen,
                          protokoll=lambda aktion, details: _protokoll(conn, aktion, details))

    def _firma_aus(f):
        f2 = {k: _text(f.get(k), n) for k, n in (("firma", 200), ("telefon", 60), ("email", 120), ("website", 300),
                                                  ("strasse", 120), ("plz", 5), ("stadt", 80), ("ansprechpartner", 120),
                                                  ("quelle_url", 500), ("notiz", 1000), ("branche", 120), ("branche_id", 5))}
        f2 = dict(f2)
        f2.update(punkte=_punkte(f.get("punkte")), ansprechpartner_geprueft=bool(f.get("ansprechpartner_geprueft")))
        return f2

    def _anlegen(conn, f):
        bid = kasten_fuer(f.get("branche_id"), f.get("branche"))
        sort = conn.execute("SELECT COALESCE(MAX(sort_order), 0) FROM leads WHERE branche_id = ?", (bid,)).fetchone()[0]
        ap = f["ansprechpartner"] if f["ansprechpartner_geprueft"] else ""  # nur bestätigte Namen
        cur = conn.execute("""
            INSERT INTO leads (branche_id, branche_name, suchwort, firma, strasse, plz, stadt, telefon, email, website,
                               ansprechpartner, ansprechpartner_geprueft, quelle, quelle_url, notiz, status, sort_order,
                               erstellt_am, ls_punkte)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ChatGPT', ?, ?, 'Neu', ?, CURRENT_TIMESTAMP, ?)
        """, (bid, KASTEN_NAME[bid], f["branche"][:80], f["firma"], f["strasse"], f["plz"], f["stadt"], f["telefon"],
              f["email"], f["website"], ap, 1 if ap else 0, f["quelle_url"], f["notiz"], int(sort or 0) + 1, f["punkte"]))
        return cur.lastrowid, bid

    def _importieren(conn, firmen):
        bestand = Bestand(conn)
        papierkorb = PapierkorbIndex(conn)
        ergebnis = {"neu": [], "vorhanden": [], "unklar": [], "fehler": [], "im_papierkorb": []}
        for i, roh in enumerate(firmen):
            if not isinstance(roh, dict):
                ergebnis["fehler"].append({"zeile": i + 1, "grund": "kein Objekt"})
                continue
            f = _firma_aus(roh)
            if not f["firma"]:
                ergebnis["fehler"].append({"zeile": i + 1, "grund": "Firma fehlt"})
                continue
            art, vid, grund = bestand.pruefen(f)
            if art:
                eintrag = {"zeile": i + 1, "firma": f["firma"], "grund": grund, "vorhanden": _kurz(bestand.zeilen[vid])}
                schutz = schutz_grund(conn, vid)
                if schutz:
                    eintrag["schutz"] = schutz
                ergebnis[art].append(eintrag)
                continue
            geloescht, warum = papierkorb.pruefen(f)
            if geloescht:
                ergebnis["im_papierkorb"].append({"zeile": i + 1, "firma": f["firma"], "grund": warum, "papierkorb": geloescht,
                                                  "hinweis": "wurde gelöscht – nur mit lead_wiederherstellen zurückholen"})
                continue
            if not telefon_norm(f["telefon"]) and not f["email"] and not f["website"]:
                ergebnis["fehler"].append({"zeile": i + 1, "firma": f["firma"], "grund": "weder Telefon noch E-Mail noch Webseite"})
                continue
            if f["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-z]{2,}", f["email"].lower()):
                ergebnis["fehler"].append({"zeile": i + 1, "firma": f["firma"], "grund": "E-Mail ungültig"})
                continue
            neu_id, bid = _anlegen(conn, f)
            neu = {"id": neu_id, "firma": f["firma"], "telefon": f["telefon"], "email": f["email"], "website": f["website"],
                   "strasse": f["strasse"], "plz": f["plz"], "stadt": f["stadt"], "status": "Neu"}
            bestand.hinzu(neu)
            ergebnis["neu"].append({"zeile": i + 1, "id": neu_id, "firma": f["firma"], "kasten": KASTEN_NAME[bid],
                                    "telefon_fehlt": not telefon_norm(f["telefon"])})
        ergebnis["kampagnenfaehig"] = [e["id"] for e in ergebnis["neu"] if not e["telefon_fehlt"]]
        return ergebnis

    def _csv_firmen(text):
        text = (text or "").lstrip("﻿")
        try:
            dialekt = csv.Sniffer().sniff(text[:2000], delimiters=",;\t")
        except csv.Error:
            dialekt = csv.excel
        leser = csv.DictReader(io.StringIO(text), dialect=dialekt)
        zuordnung = {}
        for spalte in leser.fieldnames or []:
            s = _ascii(spalte).strip().replace(" ", "_")
            for feld, namen in CSV_SPALTEN.items():
                if s in {_ascii(n).replace(" ", "_") for n in namen} and feld not in zuordnung.values():
                    zuordnung[spalte] = feld
                    break
        firmen = [{feld: zeile.get(spalte) for spalte, feld in zuordnung.items()} for zeile in leser]
        return firmen, zuordnung

    # ---------------- Leon Reinigung ----------------
    def _leon_lead_pruefen(client, lead_id):
        code, daten = client.request("GET", f"/api/leads/{int(lead_id)}", timeout=30)
        lead = (daten or {}).get("lead") or {}
        if code >= 400 or not lead:
            return None, "Leon-Lead nicht gefunden"
        if int(lead.get("do_not_call") or 0) == 1:
            return lead, "Nicht anrufen (gesperrt)"
        if str(lead.get("status") or "") in ("Kein Interesse", "Gesperrt"):
            return lead, f"Status {lead.get('status')}"
        if str(lead.get("firma") or "").startswith("KG Mitarbeiter "):
            return lead, "gehört zum KG-Agent (Stundenzettel)"
        return lead, ""

    def _kampagne_lesen(client, kampagne_id):
        code, daten = client.request("GET", "/api/campaigns", timeout=30)
        k = next((c for c in (daten or {}).get("campaigns", []) if int(c.get("id") or 0) == int(kampagne_id)), None)
        if not k:
            return None
        code, leads = client.request("GET", f"/api/campaigns/{int(kampagne_id)}/leads", timeout=30)
        liste = [{"leon_lead_id": l.get("lead_id") or l.get("id"), "firma": l.get("firma"), "telefon": l.get("telefon")}
                 for l in (leads or {}).get("selected_leads", [])]
        return {"id": k.get("id"), "name": k.get("name"), "status": k.get("status"),
                "agent": k.get("agent_name") or k.get("agent_id"), "leads": liste, "anzahl_leads": len(liste)}

    def _leads_kampagne(conn, d):
        client = leon_client_factory()
        crm_ids = [int(x) for x in (d.get("crm_lead_ids") or []) if str(x).strip().isdigit()][:200]
        leon_ids = [int(x) for x in (d.get("leon_lead_ids") or []) if str(x).strip().isdigit()][:200]
        leer = not crm_ids and not leon_ids  # nur Name: leeren Entwurf anlegen (wie im CRM)
        if leer and d.get("kampagne_id"):
            return {"success": False, "error": "Keine Firmen angegeben (crm_lead_ids und/oder leon_lead_ids)."}, 400
        uebersprungen, ok_crm, ok_leon = [], [], []
        for i in dict.fromkeys(crm_ids):
            grund = schutz_grund(conn, i)
            if grund:
                uebersprungen.append({"crm_lead_id": i, "grund": grund})
            else:
                ok_crm.append(i)
        for i in dict.fromkeys(leon_ids):
            lead, grund = _leon_lead_pruefen(client, i)
            if grund:
                uebersprungen.append({"leon_lead_id": i, "firma": (lead or {}).get("firma"), "grund": grund})
            else:
                ok_leon.append(i)
        if not leer and not ok_crm and not ok_leon:
            return {"success": False, "error": "Keine Firma darf in eine Kampagne.", "uebersprungen": uebersprungen}, 400

        kampagne_id = int(d.get("kampagne_id") or 0)
        name = _text(d.get("name"), 120)
        if kampagne_id:
            k = _kampagne_lesen(client, kampagne_id)
            if not k:
                return {"success": False, "error": "Kampagne nicht gefunden."}, 404
            if k["status"] != "Entwurf":
                return {"success": False, "error": f"Kampagne ist „{k['status']}“ – nur Entwürfe dürfen ergänzt werden."}, 409
        else:
            if not name:
                return {"success": False, "error": "Bitte einen Kampagnen-Namen angeben (oder kampagne_id eines Entwurfs)."}, 400
            # gleicher Name als Entwurf schon da (z. B. zweiter Versuch): diesen nehmen statt doppelt anlegen
            code, alle = client.request("GET", "/api/campaigns", timeout=30)
            gleich = [c for c in (alle or {}).get("campaigns", [])
                      if str(c.get("name") or "").strip().lower() == name.lower() and c.get("status") == "Entwurf"]
            if gleich:
                kampagne_id = int(gleich[0]["id"])
            else:
                code, neu = client.request("POST", "/api/campaigns", {"name": name, "agent_id": 1}, timeout=30)
                kampagne_id = int((neu or {}).get("id") or ((neu or {}).get("campaign") or {}).get("id") or 0)
                if not kampagne_id:
                    return {"success": False, "error": (neu or {}).get("error") or "Kampagne konnte nicht angelegt werden."}, 502

        hinweise = ["Gleichnamiger Entwurf war schon da – er wurde verwendet."] if not d.get("kampagne_id") and gleich else []
        if ok_crm:  # gleiche Übergabe wie „Aus Datenbank“ (Leon-Lead + leon_links)
            c = app.test_client()
            with c.session_transaction() as sess:
                sess["logged_in"] = True
            r = c.post("/api/leon/datenbank/uebergeben", json={"quelle": "leads", "ids": ok_crm, "kampagne_id": kampagne_id})
            res = r.get_json(silent=True) or {}
            if not res.get("success"):
                hinweise.append(res.get("error") or f"Übergabe fehlgeschlagen (HTTP {r.status_code})")
            hinweise += res.get("hinweise") or []
        if ok_leon:
            code, res = client.request("POST", f"/api/campaigns/{kampagne_id}/leads", {"lead_ids": ok_leon}, timeout=30)
            if not (res or {}).get("success"):
                hinweise.append((res or {}).get("error") or "Leon-Leads konnten nicht zugeordnet werden.")
        kontrolle = _kampagne_lesen(client, kampagne_id)
        _protokoll(conn, "leads_kampagne", {"kampagne_id": kampagne_id, "name": name, "crm": ok_crm, "leon": ok_leon,
                                            "uebersprungen": uebersprungen})
        conn.commit()
        return {"success": True, "kampagne": kontrolle, "uebersprungen": uebersprungen, "hinweise": hinweise[:20],
                "gestartet": False, "info": "Entwurf – Leon ruft erst an, wenn Murat die Kampagne im CRM startet."}, 200

    @app.route("/internal/mcp/crm", methods=["POST"])
    def mcp_crm():
        token = (os.getenv("KG_MCP_TOKEN") or "").strip()
        given = (request.headers.get("X-KG-MCP-Token") or "").strip()
        if not token or not hmac.compare_digest(given.encode(), token.encode()):
            return jsonify({"success": False, "error": "Zugriff verweigert"}), 403
        d = request.get_json(silent=True) or {}
        aktion = str(d.get("aktion") or "")
        conn = _conn()
        try:
            if aktion == "leads_suchen":
                q = _text(d.get("suche"), 120)
                bed, par = [], []
                if d.get("id"):
                    bed.append("l.id = ?")
                    par.append(int(d["id"]))
                if q:
                    t = telefon_norm(q)
                    bed.append("(l.firma LIKE ? OR l.email LIKE ? OR l.website LIKE ?" + (" OR REPLACE(REPLACE(REPLACE(REPLACE(l.telefon,' ',''),'/',''),'-',''),'+49','0') LIKE ?" if t else "") + ")")
                    par += [f"%{q}%"] * 3 + ([f"%{t[-7:]}%"] if t else [])
                for feld in ("stadt", "status"):
                    if d.get(feld):
                        bed.append(f"l.{feld} LIKE ?")
                        par.append(f"%{_text(d[feld], 80)}%")
                if d.get("branche") or d.get("branche_id"):
                    bed.append("l.branche_id = ?")
                    par.append(kasten_fuer(d.get("branche_id"), d.get("branche")))
                if d.get("quelle"):
                    bed.append("l.quelle = ?")
                    par.append(_text(d["quelle"], 40))
                limit = max(1, min(50, int(d.get("limit") or 20)))
                rows = conn.execute(f"""
                    SELECT l.id, l.firma, l.telefon, l.email, l.website, l.strasse, l.plz, l.stadt, l.branche_name,
                           l.ansprechpartner, l.status, l.quelle, l.quelle_url, l.notiz, l.ls_punkte AS punkte,
                           k.letztes_ergebnis AS leon_ergebnis
                    FROM leads l LEFT JOIN leon_links k ON k.quelle = 'leads' AND k.crm_id = l.id
                    {('WHERE ' + ' AND '.join(bed)) if bed else ''} ORDER BY l.id DESC LIMIT ?
                """, par + [limit]).fetchall()
                leads = []
                for r in rows:
                    e = dict(r)
                    e["schutz"] = schutz_grund(conn, e["id"])
                    leads.append(e)
                return jsonify({"success": True, "leads": leads, "anzahl": len(leads)})

            if aktion in ("lead_erstellen", "leads_importieren"):
                if aktion == "lead_erstellen":
                    firmen, zuordnung = [d.get("lead") or {}], None
                elif d.get("csv"):
                    firmen, zuordnung = _csv_firmen(str(d.get("csv")))
                else:
                    firmen, zuordnung = d.get("leads"), None
                if not isinstance(firmen, list) or not firmen:
                    return jsonify({"success": False, "error": "Keine Firmen übergeben (leads als Liste oder csv)."}), 400
                if len(firmen) > MAX_IMPORT:
                    return jsonify({"success": False, "error": f"Höchstens {MAX_IMPORT} Firmen pro Aufruf – bitte in Teilen senden."}), 400
                ergebnis = _importieren(conn, firmen)
                _protokoll(conn, aktion, {"neu": [e["id"] for e in ergebnis["neu"]], "vorhanden": len(ergebnis["vorhanden"]),
                                          "unklar": len(ergebnis["unklar"]), "fehler": len(ergebnis["fehler"])})
                conn.commit()
                antwort = {"success": True, **ergebnis,
                           "zusammenfassung": {k: len(ergebnis[k]) for k in ("neu", "vorhanden", "unklar", "fehler", "im_papierkorb")}}
                if zuordnung is not None:
                    antwort["csv_spalten"] = zuordnung
                return jsonify(antwort)

            if aktion == "lead_aktualisieren":
                lead_id = int(d.get("id") or 0)
                alt = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
                if not alt:
                    return jsonify({"success": False, "error": "Firma nicht gefunden."}), 404
                alt = dict(alt)
                if str(alt.get("status") or "").strip().lower() in ("kunde", "kunden"):
                    return jsonify({"success": False, "error": "Kunden werden hier nicht geändert."}), 409
                felder = d.get("felder") or {}
                erlaubt = {"firma": 200, "telefon": 60, "email": 120, "website": 300, "strasse": 120, "plz": 5,
                           "stadt": 80, "ansprechpartner": 120, "quelle_url": 500}
                setzen = {k: _text(v, erlaubt[k]) for k, v in felder.items() if k in erlaubt}
                if "branche" in felder or "branche_id" in felder:
                    bid = kasten_fuer(felder.get("branche_id"), felder.get("branche"))
                    setzen.update(branche_id=bid, branche_name=KASTEN_NAME[bid])
                if "status" in felder:
                    status = _text(felder["status"], 40)
                    if status not in ERLAUBTE_STATUS:
                        return jsonify({"success": False, "error": f"Status nur: {', '.join(sorted(ERLAUBTE_STATUS))}"}), 400
                    setzen["status"] = status
                if felder.get("notiz_anhaengen"):
                    zeile = f"[{datetime.now().strftime('%d.%m.%Y')} ChatGPT] {_text(felder['notiz_anhaengen'], 1000)}"
                    setzen["notiz"] = ((alt.get("notiz") or "") + "\n" + zeile).strip()
                if "ansprechpartner" in setzen:
                    setzen["ansprechpartner_geprueft"] = 1 if felder.get("ansprechpartner_geprueft") else 0
                    if not setzen["ansprechpartner_geprueft"]:
                        return jsonify({"success": False, "error": "Ansprechpartner nur, wenn er bestätigt ist (ansprechpartner_geprueft=true)."}), 400
                if not setzen:
                    return jsonify({"success": False, "error": "Keine erlaubten Felder übergeben."}), 400
                conn.execute(f"UPDATE leads SET {', '.join(k + ' = ?' for k in setzen)} WHERE id = ?",
                             list(setzen.values()) + [lead_id])
                _protokoll(conn, "lead_aktualisieren", {"id": lead_id, "vorher": {k: alt.get(k) for k in setzen}, "nachher": setzen})
                conn.commit()
                return jsonify({"success": True, "id": lead_id, "geaendert": setzen})

            if aktion == "leads_kampagne":
                try:
                    antwort, code = _leads_kampagne(conn, d)
                except Exception as exc:  # z. B. Leon nicht erreichbar
                    return jsonify({"success": False, "error": f"Leon: {exc}"}), 502
                return jsonify(antwort), code

            if aktion == "kampagne_pruefen":
                client = leon_client_factory()
                kid = int(d.get("kampagne_id") or 0)
                try:
                    if not kid and d.get("name"):
                        code, daten = client.request("GET", "/api/campaigns", timeout=30)
                        treffer = [c for c in (daten or {}).get("campaigns", [])
                                   if str(c.get("name") or "").strip().lower() == _text(d["name"], 120).lower()]
                        kid = int(treffer[0]["id"]) if treffer else 0
                    k = _kampagne_lesen(client, kid) if kid else None
                except Exception as exc:
                    return jsonify({"success": False, "error": f"Leon: {exc}"}), 502
                if not k:
                    return jsonify({"success": False, "error": "Kampagne nicht gefunden."}), 404
                return jsonify({"success": True, "kampagne": k})

            if aktion == "loeschen_vorschau":
                kandidaten = loesch_kandidaten(conn, d)
                if isinstance(kandidaten, str):
                    return jsonify({"success": False, "error": kandidaten}), 400
                zeilen, unklar, weitere, kriterien = kandidaten
                if not zeilen and not unklar and d.get("ids"):
                    pk = _papierkorb(conn)
                    schon = [i for i in d["ids"] if str(i).isdigit() and pk._im_papierkorb(int(i))]
                    if schon:
                        return jsonify({"success": False, "error": "Schon im Papierkorb (nichts zu tun).", "ids": schon}), 409
                    return jsonify({"success": False, "error": "Firma nicht gefunden."}), 404
                return jsonify(_papierkorb(conn).vorschau(zeilen, kriterien, d.get("grund"), unklar, weitere))

            if aktion == "loeschen_bestaetigen":
                antwort, code = _papierkorb(conn).bestaetigen(d.get("vorschau_id"), d.get("ids"), d.get("bestaetigt"),
                                                              d.get("grund"))
                return jsonify(antwort), code

            if aktion == "papierkorb":
                return jsonify(_papierkorb(conn).liste(d.get("suche"), d.get("limit") or 50))

            if aktion == "wiederherstellen":
                antwort, code = _papierkorb(conn).wiederherstellen(d.get("ids"))
                return jsonify(antwort), code

            if aktion == "endgueltig_loeschen":
                frei = str(os.getenv("KG_MCP_ENDGUELTIG") or "").strip() == "1"
                antwort, code = _papierkorb(conn).endgueltig(d.get("ids"), d.get("bestaetigung"), frei)
                return jsonify(antwort), code

            return jsonify({"success": False, "error": "Unbekannte aktion."}), 400
        finally:
            conn.close()
