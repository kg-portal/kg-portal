#!/bin/bash
# =====================================================
# KG CRM – endgültiger Umzug Render -> Hetzner
# Voraussetzung: Testkopie lief (crm_test_einrichten.sh): /opt/kg-crm, Dienst kg-crm, Caddy-Eintrag.
# Macht aus der Testkopie das echte CRM:
#   frische Daten + Einstellungen + geheime Dateien aus /root/kg_crm_umzug.tar.gz,
#   Internet erlaubt, Automatiken wie auf Render, Chromium für PDFs,
#   Leon-Auto-Kampagne-Zeitplan, tägliche Datenbank-Sicherung.
# KG Business und Leon Reinigung werden nicht verändert.
# Render bleibt unverändert, bis du dort den Start Command umstellst.
# =====================================================
set -u

DOMAIN="${CRM_DOMAIN:-portal.kg-reinigung.de}"
PORT=8803
DIR=/opt/kg-crm
PAKET=/root/kg_crm_umzug.tar.gz
DIENST=kg-crm
UNIT=/etc/systemd/system/$DIENST.service
HELFER="$DIR/migration/hetzner/crm_helfer.py"
CADDYFILE=/etc/caddy/Caddyfile
MARKE_ANFANG="# >>> KG-CRM-TEST (crm_test_einrichten.sh)"
JETZT=$(date +%Y%m%d_%H%M%S)

ok()     { echo "OK      $*"; }
info()   { echo "        $*"; }
warnung(){ echo "ACHTUNG $*"; }
fehler() { echo "FEHLER  $*"; exit 1; }

[ "$(id -u)" = "0" ] || fehler "bitte als root ausführen"

# ---------- 1. Prüfungen (noch keine Änderung) ----------
echo "== 1. Prüfungen =="
[ -d "$DIR/.git" ] && [ -x "$DIR/.venv/bin/python" ] || fehler "$DIR fehlt – zuerst crm_test_einrichten.sh"
grep -qF "$MARKE_ANFANG" "$CADDYFILE" || fehler "Caddy-Eintrag für $DOMAIN fehlt – zuerst crm_test_einrichten.sh"
[ -f "$PAKET" ] || fehler "Datei fehlt: $PAKET (zuerst Umzugspaket vom PC hochladen)"
tar -tzf "$PAKET" > /tmp/kg_crm_paket.txt 2>/dev/null || fehler "$PAKET ist beschädigt"
grep -qx "data/kg_portal.db" /tmp/kg_crm_paket.txt || fehler "Paket enthält keine data/kg_portal.db"
grep -qx "umzug/env.json" /tmp/kg_crm_paket.txt || fehler "Paket enthält keine Einstellungen – den grünen Knopf 'Umzugspaket' benutzen"
ok "Umzugspaket lesbar ($(wc -l < /tmp/kg_crm_paket.txt) Einträge, $(grep -c '^umzug/secrets/' /tmp/kg_crm_paket.txt) geheime Dateien)"
PLATTE_MB=$(df -Pm /opt | awk 'NR==2{print $4}')
[ "$PLATTE_MB" -ge 1500 ] || fehler "zu wenig Speicherplatz: $PLATTE_MB MB"
ok "Speicherplatz frei: $PLATTE_MB MB"

# ---------- 2. Programm ----------
echo "== 2. Programm =="
git -C "$DIR" pull -q --ff-only origin main || fehler "git pull fehlgeschlagen (nichts verändert)"
ok "CRM aktuell: $(git -C "$DIR" log --oneline -1)"
[ -f "$HELFER" ] && [ -f "$DIR/umzug_weiterleitung.py" ] || fehler "neue Dateien fehlen im CRM – ist main aktuell?"
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt" >>/root/kg_crm_pip.log 2>&1 || { tail -15 /root/kg_crm_pip.log; fehler "Pakete nicht installiert"; }
ok "Pakete installiert"

export DEBIAN_FRONTEND=noninteractive
info "Chromium für PDFs wird installiert (1-3 Minuten) ..."
export PLAYWRIGHT_BROWSERS_PATH="$DIR/pw-browsers"
if ! "$DIR/.venv/bin/python" -m playwright install --with-deps chromium >/root/kg_crm_chromium.log 2>&1; then
    "$DIR/.venv/bin/python" -m playwright install chromium >>/root/kg_crm_chromium.log 2>&1
fi
if "$DIR/.venv/bin/python" -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(); s = b.new_page(); s.set_content('<p>KG</p>'); s.pdf(); b.close()
" >>/root/kg_crm_chromium.log 2>&1; then
    PDF_OK=1; ok "Chromium: PDF-Test erfolgreich"
else
    PDF_OK=0; warnung "Chromium: PDF-Test fehlgeschlagen (/root/kg_crm_chromium.log) – Umzug geht weiter, PDF danach beheben"
fi
unset PLAYWRIGHT_BROWSERS_PATH

# ---------- 3. Daten + Einstellungen ----------
echo "== 3. Daten + Einstellungen =="
systemctl stop "$DIENST" 2>/dev/null
TMP=$(mktemp -d /opt/kg-crm-umzug.XXXX)
chmod 700 "$TMP"
tar -xzf "$PAKET" -C "$TMP" || { rm -rf "$TMP"; fehler "Entpacken fehlgeschlagen"; }

python3 "$HELFER" env-schreiben "$TMP/umzug/env.json" "$DOMAIN" || { rm -rf "$TMP"; fehler "Einstellungen unvollständig"; }

mkdir -p "$DIR/geheim/secrets"; chmod 700 "$DIR/geheim" "$DIR/geheim/secrets"
if [ -d "$TMP/umzug/secrets" ]; then
    cp -a "$TMP/umzug/secrets/." "$DIR/geheim/secrets/"
    chmod 600 "$DIR"/geheim/secrets/* 2>/dev/null
fi
for f in "$DIR"/geheim/secrets/*; do
    [ -L "$f" ] && warnung "$(basename "$f") ist nur eine Verknüpfung ohne Inhalt – Datei von Hand nachliefern"
done
ok "geheime Dateien: $(ls "$DIR/geheim/secrets" | tr '\n' ' ')"

[ -f "$DIR/tokenlar.env" ] && mv "$DIR/tokenlar.env" "$DIR/tokenlar.env.test_alt_$JETZT"
ok "Test-Einstellungen abgelegt (Automatiken wie auf Render, kein Test-Schild)"

[ -d "$DIR/data" ] && mv "$DIR/data" "$DIR/data_test_$JETZT"
mv "$TMP/data" "$DIR/data"
[ -f "$TMP/umzug_info.json" ] && cp "$TMP/umzug_info.json" "$DIR/data/.aus_render_importiert"
rm -rf "$TMP"
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
ok "Daten eingespielt (Testdaten: $DIR/data_test_$JETZT)"

# ---------- 4. Dienst ----------
echo "== 4. Dienst =="
[ -d /etc/secrets ] || mkdir -p /etc/secrets
cat > "$UNIT" <<EOF
[Unit]
Description=KG CRM ($DOMAIN)
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $HELFER start
Restart=always
RestartSec=5
MemoryMax=900M
BindPaths=$DIR/geheim/secrets:/etc/secrets

[Install]
WantedBy=multi-user.target
EOF

ZEIT_LEON="Mon..Fri *-*-* 07..11:05:00 Europe/Berlin"
ZEIT_SICHERUNG="*-*-* 02:30:00 Europe/Berlin"
systemd-analyze calendar "$ZEIT_LEON" >/dev/null 2>&1 || { ZEIT_LEON="Mon..Fri *-*-* 05..10:05:00"; ZEIT_SICHERUNG="*-*-* 00:30:00"; }

cat > /etc/systemd/system/kg-crm-leon-auto.service <<EOF
[Unit]
Description=KG CRM Leon-Auto-Kampagne (läuft nur, wenn im CRM eingeschaltet)
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $HELFER aufruf /internal/leon-auto-kampagne
EOF
cat > /etc/systemd/system/kg-crm-leon-auto.timer <<EOF
[Unit]
Description=KG CRM Leon-Auto-Kampagne Mo-Fr 7-11 Uhr
[Timer]
OnCalendar=$ZEIT_LEON
[Install]
WantedBy=timers.target
EOF
cat > /etc/systemd/system/kg-crm-sicherung.service <<EOF
[Unit]
Description=KG CRM tägliche Datenbank-Sicherung
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 $HELFER sicherung
EOF
cat > /etc/systemd/system/kg-crm-sicherung.timer <<EOF
[Unit]
Description=KG CRM Sicherung jede Nacht
[Timer]
OnCalendar=$ZEIT_SICHERUNG
Persistent=true
[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable -q "$DIENST"
systemctl restart "$DIENST"
CODE=000
for _ in $(seq 1 90); do
    sleep 1
    CODE=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/login")
    [ "$CODE" = "200" ] && break
done
[ "$CODE" = "200" ] || { journalctl -u "$DIENST" -n 40 --no-pager; fehler "CRM startet nicht – Render läuft weiter, NICHTS in Render ändern"; }
ok "Dienst $DIENST läuft (mit Internet, max. 900 MB)"
systemctl enable -q --now kg-crm-leon-auto.timer kg-crm-sicherung.timer
ok "Zeitpläne aktiv: Leon-Auto-Kampagne ($ZEIT_LEON), Sicherung ($ZEIT_SICHERUNG)"
python3 "$HELFER" sicherung >/dev/null && ok "erste Sicherung erstellt ($DIR/sicherung)"

CODE=$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/login")
[ "$CODE" = "200" ] || fehler "https://$DOMAIN antwortet nicht (Code $CODE)"
ok "https://$DOMAIN erreichbar"



echo
echo "==== FERTIG: CRM läuft auf https://$DOMAIN ===="
info "KG Business: $(systemctl is-active kg-business 2>/dev/null)   Leon Reinigung: $(systemctl is-active leon-reinigung 2>/dev/null)"
[ "$PDF_OK" = "1" ] || info "PDF noch offen – Ausgabe an Claude schicken"
info "Jetzt in Render: Start Command = gunicorn umzug_weiterleitung:app"
