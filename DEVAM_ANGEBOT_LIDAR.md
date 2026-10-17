# DEVAM – Angebot / Besichtigung / LiDAR (KG Scan) · 17.10.2026

Sadece bu konu için devam dosyası: KG Scan (LiDAR) → CRM Besichtigung → Kalkulation → Angebot (PDF + LV + KG-Mail).
Genel kurallar, sunucu ve diğer işler için ana dosya: `DEVAM.md` (bu repoda). Muhasebe/Lexware/LifeHub burada YOK.

## Kullanıcı ve kurallar (kısa)
- Murat Kicci. Türkçe, çok kısa, sonuç önce. Sesle yazar, yazım bozuk olabilir. Yorgun ve sabırsız; küfredebilir, kişisel alma.
- "yap" demeden başlama. İstenmeyen şeyi "iyileştirme". Çalışanı bozma, sadece ekle.
- Kısıtları/riskleri **baştan** söyle (geç söylenince çok kızıyor: "baştan söylesene").
- Fikrini dürüst söyle, "sırf o dedi diye" yapma (kendisi istedi).
- Sunucuya erişimim yok. Murat PowerShell'de tek satır çalıştırır: `ssh root@46.224.155.41 '…'`.
  İç çift tırnak yok. Python gerekiyorsa base64 ile: `echo <b64> | base64 -d | .venv/bin/python` (aşağıda örnek).
- Test sadece "ZZ Test …" firmalarla. Gerçek veriye yazan komutlar için önce onay.
- kg-portal push: claude branch + `kg-ai-mail-test` + `main` aynı commit. Deploy'u Murat yapar:
  `ssh root@46.224.155.41 'cd /opt/kg-crm && git pull -q --ff-only origin main && systemctl restart kg-crm && sleep 5 && systemctl is-active kg-crm && git log --oneline -1'`
- CRLF dosyalar CRLF kalır (besichtigung.html, app2.py, datenbank.html …). Python ile binary modda düzenle.
- Commit trailer (bu oturum): `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` + `Claude-Session: https://claude.ai/code/session_01YX8HHBaFqxv5miFs6Ep6Qw`

## Repolar
| Repo | Branch | Ne |
|---|---|---|
| `kg-portal/kg-portal` | `claude/kg-scan-3d-drehen` (= main = kg-ai-mail-test, 395d9a1) | KG CRM (Flask) |
| `kg-portal/kgscanv2test` | `angebot-test` (776a02a) | KG Scan iPhone app (test). Yeni oturumda `add_repo` ile ekle. |

## Murat ile anlaşılan HEDEF SİSTEM (17.10.)
```
Firma → Besichtigung → Kalkulation → Angebot → Warteliste → Kunde
        (Aufmaß)        (Festpreis)   (Nr + PDF + LV + Mail)
```
1. **CRM tek merkez.** Firma, ölçü, fiyat, Angebot-Nr, PDF, mail – hepsi CRM'de, tek kayıt (`tagesliste_leads`).
2. **KG Scan app sadece ölçer** (LiDAR → CRM). Fiyat hesabı app'ten CRM'e taşınacak (öneri, Murat henüz kesin "evet" demedi).
3. **Aufmaß alan bazlı:** Büro / WC / Küche / Flur → toplam m² + sayılar (WC, Urinal, WB, Tisch, PC, Müll). LiDAR varsa otomatik dolar,
   yoksa "Manuell erfassen" ile 4 satır elle. Oda oda detay isteğe bağlı.
4. **Fiyatı Murat belirler:** Festpreis ana alan. Hesaplayıcı sadece "Richtpreis" gösterir. Görmediği fiyat asla gitmez.
5. **Angebot-Nr sadece CRM verir.** Elle numara bir daha olmayacak.
6. Besichtigung sayfası sonra "premium" yenilenecek: önce tıklanabilir tasarım örneği, sonra parça parça. Eski sayfa yedek kalır.

## SIRADAKİ İŞLER (sırayla; her biri "yap" bekliyor)
1. **Festpreis + Angebot pozisyonları** (mevcut Besichtigung sayfasına küçük ek, bugün yapılacaktı):
   - Alanlar: Leistung 1–4 (Text, Einheiten, Preis) + Ausführungszeitraum. Örnek format (Murat'ın onayladığı, AN-2266 INCONSULT):
     `Leistung 1: 1x wöchentliche Büroreinigung (165 m²) · monatlich · 315,30 €`, `Leistung 2: Einmalige Grundreinigung · einmalig · 495,00 €`.
     "pauschal" YAZMA. Fiyat netto; şablondaki sabit "zzgl. gesetzlicher MwSt." satırı kalır (KDV tutarı yazılmaz).
   - Leistungsart örn. "Büroreinigung" / "Büroreinigung und Grundreinigung". Boş pozisyon satırları sağda boş görünmeli.
   - `/datenbank/angebot-senden` bu alanlar doluysa formül yerine onları kullanmalı (angebot_vars_json + angebot_netto). Boşsa eski davranış aynen.
   - LV frekansları Besichtigung'daki Häufigkeit'ten (zaten `lv_build_vars_from_besichtigung`, app2.py ~5714).
2. **AN-2267'yi CRM'e kaydet** (veri yazar → önce Murat onayı). Bkz. "AN-2267" bölümü.
3. **LiDAR düzeltmeleri** (app, angebot-test): hata olunca tarama oturumunu sıfırla + taranan odaları koru, tarama sırasında
   monitör (YOLO) tanımayı kapat, hata yazısını app'te sakla. Sebep tahmini aşağıda.
4. **Offline build'i test:** Codemagic → workflow `kgscan-v2-unsigned-ios` → branch `angebot-test` → SideStore kur → bir kez internetle aç
   (liste önbelleğe) → uçak modunda "ZZ Test …" ile tara/kaydet → internet aç → CRM'e otomatik gitmeli.
5. 2–3 gerçek keşifte sorunsuzsa 99 € Apple Developer (şimdilik ücretsiz Apple ID, 7 gün imza).
6. Premium Besichtigung sayfası (tasarım örneği önce).
7. Eski/kullanılmayan parçaları temizle (önce kullanım kontrolü, onaysız silme): datenbank.html içindeki srcdoc iframe'ler
   (Tagesliste / Besichtigungstermine / Angebot Warteliste kopyaları, statik `angebote` verisi, satır ~3058/5520/5805).
8. Eski formül hatası: `angebot_parse_weekly_frequency("14-tägig")` → 14 (x/Woche) döner. Yeni hesapta düzelt.

## AN-2267 (16.10.2026, CRM DIŞINDA elle yapıldı)
- NetPlans GmbH, Ungelsheimer Weg 14, 40472 Düsseldorf. Leistungsart "Büroreinigung".
- Leistung 1: "1x wöchentliche Büroreinigung" · monatlich · 540,00 € (netto). Ausführungszeitraum "nach Vereinbarung". Datum 16.10.2026.
- LV: Büro/WC/Küche/Flur "1x wöch." (INCONSULT örneği gibi; Schreibtische, Elektro, Spülmaschine, Küchengeräte "-").
- PDF'ler CRM şablonlarıyla lokal render edildi (jinja + Playwright) ve Murat'a verildi.
- Fensterreinigung fiyatı Murat söyleyecek (Leistung 2, pro Durchgang).
- CRM bu numarayı bilmiyor → CRM'in bir sonraki Angebot'u yine 2267 olur. Kayıt yapılana kadar dikkat.
- Son numaralar (16.10.): AN-2163, 2164, 2259 Monika Knops, 2265 4Crane, 2266 INCONSULT (05.08.2026).

Numara okuma (sadece okur, `mode=ro`). Script:
```python
import sqlite3,re
c=sqlite3.connect('file:data/kg_portal.db?mode=ro',uri=True)
rows=c.execute("select angebot_nr,firma,angebot_datum,status from tagesliste_leads where angebot_nr is not null and angebot_nr!=''").fetchall()
def n(v):
    m=re.search(r'(\d+)',str(v))
    return int(m.group(1)) if m else 0
rows=sorted(rows,key=lambda r:n(r[0]))
for r in rows[-5:]:
    print('AN-%s | %s | %s | %s'%(n(r[0]),r[1],r[2],r[3]))
print('NAECHSTE NUMMER: AN-%d'%(max([n(r[0]) for r in rows if n(r[0])>=2265]+[2264])+1))
```
Komut: `ssh root@46.224.155.41 'cd /opt/kg-crm && echo <base64 of script> | base64 -d | .venv/bin/python'`

## CRM – teknik bilgiler (kg-portal)
- Sunucu: Hetzner 46.224.155.41, `/opt/kg-crm`, gunicorn 8803, DB `data/kg_portal.db` (sqlite3 CLI yok → python).
  Domain: `https://portal.kg-reinigung.de` (eski `kg-portal.onrender.com` oraya yönlenir; app hâlâ onrender adresini kullanıyor).
- Akış: Tagesliste → status `besichtigung` → `templates/besichtigung.html`
  - Speichern = `/datenbank/tagesliste-status` + `/datenbank/angebot-preview` (sadece alert).
  - Senden = `/datenbank/angebot-senden` → status `angebot`, `angebot_nr` (`angebot_next_number`: max(≥2265)+1), `angebot_vars_json`,
    netto/mwst/brutto. Hepsi app2.py ~4722–4930.
- Eski formül (app2.py ~850–1030): netto = Σ m² / Leistungswert (Büro 170, WC 55, Küche 110, Flur 250 m²/h) × x/Woche × 4,33 × 35 €.
  Elle fiyat girme yeri YOK.
- `templates/angebotvorlage.html` (4 sayfa): değişkenler Nr, Datum, Kunde, Objekt, Adresse, Plz, Ort, Leistungsart, Leistung_1..4,
  Einheiten_1..4, Preis_1..4, Ausführungszeitraum. `{{X or "-"}}` → boşluk (" ") verilirse sağ taraf boş kalır.
  `/angebotvorlage?angebot_id=ID` DB'den; parametresiz URL query'den doldurur; `&print=1` → yalın sayfa + otomatik yazdır.
- `templates/Leistungsverzeichnis.html` (2 sayfa, base.html'i extend eder): `/leistungsverzeichnis?angebot_id=ID`.
  Query modunda Freq_* alanları sabit "-" (URL'den alınmaz).
- Angebot Warteliste (datenbank.html ~7270+): Vorschau/PDF/LV butonları, `kgAngebotZurueckBesichtigung`, `openAngebotMailInterface`.
  Mail ekleri: `/api/datenbank/angebot-mail-attachments` (Playwright ile iki PDF).
- Direkt Angebot: `/api/datenbank/direktes-angebot-neukunde` ve `…-bestandskunde` → Besichtigung kaydı açar.
- KG Scan API (header `X-KG-SCAN-TOKEN`, env `KG_SCAN_API_TOKEN` – token'ı asla yazma):
  `/internal/kg-scan/besichtigungen` (GET), `/neukunde`, `/result` (raeume'yi ÜZERİNE yazar → tekrar göndermek güvenli;
  sadece status `besichtigung` olan kayıt), `/model` (USDZ), `/previews` (2D/3D PNG).
- 395d9a1 (main'de, deploy edildi mi Murat'a sor): Besichtigung "3D drehen" (iPhone tarzı three.js r128, `kgRenderGeometry3D`),
  2D/3D önizleme yatay+büyük (`kgPreviewBounds`, `kgFitPreviewImage`, `.kg-scan-3d-view` min-height 560px),
  `/result` artık `floor_polygons` saklıyor.
- Besichtigung sayfası bugün: hero (Kunde/Objekt/Ansprechpartner/Termin) + KG Scan kartı (3D + sayılar) + Leistungen kutuları +
  Elemente (Tisch/PC/Müll sayaç, scan'den DOLMUYOR) + Sonstiges + "<Leistung> Details" (alan grupları: Boden, Wie häufig, oda m²) +
  Speichern/Senden. ~3.900 satır.

## KG Scan app – teknik bilgiler (kgscanv2test, angebot-test)
- SwiftUI + WKWebView. Bütün arayüz `RoomPlanV2TestView.swift` içinde raw string `private static let html = #"""…"""#`.
  Mesaj kanalı `roomPlan` (KGWebBridge). Mac/Swift derleyici yok → kontrol: tree-sitter-swift (html string'i boşaltıp parse) + gömülü JS'i
  çıkarıp `node --check`. Codemagic: XcodeGen, imzasız IPA, SideStore, ücretsiz Apple ID.
- **WKUIDelegate yok → JS `alert()` iPhone'da GÖRÜNMEZ.** Mesajlar için ekrandaki not/status alanlarını kullan.
- Commitler: 447642f Angebotsrechner (test) · fc01710 mobil düzeltme · 9a1f1c9 CRM'e birleşik `structureGeometry` + `floor_polygons` ·
  776a02a **offline** (aşağıda). Hiçbiri henüz build/test edilmedi (Murat).
- Offline (776a02a): CRM listesi `kg_crm_besichtigungen_cache` (besichtigung_data_json'suz); internetsiz "Neue Besichtigung" →
  `kg_offline_leads` (⏳); kayıt `kg_projects[].crm = {status: pending|sending|sent|error, id, lead}`; model `KGScanModels/pending_<id>.usdz`;
  internet gelince (app açılışı, liste açma, kaydetme) sırayla: neukunde → cacheSavedScenePreviews → `sendPendingScan`.
  "Gespeicherte Objekte"de durum + "CRM" butonu. Timeout: liste 10 s, neukunde 15 s. Payload `kgBuildCRMPayload` (eskisiyle birebir, test edildi).
- Angebotsrechner: `angebot-test/angebot-rechner.html` (tek dosya, app'e gömülü, `KGAngebotView.swift`, `window.KG_SCAN_IMPORT`).
  Formül: dakika = m²/(Leistungswert×Verschmutzung)×60 + Ausstattung; h/Monat = h/Woche×4,33; Lohn 15,00 € (LG1 2026);
  Urlaub/Feiertag oranlı; Krank − U1; Minijob-Pauschale 31,17 % + BG; + Material/Anfahrt/Verwaltung; Gewinn-Staffel
  1×75 % / 2×60 % / 3×50 % / 4×40 % / 5×35 % (manuel değişir). Değerler Steuerberater ile kontrol edilmedi.
- **Sahadaki hata (Praxis, 16.10.):** Raumweise 2 WC taradı, sonra sürekli "Scan-Fehler" (RoomPlan'ın kendi hatası, internetle ilgisi yok).
  Tahmin: Raumweise tüm odalar tek AR oturumunda (`keepARSessionAlive`); hata sonrası oturum sıfırlanmıyor → sonraki odalar da hata.
  Küçük WC (fayans/ayna) takip kaybı + tarama sırasında sürekli çalışan YOLO monitör tanıma (ısı/yük). Hata metni bilinmiyor → kaydetmeli.

## Test yöntemi (önceki oturumda kullanıldı; scratchpad dosyaları yeni oturumda YOK, yeniden yaz)
- CRM sayfaları: Playwright (`/opt/node-tools/node_modules/playwright`), sayfa sahte origin `http://crm.test` + `route.fulfill`;
  CDN engelli → three@0.128.0 `npm pack` ile.
- PDF: jinja2 ile `angebotvorlage.html` / `Leistungsverzeichnis.html` render (stub `request.args.print="1"`, `url_for`→ static dosya),
  Playwright `page.pdf` A4.
- App: gömülü HTML'i çıkar, `window.webkit.messageHandlers.roomPlan` stub'ı ile internet açık/kapalı simülasyonu.
