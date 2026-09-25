# -*- coding: utf-8 -*-

from flask import request, jsonify, render_template
from openai_client import ai_test, analyze_worker_message, kg_ai_chat


def register_kg_ai_routes(app, login_required, get_db_connection, normalize_phone_for_whatsapp):

    # =====================================================
    # KG AI - STUNDENZETTEL OKUMA / SAAT + RESTURLAUB
    # =====================================================

    def ai_read_stundenzettel_summary(month, year):
        month = int(month)
        year = int(year)

        start_date = f"{year}-{month:02d}-01"

        if month == 12:
            end_date = f"{year + 1}-01-01"
        else:
            end_date = f"{year}-{month + 1:02d}-01"

        conn = get_db_connection()

        workers = conn.execute("""
            SELECT id, vorname, nachname, urlaub, resturlaub
            FROM mitarbeiter
            WHERE COALESCE(status, 'aktiv') = 'aktiv'
            ORDER BY sort_order ASC, id ASC
        """).fetchall()

        result = []

        for worker in workers:
            logs = conn.execute("""
                SELECT datum, start_time, end_time, place, signed
                FROM work_logs
                WHERE worker_id = ?
                  AND datum >= ?
                  AND datum < ?
                ORDER BY datum ASC
            """, (worker["id"], start_date, end_date)).fetchall()

            total_hours = 0.0
            urlaub_days = 0
            krank_days = 0
            signed_days = 0
            missing_signature_days = 0

            for log in logs:
                place = str(log["place"] or "").strip()
                start_time = str(log["start_time"] or "").strip()
                end_time = str(log["end_time"] or "").strip()

                if place.lower() == "urlaub":
                    urlaub_days += 1

                if place.lower() == "krank":
                    krank_days += 1

                if int(log["signed"] or 0) == 1:
                    signed_days += 1
                else:
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

            full_name = f"{worker['vorname'] or ''} {worker['nachname'] or ''}".strip()

            result.append({
                "id": worker["id"],
                "name": full_name,
                "hours": round(total_hours, 2),
                "urlaub_days": urlaub_days,
                "krank_days": krank_days,
                "signed_days": signed_days,
                "missing_signature_days": missing_signature_days,
                "urlaub_total": worker["urlaub"],
                "resturlaub": worker["resturlaub"]
            })

        conn.close()

        return result


    def format_stundenzettel_summary(month, year, summary):
        answer_lines = []
        answer_lines.append(f"{month:02d}/{year} Stundenzettel Özeti")
        answer_lines.append("")

        for item in summary:
            answer_lines.append(f"{item['name']}")
            answer_lines.append(f"- Çalışma saati: {item['hours']} saat")
            answer_lines.append(f"- Urlaub: {item['urlaub_days']} gün")
            answer_lines.append(f"- Krank: {item['krank_days']} gün")
            answer_lines.append(f"- Kalan Urlaub: {item['resturlaub']} gün")
            answer_lines.append(f"- İmzasız kayıt: {item['missing_signature_days']} gün")
            answer_lines.append("")

        return "\n".join(answer_lines)


    # =====================================================
    # KG AI - TOPLU WHATSAPP DUYURU / ONAYLI
    # =====================================================

    def ai_get_active_workers_for_whatsapp():
        conn = get_db_connection()

        workers = conn.execute("""
            SELECT id, vorname, nachname, telefon
            FROM mitarbeiter
            WHERE COALESCE(status, 'aktiv') = 'aktiv'
              AND telefon IS NOT NULL
              AND telefon != ''
            ORDER BY sort_order ASC, id ASC
        """).fetchall()

        conn.close()

        result = []

        for worker in workers:
            full_name = f"{worker['vorname'] or ''} {worker['nachname'] or ''}".strip()
            phone = normalize_phone_for_whatsapp(worker["telefon"])

            if phone:
                result.append({
                    "id": worker["id"],
                    "name": full_name,
                    "phone": phone
                })

        return result


    def clean_bulk_whatsapp_message(message):
        text = str(message or "").strip()

        # Son satırda EVET / HAYIR yanlışlıkla mesaja karıştıysa temizle
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        if lines and lines[-1].lower() in ["evet", "hayır", "hayir", "nein", "iptal", "cancel"]:
            lines = lines[:-1]
            text = "\n".join(lines).strip()

        lower = text.lower()

        # =====================================================
        # 1) Komut baştaysa:
        # "Bütün işçilere WhatsApp mesajı gönder: Merhaba..."
        # =====================================================
        trigger_words = [
            "whatsapp mesajı gönder:",
            "whatsapp mesaji gonder:",
            "whatsapp mesaj gönder:",
            "whatsapp mesaj gonder:",
            "whatsapp mesajı yaz:",
            "whatsapp mesaji yaz:",
            "mesaj gönder:",
            "mesaj gonder:",
            "mesaj yaz:",
            "duyuru gönder:",
            "duyuru gonder:",
            "duyuru yaz:",
            "şu mesajı gönder:",
            "su mesaji gonder:",
            "şu mesajı yaz:",
            "su mesaji yaz:",
            "yaz:",
            "gönder:",
            "gonder:"
        ]

        for trigger in trigger_words:
            idx = lower.find(trigger)
            if idx != -1:
                text = text[idx + len(trigger):].strip()
                lower = text.lower()
                break

        # =====================================================
        # 2) Komut sondaysa:
        # "Merhaba... bunu w app tan bütün işçilere gönder"
        # Bu kısmı WhatsApp mesajından çıkarır.
        # =====================================================
        ending_commands = [
            "bunu w app tan bütün işçilere gönder",
            "bunu w app tan butun iscilere gonder",
            "bunu whatsapp tan bütün işçilere gönder",
            "bunu whatsapp tan butun iscilere gonder",
            "bunu whatsapp'tan bütün işçilere gönder",
            "bunu whatsapp'tan butun iscilere gonder",
            "bunu whatsappdan bütün işçilere gönder",
            "bunu whatsappdan butun iscilere gonder",
            "w app tan bütün işçilere gönder",
            "w app tan butun iscilere gonder",
            "whatsapp tan bütün işçilere gönder",
            "whatsapp tan butun iscilere gonder",
            "whatsapp'tan bütün işçilere gönder",
            "whatsapp'tan butun iscilere gonder",
            "whatsappdan bütün işçilere gönder",
            "whatsappdan butun iscilere gonder",
            "bütün işçilere gönder",
            "butun iscilere gonder",
            "tüm işçilere gönder",
            "tum iscilere gonder"
        ]

        lower = text.lower()

        for ending in ending_commands:
            idx = lower.rfind(ending)
            if idx != -1:
                text = text[:idx].strip()
                break

        # Sonda gereksiz nokta / tire / iki nokta kaldıysa temizle
        text = text.strip(" -:.;")

        return text


    def ai_prepare_bulk_whatsapp_preview(message):
        message = clean_bulk_whatsapp_message(message)

        if not message:
            return {
                "ok": False,
                "model": "DB-PREVIEW",
                "answer": "Toplu WhatsApp mesajı boş olamaz."
            }

        workers = ai_get_active_workers_for_whatsapp()

        if not workers:
            return {
                "ok": False,
                "model": "DB-PREVIEW",
                "answer": "Telefon numarası olan aktif işçi bulunamadı."
            }

        answer_lines = []
        answer_lines.append("Toplu WhatsApp duyurusu hazırlanıyor.")
        answer_lines.append("")
        answer_lines.append("Gönderilecek aktif işçiler:")

        for worker in workers:
            answer_lines.append(f"- {worker['name']}")

        answer_lines.append("")
        answer_lines.append("Mesaj:")
        answer_lines.append(message)
        answer_lines.append("")
        answer_lines.append("Bu mesajı bütün aktif işçilere göndermek istediğinizden emin misiniz?")
        answer_lines.append("Göndermek için sadece EVET yaz.")

        return {
            "ok": True,
            "model": "DB-PREVIEW",
            "answer": "\n".join(answer_lines),
            "message": message,
            "workers": workers
        }


    def ai_send_bulk_whatsapp_to_workers(message, confirmation):
        message = clean_bulk_whatsapp_message(message)
        confirmation = str(confirmation or "").strip().upper()

        if confirmation != "EVET":
            return {
                "ok": False,
                "model": "DB-WHATSAPP-OUTBOX",
                "answer": "Toplu mesaj gönderilmedi. Göndermek için EVET onayı gerekir."
            }

        if not message:
            return {
                "ok": False,
                "model": "DB-WHATSAPP-OUTBOX",
                "answer": "Toplu WhatsApp mesajı boş olamaz."
            }

        workers = ai_get_active_workers_for_whatsapp()

        if not workers:
            return {
                "ok": False,
                "model": "DB-WHATSAPP-OUTBOX",
                "answer": "Telefon numarası olan aktif işçi bulunamadı."
            }

        conn = get_db_connection()

        for worker in workers:
            conn.execute("""
                INSERT INTO whatsapp_outbox (phone, text, status, source)
                VALUES (?, ?, 'pending', 'kg_ai_bulk')
            """, (worker["phone"], message))

        conn.commit()
        conn.close()

        answer_lines = []
        answer_lines.append("Toplu WhatsApp mesajı gönderim kuyruğuna eklendi.")
        answer_lines.append("")
        answer_lines.append(f"Toplam kişi: {len(workers)}")
        answer_lines.append("")
        answer_lines.append("Gönderilecek kişiler:")

        for worker in workers:
            answer_lines.append(f"- {worker['name']}")

        answer_lines.append("")
        answer_lines.append("Gönderilen gerçek mesaj:")
        answer_lines.append(message)
        answer_lines.append("")
        answer_lines.append("WhatsApp connector açıksa mesajlar şimdi gönderilir.")

        return {
            "ok": True,
            "model": "DB-WHATSAPP-OUTBOX",
            "answer": "\n".join(answer_lines)
        }

    # =====================================================
    # KG AI - KUNDENPFLEGE / QUALITAETSKONTROLLE
    # =====================================================

    def ensure_kundenpflege_tables():
        conn = get_db_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kunden_quality_pref (
                kunde_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                interval_months INTEGER NOT NULL DEFAULT 3,
                unsubscribed_at TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS kunden_quality_mail (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kunde_id INTEGER NOT NULL,
                recipient TEXT NOT NULL,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                gmail_id TEXT,
                status TEXT NOT NULL DEFAULT 'sent',
                unsubscribe_token TEXT NOT NULL,
                sent_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_kunden_quality_token
            ON kunden_quality_mail(unsubscribe_token)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_kunden_quality_kunde_sent
            ON kunden_quality_mail(kunde_id, sent_at DESC)
        """)
        conn.commit()
        conn.close()

    def kundenpflege_new_token():
        import secrets
        return secrets.token_urlsafe(24)

    def kundenpflege_customer_row(row):
        return {
            "id": row["id"],
            "firma": row["firma"] or "",
            "anrede": row["anrede"] or "",
            "ansprechpartner_name": row["ansprechpartner_name"] or "",
            "email": row["email"] or "",
            "ort": row["ort"] or "",
            "vertragsstatus": row["vertragsstatus"] or "aktuell",
            "enabled": bool(row["quality_enabled"] if row["quality_enabled"] is not None else 1),
            "interval_months": int(row["interval_months"] or 3),
            "last_sent": row["last_sent"] or "",
            "unsubscribed_at": row["unsubscribed_at"] or ""
        }

    def kundenpflege_salutation(kunde):
        name = str(kunde.get("ansprechpartner_name") or "").strip()
        anrede = str(kunde.get("anrede") or "").strip()

        if name:
            if anrede.lower() == "herr":
                return f"Sehr geehrter Herr {name},"
            if anrede.lower() == "frau":
                return f"Sehr geehrte Frau {name},"
            return f"Guten Tag {name},"

        firma = str(kunde.get("firma") or "").strip()
        return f"Guten Tag{(' ' + firma) if firma else ''},"

    def kundenpflege_mail_text(kunde, unsubscribe_url):
        return """{salutation}

wir möchten regelmäßig sicherstellen, dass Sie mit unserer Reinigungsleistung zufrieden sind.

Dürfen wir Sie kurz um eine Rückmeldung bitten?

• Sind Sie mit unserer Reinigungsleistung insgesamt zufrieden?
• Gibt es etwas, das unsere Mitarbeiter anders oder besser machen sollen?
• Gibt es Bereiche, die künftig mehr Aufmerksamkeit benötigen?
• Sind Sie mit den eingesetzten Reinigungsmitteln und deren Geruch zufrieden?
• Haben Sie weitere Wünsche oder Hinweise für uns?

Eine kurze Antwort auf diese E-Mail genügt. Ihre Rückmeldung hilft uns, Probleme frühzeitig zu erkennen und unsere Leistung laufend zu verbessern.

Wenn Sie diese Qualitätsabfragen künftig nicht mehr erhalten möchten:
{unsubscribe_url}

Mit freundlichen Grüßen
Ihr Team von KG Gebäudereinigung
""".format(
            salutation=kundenpflege_salutation(kunde),
            unsubscribe_url=unsubscribe_url
        ).strip()

    @app.route("/api/ai/kundenpflege/kunden")
    @login_required
    def kg_ai_kundenpflege_kunden():
        ensure_kundenpflege_tables()
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT
                k.id,
                k.firma,
                k.anrede,
                k.ansprechpartner_name,
                k.email,
                k.ort,
                COALESCE(k.vertragsstatus, 'aktuell') AS vertragsstatus,
                p.enabled AS quality_enabled,
                p.interval_months,
                p.unsubscribed_at,
                (
                    SELECT qm.sent_at
                    FROM kunden_quality_mail qm
                    WHERE qm.kunde_id = k.id
                      AND qm.status = 'sent'
                    ORDER BY qm.sent_at DESC, qm.id DESC
                    LIMIT 1
                ) AS last_sent
            FROM kunden k
            LEFT JOIN kunden_quality_pref p ON p.kunde_id = k.id
            WHERE COALESCE(k.vertragsstatus, 'aktuell') != 'gekuendigt'
            ORDER BY k.firma COLLATE NOCASE ASC
        """).fetchall()
        conn.close()

        return jsonify({
            "ok": True,
            "kunden": [kundenpflege_customer_row(row) for row in rows]
        })

    @app.route("/api/ai/kundenpflege/preview", methods=["POST"])
    @login_required
    def kg_ai_kundenpflege_preview():
        ensure_kundenpflege_tables()
        data = request.get_json(silent=True) or {}
        raw_ids = data.get("kunde_ids") or []

        ids = []
        for item in raw_ids:
            try:
                value = int(item)
                if value > 0 and value not in ids:
                    ids.append(value)
            except Exception:
                pass

        if not ids:
            return jsonify({"ok": False, "message": "Keine Kunden ausgewählt."}), 400

        conn = get_db_connection()
        placeholders = ",".join(["?"] * len(ids))
        rows = conn.execute(f"""
            SELECT
                k.id,
                k.firma,
                k.anrede,
                k.ansprechpartner_name,
                k.email,
                k.ort,
                COALESCE(k.vertragsstatus, 'aktuell') AS vertragsstatus,
                p.enabled AS quality_enabled,
                p.interval_months,
                p.unsubscribed_at,
                NULL AS last_sent
            FROM kunden k
            LEFT JOIN kunden_quality_pref p ON p.kunde_id = k.id
            WHERE k.id IN ({placeholders})
            ORDER BY k.firma COLLATE NOCASE ASC
        """, ids).fetchall()

        result = []
        for row in rows:
            kunde = kundenpflege_customer_row(row)

            if kunde["vertragsstatus"] == "gekuendigt":
                continue
            if not kunde["email"]:
                continue
            if not kunde["enabled"] or kunde["unsubscribed_at"]:
                continue

            token = kundenpflege_new_token()
            unsubscribe_url = request.host_url.rstrip("/") + "/qualitaetsmail/abbestellen/" + token
            body = kundenpflege_mail_text(kunde, unsubscribe_url)

            result.append({
                **kunde,
                "subject": "Kurze Qualitätsabfrage zu unserer Reinigung",
                "body": body,
                "unsubscribe_token": token,
                "unsubscribe_url": unsubscribe_url
            })

        conn.close()

        if not result:
            return jsonify({
                "ok": False,
                "message": "Für die Auswahl gibt es keine versandfähigen Kunden."
            }), 400

        return jsonify({"ok": True, "mails": result})

    @app.route("/api/ai/kundenpflege/log", methods=["POST"])
    @login_required
    def kg_ai_kundenpflege_log():
        ensure_kundenpflege_tables()
        data = request.get_json(silent=True) or {}

        try:
            kunde_id = int(data.get("kunde_id") or 0)
        except Exception:
            kunde_id = 0

        recipient = str(data.get("recipient") or "").strip()
        subject = str(data.get("subject") or "").strip()
        body = str(data.get("body") or "").strip()
        gmail_id = str(data.get("gmail_id") or "").strip()
        token = str(data.get("unsubscribe_token") or "").strip()
        status = str(data.get("status") or "sent").strip()

        if kunde_id <= 0 or not recipient or not subject or not body or not token:
            return jsonify({"ok": False, "message": "Unvollständige Versanddaten."}), 400

        conn = get_db_connection()
        conn.execute("""
            INSERT INTO kunden_quality_mail
                (kunde_id, recipient, subject, body, gmail_id, status, unsubscribe_token, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        """, (kunde_id, recipient, subject, body, gmail_id, status, token))
        conn.execute("""
            INSERT INTO kunden_quality_pref (kunde_id, enabled, interval_months, updated_at)
            VALUES (?, 1, 3, datetime('now', 'localtime'))
            ON CONFLICT(kunde_id) DO UPDATE SET
                updated_at = datetime('now', 'localtime')
        """, (kunde_id,))
        conn.commit()
        conn.close()

        return jsonify({"ok": True})

    @app.route("/api/ai/kundenpflege/history")
    @login_required
    def kg_ai_kundenpflege_history():
        ensure_kundenpflege_tables()
        conn = get_db_connection()
        rows = conn.execute("""
            SELECT
                qm.id,
                qm.kunde_id,
                k.firma,
                qm.recipient,
                qm.subject,
                qm.gmail_id,
                qm.status,
                qm.sent_at,
                p.unsubscribed_at
            FROM kunden_quality_mail qm
            LEFT JOIN kunden k ON k.id = qm.kunde_id
            LEFT JOIN kunden_quality_pref p ON p.kunde_id = qm.kunde_id
            ORDER BY qm.sent_at DESC, qm.id DESC
            LIMIT 200
        """).fetchall()
        conn.close()

        return jsonify({
            "ok": True,
            "history": [dict(row) for row in rows]
        })

    @app.route("/api/ai/kundenpflege/preference", methods=["POST"])
    @login_required
    def kg_ai_kundenpflege_preference():
        ensure_kundenpflege_tables()
        data = request.get_json(silent=True) or {}

        try:
            kunde_id = int(data.get("kunde_id") or 0)
            interval_months = int(data.get("interval_months") or 3)
        except Exception:
            return jsonify({"ok": False, "message": "Ungültige Kundendaten."}), 400

        enabled = 1 if bool(data.get("enabled", True)) else 0
        interval_months = max(1, min(interval_months, 24))

        conn = get_db_connection()
        conn.execute("""
            INSERT INTO kunden_quality_pref
                (kunde_id, enabled, interval_months, unsubscribed_at, updated_at)
            VALUES (?, ?, ?, CASE WHEN ? = 1 THEN NULL ELSE datetime('now', 'localtime') END, datetime('now', 'localtime'))
            ON CONFLICT(kunde_id) DO UPDATE SET
                enabled = excluded.enabled,
                interval_months = excluded.interval_months,
                unsubscribed_at = CASE WHEN excluded.enabled = 1 THEN NULL ELSE COALESCE(kunden_quality_pref.unsubscribed_at, datetime('now', 'localtime')) END,
                updated_at = datetime('now', 'localtime')
        """, (kunde_id, enabled, interval_months, enabled))
        conn.commit()
        conn.close()

        return jsonify({"ok": True})

    @app.route("/qualitaetsmail/abbestellen/<token>")
    def kg_ai_kundenpflege_unsubscribe(token):
        ensure_kundenpflege_tables()
        token = str(token or "").strip()

        conn = get_db_connection()
        row = conn.execute("""
            SELECT kunde_id
            FROM kunden_quality_mail
            WHERE unsubscribe_token = ?
            ORDER BY id DESC
            LIMIT 1
        """, (token,)).fetchone()

        if not row:
            conn.close()
            return "<h2>Dieser Abmelde-Link ist ungültig oder nicht mehr verfügbar.</h2>", 404

        kunde_id = int(row["kunde_id"])
        conn.execute("""
            INSERT INTO kunden_quality_pref
                (kunde_id, enabled, interval_months, unsubscribed_at, updated_at)
            VALUES (?, 0, 3, datetime('now', 'localtime'), datetime('now', 'localtime'))
            ON CONFLICT(kunde_id) DO UPDATE SET
                enabled = 0,
                unsubscribed_at = datetime('now', 'localtime'),
                updated_at = datetime('now', 'localtime')
        """, (kunde_id,))
        conn.commit()
        conn.close()

        return """
        <!doctype html>
        <html lang="de">
        <head><meta charset="utf-8"><title>Abmeldung bestätigt</title></head>
        <body style="font-family:Arial,sans-serif;background:#f8fafc;padding:40px;color:#0f172a;">
          <div style="max-width:620px;margin:auto;background:#fff;border:1px solid #e2e8f0;border-radius:18px;padding:32px;">
            <h2 style="margin-top:0;">Abmeldung bestätigt</h2>
            <p>Sie erhalten künftig keine regelmäßigen Qualitätsabfragen mehr.</p>
            <p>Vielen Dank.</p>
            <p><strong>KG Gebäudereinigung</strong></p>
          </div>
        </body>
        </html>
        """

    # =====================================================
    # KG AI ANA API
    # =====================================================

    @app.route("/api/ai", methods=["POST"])
    @login_required
    def kg_ai_api():
        try:
            data = request.get_json(silent=True) or {}
            action = str(data.get("action") or "").strip()

            if action == "test":
                result = ai_test()

            elif action == "worker_message":
                message = data.get("message", "")
                result = analyze_worker_message(message)

            elif action == "chat":
                message = data.get("message", "")
                result = kg_ai_chat(message)

            elif action == "stundenzettel_summary":
                month = int(data.get("month"))
                year = int(data.get("year"))

                summary = ai_read_stundenzettel_summary(month, year)

                result = {
                    "model": "DB-READ",
                    "answer": format_stundenzettel_summary(month, year, summary)
                }

            elif action == "bulk_whatsapp_preview":
                message = data.get("message", "")
                result = ai_prepare_bulk_whatsapp_preview(message)

            elif action == "bulk_whatsapp_send":
                message = data.get("message", "")
                confirmation = data.get("confirmation", "")
                result = ai_send_bulk_whatsapp_to_workers(message, confirmation)

            else:
                return jsonify({
                    "ok": False,
                    "error": "Geçersiz action",
                    "allowed_actions": [
                        "test",
                        "worker_message",
                        "chat",
                        "stundenzettel_summary",
                        "bulk_whatsapp_preview",
                        "bulk_whatsapp_send"
                    ]
                }), 400

            return jsonify({
                "ok": True,
                "action": action,
                "model": result.get("model"),
                "answer": result.get("answer")
            })

        except Exception as e:
            return jsonify({
                "ok": False,
                "error": str(e)
            }), 500


    # =====================================================
    # KG AI - STUNDENZETTEL TEST LINKI
    # =====================================================

    @app.route("/api/ai/stundenzettel-test")
    @login_required
    def kg_ai_stundenzettel_test():
        month = int(request.args.get("month", "7"))
        year = int(request.args.get("year", "2026"))

        summary = ai_read_stundenzettel_summary(month, year)
        answer = format_stundenzettel_summary(month, year, summary)

        return "<pre>" + answer + "</pre>"


    # =====================================================
    # KG AI TEST
    # =====================================================

    @app.route("/api/ai/test")
    @login_required
    def kg_ai_old_test_redirect():
        try:
            result = ai_test()
            return jsonify({
                "ok": True,
                "action": "test",
                "model": result.get("model"),
                "answer": result.get("answer")
            })

        except Exception as e:
            return jsonify({
                "ok": False,
                "error": str(e)
            }), 500


    # =====================================================
    # KG AI SAYFASI
    # =====================================================

    @app.route("/kg-ai")
    @login_required
    def kg_ai_page():
        return render_template("kg_ai.html")