# DEVAM – KG Portal / Stundenzettel (07.10.2026)

Bu dosya yeni bir Claude Code oturumunun kaldığı yerden devam etmesi içindir. Repo: `kg-portal/kg-portal`. Branch: `claude/leon-devam-dosyasi-m7z3hz` (ayrıca `kg-ai-mail-test` ve `main`'e de aynı commit push edilir; deploy'u kullanıcı yapar).

## Kullanıcı
- Murat Kicci (Özdes Murat Kicci), KG Gebäudereinigung / KG Business sahibi. Worker id 2, tel +491632944220.
- Türkçe, kısa, sade, "sen". Sesle yazdırır, yazım bozuk olabilir. Çok yorgun ve sabırsız; küfredebilir, kişisel almayın.

## KURALLAR (ZORUNLU)
1. **SADECE İSTENENE DOKUN.** Eski çalışan hiçbir şeyi silme/bozma/değiştirme. Başka bir fonksiyonu etkileyecekse başlamadan madde madde söyle, onay al. Her değişiklikten sonra karşılaştır, raporla.
2. Konuşurken/soru sorarken işlem yapma. "yap / uygula / kur / başla" olmadan başlama. "sil" denmeden silme.
3. İstenmeyen ekstra özellik ekleme ("Benim söylemediğim şeylere kafa yorma").
4. **Secret (token, şifre, key) asla koda/sohbete yazılmaz**, erişim kodları ekrana basılmaz.
5. KG Business'ı asla bozma.
6. PowerShell tek satır komutlar cevabın sonunda; `$` yok, iç çift tırnak yok. Base64 her zaman araçla üretilir, `cmp` ile doğrulanır. Deploy öncesi sessiz-abort kontrol yok.
7. CRLF dosyalar korunur: openai_client.py, base.html, todo.html, kg_todo_routes.py, datenbank.html, gmail_mini.html, index.html, stundenzettel.html, whatsapp_inbox.html, Mitarbeiter.html.
8. Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` + `Claude-Session: https://claude.ai/code/session_01MwpvaxvhGMvdYoZRPPrQA6`. Kod/commit/PR içinde başka model adı yazma.
9. Deploy'u kullanıcı yapar. Push: claude branch + kg-ai-mail-test + main (aynı commit).

## Sunucu (Hetzner 46.224.155.41)
- kg-crm: `/opt/kg-crm`, port 8803, gunicorn (`migration/hetzner/crm_helfer.py start`), venv `/opt/kg-crm/.venv`. DB: `data/kg_portal.db`. Env: `geheim/crm_env.json`.
- Deploy: `cd /opt/kg-crm && git pull -q --ff-only origin main && systemctl restart kg-crm && sleep 5 && systemctl is-active kg-crm && git log --oneline -1`
- Sunucu saati UTC. sqlite3 CLI yok (python kullan).
- kg-whatsapp connector: `/opt/kg-portal/kg_whatsapp_connector`, env `/etc/kg-whatsapp.env`. Damla'nın WhatsApp'ı. Sunucudaki connector dosyası repodan FARKLI (lidToPhone eklentisi var) – deploy ederken yerinde patch + yedek + `node --check`.

## Bu oturumda YAPILAN ve PUSH EDİLEN (son commit 0ffe259)
1. **İşçi sayfası yeni görünüm (Modell A, KG mavisi):** `templates/stundenzettel_worker_neu.html`. app.py worker_stundenzettel route'u `STZ_NEU_IDS` env'ine göre seçiyor (varsayılan "alle" = herkes; tek tek kısıtlamak için örn. `STZ_NEU_IDS=2`). Eski sayfa `stundenzettel_worker.html` aynen duruyor. Aynı endpointler: `/api/stundenzettel/save`, `/delete`, `/<id>?code=`. Tik = imza, 40 saat / 600 €, Resturlaub, 19'unda uyarı, PC'de yazdırma. Feiertag günü sabit saat + Feste-Zeiten'deki yer gösteriliyor; Krank/Urlaub/Feiertag "ändern" listesinin en altında.
2. **CRM admin listesi yeni görünüm:** `templates/stundenzettel_liste_neu.html` + app.py `_stz_uebersicht` + `/stundenzettel/uebersicht`. Kart başına: saat, Lohn, gün, durum, Krank/Urlaub/Extra. Ay seçici, arama, "Öffnen" sağdan tam boy drawer (↑↓ ile sıradaki işçi, link kopyala, yeni pencere). Kaydet/sil sonrası rakamlar anında güncellenir (postMessage + visibilitychange + 60 sn). İnce mavi çerçeveler. Lohn kutusu: büyük = Lohn+Extra toplamı, altında ayrı ayrı. Eski görünüm `/stundenzettel?alt=1`.
3. **Feste Zeiten aylık ekstralar ("ayda bir"):** `stundenzettel_auto.py` yeni tablo `stundenzettel_extras` + `_extras_eintragen`. İki kural: `erster_arbeitstag` (ilk iş gününün bitişine saat ekle), `samstag_mitte` (15'e en yakın cumartesi; o gün varsa ekle, yoksa start'tan yeni yaz). Feste Zeiten sayfası ekstraları gösteriyor.

## AÇIK İŞ 1 — Feste Zeiten'i kaydet + Ekim'i doldur (ONAYLANDI, uygulanmayı bekliyor)
Script repoda: `migration/hetzner/feste_zeiten_einmalig.py`. 22 işçinin planını kaydeder, ekstraları ekler, Ekim 2026'yı doldurur (Kemal id4 + Arzu id5 hariç – Ekim'i doldurma, sadece plan). Önce DB yedeği alır, her işçinin adını kontrol eder (uymazsa yazmaz). Deploy komutu (kullanıcıya ver):

```
ssh root@46.224.155.41 "cd /opt/kg-crm && git pull -q --ff-only origin main && systemctl restart kg-crm && sleep 5 && systemctl is-active kg-crm && .venv/bin/python migration/hetzner/feste_zeiten_einmalig.py"
```

Onaylanan saatler (script içinde de var):
- 37 Atanas Murov: Mo–Fr 18:00–19:45 Duisburg
- 34 Seher Karatas: Mi 17:00–19:00 Neuenkamp · Sa 10:00–14:00 Neuenkamp (cumartesi yeri varsayım)
- 32 Valbone Özcan: Mo, Mi 18:00–19:00 · Fr 18:00–19:30 Düsseldorf
- 33 Yemen Findik: Do 14:00–16:00 Hamborn
- 29 Birgül Zengin: Mi 17:00–18:45 · Sa 12:00–14:00 Wanheimerort
- 27 Kebire Yigman: Sa 15:00–19:00 Großenbaum
- 3 Hatice Corbaci: Do 17:00–18:00 · Sa 10:00–14:00 Wanheimerort · ekstra: ilk iş günü +0:45, ayın ortası cumartesi 10:00–17:00
- 6 Pedrie Sali Mehmed: Mo,Di,Mi,Fr 15:00–17:00 · Do 17:00–19:00 Duisburg (44 Std → 600€ cap, 4 Extra, Minijob sınırı dikkat)
- 8 Serpil Pekdemir: Mi 15:30–19:30 · Fr 13:00–17:30 Duisburg
- 9 Semra Göktas: Mi 17:00–19:00 Ruhrort
- 10 Tülay Maras: Fr 17:00–19:00 Duisburg Mitte
- 11 Gülbahar Inanc: Mo,Mi 13:00–14:30 · Fr 13:00–15:00 Meiderich / Beeck
- 13 Ayten Kaya: Mo,Mi,Do,Fr 18:00–19:30 · Di 18:00–20:00 Duisburg · ekstra ilk iş günü +0:30
- 14 Büsra Uzunoglu: Di 14:00–17:00 Großenbaum
- 16 Mustafa Akdeniz: Mi 17:00–21:00 Moers
- 17 Adnan Islami: Mo,Mi,Fr 17:00–19:00 Duisburg
- 19 Nilüfer Katurman: Di 17:30–21:00 · Do 19:00–21:00 Duisburg Mitte · ekstra ilk iş günü +1:00
- 20 Marica Ivelj: Di 18:00–21:30 · Fr 16:30–21:30 Duisburg Mitte (kendi girmiş, Ekim doldurulmaz – zaten dolu)
- 23 Efsa Özen: Mi 17:00–18:30 · Fr 17:00–21:00 Wanheimerort
- 26 Emine Suciftci: Mo–Fr 18:00–19:30 Duisburg · ekstra ilk iş günü +0:30
- 4 Kemal Ayaz: Mo 17:00–18:00 Ruhrort · Di 17:00–18:45 Rheinhausen · Mi 17:00–18:00 Ruhrort · Do 17:00–18:45 Rheinhausen · Sa 15:00–18:30 Rheinhausen (iki cumartesi işi tek satırda birleşik)
- 5 Arzu Ayaz: Mo 17:00–18:30 Ruhrort · Di 17:00–18:45 Rheinhausen · Mi 17:00–18:30 Ruhrort · Do 17:00–18:45 Rheinhausen · Sa 15:00–17:15 Rheinhausen

Kemal+Arzu: karı koca, yeni Ruhrort işini (Mo/Mi) paylaştılar – Kemal 1:00, Arzu 1:30. İkisinin de aylık 40 saat sınırına dikkat (Aralık/Temmuz gibi 5 haftalı aylarda 0,25–0,5 Extra kaçınılmaz, toplam iş zaten 80 saati geçiyor). Defne Öztürk (id 24) sonra ayarlanacak – şimdilik atlandı. Fikret Chasan ve 5 kişi (Özdes dahil?) arşivlendi.

Ekim dolunca beklenen toplam ≈ 543 saat (kullanıcıya tablo verildi). Uygulama sonrası her işçi için script bir satır çıktı verir; kullanıcıya özetle.

## AÇIK İŞ 2 — WhatsApp aylık mesaj (bekliyor, "yap" alınmadı)
- Aylık kontrol listesi mesajı: üstte TR, altta DE, "Ja"/otomatik işleme yok, "doğruysa bir şey yapma". Metin önerisi hazırlandı, kullanıcı onayı bekliyor.
- Cevap filtresi: Stundenzettel "kaç saatim var?" gibi soruları cevap sanıyor. Düzeltme: sadece Ja/Evet VEYA soru kelimesi içermeyen tarih/gün cevap sayılsın. `stundenzettel_auto.py` `_sieht_aus_wie_antwort`. Kullanıcı onayı bekliyor.

## AÇIK İŞ 3 — Sunucu ayarları
- `WA_KI_WARTEN_MIN` sunucuda hâlâ **1** (test için). Otomatik cevapları AN yapmadan önce 10'a geri al: script `scratchpad/kontrol/server/zeit10.py` (bu scratchpad yeni oturumda yok – gerekiyorsa `geheim/crm_env.json` içinde `WA_KI_WARTEN_MIN=10` yapıp restart).
- Otomatik cevaplar şu an KAPALI (`connector_enabled` AUS). Açmak kullanıcının kararı.
- `calendar_key.json` sunucuda eksik (kullanıcı yükleyecek, secret).

## AÇIK İŞ 4 — İsteğe bağlı
- İşçi sayfasına açık "Geldim / Çıktım" (check-in/out) butonu + o anki saat/konum kaydı: işçi bilgilendirilip açık rıza ile, basmalı. Yasal ve şeffaf yol. Kullanıcı ilgileniyor; tasarım onayı bekliyor.
- Vollzeit işçiler için ayrı Lohn hesabı (40h/600€ capsiz) – ileride, Vollzeit işçi olunca.
- Leon: test araması, sonra gerçek kampanya.

## Yerel test düzeni (scratchpad – yeni oturumda yok, gerekirse yeniden kur)
- `scratchpad/crm` = repo klonu. `restart_crm.sh` test CRM'i port 5066'da açar (testui DB, admin/test). İşçi kodları: abc123 (id1), code0 (id2) ...
- Feste Zeiten üretim scripti: `scratchpad/kontrol/server/feste_zeiten.py` (repoya `migration/hetzner/feste_zeiten_einmalig.py` olarak kopyalandı).

## Hesaplama kuralı (üç yerde aynı olmalı)
15 €/saat, Grundreinigung 17 €/saat (sadece işçi sayfasında; liste/rapor 15€). 40 saat sınırı, 600 € cap. Sıra: Krank → Urlaub → Arbeit, kalan Extra. Feiertag iş gününe denk gelirse (işçi Feiertag'da çalışmıyorsa) o günün saatiyle "Feiertag" yazılır, ücrete sayılır.
