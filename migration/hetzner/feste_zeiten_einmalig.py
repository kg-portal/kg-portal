# Feste Zeiten (vom Chef bestätigt) speichern, monatliche Extras anlegen, Oktober 2026 ausfüllen.
# Vorher: Sicherung der Datenbank. Prüft je Mitarbeiter den Namen; passt er nicht, wird nichts geschrieben.
import os, sys, json, sqlite3, datetime, unicodedata
os.chdir("/opt/kg-crm")
sys.path.insert(0, "/opt/kg-crm")
import stundenzettel_auto as sa

MONAT = "2026-10"
D, M, F = "Duisburg", "Duisburg Mitte", "Wanheimerort"
def t(start, ende, ort): return {"aktiv": True, "start": start, "ende": ende, "ort": ort}
LISTE = [
    # (id, Name zur Kontrolle, Plan, Extras [(regel, stunden, start, ort)])
    (37, "Atanas", {k: t("18:00", "19:45", D) for k in ("mo", "di", "mi", "do", "fr")}, []),
    (34, "Seher", {"mi": t("17:00", "19:00", "Neuenkamp"), "sa": t("10:00", "14:00", "Neuenkamp")}, []),
    (32, "Valbone", {"mo": t("18:00", "19:00", "Düsseldorf"), "mi": t("18:00", "19:00", "Düsseldorf"), "fr": t("18:00", "19:30", "Düsseldorf")}, []),
    (33, "Yemen", {"do": t("14:00", "16:00", "Hamborn")}, []),
    (29, "Birgül", {"mi": t("17:00", "18:45", F), "sa": t("12:00", "14:00", F)}, []),
    (27, "Kebire", {"sa": t("15:00", "19:00", "Großenbaum")}, []),
    (3, "Hatice", {"do": t("17:00", "18:00", F), "sa": t("10:00", "14:00", F)},
        [("erster_arbeitstag", 0.75, None, None), ("samstag_mitte", 3, "12:00", F)]),
    (6, "Pedrie", {"mo": t("15:00", "17:00", D), "di": t("15:00", "17:00", D), "mi": t("15:00", "17:00", D),
                   "do": t("17:00", "19:00", D), "fr": t("15:00", "17:00", D)}, []),
    (8, "Serpil", {"mi": t("15:30", "19:30", D), "fr": t("13:00", "17:30", D)}, []),
    (9, "Semra", {"mi": t("17:00", "19:00", "Ruhrort")}, []),
    (10, "Tülay", {"fr": t("17:00", "19:00", M)}, []),
    (11, "Gülbahar", {"mo": t("13:00", "14:30", "Meiderich / Beeck"), "mi": t("13:00", "14:30", "Meiderich / Beeck"),
                      "fr": t("13:00", "15:00", "Meiderich / Beeck")}, []),
    (13, "Ayten", {"mo": t("18:00", "19:30", D), "di": t("18:00", "20:00", D), "mi": t("18:00", "19:30", D),
                   "do": t("18:00", "19:30", D), "fr": t("18:00", "19:30", D)}, [("erster_arbeitstag", 0.5, None, None)]),
    (14, "Büsra", {"di": t("14:00", "17:00", "Großenbaum")}, []),
    (16, "Mustafa", {"mi": t("17:00", "21:00", "Moers")}, []),
    (17, "Adnan", {"mo": t("17:00", "19:00", D), "mi": t("17:00", "19:00", D), "fr": t("17:00", "19:00", D)}, []),
    (19, "Nilüfer", {"di": t("17:30", "21:00", M), "do": t("19:00", "21:00", M)}, [("erster_arbeitstag", 1, None, None)]),
    (20, "Marica", {"di": t("18:00", "21:30", M), "fr": t("16:30", "21:30", M)}, []),
    (23, "Efsa", {"mi": t("17:00", "18:30", F), "fr": t("17:00", "21:00", F)}, []),
    (26, "Emine", {k: t("18:00", "19:30", D) for k in ("mo", "di", "mi", "do", "fr")}, [("erster_arbeitstag", 0.5, None, None)]),
    # Kemal + Arzu: neue Arbeit Mo/Mi (2,5 Std.) geteilt – Kemal 1:00, Arzu 1:30; Kemals 2. Samstagsarbeit (1:45) am selben Tag
    (4, "Kemal", {"mo": t("17:00", "18:00", "Ruhrort"), "di": t("17:00", "18:45", "Rheinhausen"),
                  "mi": t("17:00", "18:00", "Ruhrort"), "do": t("17:00", "18:45", "Rheinhausen"),
                  "sa": t("15:00", "18:30", "Rheinhausen")}, []),
    (5, "Arzu", {"mo": t("17:00", "18:30", "Ruhrort"), "di": t("17:00", "18:45", "Rheinhausen"),
                 "mi": t("17:00", "18:30", "Ruhrort"), "do": t("17:00", "18:45", "Rheinhausen"),
                 "sa": t("15:00", "17:15", "Rheinhausen")}, []),
]
# Diese bekommen nur die festen Zeiten; Oktober wird bei ihnen NICHT ausgefüllt (haben schon Einträge, neue Arbeit erst später)
NICHT_FUELLEN = {4, 5}

def norm(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "").lower()) if unicodedata.category(c) != "Mn")

conn = sqlite3.connect("data/kg_portal.db", timeout=30)
conn.row_factory = sqlite3.Row
ziel = f"data/kg_portal_vor_feste_zeiten_{datetime.datetime.now():%Y%m%d_%H%M}.db"
sicher = sqlite3.connect(ziel)
conn.backup(sicher)
sicher.close()
print("Sicherung:", ziel)
sa.ensure_tables(conn)

jetzt = datetime.datetime.now().isoformat(timespec="seconds")
gesamt_neu = 0
for wid, kontrolle, plan, extras in LISTE:
    w = conn.execute("SELECT id, vorname, nachname, status FROM mitarbeiter WHERE id = ?", (wid,)).fetchone()
    name = f"{w['vorname']} {w['nachname']}" if w else "?"
    if not w or norm(kontrolle) not in norm(name):
        print(f"[{wid}] {name}: NAME PASST NICHT ({kontrolle}) - nichts geschrieben")
        continue
    if w["status"] != "aktiv":
        print(f"[{wid}] {name}: nicht aktiv - nichts geschrieben")
        continue
    sauber = sa._plan_pruefen(plan)
    conn.execute(
        "INSERT INTO stundenzettel_vorlagen (worker_id, plan_json, aktualisiert_am) VALUES (?, ?, ?) "
        "ON CONFLICT(worker_id) DO UPDATE SET plan_json = excluded.plan_json, aktualisiert_am = excluded.aktualisiert_am",
        (wid, json.dumps(sauber, ensure_ascii=False), jetzt),
    )
    conn.execute("DELETE FROM stundenzettel_extras WHERE worker_id = ?", (wid,))
    for regel, stunden, start, ort in extras:
        conn.execute("INSERT INTO stundenzettel_extras (worker_id, regel, stunden, start, ort, notiz) VALUES (?, ?, ?, ?, ?, ?)",
                     (wid, regel, stunden, start, ort, "vom Chef bestätigt " + jetzt[:10]))
    conn.commit()
    std = sum(sa._stunden(p["start"], p["ende"]) for p in sauber.values() if p["aktiv"])
    vorher = conn.execute("SELECT COUNT(*) FROM work_logs WHERE worker_id = ? AND datum LIKE ?", (wid, MONAT + "%")).fetchone()[0]
    if wid in NICHT_FUELLEN:
        print(f"[{wid}] {name}: {str(std).replace('.', ',')} Std./Woche gespeichert | Oktober nicht ausgefüllt ({vorher} Tage schon da)")
        continue
    try:
        r = sa.monat_fuellen(conn, wid, MONAT)
        gesamt_neu += r["neu"]
        info = f"Oktober: {vorher} schon da, {r['neu']} Tage neu, zusammen {str(r['stunden']).replace('.', ',')} Std."
        hinweise = [x for x in r["uebersprungen"] if x.startswith("Extra")]
        if hinweise:
            info += " | " + "; ".join(hinweise)
    except ValueError as exc:
        info = f"Oktober nicht ausgefüllt: {exc}"
    print(f"[{wid}] {name}: {str(std).replace('.', ',')} Std./Woche, Extras {len(extras)} | {info}")

print("Fertig. Neue Tage im Oktober zusammen:", gesamt_neu)
conn.close()
