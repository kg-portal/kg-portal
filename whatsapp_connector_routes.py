from flask import request, jsonify
import os
import sqlite3

from openai_client import whatsapp_worker_auto_reply

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "kg_portal.db")

WORKER_WHATSAPP_FILE = os.path.join(BASE_DIR, "data", "worker_whatsapp_numbers.txt")

WHATSAPP_CONNECTOR_ENABLED = True

def wa_clean_id(value):
    phone = "".join(ch for ch in str(value or "") if ch.isdigit())

    if phone.startswith("0049"):
        phone = phone[4:]
    elif phone.startswith("49"):
        phone = phone[2:]
    elif phone.startswith("0"):
        phone = phone[1:]

    return phone

def wa_save_worker_id(value):
    worker_id = wa_clean_id(value)

    if not worker_id:
        return

    os.makedirs(os.path.dirname(WORKER_WHATSAPP_FILE), exist_ok=True)

    existing = set()

    if os.path.exists(WORKER_WHATSAPP_FILE):
        with open(WORKER_WHATSAPP_FILE, "r", encoding="utf-8") as f:
            existing = set(wa_clean_id(line.strip()) for line in f if line.strip())

    if worker_id not in existing:
        with open(WORKER_WHATSAPP_FILE, "a+", encoding="utf-8") as f:
            f.seek(0)
            content = f.read()

            if content and not content.endswith("\n"):
                f.write("\n")

            f.write(worker_id + "\n")


def wa_is_known_worker(phone, raw_from=""):
    values = set()

    phone_clean = wa_clean_id(phone)
    raw_clean = wa_clean_id(raw_from)

    if phone_clean:
        values.add(phone_clean)

    if raw_clean:
        values.add(raw_clean)

    if not os.path.exists(WORKER_WHATSAPP_FILE):
        return False

    with open(WORKER_WHATSAPP_FILE, "r", encoding="utf-8") as f:
        allowed = set(wa_clean_id(line.strip()) for line in f if line.strip())

    return bool(values & allowed)

def wa_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def wa_token_ok():
    expected = os.getenv("CRM_CONNECTOR_TOKEN", "CHANGE_ME_KG_TOKEN").strip()
    given = request.headers.get("X-KG-WA-TOKEN", "").strip()
    return bool(expected) and given == expected

def wa_ensure_tables():
    conn = wa_conn()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS whatsapp_inbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            wa_message_id TEXT UNIQUE,
            phone TEXT,
            name TEXT,
            body TEXT,
            msg_type TEXT,
            raw_from TEXT,
            wa_timestamp TEXT,
            status TEXT DEFAULT 'neu',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS whatsapp_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT NOT NULL,
            text TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            source TEXT DEFAULT 'auto',
            error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            sent_at TEXT
        )
    ''')

    conn.execute('''
        CREATE TABLE IF NOT EXISTS whatsapp_ai_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    conn.commit()
    conn.close()

def wa_normalize_text(value):
    text = str(value or "").strip().lower()

    replace_map = {
        "ı": "i",
        "ğ": "g",
        "ü": "u",
        "ş": "s",
        "ö": "o",
        "ç": "c",
        "ä": "a",
        "ß": "ss"
    }

    for old, new in replace_map.items():
        text = text.replace(old, new)

    return " ".join(text.split())


def wa_is_worker_hours_question(body):
    text = wa_normalize_text(body)

    keywords = [
        "saat",
        "saatim",
        "stunden",
        "stundenzettel",
        "urlaub",
        "izin",
        "resturlaub",
        "kalan izin",
        "kac saat",
        "kaç saat",
        "ne kadar saat",
        "ne kadar urlaub"
    ]

    return any(k in text for k in keywords)


def wa_find_worker_by_phone(phone, raw_from=""):
    phone_clean = wa_clean_id(phone)
    raw_clean = wa_clean_id(raw_from)

    conn = wa_conn()

    rows = conn.execute("""
        SELECT id, vorname, nachname, telefon, resturlaub
        FROM mitarbeiter
        WHERE COALESCE(status, 'aktiv') = 'aktiv'
    """).fetchall()

    conn.close()

    for row in rows:
        worker_phone = wa_clean_id(row["telefon"])

        if worker_phone and phone_clean and worker_phone == phone_clean:
            return row

        if worker_phone and raw_clean and worker_phone == raw_clean:
            return row

    return None


def wa_find_worker_by_name(body):
    text = wa_normalize_text(body)

    conn = wa_conn()

    rows = conn.execute("""
        SELECT id, vorname, nachname, telefon, resturlaub
        FROM mitarbeiter
        WHERE COALESCE(status, 'aktiv') = 'aktiv'
    """).fetchall()

    conn.close()

    for row in rows:
        full_name = f"{row['vorname'] or ''} {row['nachname'] or ''}".strip()
        full_name_norm = wa_normalize_text(full_name)

        if full_name_norm and full_name_norm in text:
            return row

    return None


def wa_month_range(year, month):
    year = int(year)
    month = int(month)

    start_date = f"{year}-{month:02d}-01"

    if month == 12:
        end_date = f"{year + 1}-01-01"
    else:
        end_date = f"{year}-{month + 1:02d}-01"

    return year, month, start_date, end_date


def wa_current_month_range():
    from datetime import datetime

    now = datetime.now()
    return wa_month_range(now.year, now.month)


def wa_detect_requested_month(body):
    from datetime import datetime

    text = wa_normalize_text(body)
    now = datetime.now()

    month_words = {
        "januar": 1,
        "ocak": 1,

        "februar": 2,
        "subat": 2,
        "şubat": 2,

        "marz": 3,
        "maerz": 3,
        "märz": 3,
        "mart": 3,

        "april": 4,
        "nisan": 4,

        "mai": 5,
        "mayis": 5,
        "mayıs": 5,

        "juni": 6,
        "haziran": 6,

        "juli": 7,
        "temmuz": 7,

        "august": 8,
        "agustos": 8,
        "ağustos": 8,

        "september": 9,
        "eylul": 9,
        "eylül": 9,

        "oktober": 10,
        "ekim": 10,

        "november": 11,
        "kasim": 11,
        "kasım": 11,

        "dezember": 12,
        "aralik": 12,
        "aralık": 12
    }

    if "gecen ay" in text or "geçen ay" in text or "letzten monat" in text or "letzter monat" in text:
        year = now.year
        month = now.month - 1

        if month == 0:
            month = 12
            year -= 1

        return year, month

    for word, month in month_words.items():
        if word in text:
            year = now.year

            # Şu anki aydan daha ileride bir ay sorulursa genelde geçen yıl değildir;
            # aynı yıl kabul ediyoruz.
            return year, month

    return now.year, now.month


def wa_worker_month_summary(worker_id, year=None, month=None):
    if year and month:
        year, month, start_date, end_date = wa_month_range(year, month)
    else:
        year, month, start_date, end_date = wa_current_month_range()

    conn = wa_conn()

    logs = conn.execute("""
        SELECT datum, start_time, end_time, place, signed
        FROM work_logs
        WHERE worker_id = ?
          AND datum >= ?
          AND datum < ?
        ORDER BY datum ASC
    """, (worker_id, start_date, end_date)).fetchall()

    conn.close()

    total_hours = 0.0
    urlaub_days = 0
    krank_days = 0
    missing_signature_days = 0

    for log in logs:
        place = str(log["place"] or "").strip()
        start_time = str(log["start_time"] or "").strip()
        end_time = str(log["end_time"] or "").strip()

        if place.lower() == "urlaub":
            urlaub_days += 1

        if place.lower() == "krank":
            krank_days += 1

        if int(log["signed"] or 0) != 1:
            if start_time or end_time or place:
                missing_signature_days += 1

        if start_time and end_time:
            try:
                start_h, start_m = start_time.split(":")
                end_h, end_m = end_time.split(":")

                start_value = int(start_h) + int(start_m) / 60
                end_value = int(end_h) + int(end_m) / 60

                hours = end_value - start_value
                if hours < 0:
                    hours += 24

                total_hours += hours
            except Exception:
                pass

    return {
        "year": year,
        "month": month,
        "hours": round(total_hours, 2),
        "urlaub_days": urlaub_days,
        "krank_days": krank_days,
        "missing_signature_days": missing_signature_days
    }


def wa_worker_hours_reply(worker):
    summary = wa_worker_month_summary(worker["id"])
    full_name = f"{worker['vorname'] or ''} {worker['nachname'] or ''}".strip()

    return (
        f"{full_name} için {summary['month']:02d}/{summary['year']} özeti:\n\n"
        f"- Çalışma saati: {summary['hours']} saat\n"
        f"- Urlaub: {summary['urlaub_days']} gün\n"
        f"- Krank: {summary['krank_days']} gün\n"
        f"- Kalan Urlaub: {worker['resturlaub']} gün\n"
        f"- İmzasız kayıt: {summary['missing_signature_days']} gün"
    )


def wa_worker_identity_request_reply():
    return (
        "Saat / Urlaub bilginizi gönderebilmem için lütfen isim soyisim yazar mısınız?\n\n"
        "Örnek: Damla Kicci"
    )

def wa_build_ai_context(phone="", raw_from="", body="", name=""):
    year, month = wa_detect_requested_month(body)
    _, _, start_date, end_date = wa_month_range(year, month)

    phone_clean = wa_clean_id(phone)
    raw_clean = wa_clean_id(raw_from)

    conn = wa_conn()

    workers = conn.execute("""
        SELECT id, vorname, nachname, telefon, eintrittsdatum, urlaub, resturlaub
        FROM mitarbeiter
        WHERE COALESCE(status, 'aktiv') = 'aktiv'
        ORDER BY sort_order ASC, id ASC
    """).fetchall()

    normalized_name = wa_normalize_text(name)
    matched_workers = []

    # 1. Önce WhatsApp LID / phone ile eşleştir
    for worker in workers:
        worker_phone = wa_clean_id(worker["telefon"])

        if worker_phone and phone_clean and worker_phone == phone_clean:
            matched_workers = [worker]
            break

        if worker_phone and raw_clean and worker_phone == raw_clean:
            matched_workers = [worker]
            break

    # 2. Numaradan bulunamadıysa isimden dene
    if not matched_workers and normalized_name:
        for worker in workers:
            full_name = f"{worker['vorname'] or ''} {worker['nachname'] or ''}".strip()
            normalized_full_name = wa_normalize_text(full_name)

            if (
                normalized_name == normalized_full_name
                or normalized_name in normalized_full_name
                or normalized_full_name in normalized_name
            ):
                matched_workers.append(worker)

    context_workers = matched_workers

    candidates = []

    for value in [phone, raw_from, phone_clean, raw_clean]:
        value = str(value or "").strip()
        if value and value not in candidates:
            candidates.append(value)

    last_inbox = []
    last_outbox = []

    if candidates:
        placeholders = ",".join(["?"] * len(candidates))

        last_inbox = conn.execute(f"""
            SELECT created_at, body
            FROM whatsapp_inbox
            WHERE phone IN ({placeholders})
               OR raw_from IN ({placeholders})
            ORDER BY id DESC
            LIMIT 6
        """, candidates + candidates).fetchall()

        last_outbox = conn.execute(f"""
            SELECT created_at, text
            FROM whatsapp_outbox
            WHERE phone IN ({placeholders})
            ORDER BY id DESC
            LIMIT 6
        """, candidates).fetchall()

    conn.close()

    lines = []
    lines.append(f"Sorulan / kontrol edilen ay: {month:02d}/{year}")
    lines.append(f"Gelen WhatsApp phone: {phone}")
    lines.append(f"Gelen WhatsApp raw_from: {raw_from}")
    lines.append(f"Yeni gelen mesaj: {body}")
    lines.append("")

    active_job_context = wa_get_active_job_context()
    if active_job_context:
        lines.append("AKTİF İŞ İLANI BİLGİSİ:")
        lines.append(active_job_context)
        lines.append("")

    lines.append("EŞLEŞEN MITARBEITER BİLGİSİ:")

    if not context_workers:
        lines.append("Eşleşen çalışan bulunamadı. Başka çalışanların bilgileri kesinlikle kullanılmamalıdır.")

    for worker in context_workers:

        summary = wa_worker_month_summary(worker["id"], year, month)
        full_name = f"{worker['vorname'] or ''} {worker['nachname'] or ''}".strip()

        lines.append("")
        lines.append(f"İsim: {full_name}")
        lines.append(f"Telefon CRM: {worker['telefon']}")
        lines.append(f"Eintrittsdatum: {worker['eintrittsdatum']}")
        lines.append(f"Urlaub Gesamt: {worker['urlaub']}")
        lines.append(f"Resturlaub: {worker['resturlaub']}")
        lines.append(f"{month:02d}/{year} çalışma saati: {summary['hours']} saat")
        lines.append(f"{month:02d}/{year} Urlaub günü: {summary['urlaub_days']}")
        lines.append(f"{month:02d}/{year} Krank günü: {summary['krank_days']}")
        lines.append(f"{month:02d}/{year} imzasız kayıt: {summary['missing_signature_days']}")

    lines.append("")
    lines.append("SON GELEN MESAJLAR:")
    for row in reversed(last_inbox):
        lines.append(f"- İşçi: {row['body']}")

    lines.append("")
    lines.append("SON GİDEN CEVAPLAR:")
    for row in reversed(last_outbox):
        lines.append(f"- KG AI: {row['text']}")

    return "\n".join(lines)

def wa_auto_reply_text(name="", body=""):
    return (
        "Mesajınız alındı. "
        "Frau Kicci’ye iletilecek."
    )


def wa_self_chat_ids():
    raw = os.getenv(
        "KG_AI_SELF_CHAT_IDS",
        "274487199191086,491631947055"
    )

    return set(
        wa_clean_id(x)
        for x in str(raw or "").split(",")
        if wa_clean_id(x)
    )


def wa_is_ai_self_window(from_me, raw_from="", to_id="", phone=""):
    if not from_me:
        return False

    ids = wa_self_chat_ids()

    raw_clean = wa_clean_id(raw_from)
    to_clean = wa_clean_id(to_id)
    phone_clean = wa_clean_id(phone)

    # Damla kendi kendine yazınca genelde:
    # from = Damla normal numara
    # to   = Damla LID
    # İkisi de bizim self id listesinde olmalı.
    if raw_clean in ids and to_clean in ids:
        return True

    # Bazı payloadlarda phone = hedef chat olabilir.
    if raw_clean in ids and phone_clean in ids:
        return True

    return False


def wa_save_active_job_context(text):
    text = str(text or "").strip()

    if not text:
        return

    conn = wa_conn()
    conn.execute('''
        INSERT INTO whatsapp_ai_state (key, value, updated_at)
        VALUES ('active_job_context', ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
    ''', (text,))
    conn.commit()
    conn.close()


def wa_get_active_job_context():
    conn = wa_conn()
    row = conn.execute("""
        SELECT value, updated_at
        FROM whatsapp_ai_state
        WHERE key = 'active_job_context'
    """).fetchone()
    conn.close()

    if not row:
        return ""

    return str(row["value"] or "").strip()


def register_whatsapp_connector_routes(app, login_required):
    wa_ensure_tables()

    @app.route("/whatsapp/api/connector-status", methods=["GET"])
    @login_required
    def whatsapp_connector_status():
        return jsonify({
            "ok": True,
            "enabled": WHATSAPP_CONNECTOR_ENABLED
        })

    @app.route("/whatsapp/api/connector-toggle", methods=["POST"])
    @login_required
    def whatsapp_connector_toggle():
        global WHATSAPP_CONNECTOR_ENABLED

        WHATSAPP_CONNECTOR_ENABLED = not WHATSAPP_CONNECTOR_ENABLED

        return jsonify({
            "ok": True,
            "enabled": WHATSAPP_CONNECTOR_ENABLED
        })

    @app.route("/api/whatsapp-connector/incoming", methods=["POST"])
    def whatsapp_connector_incoming():
        if not wa_token_ok():
            return jsonify({"ok": False, "message": "Unauthorized"}), 403

        wa_ensure_tables()
        data = request.get_json(silent=True) or {}

        wa_message_id = str(data.get("wa_message_id") or "").strip()
        phone = str(data.get("phone") or "").strip()
        name = str(data.get("name") or "").strip()
        body = str(data.get("body") or "").strip()
        msg_type = str(data.get("type") or "").strip()
        raw_from = str(data.get("from") or "").strip()
        to_id = str(data.get("to") or data.get("to_id") or "").strip()
        wa_timestamp = str(data.get("timestamp") or "").strip()

        from_me = (
            data.get("fromMe") is True
            or data.get("from_me") is True
            or data.get("isFromMe") is True
            or data.get("is_from_me") is True
            or str(data.get("fromMe") or "").lower() == "true"
            or str(data.get("from_me") or "").lower() == "true"
            or str(data.get("isFromMe") or "").lower() == "true"
            or str(data.get("is_from_me") or "").lower() == "true"
        )

        # Damla kendi WhatsApp sayfasına yazarsa burası KG AI ana pencere gibi çalışır.
        # Bu pencereye yazılan her mesaj aktif iş ilanı bilgisi olarak kaydedilir.
        if WHATSAPP_CONNECTOR_ENABLED and from_me and wa_is_ai_self_window(from_me, raw_from=raw_from, to_id=to_id, phone=phone):
            if body:
                wa_save_active_job_context(body)

                reply_target = to_id if to_id else raw_from

                conn = wa_conn()
                conn.execute('''
                    INSERT INTO whatsapp_outbox (phone, text, status, source)
                    VALUES (?, ?, 'pending', 'ai_self_window')
                ''', (
                    reply_target,
                    "Aktif iş ilanı bilgisi kaydedildi. Bundan sonra iş için yazanlara bu bilgiyi kullanacağım."
                ))
                conn.commit()
                conn.close()

                return jsonify({
                    "ok": True,
                    "handled": True,
                    "reason": "ai_self_window_active_job_saved"
                })

            return jsonify({
                "ok": True,
                "skipped": True,
                "reason": "ai_self_window_empty"
            })

        # Damla'nın başkasına gönderdiği normal mesajlara AI kesinlikle cevap vermesin.
        if from_me:
            return jsonify({
                "ok": True,
                "skipped": True,
                "reason": "from_me_message"
            })

        # WhatsApp status / broadcast / grup / boş sistem eventlerine AI karışmasın.
        if raw_from == "status@broadcast" or phone == "status@broadcast":
            return jsonify({
                "ok": True,
                "skipped": True,
                "reason": "status_broadcast"
            })

        if "@g.us" in raw_from or "@g.us" in phone:
            return jsonify({
                "ok": True,
                "skipped": True,
                "reason": "group_message"
            })

        is_known_worker = wa_is_known_worker(phone, raw_from)
        if is_known_worker:
            wa_save_worker_id(raw_from)

        if not phone:
            return jsonify({"ok": False, "message": "phone fehlt"}), 400

        # Sesli mesaj / boş içerik gelirse AI çözmeye çalışmasın.
        # Kayıtlı işçiyse yazılı mesaj istemek için kısa cevap kuyruğa ekle.
        if not body:
            if WHATSAPP_CONNECTOR_ENABLED and is_known_worker:
                reply_target = phone

                conn = wa_conn()
                conn.execute('''
                    INSERT INTO whatsapp_outbox (phone, text, status, source)
                    VALUES (?, ?, 'pending', 'voice_request_text')
                ''', (
                    reply_target,
                    "Sesli mesajları şu an otomatik okuyamıyorum. Lütfen mesajınızı kısa şekilde yazılı olarak gönderir misiniz?"
                ))
                conn.commit()
                conn.close()

            return jsonify({
                "ok": True,
                "skipped": True,
                "reason": "empty_or_voice_message"
            })

        conn = wa_conn()
        conn.execute('''
            INSERT OR IGNORE INTO whatsapp_inbox
                (wa_message_id, phone, name, body, msg_type, raw_from, wa_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (wa_message_id, phone, name, body, msg_type, raw_from, wa_timestamp))

        if not WHATSAPP_CONNECTOR_ENABLED:
            conn.commit()
            conn.close()
            return jsonify({
                "ok": True,
                "stored": True,
                "skipped": True,
                "reason": "connector_disabled"
            })

        reply_target = phone

        if is_known_worker:
            try:
                ai_context = wa_build_ai_context(
                    phone=phone,
                    raw_from=raw_from,
                    body=body,
                    name=name
                )

                reply_text = whatsapp_worker_auto_reply(
                    name,
                    body,
                    ai_context
                ).get("answer")

            except Exception as e:
                print("AI WhatsApp cevap hatası:", str(e))
                reply_text = (
                    "KG-AI Yapay Zeka Asistanı: "
                    "Mesajınız alındı. Frau Kicci’ye iletilecek."
                )

            conn.execute('''
                INSERT INTO whatsapp_outbox (phone, text, status, source)
                VALUES (?, ?, 'pending', 'ai_auto_reply')
            ''', (reply_target, reply_text))

        else:
            already_greeted = conn.execute('''
                SELECT COUNT(*)
                FROM whatsapp_outbox
                WHERE phone = ?
                  AND source = 'unknown_number_greeting'
            ''', (reply_target,)).fetchone()[0]

            if int(already_greeted or 0) == 0:
                conn.execute('''
                    INSERT INTO whatsapp_outbox (phone, text, status, source)
                    VALUES (?, ?, 'pending', 'unknown_number_greeting')
                ''', (
                    reply_target,
                    "KG-AI Yapay Zeka Asistanı: Merhaba, ben Damla Hanım’ın "
                    "yapay zeka asistanı KG-AI. Lütfen ne istediğinizi bana "
                    "söyleyiniz, ben Damla Hanım’a ileteceğim."
                ))

        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @app.route("/api/whatsapp-connector/learn-id", methods=["POST"])
    def whatsapp_connector_learn_id():
        if not wa_token_ok():
            return jsonify({"ok": False, "message": "Unauthorized"}), 403

        data = request.get_json(silent=True) or {}
        raw_from = str(data.get("raw_from") or "").strip()

        if not raw_from:
            return jsonify({
                "ok": False,
                "message": "raw_from fehlt"
            }), 400

        wa_save_worker_id(raw_from)

        return jsonify({
            "ok": True,
            "saved": wa_clean_id(raw_from)
        })


    @app.route("/api/whatsapp-connector/outbox", methods=["GET"])
    def whatsapp_connector_outbox():

        if not WHATSAPP_CONNECTOR_ENABLED:
            return jsonify({
                "ok": True,
                "items": [],
                "disabled": True
            })
        if not wa_token_ok():
            return jsonify({"ok": False, "message": "Unauthorized"}), 403

        wa_ensure_tables()
        try:
            limit = int(request.args.get("limit", "10"))
        except Exception:
            limit = 10

        conn = wa_conn()
        rows = conn.execute('''
            SELECT id, phone, text
            FROM whatsapp_outbox
            WHERE status = 'pending'
            ORDER BY id ASC
            LIMIT ?
        ''', (limit,)).fetchall()
        conn.close()
        return jsonify({"ok": True, "items": [dict(r) for r in rows]})

    @app.route("/api/whatsapp-connector/outbox/<int:item_id>/sent", methods=["POST"])
    def whatsapp_connector_outbox_sent(item_id):
        if not wa_token_ok():
            return jsonify({"ok": False, "message": "Unauthorized"}), 403
        conn = wa_conn()
        conn.execute("UPDATE whatsapp_outbox SET status='sent', sent_at=CURRENT_TIMESTAMP, error=NULL WHERE id=?", (item_id,))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @app.route("/api/whatsapp-connector/outbox/<int:item_id>/error", methods=["POST"])
    def whatsapp_connector_outbox_error(item_id):
        if not wa_token_ok():
            return jsonify({"ok": False, "message": "Unauthorized"}), 403
        data = request.get_json(silent=True) or {}
        conn = wa_conn()
        conn.execute("UPDATE whatsapp_outbox SET status='error', error=? WHERE id=?", (str(data.get("error") or ""), item_id))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})

    @app.route("/api/whatsapp-connector/messages", methods=["GET"])
    @login_required
    def whatsapp_connector_messages():
        wa_ensure_tables()
        conn = wa_conn()
        inbox = conn.execute("SELECT * FROM whatsapp_inbox ORDER BY id DESC LIMIT 100").fetchall()
        outbox = conn.execute("SELECT * FROM whatsapp_outbox ORDER BY id DESC LIMIT 100").fetchall()
        conn.close()
        return jsonify({"ok": True, "inbox": [dict(r) for r in inbox], "outbox": [dict(r) for r in outbox]})
