# To-Do ↔ KG LifeHub: das CRM zeigt die Aufgaben aus dem LifeHub (gleiche Daten wie dort „Aufgaben“),
# hakt sie ab / öffnet sie wieder und legt neue an – über die LifeHub-Schnittstelle mit eigenem Login
# (LIFEHUB_USER / LIFEHUB_PASSWORD in tokenlar.env). Die CRM-Datenbank und die alte To-Do-Liste bleiben unverändert.
# Ohne Zugang oder wenn LifeHub nicht antwortet: ok=False – die Seiten zeigen dann das Bisherige.
import os
import re
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import jsonify, render_template, request

BERLIN = ZoneInfo("Europe/Berlin")
HORIZONTE = ("today", "this_week", "month", "quarter", "half_year", "year", "two_years")
FELDER = ("id", "title", "description", "status", "visibility", "planned_date", "planned_time", "due_date",
          "due_time", "is_overdue", "reminder_count", "subtask_count", "subtask_done_count", "meta")
ID_MUSTER = re.compile(r"[0-9a-fA-F-]{36}")
# Gruppe und Farbe (wie Google Kalender) – gleiche Liste wie im LifeHub (frontend/src/lib/farben.ts), gespeichert in task.meta
FARBEN = ("tomate", "flamingo", "mandarine", "banane", "salbei", "basilikum", "pfau", "heidelbeere", "lavendel", "weintraube", "graphit")
GRUPPEN = ("Steuern & Finanzamt", "Bank & Zahlungen", "Büro & Verträge", "Kunden & Angebote", "Privat")
WARTEZEIT = 6


class LifeHubFehler(Exception):
    pass


class _Zugang:
    """Ein Login, der wiederverwendet wird; bei abgelaufener Sitzung einmal neu anmelden."""

    def __init__(self):
        self.lock = threading.Lock()
        self.kekse = None  # {"kg_session": …, "kg_csrf": …}
        self.pause_bis = 0.0  # nach falschem Login 5 Minuten nicht erneut versuchen (LifeHub-Konto nicht sperren)

    @staticmethod
    def _konfig():
        basis = (os.environ.get("LIFEHUB_API") or "http://127.0.0.1:8020/api/v1").rstrip("/")
        return basis, os.environ.get("LIFEHUB_USER", ""), os.environ.get("LIFEHUB_PASSWORD", "")

    def _anmelden(self, basis, user, passwort):
        if time.time() < self.pause_bis:
            raise LifeHubFehler("LifeHub-Anmeldung fehlgeschlagen – neuer Versuch in wenigen Minuten.")
        try:
            r = requests.post(basis + "/auth/login", json={"username": user, "password": passwort}, timeout=WARTEZEIT)
        except requests.RequestException:
            raise LifeHubFehler("LifeHub antwortet nicht.")
        if r.status_code != 200:
            if r.status_code in (400, 401, 403, 422, 429):
                self.pause_bis = time.time() + 300
            raise LifeHubFehler(f"LifeHub-Anmeldung fehlgeschlagen ({r.status_code}).")
        # Kekse direkt aus der Antwort (sie sind „Secure“ – über http://127.0.0.1 schickt requests sie sonst nicht mit)
        kekse = {k: v for k, v in r.cookies.items() if k in ("kg_session", "kg_csrf")}
        if "kg_session" not in kekse:
            raise LifeHubFehler("LifeHub-Anmeldung ohne Sitzung.")
        self.kekse = kekse

    def anfrage(self, methode, pfad, **kw):
        basis, user, passwort = self._konfig()
        if not user or not passwort:
            raise LifeHubFehler("LifeHub-Zugang fehlt (LIFEHUB_USER / LIFEHUB_PASSWORD in tokenlar.env).")
        with self.lock:
            for versuch in (1, 2):
                if self.kekse is None:
                    self._anmelden(basis, user, passwort)
                kopf = {"Cookie": "; ".join(f"{k}={v}" for k, v in self.kekse.items())}
                if methode != "GET" and self.kekse.get("kg_csrf"):
                    kopf["x-csrf-token"] = self.kekse["kg_csrf"]
                try:
                    r = requests.request(methode, basis + pfad, headers=kopf, timeout=WARTEZEIT, **kw)
                except requests.RequestException:
                    raise LifeHubFehler("LifeHub antwortet nicht.")
                if r.status_code in (401, 403) and versuch == 1:
                    self.kekse = None
                    continue
                if r.status_code >= 400:
                    raise LifeHubFehler(f"LifeHub-Fehler ({r.status_code}).")
                return r.json() if r.content else {}


ZUGANG = _Zugang()


def _kurz(t):
    k = {f: t.get(f) for f in FELDER}
    meta = t.get("meta") or {}
    k["meta"] = {x: meta.get(x) for x in ("gruppe", "farbe") if meta.get(x)}
    return k


def _wahl(e):
    gruppe = e.get("gruppe") if e.get("gruppe") in GRUPPEN else None
    farbe = e.get("farbe") if e.get("farbe") in FARBEN else None
    return gruppe, farbe


def _offen(t):
    return t.get("status") != "done"


def _datum(t):
    return t.get("planned_date") or t.get("due_date")


def horizont(h, intern=False):
    if not intern and h not in HORIZONTE:
        h = "today"
    d = ZUGANG.anfrage("GET", "/views/horizon", params={"h": h})
    ueberfaellig = [_kurz(t) for t in (d.get("overdue") or []) if _offen(t)]
    rest = [_kurz(t) for g in (d.get("groups") or []) for t in (g.get("items") or [])]
    rest += [_kurz(t) for t in (d.get("undated") or [])]
    return {"h": h, "ueberfaellig": ueberfaellig, "aufgaben": ueberfaellig + rest,
            "wiederkehrend": [{"title": p.get("title"), "datum": _datum(p)} for p in (d.get("projections") or [])][:8]}


def register_lifehub_bruecke(app, login_required):

    def fehler(exc):
        return jsonify({"ok": False, "fehler": str(exc)}), 503

    @app.route("/aufgaben")
    @login_required
    def lifehub_aufgaben_seite():
        return render_template("aufgaben.html")

    @app.route("/api/lifehub/aufgaben")
    @login_required
    def lifehub_aufgaben():
        try:
            daten = horizont(request.args.get("h", "today"))
        except LifeHubFehler as exc:
            return fehler(exc)
        daten.update(ok=True, heute=datetime.now(BERLIN).date().isoformat())
        return jsonify(daten)

    @app.route("/api/lifehub/aufgaben", methods=["POST"])
    @login_required
    def lifehub_aufgabe_neu():
        e = request.get_json(silent=True) or {}
        titel = str(e.get("titel") or "").strip()[:300]
        if not titel:
            return jsonify({"ok": False, "fehler": "Bitte einen Titel eingeben."}), 400
        neu = {"title": titel}
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(e.get("datum") or "")):
            neu["planned_date"] = e["datum"]
            if re.fullmatch(r"\d{2}:\d{2}", str(e.get("uhrzeit") or "")):
                neu["planned_time"] = e["uhrzeit"]
        notiz = str(e.get("notiz") or "").strip()
        if notiz:
            neu["description"] = notiz[:20000]
        gruppe, farbe = _wahl(e)
        if gruppe or farbe:
            neu["meta"] = {k: v for k, v in (("gruppe", gruppe), ("farbe", farbe)) if v}
        try:
            t = ZUGANG.anfrage("POST", "/tasks", json=neu)
        except LifeHubFehler as exc:
            return fehler(exc)
        return jsonify({"ok": True, "aufgabe": _kurz(t)})

    @app.route("/api/lifehub/aufgaben/<aufgabe_id>/<aktion>", methods=["POST"])
    @login_required
    def lifehub_aufgabe_aktion(aufgabe_id, aktion):
        if not ID_MUSTER.fullmatch(aufgabe_id) or aktion not in ("erledigt", "offen"):
            return jsonify({"ok": False, "fehler": "Ungültige Anfrage."}), 400
        try:
            t = ZUGANG.anfrage("POST", f"/tasks/{aufgabe_id}/{'complete' if aktion == 'erledigt' else 'reopen'}")
        except LifeHubFehler as exc:
            return fehler(exc)
        return jsonify({"ok": True, "aufgabe": _kurz(t)})

    @app.route("/api/lifehub/aufgaben/<aufgabe_id>/farbe", methods=["POST"])
    @login_required
    def lifehub_aufgabe_farbe(aufgabe_id):
        # Gruppe/Farbe ändern: vorhandene meta-Werte der Aufgabe bleiben erhalten
        if not ID_MUSTER.fullmatch(aufgabe_id):
            return jsonify({"ok": False, "fehler": "Ungültige Anfrage."}), 400
        gruppe, farbe = _wahl(request.get_json(silent=True) or {})
        try:
            alt = ZUGANG.anfrage("GET", f"/tasks/{aufgabe_id}")
            meta = dict(alt.get("meta") or {})
            for k, v in (("gruppe", gruppe), ("farbe", farbe)):
                if v:
                    meta[k] = v
                else:
                    meta.pop(k, None)
            t = ZUGANG.anfrage("PATCH", f"/tasks/{aufgabe_id}", json={"meta": meta})
        except LifeHubFehler as exc:
            return fehler(exc)
        return jsonify({"ok": True, "aufgabe": _kurz(t)})

    @app.route("/api/lifehub/startseite")
    @login_required
    def lifehub_startseite():
        # Kacheln wie LifeHub „Aufgaben → Heute“; Grafik: überfällig + fällig je Tag (heute + 6 Tage)
        try:
            heute_sicht = horizont("today")
            zwei = horizont("two_weeks", intern=True)
        except LifeHubFehler as exc:
            return fehler(exc)
        heute = datetime.now(BERLIN).date()
        h = heute.isoformat()
        alle = heute_sicht["aufgaben"]
        offen = [t for t in alle if _offen(t)]
        tage = [(heute + timedelta(days=i)).isoformat() for i in range(7)]
        je_tag = {d: 0 for d in tage}
        for t in zwei["aufgaben"]:
            if _offen(t) and _datum(t) in je_tag:
                je_tag[_datum(t)] += 1
        naechste = sorted((t for t in zwei["aufgaben"] if _offen(t)),
                          key=lambda t: (_datum(t) or "9999-99-99", t.get("planned_time") or t.get("due_time") or "99:99"))
        return jsonify({
            "ok": True, "heute": h,
            "offen": len(offen),
            "heute_faellig": sum(1 for t in offen if _datum(t) == h),
            "ueberfaellig": len(heute_sicht["ueberfaellig"]),
            "erledigt": len(alle) - len(offen),
            "gesamt": len(alle),
            "tage": [{"datum": d, "anzahl": je_tag[d]} for d in tage],
            "naechste": naechste[:10],
        })
