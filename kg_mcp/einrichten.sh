#!/bin/bash
# =====================================================
# KG Daten – MCP-Connector für ChatGPT (nur lesen) auf Hetzner
# Läuft getrennt von CRM, KG Business und Leon Reinigung:
#   Ordner /opt/kg-mcp, Dienst kg-mcp, Port 8810, liest deren Datenbanken nur.
# Das Geheimnis (Teil der Adresse) liegt in /opt/kg-mcp-geheim/env.json (600).
#
# Aufruf:
#   bash einrichten.sh            einrichten / aktualisieren (mehrmals ausführbar)
#   bash einrichten.sh adresse    die ChatGPT-Adresse noch einmal anzeigen
#   bash einrichten.sh pruefen    Dienst und Erreichbarkeit prüfen
#   bash einrichten.sh entfernen  Dienst und Caddy-Eintrag entfernen (Ordner bleibt)
# =====================================================
set -u

DOMAIN="${MCP_DOMAIN:-mcp.kg-reinigung.de}"
PORT=8810
DIR=/opt/kg-mcp
GEHEIM=/opt/kg-mcp-geheim
ENV="$GEHEIM/env.json"
DIENST=kg-mcp
UNIT=/etc/systemd/system/$DIENST.service
CADDYFILE=/etc/caddy/Caddyfile
MARKE_ANFANG="# >>> KG-MCP (kg_mcp/einrichten.sh)"
MARKE_ENDE="# <<< KG-MCP"
MODUS="${1:-einrichten}"
JETZT=$(date +%Y%m%d_%H%M%S)

ok()     { echo "OK      $*"; }
info()   { echo "        $*"; }
fehler() { echo "FEHLER  $*"; exit 1; }

[ "$(id -u)" = "0" ] || fehler "bitte als root ausführen"

pfad() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["KG_MCP_PFAD"])' "$ENV"; }

adresse() {
    echo
    echo "==== ChatGPT-Adresse (geheim – nur in ChatGPT eintragen, nirgends sonst teilen) ===="
    echo "https://$DOMAIN/$(pfad)/mcp"
    echo
}

lokal_test() {
    curl -s -m 10 -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"pruefung","version":"1"}}}' \
        "http://127.0.0.1:$PORT/$(pfad)/mcp" | grep -q '"kg-daten"'
}

caddy_block_entfernen() {
    grep -qF "$MARKE_ANFANG" "$CADDYFILE" || return 0
    cp -a "$CADDYFILE" "$CADDYFILE.vor_mcp_entfernen_$JETZT"
    awk -v a="$MARKE_ANFANG" -v e="$MARKE_ENDE" '$0==a{weg=1} !weg{print} $0==e{weg=0}' \
        "$CADDYFILE.vor_mcp_entfernen_$JETZT" > "$CADDYFILE"
    if caddy validate --config "$CADDYFILE" --adapter caddyfile >/tmp/kg_mcp_caddy.log 2>&1 && systemctl reload caddy; then
        ok "Caddy-Eintrag für $DOMAIN entfernt"
    else
        cp -a "$CADDYFILE.vor_mcp_entfernen_$JETZT" "$CADDYFILE"
        systemctl reload caddy
        fehler "Caddy-Prüfung fehlgeschlagen, alte Datei wiederhergestellt (/tmp/kg_mcp_caddy.log)"
    fi
}

case "$MODUS" in
    adresse)
        [ -f "$ENV" ] || fehler "noch nicht eingerichtet"
        adresse
        exit 0
        ;;
    pruefen)
        info "Dienst $DIENST: $(systemctl is-active $DIENST)"
        lokal_test && ok "MCP antwortet lokal" || echo "FEHLER  MCP antwortet lokal nicht"
        info "DNS $DOMAIN: $(getent hosts "$DOMAIN" | awk '{print $1}' | head -1)"
        info "https://$DOMAIN/gesund: $(curl -s -m 10 -o /dev/null -w '%{http_code}' "https://$DOMAIN/gesund")"
        info "KG Business: $(systemctl is-active kg-business)   CRM: $(systemctl is-active kg-crm)   Leon: $(systemctl is-active leon-reinigung)"
        exit 0
        ;;
    entfernen)
        systemctl disable --now $DIENST 2>/dev/null
        rm -f "$UNIT"
        systemctl daemon-reload
        ok "Dienst $DIENST entfernt"
        caddy_block_entfernen
        info "Ordner $DIR und $GEHEIM bleiben (bei Bedarf von Hand löschen)."
        exit 0
        ;;
    einrichten) ;;
    *) fehler "unbekannter Modus: $MODUS" ;;
esac

# ---------- 1. Code ----------
echo "== 1. Code =="
[ -f "$DIR/kg_mcp/kg_mcp_server.py" ] || fehler "$DIR/kg_mcp/kg_mcp_server.py fehlt"
if git -C "$DIR" pull -q --ff-only 2>/tmp/kg_mcp_git.log; then
    ok "Code aktuell: $(git -C "$DIR" log --oneline -1)"
else
    info "git pull nicht möglich (siehe /tmp/kg_mcp_git.log) – vorhandener Stand wird benutzt"
fi
python3 -c 'from zoneinfo import ZoneInfo; ZoneInfo("Europe/Berlin")' 2>/dev/null \
    || fehler "Python ohne Zeitzonen-Daten (apt install tzdata)"

# ---------- 2. Geheimnis + Einstellungen ----------
echo "== 2. Einstellungen =="
mkdir -p "$GEHEIM" && chmod 700 "$GEHEIM"
if [ -f "$ENV" ]; then
    ok "Einstellungen vorhanden ($ENV) – Adresse bleibt gleich"
else
    python3 - "$ENV" "$PORT" <<'PY'
import json, os, secrets, sys
ziel, port = sys.argv[1], int(sys.argv[2])
werte = {
    "KG_MCP_PFAD": secrets.token_urlsafe(32),
    "PORT": port,
    "DB_CRM": "/opt/kg-crm/data/kg_portal.db",
    "DB_REINIGUNG": "/opt/leon-reinigung/data/kg_business_voice.db",
    "DB_BUSINESS": "/opt/kg-business/data/kg_business_voice.db",
}
fd = os.open(ziel, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(werte, f, indent=2)
PY
    ok "Einstellungen angelegt ($ENV, 600)"
fi
chmod 600 "$ENV"
for db in /opt/kg-crm/data/kg_portal.db /opt/leon-reinigung/data/kg_business_voice.db /opt/kg-business/data/kg_business_voice.db; do
    [ -f "$db" ] && info "Datenbank gefunden: $db" || info "Datenbank FEHLT: $db (dieser Bereich meldet dann einen Fehler)"
done

# ---------- 3. Dienst ----------
echo "== 3. Dienst =="
if ss -ltnH "sport = :$PORT" 2>/dev/null | grep -q . && ! systemctl is-active -q $DIENST; then
    fehler "Port $PORT ist schon von einem anderen Programm belegt – nichts geändert"
fi
cat > "$UNIT" <<EOF
[Unit]
Description=KG Daten - MCP fuer ChatGPT (nur lesen)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/kg_mcp/kg_mcp_server.py $ENV
Restart=always
RestartSec=5
MemoryMax=150M
NoNewPrivileges=yes
PrivateTmp=yes
ProtectHome=yes
ProtectSystem=full

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q $DIENST
systemctl restart $DIENST
for _ in $(seq 1 15); do
    lokal_test && break
    sleep 1
done
lokal_test || { journalctl -u $DIENST -n 20 --no-pager; fehler "Dienst $DIENST antwortet nicht"; }
ok "Dienst $DIENST läuft (127.0.0.1:$PORT, max. 150 MB)"

# ---------- 4. Caddy ----------
echo "== 4. Caddy =="
if grep -qF "$MARKE_ANFANG" "$CADDYFILE"; then
    ok "Caddy-Eintrag für $DOMAIN schon vorhanden"
elif grep -qE "^[[:space:]]*$DOMAIN([[:space:],{]|$)" "$CADDYFILE"; then
    fehler "$DOMAIN steht schon (von Hand) im Caddyfile – nichts geändert"
else
    SICHERUNG="$CADDYFILE.vor_mcp_$JETZT"
    cp -a "$CADDYFILE" "$SICHERUNG"
    printf '\n%s\n%s {\n\treverse_proxy 127.0.0.1:%s\n}\n%s\n' "$MARKE_ANFANG" "$DOMAIN" "$PORT" "$MARKE_ENDE" >> "$CADDYFILE"
    if ! caddy validate --config "$CADDYFILE" --adapter caddyfile >/tmp/kg_mcp_caddy.log 2>&1; then
        cp -a "$SICHERUNG" "$CADDYFILE"
        tail -5 /tmp/kg_mcp_caddy.log
        fehler "Caddy-Prüfung fehlgeschlagen – alte Datei wiederhergestellt, nichts geändert"
    fi
    if ! systemctl reload caddy; then
        cp -a "$SICHERUNG" "$CADDYFILE"; systemctl reload caddy
        fehler "Caddy-Neuladen fehlgeschlagen – alte Datei wiederhergestellt"
    fi
    ok "Caddy: $DOMAIN -> 127.0.0.1:$PORT (Sicherung: $SICHERUNG)"
fi

# ---------- 5. Von außen erreichbar? ----------
echo "== 5. Prüfung von außen =="
IP=$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1)
if [ -z "$IP" ]; then
    info "DNS für $DOMAIN fehlt noch (Squarespace: A-Eintrag 'mcp' -> 46.224.155.41)."
    info "Später einfach 'bash $DIR/kg_mcp/einrichten.sh pruefen' ausführen."
else
    CODE=000
    for _ in $(seq 1 30); do
        CODE=$(curl -s -m 10 -o /dev/null -w '%{http_code}' "https://$DOMAIN/gesund")
        [ "$CODE" = "200" ] && break
        sleep 2
    done
    if [ "$CODE" = "200" ]; then
        ok "https://$DOMAIN erreichbar (Zertifikat ok)"
    else
        info "https://$DOMAIN antwortet noch nicht (Code $CODE, DNS $IP) – in 2 Minuten 'pruefen' ausführen."
    fi
fi

echo
echo "==== FERTIG ===="
info "KG Business: $(systemctl is-active kg-business 2>/dev/null)   CRM: $(systemctl is-active kg-crm 2>/dev/null)   Leon: $(systemctl is-active leon-reinigung 2>/dev/null)"
adresse
