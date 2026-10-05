# =====================================================
# UMZUG RENDER -> HETZNER
# 1) Weiterleitung: nur die Stundenzettel-Links aus UMZUG_CODES gehen an UMZUG_ZIEL.
#    Ohne beide Einstellungen passiert nichts. "*" = alle Stundenzettel-Links.
# 2) /admin/datensicherung: kompletter data/-Ordner als eine Datei (nur nach Login).
#    Die Datei wird im Hintergrund gebaut und in kleinen Teilen geladen,
#    damit keine Anfrage lange dauert.
#    Umzugspaket: zusätzlich die Einstellungen (Umgebung) und /etc/secrets – nur für den Umzug.
# 3) KG_SERVER_HINWEIS: kleines Schild auf der Stundenzettel-Seite (nur auf dem Testserver gesetzt).
# 4) KG_HINTER_PROXY=1: hinter Caddy (Hetzner) – Links mit https und richtigem Namen.
# =====================================================

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import threading
import time
import uuid
from datetime import datetime, timezone
from html import escape
from urllib.parse import urlparse

from flask import Response, jsonify, redirect, request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
SICHERUNG_DIR = os.path.join("/tmp", "kg_crm_sicherung")
DATEI_NAME = "kg_crm_daten.tar.gz"
GEHEIM_DIR = "/etc/secrets"
# Einstellungen, die das CRM liest oder die in Render eingetragen sind (nur diese kommen ins Umzugspaket)
UMZUG_ENV_NAMEN = (
    "APIFY_TOKEN", "BLENDER_BIN", "CRM_CONNECTOR_TOKEN", "FINTS_PIN", "GMAPS_KEY", "GOOGLE_CALENDAR_ID",
    "GOOGLE_DAILY_LIMIT", "INTERNAL_CRON_TOKEN", "KAMPAGNEN_BERICHT_LAUF", "KG_AI_SELF_CHAT_IDS",
    "KG_BUSINESS_PASSWORD", "KG_BUSINESS_URL", "KG_BUSINESS_USER", "KG_INTERNAL_CRON_TOKEN",
    "KG_PORTAL_PASSWORD", "KG_PORTAL_SECRET_KEY", "KG_PORTAL_USER", "KG_SCAN_API_TOKEN", "LEAD_SAMMLER_URL",
    "LEON_AUTO_TOKEN", "LEON_PASSWORD", "LEON_STZ_KAMPAGNE", "LEON_URL", "LEON_USER", "LEXWARE_API_TOKEN",
    "OPENAI_API_KEY", "OPENAI_MODEL", "STZ_AB_TAG", "STZ_AUTOMATIK_AUS", "STZ_CRON_TOKEN", "TAGESLISTE_LAUF",
)
TEIL_GROESSE = 16 * 1024 * 1024
WORKER_PREFIX = "/stundenzettel/worker/"

_lock = threading.Lock()


def _status_pfad():
    return os.path.join(SICHERUNG_DIR, "status.json")


def _status_lesen():
    try:
        with open(_status_pfad(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"zustand": "leer"}


def _status_schreiben(daten):
    os.makedirs(SICHERUNG_DIR, exist_ok=True)
    tmp = _status_pfad() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(daten, f)
    os.replace(tmp, _status_pfad())


def _ist_sqlite(pfad):
    try:
        with open(pfad, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except Exception:
        return False


def _sqlite_kopie(quelle, ziel):
    src = sqlite3.connect(quelle, timeout=30)
    dst = sqlite3.connect(ziel)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _tabellen_zaehlen(pfad):
    conn = sqlite3.connect(pfad)
    try:
        namen = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in namen}
    finally:
        conn.close()


def _sicherung_bauen(paket=False):
    arbeit = os.path.join(SICHERUNG_DIR, "arbeit")
    ziel = os.path.join(SICHERUNG_DIR, DATEI_NAME)
    teil = ziel + ".teil"
    try:
        shutil.rmtree(arbeit, ignore_errors=True)
        os.makedirs(arbeit, exist_ok=True)
        info = {"erstellt_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "dateien": 0, "bytes": 0, "datenbanken": {}}
        with tarfile.open(teil, "w:gz", compresslevel=6) as tar:
            for wurzel, ordner, dateien in os.walk(DATA_DIR):
                ordner.sort()
                for name in sorted(dateien):
                    voll = os.path.join(wurzel, name)
                    rel = os.path.relpath(voll, BASE_DIR)
                    if name.endswith(("-wal", "-shm", "-journal")) or not os.path.isfile(voll):
                        continue
                    if _ist_sqlite(voll):
                        kopie = os.path.join(arbeit, uuid.uuid4().hex + ".db")
                        _sqlite_kopie(voll, kopie)
                        conn = sqlite3.connect(kopie)
                        try:
                            pruefung = conn.execute("PRAGMA integrity_check").fetchone()[0]
                        finally:
                            conn.close()
                        info["datenbanken"][rel] = {"integrity": pruefung, "tabellen": _tabellen_zaehlen(kopie)}
                        info["bytes"] += os.path.getsize(kopie)
                        tar.add(kopie, arcname=rel)
                        os.remove(kopie)
                    else:
                        info["bytes"] += os.path.getsize(voll)
                        tar.add(voll, arcname=rel)
                    info["dateien"] += 1
            if paket:
                env = {n: os.environ[n] for n in UMZUG_ENV_NAMEN if os.environ.get(n) is not None}
                env_pfad = os.path.join(arbeit, "env.json")
                with open(env_pfad, "w", encoding="utf-8") as f:
                    json.dump(env, f, ensure_ascii=False)
                tar.add(env_pfad, arcname="umzug/env.json")
                info["einstellungen"] = len(env)
                info["geheime_dateien"] = 0
                if os.path.isdir(GEHEIM_DIR):
                    for name in sorted(os.listdir(GEHEIM_DIR)):
                        voll = os.path.join(GEHEIM_DIR, name)
                        if os.path.isfile(voll):
                            tar.add(voll, arcname="umzug/secrets/" + name)
                            info["geheime_dateien"] += 1
            info_pfad = os.path.join(arbeit, "umzug_info.json")
            with open(info_pfad, "w", encoding="utf-8") as f:
                json.dump(info, f, ensure_ascii=False, indent=1)
            tar.add(info_pfad, arcname="umzug_info.json")
        os.replace(teil, ziel)
        sha = hashlib.sha256()
        with open(ziel, "rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                sha.update(block)
        groesse = os.path.getsize(ziel)
        _status_schreiben({
            "zustand": "fertig",
            "erstellt_utc": info["erstellt_utc"],
            "groesse": groesse,
            "teile": max(1, -(-groesse // TEIL_GROESSE)),
            "sha256": sha.hexdigest(),
            "dateien": info["dateien"],
            "paket": bool(paket),
            "einstellungen": info.get("einstellungen", 0),
            "geheime_dateien": info.get("geheime_dateien", 0),
        })
    except Exception as exc:
        _status_schreiben({"zustand": "fehler", "fehler": str(exc)[:300]})
    finally:
        shutil.rmtree(arbeit, ignore_errors=True)
        try:
            os.remove(teil)
        except OSError:
            pass


SEITE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Datensicherung</title>
<style>body{font-family:system-ui,sans-serif;background:#f1f5f9;color:#0f172a;margin:0;padding:32px 16px}
.k{max-width:560px;margin:0 auto;background:#fff;border-radius:14px;padding:24px;box-shadow:0 8px 24px rgba(15,23,42,.08)}
h1{font-size:20px;margin:0 0 6px}p{color:#475569;font-size:14px}
button{background:#1d4ed8;color:#fff;border:0;border-radius:10px;padding:12px 18px;font-size:15px;font-weight:700;cursor:pointer}
button:disabled{opacity:.5;cursor:default}.bar{height:10px;background:#e2e8f0;border-radius:99px;overflow:hidden;margin:16px 0 8px}
.bar div{height:100%;width:0;background:#16a34a;transition:width .2s}#t{font-size:14px;font-weight:600}</style></head>
<body><div class="k"><h1>Datensicherung (data-Ordner)</h1>
<p>Erstellt eine Kopie aller CRM-Daten als <b>kg_crm_daten.tar.gz</b> und lädt sie herunter.
Es wird nichts verändert oder gelöscht.</p>
<button id="b" onclick="los(false)">Sicherung erstellen und herunterladen</button>
<p style="margin-top:18px">Nur für den Umzug auf den eigenen Server: zusätzlich alle Zugangsdaten
(Einstellungen + geheime Dateien). Datei danach nicht weitergeben.</p>
<button id="u" onclick="los(true)" style="background:#047857">Umzugspaket herunterladen</button>
<div class="bar"><div id="f"></div></div><div id="t"></div></div>
<script>
const t=document.getElementById('t'),f=document.getElementById('f'),b=document.getElementById('b'),u=document.getElementById('u');
const warte=ms=>new Promise(r=>setTimeout(r,ms));
async function json(u,o){const r=await fetch(u,Object.assign({cache:'no-store',credentials:'same-origin'},o||{}));
 if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}
async function los(paket){
 b.disabled=true;u.disabled=true;f.style.width='0';t.textContent='Sicherung wird erstellt …';
 try{
  let s=await json('/admin/datensicherung/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({paket:!!paket})});
  while(s.zustand==='laeuft'){await warte(2000);s=await json('/admin/datensicherung/status');}
  if(s.zustand!=='fertig')throw new Error(s.fehler||'Sicherung fehlgeschlagen');
  const teile=[];
  for(let i=0;i<s.teile;i++){
   t.textContent='Herunterladen: Teil '+(i+1)+' von '+s.teile+' ('+(s.groesse/1048576).toFixed(1)+' MB)';
   let r=null;
   for(let v=0;v<3&&!(r&&r.ok);v++){r=await fetch('/admin/datensicherung/teil/'+i,{cache:'no-store',credentials:'same-origin'});}
   if(!r.ok)throw new Error('Teil '+(i+1)+': HTTP '+r.status);
   teile.push(await r.arrayBuffer());f.style.width=((i+1)/s.teile*100)+'%';
  }
  const blob=new Blob(teile,{type:'application/gzip'});
  if(blob.size!==s.groesse)throw new Error('Größe stimmt nicht ('+blob.size+' statt '+s.groesse+')');
  const name=s.paket?'kg_crm_umzug.tar.gz':'kg_crm_daten.tar.gz';
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;
  document.body.appendChild(a);a.click();a.remove();
  await fetch('/admin/datensicherung/aufraeumen',{method:'POST',credentials:'same-origin'});
  t.textContent='Fertig: '+name+' ('+(s.groesse/1048576).toFixed(1)+' MB, '+s.dateien+' Dateien'
   +(s.paket?', '+s.einstellungen+' Einstellungen, '+s.geheime_dateien+' geheime Dateien':'')+', Stand '+s.erstellt_utc+' UTC)';
 }catch(e){t.textContent='Fehler: '+e.message;}
 b.disabled=false;u.disabled=false;
}
</script></body></html>"""


def register_umzug(app, login_required):

    if (os.getenv("KG_HINTER_PROXY") or "").strip() == "1":
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    @app.before_request
    def umzug_weiterleitung():
        ziel = (os.getenv("UMZUG_ZIEL") or "").strip().rstrip("/")
        codes = {c.strip() for c in (os.getenv("UMZUG_CODES") or "").split(",") if c.strip()}
        if not ziel or not codes or not request.path.startswith(WORKER_PREFIX):
            return None
        code = request.path[len(WORKER_PREFIX):].strip("/")
        if "*" not in codes and code not in codes:
            return None
        if urlparse(ziel).netloc.lower() == (request.host or "").lower():
            return None  # schon auf dem Ziel: keine Schleife
        url = ziel + request.path
        if request.query_string:
            url += "?" + request.query_string.decode("utf-8", "ignore")
        antwort = redirect(url, code=302)
        antwort.headers["Cache-Control"] = "no-store"
        return antwort

    @app.after_request
    def umzug_server_hinweis(antwort):
        text = (os.getenv("KG_SERVER_HINWEIS") or "").strip()
        if (not text or not request.path.startswith(WORKER_PREFIX) or antwort.status_code != 200
                or antwort.direct_passthrough or antwort.mimetype != "text/html"):
            return antwort
        html = antwort.get_data(as_text=True)
        if "</body>" not in html:
            return antwort
        schild = ('<div style="position:fixed;left:8px;bottom:8px;z-index:99999;background:#047857;color:#fff;'
                  'font:600 12px/1.2 system-ui,sans-serif;padding:5px 10px;border-radius:999px;'
                  'pointer-events:none;opacity:.92">' + escape(text) + '</div>')
        vorne, hinten = html.rsplit("</body>", 1)
        antwort.set_data(vorne + schild + "</body>" + hinten)
        return antwort

    @app.route("/admin/datensicherung")
    @login_required
    def umzug_datensicherung_seite():
        return Response(SEITE, mimetype="text/html", headers={"Cache-Control": "no-store"})

    @app.route("/admin/datensicherung/start", methods=["POST"])
    @login_required
    def umzug_datensicherung_start():
        with _lock:
            s = _status_lesen()
            if s.get("zustand") == "laeuft" and time.time() - s.get("seit", 0) < 1800:
                return jsonify(s)
            paket = bool((request.get_json(silent=True) or {}).get("paket"))
            s = {"zustand": "laeuft", "seit": time.time(), "paket": paket}
            _status_schreiben(s)
            threading.Thread(target=_sicherung_bauen, args=(paket,), daemon=True, name="kg-datensicherung").start()
        return jsonify(s)

    @app.route("/admin/datensicherung/status")
    @login_required
    def umzug_datensicherung_status():
        return jsonify(_status_lesen())

    @app.route("/admin/datensicherung/aufraeumen", methods=["POST"])
    @login_required
    def umzug_datensicherung_aufraeumen():
        # Nach dem Herunterladen: Datei (evtl. mit Zugangsdaten) vom Server löschen
        s = _status_lesen()
        if s.get("zustand") == "fertig":
            try:
                os.remove(os.path.join(SICHERUNG_DIR, DATEI_NAME))
            except OSError:
                pass
            _status_schreiben({"zustand": "leer"})
        return jsonify({"success": True})

    @app.route("/admin/datensicherung/teil/<int:nr>")
    @login_required
    def umzug_datensicherung_teil(nr):
        s = _status_lesen()
        pfad = os.path.join(SICHERUNG_DIR, DATEI_NAME)
        if s.get("zustand") != "fertig" or not os.path.isfile(pfad) or nr < 0 or nr >= s.get("teile", 0):
            return jsonify({"success": False, "error": "Teil nicht vorhanden"}), 404
        with open(pfad, "rb") as f:
            f.seek(nr * TEIL_GROESSE)
            daten = f.read(TEIL_GROESSE)
        return Response(daten, mimetype="application/octet-stream", headers={"Cache-Control": "no-store"})
