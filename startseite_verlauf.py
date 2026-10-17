# Startseite: Verlauf je Monat des laufenden Jahres (nur lesend, ändert nichts).
# - Kunden: Bestand am Monatsende (Vertragsbeginn bis Vertragsende; gekündigte fallen im Endmonat weg)
# - Mitarbeiter: Bestand am Monatsende (Eintritt; gelöschte bis zu ihrem letzten Stundenzettel-Monat)
# - Bilanz: Einnahmen (Lexware, wie Jahresübersicht) und gewerbliche Ausgaben je Monat
# Monate nach dem aktuellen Monat sind None (Grafik zeigt dort keine Säule).
import calendar
import datetime


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

    def bis_heute(werte, runden=None):
        return [(round(v, runden) if runden is not None else v) if i < akt else None for i, v in enumerate(werte)]

    return {
        "kunden_bestand_verisi": bis_heute(kunden),
        "personal_bestand_verisi": bis_heute(personal),
        "bilanz_einnahmen_verisi": bis_heute(list(einnahmen), 2),
        "bilanz_ausgaben_verisi": bis_heute(ausgaben, 2),
        "kunden_gekuendigt_ohne_ende": ohne_ende,
    }
