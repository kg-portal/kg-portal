#!/bin/bash
# =====================================================
# KG Daten (ChatGPT): Welche Werkzeuge bietet der laufende MCP-Dienst gerade an?
# - Startet nur den Dienst kg-mcp neu (CRM, Leon und Kampagnen bleiben unberührt).
# - Fragt tools/list lokal (127.0.0.1:8810) und über die öffentliche Adresse ab.
# - Die geheime Adresse wird nie angezeigt.
# =====================================================
set -u
ENV=/opt/kg-mcp-geheim/env.json
DOMAIN="${MCP_DOMAIN:-mcp.kg-reinigung.de}"
PFAD=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["KG_MCP_PFAD"])' "$ENV") || { echo "FEHLER  $ENV nicht lesbar"; exit 1; }

systemctl restart kg-mcp && sleep 2
echo "Code /opt/kg-mcp: $(git -C /opt/kg-mcp log --oneline -1)"
echo "Dienst kg-mcp:    $(systemctl is-active kg-mcp)"
echo "Port 8810:        $(ss -ltnp 'sport = :8810' | grep -o 'users:.*' | head -1)"

liste() {
    python3 - "$1" <<'PY'
import json, sys, urllib.request
body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
anfrage = urllib.request.Request(sys.argv[1], data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
try:
    with urllib.request.urlopen(anfrage, timeout=20) as r:
        daten = json.loads(r.read().decode())
    werkzeuge = daten.get("result", {}).get("tools", [])
    print(f"{len(werkzeuge)} Werkzeuge:")
    for w in werkzeuge:
        nur_lesen = (w.get("annotations") or {}).get("readOnlyHint")
        print(f"   - {w['name']}" + ("" if nur_lesen else "   (ändert etwas)"))
except Exception as exc:
    print("FEHLER", exc)
PY
}
echo "Lokal:";       liste "http://127.0.0.1:8810/$PFAD/mcp"
echo "Öffentlich:";  liste "https://$DOMAIN/$PFAD/mcp"
