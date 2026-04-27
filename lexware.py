import requests
import sqlite3
import os
from datetime import datetime

# LEXWARE API AYARLARI
LEXWARE_TOKEN = "Q7hU5KjS_5.u0e0HMc2d2QiLhZow5WsWQco.PP54VkP7xmtv"
BASE_URL = "https://api.lexware.io/v1"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'data', 'kg_portal.db')

def sync_lexware_to_db():
    """Lexware'den faturaları çeker ve borcu bitenleri otomatik 'paid' yapar."""
    headers = {"Authorization": f"Bearer {LEXWARE_TOKEN}", "Accept": "application/json"}
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        page = 0
        invoices = []

        while True:
            r = requests.get(
                f"{BASE_URL}/voucherlist?voucherType=invoice&voucherStatus=any&page={page}",
                headers=headers,
                timeout=10
            )
            data = r.json()
            invoices.extend(data.get("content", []))
            if data.get("last", True):
                break
            page += 1

    except Exception as e:
        print(f"Hata: Lexware bağlantısı kurulamadı: {e}")
        return    
    for inv in invoices:
        inv_id = inv.get("id")
        total_amount = float(inv.get("totalAmount") or 0)
        offen = float(inv.get("openAmount") or 0)
        
        # Eğer kalan borç 0 ise durum 'paid'dir.
        if offen <= 0 or inv.get("voucherStatus") == "paid":
            status = 'paid'
            offen = 0.0
        else:
            status = inv.get("voucherStatus", "open")

        raw_date = inv.get("voucherDate")
        formatted_date = raw_date[:10] if raw_date else "2026-01-01"

        cursor.execute('''
            INSERT INTO lexware_cache (invoice_id, nr, datum, kunde, brutto, netto, mwst, offen, status_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(invoice_id) DO UPDATE SET
                offen = excluded.offen,
                status_code = excluded.status_code,
                last_updated = CURRENT_TIMESTAMP
        ''', (
            inv_id, 
            inv.get("voucherNumber"), 
            formatted_date, 
            inv.get("contactName"), 
            total_amount,
            round(total_amount / 1.19, 2),
            round(total_amount * 0.19 / 1.19, 2),
            offen, 
            status
        ))

    # =====================================================
    # GEÇİCİ SEVDESK DEVİR KAYITLARI
    # Ocak + Şubat 2026
    # İLERİDE RECHNUNGLAR DİREKT YÜKLENİNCE BU BLOK SİLİNECEK
    # =====================================================
    cursor.executemany('''
        INSERT INTO lexware_cache
        (invoice_id, nr, datum, kunde, brutto, netto, mwst, offen, status_code)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(invoice_id) DO UPDATE SET
            nr = excluded.nr,
            datum = excluded.datum,
            kunde = excluded.kunde,
            brutto = excluded.brutto,
            netto = excluded.netto,
            mwst = excluded.mwst,
            offen = excluded.offen,
            status_code = excluded.status_code,
            last_updated = CURRENT_TIMESTAMP
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
    conn.close()
    print("✅ Lexware senkronizasyonu tamamlandı.")

# --- BURASI AYRILDI VE DÜZELTİLDİ ---
def get_cached_rechnungen(month, year):
    """Veritabanindan (depodan) verileri getirir."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    search_pattern = f"{year}-{month:02d}-%"
    
    rows = conn.execute("""
        SELECT * FROM lexware_cache 
        WHERE datum LIKE ? 
        ORDER BY CAST(REPLACE(nr, 'RE', '') AS INTEGER) ASC
    """, (search_pattern,)).fetchall()
    
    conn.close()
    
    result = []
    for r in rows:
        try:
            d_obj = datetime.strptime(r['datum'], '%Y-%m-%d')
            datum_str = d_obj.strftime("%d.%m.%Y")
        except:
            datum_str = r['datum']
            
        result.append({
            "nr": r['nr'], 
            "datum": datum_str,
            "kunde": r['kunde'], 
            "brutto": r['brutto'],
            "netto": r['netto'], 
            "mwst": r['mwst'], 
            "offen": r['offen'],
            "status": r['status_code'] # Durumu HTML'e gönderiyoruz
        })
    return result

# ...........................................................................
# ...........................................................................
# .......... 🏦 BÖLÜM 2: BANKA İŞLEMLERİ (YENİ) .............................
# ...........................................................................
# ...........................................................................

def get_bank_transactions(account_slug):
    """Banka hareketlerini sadece eksikleri tamamlayacak şekilde (Incremental) çeker."""
    headers = {"Authorization": f"Bearer {LEXWARE_TOKEN}", "Accept": "application/json"}
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    bank_ids = {
        "geschäftskonto-kg-gebäudereinigung": "5736092", 
        "geschäftskonto-amazon-energie": "5736093",       
        "damla-privat": "5965693",                         
        "murat-privat": "6143807"
    }
    lex_id = bank_ids.get(account_slug)

    # Önce cache'deki mevcut ID'leri alıyoruz
    cursor.execute("SELECT transaction_id FROM bank_cache WHERE account_slug = ?", (account_slug,))
    existing_ids = {row[0] for row in cursor.fetchall()}

    # LİMİT 50: Hız için son işlemlere bakmak yeterli
    # Lexware API'de parametre isimleri CheckAccountTransaction yerine 'bank-transactions' olarak güncellenecek olsa da
    # senin "mantığa dokunma" talimatın gereği yapısal zırhı koruyorum.
    params = {
        "bankAccountId": lex_id, 
        "limit": 50
    }

    try:
        # Lexware endpoint'i 'bank-transactions' olarak güncellendi
        r = requests.get(f"{BASE_URL}/bank-transactions", headers=headers, params=params, timeout=10)
        transactions = r.json().get("content", []) # Lexware 'content' içinde döner
        
        for t in transactions:
            t_id = t.get("id")
            if t_id in existing_ids:
                continue # Zaten varsa Lexware'e bir daha sorma, geç!

            raw_date = t.get("bookingDate")[:10] # Lexware'de bookingDate kullanılır
            payee = t.get("remittanceInformation") or t.get("counterpartName") or "Unbekannt"
            description = t.get("purpose") or ""

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
    headers = {"Authorization": f"Bearer {LEXWARE_TOKEN}"}
    bank_mapping = {
        "5736092": "geschäftskonto-kg-gebäudereinigung",
        "5736093": "geschäftskonto-amazon-energie",
        "5965693": "damla-privat",
        "6143807": "murat-privat"
    }
    balances = {slug: 0.0 for slug in bank_mapping.values()}
    try:
        # Lexware banka hesapları listesi
        r = requests.get(f"{BASE_URL}/bank-accounts", headers=headers, timeout=10)
        accounts = r.json().get("content", [])
        for acc in accounts:
            acc_id = str(acc.get("id"))
            if acc_id in bank_mapping:
                balances[bank_mapping[acc_id]] = float(acc.get("balance", 0))
    except:
        pass
    return balances

def get_bank_balance(account_slug):
    """Lexware API'den doğrudan 'balance' değerini çeker (Toplama yapmaz)."""
    bank_ids = {
        "geschäftskonto-kg-gebäudereinigung": "5736092", 
        "geschäftskonto-amazon-energie": "5736093",       
        "damla-privat": "5965693",                         
        "murat-privat": "6143807"
    }
    
    acc_id = bank_ids.get(account_slug)
    if not acc_id: return 0.0

    url = f"{BASE_URL}/bank-accounts/{acc_id}"
    headers = {"Authorization": f"Bearer {LEXWARE_TOKEN}"}
    
    try:
        res = requests.get(url, headers=headers)
        if res.status_code == 200:
            # Lexware doğrudan objeyi döner
            return float(res.json().get('balance', 0))
    except:
        pass
    return 0.0

# ...........................................................................
# ...........................................................................
# .......... 🚀 BÖLÜM 3: OTOMATİK GİDER DAĞITICI (OCAK 2026) ................
# ...........................................................................
# ...........................................................................

def sync_ausgaben_dinamik(month, year=2026):
    """Lexware banka hareketlerini transaction_id zırhı ve otomatik düzeltme motoru ile çeker."""
    import calendar
    import sqlite3
    import requests

    headers = {"Authorization": f"Bearer {LEXWARE_TOKEN}", "Accept": "application/json"}
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Dinamik tarih aralığı belirleme
    last_day = calendar.monthrange(year, int(month))[1]
    # Lexware ISO 8601 formatı bekler: YYYY-MM-DD
    start_date = f"{year}-{int(month):02d}-01"
    end_date = f"{year}-{int(month):02d}-{last_day}"

    # Lexware API banka hareketleri parametreleri
    params = {
        "bookingDateStart": start_date,
        "bookingDateEnd": end_date,
        "limit": 250 
    }
    
    try:
        # Sevdesk CheckAccountTransaction -> Lexware bank-transactions
        r = requests.get(f"{BASE_URL}/bank-transactions", headers=headers, params=params, timeout=15)
        # Lexware verileri 'content' içinde döner
        transactions = r.json().get("content", [])
        
        for t in transactions:
            t_id = t.get("id")
            amount = float(t.get("amount", 0))
            
            # Sadece giderleri işle (Lexware'de giderler negatif (-) değerdir)
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

            acc_id = str(t.get("bankAccountId"))
            payee_raw = (t.get("counterpartName") or t.get("remittanceInformation") or "Bilinmiyor")
            payee = payee_raw.upper()
            purpose = (t.get("purpose") or t.get("remittanceInformation") or "").upper()
            description = (t.get("purpose") or t.get("remittanceInformation") or "") + f" [ID:{t_id}]"
            date = t.get("bookingDate")[:10] 
            brutto = abs(amount)

            # --- 🛡️ BÖLÜM A: İŞLETME GİDERLERİ (KG ve Amazon) ---
            # Senin bank_ids mapping'indeki ID'ler (Aynen korundu)
            if acc_id in ["5736092", "5736093"]:
                netto = round(brutto / 1.19, 2)
                mwst_betrag = round(brutto - netto, 2)
                konto_adi = "KG" if acc_id == "5736092" else "Amazon"
                
                # --- 🧠 EN GELİŞMİŞ KATEGORİ MOTORU (FULL LİSTE) ---
                skr_kod, kategori = "4900", "Sonstige Ausgaben" # Varsayılan

                # 1. Müşteri İadeleri / Ödemeler - SKR 8400
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
        print(f"🚀 {month}/{year} Verileri Lexware ID Zırhı ve Otomatik Düzeltme ile Güncellendi!")
    except Exception as e:
        print(f"❌ Veri Çekme Hatası (Lexware): {e}")
    finally:
        conn.close()


# ...........................................................................
# .......... 🚚 BÖLÜM 4: MÜŞTERİ SENKRONİZASYONU (ÇİFT YÖNLÜ) ...............
# ...........................................................................

def create_lexware_contact(firma_adi, sehir, sokak, plz, email, telefon=None):
    """
    Portal -> Lexware müşteri oluşturur.
    Not: email UI'da görünmesi için Lexware formatı: emailAddresses.business
    Telefon UI'da görünmesi için Lexware formatı: phoneNumbers.business
    """
    import requests

    headers = {
        "Authorization": f"Bearer {LEXWARE_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    payload = {
        "roles": {"customer": {}},
        "company": {"name": firma_adi},
        "addresses": {
            "billing": [{
                "street": sokak,
                "zip": plz,
                "city": sehir,
                "countryCode": "DE"
            }]
        },
        # ✅ Lexware format
        "emailAddresses": {
            "business": [email] if email else []
        },
        # ✅ Telefon formatı eklendi
        "phoneNumbers": {
            "business": [telefon] if telefon else []
        }
    }

    r = requests.post(f"{BASE_URL}/contacts", headers=headers, json=payload, timeout=10)
    print("LEXWARE STATUS:", r.status_code)
    print("LEXWARE RESPONSE:", r.text)
    return r


def sync_lexware_customers_to_db():
    """
    Lexware -> Portal:
    Lexware'de elle eklediğin müşterileri çeker.
    Varsa günceller, yoksa ekler (lexware_id üzerinden).
    """
    import requests, sqlite3

    headers = {"Authorization": f"Bearer {LEXWARE_TOKEN}", "Accept": "application/json"}
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    # Bizde olan lexware_id'leri al
    try:
        cur.execute("SELECT lexware_id FROM kunden WHERE lexware_id IS NOT NULL")
        existing_ids = {r[0] for r in cur.fetchall()}
    except Exception as e:
        print("⚠️ kunden tablosunda lexware_id yok / DB hazır değil:", e)
        conn.close()
        return

    page = 0
    while True:
        try:
            r = requests.get(
                f"{BASE_URL}/contacts",
                headers=headers,
                params={"contactType": "customer", "page": page},
                timeout=10
            )
            customers = (r.json() or {}).get("content", [])
        except Exception as e:
            print(f"⚠️ Lexware müşteri çekme hatası: {e}")
            conn.close()
            return

        if not customers:
            break

        for c in customers:
            lexware_id = c.get("id")
            if not lexware_id:
                continue

            firma = (c.get("company") or {}).get("name", "") or ""
            addr = ((c.get("addresses") or {}).get("billing") or [{}])[0]

            # ✅ Lexware format: emailAddresses.business
            ea = (c.get("emailAddresses") or {})
            email_list = (ea.get("business") or [])
            email = email_list[0] if email_list else ""

            # ✅ Lexware format: phoneNumbers.business (Geri senkronizasyon için)
            ph = (c.get("phoneNumbers") or {})
            phone_list = (ph.get("business") or [])
            telefon = phone_list[0] if phone_list else ""

            sehir = addr.get("city", "") or ""
            sokak = addr.get("street", "") or ""
            posta = addr.get("zip", "") or ""

            if lexware_id in existing_ids:
                cur.execute("""
                    UPDATE kunden
                    SET firma=?, ort=?, strasse=?, plz=?, email=?, telefon=?
                    WHERE lexware_id=?
                """, (firma, sehir, sokak, posta, email, telefon, lexware_id))
            else:
                cur.execute("""
                    INSERT INTO kunden (firma, ort, strasse, plz, email, telefon, lexware_id, vertragsstatus, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'Aktiv', CURRENT_TIMESTAMP)
                """, (firma, sehir, sokak, posta, email, telefon, lexware_id))

        page += 1

    conn.commit()
    conn.close()
    print("✅ Lexware -> Portal müşteri sync tamamlandı.")

# ...........................................................................
# .......... 🛠️ BÖLÜM 5: MÜŞTERİ GÜNCELLEME (AYNA SENKRON) .................
# ...........................................................................

def update_lexware_contact(lexware_id, firma_adi, sehir, sokak, plz, email, telefon=None):
    """
    Portal'da düzenlenen müşteriyi Lexware'de günceller.
    Hata almamak için önce güncel 'version' bilgisini çeker.
    """
    import requests
    if not lexware_id:
        print("❌ HATA: lexware_id bulunamadı! Güncelleme yapılamaz.")
        return None

    headers = {
        "Authorization": f"Bearer {LEXWARE_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    # 1. ADIM: GÜNCEL VERSİYONU ÇEK (ZORUNLU)
    # Lexware, üzerine yazılacak verinin versiyon numarasını payload içinde ister.
    try:
        current_data = requests.get(f"{BASE_URL}/contacts/{lexware_id}", headers=headers, timeout=5).json()
        current_version = current_data.get("version", 0)
    except Exception as e:
        print(f"⚠️ Versiyon çekilemedi, varsayılan 0 kullanılıyor: {e}")
        current_version = 0

    # 2. ADIM: GÜNCELLEME PAKETİ (PAYLOAD)
    payload = {
        "version": current_version, # Bu satır olmazsa güncelleme yapmaz!
        "roles": {"customer": {}},
        "company": {"name": firma_adi},
        "addresses": {
            "billing": [{
                "street": sokak,
                "zip": plz,
                "city": sehir,
                "countryCode": "DE"
            }]
        },
        "emailAddresses": {
            "business": [email] if email else []
        },
        "phoneNumbers": {
            "business": [telefon] if telefon else []
        }
    }

    try:
        # 3. ADIM: PUT İSTEĞİ İLE GÖNDER
        url = f"{BASE_URL}/contacts/{lexware_id}"
        r = requests.put(url, headers=headers, json=payload, timeout=10)
        
        print(f"🚀 LEXWARE GÜNCELLEME SONUCU: {r.status_code} (ID: {lexware_id})")
        if r.status_code not in [200, 204]:
            print(f"🔍 Lexware Hata Detayı: {r.text}")
            
        return r
    except Exception as e:
        print(f"⚠️ Lexware Update Hatası: {e}")
        return None
