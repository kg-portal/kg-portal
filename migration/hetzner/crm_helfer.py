#!/usr/bin/env python3
"""Hilfsprogramm für das CRM auf Hetzner (nur Python-Standardbibliothek).

  crm_helfer.py start                        CRM starten (Einstellungen aus geheim/crm_env.json)
  crm_helfer.py aufruf /internal/...         Zeitplan-Aufruf an das laufende CRM (mit Token)
  crm_helfer.py sicherung                    tägliche Datenbank-Sicherung, 14 Tage behalten
  crm_helfer.py env-schreiben QUELLE DOMAIN  Einstellungen aus dem Umzugspaket übernehmen
"""
import glob
import json
import os
import secrets
import sqlite3
import sys
import time
import urllib.request

DIR = "/opt/kg-crm"
ENV_DATEI = os.path.join(DIR, "geheim", "crm_env.json")
PORT = 8803
NICHT_UEBERNEHMEN = ("KG_SERVER_HINWEIS", "PLAYWRIGHT_BROWSERS_PATH")


def env_laden():
    with open(ENV_DATEI, encoding="utf-8") as f:
        return json.load(f)


def start():
    env = dict(os.environ)
    env.update({k: str(v) for k, v in env_laden().items()})
    env["PYTHONUNBUFFERED"] = "1"
    gunicorn = os.path.join(DIR, ".venv", "bin", "gunicorn")
    os.chdir(DIR)
    os.execve(gunicorn, [gunicorn, "-w", "1", "--threads", "4", "--timeout", "180",
                         "-b", f"127.0.0.1:{PORT}", "app:app"], env)


def aufruf(pfad):
    token = env_laden().get("LEON_AUTO_TOKEN", "")
    anfrage = urllib.request.Request(f"http://127.0.0.1:{PORT}{pfad}", method="POST",
                                     headers={"X-Cron-Token": token})
    with urllib.request.urlopen(anfrage, timeout=900) as antwort:
        print(antwort.status, antwort.read(500).decode("utf-8", "replace"))


def sicherung():
    ziel = os.path.join(DIR, "sicherung")
    os.makedirs(ziel, mode=0o700, exist_ok=True)
    datei = os.path.join(ziel, time.strftime("kg_portal_%Y%m%d.db"))
    src = sqlite3.connect(os.path.join(DIR, "data", "kg_portal.db"), timeout=60)
    dst = sqlite3.connect(datei + ".teil")
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.replace(datei + ".teil", datei)
    alle = sorted(glob.glob(os.path.join(ziel, "kg_portal_*.db")))
    for alt in alle[:-14]:
        os.remove(alt)
    print("Sicherung:", datei, "| vorhanden:", min(len(alle), 14))


def env_schreiben(quelle, domain):
    with open(quelle, encoding="utf-8") as f:
        env = {k: str(v) for k, v in json.load(f).items()
               if not k.startswith("UMZUG_") and k not in NICHT_UEBERNEHMEN}
    alt = {}
    if os.path.exists(ENV_DATEI):
        with open(ENV_DATEI, encoding="utf-8") as f:
            alt = json.load(f)
    env["KG_PORTAL_BASE_URL"] = "https://" + domain
    env["KG_HINTER_PROXY"] = "1"
    env["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(DIR, "pw-browsers")
    if not env.get("LEON_AUTO_TOKEN"):
        env["LEON_AUTO_TOKEN"] = alt.get("LEON_AUTO_TOKEN") or secrets.token_urlsafe(32)
    os.makedirs(os.path.dirname(ENV_DATEI), mode=0o700, exist_ok=True)
    fd = os.open(ENV_DATEI + ".neu", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(env, f, ensure_ascii=False, indent=1)
    os.replace(ENV_DATEI + ".neu", ENV_DATEI)
    pflicht = [n for n in ("KG_PORTAL_SECRET_KEY", "KG_PORTAL_PASSWORD") if not env.get(n)]
    print(len(env), "Einstellungen übernommen" + (" | FEHLT: " + ", ".join(pflicht) if pflicht else ""))
    return 1 if pflicht else 0


if __name__ == "__main__":
    befehl = sys.argv[1] if len(sys.argv) > 1 else ""
    if befehl == "start":
        start()
    elif befehl == "aufruf" and len(sys.argv) == 3:
        aufruf(sys.argv[2])
    elif befehl == "sicherung":
        sicherung()
    elif befehl == "env-schreiben" and len(sys.argv) == 4:
        sys.exit(env_schreiben(sys.argv[2], sys.argv[3]))
    else:
        print(__doc__)
        sys.exit(2)
