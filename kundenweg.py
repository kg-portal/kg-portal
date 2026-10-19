# Kundenweg (/akte): alle laufenden Firmen auf einen Blick – Spalten Interesse, Besichtigung, Angebot, Vertrag, Neue Kunden.
# Nur lesen. Die Stufe kommt aus denselben Daten wie die Kundenakte (kundenakte.py); Klick auf eine Karte öffnet die Akte.
# Abgelehnte Angebote und „Kein Interesse“ bleiben gespeichert und stehen im Filter „Abgelehnt / Verloren“.
from datetime import datetime

from flask import jsonify, render_template

import kundenakte as ka

SPALTEN = [("interesse", "Interesse"), ("besichtigung", "Besichtigung"), ("angebot", "Angebot"), ("vertrag", "Vertrag"), ("kunde", "Neue Kunden")]


def _tm(d):
    return d.strftime("%d.%m.") if d else ""


def board(conn):
    ka.tabellen(conn)
    jetzt = datetime.now(ka.BERLIN)
    hat = lambda t: ka._hat_tabelle(conn, t)

    live = {}
    if hat("tagesliste_leads"):
        sp = ka._spalten(conn, "tagesliste_leads")
        felder = [f for f in ("id", "firma", "ort", "telefon", "quelle", "status", "angebot_nr", "angebot_datum", "angebot_netto",
                              "erstellt_am", "interessiert_am") if f in sp]
        for r in conn.execute(f"SELECT {', '.join(felder)} FROM tagesliste_leads").fetchall():
            live[r["id"]] = r
    sicherung = {}  # tid → {status: backup_am}, dazu die neueste Zeile für Firma/Ort
    if hat("tagesliste_status_backup"):
        for r in conn.execute("SELECT tagesliste_id, firma, ort, telefon, quelle, status, backup_am FROM tagesliste_status_backup ORDER BY backup_am").fetchall():
            e = sicherung.setdefault(r["tagesliste_id"], {"st": {}, "zeile": r})
            e["st"][str(r["status"] or "").lower()] = ka._zeit(r["backup_am"], utc=True)
            e["zeile"] = r
    historie = {}
    if hat("tagesliste_status_history"):
        for r in conn.execute("SELECT tagesliste_id, status, datum FROM tagesliste_status_history").fetchall():
            historie.setdefault(r["tagesliste_id"], {})[str(r["status"] or "").lower()] = ka._zeit(r["datum"])
    nummern = {}
    if hat("angebot_nummern"):
        for r in conn.execute("SELECT tagesliste_id, nr, vergeben_am FROM angebot_nummern ORDER BY id").fetchall():
            nummern[r["tagesliste_id"]] = r
    entwuerfe = {r[0]: ka._zeit(r[1]) for r in conn.execute("SELECT tagesliste_id, geaendert_am FROM angebot_editor_entwuerfe").fetchall()} \
        if hat("angebot_editor_entwuerfe") else {}
    vertraege = {r["tagesliste_id"]: r for r in conn.execute("SELECT tagesliste_id, erstellt_am, gesendet_am FROM vertrag_editor WHERE tagesliste_id IS NOT NULL").fetchall()} \
        if hat("vertrag_editor") else {}
    kunden = {}
    for r in conn.execute("SELECT tagesliste_id FROM akte_kunde").fetchall():
        k = ka.kunde_link(conn, r[0])
        if k:
            kunden[r[0]] = k
    besichtigt = {r[0]: ka._zeit(r[1], utc=True) for r in conn.execute("SELECT tagesliste_id, MAX(gespeichert_am) FROM akte_besichtigung GROUP BY tagesliste_id").fetchall()}
    leon = {r[0] for r in conn.execute("SELECT DISTINCT tagesliste_id FROM leon_links WHERE tagesliste_id IS NOT NULL").fetchall()} if hat("leon_links") else set()
    anfragen = {}
    if hat("neue_anfragen") and "tagesliste_id" in ka._spalten(conn, "neue_anfragen"):
        for r in conn.execute("SELECT tagesliste_id, firma, ort, erstellt_am FROM neue_anfragen WHERE tagesliste_id IS NOT NULL").fetchall():
            anfragen[r["tagesliste_id"]] = r

    ids = set(vertraege) | set(kunden) | set(besichtigt) | set(entwuerfe) | set(anfragen)
    ids |= {t for t, r in live.items() if str(r["status"] or "").lower() in ("besichtigung", "angebot")}
    ids |= {t for t, e in sicherung.items() if set(e["st"]) & {"interessiert", "besichtigung", "kontaktformular", "verloren"}}

    karten = []
    for tid in ids:
        z = live.get(tid)
        b = sicherung.get(tid, {"st": {}, "zeile": None})
        quelle_zeile = z or b["zeile"] or anfragen.get(tid)
        if quelle_zeile is None and tid not in kunden:
            continue
        st = set(b["st"]) | set(historie.get(tid, {}))
        status_live = str(z["status"] or "offen").lower() if z else ""
        if status_live in ("besichtigung", "angebot"):
            st.add(status_live)
        firma = ka._txt(quelle_zeile["firma"]) if quelle_zeile is not None else kunden[tid]["firma"]
        ort = ka._txt(quelle_zeile["ort"]) if quelle_zeile is not None and "ort" in quelle_zeile.keys() else ""
        quelle = ka._txt(quelle_zeile["quelle"]) if quelle_zeile is not None and "quelle" in quelle_zeile.keys() else ""
        herkunft = "Leon" if tid in leon or "leon" in quelle.lower() else ("Website" if tid in anfragen else (quelle.split(" · ")[0] if quelle else "Datenbank"))
        karte = {"id": tid, "firma": firma or "Ohne Firma", "ort": ort, "herkunft": herkunft, "chips": [], "warn": "", "unter": "", "spalte": "", "gruppe": "laufend"}
        chips = karte["chips"]
        angebot_nr = ka._txt(z["angebot_nr"]) if z is not None and "angebot_nr" in z.keys() else ""
        hat_angebot = status_live == "angebot" and bool(angebot_nr)
        zeit = None
        if tid in kunden:
            k = kunden[tid]
            karte["spalte"], zeit = "kunde", k["am"]
            karte["unter"] = "Kunde seit " + (k["am"].strftime("%d.%m.%Y") if k["am"] else "")
            if k["kundennummer"]:
                chips.append({"t": f"Kd.-Nr. {k['kundennummer']}", "f": "g"})
            chips.append({"t": "feste Zeiten + Mitarbeiter eintragen", "f": ""})
        elif tid in vertraege:
            v = vertraege[tid]
            karte["spalte"] = "vertrag"
            if ka._txt(v["gesendet_am"]):
                zeit = ka._zeit(v["gesendet_am"])
                chips += [{"t": "Vertrag gesendet ✓", "f": "g"}, {"t": "Kunde anlegen", "f": "y"}]
            else:
                zeit = ka._zeit(v["erstellt_am"])
                chips += [{"t": "Vertrag vorbereitet", "f": ""}, {"t": "noch nicht gesendet", "f": "r"}]
                karte["warn"] = "y"
        elif hat_angebot or tid in entwuerfe:
            karte["spalte"] = "angebot"
            if hat_angebot:
                n = nummern.get(tid)
                az = (ka._zeit(n["vergeben_am"]) if n else None) or ka._zeit(z["angebot_datum"])
                lage = ka.angebot_lage(conn, tid, az, False, jetzt)
                zeit = az
                netto = ka._zahl(z["angebot_netto"]) if "angebot_netto" in z.keys() else 0
                karte["unter"] = f"AN-{angebot_nr}" + (f" · {ka._euro(netto)} / Monat" if netto else "")
                if lage["status"] == "abgelehnt":
                    karte["gruppe"] = "abgelehnt"
                    chips.append({"t": "abgelehnt · " + (lage["absage"]["grund"] or "ohne Grund"), "f": "r"})
                elif lage["faellig"] and lage["alter"] > ka.NACHFASSEN_FENSTER:  # wie die Arbeitsliste: zu alt für die Erinnerung
                    chips.append({"t": f"{lage['alter']} Tage ohne Antwort", "f": "grau"})
                elif lage["faellig"]:
                    chips.append({"t": f"Nachfassen fällig · {lage['alter']} Tage", "f": "r"})
                    karte["warn"] = "r"
                else:
                    chips.append({"t": f"wartet {lage['alter']} {'Tag' if lage['alter'] == 1 else 'Tage'}" if lage["alter"] else "heute gesendet", "f": "y"})
                if lage["nachgefasst_anzahl"]:
                    chips.append({"t": f"↻ {lage['nachgefasst_anzahl']}× nachgefasst", "f": ""})
            else:
                zeit = entwuerfe[tid]
                karte["unter"] = "Angebot in Arbeit"
                chips.append({"t": "Entwurf", "f": "grau"})
            if quelle.startswith("Direktes Angebot"):
                chips.append({"t": "⚡ ohne Besichtigung", "f": "v"})
        elif "besichtigung" in st or tid in besichtigt:
            karte["spalte"] = "besichtigung"
            bes = ka._zusammenfassung(ka.besichtigung_daten(conn, tid) or {}) if tid in besichtigt else None
            echt = bool(bes and (bes["anzahl_raeume"] or bes["scan"]) and not bes["von_leon"])
            zeit = besichtigt.get(tid) or b["st"].get("besichtigung") or historie.get(tid, {}).get("besichtigung")
            if echt:
                chips.append({"t": "besichtigt " + _tm(besichtigt.get(tid)), "f": "g"})
                if bes["scan"]:
                    chips.append({"t": "KG Scan ✓", "f": "g"})
                chips.append({"t": "Angebot fehlt", "f": "y"})
                karte["warn"] = "y"
                if bes["m2"]:
                    karte["unter"] = f"{bes['m2']:g} m²".replace(".", ",") + (" · " + ", ".join(bes["bereiche"][:3]) if bes["bereiche"] else "")
            else:
                chips.append({"t": "für Besichtigung vorgemerkt", "f": ""})
            if "verloren" in st:
                karte["gruppe"] = "verloren"
        elif st & {"interessiert", "kontaktformular"} or tid in anfragen:
            karte["spalte"] = "interesse"
            zeit = (ka._zeit(z["interessiert_am"]) if z is not None and "interessiert_am" in z.keys() else None) or b["st"].get("interessiert") \
                or historie.get(tid, {}).get("interessiert") or b["st"].get("kontaktformular") or (ka._zeit(anfragen[tid]["erstellt_am"], utc=True) if tid in anfragen else None)
            if tid in anfragen:
                chips.append({"t": "Anfrage Website", "f": ""})
            if "kontaktformular" in st:
                chips.append({"t": "Kontaktformular", "f": ""})
            if "interessiert" in st:
                chips.append({"t": "Interesse " + _tm(zeit), "f": "v"})
            if "verloren" in st:
                karte["gruppe"] = "verloren"
        elif "verloren" in st:
            karte["spalte"], karte["gruppe"] = "interesse", "verloren"
            zeit = b["st"].get("verloren")
        else:
            continue
        if karte["gruppe"] == "verloren":
            chips.insert(0, {"t": "Kein Interesse", "f": "r"})
            karte["warn"] = ""
        karte["datum"] = zeit.strftime("%d.%m.%Y") if zeit else ""
        karte["_s"] = zeit.isoformat() if zeit else ""
        karten.append(karte)

    karten.sort(key=lambda k: k["_s"], reverse=True)
    for k in karten:
        k.pop("_s", None)
    lauf = [k for k in karten if k["gruppe"] == "laufend"]
    warn = {
        "nachfassen": sum(1 for k in lauf if k["spalte"] == "angebot" and k["warn"] == "r"),
        "angebot_fehlt": sum(1 for k in lauf if k["spalte"] == "besichtigung" and k["warn"] == "y"),
        "vertrag_offen": sum(1 for k in lauf if k["spalte"] == "vertrag" and k["warn"] == "y"),
        "kunde_anlegen": sum(1 for k in lauf if k["spalte"] == "vertrag" and not k["warn"]),
    }
    return {"ok": True, "spalten": [{"key": k, "name": n} for k, n in SPALTEN], "karten": karten, "warn": warn,
            "laufend": len(lauf), "stand": jetzt.strftime("%d.%m.%Y · %H:%M")}


def register_kundenweg(app, login_required, get_db_connection):
    @app.route("/akte")
    @login_required
    def kundenweg_seite():
        return render_template("kundenweg.html")

    @app.route("/api/kundenweg")
    @login_required
    def kundenweg_daten():
        conn = get_db_connection()
        try:
            return jsonify(board(conn))
        finally:
            conn.close()
