# Premium-Design – Einspielen, Version 1 / Version 2 / alt

Stand: 17.10.2026 · Branch `claude/nifty-galileo-19dve0`

Es gibt drei Aussehen. Alle betreffen nur Vorlagen (`templates/`) – keine Python-Datei, keine Daten,
keine Funktion. Druck/PDF (Angebot, Leistungsverzeichnis, Verträge, Interesse-Druck) bleibt unverändert.
Datenbank- und Besichtigung-Seite sind in allen drei Ständen gleich (neu, freigegeben).

| Stand | Commit | Aussehen |
|---|---|---|
| alt | `37f3f5a` | bisheriges Aussehen |
| Version 1 | `dda1d6b` | hell, Systemblau-Köpfe, Glas-Kacheln, farbige Symbol-Quadrate, Schrift Inter |
| Version 2 „Graphit“ | aktueller Stand (HEAD) | Graphit-Menü, Kopfleiste mit Seitenname/Datum/Uhr, ruhige weiße Karten, feine Grafiken, Schrift Plus Jakarta Sans |

## 1. Neuen Stand holen (einmal, PowerShell)
Holt alles und zeigt Version 2:
```
ssh root@46.224.155.41 'cd /opt/kg-crm && git fetch -q origin claude/nifty-galileo-19dve0 && git merge -q --ff-only FETCH_HEAD && systemctl restart kg-crm && sleep 5 && systemctl is-active kg-crm && git log --oneline -1'
```

## 2. Umschalten (jederzeit, je ein Befehl)
Version 1 ansehen:
```
ssh root@46.224.155.41 'cd /opt/kg-crm && git checkout dda1d6b -- templates && systemctl restart kg-crm && sleep 5 && systemctl is-active kg-crm'
```
Version 2 ansehen:
```
ssh root@46.224.155.41 'cd /opt/kg-crm && git checkout HEAD -- templates && systemctl restart kg-crm && sleep 5 && systemctl is-active kg-crm'
```
Altes Aussehen:
```
ssh root@46.224.155.41 'cd /opt/kg-crm && git checkout 37f3f5a -- templates && systemctl restart kg-crm && sleep 5 && systemctl is-active kg-crm'
```
Nach jedem Umschalten im Browser Strg+F5.

Wichtig: Vor dem nächsten normalen Einspielen (Schritt 1) erst „Version 2 ansehen“ ausführen,
sonst bricht `git merge` wegen der umgeschalteten Vorlagen ab (es geht nichts kaputt, nur der neue Stand kommt nicht an).

## 2b. Einzelne Seiten (Seiten-Auswahl, aktueller Weg)
Der Inhaber wählt Seite für Seite. Gewählte Seiten liegen im Branch; jede wird einzeln eingespielt,
ohne `git merge` (Rahmen/Menü und alle anderen Seiten bleiben, wie sie auf dem Server sind).

**Kunden** = Version 1 + Grafik „Umsatzanteil“ + Kundenarten (Bestandskunden / Jährlich / Einmalig / Gekündigt,
Karten + Reiter, Art aus „Häufigkeit“, neue Auswahl „einmalig“) + Häufigkeit als Rangliste:
```
ssh root@46.224.155.41 'cd /opt/kg-crm && cp templates/kunden.html /root/kunden_vorher.html && git fetch -q origin claude/nifty-galileo-19dve0 && git checkout FETCH_HEAD -- templates/kunden.html && systemctl restart kg-crm && sleep 5 && systemctl is-active kg-crm'
```
Kunden zurück:
```
ssh root@46.224.155.41 'cd /opt/kg-crm && git checkout HEAD -- templates/kunden.html && systemctl restart kg-crm && sleep 5 && systemctl is-active kg-crm'
```

## 3. Mischen
Wenn Seiten aus Version 1 und Version 2 gemischt werden sollen (z. B. Startseite V1, Kalender V2):
Claude stellt den Mix im Repo zusammen, testet ihn und liefert danach wieder einen einzigen Einspiel-Befehl.
Der Rahmen (Menü links, Schrift, Kopfleiste) gilt für alle Seiten gemeinsam: entweder V1 oder V2.
