from flask import Flask, render_template, request, redirect, url_for
import sqlite3
import json
import os

app = Flask(__name__)
DB_PATH = os.path.join('data', 'kg_portal.db')

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/kunden", methods=['GET', 'POST'])
def kunden():
    conn = get_db_connection()
    if request.method == 'POST':
        kunde_id = request.form.get('kunde_id')
        data_json = json.dumps(request.form.to_dict())
        if kunde_id and kunde_id.strip():
            conn.execute('''
                UPDATE kunden SET
                    firma=?, ort=?, monat=?, strasse=?, plz=?,
                    ansprechpartner_name=?, telefon=?, email=?,
                    kundennummer=?, vertrag_beginn=?, vertrag_ende=?,
                    haeufigkeit=?, data_json=?
                WHERE id=?
            ''', (
                request.form.get('firma'), request.form.get('stadt'),
                request.form.get('betrag'), request.form.get('strasse'),
                request.form.get('plz'), request.form.get('name'),
                request.form.get('telefon'), request.form.get('email'),
                request.form.get('kundennummer'), request.form.get('beginn'),
                request.form.get('ende'), request.form.get('haeufigkeit'),
                data_json, kunde_id
            ))
        else:
            conn.execute('''
                INSERT INTO kunden (
                    firma, ort, monat, strasse, plz, ansprechpartner_name,
                    telefon, email, kundennummer, vertrag_beginn, vertrag_ende,
                    haeufigkeit, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                request.form.get('firma'), request.form.get('stadt'),
                request.form.get('betrag'), request.form.get('strasse'),
                request.form.get('plz'), request.form.get('name'),
                request.form.get('telefon'), request.form.get('email'),
                request.form.get('kundennummer'), request.form.get('beginn'),
                request.form.get('ende'), request.form.get('haeufigkeit'),
                data_json
            ))
        conn.commit()
        conn.close()
        return redirect(url_for('kunden'))
    
    kunden = conn.execute('SELECT * FROM kunden ORDER BY id DESC').fetchall()
    conn.close()
    return render_template("kunden.html", kunden=kunden)

@app.route("/kunden/delete/<int:id>")
def delete_kunde(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM kunden WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('kunden'))

@app.route("/mitarbeiter", methods=['GET', 'POST'])
def mitarbeiter():
    conn = get_db_connection()
    if request.method == 'POST':
        mitarbeiter_id = request.form.get('mitarbeiter_id')
        data_json = json.dumps(request.form.to_dict())
        if mitarbeiter_id and mitarbeiter_id.strip():
            conn.execute('''
                UPDATE mitarbeiter SET
                    vorname=?, nachname=?, ort=?, strasse=?, plz=?,
                    geburtsdatum=?, eintrittsdatum=?, telefon=?, email=?,
                    steuer_id=?, sv_nummer=?, krankenkasse=?, iban=?,
                    stundenlohn=?, urlaub=?, resturlaub=?, art=?, data_json=?
                WHERE id=?
            ''', (
                request.form.get('vorname'), request.form.get('nachname'),
                request.form.get('stadt'), request.form.get('strasse'),
                request.form.get('plz'), request.form.get('geburtsdatum'),
                request.form.get('eintrittsdatum'), request.form.get('telefon'),
                request.form.get('email'), request.form.get('steuer_id'),
                request.form.get('sv_nummer'), request.form.get('krankenkasse'),
                request.form.get('iban'), request.form.get('stundenlohn'),
                request.form.get('urlaub'), request.form.get('resturlaub'),
                request.form.get('art'), data_json, mitarbeiter_id
            ))
        else:
            conn.execute('''
                INSERT INTO mitarbeiter (
                    vorname, nachname, ort, strasse, plz, geburtsdatum,
                    eintrittsdatum, telefon, email, steuer_id, sv_nummer,
                    krankenkasse, iban, stundenlohn, urlaub, resturlaub, art, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                request.form.get('vorname'), request.form.get('nachname'),
                request.form.get('stadt'), request.form.get('strasse'),
                request.form.get('plz'), request.form.get('geburtsdatum'),
                request.form.get('eintrittsdatum'), request.form.get('telefon'),
                request.form.get('email'), request.form.get('steuer_id'),
                request.form.get('sv_nummer'), request.form.get('krankenkasse'),
                request.form.get('iban'), request.form.get('stundenlohn'),
                request.form.get('urlaub'), request.form.get('resturlaub'),
                request.form.get('art'), data_json
            ))
        conn.commit()
        conn.close()
        return redirect(url_for('mitarbeiter'))

    mitarbeiter_liste = conn.execute('SELECT * FROM mitarbeiter ORDER BY created_at DESC').fetchall()
    conn.close()
    return render_template("Mitarbeiter.html", mitarbeiter_liste=mitarbeiter_liste)

@app.route("/mitarbeiter/delete/<int:id>")
def delete_mitarbeiter(id):
    conn = get_db_connection()
    conn.execute('DELETE FROM mitarbeiter WHERE id = ?', (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('mitarbeiter'))

@app.route("/kalender")
def kalender():
    return render_template("kalender.html")

if __name__ == "__main__":
    # Dış erişim için host 0.0.0.0 olarak güncellendi
    app.run(host='0.0.0.0', port=5000, debug=True)