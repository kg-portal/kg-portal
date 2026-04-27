
# =====================================================
# KG-PORTAL V2
# Bölüm 1- ANA UYGULAMA DOSYASI (Flask) 
# =====================================================

import io
from playwright.sync_api import sync_playwright

from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from functools import wraps
import sqlite3
import json
import os
import secrets

from docx import Document
from docx.shared import RGBColor
from docx.enum.text import WD_COLOR_INDEX
import tempfile
from datetime import datetime

from lexware import sync_lexware_to_db, get_cached_rechnungen
from fints_import import (
    sync_fints_to_db,
    get_fints_transactions,
    get_fints_all_balances,
    get_fints_balance
)
from app2 import register_app2_routes
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
            if request.path.startswith('/stundenzettel/worker/') or request.path.startswith('/api/stundenzettel/'):
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
    if request.path.startswith('/stundenzettel/worker/') or request.path.startswith('/api/stundenzettel/'):
        return

    # 3. Diğer her yer için şifre ekranına yolla
    return redirect(url_for('login'))
register_app2_routes(app, login_required)

# ... (Buradan aşağısı get_db_connection() diye devam ediyor, aynen kalsın)

# =====================================================
# Bölüm 2- VERİTABANI BAĞLANTISI
# =====================================================
def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

VERTRAG_HTML_PATH = os.path.join("templates", "Mitarbeiter Vertrag Vorlage.html")
BEFREIUNG_HTML_PATH = os.path.join("templates", "Befreiungsantrag-fuer-Arbeitnehmer-im-Gewerbe.html")

def temiz(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() == "none" else s

def tarih(v):
    v = temiz(v)
    if not v:
        return ""
    try:
        return datetime.strptime(v, "%Y-%m-%d").strftime("%d.%m.%Y")
    except:
        return v

def para(v):
    try:
        return f"{float(str(v).replace(',', '.')):.2f}".replace(".", ",")
    except:
        return temiz(v)

def urlaub_calc(woche):
    return {
        "1": "4",
        "2": "8",
        "3": "12",
        "4": "16",
        "5": "20",
        "6": "24"
    }.get(str(temiz(woche)), "0")

def art_fix(art):
    art_text = temiz(art).lower()
    if "minijob" in art_text:
        return "603,00 €"
    if "teilzeit" in art_text:
        return "Teilzeit"
    if "vollzeit" in art_text:
        return "Vollzeit"
    return ""

def mitarbeiter_vertrag_daten_holen(mitarbeiter_id, datum_text):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM mitarbeiter WHERE id = ?", (mitarbeiter_id,)).fetchone()
    conn.close()

    if not row:
        raise Exception("Mitarbeiter bulunamadı")

    d = dict(row)

    iban_raw = temiz(d.get("iban"))

    if "/" in iban_raw:
        parts = iban_raw.split("/", 1)
        iban = parts[0].strip()
        bank = parts[1].strip()
    else:
        iban = iban_raw
        bank = ""

    weitere = temiz(d.get("weitere_beschaeftigung"))
    weitere_firma = temiz(d.get("weitere_firma"))

    if weitere.lower() == "ja" and weitere_firma:
        weitere_text = weitere_firma
    elif weitere:
        weitere_text = weitere
    else:
        weitere_text = "Keine"

    return {
        "{{Anrede}}": temiz(d.get("anrede")),
        "{{ANREDE}}": temiz(d.get("anrede")),
        "{{Vorname}}": temiz(d.get("vorname")),
        "{{VORNAME}}": temiz(d.get("vorname")), 
        "{{Nachname}}": temiz(d.get("nachname")),
        "{{NACHNAME}}": temiz(d.get("nachname")),
        "{{Eintrittsdatum}}": tarih(d.get("eintrittsdatum")),
        "{{Position}}": temiz(d.get("position")),
        "{{Stadt}}": temiz(d.get("ort")),
        "{{Probezeit}}": temiz(d.get("probezeit")),
        "{{Stundenlohn (€)}}": para(d.get("stundenlohn")),
        "{{Art}}": art_fix(d.get("art")),
        "{{Bankname}}": bank,
        "{{IBAN}}": iban,
        "{{WEITERE_BESCHAEFTIGUNG}}": weitere_text,
        "{{ARBEITSTAGE_WOCHE}}": temiz(d.get("arbeitstage_woche")),
        "{{DATUM}}": temiz(datum_text),
        "{{ORT}}": temiz(d.get("ort")),
        "{{Ort}}": temiz(d.get("ort")),
        "{{SV_NUMMER}}": temiz(d.get("sv_nummer")),
        "{{URLAUBSTAGE}}": urlaub_calc(d.get("arbeitstage_woche")),
    }

def inject_print_css(html_text):
    extra_css = """
<style>
@page {
    size: A4 portrait;
    margin: 0;
}

html, body {
    margin: 0 !important;
    padding: 0 !important;
    background: white !important;
}

@media print {
    html, body {
        margin: 0 !important;
        padding: 0 !important;
        width: 210mm !important;
        background: white !important;
        -webkit-print-color-adjust: exact !important;
        print-color-adjust: exact !important;
    }

    .pdf24_view {
        font-size: 1em !important;
        transform: none !important;
        -webkit-transform: none !important;
        -moz-transform: none !important;
        transform-origin: top left !important;
        -webkit-transform-origin: top left !important;
        -moz-transform-origin: top left !important;
    }

    .pdf24_03,
    .pdf24_04,
    .pdf24_05,
    .pdf24_06 {
        break-inside: avoid !important;
        page-break-inside: avoid !important;
    }

    .pdf24_05 {
        width: 210mm !important;
        min-height: 297mm !important;
        max-height: 297mm !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
        break-after: page !important;
        page-break-after: always !important;
    }

    .pdf24_05:last-of-type {
        break-after: auto !important;
        page-break-after: auto !important;
    }

    .pdf24_02 {
        width: 210mm !important;
        height: 297mm !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: hidden !important;
        display: block !important;
    }
}
</style>
"""
    if "</head>" in html_text:
        return html_text.replace("</head>", extra_css + "\n</head>")
    return extra_css + "\n" + html_text


def html_vertrag_olustur(template_path, data):
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    for key, val in data.items():
        html = html.replace(key, str(val))

    svnr = data.get("{{SV_NUMMER}}", "")
    i = 0
    result = ""
    for ch in html:
        if ch == "¤":
            if i < len(svnr):
                result += svnr[i]
                i += 1
            else:
                result += ""
        else:
            result += ch

    html = result
    return html


def html_to_pdf_download_response(html_text, download_name):
    return html_text, 200, {
        "Content-Type": "text/html; charset=utf-8"
    }


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
            data_json TEXT,
            sort_order INTEGER DEFAULT 0
        )
    ''')

    try:
        conn.execute("ALTER TABLE kunden ADD COLUMN sort_order INTEGER DEFAULT 0")
    except Exception:
        pass

    # ===============================
    # Aynı günün üstüne kayıt yapabilmek için olan kaydi tekrarlamaz
    # ===============================
    conn.execute('''
        CREATE TABLE IF NOT EXISTS work_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            worker_id INTEGER,
            datum TEXT,
            start_time TEXT,
            end_time TEXT,
            place TEXT,
            signed INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(worker_id) REFERENCES mitarbeiter(id)
        )
    ''')

    # 🚀 Bu satır olmazsa "Speichern" dediğinde aynı günün üstüne yazamaz, hata verir:
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_worker_date ON work_logs(worker_id, datum)")

    try:
        conn.execute("ALTER TABLE mitarbeiter ADD COLUMN position TEXT")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE mitarbeiter ADD COLUMN arbeitstage_woche TEXT")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE mitarbeiter ADD COLUMN probezeit TEXT")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE mitarbeiter ADD COLUMN weitere_beschaeftigung TEXT")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE mitarbeiter ADD COLUMN weitere_firma TEXT")
    except Exception:
        pass

    # ===============================
    # LEXWARE ID ZIRHI
    # ===============================
    try:
        conn.execute("ALTER TABLE kunden ADD COLUMN lexware_id TEXT")
    except Exception:
        pass

    try:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_kunden_lexware_id ON kunden(lexware_id)"
        )
    except Exception:
        pass

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
            access_code TEXT,
            position TEXT,
            arbeitstage_woche TEXT,
            probezeit TEXT,
            weitere_beschaeftigung TEXT,
            weitere_firma TEXT,
            stundenlohn REAL,
            urlaub INTEGER,
            resturlaub INTEGER,
            art TEXT,
            data_json TEXT,
            sort_order INTEGER DEFAULT 0,
            status TEXT DEFAULT 'aktiv',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    try:
        conn.execute("ALTER TABLE mitarbeiter ADD COLUMN anrede TEXT")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE mitarbeiter ADD COLUMN lohn TEXT")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE mitarbeiter ADD COLUMN sort_order INTEGER DEFAULT 0")
    except Exception:
        pass

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
    except Exception:
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

    # LEXWARE VERİ DEPOSU (IŞIK HIZI İÇİN)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS lexware_cache (
            invoice_id TEXT PRIMARY KEY,
            nr TEXT,
            datum TEXT,
            kunde TEXT,
            brutto REAL,
            netto REAL,
            mwst REAL,
            offen REAL,
            status_code TEXT,
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

    # RATENZAHLUNGEN TABLOSU (KREDİLER) - YENİ
    conn.execute('''
        CREATE TABLE IF NOT EXISTS ratenzahlungen (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kreditname TEXT,
            gesamtbetrag REAL,
            monatliche_rate REAL,
            laufzeit INTEGER,
            beginn TEXT,
            ende TEXT,
            einzahl_raten INTEGER,
            rest_raten INTEGER,
            restbetrag REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    try:
        conn.execute("ALTER TABLE ratenzahlungen ADD COLUMN sort_order INTEGER DEFAULT 0")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE ratenzahlungen ADD COLUMN renk_kodu TEXT DEFAULT '#007bff'")
    except Exception:
        pass

    # 🔥 GEWERBLICHE AUSGABEN (İŞLETME GİDERLERİ)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS gewerbliche_ausgaben (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            datum TEXT,
            skr03_kod TEXT,
            kategorie TEXT,
            empfaenger TEXT,
            zweck TEXT,
            brutto REAL,
            mwst_betrag REAL,
            netto REAL,
            konto TEXT,
            monat TEXT
        )
    ''')

    # 🔥 PRIVATE AUSGABEN (ŞAHSİ GİDERLER)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS private_ausgaben (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            datum TEXT,
            skr03_kod TEXT,
            kategorie TEXT,
            empfaenger TEXT,
            zweck TEXT,
            betrag REAL,
            konto TEXT,
            notiz TEXT,
            monat TEXT
        )
    ''')

    conn.commit()
    conn.close()

init_db()

def run_db_migration():
    conn = get_db_connection()
    try:
        conn.execute("ALTER TABLE gewerbliche_ausgaben ADD COLUMN kategori TEXT")
        conn.execute("ALTER TABLE private_ausgaben ADD COLUMN kategori TEXT")
        conn.commit()
        print("✅ Veritabanı yapısı güncellendi.")
    except Exception:
        pass
    finally:
        conn.close()

run_db_migration()


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
@login_required
def index():
    # 1. API Senkronizasyonu (En güncel veriyi çekmek için)
    try:
        sync_lexware_to_db() 
    except Exception as e:
        print(f"⚠️ API baglantisi yok: {e}")

    conn = get_db_connection()
    import datetime
    now = datetime.datetime.now()

    # 2. CİRO HESAPLAMA (Mart başındaki faturaları da yakalayan zırhlı sorgu)
    target_month = f"{now.month:02d}"
    target_year = str(now.year)

    monat_row = conn.execute("""
        SELECT SUM(brutto) FROM lexware_cache 
        WHERE (datum LIKE ? OR datum LIKE ?)
    """, (f"{target_year}-{target_month}-%", f"%.{target_month}.{target_year}")).fetchone()

    monatlicher_umsatz = monat_row[0] if monat_row and monat_row[0] else 0.0

    # 3. YILLIK GRAFİK VERİSİ (Boşluk hatası giderilmiş temiz versiyon)
    jahres_grafik_verisi = []
    jahres_umsatz = 0
    for m in range(1, 13):
        p_iso = f"{now.year}-{m:02d}-%"
        p_dot = f"%.{m:02d}.{now.year}"
        
        r = conn.execute("""
            SELECT SUM(brutto) FROM lexware_cache 
            WHERE (datum LIKE ? OR datum LIKE ?)
        """, (p_iso, p_dot)).fetchone()
        
        val = r[0] if r and r[0] else 0.0
        jahres_grafik_verisi.append(val)
        jahres_umsatz += val

    # 4. MÜŞTERİ VE PERSONEL SAYILARI
    customer_count = conn.execute("SELECT COUNT(*) FROM kunden WHERE vertragsstatus != 'gekuendigt' OR vertragsstatus IS NULL").fetchone()[0]
    
    kunden_stats = conn.execute("SELECT strftime('%m', created_at) as ay, COUNT(id) FROM kunden GROUP BY ay").fetchall()
    kunden_grafik_verisi = [0] * 12
    for row in kunden_stats:
        if row['ay']:
            kunden_grafik_verisi[int(row['ay']) - 1] = row[1]

    employee_count = conn.execute("SELECT COUNT(*) FROM mitarbeiter WHERE status = 'aktiv'").fetchone()[0]
    
    personel_stats = conn.execute("SELECT strftime('%m', created_at) as ay, COUNT(id) FROM mitarbeiter WHERE status = 'aktiv' GROUP BY ay").fetchall()
    personel_grafik_verisi = [0] * 12
    for row in personel_stats:
        if row['ay']:
            personel_grafik_verisi[int(row['ay']) - 1] = row[1]

    # 5. TO-DO VE PİL (BATTERY) HESAPLARI
    todo_kw_labels = []
    todo_erledigt_verisi = []
    todo_offen_verisi = []
    start_of_year = datetime.datetime(now.year, 1, 1)
    start_date = start_of_year - datetime.timedelta(days=start_of_year.weekday())

    for i in range(52):
        week_start = start_date + datetime.timedelta(weeks=i)
        week_end = week_start + datetime.timedelta(days=6)
        todo_kw_labels.append(f"KW {week_start.isocalendar()[1]}")
        
        er = conn.execute("SELECT COUNT(*) FROM todos WHERE done = 1 AND deadline BETWEEN ? AND ?", (week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d'))).fetchone()[0]
        of = conn.execute("SELECT COUNT(*) FROM todos WHERE done = 0 AND deadline BETWEEN ? AND ?", (week_start.strftime('%Y-%m-%d'), week_end.strftime('%Y-%m-%d'))).fetchone()[0]
        todo_erledigt_verisi.append(er)
        todo_offen_verisi.append(of)

    initial_todo_index = max(0, min(now.isocalendar()[1] - 3, 47))
    t_er = conn.execute("SELECT COUNT(*) FROM todos WHERE done = 1").fetchone()[0]
    t_of = conn.execute("SELECT COUNT(*) FROM todos WHERE done = 0").fetchone()[0]
    todo_percent = int((t_er / (t_er + t_of)) * 100) if (t_er + t_of) > 0 else 0

    monatstrend_grafik_verisi = [0, 2500, 5000, 7500, monatlicher_umsatz]
    conn.close()

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
# Bölüm 6- KUNDEN (ZIRHLI VE HATASIZ VERSİYON)
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
            # --- 1. PORTAL VERİTABANINI GÜNCELLE (Mevcut yapı korundu) ---
            conn.execute("""
                UPDATE kunden SET
                    firma=?, ort=?, monat=?, strasse=?, plz=?,
                    ansprechpartner_name=?, telefon=?, email=?,
                    kundennummer=?, vertrag_beginn=?, vertrag_ende=?,
                    haeufigkeit=?, vertragsstatus=?, vertragslaufzeit=?, data_json=?
                WHERE id=?
            """, (
                form_data.get("firma"), form_data.get("stadt"), form_data.get("betrag"),
                form_data.get("strasse"), form_data.get("plz"), name,
                form_data.get("telefon"), form_data.get("email"),
                form_data.get("kundennummer"), form_data.get("beginn"),
                form_data.get("ende"), form_data.get("haeufigkeit"),
                form_data.get("status"), form_data.get("laufzeit"),
                data_json, kunde_id
            ))

            # --- 🔥 AYNA SENKRON: BEARBEITEN YAPILINCA LEXWARE'I GÜNCELLE ---
            try:
                from lexware import update_lexware_contact
                # Önce bu müşterinin lexware_id'sini bulalım
                row = conn.execute("SELECT lexware_id FROM kunden WHERE id=?", (kunde_id,)).fetchone()
                if row and row["lexware_id"]:
                    update_lexware_contact(
                        lexware_id=row["lexware_id"],
                        firma_adi=form_data.get("firma"),
                        sehir=form_data.get("stadt"),
                        sokak=form_data.get("strasse"),
                        plz=form_data.get("plz"),
                        email=form_data.get("email"),
                        telefon=form_data.get("telefon")
                    )
            except Exception as e:
                print(f"⚠️ Lexware güncelleme hatası: {e}")

        else:
            # --- 2. YENİ KAYIT EKLE (Mevcut yapı korundu) ---
            cursor = conn.execute("""
                INSERT INTO kunden (
                    firma, ort, monat, strasse, plz,
                    ansprechpartner_name, telefon, email,
                    kundennummer, vertrag_beginn, vertrag_ende,
                    haeufigkeit, vertragsstatus, vertragslaufzeit, data_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                form_data.get("firma"), form_data.get("stadt"), form_data.get("betrag"),
                form_data.get("strasse"), form_data.get("plz"), name,
                form_data.get("telefon"), form_data.get("email"),
                form_data.get("kundennummer"), form_data.get("beginn"),
                form_data.get("ende"), form_data.get("haeufigkeit"),
                form_data.get("status"), form_data.get("laufzeit"),
                data_json
            ))
            new_kunde_id = cursor.lastrowid # Yeni eklenen portal ID'si

            # --- 🔥 AYNA SENKRON: YENİ KAYITTA LEXWARE ID'SİNİ AL VE KAYDET ---
            try:
                from lexware import create_lexware_contact
                res = create_lexware_contact(
                    firma_adi=form_data.get("firma"), 
                    sehir=form_data.get("stadt"), 
                    sokak=form_data.get("strasse"),
                    plz=form_data.get("plz"),
                    email=form_data.get("email"),
                    telefon=form_data.get("telefon")
                )
                
                # Eğer kayıt başarılıysa dönen ID'yi portal veritabanına geri yazalım
                if res and (res.status_code == 201 or res.status_code == 200):
                    lex_id = res.json().get("id")
                    if lex_id:
                        conn.execute("UPDATE kunden SET lexware_id=? WHERE id=?", (lex_id, new_kunde_id))
            except Exception as e:
                print(f"⚠️ Lexware kayıt hatası: {e}")

        conn.commit()
        conn.close()
        return redirect(url_for("kunden"))

    # 🔢 OTOMATİK NUMARA HESAPLAMA (YENİ)
    # Veritabanındaki sadece rakamlardan oluşan en büyük numarayı bulur
    last_nr_row = conn.execute("SELECT kundennummer FROM kunden WHERE kundennummer GLOB '[0-9]*' ORDER BY CAST(kundennummer AS INTEGER) DESC LIMIT 1").fetchone()
    
    if last_nr_row and last_nr_row['kundennummer']:
        next_nr = int(last_nr_row['kundennummer']) + 1
    else:
        next_nr = 10002 # Veritabanı boşsa 10002'den başlar

    # Müşteri listesini çek ve sayfayı yükle (Aşağıya doğru 1-2-3 sıralaması)
    kunden_liste = conn.execute("SELECT * FROM kunden ORDER BY sort_order ASC, id ASC").fetchall()
    conn.close()
    
    # next_nr değişkenini kunden.html'e gönderiyoruz
    return render_template("kunden.html", kunden=kunden_liste, next_nr=next_nr)

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
                    anrede=?, vorname=?, nachname=?, ort=?, strasse=?, plz=?,
                    geburtsdatum=?, eintrittsdatum=?, telefon=?, email=?,
                    steuer_id=?, sv_nummer=?, krankenkasse=?, iban=?,
                    position=?, arbeitstage_woche=?, probezeit=?, weitere_beschaeftigung=?, weitere_firma=?,
                    stundenlohn=?, urlaub=?, resturlaub=?, art=?, lohn=?, data_json=?
                WHERE id=?
            """, (
                form_data.get("anrede"),
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
                form_data.get("position"),
                form_data.get("arbeitstage_woche"),
                form_data.get("probezeit"),
                form_data.get("weitere_beschaeftigung"),
                form_data.get("weitere_firma"),
                form_data.get("stundenlohn"),
                form_data.get("urlaub"),
                form_data.get("resturlaub"),
                form_data.get("art"),
                form_data.get("lohn"),
                data_json,
                mitarbeiter_id
            ))
        else:
            import secrets
            access_code = secrets.token_urlsafe(24)

            conn.execute("""
                INSERT INTO mitarbeiter (
                    anrede, vorname, nachname, ort, strasse, plz,
                    geburtsdatum, eintrittsdatum, telefon, email,
                    steuer_id, sv_nummer, krankenkasse, iban,
                    position, arbeitstage_woche, probezeit, weitere_beschaeftigung, weitere_firma,
                    stundenlohn, urlaub, resturlaub, art, lohn, data_json, access_code
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)

            """, (
                form_data.get("anrede"),
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
                form_data.get("position"),
                form_data.get("arbeitstage_woche"),
                form_data.get("probezeit"),
                form_data.get("weitere_beschaeftigung"),
                form_data.get("weitere_firma"),
                form_data.get("stundenlohn"),
                form_data.get("urlaub"),
                form_data.get("resturlaub"),
                form_data.get("art"),
                form_data.get("lohn"),
                data_json,
                access_code
            ))

        conn.commit()
        conn.close()
        return redirect(url_for("mitarbeiter"))

    mitarbeiter_liste = conn.execute(
    "SELECT * FROM mitarbeiter ORDER BY sort_order ASC, id ASC"
).fetchall()
    conn.close()
    return render_template("Mitarbeiter.html", mitarbeiter_liste=mitarbeiter_liste)
@app.route("/mitarbeiter/vertrag-erstellen", methods=["POST"])
@login_required
def mitarbeiter_vertrag_erstellen():
    mitarbeiter_id = request.form.get("mitarbeiter_id", "").strip()
    datum_text = request.form.get("datum", "").strip()
    lohn = request.form.get("lohn", "").strip()

    if not mitarbeiter_id or not datum_text:
        return jsonify({"success": False, "message": "Daten fehlen"}), 400

    try:
        data = mitarbeiter_vertrag_daten_holen(int(mitarbeiter_id), datum_text)
        data["{{lohn}}"] = temiz(lohn)

        vertrag_html = html_vertrag_olustur("templates/Mitarbeiter Vertrag Vorlage.html", data)
        vertrag_html = inject_print_css(vertrag_html)

        befreiung_html = html_vertrag_olustur("templates/Befreiungsantrag-fuer-Arbeitnehmer-im-Gewerbe.html", data)

        final_html = vertrag_html + '\n<div style="page-break-before: always !important;"></div>\n' + befreiung_html

        return final_html, 200, {'Content-Type': 'text/html; charset=utf-8'}

    except Exception as e:
        return jsonify({"success": False, "message": f"Vertrag Fehler: {str(e)}"}), 500


# 🔽🔽🔽 BURADAN SONRA EKLENDİ 🔽🔽🔽

@app.route("/mitarbeiter/vertrag-pdf", methods=["GET"])
@login_required
def mitarbeiter_vertrag_pdf():
    mitarbeiter_id = request.args.get("mitarbeiter_id", "").strip()
    datum_text = request.args.get("datum", "").strip()
    lohn = request.args.get("lohn", "").strip()

    if not mitarbeiter_id or not datum_text:
        return jsonify({"success": False, "message": "Daten fehlen"}), 400

    try:
        data = mitarbeiter_vertrag_daten_holen(int(mitarbeiter_id), datum_text)
        data["{{lohn}}"] = temiz(lohn)

        html = html_vertrag_olustur("templates/Mitarbeiter Vertrag Vorlage.html", data)
        html = inject_print_css(html)

        return html_to_pdf_download_response(html, "Vertrag.pdf")

    except Exception as e:
        return jsonify({"success": False, "message": f"Vertrag Fehler: {str(e)}"}), 500


@app.route("/mitarbeiter/befreiung-pdf", methods=["GET"])
@login_required
def mitarbeiter_befreiung_pdf():
    mitarbeiter_id = request.args.get("mitarbeiter_id", "").strip()
    datum_text = request.args.get("datum", "").strip()
    lohn = request.args.get("lohn", "").strip()

    if not mitarbeiter_id or not datum_text:
        return jsonify({"success": False, "message": "Daten fehlen"}), 400

    try:
        data = mitarbeiter_vertrag_daten_holen(int(mitarbeiter_id), datum_text)
        data["{{lohn}}"] = temiz(lohn)

        html = html_vertrag_olustur("templates/Befreiungsantrag-fuer-Arbeitnehmer-im-Gewerbe.html", data)
        html = inject_print_css(html)

        return html_to_pdf_download_response(html, "Befreiung.pdf")

    except Exception as e:
        return jsonify({"success": False, "message": f"Befreiung Fehler: {str(e)}"}), 500

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
# Bölüm 12- KALENDER
# =====================================================
@app.route("/kalender")
def kalender():
    return render_template("kalender.html")


# =====================================================
# Bölüm 16- STUNDENZETTEL (Dosya Sistemi ve Listeleme)
# =====================================================
@app.route("/stundenzettel")
def stundenzettel():
    import os
    conn = get_db_connection()
    # Sadece aktif işçileri çekiyoruz
    mitarbeiter_liste = conn.execute("SELECT id, vorname, nachname, access_code FROM mitarbeiter WHERE status = 'aktiv'").fetchall()
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
@app.route("/stundenzettel/worker/<string:code>")
def worker_stundenzettel(code):
    conn = get_db_connection()
    worker = conn.execute(
        "SELECT * FROM mitarbeiter WHERE access_code = ?",
        (code,)
    ).fetchone()

    if not worker:
        conn.close()
        return "<h1>⚠️ Zugriff verweigert / Geçersiz Link</h1><p>Bilgiler uyuşmuyor.</p>", 403

    logs = conn.execute("""
        SELECT datum, start_time, end_time, place, signed
        FROM work_logs
        WHERE worker_id = ?
    """, (worker["id"],)).fetchall()
    conn.close()

    saved_logs = {}
    for log in logs:
        saved_logs[log["datum"]] = {
            "start": log["start_time"],
            "end": log["end_time"],
            "place": log["place"],
            "signed": log["signed"]
        }

    # Her şey doğruysa menüsüz işçi sayfasını açar
    return render_template(
        "stundenzettel_worker.html",
        mitarbeiter_liste=[worker],
        saved_logs=json.dumps(saved_logs)
    )
# =====================================================
# Bölüm 18- BUCHHALTUNG (MUHASEBE) - TEK PARÇA & HIZLI
# =====================================================

@app.route("/buchhaltung")
@login_required
def buchhaltung():


    # --- 🔥 ADIM 2: DİNAMİK VERİ TETİKLEYİCİSİ (GÜNCELLENDİ) ---
    import datetime
    # import sevdesk silindi!
    now = datetime.datetime.now()
    
    selected_month = request.args.get('month', default=now.month, type=int)
    selected_year = request.args.get('year', default=now.year, type=int)

    # NOT: Lexware gider senkronizasyonu hazır olana kadar dinamik çekim pasif.
    # ----------------------------------------------------

    # 1. Lexware ile veritabanını eşitle
    sync_lexware_to_db() 
    
    # 2. Seçilen ay ve yıl bilgilerini al (Aşağıdaki değişkenleri kullandığın için burası kalmalı)
    # now ve selected_month/year yukarıda tanımlandığı için çakışmaz.
    
    # 3. ÖNEMLİ: get_cached_rechnungen artık sadece veritabanından (cache) okur
    veriler = get_cached_rechnungen(selected_month, selected_year)
    
    # 4. ÜST KARTLARI SEÇİLEN AYA GÖRE HESAPLA (YENİ ZIRHLI SİSTEM)
    p_iso = f"{selected_year}-{selected_month:02d}-%"
    p_dot = f"%.{selected_month:02d}.{selected_year}"
    
    conn = get_db_connection()
    row = conn.execute("""
        SELECT SUM(brutto), SUM(offen), SUM(mwst) 
        FROM lexware_cache 
        WHERE (datum LIKE ? OR datum LIKE ?)
    """, (p_iso, p_dot)).fetchone()
    
    monatsumsatz = row[0] if row[0] else 0.0
    offene_forderungen = row[1] if row[1] else 0.0
    bezahlt_monat = monatsumsatz - offene_forderungen
    mwst_zahllast = row[2] if row[2] else 0.0
    conn.close()

    # --- 🏦 VERİTABANINDAN GİDERLERİ ÇEK ---
    conn = get_db_connection()
    gewerbliche_ausgaben = conn.execute(
        'SELECT * FROM gewerbliche_ausgaben WHERE monat = ? ORDER BY datum DESC', 
        (f"{selected_month:02d}",)
    ).fetchall()
    
    private_ausgaben = conn.execute(
        'SELECT * FROM private_ausgaben WHERE monat = ? ORDER BY datum DESC', 
        (f"{selected_month:02d}",)
    ).fetchall()
    conn.close()
    
    monat_isimleri = {
        1: "Januar", 2: "Februar", 3: "März", 4: "April", 5: "Mai", 6: "Juni",
        7: "Juli", 8: "August", 9: "September", 10: "Oktober", 11: "November", 12: "Dezember"
    }
    selected_month_name = monat_isimleri.get(selected_month)
    
    selected_bank = request.args.get('bank_account')
    bank_data = []
    total_bank_balance = 0.0
    all_balances = get_fints_all_balances()
    
    if selected_bank:
        bank_data = get_fints_transactions(selected_bank, selected_month, selected_year)
        total_bank_balance = get_fints_balance(selected_bank)
    
    # 🔥 BURASI DIŞARIDA OLACAK
    conn = get_db_connection()
    ratenzahlungen = conn.execute("SELECT * FROM ratenzahlungen ORDER BY sort_order ASC, id ASC").fetchall()
    conn.close()
    
    # --- KARTLAR İÇİN HESAPLAMA ---
    total_g = sum(r['gesamtbetrag'] for r in ratenzahlungen) if ratenzahlungen else 0.0
    total_r = sum(r['restbetrag'] for r in ratenzahlungen) if ratenzahlungen else 0.0
    
    # 5. Tüm verileri HTML'e gönder
    return render_template(
        "buchhaltung.html",
        rechnungen=veriler,
        ratenzahlungen=ratenzahlungen,
        monatsumsatz=monatsumsatz,
        bezahlt_monat=bezahlt_monat,
        offene_forderungen=offene_forderungen,
        mwst_zahllast=mwst_zahllast,
        bank_data=bank_data,
        active_bank=selected_bank,
        total_bank_balance=total_bank_balance,
        all_balances=all_balances,
        selected_month=f"{selected_month:02d}",
        selected_month_name=selected_month_name,
        total_g=total_g,
        total_r=total_r,
        gewerbliche_ausgaben=gewerbliche_ausgaben,
        private_ausgaben=private_ausgaben
    )

@app.route("/delete_ratenzahlung/<int:id>")
@login_required
def delete_ratenzahlung(id):
    conn = get_db_connection()
    conn.execute("DELETE FROM ratenzahlungen WHERE id = ?", (id,))
    conn.commit()
    conn.close()
    return redirect(url_for('buchhaltung') + "#rates")

# BU YENİ: DÜZENLEME İÇİN VERİ GETİRME (KALEM İÇİN)
@app.route("/get_ratenzahlung/<int:id>")
@login_required
def get_ratenzahlung(id):
    conn = get_db_connection()
    rate = conn.execute("SELECT * FROM ratenzahlungen WHERE id = ?", (id,)).fetchone()
    conn.close()
    if rate:
        return jsonify(dict(rate))
    return jsonify({"error": "Not found"}), 404

# BU GÜNCELLEDİĞİMİZ EKLEME/GÜNCELLEME FONKSİYONU (RENK DESTEKLİ)
@app.route("/add_ratenzahlung", methods=["POST"])
@login_required
def add_ratenzahlung():
    f = request.form
    rate_id = f.get("rate_id") 
    conn = get_db_connection()
    
    try:
        # SENİN HESAPLAMA MANTIĞIN - HİÇ DOKUNULMADI
        laufzeit = int(f.get("laufzeit", 0))
        einzahl = int(f.get("einzahl_raten", 0))
        rate_val = float(f.get("monatliche_rate", 0))
        rest_raten = max(0, laufzeit - einzahl)
        restbetrag = rest_raten * rate_val
    except ValueError:
        return "Geçersiz sayı formatı", 400

    if rate_id: # ID varsa UPDATE yap (Renk dahil)
        conn.execute('''
            UPDATE ratenzahlungen SET 
                kreditname=?, gesamtbetrag=?, monatliche_rate=?, laufzeit=?, 
                beginn=?, ende=?, einzahl_raten=?, rest_raten=?, restbetrag=?,
                renk_kodu=?
            WHERE id=?
        ''', (f.get("kreditname"), f.get("gesamtbetrag"), rate_val, laufzeit,
              f.get("beginn"), f.get("ende"), einzahl, rest_raten, restbetrag, 
              f.get("renk_kodu"), rate_id))
    else: # ID yoksa INSERT yap (Renk dahil)
        conn.execute('''
            INSERT INTO ratenzahlungen (
                kreditname, gesamtbetrag, monatliche_rate, laufzeit, 
                beginn, ende, einzahl_raten, rest_raten, restbetrag, renk_kodu
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (f.get("kreditname"), f.get("gesamtbetrag"), rate_val, laufzeit,
              f.get("beginn"), f.get("ende"), einzahl, rest_raten, restbetrag, 
              f.get("renk_kodu", "#007bff")))
    
    conn.commit()
    conn.close()
    return redirect(url_for('buchhaltung') + "#rates")

# =====================================================
# 🔥 BÖLÜM 18.5: STUNDENZETTEL KAYIT MOTORU (NİHAİ ZIRH)
# =====================================================

@app.route("/api/stundenzettel/<int:worker_id>")
def get_stundenzettel(worker_id):
    conn = get_db_connection()
    logs = conn.execute("""
        SELECT datum, start_time, end_time, place, signed
        FROM work_logs
        WHERE worker_id = ?
        ORDER BY datum ASC
    """, (worker_id,)).fetchall()
    conn.close()

    entries = []
    for log in logs:
        entries.append({
            "date": log["datum"],
            "start": log["start_time"] or "",
            "end": log["end_time"] or "",
            "place": log["place"] or "",
            "signed": bool(log["signed"])
        })

    return jsonify({
        "success": True,
        "entries": entries
    })



@app.route("/api/stundenzettel/save", methods=["POST"])
def save_stundenzettel():
    data = request.json
    worker_id = data.get("worker_id")
    entries = data.get("entries") # JS'den gelen liste
    
    if not worker_id or not entries:
        return jsonify({"success": False, "error": "Daten fehlen"}), 400

    conn = get_db_connection()
    try:
        for e in entries:
            # 🚀 Bölüm 18.5: UPSERT MANTIĞI 
            # (Aynı işçi ve tarih varsa güncelle, yoksa yeni satır aç)
            conn.execute('''
                INSERT INTO work_logs (worker_id, datum, start_time, end_time, place, signed)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_id, datum) DO UPDATE SET
                    start_time=excluded.start_time,
                    end_time=excluded.end_time,
                    place=excluded.place,
                    signed=excluded.signed
            ''', (worker_id, e['date'], e['start'], e['end'], e['place'], 1 if e['signed'] else 0))
            
            # 🔥 Bölüm 18.5: URALUB DÜŞME
            if e['place'] == "Urlaub":
                conn.execute("UPDATE mitarbeiter SET resturlaub = MAX(0, resturlaub - 1) WHERE id = ?", (worker_id,))

        conn.commit()
        return jsonify({"success": True})
    except Exception as ex:
        print(f"❌ DB Kayıt Hatası: {ex}")
        return jsonify({"success": False, "error": str(ex)}), 500
    finally:
        conn.close()


@app.route('/api/stundenzettel/delete', methods=['POST'])
def delete_stundenzettel():
    data = request.json

    worker_id = data.get('worker_id')
    date = data.get('date')

    if not worker_id or not date:
        return jsonify({"success": False, "error": "Daten fehlen"}), 400

    conn = get_db_connection()
    try:
        conn.execute("""
            DELETE FROM work_logs
            WHERE worker_id = ? AND datum = ?
        """, (worker_id, date))

        conn.commit()
        return jsonify({"success": True})

    except Exception as e:
        print(f"❌ DB Silme Hatası: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        conn.close()

# =====================================================
# Bölüm 19- LEXWARE BAĞLANTISI VE BAŞLATMA
# =====================================================

def init_services():
    """Uygulama başlarken Lexware verilerini günceller."""
    try:
        # 1️⃣ Lexware fatura senkronizasyonu
        sync_lexware_to_db()

        # 2️⃣ 🔥 Lexware → Portal MÜŞTERİ SENKRONU (ELLE EKLENENLER)
        from lexware import sync_lexware_customers_to_db
        sync_lexware_customers_to_db()

        print("🚀 Lexware Fatura + Müşteri Verileri Başarıyla Güncellendi!")
    except Exception as e:
        print(f"⚠️ Init Services Hatası: {e}")

# =====================================================
# 🌍 BÖLÜM 20: PLZ API (NRW ÖZEL VE YEREL VERİ SİSTEMİ)
# =====================================================

# NRW'deki tüm önemli noktaları buraya gömüyoruz. 
# Buraya eklediğin her kod internete sormadan anında çalışır.
NRW_STADT_LISTE = {
    # --- DUISBURG (Senin patladığın yer, tüm mahalleler eklendi) ---
    "47051": "Duisburg", "47053": "Duisburg", "47055": "Duisburg", "47057": "Duisburg", "47058": "Duisburg",
    "47059": "Duisburg", "47119": "Duisburg", "47137": "Duisburg", "47138": "Duisburg", "47139": "Duisburg",
    "47166": "Duisburg", "47167": "Duisburg", "47169": "Duisburg", "47178": "Duisburg", "47179": "Duisburg",
    "47198": "Duisburg", "47199": "Duisburg", "47226": "Duisburg", "47228": "Duisburg", "47229": "Duisburg",
    "47239": "Duisburg", "47249": "Duisburg", "47259": "Duisburg", "47269": "Duisburg", "47279": "Duisburg",

    # --- KÖLN ---
    "50667": "Köln", "50668": "Köln", "50670": "Köln", "50672": "Köln", "50674": "Köln", "50676": "Köln",
    "50677": "Köln", "50678": "Köln", "50679": "Köln", "50733": "Köln", "50735": "Köln", "50737": "Köln",
    "50739": "Köln", "50765": "Köln", "50767": "Köln", "50769": "Köln", "50823": "Köln", "50825": "Köln",
    "50827": "Köln", "50829": "Köln", "50858": "Köln", "50859": "Köln", "50931": "Köln", "50933": "Köln",
    "50935": "Köln", "50937": "Köln", "50939": "Köln", "50968": "Köln", "50969": "Köln", "50996": "Köln",
    "50997": "Köln", "50999": "Köln", "51061": "Köln", "51063": "Köln", "51065": "Köln", "51067": "Köln",
    "51069": "Köln", "51103": "Köln", "51105": "Köln", "51107": "Köln", "51109": "Köln", "51143": "Köln",
    "51145": "Köln", "51147": "Köln", "51149": "Köln",

    # --- DÜSSELDORF ---
    "40210": "Düsseldorf", "40211": "Düsseldorf", "40212": "Düsseldorf", "40213": "Düsseldorf", "40215": "Düsseldorf",
    "40217": "Düsseldorf", "40219": "Düsseldorf", "40221": "Düsseldorf", "40223": "Düsseldorf", "40225": "Düsseldorf",
    "40227": "Düsseldorf", "40229": "Düsseldorf", "40231": "Düsseldorf", "40233": "Düsseldorf", "40235": "Düsseldorf",
    "40237": "Düsseldorf", "40239": "Düsseldorf", "40468": "Düsseldorf", "40470": "Düsseldorf", "40472": "Düsseldorf",
    "40474": "Düsseldorf", "40476": "Düsseldorf", "40477": "Düsseldorf", "40479": "Düsseldorf", "40489": "Düsseldorf",
    "40545": "Düsseldorf", "40547": "Düsseldorf", "40549": "Düsseldorf", "40589": "Düsseldorf", "40591": "Düsseldorf",
    "40593": "Düsseldorf", "40595": "Düsseldorf", "40597": "Düsseldorf", "40599": "Düsseldorf", "40625": "Düsseldorf",
    "40627": "Düsseldorf", "40629": "Düsseldorf",

    # --- DORTMUND ---
    "44135": "Dortmund", "44137": "Dortmund", "44139": "Dortmund", "44141": "Dortmund", "44143": "Dortmund",
    "44145": "Dortmund", "44147": "Dortmund", "44149": "Dortmund", "44225": "Dortmund", "44227": "Dortmund",
    "44229": "Dortmund", "44263": "Dortmund", "44265": "Dortmund", "44267": "Dortmund", "44269": "Dortmund",
    "44287": "Dortmund", "44289": "Dortmund", "44309": "Dortmund", "44319": "Dortmund", "44328": "Dortmund",
    "44329": "Dortmund", "44339": "Dortmund", "44357": "Dortmund", "44359": "Dortmund", "44369": "Dortmund",
    "44379": "Dortmund", "44388": "Dortmund",

    # --- ESSEN ---
    "45127": "Essen", "45128": "Essen", "45130": "Essen", "45131": "Essen", "45133": "Essen", "45134": "Essen",
    "45136": "Essen", "45138": "Essen", "45139": "Essen", "45141": "Essen", "45143": "Essen", "45144": "Essen",
    "45145": "Essen", "45147": "Essen", "45149": "Essen", "45219": "Essen", "45239": "Essen", "45257": "Essen",
    "45259": "Essen", "45276": "Essen", "45277": "Essen", "45279": "Essen", "45289": "Essen", "45307": "Essen",
    "45309": "Essen", "45326": "Essen", "45327": "Essen", "45329": "Essen", "45355": "Essen", "45356": "Essen",
    "45357": "Essen", "45359": "Essen",

    # --- MÜLHEIM AN DER RUHR ---
    "45468": "Mülheim an der Ruhr", "45470": "Mülheim an der Ruhr", "45472": "Mülheim an der Ruhr",
    "45473": "Mülheim an der Ruhr", "45475": "Mülheim an der Ruhr", "45476": "Mülheim an der Ruhr",
    "45478": "Mülheim an der Ruhr", "45481": "Mülheim an der Ruhr",

    # --- BOCHUM ---
    "44787": "Bochum", "44789": "Bochum", "44791": "Bochum", "44793": "Bochum", "44795": "Bochum",
    "44797": "Bochum", "44799": "Bochum", "44801": "Bochum", "44803": "Bochum", "44805": "Bochum",
    "44807": "Bochum", "44809": "Bochum", "44866": "Bochum", "44867": "Bochum", "44869": "Bochum",
    "44879": "Bochum", "44892": "Bochum", "44894": "Bochum",

    # --- OBERHAUSEN ---
    "46045": "Oberhausen", "46047": "Oberhausen", "46049": "Oberhausen", "46117": "Oberhausen",
    "46119": "Oberhausen", "46145": "Oberhausen", "46147": "Oberhausen", "46149": "Oberhausen",

    # --- WUPPERTAL ---
    "42103": "Wuppertal", "42105": "Wuppertal", "42107": "Wuppertal", "42109": "Wuppertal", "42111": "Wuppertal",
    "42113": "Wuppertal", "42115": "Wuppertal", "42117": "Wuppertal", "42119": "Wuppertal", "42275": "Wuppertal",
    "42277": "Wuppertal", "42279": "Wuppertal", "42281": "Wuppertal", "42283": "Wuppertal", "42285": "Wuppertal",
    "42287": "Wuppertal", "42289": "Wuppertal", "42327": "Wuppertal", "42329": "Wuppertal", "42349": "Wuppertal",
    "42369": "Wuppertal", "42389": "Wuppertal", "42399": "Wuppertal",

    # --- DİĞER ÖNEMLİ NRW ŞEHİRLERİ VE KASABALARI (Eksiksiz 396 Belediye Temeli) ---
    "52062": "Aachen", "52064": "Aachen", "52066": "Aachen", "52068": "Aachen", "52070": "Aachen",
    "52072": "Aachen", "52074": "Aachen", "52076": "Aachen", "52078": "Aachen", "52080": "Aachen",
    "48683": "Ahaus", "59227": "Ahlen", "52457": "Aldenhoven", "53347": "Alfter", "58762": "Altena",
    "48341": "Altenberge", "59609": "Anröchte", "59821": "Arnsberg", "59387": "Ascheberg", "57439": "Attendorn",
    "32832": "Augustdorf", "57319": "Bad Berleburg", "33014": "Bad Driburg", "53604": "Bad Honnef",
    "57334": "Bad Laasphe", "33175": "Bad Lippspringe", "53902": "Bad Münstereifel", "32545": "Bad Oeynhausen",
    "32105": "Bad Salzuflen", "59505": "Bad Sassendorf", "33181": "Bad Wünnenberg", "52499": "Baesweiler",
    "58802": "Balve", "32683": "Barntrup", "59269": "Beckum", "50181": "Bedburg", "47551": "Bedburg-Hau",
    "48361": "Beelen", "50126": "Bergheim", "51427": "Bergisch Gladbach", "59192": "Bergkamen", "51702": "Bergneustadt",
    "59909": "Bestwig", "37688": "Beverungen", "48727": "Billerbeck", "33602": "Bielefeld", "53945": "Blankenheim",
    "32825": "Blomberg", "46325": "Borken", "53332": "Bornheim", "46236": "Bottrop", "33034": "Brakel",
    "58339": "Breckerfeld", "59929": "Brilon", "41379": "Brüggen", "50321": "Brühl", "32257": "Bünde",
    "57299": "Burbach", "33142": "Büren", "51399": "Burscheid", "44575": "Castrop-Rauxel", "48653": "Coesfeld",
    "53949": "Dahlem", "45711": "Datteln", "33129": "Delbrück", "32756": "Detmold", "46535": "Dinslaken",
    "32694": "Dörentrup", "41539": "Dormagen", "46282": "Dorsten", "48317": "Drensteinfurt", "57489": "Drolshagen",
    "48249": "Dülmen", "52349": "Düren", "53783": "Eitorf", "50189": "Elsdorf", "46446": "Emmerich am Rhein",
    "48282": "Emsdetten", "32130": "Enger", "51766": "Engelskirchen", "58256": "Ennepetal", "59320": "Ennigerloh",
    "59469": "Ense", "50374": "Erftstadt", "41812": "Erkelenz", "57339": "Erndtebrück", "59597": "Erwitte",
    "52249": "Eschweiler", "59889": "Eslohe", "32339": "Espelkamp", "53879": "Euskirchen", "48351": "Everswinkel",
    "32699": "Extertal", "57413": "Finnentrop", "50226": "Frechen", "57258": "Freudenberg", "58730": "Fröndenberg/Ruhr",
    "52538": "Gangelt", "52511": "Geilenkirchen", "47608": "Geldern", "45879": "Gelsenkirchen", "48712": "Gescher",
    "59590": "Geseke", "58285": "Gevelsberg", "45964": "Gladbeck", "47574": "Goch", "47929": "Grefrath",
    "48268": "Greven", "41515": "Grevenbroich", "48599": "Gronau (Westf.)", "51643": "Gummersbach", "33330": "Gütersloh",
    "42781": "Haan", "58089": "Hagen", "33790": "Halle (Westf.)", "59969": "Hallenberg", "45721": "Haltern am See",
    "58553": "Halver", "59063": "Hamm", "46499": "Hamminkeln", "33428": "Harsewinkel", "45525": "Hattingen",
    "48329": "Havixbeck", "48619": "Heek", "48734": "Heiden", "42579": "Heiligenhaus", "52396": "Heimbach",
    "52525": "Heinsberg", "53940": "Hellenthal", "58675": "Hemer", "53773": "Hennef (Sieg)", "58313": "Herdecke",
    "32049": "Herford", "44623": "Herne", "58849": "Herscheid", "45699": "Herten", "52134": "Herzogenrath",
    "32120": "Hiddenhausen", "57271": "Hilchenbach", "40721": "Hilden", "32479": "Hille", "48477": "Hörstel",
    "32805": "Horn-Bad Meinberg", "48612": "Horstmar", "33161": "Hövelhof", "37671": "Höxter", "41836": "Hückelhoven",
    "42499": "Hückeswagen", "32609": "Hüllhorst", "46569": "Hünxe", "52393": "Hürtgenwald", "50354": "Hürth",
    "49477": "Ibbenbüren", "52459": "Inden", "58636": "Iserlohn", "46419": "Isselburg", "47661": "Issum",
    "41363": "Jüchen", "52428": "Jülich", "41564": "Kaarst", "47546": "Kalkar", "53925": "Kall",
    "32689": "Kalletal", "47475": "Kamp-Lintfort", "47906": "Kempen", "47647": "Kerken", "50169": "Kerpen",
    "47623": "Kevelaer", "58566": "Kierspe", "57399": "Kirchhundem", "32278": "Kirchlengern", "47533": "Kleve",
    "47798": "Krefeld", "53639": "Königswinter", "41352": "Korschenbroich", "47559": "Kranenburg", "52372": "Kreuzau",
    "57223": "Kreuztal", "51515": "Kürten", "49549": "Ladbergen", "48366": "Laer", "32791": "Lage",
    "40764": "Langenfeld (Rheinland)", "52379": "Langerwehe", "48739": "Legden", "42799": "Leichlingen (Rheinland)",
    "32657": "Lemgo", "49525": "Lengerich", "57368": "Lennestadt", "33818": "Leopoldshöhe", "51371": "Leverkusen",
    "33165": "Lichtenau", "49536": "Lienen", "51789": "Lindlar", "52441": "Linnich", "59510": "Lippetal",
    "59555": "Lippstadt", "32584": "Löhne", "53797": "Lohmar", "49504": "Lotte", "32312": "Lübbecke",
    "58507": "Lüdenscheid", "59348": "Lüdinghausen", "32676": "Lügde", "44532": "Lünen", "51709": "Marienheide",
    "37696": "Marienmünster", "45768": "Marl", "34431": "Marsberg", "53894": "Mechernich", "53340": "Meckenheim",
    "59964": "Medebach", "40667": "Meerbusch", "58540": "Meinerzhagen", "58706": "Menden (Sauerland)", "52399": "Merzenich",
    "59872": "Meschede", "48629": "Metelen", "49497": "Mettingen", "40822": "Mettmann", "32423": "Minden",
    "47441": "Moers", "53804": "Much", "48143": "Münster", "57250": "Netphen", "41334": "Nettetal",
    "58809": "Neuenrade", "48485": "Neuenkirchen", "57290": "Neunkirchen", "53819": "Neunkirchen-Seelscheid", "41460": "Neuss",
    "52385": "Nideggen", "53859": "Niederkassel", "41372": "Niederkrüchten", "52382": "Niederzier", "33039": "Nieheim",
    "59394": "Nordkirchen", "48356": "Nordwalde", "52388": "Nörvenich", "48301": "Nottuln", "51588": "Nümbrecht",
    "48607": "Ochtrup", "51519": "Odenthal", "59302": "Oelde", "45739": "Oer-Erkenschwick", "33813": "Oerlinghausen",
    "59399": "Olfen", "57462": "Olpe", "59939": "Olsberg", "51491": "Overath", "33098": "Paderborn",
    "32469": "Petershagen", "58840": "Plettenberg", "32457": "Porta Westfalica", "32361": "Preußisch Oldendorf",
    "50259": "Pulheim", "42477": "Radevormwald", "46348": "Raesfeld", "32369": "Rahden", "40878": "Ratingen",
    "49509": "Recke", "45657": "Recklinghausen", "46459": "Rees", "51580": "Reichshof", "42853": "Remscheid",
    "33378": "Rheda-Wiedenbrück", "46414": "Rhede", "53359": "Rheinbach", "47495": "Rheinberg", "48430": "Rheine",
    "47509": "Rheurdt", "33397": "Rietberg", "32289": "Rödinghausen", "52159": "Roetgen", "41569": "Rommerskirchen",
    "48720": "Rosendahl", "51503": "Rösrath", "53809": "Ruppichteroth", "59602": "Rüthen", "48369": "Saerbeck",
    "33154": "Salzkotten", "53757": "Sankt Augustin", "48336": "Sassenberg", "58579": "Schalksmühle",
    "46514": "Schermbeck", "32816": "Schieder-Schwalenberg", "33189": "Schlangen", "53937": "Schleiden",
    "33758": "Schloß Holte-Stukenbrock", "57392": "Schmallenberg", "41366": "Schwalmtal", "58239": "Schwerte",
    "52538": "Selfkant", "59379": "Selm", "48308": "Senden", "48324": "Sendenhorst", "53721": "Siegburg",
    "57072": "Siegen", "52152": "Simmerath", "59494": "Soest", "42651": "Solingen", "47665": "Sonsbeck",
    "32139": "Spenge", "45549": "Sprockhövel", "48708": "Stadtlohn", "48565": "Steinfurt", "33803": "Steinhagen",
    "32839": "Steinheim", "32351": "Stemwede", "52222": "Stolberg (Rhld.)", "47638": "Straelen",
    "59846": "Sundern (Sauerland)", "53913": "Swisttal", "48291": "Telgte", "52445": "Titz", "47918": "Tönisvorst",
    "53840": "Troisdorf", "52531": "Übach-Palenberg", "47589": "Uedem", "59423": "Unna", "42549": "Velbert",
    "46342": "Velen", "33415": "Verl", "33775": "Versmold", "52391": "Vettweiß", "41747": "Viersen",
    "32602": "Vlotho", "48691": "Vreden", "53343": "Wachtberg", "47669": "Wachtendonk", "59329": "Wadersloh",
    "51545": "Waldbröl", "52525": "Waldfeucht", "45731": "Waltrop", "34414": "Warburg", "48231": "Warendorf",
    "59581": "Warstein", "41849": "Wassenberg", "47652": "Weeze", "41844": "Wegberg", "53919": "Weilerswist",
    "59514": "Welver", "57482": "Wenden", "58791": "Werdohl", "59457": "Werl", "42929": "Wermelskirchen",
    "59368": "Werne", "33824": "Werther (Westf.)", "46483": "Wesel", "50389": "Wesseling", "49492": "Westerkappeln",
    "58300": "Wetter (Ruhr)", "48493": "Wettringen", "58739": "Wickede (Ruhr)", "51674": "Wiehl",
    "34439": "Willebadessen", "47877": "Willich", "57234": "Wilnsdorf", "51570": "Windeck", "59955": "Winterberg",
    "51688": "Wipperfürth", "44892": "Witten", "42489": "Wülfrath", "52146": "Würselen", "46509": "Xanten",
    "53909": "Zülpich"
}
@app.route("/api/plz/<plz>")
def get_plz(plz):
    if len(plz) != 5 or not plz.isdigit():
        return jsonify({"city": None})

    # 1. ADIM: Önce kendi yerel listemize bak (Hata payı sıfır)
    if plz in NRW_STADT_LISTE:
        return jsonify({"city": NRW_STADT_LISTE[plz]})

    # 2. ADIM: Listede yoksa Zippopotam'a git (Nominatim gibi [] döndürmez)
    try:
        # Bu servis tarayıcı engelini takmaz, daha sağlamdır
        r = requests.get(f"https://api.zippopotam.us/de/{plz}", timeout=3)
        if r.status_code == 200:
            data = r.json()
            if "places" in data:
                city = data["places"][0]["place name"]
                return jsonify({"city": city})
    except Exception as e:
        print(f"Yedek API Hatası: {e}")

    return jsonify({"city": None})

# =====================================================
# Bölüm 21 - SORT ORDER Kaydirma Sistemi Kaydetme
# =====================================================

@app.route("/mitarbeiter/save-order", methods=["POST"])
@login_required
def save_mitarbeiter_order():
    try:
        data = request.get_json(silent=True) or {}
        order = data.get("order", [])

        if not isinstance(order, list) or not order:
            return jsonify({"success": False}), 400

        conn = get_db_connection()

        for index, mitarbeiter_id in enumerate(order, start=1):
            conn.execute(
                "UPDATE mitarbeiter SET sort_order = ? WHERE id = ?",
                (index, mitarbeiter_id)
            )

        conn.commit()
        conn.close()

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/kunden/save-order", methods=["POST"])
@login_required
def save_kunden_order():
    try:
        data = request.get_json(silent=True) or {}
        order = data.get("order", [])

        if not isinstance(order, list) or not order:
            return jsonify({"success": False}), 400

        conn = get_db_connection()

        for index, kunden_id in enumerate(order, start=1):
            conn.execute(
                "UPDATE kunden SET sort_order = ? WHERE id = ?",
                (index, kunden_id)
            )

        conn.commit()
        conn.close()

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/raten/save-order", methods=["POST"])
@login_required
def save_raten_order():
    try:
        data = request.get_json(silent=True) or {}
        order = data.get("order", [])

        if not isinstance(order, list) or not order:
            return jsonify({"success": False}), 400

        conn = get_db_connection()

        for index, rate_id in enumerate(order, start=1):
            conn.execute(
                "UPDATE ratenzahlungen SET sort_order = ? WHERE id = ?",
                (index, rate_id)
            )

        conn.commit()
        conn.close()

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# =====================================================
# Bölüm 23- SPARKASSE BAĞLANTISI
# =====================================================

@app.route("/api/sparkasse-sync")
@login_required
def sparkasse_sync():
    try:
        result = sync_fints_to_db()
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": "0", "message": str(e)})

# =====================================================
# Bölüm 24 - REINIGUNG HESAPLAMA API
# =====================================================

@app.route("/api/calc", methods=["POST"])
def calc():
    data = request.json

    branche = data.get("branche")
    m2 = float(data.get("m2", 0))
    schmutz = int(data.get("schmutz", 3))

    # 🔥 BASE WERTE
    REINIGUNGSWERTE = {
        "buero": 170,
        "arztpraxis": 140,
        "fitnessstudio": 150,
        "restaurant": 120,
        "industriehalle": 100,
        "grundreinigung": 80
    }

    # 🔥 SENİN MODEL (0-3 BASE)
    def schmutz_faktor(level):
        if level <= 3:
            return 1.0
        elif level == 4:
            return 0.95
        elif level == 5:
            return 0.90
        elif level == 6:
            return 0.85
        elif level == 7:
            return 0.80
        elif level == 8:
            return 0.70
        elif level == 9:
            return 0.60
        else:
            return 0.50

    basis = REINIGUNGSWERTE.get(branche, 170)

    faktor = schmutz_faktor(schmutz)
    leistung_effektiv = basis * faktor

    stunden = m2 / leistung_effektiv

    return jsonify({
        "leistung": round(leistung_effektiv, 2),
        "stunden": round(stunden, 2)
    })

# =====================================================
# BÖLÜM 24: UYGULAMA BAŞLATICI (NİHAİ ZIRHLI SÜRÜM)
# =====================================================

def run_db_migration():
    """Eksik sütun hatasını (kategori) kökten çözer."""
    conn = get_db_connection()
    try:
        # Tabloya 'kategori' sütununu zorla ekler
        conn.execute("ALTER TABLE gewerbliche_ausgaben ADD COLUMN kategori TEXT")
        conn.execute("ALTER TABLE private_ausgaben ADD COLUMN kategori TEXT")
        conn.commit()
        print("✅ Veritabanı yapısı güncellendi.")
    except Exception:
        # Sütun zaten varsa hata vermez
        pass
    finally:
        conn.close()


def sevdesk_gecici_devir_kaydi_ekle():
    """
    GEÇİCİ SEVDESK DEVİR KAYDI
    Ocak ve Şubat 2026 verilerini 1 kere lexware_cache içine ekler.
    İLERİDE RECHNUNGLAR DİREKT SİSTEME YÜKLENİNCE BU FONKSİYON SİLİNECEK.
    """
    conn = get_db_connection()
    try:
        conn.executemany('''
            INSERT OR IGNORE INTO lexware_cache
            (invoice_id, nr, datum, kunde, brutto, netto, mwst, offen, status_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', [
            (
                'SEVDESK-2026-01',
                'SV-2026-01',
                '2026-01-31',
                'SevDesk Übertrag Januar 2026 - später löschen',
                20253.04,
                17019.36,
                3233.68,
                0.00,
                'paid'
            ),
            (
                'SEVDESK-2026-02',
                'SV-2026-02',
                '2026-02-28',
                'SevDesk Übertrag Februar 2026 - später löschen',
                25790.37,
                21672.57,
                4117.80,
                0.00,
                'paid'
            )
        ])
        conn.commit()
        print("✅ Geçici SevDesk Ocak/Şubat devir kayıtları eklendi.")
    except Exception as e:
        print(f"❌ Geçici SevDesk devir kaydı eklenemedi: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    # 1. Veritabanı yapısını kontrol et ve eksik sütunları tamamla
    init_db()          
    run_db_migration()

    # 2. GEÇİCİ SEVDESK DEVİR KAYDI
    # İLERİDE RECHNUNGLAR DİREKT YÜKLENECEĞİ İÇİN BU SATIR VE FONKSİYON SİLİNECEK
    sevdesk_gecici_devir_kaydi_ekle()
    
    # 3. 🛡️ TEMİZLİK BİTTİ - BU SATIRLAR ARTIK PASİF (YORUMDA)
    # Eğer her şeyi tekrar sıfırlamak istersen başlarındaki '#' işaretlerini kaldırabilirsin.
    # conn = get_db_connection()
    # conn.execute("DELETE FROM gewerbliche_ausgaben")
    # conn.execute("DELETE FROM private_ausgaben")
    # conn.commit(); conn.close()
    # print("🧹 Çöpler temizlendi, veritabanı pırıl pırıl!")

    # 4. Lexware verilerini çek (Zırhlı sistem sayesinde sadece yenileri ekler)
    init_services()  

    # 5. Uygulamayı başlat
    app.run(host="0.0.0.0", port=5000, debug=True)
