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