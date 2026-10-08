# Kemal (4) + Arzu (5): Oktober 2026 nach den festen Zeiten ausrichten (vom Chef so gewünscht).
# Vorher Sicherung. Vorhandene Arbeitstage an festen Tagen bekommen Beginn/Ende/Ort aus dem Plan,
# Krank/Urlaub/Feiertag bleiben, Einträge an anderen Tagen bleiben (nur gemeldet), leere Tage werden ausgefüllt.
import os, sys, sqlite3, datetime
os.chdir("/opt/kg-crm")
sys.path.insert(0, "/opt/kg-crm")
import stundenzettel_auto as sa

MONAT = "2026-10"
conn = sqlite3.connect("data/kg_portal.db", timeout=30)
conn.row_factory = sqlite3.Row
ziel = f"data/kg_portal_vor_kemal_arzu_{datetime.datetime.now():%Y%m%d_%H%M}.db"
sicher = sqlite3.connect(ziel)
conn.backup(sicher)
sicher.close()
print("Sicherung:", ziel)
sa.ensure_tables(conn)

for wid, kontrolle in ((4, "Kemal"), (5, "Arzu")):
    w = conn.execute("SELECT vorname, nachname FROM mitarbeiter WHERE id = ?", (wid,)).fetchone()
    name = f"{w['vorname']} {w['nachname']}" if w else "?"
    if not w or kontrolle.lower() not in name.lower():
        print(f"[{wid}] {name}: NAME PASST NICHT - nichts geändert")
        continue
    if sa._monat_row(conn, wid, MONAT).get("status") == "bestaetigt":
        print(f"[{wid}] {name}: Oktober ist bestätigt/gesperrt - nichts geändert")
        continue
    plan = sa._plan_laden(conn, wid)
    for log in conn.execute("SELECT id, datum, start_time, end_time, place FROM work_logs WHERE worker_id = ? AND datum LIKE ? ORDER BY datum",
                            (wid, MONAT + "%")).fetchall():
        tag = datetime.date.fromisoformat(log["datum"])
        p = plan[sa.WOCHENTAGE[tag.weekday()]]
        alt = f"{log['start_time']}-{log['end_time']} {log['place']}"
        if (log["place"] or "") in sa.SONDER_ORTE:
            print(f"   {tag:%d.%m.} {alt} - bleibt")
        elif not p["aktiv"]:
            print(f"   {tag:%d.%m.} {alt} - kein fester Tag, bleibt")
        elif (log["start_time"], log["end_time"], log["place"]) == (p["start"], p["ende"], p["ort"]):
            print(f"   {tag:%d.%m.} {alt} - stimmt schon")
        else:
            conn.execute("UPDATE work_logs SET start_time = ?, end_time = ?, place = ? WHERE id = ?",
                         (p["start"], p["ende"], p["ort"], log["id"]))
            print(f"   {tag:%d.%m.} {alt} -> {p['start']}-{p['ende']} {p['ort']}")
    conn.commit()
    r = sa.monat_fuellen(conn, wid, MONAT)
    print(f"[{wid}] {name}: {r['neu']} Tage neu, Oktober zusammen {str(r['stunden']).replace('.', ',')} Std.")
conn.close()
