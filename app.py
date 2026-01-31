
# =====================================================
# KG-PORTAL V2
# Bölüm 1- ANA UYGULAMA DOSYASI (Flask) 
# =====================================================

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from functools import wraps
import sqlite3
import json
import os
from leistungen import LEISTUNGEN
from sevdesk import sync_sevdesk_to_db, get_cached_rechnungen

app = Flask(__name__)
app.secret_key = 'kg_reinigung_ozel_anahtar_2026' # Güvenlik anahtarı
DB_PATH = os.path.join('data', 'kg_portal.db') 

# GİRİŞ BİLGİLERİN (Eşinle kullanacağın şifre)
USER_ID = "admin"
USER_PASS = "Secret8391." 

# BEKÇİ FONKSİYONU (Giriş kontrolü yapar)
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            # İşçi linkiyle geliniyorsa engelleme (ŞİFRESİZ GEÇİŞ)
            if request.path.startswith('/stundenzettel/worker/'):
                return f(*args, **kwargs)
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.before_request
def auto_login_check():
    # 1. Login, statik dosyalar veya zaten giriş yapmış olanlar geçer
    if request.endpoint in ['login', 'static'] or 'logged_in' in session:
        return
    
    # 2. İŞÇİ LİNKLERİ İÇİN ŞİFRE SORMADAN GEÇİŞ İZNİ
    if request.path.startswith('/stundenzettel/worker/'):
        return

    # 3. Diğer her yer için şifre ekranına yolla
    return redirect(url_for('login'))

# ... (Buradan aşağısı get_db_connection() diye devam ediyor, aynen kalsın)

# =====================================================
# Bölüm 2- VERİTABANI BAĞLANTISI
# =====================================================
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# =====================================================
# Bölüm 3- VERİTABANI OLUŞTURMA / KONTROL
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
            status TEXT DEFAULT 'aktiv',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    #####################################################################
    # >>>>>> 🔥 BURAYI EKLEDİM - VERİTABANI HATASINI ÇÖZEN KISIM 🔥 <<<<<<
    try:
        conn.execute("ALTER TABLE mitarbeiter ADD COLUMN access_code TEXT DEFAULT '1234'")
    except:
        pass # Eğer sütun zaten varsa hata vermez, sessizce geçer.
    # <<<<<< 🔥 BURASI BİTTİ 🔥 <<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<
    # ####################################################################

# To-Do Listesi Tablosu
    conn.execute('''
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL,
            done INTEGER DEFAULT 0,
            deadline TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Mevcut veritabanında 'deadline' sütunu yoksa zorla ekler (Hata Önleyici)
    try:
        conn.execute("ALTER TABLE todos ADD COLUMN deadline TEXT")
    except:
        pass

    # ANGEBOTE TABLOSU (YENİ)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS angebote (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma TEXT,
            ansprechpartner TEXT,
            strasse TEXT,
            plz TEXT,
            ort TEXT,
            m2 REAL,
            reinigungsart TEXT,
            haeufigkeit TEXT,
            leistungen_json TEXT,
            status TEXT DEFAULT 'Offen',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # BESICHTIGUNGSTERMINE TABLOSU
    conn.execute('''
        CREATE TABLE IF NOT EXISTS besichtigungen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            firma TEXT,
            ansprechpartner TEXT,
            telefon TEXT,
            email TEXT,
            strasse TEXT,
            plz TEXT,
            ort TEXT,
            termin_datum TEXT,
            termin_uhrzeit TEXT,
            status TEXT DEFAULT 'Geplant',
            notizen TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

# SEVDESK VERİ DEPOSU (IŞIK HIZI İÇİN)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sevdesk_cache (
            invoice_id TEXT PRIMARY KEY,
            nr TEXT,
            datum TEXT,
            kunde TEXT,
            brutto REAL,
            netto REAL,
            mwst REAL,
            offen REAL,
            status_code INTEGER,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

# BANKA İŞLEMLERİ ÖNBELLEĞİ (YENİ)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS bank_cache (
            transaction_id TEXT PRIMARY KEY,
            account_slug TEXT,
            payee TEXT,
            datum TEXT,
            description TEXT,
            amount REAL,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()

init_db()

# =====================================================
# Bölüm 4-GİRİŞ VE ÇIKIŞ İŞLEMLERİ (BURAYA GELDİ)
# =====================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form['username'] == USER_ID and request.form['password'] == USER_PASS:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            flash('Geçersiz kullanıcı adı veya şifre!', 'danger')
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

# =====================================================
# Bölüm 5- ANA SAYFA
# =====================================================
@app.route("/")
def index():
    conn = get_db_connection()

    # BURAYA EKLE (SevDesk Hesaplama) -------------------------
    sync_sevdesk_to_db() 
    import datetime
    now = datetime.datetime.now()
    
    search_pattern = f"{now.year}-{now.month:02d}-%"
    monat_row = conn.execute("SELECT SUM(brutto) FROM sevdesk_cache WHERE datum LIKE ?", (search_pattern,)).fetchone()
    monatlicher_umsatz = monat_row[0] if monat_row[0] else 0.0

    jahres_grafik_verisi = []
    jahres_umsatz = 0
    for m in range(1, 13):
        p = f"{now.year}-{m:02d}-%"
        r = conn.execute("SELECT SUM(brutto) FROM sevdesk_cache WHERE datum LIKE ?", (p,)).fetchone()
        val = r[0] if r[0] else 0.0
        jahres_grafik_verisi.append(val)
        jahres_umsatz += val
    # -------------------------------------------------------

    # Buradan sonrası senin eski kodların (Müşteri sayısı vs.) aynen devam etsin...
    
    # SADECE AKTİF MÜŞTERİ SAYISI
    customer_count = conn.execute("SELECT COUNT(*) FROM kunden WHERE vertragsstatus != 'gekuendigt' OR vertragsstatus IS NULL").fetchone()[0]
    
    # GRAFİK İÇİN AYLIK VERİ (Örnek: Her ay sisteme giren müşteri sayısı)
    # Bu sorgu her ay kaç müşteri eklendiğini sayar
    kunden_stats = conn.execute("""
        SELECT strftime('%m', created_at) as ay, COUNT(id) 
        FROM kunden 
        GROUP BY ay 
        ORDER BY ay ASC
    """).fetchall()
    
    # Grafik için 12 aylık bir liste hazırlıyoruz (boş aylar 0 görünür)
    kunden_grafik_verisi = [0] * 12
    for row in kunden_stats:
        index = int(row['ay']) - 1
        kunden_grafik_verisi[index] = row[1]

    # -----------------------------------------------------
    # 👥 AKTİF PERSONEL VERİLERİ (Mevcut Kodun)
    # -----------------------------------------------------
    employee_count = conn.execute("SELECT COUNT(*) FROM mitarbeiter WHERE status = 'aktiv'").fetchone()[0]
    
    personel_stats = conn.execute("""
        SELECT strftime('%m', created_at) as ay, COUNT(id) 
        FROM mitarbeiter 
        WHERE status = 'aktiv' 
        GROUP BY ay 
        ORDER BY ay ASC
    """).fetchall()
    
    personel_grafik_verisi = [0] * 12
    for row in personel_stats:
        idx = int(row['ay']) - 1
        personel_grafik_verisi[idx] = row[1]

    # -----------------------------------------------------
    # 📊 ✅ TO-DO HAFTALIK GRAFİK VERİSİ (YENİ EKİ)
    # -----------------------------------------------------
    import datetime
    today = datetime.datetime.now()
    todo_kw_labels = []
    todo_erledigt_verisi = []
    todo_offen_verisi = []

    # Yılın ilk gününü bul (KW 1 başlangıcı)
    start_of_year = datetime.datetime(today.year, 1, 1)
    # İlk haftanın Pazartesi gününe git
    start_date = start_of_year - datetime.timedelta(days=start_of_year.weekday())

    for i in range(52):
        week_start = start_date + datetime.timedelta(weeks=i)
        week_end = week_start + datetime.timedelta(days=6)
        kw_num = week_start.isocalendar()[1]
        todo_kw_labels.append(f"KW {kw_num}")
        
        erledigt = conn.execute("SELECT COUNT(*) FROM todos WHERE done = 1 AND deadline BETWEEN ? AND ?", 
                                (week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d'))).fetchone()[0]
        offen = conn.execute("SELECT COUNT(*) FROM todos WHERE done = 0 AND deadline BETWEEN ? AND ?", 
                             (week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d'))).fetchone()[0]
        
        todo_erledigt_verisi.append(erledigt)
        todo_offen_verisi.append(offen)

    # Başlangıçta hangi haftanın merkezde olacağını hesapla (Bugünkü hafta)
    current_kw = today.isocalendar()[1]
    initial_todo_index = max(0, min(current_kw - 3, 47))

    # =========================
    # TODO BATTERY YÜZDE HESABI (Genel Toplam Üzerinden)
    # =========================
    total_erledigt_row = conn.execute("SELECT COUNT(*) FROM todos WHERE done = 1").fetchone()
    total_offen_row = conn.execute("SELECT COUNT(*) FROM todos WHERE done = 0").fetchone()
    
    t_erledigt = total_erledigt_row[0] if total_erledigt_row else 0
    t_offen = total_offen_row[0] if total_offen_row else 0
    total_tasks = t_erledigt + t_offen
    todo_percent = int((t_erledigt / total_tasks) * 100) if total_tasks > 0 else 0


    conn.close()
    
    # MONATSTREND GRAFİĞİ İÇİN VERİ (Start, W1, W2, W3, Aktuell)
    monatstrend_grafik_verisi = [0, 2500, 5000, 7500, monatlicher_umsatz]
    
    # -----------------------------------------------------
    # 🚀 RENDER (TÜM VERİLER GÖNDERİLİYOR)
    # -----------------------------------------------------
    return render_template(
        "index.html",
        customer_count=customer_count,
        kunden_grafik_verisi=kunden_grafik_verisi,
        employee_count=employee_count,
        personel_grafik_verisi=personel_grafik_verisi,
        todo_kw_labels=todo_kw_labels,
        todo_erledigt_verisi=todo_erledigt_verisi,
        todo_offen_verisi=todo_offen_verisi,
        todo_percent=todo_percent,
        monatlicher_umsatz=monatlicher_umsatz,
        jahres_umsatz=jahres_umsatz,
        jahres_grafik_verisi=jahres_grafik_verisi,
        initial_todo_index=initial_todo_index,
        monatstrend_grafik_verisi=monatstrend_grafik_verisi
    )



# =====================================================
# Bölüm 6- KUNDEN
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
# Bölüm 7- KUNDEN LÖSCHEN
# =====================================================
@app.route("/kunden/delete/<int:id>")
def delete_kunde(id):
    conn = get_db_connection()
    conn.execute("DELETE FROM kunden WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for("kunden"))

# =====================================================
# Bölüm 8- MITARBEITER
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
# Bölüm 9- MITARBEITER LÖSCHEN
# =====================================================
@app.route("/mitarbeiter/delete/<int:id>")
def delete_mitarbeiter(id):
    conn = get_db_connection()
    # ESKİ SİLME KOMUTUNU SİLDİK, YERİNE BU GELDİ:
    conn.execute("UPDATE mitarbeiter SET status = 'gelöscht' WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for("mitarbeiter"))

@app.route("/mitarbeiter/activate/<int:id>")
def activate_mitarbeiter(id):
    conn = get_db_connection()
    # İŞÇİYİ TEKRAR YUKARIYA TAŞIMAK İÇİN:
    conn.execute("UPDATE mitarbeiter SET status = 'aktiv' WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for("mitarbeiter"))

@app.route("/mitarbeiter/hard_delete/<int:id>")
def hard_delete_mitarbeiter(id):
    conn = get_db_connection()
    # Bu komut personeli veritabanından tamamen siler!
    conn.execute("DELETE FROM mitarbeiter WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for("mitarbeiter"))

# =====================================================
# Bölüm 10- TO-DO LISTE (Haftalık Timeline Sistemi)
# =====================================================
@app.route("/todo")
def todo_index():
    import datetime
    conn = get_db_connection()
    
    # Deadline sütunu yoksa veritabanına ekle
    try:
        conn.execute("ALTER TABLE todos ADD COLUMN deadline TEXT")
        conn.commit()
    except:
        pass

    todos = conn.execute("SELECT * FROM todos ORDER BY deadline ASC").fetchall()
    conn.close()

    # Görevleri KW (Takvim Haftası) bazında gruplama mantığı
    grouped_todos = {}
    now = datetime.datetime.now()
    now_date = now.strftime('%Y-%m-%d')

    for todo in todos:
        if todo['deadline']:
            dt = datetime.datetime.strptime(todo['deadline'], '%Y-%m-%d')
            kw = dt.isocalendar()[1] # Hafta numarasını al (KW)
            key = f"KW {kw} ({dt.strftime('%d.%m.%Y')})"
        else:
            key = "Ungeplant"
        
        if key not in grouped_todos:
            grouped_todos[key] = []
        grouped_todos[key].append(todo)

    return render_template("todo.html", 
                           grouped_todos=grouped_todos, 
                           total_count=len(todos),
                           now_date=now_date)

@app.route("/todo/add", methods=["POST"])
def add_todo():
    task = request.form.get("task")
    deadline = request.form.get("deadline") # Tarih verisini alıyoruz
    if task:
        conn = get_db_connection()
        conn.execute("INSERT INTO todos (task, deadline) VALUES (?, ?)", (task, deadline))
        conn.commit()
        conn.close()
    return redirect(url_for("todo_index"))

@app.route("/todo/toggle/<int:id>")
def toggle_todo(id):
    conn = get_db_connection()
    conn.execute("UPDATE todos SET done = 1 - done WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for("todo_index"))

@app.route("/todo/delete/<int:id>")
def delete_todo(id):
    conn = get_db_connection()
    conn.execute("DELETE FROM todos WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for("todo_index"))

# --- DÜZENLEME İÇİN VERİ GETİRME ---
@app.route("/todo/get/<int:id>")
def get_todo(id):
    conn = get_db_connection()
    todo = conn.execute("SELECT * FROM todos WHERE id = ?", (id,)).fetchone()
    conn.close()
    if todo:
        return jsonify(dict(todo))
    return jsonify({"error": "Not found"}), 404

# --- DÜZENLENEN VERİYİ KAYDETME ---
@app.route("/todo/update", methods=["POST"])
def update_todo():
    todo_id = request.form.get("id")
    task = request.form.get("task")
    deadline = request.form.get("deadline")
    
    conn = get_db_connection()
    conn.execute("UPDATE todos SET task = ?, deadline = ? WHERE id = ?", (task, deadline, todo_id))
    conn.commit()
    conn.close()
    return redirect(url_for("todo_index"))

# =====================================================
# Bölüm 11- DATENBANK
# =====================================================
@app.route("/datenbank")
def datenbank():
    return render_template("datenbank.html")

# =====================================================
# Bölüm 12- KALENDER
# =====================================================
@app.route("/kalender")
def kalender():
    return render_template("kalender.html")

# =====================================================
# Bölüm 13- ANGEBOT & VERTRAG (STRATEJİK GÜNCELLEME)
# =====================================================

@app.route("/angebot")
def angebot_index():
    conn = get_db_connection()
    # Yeni dosya ismi "angebot&vertrag.html" olarak güncellendi
    angebote = conn.execute("SELECT * FROM angebote ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("angebot&vertrag.html", angebote=angebote)

@app.route("/angebot/create", methods=["POST"])
def create_angebot():
    f = request.form
    angebot_id = f.get("angebot_id") # Bearbeiten için ID kontrolü
    
    # Hizmet listesini (service_ ile başlayanlar) ayıklayıp JSON yapıyoruz
    leistungen = {k: v for k, v in f.items() if k.startswith('service_')}
    leistungen_json = json.dumps(leistungen, ensure_ascii=False)
    
    conn = get_db_connection()
    if angebot_id and angebot_id != "":
        # MEVCUT KAYDI GÜNCELLE (Bearbeiten Modu)
        conn.execute('''UPDATE angebote SET firma=?, ansprechpartner=?, strasse=?, plz=?, ort=?, 
                        m2=?, reinigungsart=?, haeufigkeit=?, leistungen_json=? WHERE id=?''',
                     (f.get("firma"), f.get("ansprechpartner"), f.get("strasse"), f.get("plz"), f.get("ort"),
                      f.get("m2"), f.get("reinigungsart"), f.get("haeufigkeit_genel"), leistungen_json, angebot_id))
    else:
        # YENİ KAYIT EKLE
        conn.execute('''INSERT INTO angebote (firma, ansprechpartner, strasse, plz, ort, m2, reinigungsart, haeufigkeit, leistungen_json) 
                        VALUES (?,?,?,?,?,?,?,?,?)''', 
                     (f.get("firma"), f.get("ansprechpartner"), f.get("strasse"), f.get("plz"), f.get("ort"), 
                      f.get("m2"), f.get("reinigungsart"), f.get("haeufigkeit_genel"), leistungen_json))
    
    conn.commit()
    conn.close()
    return redirect(url_for('angebot_index'))

@app.route("/angebot/get/<int:id>")
def get_angebot(id):
    # JavaScript'in formu doldurması için veriyi gönderir
    conn = get_db_connection()
    a = conn.execute("SELECT * FROM angebote WHERE id = ?", (id,)).fetchone()
    conn.close()
    return jsonify(dict(a))

@app.route("/angebot/update_status/<int:id>/<string:status>")
def update_angebot_status(id, status):
    conn = get_db_connection()
    conn.execute("UPDATE angebote SET status = ? WHERE id = ?", (status, id))
    conn.commit()
    conn.close()
    return redirect(url_for('angebot_index'))

# =====================================================
# Bölüm 14- VERTRAG (SÖZLEŞME) SÜRECİ
# =====================================================

@app.route("/vertrag/create/<int:id>")
def vertrag_create_form(id):
    # Tekliften sözleşme formuna geçiş aşaması
    conn = get_db_connection()
    angebot = conn.execute("SELECT * FROM angebote WHERE id = ?", (id,)).fetchone()
    conn.close()
    return render_template("vertrag_form.html", a=angebot)

@app.route("/vertrag/submit", methods=["POST"])
def vertrag_submit():
    f = request.form
    conn = get_db_connection()
    
    # 1. Firmayı kunden tablosuna kalıcı olarak ekle
    conn.execute('''INSERT INTO kunden (firma, ort, strasse, plz, ansprechpartner_name, vertrag_beginn, kundennummer, monat) 
                    VALUES (?,?,?,?,?,?,?,?)''', 
                 (f.get("firma"), f.get("ort"), f.get("strasse"), f.get("plz"), 
                  f.get("ansprechpartner"), f.get("v_beginn"), f.get("k_nummer"), f.get("preis")))
    
    # 2. Teklifi 'Bestätigt' durumuna çek
    conn.execute("UPDATE angebote SET status = 'Bestätigt' WHERE id = ?", (f.get("angebot_id"),))
    
    conn.commit()
    conn.close()
    return redirect(url_for('kunden_list'))

# =====================================================
# Bölüm 15- BESICHTIGUNGSTERMINE
# =====================================================

@app.route("/besichtigung", methods=["GET", "POST"])
def besichtigung_index():
    conn = get_db_connection()
    
    if request.method == "POST":
        f = request.form
        besichtigung_id = f.get("besichtigung_id")
        
        if besichtigung_id:
            # GÜNCELLEME (Bearbeiten)
            conn.execute('''UPDATE besichtigungen SET 
                            firma=?, ansprechpartner=?, telefon=?, email=?, 
                            strasse=?, plz=?, ort=?, termin_datum=?, 
                            termin_uhrzeit=?, notizen=? WHERE id=?''',
                         (f.get("firma"), f.get("ansprechpartner"), f.get("telefon"), f.get("email"),
                          f.get("strasse"), f.get("plz"), f.get("ort"), f.get("datum"),
                          f.get("uhrzeit"), f.get("notizen"), besichtigung_id))
        else:
            # YENİ KAYIT
            conn.execute('''INSERT INTO besichtigungen 
                            (firma, ansprechpartner, telefon, email, strasse, plz, ort, termin_datum, termin_uhrzeit, notizen) 
                            VALUES (?,?,?,?,?,?,?,?,?,?)''', 
                         (f.get("firma"), f.get("ansprechpartner"), f.get("telefon"), f.get("email"),
                          f.get("strasse"), f.get("plz"), f.get("ort"), f.get("datum"),
                          f.get("uhrzeit"), f.get("notizen")))
        
        conn.commit()
        conn.close()
        return redirect(url_for('besichtigung_index'))

    # Listeleme
    termine = conn.execute("SELECT * FROM besichtigungen ORDER BY termin_datum ASC, termin_uhrzeit ASC").fetchall()
    conn.close()

    # Sadece burayı değiştiriyoruz:
    import json
    return render_template("besichtigung.html", 
                           termine=termine, 
                           sabit_hizmetler=json.dumps(LEISTUNGEN))

@app.route("/besichtigung/delete/<int:id>")
def delete_besichtigung(id):
    conn = get_db_connection()
    conn.execute("DELETE FROM besichtigungen WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('besichtigung_index'))

@app.route("/besichtigung/status/<int:id>/<string:status>")
def update_besichtigung_status(id, status):
    conn = get_db_connection()
    conn.execute("UPDATE besichtigungen SET status = ? WHERE id = ?", (status, id))
    conn.commit()
    conn.close()
    return redirect(url_for('besichtigung_index'))

# =====================================================
# Bölüm 16- STUNDENZETTEL (Dosya Sistemi ve Listeleme)
# =====================================================
@app.route("/stundenzettel")
def stundenzettel():
    import os
    conn = get_db_connection()
    # Sadece aktif işçileri çekiyoruz
    mitarbeiter_liste = conn.execute("SELECT id, vorname, nachname FROM mitarbeiter WHERE status = 'aktiv'").fetchall()
    conn.close()

    # 1. Ana klasörü kontrol et (data/stundenzettel)
    base_path = os.path.join('data', 'stundenzettel')
    if not os.path.exists(base_path):
        os.makedirs(base_path)

    # 2. Her işçi için klasör var mı bak, yoksa aç
    for m in mitarbeiter_liste:
        folder_name = f"{m['vorname']}_{m['nachname']}"
        worker_path = os.path.join(base_path, folder_name)
        if not os.path.exists(worker_path):
            os.makedirs(worker_path)

    return render_template("stundenzettel.html", mitarbeiter_liste=mitarbeiter_liste)

# =====================================================
# Bölüm 17- STUNDENZETTEL DETAY (İşçiye Özel Sayfa)
# =====================================================

# BU SENİN ESKİ KODUN - DOKUNMA! (Admin için)
@app.route("/stundenzettel/<int:id>")
def edit_stundenzettel(id):
    conn = get_db_connection()
    worker = conn.execute("SELECT * FROM mitarbeiter WHERE id = ?", (id,)).fetchone()
    conn.close()
    if not worker:
        return "Mitarbeiter nicht gefunden", 404
    return render_template("stundenzettel_detail.html", worker=worker)

# BU DA İŞÇİ İÇİN OLAN KOD - GÜVENLİK VE İSİM EKLENMİŞ HALİ
@app.route("/stundenzettel/worker/<int:id>/<string:name>/<string:code>")
def worker_stundenzettel(id, name, code):
    conn = get_db_connection()
    # Veritabanından ID, İsim ve 4 haneli Gizli Kodun hepsini aynı anda kontrol ediyoruz!
    # (vorname || '_' || nachname) kısmı isimleri "Murat_Kicci" formatında birleştirir.
    worker = conn.execute(
        "SELECT * FROM mitarbeiter WHERE id = ? AND (vorname || '_' || nachname) = ? AND access_code = ?", 
        (id, name, code)
    ).fetchone()
    conn.close()
    
    if not worker:
        # Eğer linkteki isim veya 4 haneli kod yanlışsa erişim yok!
        return "<h1>⚠️ Zugriff verweigert / Geçersiz Link</h1><p>Bilgiler uyuşmuyor.</p>", 403
    
    # Her şey doğruysa menüsüz işçi sayfasını açar
    return render_template("stundenzettel_worker.html", mitarbeiter_liste=[worker])
# =====================================================
# Bölüm 18- BUCHHALTUNG (MUHASEBE)
# =====================================================

@app.route("/buchhaltung")
@login_required
def buchhaltung():
    # 1. Sevdesk ile veritabanını eşitle
    sync_sevdesk_to_db()
    
    # 2. Dinamik tarih al
    import datetime
    now = datetime.datetime.now()
    
    # 3. Verileri çek
    veriler = get_cached_rechnungen(now.month, now.year)
    
    # 4. ÜST KARTLAR İÇİN TOPLAMLARI HESAPLA
    monatsumsatz = sum(r['brutto'] for r in veriler)
    offene_forderungen = sum(r['offen'] for r in veriler)
    bezahlt_monat = monatsumsatz - offene_forderungen
    mwst_zahllast = sum(r['mwst'] for r in veriler)
    
    # --- 🏦 BANKA VERİSİ ÇEKME ---
    selected_bank = request.args.get('bank_account') 
    bank_data = []
    if selected_bank:
        from sevdesk import get_bank_transactions
        bank_data = get_bank_transactions(selected_bank)
    
    # 5. Tüm verileri HTML'e gönder
    return render_template(
        "buchhaltung.html", 
        rechnungen=veriler,
        monatsumsatz=monatsumsatz,
        bezahlt_monat=bezahlt_monat,
        offene_forderungen=offene_forderungen,
        mwst_zahllast=mwst_zahllast,
        bank_data=bank_data,
        active_bank=selected_bank
    )


# =====================================================
# Bölüm 19- SEVDESK BAĞLANTISI
# =====================================================

import sevdesk

def init_services():
    pass   # SADECE import yeterli, test çağrılmaz


# =====================================================
# Bölüm 20- UYGULAMA BAŞLAT
# =====================================================
if __name__ == "__main__":
    init_services()
    app.run(host="0.0.0.0", port=5000, debug=True)
