# KG CRM – Übergabe für den nächsten Chat (Stand 22.10.2026)

Inhaber: **Murat Kicci** (KG-Gebäudereinigung, Duisburg). Dieses Dokument reicht, um im neuen Chat „KG CRM“
nahtlos weiterzuarbeiten. Andere Systeme (Leon, KG Business, KG Scan, Leon SaaS) haben eigene Chats.

---

## 1. Arbeitsregeln (strikt)

- Antworten **auf Türkisch, kurz, Ergebnis zuerst**, einfache Sprache. Kopierbare Texte immer in Code-Blöcken
  (kein Blockquote – zerbricht beim Einfügen in WhatsApp).
- Murat nutzt **Windows PowerShell 5.1**. Server-Befehle immer als **ein** Einzeiler `ssh root@46.224.155.41 '…'`,
  **keine doppelten Anführungszeichen** innerhalb, kein lokales `&&`.
- Claude hat **keinen Serverzugang**. Murat führt Befehle aus und schickt die Ausgabe. Nur lesende Prüfbefehle vorschlagen.
- **Nichts kaputt machen, nichts löschen, keine Eigeninitiative.** Nur das tun, worüber gesprochen wird. Wenn eine Änderung
  eine andere Funktion berühren könnte: **vorher das Risiko sagen**. Geprüfte Teile (Texte, Vorlagen) nicht „verbessern“.
- Wenn Murat sagt „önce … özetle / okey verdikten sonra“: **nur planen**, auf OK warten. **„yap“ = machen.**
  Ohne OK keine Dateien lesen/ändern, die zu einer neuen Umsetzung gehören.
- Echte Einstellungen nie zum Testen ändern, echte Leads/Kunden nie löschen, echte Daten nicht lesen.
  Tests nur mit **„ZZ Test …“**-Daten oder lokaler Kopie. In Tests keine echten Mails senden.
- Vor jedem Push: lokal testen, Diff prüfen. Nach dem Push: **genau den abgesicherten Server-Befehl** liefern (Abschnitt 3).
- Vor jedem Deploy beantworten: „Leon'u etkiler mi?“ → CRM-Deploys betreffen Leon **nicht**.
- UI-Texte **Deutsch**, keine türkischen Zeichen in der UI. CRM-Hauptfarbe Blau. Keine Zahlen erfinden.
- Keine Modellnamen in Commits. Commits enden mit den Attribution-Zeilen (Co-Authored-By / Claude-Session).
- `__pycache__` vor Abschluss entfernen.
- „Elden“ (schwarz) Lohn auszahlen: abgelehnt – dabei bleiben.

## 2. System

| | |
|---|---|
| Server | Hetzner `46.224.155.41` |
| Pfad / Dienst | `/opt/kg-crm`, systemd `kg-crm`, gunicorn `127.0.0.1:8803` |
| Domain | `https://portal.kg-reinigung.de` |
| Datenbank | SQLite `/opt/kg-crm/data/kg_portal.db` (`data/` ist git-ignoriert) |
| Geheimnisse | `/opt/kg-crm/tokenlar.env` (nie in Code/Chat) |
| Gmail | Gmail-API (`app2.get_gmail_service()`), Absender `info@kg-reinigung.de`; Schlüssel **nicht** in `static/` (geprüft: 404) |
| Backups | `/root/yedek/kg-crm_db_vor_<name>_<Zeit>.db` (vom Deploy-Befehl) |

Repo: `github.com/kg-portal/kg-portal`
- Entwicklung: Branch **`claude/nifty-galileo-19dve0`** (Stand `4acdb4c`)
- Live: Branch **`server-stand-20261019`** (letzter gepushter Live-Commit **`3b19ba2`**)
- Murat hat zuletzt bis `c0e22a8` bestätigt; ob `3b19ba2` („und mittig“) schon installiert ist, ist offen.
  Prüfen: `ssh root@46.224.155.41 'cd /opt/kg-crm; git log --oneline -1'`

**CRLF-Dateien – CRLF beibehalten:** `app2.py`, `templates/datenbank.html`, `besichtigung.html`, `base.html`,
`angebotvorlage.html`, `Leistungsverzeichnis.html`, `Mitarbeiter.html`, `gmail_mini.html`.

## 3. Deploy-Verfahren (bewährt)

1. Änderung auf dem Dev-Branch committen und pushen.
2. **Live-Commit mit Git-Plumbing** auf dem aktuellen Live-Stand bauen (nur die geänderten Dateien):
   ```bash
   BASE=<aktueller server-stand Commit>
   git fetch origin server-stand-20261019 && [ "$(git rev-parse FETCH_HEAD)" = "$BASE" ]
   # vorher prüfen: jede geänderte Datei auf dem Dev-Branch HEAD~1 == $BASE:Datei
   export GIT_INDEX_FILE=/tmp/idx && rm -f $GIT_INDEX_FILE && git read-tree $BASE
   git update-index --add --cacheinfo 100644,$(git rev-parse HEAD:<datei>),<datei>   # je Datei
   T=$(git write-tree); C=$(git log -1 --format=%B HEAD | git commit-tree $T -p $BASE)
   git push origin $C:refs/heads/server-stand-20261019; unset GIT_INDEX_FILE
   ```
3. Murat bekommt den **abgesicherten Befehl** (HEAD-Prüfung, saubere Arbeitskopie, DB-Sicherung mit Integritätsprüfung,
   fetch, FETCH_HEAD-Prüfung, `merge --ff-only`, Neustart, `/login` muss 200 liefern, sonst automatisch `git reset --keep`
   zurück; am Ende `OK …`). Vorlage – nur `<ALT>`, `<NEU>`, `<name>`, `<grep>`, `<Text>` ersetzen:

```
ssh root@46.224.155.41 'cd /opt/kg-crm || exit 1; A=$(git rev-parse HEAD); [ $A = <ALT> ] || { echo STOP HEAD unerwartet $A; exit 1; }; N=$(git status --porcelain --untracked-files=no | wc -l); [ $N -eq 0 ] || { echo STOP Dateien geaendert; git status --porcelain --untracked-files=no; exit 1; }; Z=$(date +%Y%m%d_%H%M%S); echo aW1wb3J0IHNxbGl0ZTMsIHN5cwpxdWVsbGUsIHppZWwgPSBzeXMuYXJndlsxXSwgc3lzLmFyZ3ZbMl0KcSA9IHNxbGl0ZTMuY29ubmVjdCgiZmlsZToiICsgcXVlbGxlICsgIj9tb2RlPXJvIiwgdXJpPVRydWUsIHRpbWVvdXQ9MzApCnogPSBzcWxpdGUzLmNvbm5lY3QoemllbCkKcS5iYWNrdXAoeikKciA9IHouZXhlY3V0ZSgiUFJBR01BIGludGVncml0eV9jaGVjayIpLmZldGNob25lKClbMF0KbiA9IHouZXhlY3V0ZSgiU0VMRUNUIGNvdW50KCopIEZST00gc3FsaXRlX21hc3RlciBXSEVSRSB0eXBlPSd0YWJsZSciKS5mZXRjaG9uZSgpWzBdCnouY2xvc2UoKQpxLmNsb3NlKCkKcHJpbnQoIkRCIiwgcXVlbGxlLCAiLT4iLCB6aWVsLCAifCBpbnRlZ3JpdHk6IiwgciwgInwgVGFiZWxsZW46IiwgbikKc3lzLmV4aXQoMCBpZiByID09ICJvayIgZWxzZSAyKQo= | base64 -d | python3 - /opt/kg-crm/data/kg_portal.db /root/yedek/kg-crm_db_vor_<name>_$Z.db || { echo STOP DB-Sicherung; exit 1; }; git fetch -q origin server-stand-20261019 || { echo STOP fetch; exit 1; }; [ $(git rev-parse FETCH_HEAD) = <NEU> ] || { echo STOP falscher Stand; exit 1; }; git merge -q --ff-only FETCH_HEAD || { echo STOP merge; exit 1; }; systemctl restart kg-crm; sleep 7; C=$(curl -s -o /dev/null -w %{http_code} http://127.0.0.1:8803/login); if systemctl is-active --quiet kg-crm && [ $C = 200 ]; then echo OK Dienst laeuft; else echo STOP Dienst $C - gehe zurueck; git reset -q --keep $A; systemctl restart kg-crm; sleep 6; systemctl is-active kg-crm; exit 1; fi; journalctl -u kg-crm --since -3min --no-pager | grep -i -e <grep> -e traceback | tail -5; git log --oneline -1; echo OK <Text>'
```

Der base64-Teil ist nur dieses Sicherungsskript (keine Geheimnisse):
```python
import sqlite3, sys
quelle, ziel = sys.argv[1], sys.argv[2]
q = sqlite3.connect("file:" + quelle + "?mode=ro", uri=True, timeout=30)
z = sqlite3.connect(ziel); q.backup(z)
r = z.execute("PRAGMA integrity_check").fetchone()[0]
n = z.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
z.close(); q.close()
print("DB", quelle, "->", ziel, "| integrity:", r, "| Tabellen:", n)
sys.exit(0 if r == "ok" else 2)
```
Nach dem Befehl: Murat macht **Strg+F5**. Mit `bash -n` die Syntax des inneren Befehls prüfen, bevor er ihn bekommt.

## 4. Lokal testen

Der alte Test-Ordner (Scratchpad) existiert im neuen Chat nicht mehr. Neu aufbauen:
Live-Stand auschecken (`git worktree`/Kopie von `server-stand-20261019`), venv mit `requirements.txt`, Playwright-Chromium,
Test-DB nur mit **ZZ-Test-Daten**, Start z. B.
`KG_PORTAL_SECRET_KEY=testkey KG_PORTAL_PASSWORD=test gunicorn -w 1 --threads 4 -b 127.0.0.1:5069 app:app`
(Login `admin`/`test`). CDN-Aufrufe im Test abfangen, Gmail-Senden (`/api/gmail/send`) im Browser-Test abfangen.
Bei Vorlagen-Änderungen: alte und neue Version als Bild vergleichen (pixelgleich, wenn das Kästchen/Feature aus ist).

## 5. Was gebaut wurde (Auszug, alles live)

**Angebot**
- Angebot-Editor (`angebot_editor.py`, `templates/angebot_editor.html`, Vorlage `angebotvorlage.html`):
  Kalkulation (Lohn 15 €, Minijob-Abgaben 31,17 %, Material %, Aufschlag 40 %), Sonderleistungen nach m²,
  Leistungsart-Liste, LV-Zeilen, PDF ansehen/herunterladen, Angenommen/Abgelehnt/Erinnerung.
- **Leistung 5–8** über „+ Leistung“ / „− Leistung“; ab 5 Zeilen kompakter, Seite 3 bleibt eine Seite;
  Warnung „Seite 3 im Angebot ist voll“. Mit 1–4 Zeilen pixelgleich wie vorher.

**Vertrag** (`vertrag_editor.py`, `templates/vertrag_editor.html`, `vertrag_vorlage.html`, Mail `vertrag_mail.html`)
- § 7 als Tabelle (Netto/MwSt/Brutto), Probezeit 1/2/3 Monate, Büro-Ort Duisburg, Uhrzeiten je Wochentag,
  Strich-Zeilen gelten als leer, „PDF ansehen“ öffnet zum Lesen.
- KG-Stempel auf der **Auftragnehmer**-Linie (Seite 4).
- **Reinigungsobjekt mit eigener Adresse** (Kästchen): Seite 1 „wird über folgendes Objekt: …“ und § 1 nehmen die
  Objektadresse; oben/„Zwischen“ bleibt Firmen-/Rechnungsadresse; Vertrag-Mail und Schlüsselprotokoll ebenfalls Objekt.
  Felder `objekt_anders`, `objekt_adresse`, `objekt_plz`, `objekt_ort`; Hilfsfunktion `objekt_adresse(f)`.
- Seite 1: Datum rechtsbündig mit Kontakt-Block (22,5 mm), „-nachfolgend Auftraggeber-“ bei 170,7 mm, „und“ mittig 186,9 mm.
- Vertrag-Mail: SEPA-Absatz („Übrigens: …“) + **leere Zeile für den GoCardless-Link** (Murat fügt ihn jedes Mal ein;
  Warnung vor dem Senden ohne Link), Satz zur Rechnungs-E-Mail, Schlüsselübergabe Ja/Nein + Datum.
- `angebot_bestaetigen(conn, tid)` = Knopf „✓ Bestätigt → Vertrag“ (auch von der E-Unterschrift genutzt).
- Vertrag hat weiterhin **max. 4 Leistungen** (Angebot 8) – offen.

**Weitere**
- Schlüsselübergabeprotokoll (`schluessel.py`, oranger Knopf im Vertrag-Editor).
- Mitarbeiter: Premium-Karten, roter Kündigung-Knopf (`kuendigung.py`).
- Kundenweg-Board, Kundenakte (`kundenakte.py`, `/akte/<id>`), Vertretung, Leon-Ansichten im CRM (Orb-Design).

## 6. Digitale Unterschrift (E-Unterschrift) – `esign.py`

- Mail-Fenster Angebot (`gmail_mini.html`) und Vertrag (`vertrag_mail.html`) laden `static/esign_mail.js`.
  Kästchen „Button … digital unterschreiben einfügen (Test)“ – **Testphase: standardmäßig aus**.
  Angehakt → Absatz + blauer Button; beim Senden `POST /api/esign/anlegen` → persönlicher Link.
- Kunde ohne Login: `/unterschrift/<token>` (in `app.py` `auto_login_check` freigegeben). Seitenbilder, Name, Funktion,
  Unterschrift (Finger/Maus), Bestätigung. Link 30 Tage, einmalig, neuer Link ersetzt alten.
- Unterschriebenes PDF = versendetes PDF (SHA-256) + Unterschrift auf Auftraggeber-Linie + Seite „Unterschriftsprotokoll“
  (pypdf + Playwright). Dateien unter `data/esign/<id>/`. Tabelle `esign_vorgang` (nur Token-Hash gespeichert).
- Danach: Mail mit PDF an Kunden **und** an info@ (zwei Mails – gewollt); Angebot → Vertrag-Warteliste.
- CRM-Liste: `/unterschriften`. Live getestet: Angebot „Deneme 16.10.2026“ unterschrieben ✓.
  **Vertrag-Unterschrift live noch nicht getestet** (Link an KG-Store vom 20.10. 12:24 offen).
- Nach Murats OK: Kästchen standardmäßig an.

## 7. Offene Punkte (CRM)

1. Prüfen, ob Live-Stand `3b19ba2` installiert ist (sonst „und mittig“-Befehl ausführen lassen).
2. E-Unterschrift: Vertrag live testen; danach Kästchen standardmäßig an; ggf. Status in Kundenakte/Vertrag-Liste anzeigen.
3. Vertrag auch mit Leistung 5–8 (§ 7 passt heute nur 4 sicher – Layout prüfen).
4. KG Scan → CRM: Endpunkte `/internal/kg-scan/*` mit Header `X-KG-SCAN-TOKEN`, Server-Variable `KG_SCAN_API_TOKEN`.
   App sendet jetzt an `portal.kg-reinigung.de` (Branch im Repo `kgscanv2test`). Prüfbefehl Token-Gleichheit wurde gegeben
   (erwartet „TOKEN PASST“ + 403). Token steht in der App im Code – Sicherheitsrisiko, später austauschen.
5. Backlog: Rechner „Std/Woche“ direkt, LV-Zeilen-Kasten offen, Besichtigung 35 €/Std-Zusammenfassung, Teppich ×1,15,
   eine Einstellungsseite (Lohn/%/€-m²), verlorene Besichtigung-Daten aus Sicherung, Vertrag-Feld „zeiten“ als Textarea.
6. Server-Wartung (nur mit Murats OK): Log 1,7 GB, `/opt/kg-portal` (alte Kopie) aufräumen, `tokenlar.env` schützen,
   Hetzner-Backup im Panel aktivieren.

## 8. Nicht anfassen (andere Chats)

- **Leon / KG Business** (`/opt/kg-business`, Repo `kg-business-voice`), **Leon Reinigung** (`kg-reinigung-voice`),
  **Leon SaaS** (`leon-saas`, eigenes Projekt), **KG Scan** (`kgscanv2test`, iOS, Codemagic). CRM-Deploys betreffen diese nicht.
