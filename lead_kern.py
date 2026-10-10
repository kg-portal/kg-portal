# =====================================================
# Firmen (leads) für ChatGPT – gemeinsamer Kern, gleiche Datei im KG CRM (lead_kern.py)
# und in KG Business (services/lead_kern.py). Nur Python-Standardbibliothek.
#
# - Normalisieren + Dubletten: Telefon, E-Mail, Webseiten-Domain, Firma+Ort, Firma+Straße;
#   Ähnliches = „unklar“ (nie automatisch zusammenführen)
# - Reinigungsfirmen erkennen: „sicher“ oder „unklar“ – Unklares wird nie automatisch gelöscht
# - Papierkorb (je Datenbank ein eigener – CRM und Business werden nie gemischt):
#     Löschen nur in zwei Schritten: Vorschau (IDs + Fingerabdruck, 30 Min. gültig) → Bestätigung genau
#     dieser IDs; vorher geändert/geschützt = neue Vorschau. In Teilen zu 50, jede Teil-Löschung eine
#     Transaktion. Gelöschte Zeilen (alle Spalten) liegen im Papierkorb und sind wiederherstellbar
#     (gleiche ID). Endgültig löschen nur mit eigener Freigabe + „ENDGÜLTIG LÖSCHEN“.
#     Firmen im Papierkorb kommen über Lead-Sammler/ChatGPT nicht wieder herein.
# =====================================================
import csv
import difflib
import hashlib
import io
import json
import re
import secrets
import unicodedata
from datetime import datetime, timedelta

VORSCHAU_MINUTEN = 30
MAX_VORSCHAU = 200
MAX_WIEDERHERSTELLEN = 100
MAX_ENDGUELTIG = 50
TEIL = 50
BESTAETIGUNG_ENDGUELTIG = "ENDGÜLTIG LÖSCHEN"

FREEMAIL = {"gmail.com", "googlemail.com", "web.de", "gmx.de", "gmx.net", "t-online.de", "outlook.com", "outlook.de",
            "hotmail.com", "hotmail.de", "yahoo.com", "yahoo.de", "icloud.com", "aol.com", "freenet.de", "live.de"}
RECHTSFORM = re.compile(r"\b(gmbh|mbh|ug|haftungsbeschraenkt|ag|kg|gbr|ohg|ek|e k|eg|ev|e v|co|und co|inh|inhaber)\b")

# Eigene Firmen und Testeinträge – nie löschen
EIGENE = re.compile(r"kg\s*-?\s*gebaeudereinigung|kg\s+mitarbeiter|kg\s*-?\s*business|kg\s*-?\s*store|kicci")


# ---------------- Normalisieren ----------------
def text(wert, laenge=200):
    return re.sub(r"\s+", " ", str(wert or "")).strip()[:laenge]


def ascii_text(wert):
    t = str(wert or "").lower().replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()


def telefon_norm(telefon):
    t = re.sub(r"\D", "", str(telefon or ""))
    if t.startswith("0049"):
        t = "0" + t[4:].lstrip("0")
    elif t.startswith("49") and len(t) > 10:
        t = "0" + t[2:].lstrip("0")
    return t if len(t) >= 6 else ""


def firma_norm(firma):
    t = re.sub(r"[^a-z0-9 ]", " ", ascii_text(firma).replace("&", " und "))
    t = RECHTSFORM.sub(" ", t)
    return re.sub(r"\s+", "", t)


def ort_norm(ort):
    return re.sub(r"[^a-z]", "", ascii_text(ort))


def strasse_norm(strasse):
    t = ascii_text(strasse).replace("strasse", "str").replace("str.", "str")
    return re.sub(r"[^a-z0-9]", "", t)


def domain(website="", email=""):
    website, email = str(website or ""), str(email or "")
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


def punkte(wert):
    try:
        return max(0, min(100, int(float(wert))))
    except (TypeError, ValueError):
        return None


def jetzt():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def tabelle_da(conn, name):
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def spalten(conn, tabelle):
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{tabelle}")')]


def eigene_firma(firma):
    return bool(EIGENE.search(ascii_text(firma)))


# ---------------- Dubletten ----------------
class Bestand:
    """Alle Firmen einmal laden und für die Prüfung indizieren (auch 20 000+ Zeilen sind schnell)."""

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


def kurz(r):
    return {"id": r["id"], "firma": r.get("firma"), "telefon": r.get("telefon"), "stadt": r.get("stadt"),
            "status": r.get("status")}


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
    "notiz": ("notiz", "grund", "begruendung", "kommentar", "note", "notizen"),
}


def csv_firmen(inhalt, extra=None):
    """CSV-Text (Komma, Semikolon oder Tab) → Liste von Firmen + erkannte Spalten."""
    namen_je_feld = dict(CSV_SPALTEN, **(extra or {}))
    inhalt = (inhalt or "").lstrip("﻿")
    try:
        dialekt = csv.Sniffer().sniff(inhalt[:2000], delimiters=",;\t")
    except csv.Error:
        dialekt = csv.excel
    leser = csv.DictReader(io.StringIO(inhalt), dialect=dialekt)
    zuordnung = {}
    for spalte in leser.fieldnames or []:
        s = ascii_text(spalte).strip().replace(" ", "_")
        for feld, namen in namen_je_feld.items():
            if s in {ascii_text(n).replace(" ", "_") for n in namen} and feld not in zuordnung.values():
                zuordnung[spalte] = feld
                break
    firmen = [{feld: zeile.get(spalte) for spalte, feld in zuordnung.items()} for zeile in leser]
    return firmen, zuordnung


# ---------------- Reinigungsfirmen erkennen ----------------
REINIGUNG_SICHER = re.compile(
    r"gebaeude\s*-?\s*reinig|gebaeudedienst|reinigungs\s*-?\s*(firma|service|dienst|unternehmen|betrieb|team|"
    r"gesellschaft|kraft|kraefte|profi|fachbetrieb|meister)|unterhaltsreinig|bueroreinig|glas\s*-?\s*(und\s+)?"
    r"(gebaeude)?reinig|fensterreinig|grundreinig|bau\s*-?\s*(end)?reinig|praxisreinig|treppenhausreinig|"
    r"industriereinig|objektreinig|sonderreinig|\breinigung(en)?\b|\bcleaning\b|\bclean\s*(service|team|pro)")
REINIGUNG_AUSNAHME = re.compile(
    r"textil|chemisch|kleider|waesche|wascherei|auto|kfz|fahrzeug|kanal|rohr|abfluss|teppich|polster|schornstein|"
    r"tank|solar|dachrinne|fassade|hausmeister|facility|entruempel|desinfektion|schaedling|boot|pool|grill|fahrrad|"
    r"reinigungs\s*-?\s*(mittel|geraet|bedarf|technik|maschin|chemie|artikel|zubehoer|automat)|waschstrasse|"
    r"waschanlage|pflege")
REINIGUNG_SCHWACH = re.compile(r"reinig|clean|putz|gebaeudeservice|gebaeudemanagement|hauswirtschaft|facility|hausmeister")


def reinigungsfirma(firma="", branche="", nebentext=""):
    """→ ("sicher" | "unklar" | None, grund). firma/branche entscheiden; nebentext (Suchwort, Beruf) macht höchstens „unklar“."""
    haupt = ascii_text(f"{firma} {branche}")
    neben = ascii_text(nebentext)
    ausnahme = REINIGUNG_AUSNAHME.search(haupt)
    sicher = REINIGUNG_SICHER.search(haupt)
    if sicher and not ausnahme:
        return "sicher", f"„{sicher.group(0).strip()}“ in Firma/Branche"
    if sicher or REINIGUNG_SCHWACH.search(haupt):
        wort = (ausnahme or sicher or REINIGUNG_SCHWACH.search(haupt)).group(0).strip()
        return "unklar", f"nicht eindeutig („{wort}“)"
    if REINIGUNG_SICHER.search(neben) or REINIGUNG_SCHWACH.search(neben):
        return "unklar", "nur Suchwort/Beruf deutet auf Reinigung"
    return None, ""


# ---------------- Papierkorb ----------------
def papierkorb_tabellen(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS lead_papierkorb (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id INTEGER NOT NULL,
        firma TEXT, stadt TEXT, telefon_norm TEXT, email TEXT, domain TEXT, name_norm TEXT, ort_norm TEXT,
        daten_json TEXT NOT NULL,
        anhang_json TEXT,
        grund TEXT, von TEXT, vorschau_id TEXT,
        geloescht_am TEXT NOT NULL,
        wiederhergestellt_am TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lead_papierkorb_lead ON lead_papierkorb(lead_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lead_papierkorb_tel ON lead_papierkorb(telefon_norm)")
    conn.execute("""CREATE TABLE IF NOT EXISTS lead_loesch_vorschau (
        id TEXT PRIMARY KEY,
        erstellt_am TEXT, gueltig_bis TEXT, kriterien_json TEXT, grund TEXT,
        ids_json TEXT, fingerabdruck_json TEXT,
        ausgefuehrt_am TEXT, ergebnis_json TEXT)""")


def papierkorb_telefone(conn):
    """Telefonnummern (normalisiert) aller Firmen im Papierkorb – für Lead-Sammler-Eingänge."""
    try:
        return {r[0] for r in conn.execute(
            "SELECT telefon_norm FROM lead_papierkorb WHERE wiederhergestellt_am IS NULL AND COALESCE(telefon_norm,'') <> ''")}
    except Exception:  # Tabelle gibt es erst nach der ersten Löschung
        return set()


class PapierkorbIndex:
    """Gelöschte Firmen sollen nicht über Import/Lead-Sammler wiederkommen."""

    def __init__(self, conn):
        self.tel, self.mail, self.dom, self.name_ort = {}, {}, {}, {}
        try:
            zeilen = conn.execute("SELECT lead_id, firma, telefon_norm, email, domain, name_norm, ort_norm, geloescht_am "
                                  "FROM lead_papierkorb WHERE wiederhergestellt_am IS NULL").fetchall()
        except Exception:
            zeilen = []
        for r in zeilen:
            e = {"lead_id": r[0], "firma": r[1], "geloescht_am": r[7]}
            for wert, index in ((r[2], self.tel), (r[3], self.mail), (r[4], self.dom)):
                if wert:
                    index.setdefault(wert, e)
            if r[5] and r[6]:
                self.name_ort.setdefault((r[5], r[6]), e)

    def pruefen(self, f):
        t = telefon_norm(f.get("telefon"))
        if t and t in self.tel:
            return self.tel[t], "gleiche Telefonnummer"
        m = (f.get("email") or "").strip().lower()
        if m and m in self.mail:
            return self.mail[m], "gleiche E-Mail"
        d = domain(f.get("website"), f.get("email"))
        if d and d in self.dom:
            return self.dom[d], "gleiche Webseite/Domain"
        n, o = firma_norm(f.get("firma")), ort_norm(f.get("stadt"))
        if n and o and (n, o) in self.name_ort:
            return self.name_ort[(n, o)], "gleiche Firma am gleichen Ort"
        return None, ""


def fingerabdruck(zeile, ohne=()):
    daten = {k: v for k, v in dict(zeile).items() if k not in ohne}
    return hashlib.sha256(json.dumps(daten, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()[:20]


class Papierkorb:
    """Zwei-Schritt-Löschen, Wiederherstellen, endgültig löschen – für die Tabelle leads einer Datenbank.

    schutz(conn, ids)            → {id: grund}  (geschützte Firmen werden nie gelöscht)
    verknuepfungen(conn, ids)    → {id: [text]} (in der Vorschau anzeigen)
    anhang_sichern(conn, id)     → dict|None    (z. B. Kampagnen-Entwurf-Zeilen, vor dem Löschen)
    anhang_zurueck(conn, id, a)  → [hinweis]    (beim Wiederherstellen)
    """

    def __init__(self, conn, schutz, verknuepfungen=None, anhang_sichern=None, anhang_zurueck=None,
                 protokoll=None, von="ChatGPT (KG Daten)", fingerabdruck_ohne=("sort_order",)):
        self.conn = conn
        self.schutz = schutz
        self.verknuepfungen = verknuepfungen or (lambda c, ids: {})
        self.anhang_sichern = anhang_sichern or (lambda c, lid: None)
        self.anhang_zurueck = anhang_zurueck or (lambda c, lid, a: [])
        self.protokoll = protokoll or (lambda aktion, details: None)
        self.von = von
        self.ohne = tuple(fingerabdruck_ohne)
        papierkorb_tabellen(conn)
        conn.commit()

    # ----- Hilfen
    def _zeile(self, lead_id):
        r = self.conn.execute("SELECT * FROM leads WHERE id = ?", (int(lead_id),)).fetchone()
        return dict(r) if r else None

    def _im_papierkorb(self, lead_id):
        r = self.conn.execute("SELECT * FROM lead_papierkorb WHERE lead_id = ? AND wiederhergestellt_am IS NULL "
                              "ORDER BY id DESC LIMIT 1", (int(lead_id),)).fetchone()
        return dict(r) if r else None

    @staticmethod
    def _kurz(r):
        return {"id": r["id"], "firma": r.get("firma"),
                "branche": r.get("branche_name") or r.get("branche") or "",
                "stadt": r.get("stadt"), "telefon": r.get("telefon"), "status": r.get("status")}

    def _schutz(self, ids):
        ergebnis = {}
        for i in range(0, len(ids), 400):
            ergebnis.update(self.schutz(self.conn, ids[i:i + 400]) or {})
        return ergebnis

    # ----- Schritt 1: Vorschau
    def vorschau(self, zeilen, kriterien, grund="", unklar=None, weitere=0):
        """zeilen: Kandidaten (dict mit allen Spalten). unklar: [(zeile, grund)] – werden nur gezeigt."""
        zeilen = list(zeilen)[:MAX_VORSCHAU]
        ids = [int(r["id"]) for r in zeilen]
        schutz = self._schutz(ids)
        links = self.verknuepfungen(self.conn, ids) or {}
        loeschbar, geschuetzt = [], []
        for r in zeilen:
            e = self._kurz(r)
            if links.get(r["id"]):
                e["verknuepfungen"] = links[r["id"]]
            if schutz.get(r["id"]):
                e["schutz"] = schutz[r["id"]]
                geschuetzt.append(e)
            else:
                loeschbar.append(e)
        unklar_liste = []
        for r, warum in (unklar or [])[:MAX_VORSCHAU]:
            e = self._kurz(r)
            e["warum_unklar"] = warum
            if schutz.get(r["id"]):
                e["schutz"] = schutz[r["id"]]
            unklar_liste.append(e)
        antwort = {"success": True, "loeschbar": loeschbar, "geschuetzt": geschuetzt, "unklar": unklar_liste,
                   "anzahl": {"loeschbar": len(loeschbar), "geschuetzt": len(geschuetzt), "unklar": len(unklar_liste),
                              "weitere_treffer": int(weitere)},
                   "geloescht": False}
        if not loeschbar:
            antwort["hinweis"] = "Nichts löschbar – es wurde nichts vorgemerkt."
            return antwort
        vid = secrets.token_hex(6)
        bis = (datetime.now() + timedelta(minutes=VORSCHAU_MINUTEN)).strftime("%Y-%m-%d %H:%M:%S")
        abdruecke = {str(r["id"]): fingerabdruck(r, self.ohne) for r in zeilen if not schutz.get(r["id"])}
        self.conn.execute("INSERT INTO lead_loesch_vorschau (id, erstellt_am, gueltig_bis, kriterien_json, grund, ids_json, "
                          "fingerabdruck_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                          (vid, jetzt(), bis, json.dumps(kriterien, ensure_ascii=False, default=str), text(grund, 300),
                           json.dumps([e["id"] for e in loeschbar]), json.dumps(abdruecke)))
        self.protokoll("loeschen_vorschau", {"vorschau_id": vid, "kriterien": kriterien, "loeschbar": [e["id"] for e in loeschbar],
                                             "geschuetzt": len(geschuetzt), "unklar": len(unklar_liste)})
        self.conn.commit()
        antwort.update(vorschau_id=vid, gueltig_bis=bis,
                       hinweis=f"Noch NICHTS gelöscht. Murat die Liste zeigen; nach seinem Ja loeschen bestätigen mit "
                               f"vorschau_id={vid} und genau den bestätigten IDs (bestaetigt=true). "
                               f"Gültig {VORSCHAU_MINUTEN} Minuten. Unklare/geschützte werden nicht gelöscht.")
        if weitere:
            antwort["hinweis"] += f" Es gibt noch {weitere} weitere Treffer – nach dieser Runde neue Vorschau."
        return antwort

    # ----- Schritt 2: Bestätigen
    def bestaetigen(self, vorschau_id, ids, bestaetigt=False, grund=""):
        if bestaetigt is not True:
            return {"success": False, "error": "Nicht bestätigt (bestaetigt=true fehlt) – erst Murats ausdrückliches Ja holen."}, 400
        v = self.conn.execute("SELECT * FROM lead_loesch_vorschau WHERE id = ?", (text(vorschau_id, 40),)).fetchone()
        if not v:
            return {"success": False, "error": "Vorschau nicht gefunden – bitte neue Vorschau."}, 404
        v = dict(v)
        try:
            ids = list(dict.fromkeys(int(x) for x in (ids or [])))
        except (TypeError, ValueError):
            return {"success": False, "error": "ids müssen Zahlen sein."}, 400
        if not ids:
            return {"success": False, "error": "Keine IDs angegeben – genau die bestätigten IDs aus der Vorschau senden."}, 400
        if v["ausgefuehrt_am"]:
            frueher = json.loads(v["ergebnis_json"] or "{}")
            if sorted(frueher.get("angefragt") or []) == sorted(ids):
                return dict(frueher, success=True, schon_ausgefuehrt=True,
                            hinweis=f"Diese Löschung wurde schon am {v['ausgefuehrt_am']} ausgeführt – nichts doppelt."), 200
            return {"success": False, "error": "Diese Vorschau wurde schon ausgeführt – für andere IDs bitte neue Vorschau."}, 409
        if v["gueltig_bis"] < jetzt():
            return {"success": False, "error": "Vorschau abgelaufen – bitte neue Vorschau."}, 409
        erlaubt = set(json.loads(v["ids_json"] or "[]"))
        fremd = [i for i in ids if i not in erlaubt]
        if fremd:
            return {"success": False, "error": "Diese IDs waren nicht in der Vorschau (löschbar) – nichts gelöscht.",
                    "nicht_in_vorschau": fremd}, 400
        abdruecke = json.loads(v["fingerabdruck_json"] or "{}")

        # Vorprüfung: alles unverändert und weiterhin ungeschützt? Sonst wird gar nichts gelöscht.
        vorhanden, schon, weg, geaendert = [], [], [], []
        for i in ids:
            r = self._zeile(i)
            if not r:
                (schon if self._im_papierkorb(i) else weg).append(i)
            elif fingerabdruck(r, self.ohne) != abdruecke.get(str(i)):
                geaendert.append({"id": i, "firma": r.get("firma")})
            else:
                vorhanden.append(i)
        schutz = self._schutz(vorhanden)
        jetzt_geschuetzt = [{"id": i, "schutz": schutz[i]} for i in vorhanden if schutz.get(i)]
        if geaendert or jetzt_geschuetzt:
            return {"success": False, "error": "Seit der Vorschau hat sich etwas geändert – nichts gelöscht, bitte neue Vorschau.",
                    "geaendert": geaendert, "jetzt_geschuetzt": jetzt_geschuetzt}, 409

        geloescht, fehler, offen = [], [], []
        grund = text(grund, 300) or v["grund"] or ""
        self.conn.commit()
        for start in range(0, len(vorhanden), TEIL):
            teil = vorhanden[start:start + TEIL]
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                fertig = []
                for i in teil:
                    r = self._zeile(i)
                    if not r:
                        continue
                    anhang = self.anhang_sichern(self.conn, i)
                    self.conn.execute(
                        "INSERT INTO lead_papierkorb (lead_id, firma, stadt, telefon_norm, email, domain, name_norm, ort_norm, "
                        "daten_json, anhang_json, grund, von, vorschau_id, geloescht_am) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (i, r.get("firma"), r.get("stadt"), telefon_norm(r.get("telefon")),
                         (r.get("email") or "").strip().lower(), domain(r.get("website"), r.get("email")),
                         firma_norm(r.get("firma")), ort_norm(r.get("stadt")),
                         json.dumps(r, ensure_ascii=False, default=str),
                         json.dumps(anhang, ensure_ascii=False, default=str) if anhang else None,
                         grund, self.von, v["id"], jetzt()))
                    self.conn.execute("DELETE FROM leads WHERE id = ?", (i,))
                    fertig.append({"id": i, "firma": r.get("firma")})
                self.conn.commit()
                geloescht += fertig
            except Exception as exc:  # dieser Teil bleibt vollständig erhalten
                self.conn.rollback()
                fehler.append({"ids": teil, "grund": f"{type(exc).__name__}: {exc}"[:300]})
                offen = vorhanden[start + TEIL:]
                break
        ergebnis = {"geloescht": geloescht, "schon_im_papierkorb": schon, "nicht_mehr_vorhanden": weg,
                    "fehler": fehler, "nicht_bearbeitet": offen, "angefragt": ids,
                    "anzahl_geloescht": len(geloescht)}
        if not fehler:
            self.conn.execute("UPDATE lead_loesch_vorschau SET ausgefuehrt_am = ?, ergebnis_json = ? WHERE id = ?",
                              (jetzt(), json.dumps(ergebnis, ensure_ascii=False), v["id"]))
        self.protokoll("loeschen", {"vorschau_id": v["id"], "geloescht": [e["id"] for e in geloescht],
                                    "fehler": fehler, "grund": grund})
        self.conn.commit()
        ergebnis.update(success=not fehler,
                        hinweis=("In den Papierkorb verschoben – wiederherstellbar mit lead_wiederherstellen."
                                 if not fehler else "Teilweise fehlgeschlagen: die fehlerhaften/übrigen IDs sind unverändert. "
                                                    "Dieselbe Bestätigung nochmal senden setzt fort (nichts doppelt)."))
        return ergebnis, 200 if not fehler else 207

    # ----- Papierkorb ansehen
    def liste(self, suche="", limit=50):
        limit = max(1, min(200, int(limit or 50)))
        bed, par = ["wiederhergestellt_am IS NULL"], []
        if suche:
            bed.append("(firma LIKE ? OR stadt LIKE ? OR CAST(lead_id AS TEXT) = ?)")
            par += [f"%{text(suche, 80)}%", f"%{text(suche, 80)}%", text(suche, 20)]
        wo = " AND ".join(bed)
        gesamt = self.conn.execute(f"SELECT COUNT(*) FROM lead_papierkorb WHERE {wo}", par).fetchone()[0]
        rows = self.conn.execute(f"SELECT lead_id, firma, stadt, grund, von, geloescht_am, daten_json FROM lead_papierkorb "
                                 f"WHERE {wo} ORDER BY id DESC LIMIT ?", par + [limit]).fetchall()
        eintraege = []
        for r in rows:
            daten = json.loads(r["daten_json"] or "{}")
            eintraege.append({"id": r["lead_id"], "firma": r["firma"], "stadt": r["stadt"], "telefon": daten.get("telefon"),
                              "status_vorher": daten.get("status"), "grund": r["grund"], "von": r["von"],
                              "geloescht_am": r["geloescht_am"]})
        return {"success": True, "papierkorb": eintraege, "anzahl": gesamt}

    # ----- Wiederherstellen
    def wiederherstellen(self, ids):
        try:
            ids = list(dict.fromkeys(int(x) for x in (ids or [])))[:MAX_WIEDERHERSTELLEN]
        except (TypeError, ValueError):
            return {"success": False, "error": "ids müssen Zahlen sein."}, 400
        if not ids:
            return {"success": False, "error": "Keine IDs angegeben."}, 400
        jetzt_spalten = set(spalten(self.conn, "leads"))
        bestand = Bestand(self.conn)
        ok, nicht = [], []
        for i in ids:
            p = self._im_papierkorb(i)
            if not p:
                frueher = self.conn.execute("SELECT wiederhergestellt_am FROM lead_papierkorb WHERE lead_id = ? "
                                            "ORDER BY id DESC LIMIT 1", (i,)).fetchone()
                vorhanden = self._zeile(i)
                nicht.append({"id": i, "grund": "ist schon wieder da" if vorhanden else
                              (f"schon am {frueher[0]} wiederhergestellt" if frueher else "nicht im Papierkorb")})
                continue
            daten = json.loads(p["daten_json"])
            if self._zeile(i):
                nicht.append({"id": i, "grund": "ID ist schon belegt"})
                continue
            art, vid, warum = bestand.pruefen(daten)
            if art == "vorhanden":
                nicht.append({"id": i, "firma": p["firma"], "grund": f"inzwischen neu vorhanden als ID {vid} ({warum}) – "
                                                                    "erst klären, sonst doppelt"})
                continue
            werte = {k: v for k, v in daten.items() if k in jetzt_spalten}
            try:
                self.conn.commit()
                self.conn.execute("BEGIN IMMEDIATE")
                self.conn.execute(f"INSERT INTO leads ({', '.join(werte)}) VALUES ({', '.join('?' * len(werte))})",
                                  list(werte.values()))
                hinweise = self.anhang_zurueck(self.conn, i, json.loads(p["anhang_json"]) if p["anhang_json"] else None) or []
                self.conn.execute("UPDATE lead_papierkorb SET wiederhergestellt_am = ? WHERE id = ?", (jetzt(), p["id"]))
                self.conn.commit()
            except Exception as exc:
                self.conn.rollback()
                nicht.append({"id": i, "firma": p["firma"], "grund": f"Fehler: {type(exc).__name__}: {exc}"[:300]})
                continue
            bestand.hinzu(dict(daten, id=i))
            e = {"id": i, "firma": p["firma"], "status": daten.get("status")}
            if art == "unklar":
                hinweise.append(f"ähnliche Firma vorhanden: ID {vid} ({warum})")
            if hinweise:
                e["hinweise"] = hinweise
            ok.append(e)
        self.protokoll("wiederherstellen", {"ok": [e["id"] for e in ok], "nicht": nicht})
        self.conn.commit()
        return {"success": bool(ok) or not nicht, "wiederhergestellt": ok, "nicht_wiederhergestellt": nicht}, 200

    # ----- Endgültig
    def endgueltig(self, ids, bestaetigung, freigegeben):
        if not freigegeben:
            return {"success": False, "error": "Endgültiges Löschen ist nicht freigeschaltet (eigene Freigabe auf dem Server nötig). "
                                               "Die Firmen bleiben sicher im Papierkorb."}, 403
        if ascii_text(bestaetigung).strip().upper() != ascii_text(BESTAETIGUNG_ENDGUELTIG).upper():
            return {"success": False, "error": f"Bestätigung fehlt: bestaetigung muss genau „{BESTAETIGUNG_ENDGUELTIG}“ sein "
                                               "(erst Murat ausdrücklich fragen)."}, 400
        try:
            ids = list(dict.fromkeys(int(x) for x in (ids or [])))
        except (TypeError, ValueError):
            return {"success": False, "error": "ids müssen Zahlen sein."}, 400
        if not ids or len(ids) > MAX_ENDGUELTIG:
            return {"success": False, "error": f"1 bis {MAX_ENDGUELTIG} IDs je Aufruf."}, 400
        weg, nicht = [], []
        for i in ids:
            p = self._im_papierkorb(i)
            if not p:
                nicht.append({"id": i, "grund": "nicht im Papierkorb"})
                continue
            self.conn.execute("DELETE FROM lead_papierkorb WHERE id = ?", (p["id"],))
            weg.append({"id": i, "firma": p["firma"]})
        self.protokoll("endgueltig_loeschen", {"ids": weg, "nicht": nicht})
        self.conn.commit()
        return {"success": True, "endgueltig_geloescht": weg, "nicht": nicht}, 200
