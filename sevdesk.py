import requests
import sqlite3
import os
from datetime import datetime

SEVDESK_TOKEN = "5fa864d3f0ae1981ed458bf74aa945d7"
BASE_URL = "https://my.sevdesk.de/api/v1"

# ADIM 1: DOSYA YOLUNU ZIRHLI HALE GETİRDİM
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'kg_portal.db')

def sync_sevdesk_to_db():
    """Sevdesk'ten sadece yeni veya durumu degisen faturalari cekip veritabanina yazar."""
    headers = {"Authorization": SEVDESK_TOKEN, "Accept": "application/json"}
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # --- 🔥 ÖDENMİŞLERİ ATLAMAK İÇİN BURAYI GÜNCELLEDİM ---
    # Veritabanında zaten ödenmiş (220 veya 1000) olanları çekiyoruz
    cursor.execute("SELECT invoice_id FROM sevdesk_cache WHERE status_code IN (220, 1000)")
    paid_invoice_ids = {row[0] for row in cursor.fetchall()}

    # Sadece son 50 faturayi kontrol et (Hizli tarama)
    params = {"limit": 50, "embed": "contact"}
    
    try:
        r = requests.get(f"{BASE_URL}/Invoice", headers=headers, params=params, timeout=10)
        invoices = r.json().get("objects", [])
    except Exception as e:
        print(f"Hata: Sevdesk baglantisi kurulamadi: {e}")
        return

    for inv in invoices:
        inv_id = inv.get("id")
        
        # EĞER FATURA ZATEN ÖDENMİŞSE SEVDESK'E TEKRAR SORMUYORUZ, ATLIYORUZ
        if inv_id in paid_invoice_ids:
            continue

        status = int(inv.get("status", 0))
        # 220 veya 1000 ise Bezahlt, degilse acik miktar Brüt tutardir
        offen = 0.0 if status in [220, 1000] else float(inv.get("sumGross") or 0)
        
        raw_date = inv.get("invoiceDate") or inv.get("create")
        formatted_date = raw_date[:10] # YYYY-MM-DD

        # UPSERT: Kayit varsa güncelle, yoksa ekle
        cursor.execute('''
            INSERT INTO sevdesk_cache (invoice_id, nr, datum, kunde, brutto, netto, mwst, offen, status_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(invoice_id) DO UPDATE SET
                offen = excluded.offen,
                status_code = excluded.status_code,
                last_updated = CURRENT_TIMESTAMP
        ''', (
            inv_id, inv.get("invoiceNumber"), formatted_date, 
            inv.get("addressName"), float(inv.get("sumGross") or 0),
            float(inv.get("sumNet") or 0), 
            float(inv.get("sumGross") or 0) - float(inv.get("sumNet") or 0),
            offen, status
        ))

    conn.commit()
    conn.close()

def get_cached_rechnungen(month, year):
    """Veritabanindan (depodan) verileri milisaniyeler icinde getirir."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    
    # Secilen aya göre filtreleme (YYYY-MM-DD formatina göre)
    search_pattern = f"{year}-{month:02d}-%"
    
    rows = conn.execute("""
        SELECT * FROM sevdesk_cache 
        WHERE datum LIKE ? 
        ORDER BY datum DESC
    """, (search_pattern,)).fetchall()
    
    conn.close()
    
    result = []
    for r in rows:
        d_obj = datetime.strptime(r['datum'], '%Y-%m-%d')
        result.append({
            "nr": r['nr'], 
            "datum": d_obj.strftime("%d.%m.%Y"),
            "kunde": r['kunde'], 
            "brutto": r['brutto'],
            "netto": r['netto'], 
            "mwst": r['mwst'], 
            "offen": r['offen']
        })
    return result

# ...........................................................................
# ...........................................................................
# .......... 🏦 BÖLÜM 2: BANKA İŞLEMLERİ (YENİ) .............................
# ...........................................................................
# ...........................................................................

def get_bank_transactions(account_slug):
    """Banka hareketlerini sadece eksikleri tamamlayacak şekilde (Incremental) çeker."""
    headers = {"Authorization": SEVDESK_TOKEN, "Accept": "application/json"}
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    bank_ids = {
        "geschäftskonto-kg-gebäudereinigung": "5736092", 
        "geschäftskonto-amazon-energie": "5736093",       
        "damla-privat": "5965693",                        
        "murat-privat": "6143807"
    }
    sev_id = bank_ids.get(account_slug)

    # Önce cache'deki mevcut ID'leri alıyoruz
    cursor.execute("SELECT transaction_id FROM bank_cache WHERE account_slug = ?", (account_slug,))
    existing_ids = {row[0] for row in cursor.fetchall()}

    # LİMİT 50: Hız için son işlemlere bakmak yeterli
    params = {
        "checkAccount[id]": sev_id, 
        "checkAccount[objectName]": "CheckAccount", 
        "limit": 50
    }

    try:
        r = requests.get(f"{BASE_URL}/CheckAccountTransaction", headers=headers, params=params, timeout=10)
        transactions = r.json().get("objects", [])
        
        for t in transactions:
            t_id = t.get("id")
            if t_id in existing_ids:
                continue # Zaten varsa Sevdesk'e bir daha sorma, geç!

            raw_date = t.get("valueDate")[:10] 
            payee = t.get("payeePayerName") or t.get("payeeName") or t.get("entryText") or "Unbekannt"
            description = t.get("paymtPurpose") or t.get("entryText") or ""

            cursor.execute('''
                INSERT INTO bank_cache (transaction_id, account_slug, payee, datum, description, amount)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(transaction_id) DO NOTHING
            ''', (t_id, account_slug, payee, raw_date, description, float(t.get("amount") or 0)))
        conn.commit()
    except Exception as e:
        print(f"Banka Hatası: {e}")

    # Veritabanından (Cache) çek
    rows = cursor.execute("""
        SELECT * FROM bank_cache 
        WHERE account_slug = ? 
        ORDER BY datum DESC
    """, (account_slug,)).fetchall()
    
    conn.close()

    result = []
    for r in rows:
        formatted_date = datetime.strptime(r[3], "%Y-%m-%d").strftime("%d.%m.%Y")
        result.append({
            "payee": r[2],          
            "date": formatted_date, 
            "description": r[4],    
            "amount": r[5]          
        })
    return result

def get_all_bank_balances():
    """Tüm banka bakiyelerini TEK BİR API isteğiyle topluca çeker (Hızı 4-8 kat artırır)."""
    headers = {"Authorization": SEVDESK_TOKEN}
    bank_mapping = {
        "5736092": "geschäftskonto-kg-gebäudereinigung",
        "5736093": "geschäftskonto-amazon-energie",
        "5965693": "damla-privat",
        "6143807": "murat-privat"
    }
    balances = {slug: 0.0 for slug in bank_mapping.values()}
    try:
        # Tek seferde tüm hesapları getirir
        r = requests.get(f"{BASE_URL}/CheckAccount", headers=headers, timeout=10)
        accounts = r.json().get("objects", [])
        for acc in accounts:
            acc_id = str(acc.get("id"))
            if acc_id in bank_mapping:
                balances[bank_mapping[acc_id]] = float(acc.get("balance", 0))
    except:
        pass
    return balances

def get_bank_balance(account_slug):
    """SevDesk API'den doğrudan 'balance' değerini çeker (Toplama yapmaz)."""
    bank_ids = {
        "geschäftskonto-kg-gebäudereinigung": "5736092", 
        "geschäftskonto-amazon-energie": "5736093",       
        "damla-privat": "5965693",                        
        "murat-privat": "6143807"
    }
    
    acc_id = bank_ids.get(account_slug)
    if not acc_id: return 0.0

    url = f"{BASE_URL}/CheckAccount/{acc_id}"
    headers = {"Authorization": SEVDESK_TOKEN}
    
    try:
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            return float(res.json()['objects'][0]['balance'])
    except:
        pass
    return 0.0
