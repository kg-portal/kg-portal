import pandas as pd
import sqlite3
import os

# Veritabanı yolu
DB_PATH = os.path.join('data', 'kg_portal.db')

def process_lexware_export(file_path):
    """
    Lexware CSV dosyalarını otomatik tanır ve içeriği veritabanına işler.
    RA: Gelir Faturaları (Sales) - 20.253,04 € verisi buradan gelir.
    RE: Gider Faturaları (Expenses)
    """
    file_name = os.path.basename(file_path)
    
    try:
        # Lexware CSV'leri latin1 ve ';' ayraçlıdır.
        df = pd.read_csv(file_path, sep=';', encoding='latin1')
        
        # Sayısal temizlik (1.234,56 -> 1234.56)
        def clean_val(val):
            if isinstance(val, str):
                # Noktayı kaldır, virgülü noktaya çevir
                return float(val.replace('.', '').replace(',', '.'))
            return val

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        if "Export_RA" in file_name:
            print(f"📊 GELİR DOSYASI TESPİT EDİLDİ: {file_name}")
            for _, row in df.iterrows():
                brutto = clean_val(row['Gesamtbetrag'])
                # RG-Nr üzerinden duble kayıt engelleme
                cursor.execute('''
                    INSERT INTO lexware_cache (invoice_id, nr, datum, kunde, brutto, netto, mwst, offen, status_code)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(invoice_id) DO UPDATE SET 
                        status_code = excluded.status_code,
                        offen = excluded.offen
                ''', (
                    row['RG-Nr.'], row['RG-Nr.'], row['RG-Datum'], 
                    row['Firmenname'], brutto, brutto / 1.19,
                    clean_val(row['Steuerbetrag']), 0.0 if pd.notnull(row['bezahlt am']) else brutto,
                    'paid' if pd.notnull(row['bezahlt am']) else 'open'
                ))
            print(f"✅ {len(df)} adet gelir faturası başarıyla işlendi.")

        elif "Export_RE" in file_name:
            print(f"📉 GİDER DOSYASI TESPİT EDİLDİ: {file_name}")
            # Gider dosyası dolu geldiğinde burası aktif olacak
            if len(df) == 0:
                print("⚠️ Gider dosyası boş, işlem yapılmadı.")
            pass

        conn.commit()
        conn.close()
        return True

    except Exception as e:
        print(f"❌ HATA ({file_name}): {e}")
        return False

if __name__ == "__main__":
    # Klasör ismini tam olarak istediğin gibi tanımladım
    import_dir = "lexware import"
    
    if os.path.exists(import_dir):
        files = [f for f in os.listdir(import_dir) if f.endswith(".csv")]
        if not files:
            print(f"⚠️ '{import_dir}' klasörü boş. CSV dosyalarını içine at!")
        else:
            for f in files:
                process_lexware_export(os.path.join(import_dir, f))
    else:
        print(f"❌ '{import_dir}' klasörü bulunamadı! Lütfen klasörü oluştur.")