# Startseite: Verlauf je Monat des laufenden Jahres (nur lesend, ändert nichts).
# - Kunden: Bestand am Monatsende (Vertragsbeginn bis Vertragsende; gekündigte fallen im Endmonat weg)
# - Mitarbeiter: Bestand am Monatsende (Eintritt; gelöschte bis zu ihrem letzten Stundenzettel-Monat)
# - Bilanz: Einnahmen (Lexware, wie Jahresübersicht) und gewerbliche Ausgaben je Monat
# - Telefonakquise (Anrufe, Portfolio, Besichtigung, Verträge) je Monat und Angebot-Zahlen
# - Monatstrend: Einnahmen des laufenden Monats je Woche aufsummiert
# Monate nach dem aktuellen Monat sind None (Grafik zeigt dort keine Säule).
import calendar
import datetime
import re


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


def _monatsenden(jahr):
    return [datetime.date(jahr, m, calendar.monthrange(jahr, m)[1]) for m in range(1, 13)]


def startseite_verlauf(conn, now, einnahmen):
    jahr, akt = now.year, now.month
    enden = _monatsenden(jahr)

    # Kunden
    kunden = [0] * 12
    ohne_ende = 0
    for r in conn.execute("SELECT vertrag_beginn, vertrag_ende, vertragsstatus, created_at FROM kunden").fetchall():
        start = _datum(r["vertrag_beginn"]) or _datum(str(r["created_at"] or "")[:10]) or datetime.date.min
        gekuendigt = str(r["vertragsstatus"] or "").strip().lower() == "gekuendigt"
        ende = _datum(r["vertrag_ende"]) if gekuendigt else None
        if gekuendigt and ende is None:
            ohne_ende += 1
            continue
        for i, me in enumerate(enden):
            if start <= me and (ende is None or ende >= me):
                kunden[i] += 1

    # Mitarbeiter
    letzter_monat = {}
    try:
        for r in conn.execute("SELECT worker_id, MAX(monat) AS m FROM stundenzettel_monate GROUP BY worker_id").fetchall():
            letzter_monat[r["worker_id"]] = str(r["m"] or "")
    except Exception:
        pass
    personal = [0] * 12
    for r in conn.execute("SELECT id, eintrittsdatum, status, created_at FROM mitarbeiter").fetchall():
        start = _datum(r["eintrittsdatum"]) or _datum(str(r["created_at"] or "")[:10]) or datetime.date.min
        aktiv = str(r["status"] or "").strip().lower() == "aktiv"
        bis = None
        if not aktiv:
            lm = letzter_monat.get(r["id"], "")
            try:
                j, m = [int(x) for x in lm.split("-")[:2]]
                bis = datetime.date(j, m, calendar.monthrange(j, m)[1])
            except (ValueError, TypeError):
                continue
        for i, me in enumerate(enden):
            if start <= me and (bis is None or bis >= me):
                personal[i] += 1

    # Ausgaben (gewerblich) je Monat nach Datum
    ausgaben = [0.0] * 12
    try:
        for r in conn.execute("SELECT datum, brutto FROM gewerbliche_ausgaben").fetchall():
            d = _datum(r["datum"])
            if d and d.year == jahr:
                try:
                    ausgaben[d.month - 1] += abs(float(r["brutto"] or 0))
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass

    # Telefonakquise je Monat (wie die Monatszahlen der Datenbank: Einträge im Status-Verlauf)
    def je_monat(sql, params=()):
        werte = [0] * 12
        try:
            for r in conn.execute(sql, params).fetchall():
                d = _datum(str(r[0] or "")[:10])
                if d and d.year == jahr:
                    werte[d.month - 1] += 1
        except Exception:
            pass
        return werte

    anrufe = je_monat("SELECT datum FROM tagesliste_status_history WHERE status = 'angerufen'")
    besichtigungen = je_monat("SELECT datum FROM tagesliste_status_history WHERE status = 'besichtigung'")
    portfolio = je_monat("SELECT datum FROM tagesliste_status_history WHERE status = 'kontaktformular'")
    for i, v in enumerate(je_monat("SELECT portfolio_am FROM neue_anfragen WHERE COALESCE(portfolio_am, '') != ''")):
        portfolio[i] += v
    vertraege = [0] * 12
    for r in conn.execute("SELECT vertrag_beginn, created_at FROM kunden").fetchall():
        d = _datum(r["vertrag_beginn"]) or _datum(str(r["created_at"] or "")[:10])
        if d and d.year == jahr:
            vertraege[d.month - 1] += 1

    # Angebote (Tagesliste): gesendet = mit Angebot-Nummer; bestätigt = danach Kunde, nicht bestätigt = danach „verloren“;
    # wartend = gesendet und noch ohne Entscheidung (egal welcher Status sonst, z. B. offen/besichtigung)
    angebot = {"gesendet": 0, "wartend": 0, "bestaetigt": 0, "nicht": 0, "wartend_netto": 0.0}
    angebot_liste = []
    try:
        for r in conn.execute("SELECT firma, plz, ort, status, angebot_nr, angebot_datum, angebot_netto FROM tagesliste_leads "
                              "WHERE COALESCE(angebot_nr, '') != '' ORDER BY id DESC").fetchall():
            angebot["gesendet"] += 1
            st = str(r["status"] or "").strip().lower()
            try:
                netto = float(r["angebot_netto"] or 0)
            except (TypeError, ValueError):
                netto = 0.0
            if st in ("kunde", "kunden"):
                angebot["bestaetigt"] += 1
                stand = "bestaetigt"
            elif st == "verloren":
                angebot["nicht"] += 1
                stand = "nicht"
            else:
                angebot["wartend"] += 1
                angebot["wartend_netto"] += netto
                stand = "wartend"
            angebot_liste.append({"nr": re.sub(r"(?i)^AN-", "", str(r["angebot_nr"] or "").strip()), "firma": str(r["firma"] or ""),
                                  "ort": " ".join(x for x in (str(r["plz"] or ""), str(r["ort"] or "")) if x),
                                  "datum": str(r["angebot_datum"] or ""), "netto": round(netto, 2), "stand": stand})
    except Exception:
        pass

    # Monatstrend: Einnahmen des laufenden Monats aufsummiert (Start, bis 7., 14., 21., heute); spätere Wochen leer
    trend_tage = [0.0] * 32
    try:
        for r in conn.execute("SELECT datum, brutto FROM lexware_cache").fetchall():
            d = _datum(r["datum"])
            if d and d.year == jahr and d.month == akt:
                try:
                    trend_tage[d.day] += float(r["brutto"] or 0)
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass
    heute = now.day
    monatstrend = [0.0] + [round(sum(trend_tage[:t + 1]), 2) if t <= heute else None for t in (7, 14, 21)] + \
                  [round(float(einnahmen[akt - 1] or 0), 2)]

    def bis_heute(werte, runden=None):
        return [(round(v, runden) if runden is not None else v) if i < akt else None for i, v in enumerate(werte)]

    return {
        "kunden_bestand_verisi": bis_heute(kunden),
        "personal_bestand_verisi": bis_heute(personal),
        "bilanz_einnahmen_verisi": bis_heute(list(einnahmen), 2),
        "bilanz_ausgaben_verisi": bis_heute(ausgaben, 2),
        "kunden_gekuendigt_ohne_ende": ohne_ende,
        "akquise_anrufe_verisi": bis_heute(anrufe),
        "akquise_besichtigung_verisi": bis_heute(besichtigungen),
        "akquise_portfolio_verisi": bis_heute(portfolio),
        "akquise_vertraege_verisi": bis_heute(vertraege),
        "akquise_monat": {"anrufe": anrufe[akt - 1], "portfolio": portfolio[akt - 1],
                          "besichtigung": besichtigungen[akt - 1], "vertraege": vertraege[akt - 1]},
        "angebot_zahlen": angebot,
        "angebot_liste": angebot_liste,
        "monatstrend_echt": monatstrend,
        "einnahmen_monat": float(einnahmen[akt - 1] or 0),
    }
