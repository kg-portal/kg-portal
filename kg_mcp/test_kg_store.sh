#!/bin/bash
# =====================================================
# Erster echter Test über KG Daten (genau wie ChatGPT es aufruft):
#   Kampagne „10.10.26 chatgpt test“ als ENTWURF mit nur Leon-Reinigung-Lead 95 (kg store).
# - Gibt es die Kampagne schon, wird sie nur zurückgelesen (kein zweites Mal angelegt).
# - Es wird nichts gestartet und niemand angerufen.
# =====================================================
set -u
ENV=/opt/kg-mcp-geheim/env.json
PFAD=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["KG_MCP_PFAD"])' "$ENV") || { echo "FEHLER  $ENV nicht lesbar"; exit 1; }

python3 - "http://127.0.0.1:8810/$PFAD/mcp" <<'PY'
import json, sys, urllib.request
URL = sys.argv[1]


def werkzeug(name, args):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": args}}).encode()
    anfrage = urllib.request.Request(URL, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(anfrage, timeout=120) as r:
        ergebnis = json.loads(r.read().decode())["result"]
    return ergebnis.get("isError"), ergebnis["content"][0]["text"]


NAME = "10.10.26 chatgpt test"
fehler, text = werkzeug("kampagne_pruefen", {"name": NAME})
if fehler:
    print("Kampagne gibt es noch nicht – lege Entwurf an …")
    fehler, text = werkzeug("leads_kampagne_hinzufuegen", {"name": NAME, "leon_lead_ids": [95]})
    print(("FEHLER  " if fehler else "OK      angelegt\n") + text)
    fehler, text = werkzeug("kampagne_pruefen", {"name": NAME})
k = json.loads(text)["kampagne"] if not fehler else None
if not k:
    print("FEHLER  Zurücklesen:", text)
    sys.exit(1)
print(f"Zurückgelesen: Kampagne {k['id']} „{k['name']}“ | Status: {k['status']} | Agent: {k['agent']}")
for l in k["leads"]:
    print(f"   Leon-Lead {l['leon_lead_id']}: {l['firma']} ({l['telefon']})")
print("OK      Entwurf – es wurde nichts gestartet." if k["status"] == "Entwurf" else "ACHTUNG Status ist nicht Entwurf!")
PY
