#!/bin/bash
# =====================================================
# KG Daten (ChatGPT): Leon-Kampagnen als Entwurf anlegen freischalten
# - Erzeugt einmalig ein geheimes Token (wird nie angezeigt) und trägt es ein:
#     CRM:      /opt/kg-crm/tokenlar.env      KG_MCP_TOKEN=…
#     KG Daten: /opt/kg-mcp-geheim/env.json   KG_MCP_CRM_TOKEN, KG_CRM_URL
# - Startet CRM und KG Daten neu und prüft die Verbindung.
# - Mehrmals ausführbar. Zurücknehmen: KG_MCP_TOKEN aus tokenlar.env löschen, CRM neu starten.
# =====================================================
set -u

CRM_DIR=/opt/kg-crm
CRM_ENV=$CRM_DIR/tokenlar.env
MCP_ENV=/opt/kg-mcp-geheim/env.json
CRM_URL=http://127.0.0.1:8803
JETZT=$(date +%Y%m%d_%H%M%S)

ok()     { echo "OK      $*"; }
fehler() { echo "FEHLER  $*"; exit 1; }

[ "$(id -u)" = "0" ] || fehler "bitte als root ausführen"
[ -f "$CRM_ENV" ] || fehler "$CRM_ENV fehlt"
[ -f "$MCP_ENV" ] || fehler "$MCP_ENV fehlt (zuerst kg_mcp/einrichten.sh)"

KG_T=$(grep -E '^KG_MCP_TOKEN=' "$CRM_ENV" | tail -1 | cut -d= -f2-)
if [ -z "$KG_T" ]; then
    cp -a "$CRM_ENV" "$CRM_ENV.vor_mcp_$JETZT"
    KG_T=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
    printf '\nKG_MCP_TOKEN=%s\n' "$KG_T" >> "$CRM_ENV"
    ok "Token im CRM hinterlegt (Sicherung: $CRM_ENV.vor_mcp_$JETZT)"
else
    ok "Token im CRM schon vorhanden"
fi
export KG_T

python3 - "$MCP_ENV" "$CRM_URL" <<'PY' || fehler "env.json konnte nicht geschrieben werden"
import json, os, sys
pfad, url = sys.argv[1], sys.argv[2]
with open(pfad, encoding="utf-8") as f:
    daten = json.load(f)
daten["KG_MCP_CRM_TOKEN"] = os.environ["KG_T"]
daten.setdefault("KG_CRM_URL", url)
neu = pfad + ".neu"
with open(neu, "w", encoding="utf-8") as f:
    json.dump(daten, f, ensure_ascii=False, indent=2)
os.chmod(neu, 0o600)
os.replace(neu, pfad)
PY
ok "Token in KG Daten hinterlegt"

systemctl restart kg-crm && sleep 5
[ "$(systemctl is-active kg-crm)" = "active" ] || fehler "CRM läuft nicht – journalctl -u kg-crm -n 50"
systemctl restart kg-mcp && sleep 2
[ "$(systemctl is-active kg-mcp)" = "active" ] || fehler "KG Daten läuft nicht – journalctl -u kg-mcp -n 50"
ok "CRM und KG Daten neu gestartet"

python3 - "$CRM_URL" <<'PY'
import json, os, sys, urllib.request, urllib.error
anfrage = urllib.request.Request(
    sys.argv[1] + "/internal/mcp/leon-kampagne", data=b'{"aktion":"branchen"}', method="POST",
    headers={"Content-Type": "application/json", "X-KG-MCP-Token": os.environ["KG_T"]})
try:
    with urllib.request.urlopen(anfrage, timeout=30) as r:
        daten = json.loads(r.read().decode())
    print(f"OK      Verbindung CRM ↔ KG Daten: {len(daten.get('branchen') or [])} Branchen gefunden")
except urllib.error.HTTPError as exc:
    print(f"FEHLER  CRM antwortet mit HTTP {exc.code} – ist der neue CRM-Code schon gezogen (git pull in /opt/kg-crm)?")
except Exception as exc:
    print(f"FEHLER  CRM nicht erreichbar: {exc}")
PY
