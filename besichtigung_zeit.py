# Besichtigung: Zeitberechnung mit allen Details (nur rechnen, speichert nichts).
# Flächen je Bereich + Elemente + Sonstiges (Schreibtische, Elektrogeräte, Geschirr, Lampen, Regale),
# Fitness, Treppenhaus, Fenster/Jalousien, Grund-/Bauendreinigung (einmalig) und Rüstzeit.
# Alle Richtwerte stehen hier oben und können später angepasst werden.
# Die Angebots-Berechnung (app2.angebot_calculate_from_besichtigung) bleibt unverändert.
import math
import re

from flask import jsonify, request

MONAT = 4.33  # Wochen pro Monat

# m² pro Stunde
LEISTUNGSWERT = {
    "buero": 170, "wc": 55, "kueche": 110, "flur": 250, "dusche": 60,
    "fenster": 30, "jalousien": 15, "grund": 30, "bauend": 25,
}
BODEN_FAKTOR = {"teppich": 1.15}

# Minuten je Stück und Einsatz
MIN_WC, MIN_URINAL, MIN_WB = 2.0, 1.5, 1.0
MIN_MUELL = 0.5
MIN_SCHREIBTISCH = 1.0
MIN_GERAET = {"pc": 0.5, "bildschirm": 0.5, "telefon": 0.3, "drucker": 1.0}
MIN_GESCHIRR = {"einräumen": 5.0, "ausräumen": 5.0, "ein- und ausräumen": 10.0}
MIN_STEHLAMPE, MIN_REGAL = 1.0, 2.0
MIN_ETAGE, MIN_KELLER, MIN_ETAGE_FENSTER = 12.0, 10.0, 10.0
MIN_FITNESS = {"geräte desinfizieren": 20.0, "matten desinfizieren": 5.0, "kontaktflächen desinfizieren": 5.0}
MIN_SPIEGEL = 10.0
MIN_RUESTZEIT = 10.0  # Vorbereitung / Material je Einsatz

VERSCHMUTZUNG = {"leicht": 1.0, "mittel": 1.25, "stark": 1.5, "sehr stark": 1.8}
MOEBEL = {"leerstehend": 1.0, "teilmöbliert": 1.1, "vollmöbliert": 1.2}

FARBE = {
    "buero": "#2563eb", "wc": "#ef4444", "kueche": "#16a34a", "flur": "#f59e0b", "dusche": "#0891b2",
    "fenster": "#0ea5e9", "jalousien": "#6366f1", "extras": "#7c3aed", "treppe": "#64748b",
    "fitness": "#db2777", "ruest": "#94a3b8", "einmalig": "#0f172a",
}


def _zahl(text):
    m = re.search(r"(\d+(?:[.,]\d+)?)", str(text or ""))
    return float(m.group(1).replace(",", ".")) if m else 0.0


def einsaetze_pro_monat(text, tage_pro_woche=0):
    """Häufigkeit → Einsätze pro Monat; None = einmalig."""
    t = str(text or "").strip().lower()
    if not t or t in ("keine", "bitte wählen", "nein", "ohne"):
        return 0.0
    if "einmalig" in t:
        return None
    if "täglich" in t and "14" not in t:
        return (tage_pro_woche or 5) * MONAT
    if "14-tägig" in t or "14 tägig" in t:
        return MONAT / 2
    m = re.search(r"(\d+)\s*x\s*wöchentlich", t)
    if m:
        return int(m.group(1)) * MONAT
    m = re.search(r"(\d+)\s*x\s*monatlich", t)
    if m:
        return float(m.group(1))
    m = re.search(r"alle\s*(\d+)\s*monate", t)
    if m:
        return 1.0 / max(1, int(m.group(1)))
    m = re.search(r"(\d+)\s*x\s*jährlich", t)
    if m:
        return int(m.group(1)) / 12.0
    return 0.0


def bereich_schluessel(text):
    t = str(text or "").lower()
    for wort, key in (("jalousie", "jalousien"), ("fenster", "fenster"), ("dusche", "dusche"), ("wc", "wc"), ("sanitär", "wc"),
                      ("küche", "kueche"), ("flur", "flur"), ("büro", "buero"), ("besprechung", "buero"), ("server", "buero")):
        if wort in t:
            return key
    return ""


def _feld(felder, *woerter):
    """erstes Feld, dessen Beschriftung alle Wörter enthält → (values, active)"""
    for f in felder:
        label = str(f.get("label") or "").lower()
        if all(w in label for w in woerter):
            return [str(v) for v in (f.get("values") or [])], [str(a) for a in (f.get("active") or [])]
    return [], []


def _wert(felder, *woerter):
    werte, _ = _feld(felder, *woerter)
    return werte[0] if werte else ""


def berechnen(daten):
    zeilen, einmalig = [], []
    tage = int(daten.get("einsatztage") or 0)
    elemente = daten.get("elemente") or {}
    tische = int(_zahl(elemente.get("arbeitstische")))
    pcs = int(_zahl(elemente.get("pc_monitor")))
    muell = int(_zahl(elemente.get("muellbehaelter")))

    def zeile(name, key, menge, minuten, einsaetze, gruppe="Flächen", gid=None):
        if einsaetze is None:
            einmalig.append({"name": name, "farbe": FARBE.get(key, "#0f172a"), "menge": menge, "stunden": round(minuten / 60, 2), "gid": gid})
            return
        if minuten <= 0 or einsaetze <= 0:
            return
        zeilen.append({
            "gruppe": gruppe, "name": name, "key": key, "farbe": FARBE.get(key, "#64748b"), "menge": menge,
            "min_einsatz": round(minuten, 1), "einsaetze": round(einsaetze, 2), "std_monat": round(minuten * einsaetze / 60, 2), "gid": gid,
        })

    # Flächen je Gruppe (Büro, WC, Küche, Flur, Dusche, Fenster, Jalousien)
    haupt = 0.0
    for g in daten.get("gruppen") or []:
        key = bereich_schluessel(g.get("bereich"))
        m2 = float(g.get("m2") or 0)
        if not key or m2 <= 0:
            continue
        e = einsaetze_pro_monat(g.get("haeufigkeit"), tage)
        faktor = BODEN_FAKTOR.get(str(g.get("boden") or "").lower(), 1.0)
        minuten = m2 / LEISTUNGSWERT[key] * 60 * faktor
        menge = f"{m2:.2f} m²".replace(".", ",")
        if key == "wc":
            objekte = int(g.get("wc") or 0) * MIN_WC + int(g.get("urinal") or 0) * MIN_URINAL + int(g.get("wb") or 0) * MIN_WB
            minuten += objekte
            if objekte:
                menge += f" · {int(g.get('wc') or 0)} WC / {int(g.get('urinal') or 0)} Urinal / {int(g.get('wb') or 0)} WB"
        name = str(g.get("bereich") or key)
        if g.get("leistung") and key not in ("fenster", "jalousien"):
            name = f"{name} · {g.get('leistung')}"
        zeile(name, key, menge, minuten, e, "Flächen", g.get("gid"))
        if e and key in ("buero", "wc", "kueche", "flur", "dusche"):
            haupt = max(haupt, e)

    # Elemente: Müll bei jedem Einsatz
    if muell and haupt:
        zeile("Müllbehälter leeren", "extras", f"{muell} Stück", muell * MIN_MUELL, haupt, "Extras")

    # Sonstiges
    so = daten.get("sonstiges") or []
    if _wert(so, "schreibtisch") == "Ja":
        e = einsaetze_pro_monat(_wert_nach(so, "schreibtisch"), tage)
        anzahl = tische or 0
        zeile("Schreibtische reinigen", "extras", f"{anzahl} Tische", anzahl * MIN_SCHREIBTISCH, e, "Extras")
    if _wert(so, "elektro") == "Mit":
        _, geraete = _feld(so, "welche geräte")
        e = einsaetze_pro_monat(_wert(so, "häufig", "elektro"), tage)
        minuten, teile = 0.0, []
        for gname in geraete:
            k = gname.strip().lower()
            if k in ("pc", "bildschirm"):
                n = pcs
            elif k == "telefon":
                n = tische
            elif k == "drucker":
                n = max(1, math.ceil(tische / 10)) if tische else 1
            else:
                continue
            minuten += n * MIN_GERAET[k]
            teile.append(f"{n} {gname}")
        zeile("Elektrogeräte", "extras", " · ".join(teile) or "–", minuten, e, "Extras")
    geschirr = _wert(so, "geschirr").lower()
    if geschirr in MIN_GESCHIRR:
        zeile("Geschirrspüler", "extras", geschirr.capitalize(), MIN_GESCHIRR[geschirr], einsaetze_pro_monat(_wert(so, "häufig", "geschirr"), tage), "Extras")
    for wort, name, minuten in (("stehlampe", "Stehlampen", MIN_STEHLAMPE), ("regal", "Regale", MIN_REGAL)):
        if _wert(so, wort) == "Ja":
            werte, _ = _feld(so, wort)
            n = int(_zahl(_nach(so, wort, "stück"))) or 1
            zeile(name, "extras", f"{n} Stück", n * minuten, einsaetze_pro_monat(_nach(so, wort, "häufig"), tage), "Extras")

    # Leistungs-spezifische Felder (Fitness, Treppenhaus, Grund-/Bauendreinigung)
    for teil in daten.get("leistungen_felder") or []:
        titel = str(teil.get("leistung") or "")
        felder = teil.get("felder") or []
        t = titel.lower()
        if "fitness" in t:
            for label, minuten in MIN_FITNESS.items():
                if _wert(felder, label.split()[0]) == "Ja" and haupt:
                    zeile(label.capitalize(), "fitness", "je Einsatz", minuten, haupt, "Extras")
            if _wert(felder, "spiegel", "vorhanden") == "Ja":
                zeile("Spiegel reinigen", "fitness", "alle Spiegel", MIN_SPIEGEL, einsaetze_pro_monat(_wert(felder, "häufig", "spiegel"), tage), "Extras")
        elif "treppenhaus" in t:
            start = _wert(felder, "etage")
            bis = _wert(felder, "bis")
            n_bis = int(_zahl(bis)) if bis and bis != "Keine" else 0
            etagen = (n_bis + 1) if n_bis else 1
            if start == "Keller":
                etagen += 1
            e = einsaetze_pro_monat(_wert(felder, "wie häufig"), tage)
            zeile("Treppenhaus", "treppe", f"{etagen} Etagen", etagen * MIN_ETAGE, e, "Flächen")
            if _wert(felder, "keller", "fegen") == "Ja":
                zeile("Keller fegen", "treppe", "Keller", MIN_KELLER, einsaetze_pro_monat(_wert(felder, "häufig", "keller"), tage), "Extras")
            if _wert(felder, "fensterreinigung") == "Ja":
                zeile("Treppenhaus-Fenster", "fenster", f"{etagen} Etagen", etagen * MIN_ETAGE_FENSTER, einsaetze_pro_monat(_wert(felder, "häufig", "fenster"), tage), "Extras")
        elif "grundreinigung" in t or "bauendreinigung" in t:
            bau = "bauend" in t
            m2 = float(daten.get("flaeche_gesamt") or 0)
            faktor = VERSCHMUTZUNG.get(_wert(felder, "bauschmutz" if bau else "verschmutzung").lower(), 1.0)
            faktor *= MOEBEL.get(_wert(felder, "möbelzustand").lower(), 1.0)
            if _wert(felder, "möbel verrücken") == "Ja":
                faktor *= 1.15
            if m2 > 0:
                minuten = m2 / LEISTUNGSWERT["bauend" if bau else "grund"] * 60 * faktor
                einmalig.append({"name": titel, "farbe": FARBE["einmalig"], "menge": f"{m2:.2f} m²".replace(".", ","), "stunden": round(minuten / 60, 2)})
            else:
                einmalig.append({"name": titel, "farbe": FARBE["einmalig"], "menge": "Fläche fehlt", "stunden": 0})

    # Rüstzeit je Einsatz
    if haupt:
        zeile("Rüstzeit (Vorbereitung, Material)", "ruest", "je Einsatz", MIN_RUESTZEIT, haupt, "Extras")

    std = sum(z["std_monat"] for z in zeilen)
    return {
        "zeilen": zeilen,
        "einmalig": einmalig,
        "std_monat": round(std, 2),
        "std_einmalig": round(sum(e["stunden"] for e in einmalig), 2),
        "einsaetze_monat": round(haupt, 2),
    }


def _nach(felder, wort, unter):
    """Feld nach dem Feld mit `wort` (z. B. Stückzahl / Wie häufig in derselben Zeile)"""
    gefunden = False
    for f in felder:
        label = str(f.get("label") or "").lower()
        if wort in label:
            gefunden = True
            continue
        if gefunden and unter in label:
            werte = f.get("values") or []
            return str(werte[0]) if werte else ""
        if gefunden and label and unter not in label and not label.startswith("wie") and "stück" not in label:
            break
    return ""


def _wert_nach(felder, wort):
    return _nach(felder, wort, "häufig")


def register_besichtigung_zeit(app, login_required, stundensatz):

    @app.route("/api/besichtigung/zeitberechnung", methods=["POST"])
    @login_required
    def besichtigung_zeitberechnung():
        daten = request.get_json(silent=True) or {}
        try:
            ergebnis = berechnen(daten)
        except Exception as exc:
            return jsonify({"ok": False, "message": str(exc)})
        satz = float(stundensatz() or 0)
        ergebnis.update({
            "ok": True,
            "stundensatz": satz,
            "netto_monat": round(ergebnis["std_monat"] * satz, 2),
            "netto_einmalig": round(ergebnis["std_einmalig"] * satz, 2),
        })
        return jsonify(ergebnis)
