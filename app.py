# =====================================================
# KG-PORTAL V2
# ANA UYGULAMA DOSYASI (Flask)
# =====================================================

from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import json
import os

app = Flask(__name__)
DB_PATH = os.path.join('data', 'kg_portal.db')

# =====================================================
# VERİTABANI BAĞLANTISI
# =====================================================
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# =====================================================
# VERİTABANI OLUŞTURMA / KONTROL
# =====================================================
def init_db():
    if not os.path.exists('data'):
        os.makedirs('data')

    conn = get_db_connection()

    conn.execute('''
        CREATE TABLE IF NOT EXISTS kunden (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma TEXT NOT NULL,
            ort TEXT NOT NULL,
            monat REAL NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            strasse TEXT,
            plz TEXT,
            ansprechpartner_name TEXT,
            telefon TEXT,
            email TEXT,
            kundennummer TEXT,
            vertrag_beginn TEXT,
            vertrag_ende TEXT,
            haeufigkeit TEXT,
            vertragsstatus TEXT,
            vertragslaufzeit TEXT,
            data_json TEXT
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS mitarbeiter (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vorname TEXT NOT NULL,
            nachname TEXT NOT NULL,
            ort TEXT,
            strasse TEXT,
            plz TEXT,
            geburtsdatum TEXT,
            eintrittsdatum TEXT,
            telefon TEXT,
            email TEXT,
            steuer_id TEXT,
            sv_nummer TEXT,
            krankenkasse TEXT,
            iban TEXT,
            stundenlohn REAL,
            urlaub INTEGER,
            resturlaub INTEGER,
            art TEXT,
            data_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()

init_db()

# =====================================================
# ANA SAYFA
# =====================================================
@app.route("/")
def index():
    return render_template("index.html")

# =====================================================
# KUNDEN
# =====================================================
@app.route("/kunden", methods=["GET", "POST"])
def kunden():
    conn = get_db_connection()

    if request.method == "POST":
        form_data = request.form.to_dict()
        kunde_id = form_data.get("kunde_id")

        anrede = form_data.get("anrede", "").strip()
        name = form_data.get("name", "").strip()
        if anrede and name and not name.startswith(anrede):
            name = f"{anrede} {name}"

        data_json = json.dumps(form_data, ensure_ascii=False)

        if kunde_id:
            conn.execute("""
                UPDATE kunden SET
                    firma=?,
                    ort=?,
                    monat=?,
                    strasse=?,
                    plz=?,
                    ansprechpartner_name=?,
                    telefon=?,
                    email=?,
                    kundennummer=?,
                    vertrag_beginn=?,
                    vertrag_ende=?,
                    haeufigkeit=?,
                    vertragsstatus=?,
                    vertragslaufzeit=?,
                    data_json=?
                WHERE id=?
            """, (
                form_data.get("firma"),
                form_data.get("stadt"),
                form_data.get("betrag"),
                form_data.get("strasse"),
                form_data.get("plz"),
                name,
                form_data.get("telefon"),
                form_data.get("email"),
                form_data.get("kundennummer"),
                form_data.get("beginn"),
                form_data.get("ende"),
                form_data.get("haeufigkeit"),
                form_data.get("status"),
                form_data.get("laufzeit"),
                data_json,
                kunde_id
            ))
        else:
            conn.execute("""
                INSERT INTO kunden (
                    firma, ort, monat, strasse, plz,
                    ansprechpartner_name, telefon, email,
                    kundennummer, vertrag_beginn, vertrag_ende,
                    haeufigkeit, vertragsstatus, vertragslaufzeit, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                form_data.get("firma"),
                form_data.get("stadt"),
                form_data.get("betrag"),
                form_data.get("strasse"),
                form_data.get("plz"),
                name,
                form_data.get("telefon"),
                form_data.get("email"),
                form_data.get("kundennummer"),
                form_data.get("beginn"),
                form_data.get("ende"),
                form_data.get("haeufigkeit"),
                form_data.get("status"),
                form_data.get("laufzeit"),
                data_json
            ))

        conn.commit()
        conn.close()
        return redirect(url_for("kunden"))

    kunden = conn.execute(
        "SELECT * FROM kunden ORDER BY id DESC"
    ).fetchall()
    conn.close()
    return render_template("kunden.html", kunden=kunden)

# =====================================================
# KUNDEN LÖSCHEN
# =====================================================
@app.route("/kunden/delete/<int:id>")
def delete_kunde(id):
    conn = get_db_connection()
    conn.execute("DELETE FROM kunden WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for("kunden"))

# =====================================================
# MITARBEITER
# =====================================================
@app.route("/mitarbeiter", methods=["GET", "POST"])
def mitarbeiter():
    conn = get_db_connection()

    if request.method == "POST":
        form_data = request.form.to_dict()
        mitarbeiter_id = form_data.get("mitarbeiter_id")
        data_json = json.dumps(form_data, ensure_ascii=False)

        if mitarbeiter_id:
            conn.execute("""
                UPDATE mitarbeiter SET
                    vorname=?, nachname=?, ort=?, strasse=?, plz=?,
                    geburtsdatum=?, eintrittsdatum=?, telefon=?, email=?,
                    steuer_id=?, sv_nummer=?, krankenkasse=?, iban=?,
                    stundenlohn=?, urlaub=?, resturlaub=?, art=?, data_json=?
                WHERE id=?
            """, (
                form_data.get("vorname"),
                form_data.get("nachname"),
                form_data.get("stadt"),
                form_data.get("strasse"),
                form_data.get("plz"),
                form_data.get("geburtsdatum"),
                form_data.get("eintrittsdatum"),
                form_data.get("telefon"),
                form_data.get("email"),
                form_data.get("steuer_id"),
                form_data.get("sv_nummer"),
                form_data.get("krankenkasse"),
                form_data.get("iban"),
                form_data.get("stundenlohn"),
                form_data.get("urlaub"),
                form_data.get("resturlaub"),
                form_data.get("art"),
                data_json,
                mitarbeiter_id
            ))
        else:
            conn.execute("""
                INSERT INTO mitarbeiter (
                    vorname, nachname, ort, strasse, plz,
                    geburtsdatum, eintrittsdatum, telefon, email,
                    steuer_id, sv_nummer, krankenkasse, iban,
                    stundenlohn, urlaub, resturlaub, art, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                form_data.get("vorname"),
                form_data.get("nachname"),
                form_data.get("stadt"),
                form_data.get("strasse"),
                form_data.get("plz"),
                form_data.get("geburtsdatum"),
                form_data.get("eintrittsdatum"),
                form_data.get("telefon"),
                form_data.get("email"),
                form_data.get("steuer_id"),
                form_data.get("sv_nummer"),
                form_data.get("krankenkasse"),
                form_data.get("iban"),
                form_data.get("stundenlohn"),
                form_data.get("urlaub"),
                form_data.get("resturlaub"),
                form_data.get("art"),
                data_json
            ))

        conn.commit()
        conn.close()
        return redirect(url_for("mitarbeiter"))

    mitarbeiter_liste = conn.execute(
        "SELECT * FROM mitarbeiter ORDER BY created_at DESC"
    ).fetchall()
    conn.close()
    return render_template("Mitarbeiter.html", mitarbeiter_liste=mitarbeiter_liste)

# =====================================================
# MITARBEITER LÖSCHEN
# =====================================================
@app.route("/mitarbeiter/delete/<int:id>")
def delete_mitarbeiter(id):
    conn = get_db_connection()
    conn.execute("DELETE FROM mitarbeiter WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for("mitarbeiter"))

# =====================================================
# KALENDER
# =====================================================
@app.route("/kalender")
def kalender():
    return render_template("kalender.html")

# =====================================================
# UYGULAMA BAŞLAT
# =====================================================
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
