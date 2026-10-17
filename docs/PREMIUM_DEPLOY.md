# Premium-Design – Einspielen und Zurücksetzen

Stand: 17.10.2026 · Branch `claude/nifty-galileo-19dve0`

## Was ist neu
Einheitliches Premium-Design im ganzen CRM: Schrift Inter, Menü mit Symbol-Kacheln, Systemblau-Köpfe,
Kennzahl-Kacheln, lesende Grafiken (Ringe, Säulen, Donuts), Start-Animationen, Hover-Bewegung.
Nur Vorlagen (`templates/`) geändert – keine Python-Datei, keine Datenbank, keine Funktion.
Druck/PDF (Angebot, Leistungsverzeichnis, Verträge, Interesse-Druck) unverändert.

Commits: `1a5b60a` … `dd0d6f0` (Teil 1–6). Der Stand direkt davor ist `37f3f5a`
(Datenbank-Seite + Besichtigung im Bento-Design – das bleibt auch beim Zurücksetzen erhalten).

## Einspielen (PowerShell, ein Befehl)
```
ssh root@46.224.155.41 'cd /opt/kg-crm && git fetch -q origin claude/nifty-galileo-19dve0 && git merge -q --ff-only FETCH_HEAD && systemctl restart kg-crm && sleep 5 && systemctl is-active kg-crm && git log --oneline -1'
```
Erwartet: `active` und `… Premium-Deploy-Anleitung …` (oder neuer). Danach im Browser Strg+F5.

## Zurück zum alten Aussehen (PowerShell, ein Befehl)
Setzt nur die Vorlagen (`templates/`) auf den Stand `37f3f5a` zurück (vor dem Premium-Umbau,
Datenbank und Besichtigung bleiben neu). Python und Daten bleiben unverändert:
```
ssh root@46.224.155.41 'cd /opt/kg-crm && git checkout 37f3f5a -- templates && systemctl restart kg-crm && sleep 5 && systemctl is-active kg-crm && git status --short | head -3'
```
Erwartet: `active` und Zeilen mit `M templates/…` (die zurückgesetzten Vorlagen).

## Doch wieder Premium (nach dem Zurücksetzen)
```
ssh root@46.224.155.41 'cd /opt/kg-crm && git checkout HEAD -- templates && systemctl restart kg-crm && sleep 5 && systemctl is-active kg-crm && git status --short | head -3'
```
Erwartet: `active` und keine `M templates/…`-Zeilen.

Wichtig: Solange zurückgesetzt ist, vor dem nächsten normalen Einspielen erst „Doch wieder Premium“
ausführen – sonst bricht `git merge` wegen der geänderten Vorlagen ab (es geht nichts kaputt, nur
der neue Stand kommt nicht an).
