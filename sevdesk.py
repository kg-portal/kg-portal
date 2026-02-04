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
# ...........................................................................
# ...........................................................................
# .......... 🚀 BÖLÜM 3: OTOMATİK GİDER DAĞITICI (OCAK 2026) ................
# ...........................................................................
# ...........................................................................

def sync_ausgaben_dinamik(month, year=2026):
    """Sevdesk banka hareketlerini transaction_id zırhı ve otomatik düzeltme motoru ile çeker."""
    import calendar
    import sqlite3
    import requests

    headers = {"Authorization": SEVDESK_TOKEN, "Accept": "application/json"}
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Dinamik tarih aralığı belirleme
    last_day = calendar.monthrange(year, int(month))[1]
    start_date = f"{year}-{int(month):02d}-01T00:00:00Z"
    end_date = f"{year}-{int(month):02d}-{last_day}T23:59:59Z"

    params = {
        "startDate": start_date,
        "endDate": end_date,
        "limit": 250 
    }
    
    try:
        r = requests.get(f"{BASE_URL}/CheckAccountTransaction", headers=headers, params=params, timeout=15)
        transactions = r.json().get("objects", [])
        
        for t in transactions:
            t_id = t.get("id")
            amount = float(t.get("amount", 0))
            
            # Sadece giderleri işle
            if amount >= 0: 
                continue 
            
            # --- 🛡️ AKILLI GÜNCELLEME ZIRHI (UPSERT MANTIĞI) ---
            search_id = f"%[ID:{t_id}]%"
            
            # Önce bu ID ile mevcut kayda bakıyoruz
            cursor.execute("SELECT kategori FROM gewerbliche_ausgaben WHERE zweck LIKE ?", (search_id,))
            row_g = cursor.fetchone()
            
            # Eğer kayıt varsa ve kategorisi zaten belirlenmişse (Sonstige değilse) atla
            if row_g and row_g[0] != "Sonstige Ausgaben":
                continue

            cursor.execute("SELECT id FROM private_ausgaben WHERE zweck LIKE ?", (search_id,))
            if cursor.fetchone(): 
                continue 
            # --------------------------------------------------

            acc_id = str(t.get("checkAccount", {}).get("id"))
            payee_raw = (t.get("payeeName") or t.get("payeePayerName") or "Bilinmiyor")
            payee = payee_raw.upper()
            purpose = (t.get("description") or t.get("paymtPurpose") or "").upper()
            description = (t.get("description") or t.get("paymtPurpose") or "") + f" [ID:{t_id}]"
            date = t.get("valueDate")[:10] 
            brutto = abs(amount)

            # --- 🛡️ BÖLÜM A: İŞLETME GİDERLERİ (KG ve Amazon) ---
            if acc_id in ["5736092", "5736093"]:
                netto = round(brutto / 1.19, 2)
                mwst_betrag = round(brutto - netto, 2)
                konto_adi = "KG" if acc_id == "5736092" else "Amazon"
                
                # --- 🧠 EN GELİŞMİŞ KATEGORİ MOTORU (FULL LİSTE) ---
                skr_kod, kategori = "4900", "Sonstige Ausgaben" # Varsayılan

                # 1. Müşteri İadeleri / Ödemeler (Resimdeki Liste) - SKR 8400
                musteriler = [
                    "JESKE", "HAASE", "DEMANT", "ROSSBACH", "BOLTEN", "BLÜGGEL", "PANZOG", "MÜLLER", 
                    "PETERS IMMO", "RHEINBAU", "MB ENERGY", "GASSEN", "STAPELTOR", "LEFFEK", "DMG DIESEL", 
                    "SYNERGIE", "NJP GROSTOLLEN", "WOHNWERT", "CTV DUISBURG", "KAPUSCZOK", "SURMUND", 
                    "SCHWERTFEGER", "EGIT", "KARL-HEINZ EFKEMANN", "MALTESER", "CO-GEM"
                ]
                if any(m in payee for m in musteriler):
                    skr_kod, kategori = "8400", "Erlöse/Zahlung"

                # 2. Araç & Kredi & Leasing (SKR 4530 / 4970)
                elif any(x in payee for x in ["SHELL", "ARAL", "ESSO", "TOTAL", "MERCEDES", "KARCHER", "MLF MERCATOR", "BARCLAYS", "CONSORS", "LEASING"]):
                    skr_kod, kategori = "4530", "Kfz-Kosten / Kredi"
                
                # 3. Kiralar (Büro & Garaj) (SKR 4210)
                elif any(x in payee for x in ["KIEFER & ZEHNER", "THOMAS NIESSEN", "MARGARETE GAUER", "CEM ÜLGER", "MIETE"]):
                    skr_kod, kategori = "4210", "Miete/Pacht"

                # 4. Sosyal Güvenlik & Sigorta (SKR 4130 / 4360)
                elif any(x in payee for x in ["KNAPPSCHAFT", "IKK", "BG BAU", "SIGNAL IDUNA", "ERGO", "AXA", "DEURAG"]):
                    skr_kod, kategori = "4130", "Sozialabgaben & Vers."

                # 5. Personel Maaşları (SKR 4100)
                elif "LOHN" in purpose or any(p in payee for p in ["ÖZDES MURAT", "AYHAN", "SEMRA", "MELIH", "PEDRIE", "ARZU", "SEVVAL", "KEMAL", "MUSTAFA", "RAMAZAN", "TULAY", "HATIC", "CIGDEM"]):
                    skr_kod, kategori = "4100", "Löhne und Gehälter"

                # 6. İnternet & Telefon (SKR 4910)
                elif any(x in payee for x in ["TELEKOM", "TELEFONICA", "VODAFONE", "CHECKDOMAIN", "DOMAINFACTORY"]):
                    skr_kod, kategori = "4910", "Internet & Telefon"

                # 7. Enerji (Gas, Strom, Wasser) (SKR 4240)
                elif "STADTWERKE" in payee or "EWE VERTRIEB" in payee:
                    skr_kod, kategori = "4240", "Gas, Strom, Wasser"

                # 8. Malzeme & Ofis (SKR 3400 / 4930)
                elif any(x in payee for x in ["TOOM", "REWE", "CLEAN CONNECTION", "TRIVANTO", "METRO", "LIDL", "ALDI", "NETTO", "BIE-DRO"]):
                    skr_kod, kategori = "3400", "Wareneinkauf"
                elif any(x in payee for x in ["ADOBE", "OPENAI", "ESET", "NORTON", "MICROSOFT", "GOOGLE GSUITE", "ESELT GMBH", "TRINKGUT"]):
                    skr_kod, kategori = "4930", "Bürobedarf & EDV"

                # 9. Vergi & Banka & PayPal (SKR 1780 / 4970)
                elif "FINANZAMT" in payee:
                    skr_kod, kategori = "1780", "Umsatzsteuer"
                elif "PAYPAL" in payee or "NEXI" in payee:
                    skr_kod, kategori = "4970", "Nebenkosten Geldverkehr"

                # 10. Nakit & Şahsi (SKR 1800)
                elif any(x in payee for x in ["DAMLA KICCI", "MURAT KICCI", "WANH.ORT", "HOCHFELD"]):
                    skr_kod, kategori = "1800", "Privatentnahme"

                # --- GÜNCELLEME VEYA EKLEME KARARI ---
                if row_g and row_g[0] == "Sonstige Ausgaben" and kategori != "Sonstige Ausgaben":
                    cursor.execute('''
                        UPDATE gewerbliche_ausgaben 
                        SET skr03_kod = ?, kategori = ? 
                        WHERE zweck LIKE ?
                    ''', (skr_kod, kategori, search_id))
                elif not row_g:
                    cursor.execute('''
                        INSERT INTO gewerbliche_ausgaben (datum, skr03_kod, kategori, empfaenger, zweck, brutto, mwst_betrag, netto, konto, monat)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (date, skr_kod, kategori, payee_raw, description, brutto, mwst_betrag, netto, konto_adi, f"{int(month):02d}"))

            # --- 🏠 BÖLÜM B: ŞAHSİ GİDERLER (Damla ve Murat) ---
            elif acc_id in ["5965693", "6143807"]:
                owner = "Damla" if acc_id == "5965693" else "Murat"
                cursor.execute('''
                    INSERT INTO private_ausgaben (datum, skr03_kod, kategori, empfaenger, zweck, betrag, konto, monat)
                    VALUES (?, '1800', 'Privatentnahme', ?, ?, ?, ?, ?)
                ''', (date, payee_raw, description, brutto, owner, f"{int(month):02d}"))

        conn.commit()
        print(f"🚀 {month}/{year} Verileri ID Zırhı ve Otomatik Düzeltme ile Güncellendi!")
    except Exception as e:
        print(f"❌ Veri Çekme Hatası: {e}")
    finally:
        conn.close()
