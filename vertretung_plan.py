# =====================================================
# VERTRETUNG – Monatsplan (großer Kalender)
# Urlaub/Krank je Mitarbeiter im Monat, welche Einsätze dadurch offen sind,
# wer vertreten kann (frei an dem Tag, kennt das Objekt, hat noch Stunden
# bis zur Grenze von 40 Std. / 600 € – gleiche Rechnung wie der Stundenzettel).
#
# Gespeichert wird ausschließlich über die bestehenden Stundenzettel-Routen
# (/api/stundenzettel/save und /delete) – dadurch bleibt die Urlaubsrechnung
# (Resturlaub abziehen / zurückgeben) genau wie bisher. Die bisherigen Routen
# in vertrag_vertretung.py bleiben unverändert.
# =====================================================
from datetime import date, datetime, timedelta

from flask import jsonify, request

from stundenzettel_auto import (GELD_GRENZE, MONATE, SONDER_ORTE, STUNDEN_GRENZE, STUNDENLOHN, WOCHENTAGE,
                                _monat_param, _plan_laden, _stunden, ensure_tables as stz_tables, feiertage_nrw)

TAGE_KURZ = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
GRUENDE = ("Urlaub", "Krank")


def _zahl(v):
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _datum(v):
    try:
        return date.fromisoformat(str(v)[:10])
    except (TypeError, ValueError):
        return None


def _rechnung(roh, lohn):
    """Wie stundenzettel_auto.monat_rechnung: erst Krank, dann Urlaub, dann Arbeit bis 40 Std. (höchstens 600 €)."""
    krank = min(roh["krank"], STUNDEN_GRENZE)
    urlaub = min(roh["urlaub"], max(0, STUNDEN_GRENZE - krank))
    arbeit = min(roh["arbeit"], max(0, STUNDEN_GRENZE - krank - urlaub))
    summe = roh["krank"] + roh["urlaub"] + roh["arbeit"]
    gesamt = min(STUNDEN_GRENZE, summe)
    # freie Stunden bis zur Grenze (Stunden und Euro – je nachdem, was zuerst erreicht ist)
    obergrenze = min(STUNDEN_GRENZE, GELD_GRENZE / lohn) if lohn > 0 else STUNDEN_GRENZE
    return {"krank": round(krank, 2), "urlaub": round(urlaub, 2), "arbeit": round(arbeit, 2),
            "stunden": round(summe, 2), "euro": round(min(gesamt * lohn, GELD_GRENZE), 2),
            "frei_std": round(max(0.0, obergrenze - summe), 2), "frei_eur": round(max(0.0, GELD_GRENZE - min(summe * lohn, GELD_GRENZE)), 2),
            "extra": round(max(0.0, summe - STUNDEN_GRENZE), 2)}


def register_vertretung_plan(app, login_required, get_db_connection):
    from vertrag_vertretung import ensure_vertretung_table

    def _intern(pfad, body):
        """Über die bestehende Stundenzettel-Route speichern/löschen (gleiche Urlaubslogik)."""
        client = app.test_client()
        with client.session_transaction() as sess:
            sess["logged_in"] = True
        resp = client.post(pfad, json=body)
        data = resp.get_json(silent=True) or {}
        if not data.get("success"):
            raise ValueError(data.get("error") or f"Speichern fehlgeschlagen (HTTP {resp.status_code}).")

    def _mitarbeiter(conn):
        spalten = {r[1] for r in conn.execute("PRAGMA table_info(mitarbeiter)")}
        felder = ["id", "vorname", "nachname"] + [f for f in ("telefon", "art", "stundenlohn", "urlaub", "resturlaub", "eintrittsdatum") if f in spalten]
        reihen = conn.execute(f"SELECT {', '.join(felder)} FROM mitarbeiter WHERE status = 'aktiv' ORDER BY sort_order, vorname").fetchall()
        liste = []
        for r in reihen:
            w = dict(r)
            vor, nach = (w.get("vorname") or "").strip(), (w.get("nachname") or "").strip()
            w["name"] = f"{vor} {nach}".strip() or f"#{w['id']}"
            w["kuerzel"] = ((vor[:1] + nach[:1]) or w["name"][:2]).upper()
            lohn = _zahl(w.get("stundenlohn"))
            w["lohn"] = lohn if lohn and lohn > 0 else STUNDENLOHN
            w["plan"] = _plan_laden(conn, w["id"])
            liste.append(w)
        return liste

    def _feiertage(start, ende):
        tage = {}
        for jahr in {start.year, (ende - timedelta(days=1)).year}:
            tage.update({d.isoformat(): n for d, n in feiertage_nrw(jahr).items()})
        return tage

    def _plan_tag(w, d, feiertage):
        """Fester Einsatz an diesem Tag laut Vorlage (oder None)."""
        p = w["plan"][WOCHENTAGE[d.weekday()]]
        eintritt = _datum(w.get("eintrittsdatum"))
        if not p["aktiv"] or d.isoformat() in feiertage or (eintritt and d < eintritt):
            return None
        return p

    # ------------------------------------------------- Monat
    @app.route("/api/vertretung/monat")
    @login_required
    def vertretung_monat():
        start, ende, monat = _monat_param(request.args.get("monat"))
        heute = date.today()
        conn = get_db_connection()
        try:
            stz_tables(conn)
            ensure_vertretung_table(conn)
            workers = _mitarbeiter(conn)
            namen = {w["id"]: w["name"] for w in workers}
            feiertage = _feiertage(start, ende)
            logs = {}
            for r in conn.execute("SELECT worker_id, datum, start_time, end_time, place FROM work_logs WHERE datum >= ? AND datum < ?",
                                  (start.isoformat(), ende.isoformat())):
                logs[(r["worker_id"], r["datum"])] = dict(r)
            vertretungen = [dict(r) for r in conn.execute(
                "SELECT * FROM vertretungen WHERE datum >= ? AND datum < ? ORDER BY datum, id", (start.isoformat(), ende.isoformat()))]
            gesperrt = {r["worker_id"] for r in conn.execute(
                "SELECT worker_id FROM stundenzettel_monate WHERE monat = ? AND status = 'bestaetigt'", (monat,))}
            # Wer kennt welches Objekt (Einsätze im letzten Jahr)
            kennt = {}
            seit = (start - timedelta(days=365)).isoformat()
            for r in conn.execute("SELECT worker_id, place, COUNT(*) AS n FROM work_logs WHERE datum >= ? AND COALESCE(place, '') <> '' GROUP BY worker_id, place", (seit,)):
                if r["place"] not in SONDER_ORTE:
                    kennt.setdefault(str(r["worker_id"]), {})[r["place"]] = r["n"]

            tage = []
            d = start
            while d < ende:
                tage.append({"datum": d.isoformat(), "tag": d.day, "wt": d.weekday(), "feiertag": feiertage.get(d.isoformat(), "")})
                d += timedelta(days=1)

            mitarbeiter, einsaetze = [], []
            for w in workers:
                roh_ist = {"arbeit": 0.0, "krank": 0.0, "urlaub": 0.0}
                roh_plan = {"arbeit": 0.0, "krank": 0.0, "urlaub": 0.0}
                eintraege, zaehler = {}, {"urlaub": 0, "krank": 0}
                d = start
                while d < ende:
                    iso = d.isoformat()
                    log = logs.get((w["id"], iso))
                    p = _plan_tag(w, d, feiertage)
                    if log:
                        ort = (log["place"] or "").strip()
                        h = _stunden(log["start_time"], log["end_time"]) if log["start_time"] and log["end_time"] else 0.0
                        art = "urlaub" if ort == "Urlaub" else "krank" if ort == "Krank" else "feiertag" if ort == "Feiertag" else "arbeit"
                        eintraege[iso] = {"art": art, "ort": ort, "start": log["start_time"] or "", "ende": log["end_time"] or "", "h": round(h, 2)}
                        topf = art if art in ("urlaub", "krank") else "arbeit"
                        roh_ist[topf] += h
                        roh_plan[topf] += h
                        if art in zaehler:
                            zaehler[art] += 1
                        if art in ("urlaub", "krank") and p:
                            v = next((x for x in vertretungen if x["datum"] == iso and x["abwesend_id"] == w["id"]), None)
                            einsaetze.append({"datum": iso, "abwesend_id": w["id"], "abwesend": w["name"], "grund": ort,
                                              "objekt": p["ort"], "start": p["start"], "ende": p["ende"], "h": round(_stunden(p["start"], p["ende"]), 2),
                                              "vertretung": v and {"id": v["id"], "vertreter_id": v["vertreter_id"],
                                                                   "name": namen.get(v["vertreter_id"], "?"),
                                                                   "start": v["start_time"], "ende": v["end_time"], "objekt": v["objekt"]}})
                    elif p:
                        h = _stunden(p["start"], p["ende"])
                        eintraege[iso] = {"art": "plan", "ort": p["ort"], "start": p["start"], "ende": p["ende"], "h": round(h, 2)}
                        roh_plan["arbeit"] += h
                    d += timedelta(days=1)
                feste = [{"tag": TAGE_KURZ[i], "start": w["plan"][t]["start"], "ende": w["plan"][t]["ende"], "ort": w["plan"][t]["ort"],
                          "h": round(_stunden(w["plan"][t]["start"], w["plan"][t]["ende"]), 2)}
                         for i, t in enumerate(WOCHENTAGE) if w["plan"][t]["aktiv"]]
                mitarbeiter.append({
                    "id": w["id"], "name": w["name"], "kuerzel": w["kuerzel"], "telefon": w.get("telefon") or "",
                    "art": w.get("art") or "", "lohn": w["lohn"],
                    "urlaub": _zahl(w.get("urlaub")), "resturlaub": _zahl(w.get("resturlaub")),
                    "feste": feste, "wochenstunden": round(sum(x["h"] for x in feste), 2),
                    "ist": _rechnung(roh_ist, w["lohn"]), "plan": _rechnung(roh_plan, w["lohn"]),
                    "urlaub_tage": zaehler["urlaub"], "krank_tage": zaehler["krank"],
                    "gesperrt": w["id"] in gesperrt, "tage": eintraege,
                })
            return jsonify({"success": True, "monat": monat, "titel": f"{MONATE[start.month - 1]} {start.year}",
                            "heute": heute.isoformat(), "tage": tage, "mitarbeiter": mitarbeiter, "einsaetze": einsaetze,
                            "kennt": kennt, "grenze": {"stunden": STUNDEN_GRENZE, "euro": GELD_GRENZE}})
        finally:
            conn.close()

    # ------------------------------------------------- Abwesenheit (Vorschau + Speichern)
    @app.route("/api/vertretung/plan/abwesenheit", methods=["POST"])
    @login_required
    def vertretung_plan_abwesenheit():
        d = request.get_json(silent=True) or {}
        grund = d.get("grund") if d.get("grund") in GRUENDE else None
        von, bis = _datum(d.get("von")), _datum(d.get("bis") or d.get("von"))
        try:
            wid = int(d.get("worker_id"))
        except (TypeError, ValueError):
            wid = None
        if not (grund and von and bis and wid):
            return jsonify({"success": False, "error": "Mitarbeiter, Grund und Datum angeben."}), 400
        if bis < von or (bis - von).days > 92:
            return jsonify({"success": False, "error": "Zeitraum prüfen (höchstens 3 Monate)."}), 400
        conn = get_db_connection()
        try:
            stz_tables(conn)
            ensure_vertretung_table(conn)
            w = next((x for x in _mitarbeiter(conn) if x["id"] == wid), None)
            if not w:
                return jsonify({"success": False, "error": "Mitarbeiter nicht gefunden (oder nicht aktiv)."}), 404
            feiertage = _feiertage(von, bis + timedelta(days=1))
            hat_plan = any(w["plan"][t]["aktiv"] for t in WOCHENTAGE)
            logs = {r["datum"]: dict(r) for r in conn.execute(
                "SELECT datum, start_time, end_time, place FROM work_logs WHERE worker_id = ? AND datum >= ? AND datum <= ?",
                (wid, von.isoformat(), bis.isoformat()))}
            vertreter_tage = {r["datum"]: dict(r) for r in conn.execute(
                "SELECT * FROM vertretungen WHERE vertreter_id = ? AND datum >= ? AND datum <= ?", (wid, von.isoformat(), bis.isoformat()))}
            gesperrt = {r["monat"] for r in conn.execute(
                "SELECT monat FROM stundenzettel_monate WHERE worker_id = ? AND status = 'bestaetigt'", (wid,))}
        finally:
            conn.close()

        tage, eintraege, abzug = [], [], 0
        tag = von
        while tag <= bis:
            iso = tag.isoformat()
            log = logs.get(iso)
            zeile = {"datum": iso, "label": f"{TAGE_KURZ[tag.weekday()]} {tag.strftime('%d.%m.')}"}
            p = _plan_tag(w, tag, feiertage)
            if iso in feiertage:
                zeile.update(status="aus", text=f"Feiertag ({feiertage[iso]})")
            elif hat_plan and not p:
                zeile.update(status="aus", text="kein Arbeitstag laut festen Zeiten")
            elif not hat_plan and tag.weekday() == 6:
                zeile.update(status="aus", text="Sonntag")
            elif log and (log["place"] or "") == grund:
                zeile.update(status="aus", text=f"schon {grund}")
            else:
                start, ende = (p["start"], p["ende"]) if p else ("", "")
                h = round(_stunden(start, ende), 2) if p else 0
                text = f"{grund}" + (f" · {start}–{ende} ({f'{h:g}'.replace('.', ',')} Std. bezahlt)" if p else " (ohne feste Zeiten)")
                hinweise = []
                if log:
                    alt = (log["place"] or "").strip()
                    hinweise.append(f"ersetzt {alt} {str(log['start_time'] or '')[:5]}–{str(log['end_time'] or '')[:5]}".strip(" –"))
                if iso in vertreter_tage:
                    hinweise.append(f"Vertretung am {tag.strftime('%d.%m.')} wird wieder frei")
                if iso[:7] in gesperrt:
                    hinweise.append("Monat ist schon bestätigt")
                zeile.update(status="neu", text=text, hinweis="; ".join(hinweise))
                eintraege.append({"date": iso, "start": start, "end": ende, "place": grund, "signed": False})
                if grund == "Urlaub" and not (log and log["place"] == "Urlaub"):
                    abzug += 1
            tage.append(zeile)
            tag += timedelta(days=1)

        rest = _zahl(w.get("resturlaub"))
        antwort = {"success": True, "tage": tage, "anzahl": len(eintraege), "urlaub_abzug": abzug,
                   "resturlaub_vorher": rest, "resturlaub_nachher": (max(0, rest - abzug) if rest is not None else None),
                   "name": w["name"], "ohne_plan": not hat_plan}
        if not d.get("speichern"):
            return jsonify(antwort)
        if not eintraege:
            return jsonify({"success": False, "error": "Keine Tage zum Eintragen in diesem Zeitraum."}), 400
        try:
            _intern("/api/stundenzettel/save", {"worker_id": wid, "entries": eintraege})
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        # Hatte der Mitarbeiter an diesen Tagen selbst eine Vertretung, ist die jetzt überschrieben → wieder offen
        neu = {e["date"] for e in eintraege}
        frei = [v["id"] for iso, v in vertreter_tage.items() if iso in neu]
        if frei:
            conn = get_db_connection()
            try:
                conn.executemany("DELETE FROM vertretungen WHERE id = ?", [(i,) for i in frei])
                conn.commit()
            finally:
                conn.close()
        antwort["gespeichert"] = True
        return jsonify(antwort)

    @app.route("/api/vertretung/plan/abwesenheit/entfernen", methods=["POST"])
    @login_required
    def vertretung_plan_abwesenheit_entfernen():
        d = request.get_json(silent=True) or {}
        tag = _datum(d.get("datum"))
        try:
            wid = int(d.get("worker_id"))
        except (TypeError, ValueError):
            wid = None
        if not (tag and wid):
            return jsonify({"success": False, "error": "Mitarbeiter und Datum angeben."}), 400
        conn = get_db_connection()
        try:
            ensure_vertretung_table(conn)
            log = conn.execute("SELECT place FROM work_logs WHERE worker_id = ? AND datum = ?", (wid, tag.isoformat())).fetchone()
            vertretung = conn.execute("SELECT v.id, m.vorname, m.nachname FROM vertretungen v LEFT JOIN mitarbeiter m ON m.id = v.vertreter_id "
                                      "WHERE v.abwesend_id = ? AND v.datum = ?", (wid, tag.isoformat())).fetchone()
        finally:
            conn.close()
        if not log or (log["place"] or "") not in GRUENDE:
            return jsonify({"success": False, "error": "An diesem Tag ist kein Urlaub/Krank eingetragen."}), 404
        try:
            _intern("/api/stundenzettel/delete", {"worker_id": wid, "date": tag.isoformat()})
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        antwort = {"success": True, "grund": log["place"]}
        if vertretung:
            antwort["vertretung"] = {"id": vertretung["id"], "name": f"{vertretung['vorname'] or ''} {vertretung['nachname'] or ''}".strip()}
        return jsonify(antwort)

    # ------------------------------------------------- Vertretung eintragen / entfernen
    @app.route("/api/vertretung/plan/eintragen", methods=["POST"])
    @login_required
    def vertretung_plan_eintragen():
        d = request.get_json(silent=True) or {}
        tag = _datum(d.get("datum"))
        try:
            vertreter = int(d.get("vertreter_id"))
            abwesend = int(d.get("abwesend_id")) if d.get("abwesend_id") else None
        except (TypeError, ValueError):
            vertreter = None
        objekt = str(d.get("objekt") or "").strip()[:120]
        start, ende = str(d.get("start") or "")[:5], str(d.get("ende") or "")[:5]
        if not (tag and vertreter and objekt and start and ende):
            return jsonify({"success": False, "error": "Vertretung, Datum, Objekt und Zeit angeben."}), 400
        conn = get_db_connection()
        try:
            stz_tables(conn)
            ensure_vertretung_table(conn)
            w = next((x for x in _mitarbeiter(conn) if x["id"] == vertreter), None)
            vorhanden = conn.execute("SELECT place, start_time, end_time FROM work_logs WHERE worker_id = ? AND datum = ?",
                                     (vertreter, tag.isoformat())).fetchone()
            schon = abwesend and conn.execute("SELECT id FROM vertretungen WHERE abwesend_id = ? AND datum = ?",
                                               (abwesend, tag.isoformat())).fetchone()
        finally:
            conn.close()
        if not w:
            return jsonify({"success": False, "error": "Mitarbeiter nicht gefunden (oder nicht aktiv)."}), 404
        if vorhanden:
            return jsonify({"success": False, "error": f"{w['name']} hat an diesem Tag schon einen Eintrag ({vorhanden['place']})."}), 409
        if _plan_tag(w, tag, _feiertage(tag, tag + timedelta(days=1))):
            return jsonify({"success": False, "error": f"{w['name']} arbeitet an diesem Tag selbst laut festen Zeiten."}), 409
        if schon:
            return jsonify({"success": False, "error": "Für diesen Einsatz ist schon eine Vertretung eingetragen."}), 409
        try:
            _intern("/api/stundenzettel/save", {"worker_id": vertreter, "entries": [
                {"date": tag.isoformat(), "start": start, "end": ende, "place": objekt, "signed": False}]})
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        conn = get_db_connection()
        try:
            conn.execute("INSERT INTO vertretungen (datum, abwesend_id, vertreter_id, objekt, start_time, end_time, erstellt_am) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (tag.isoformat(), abwesend, vertreter, objekt, start, ende, datetime.now().isoformat(timespec="seconds")))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True})

    @app.route("/api/vertretung/plan/vertretung/entfernen", methods=["POST"])
    @login_required
    def vertretung_plan_vertretung_entfernen():
        d = request.get_json(silent=True) or {}
        try:
            vid = int(d.get("id"))
        except (TypeError, ValueError):
            return jsonify({"success": False, "error": "Vertretung angeben."}), 400
        conn = get_db_connection()
        try:
            ensure_vertretung_table(conn)
            v = conn.execute("SELECT * FROM vertretungen WHERE id = ?", (vid,)).fetchone()
            log = v and conn.execute("SELECT place, start_time, end_time FROM work_logs WHERE worker_id = ? AND datum = ?",
                                     (v["vertreter_id"], v["datum"])).fetchone()
        finally:
            conn.close()
        if not v:
            return jsonify({"success": False, "error": "Vertretung nicht gefunden."}), 404
        # Stundenzettel-Eintrag der Vertretung nur löschen, wenn er noch genau so ist wie eingetragen
        passt = log and (log["place"] or "") == (v["objekt"] or "") and str(log["start_time"] or "")[:5] == str(v["start_time"] or "")[:5] \
            and str(log["end_time"] or "")[:5] == str(v["end_time"] or "")[:5]
        if passt:
            try:
                _intern("/api/stundenzettel/delete", {"worker_id": v["vertreter_id"], "date": v["datum"]})
            except ValueError as exc:
                return jsonify({"success": False, "error": str(exc)}), 400
        conn = get_db_connection()
        try:
            conn.execute("DELETE FROM vertretungen WHERE id = ?", (vid,))
            conn.commit()
        finally:
            conn.close()
        return jsonify({"success": True, "stundenzettel_geloescht": bool(passt)})
