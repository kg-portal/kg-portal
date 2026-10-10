#!/bin/bash
# =====================================================
# KG Daten (ChatGPT): KG Business freischalten – Firmen suchen/anlegen/ändern/löschen (Papierkorb)
# und Leon-Business-Kampagnen als Entwurf (gestartet wird nie).
# - Erzeugt einmalig ein eigenes geheimes Token (wird nie angezeigt, getrennt vom CRM-Token) und trägt es ein:
#     KG Business: <Arbeitsordner von kg-business>/tokenlar.env   KG_MCP_BUSINESS_TOKEN
#     KG Daten:    /opt/kg-mcp-geheim/env.json                    KG_MCP_BUSINESS_TOKEN, KG_BUSINESS_INTERN_URL
# - Findet den Port von KG Business selbst (laufender Dienst, sonst APP_PORT).
# - Startet KG Business nur neu, wenn gerade KEINE Kampagne aktiv ist (laufende Anrufe bleiben unberührt).
# - Mehrmals ausführbar. Zurücknehmen: KG_MCP_BUSINESS_TOKEN aus tokenlar.env löschen, kg-business neu starten.
# =====================================================
set -u

DIENST=kg-business
MCP_ENV=/opt/kg-mcp-geheim/env.json
JETZT=$(date +%Y%m%d_%H%M%S)

ok()     { echo "OK      $*"; }
fehler() { echo "FEHLER  $*"; exit 1; }

[ "$(id -u)" = "0" ] || fehler "bitte als root ausführen"
[ -f "$MCP_ENV" ] || fehler "$MCP_ENV fehlt (zuerst kg_mcp/einrichten.sh)"
ORDNER=$(systemctl show -p WorkingDirectory --value "$DIENST" 2>/dev/null)
[ -n "$ORDNER" ] && [ -d "$ORDNER" ] || ORDNER=/opt/kg-business
[ -f "$ORDNER/app.py" ] || fehler "KG Business nicht gefunden ($ORDNER/app.py)"
BUS_ENV=$ORDNER/tokenlar.env
DB=$ORDNER/data/kg_business_voice.db
grep -q "mcp_business_bp" "$ORDNER/app.py" || fehler "Neuer Code fehlt – zuerst: cd $ORDNER && git pull --ff-only origin kg-business-green-shell"

# Keine aktive Kampagne? (Neustart würde laufende Anrufe trennen)
AKTIV=$(python3 - "$DB" <<'PY'
import sqlite3, sys
try:
    c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True, timeout=10)
    print(c.execute("SELECT COUNT(*) FROM campaigns WHERE status = 'Aktiv'").fetchone()[0])
except Exception:
    print(0)
PY
)
[ "$AKTIV" = "0" ] || fehler "In KG Business läuft gerade eine Kampagne – erst pausieren/fertig werden lassen, dann nochmal ausführen."

# Token in tokenlar.env (fehlt es, einmalig erzeugen; vorher Sicherung, Rechte bleiben)
KG_T=$(python3 - "$BUS_ENV" "$BUS_ENV.vor_mcp_$JETZT" <<'PY'
import os, secrets, shutil, sys
pfad, sicherung = sys.argv[1], sys.argv[2]
zeilen = open(pfad, encoding="utf-8").read().splitlines() if os.path.exists(pfad) else []
for z in zeilen:
    if z.strip().startswith("KG_MCP_BUSINESS_TOKEN=") and z.split("=", 1)[1].strip():
        print("DA:" + z.split("=", 1)[1].strip().strip('"').strip("'"))
        sys.exit(0)
if os.path.exists(pfad):
    shutil.copy2(pfad, sicherung)
token = secrets.token_urlsafe(32)
zeilen.append("KG_MCP_BUSINESS_TOKEN=" + token)
neu = pfad + ".neu"
with open(neu, "w", encoding="utf-8") as f:
    f.write("\n".join(zeilen) + "\n")
os.chmod(neu, (os.stat(pfad).st_mode & 0o777) if os.path.exists(pfad) else 0o600)
if os.path.exists(pfad):
    st = os.stat(pfad)
    os.chown(neu, st.st_uid, st.st_gid)
os.replace(neu, pfad)
print("NEU:" + token)
PY
) || fehler "$BUS_ENV konnte nicht gelesen/geschrieben werden"
case "$KG_T" in
    NEU:*) ok "Token in KG Business hinterlegt (Sicherung: $BUS_ENV.vor_mcp_$JETZT)" ;;
    DA:*)  ok "Token in KG Business schon vorhanden" ;;
    *)     fehler "Token konnte nicht gelesen werden" ;;
esac
KG_T=${KG_T#*:}
export KG_T

systemctl restart "$DIENST" && sleep 6
[ "$(systemctl is-active "$DIENST")" = "active" ] || fehler "KG Business läuft nicht – journalctl -u $DIENST -n 50"
ok "KG Business neu gestartet"

# Port: was der Dienst wirklich belegt; sonst APP_PORT aus tokenlar.env; sonst 5000
PID=$(systemctl show -p MainPID --value "$DIENST")
PORT=""
for p in $PID $(pgrep -P "$PID" 2>/dev/null); do
    PORT=$(ss -ltnpH 2>/dev/null | grep "pid=$p," | grep -o '127\.0\.0\.1:[0-9][0-9]*\|0\.0\.0\.0:[0-9][0-9]*\|\*:[0-9][0-9]*\|\[::\]:[0-9][0-9]*' | head -1 | grep -o '[0-9]*$')
    [ -n "$PORT" ] && break
done
[ -n "$PORT" ] || PORT=$(grep -s '^APP_PORT=' "$BUS_ENV" | tail -1 | cut -d= -f2 | tr -dc '0-9')
[ -n "$PORT" ] || PORT=5000
export URL="http://127.0.0.1:$PORT"

python3 - "$MCP_ENV" <<'PY' || fehler "env.json konnte nicht geschrieben werden"
import json, os, sys
pfad = sys.argv[1]
with open(pfad, encoding="utf-8") as f:
    daten = json.load(f)
daten["KG_MCP_BUSINESS_TOKEN"] = os.environ["KG_T"]
daten["KG_BUSINESS_INTERN_URL"] = os.environ["URL"]
neu = pfad + ".neu"
with open(neu, "w", encoding="utf-8") as f:
    json.dump(daten, f, ensure_ascii=False, indent=2)
os.chmod(neu, 0o600)
os.replace(neu, pfad)
PY
ok "Token und Adresse ($URL) in KG Daten hinterlegt"

systemctl restart kg-mcp && sleep 2
[ "$(systemctl is-active kg-mcp)" = "active" ] || fehler "KG Daten läuft nicht – journalctl -u kg-mcp -n 50"
ok "KG Daten neu gestartet"

python3 - <<'PY'
import json, os, urllib.request, urllib.error
anfrage = urllib.request.Request(os.environ["URL"] + "/internal/mcp/business", data=b'{"aktion":"status"}', method="POST",
                                 headers={"Content-Type": "application/json", "X-KG-MCP-Token": os.environ["KG_T"]})
try:
    with urllib.request.urlopen(anfrage, timeout=30) as r:
        daten = json.loads(r.read().decode())
    print(f"OK      Verbindung KG Daten ↔ KG Business: {daten.get('firmen')} Firmen in KG Business")
except urllib.error.HTTPError as exc:
    print(f"FEHLER  KG Business antwortet mit HTTP {exc.code} – Code gezogen und Dienst neu gestartet?")
except Exception as exc:
    print(f"FEHLER  KG Business nicht erreichbar: {exc}")
PY
