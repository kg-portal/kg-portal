#!/bin/bash
# =====================================================
# KG CRM – Testkopie auf Hetzner
# Läuft getrennt von KG Business und Leon Reinigung:
#   Ordner /opt/kg-crm, Dienst kg-crm, Port 8803, eigene Daten.
# Die Testkopie kann NICHTS nach außen senden (kein Internet für den Dienst,
# keine Schlüssel für Mail, WhatsApp, Leon, Lexware, OpenAI; Automatiken aus).
#
# Aufruf:
#   bash crm_test_einrichten.sh             einrichten (mehrmals ausführbar)
#   bash crm_test_einrichten.sh neu-daten   Daten aus /root/kg_crm_daten.tar.gz neu einspielen
#   bash crm_test_einrichten.sh pruefen     Dienst + letzte Stundenzettel-Einträge zeigen
#   bash crm_test_einrichten.sh entfernen   Dienst und Caddy-Eintrag entfernen (Ordner bleibt)
# =====================================================
set -u

DOMAIN="${CRM_DOMAIN:-portal.kg-reinigung.de}"
PORT=8803
DIR=/opt/kg-crm
ARCHIV=/root/kg_crm_daten.tar.gz
DIENST=kg-crm
UNIT=/etc/systemd/system/$DIENST.service
CADDYFILE=/etc/caddy/Caddyfile
REPO=https://github.com/kg-portal/kg-portal.git
MARKE_ANFANG="# >>> KG-CRM-TEST (crm_test_einrichten.sh)"
MARKE_ENDE="# <<< KG-CRM-TEST"
MODUS="${1:-einrichten}"
JETZT=$(date +%Y%m%d_%H%M%S)

ok()     { echo "OK      $*"; }
info()   { echo "        $*"; }
fehler() { echo "FEHLER  $*"; exit 1; }

[ "$(id -u)" = "0" ] || fehler "bitte als root ausführen"

caddy_block_entfernen() {
    grep -qF "$MARKE_ANFANG" "$CADDYFILE" || return 0
    cp -a "$CADDYFILE" "$CADDYFILE.vor_crm_entfernen_$JETZT"
    awk -v a="$MARKE_ANFANG" -v e="$MARKE_ENDE" '$0==a{weg=1} !weg{print} $0==e{weg=0}' \
        "$CADDYFILE.vor_crm_entfernen_$JETZT" > "$CADDYFILE"
    if caddy validate --config "$CADDYFILE" --adapter caddyfile >/tmp/kg_crm_caddy.log 2>&1 && systemctl reload caddy; then
        ok "Caddy-Eintrag für $DOMAIN entfernt"
    else
        cp -a "$CADDYFILE.vor_crm_entfernen_$JETZT" "$CADDYFILE"
        systemctl reload caddy
        fehler "Caddy-Prüfung fehlgeschlagen, alte Datei wiederhergestellt (/tmp/kg_crm_caddy.log)"
    fi
}

zeige_daten() {
    python3 - "$DIR/data/kg_portal.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print("        Prüfung:", c.execute("PRAGMA integrity_check").fetchone()[0])
for t in ("mitarbeiter", "work_logs", "kunden"):
    try:
        print(f"        {t}: {c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
    except Exception:
        pass
PY
}

# ---------- entfernen ----------
if [ "$MODUS" = "entfernen" ]; then
    systemctl disable --now "$DIENST" 2>/dev/null
    rm -f "$UNIT"; systemctl daemon-reload
    ok "Dienst $DIENST gestoppt und entfernt"
    [ -f "$CADDYFILE" ] && caddy_block_entfernen
    info "Ordner $DIR bleibt erhalten (Daten). KG Business und Leon wurden nicht verändert."
    exit 0
fi

# ---------- prüfen ----------
if [ "$MODUS" = "pruefen" ]; then
    echo "Dienst $DIENST: $(systemctl is-active $DIENST)"
    echo "Lokal:  $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PORT/login)"
    echo "Extern: $(curl -s -o /dev/null -w '%{http_code}' https://$DOMAIN/login)"
    echo "Letzte Stundenzettel-Einträge auf Hetzner:"
    python3 - "$DIR/data/kg_portal.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
for r in c.execute("""SELECT m.vorname, m.nachname, w.datum, w.start_time, w.end_time, w.place
                      FROM work_logs w JOIN mitarbeiter m ON m.id = w.worker_id
                      ORDER BY w.rowid DESC LIMIT 3"""):
    print("       ", " | ".join(str(x or "") for x in r))
PY
    exit 0
fi

[ "$MODUS" = "einrichten" ] || [ "$MODUS" = "neu-daten" ] || fehler "unbekannter Aufruf: $MODUS"

# ---------- 1. Prüfungen (noch keine Änderung) ----------
echo "== 1. Prüfungen =="
DIENST_LAEUFT=0; systemctl is-active --quiet "$DIENST" && DIENST_LAEUFT=1
if [ $DIENST_LAEUFT = 0 ] && ss -ltn "sport = :$PORT" | grep -q LISTEN; then
    fehler "Port $PORT ist schon belegt"
fi
ok "Port $PORT frei für $DIENST"

[ -f "$CADDYFILE" ] && systemctl is-active --quiet caddy || fehler "Caddy nicht gefunden ($CADDYFILE)"
ok "Caddy läuft"

DNS_IP=$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}')
[ -n "$DNS_IP" ] || fehler "DNS-Eintrag fehlt: $DOMAIN -> A -> $(hostname -I | awk '{print $1}') (wie leon.kg-reinigung.de)"
hostname -I | tr ' ' '\n' | grep -qx "$DNS_IP" || fehler "$DOMAIN zeigt auf $DNS_IP, nicht auf diesen Server"
ok "DNS $DOMAIN -> $DNS_IP"

FREI_MB=$(free -m | awk '/^Mem:/{print $7}')
[ "$FREI_MB" -ge 400 ] || fehler "zu wenig freier Arbeitsspeicher: $FREI_MB MB"
ok "Arbeitsspeicher frei: $FREI_MB MB"

PLATTE_MB=$(df -Pm /opt | awk 'NR==2{print $4}')
[ "$PLATTE_MB" -ge 1500 ] || fehler "zu wenig Speicherplatz: $PLATTE_MB MB"
ok "Speicherplatz frei: $PLATTE_MB MB"

DATEN_EINSPIELEN=0
if [ "$MODUS" = "neu-daten" ] || [ ! -f "$DIR/data/.aus_render_importiert" ]; then
    DATEN_EINSPIELEN=1
    [ -f "$ARCHIV" ] || fehler "Datei fehlt: $ARCHIV (zuerst die Datensicherung vom PC hochladen)"
    tar -tzf "$ARCHIV" > /tmp/kg_crm_archiv.txt 2>/dev/null || fehler "$ARCHIV ist beschädigt"
    grep -qx "data/kg_portal.db" /tmp/kg_crm_archiv.txt || fehler "$ARCHIV enthält keine data/kg_portal.db"
    ok "Datensicherung lesbar ($(wc -l < /tmp/kg_crm_archiv.txt) Einträge, $(du -h "$ARCHIV" | cut -f1))"
fi

# ---------- 2. Programm ----------
echo "== 2. Programm =="
if [ ! -d "$DIR/.git" ]; then
    git clone -q --depth 1 -b main "$REPO" "$DIR" || fehler "git clone fehlgeschlagen"
    ok "CRM geladen: $(git -C "$DIR" log --oneline -1)"
else
    git -C "$DIR" pull -q --ff-only origin main || fehler "git pull fehlgeschlagen (Daten unverändert)"
    ok "CRM aktuell: $(git -C "$DIR" log --oneline -1)"
fi

if [ ! -x "$DIR/.venv/bin/python" ]; then
    if ! python3 -m venv "$DIR/.venv" >/dev/null 2>&1; then
        rm -rf "$DIR/.venv"
        apt-get install -y -q python3-venv >/dev/null 2>&1 || { apt-get update -q >/dev/null 2>&1; apt-get install -y -q python3-venv >/dev/null 2>&1; }
        python3 -m venv "$DIR/.venv" || fehler "Python-venv lässt sich nicht anlegen"
    fi
fi
info "Pakete werden installiert (1-3 Minuten) ..."
"$DIR/.venv/bin/pip" install -q --upgrade pip >/root/kg_crm_pip.log 2>&1
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt" >>/root/kg_crm_pip.log 2>&1 || { tail -15 /root/kg_crm_pip.log; fehler "Pakete nicht installiert (/root/kg_crm_pip.log)"; }
ok "Pakete installiert ($("$DIR/.venv/bin/python" --version))"

if [ ! -f "$DIR/tokenlar.env" ]; then
    ZUF() { python3 -c 'import secrets; print(secrets.token_urlsafe(32))'; }
    umask 077
    cat > "$DIR/tokenlar.env" <<EOF
# Testkopie auf Hetzner. Absichtlich ohne Schlüssel für Mail, WhatsApp, Leon, Lexware, OpenAI.
KG_PORTAL_SECRET_KEY=$(ZUF)
KG_PORTAL_USER=admin
KG_PORTAL_PASSWORD=$(ZUF)
STZ_AUTOMATIK_AUS=1
KAMPAGNEN_BERICHT_LAUF=0
TAGESLISTE_LAUF=0
KG_SERVER_HINWEIS=Hetzner-Test
EOF
    umask 022
    ok "tokenlar.env angelegt (Automatiken aus, Testpasswort zufällig)"
else
    ok "tokenlar.env vorhanden (unverändert)"
fi

# ---------- 3. Daten ----------
echo "== 3. Daten =="
if [ $DATEN_EINSPIELEN = 1 ]; then
    systemctl stop "$DIENST" 2>/dev/null
    TMP=$(mktemp -d /opt/kg-crm-import.XXXX)
    tar -xzf "$ARCHIV" -C "$TMP" || { rm -rf "$TMP"; fehler "Entpacken fehlgeschlagen"; }
    [ -d "$DIR/data" ] && mv "$DIR/data" "$DIR/data_vor_import_$JETZT"
    mv "$TMP/data" "$DIR/data"
    [ -f "$TMP/umzug_info.json" ] && cp "$TMP/umzug_info.json" "$DIR/data/.aus_render_importiert" || date > "$DIR/data/.aus_render_importiert"
    rm -rf "$TMP"
    if [ -f "$DIR/data/calendar_key.json" ]; then
        mv "$DIR/data/calendar_key.json" "$DIR/data/calendar_key.json.test_aus"
        info "calendar_key.json für den Test deaktiviert (kein Zugriff auf den echten Kalender)"
    fi
    ok "Daten eingespielt (alter Stand: $DIR/data_vor_import_$JETZT)"
else
    ok "Daten schon eingespielt (neu einspielen: bash crm_test_einrichten.sh neu-daten)"
fi
zeige_daten

# ---------- 4. Dienst ----------
echo "== 4. Dienst =="
cat > "$UNIT" <<EOF
[Unit]
Description=KG CRM Testkopie (Port $PORT)
After=network.target

[Service]
WorkingDirectory=$DIR
EnvironmentFile=$DIR/tokenlar.env
Environment=PYTHONUNBUFFERED=1
ExecStart=$DIR/.venv/bin/gunicorn -w 1 --threads 4 --timeout 120 -b 127.0.0.1:$PORT app:app
Restart=always
RestartSec=5
MemoryMax=700M
IPAddressDeny=any
IPAddressAllow=localhost
InaccessiblePaths=-/etc/secrets

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q "$DIENST"
systemctl restart "$DIENST"
CODE=000
for _ in $(seq 1 60); do
    sleep 1
    CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/login")
    [ "$CODE" = "200" ] && break
done
[ "$CODE" = "200" ] || { journalctl -u "$DIENST" -n 30 --no-pager; fehler "CRM startet nicht"; }
ok "Dienst $DIENST läuft (127.0.0.1:$PORT, ohne Internet nach außen, max. 700 MB)"

# ---------- 5. Caddy ----------
echo "== 5. Caddy =="
if grep -qF "$MARKE_ANFANG" "$CADDYFILE"; then
    ok "Caddy-Eintrag für $DOMAIN schon vorhanden"
elif grep -qE "^[[:space:]]*$DOMAIN([[:space:],{]|$)" "$CADDYFILE"; then
    fehler "$DOMAIN steht schon (von Hand) im Caddyfile – nichts geändert"
else
    SICHERUNG="$CADDYFILE.vor_crm_$JETZT"
    cp -a "$CADDYFILE" "$SICHERUNG"
    printf '\n%s\n%s {\n\treverse_proxy 127.0.0.1:%s\n}\n%s\n' "$MARKE_ANFANG" "$DOMAIN" "$PORT" "$MARKE_ENDE" >> "$CADDYFILE"
    if ! caddy validate --config "$CADDYFILE" --adapter caddyfile >/tmp/kg_crm_caddy.log 2>&1; then
        cp -a "$SICHERUNG" "$CADDYFILE"
        tail -5 /tmp/kg_crm_caddy.log
        fehler "Caddy-Prüfung fehlgeschlagen – alte Datei wiederhergestellt, Business unverändert"
    fi
    if ! systemctl reload caddy; then
        cp -a "$SICHERUNG" "$CADDYFILE"; systemctl reload caddy
        fehler "Caddy-Neuladen fehlgeschlagen – alte Datei wiederhergestellt"
    fi
    ok "Caddy: $DOMAIN -> 127.0.0.1:$PORT (Sicherung: $SICHERUNG)"
fi

for _ in $(seq 1 45); do
    CODE=$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/login")
    [ "$CODE" = "200" ] && break
    sleep 2
done
[ "$CODE" = "200" ] || fehler "https://$DOMAIN antwortet noch nicht (Code $CODE) – in 2 Minuten nochmal ausführen"
ok "https://$DOMAIN erreichbar (Zertifikat ok)"

echo
echo "==== FERTIG: Test-CRM läuft auf https://$DOMAIN ===="
info "KG Business: $(systemctl is-active kg-business 2>/dev/null)   Leon Reinigung: $(systemctl is-active leon-reinigung 2>/dev/null)"
info "Nächster Schritt: in Render UMZUG_ZIEL und UMZUG_CODES eintragen."
