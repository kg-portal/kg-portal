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
                survey_token TEXT,
                sent_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        try:
            conn.execute("ALTER TABLE kunden_quality_mail ADD COLUMN survey_token TEXT")
        except Exception:
            pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS kunden_quality_survey (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mail_id INTEGER NOT NULL UNIQUE,
                kunde_id INTEGER NOT NULL,
                score_gesamt INTEGER NOT NULL,
                score_mitarbeiter INTEGER NOT NULL,
                score_sauberkeit INTEGER NOT NULL,
                score_reinigungsmittel INTEGER NOT NULL,
                score_kommunikation INTEGER NOT NULL,
                kommentar TEXT,
                submitted_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_kunden_quality_token
            ON kunden_quality_mail(unsubscribe_token)
        """)
        try:
            conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_kunden_quality_survey_token
                ON kunden_quality_mail(survey_token)
                WHERE survey_token IS NOT NULL
            """)
        except Exception:
            pass
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_kunden_quality_kunde_sent
            ON kunden_quality_mail(kunde_id, sent_at DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_kunden_quality_survey_kunde
            ON kunden_quality_survey(kunde_id, submitted_at DESC)
        """)
        conn.commit()
        conn.close()

    def kundenpflege_new_token():
        import secrets
        return secrets.token_urlsafe(24)

    def kundenpflege_customer_row(row):
        keys = set(row.keys())

        def val(name, default=""):
            if name not in keys:
                return default
            value = row[name]
            return default if value is None else value

        return {
            "id": row["id"],
            "firma": row["firma"] or "",
            "anrede": "",
            "ansprechpartner_name": row["ansprechpartner_name"] or "",
            "email": row["email"] or "",
            "ort": row["ort"] or "",
            "vertragsstatus": row["vertragsstatus"] or "aktuell",
            "enabled": bool(row["quality_enabled"] if row["quality_enabled"] is not None else 1),
            "interval_months": int(row["interval_months"] or 3),
            "last_sent": row["last_sent"] or "",
            "unsubscribed_at": row["unsubscribed_at"] or "",
            "last_survey_at": val("last_survey_at", ""),
            "score_gesamt": val("score_gesamt", ""),
            "score_mitarbeiter": val("score_mitarbeiter", ""),
            "score_sauberkeit": val("score_sauberkeit", ""),
            "score_reinigungsmittel": val("score_reinigungsmittel", ""),
            "score_kommunikation": val("score_kommunikation", ""),
            "survey_kommentar": val("survey_kommentar", "")
        }

    def kundenpflege_mail_text(kunde, survey_url, unsubscribe_url):
        return """Sehr geehrter Kunde,

wir möchten regelmäßig sicherstellen, dass Sie mit unserer Reinigungsleistung zufrieden sind.

Dürfen wir Sie kurz um eine Rückmeldung bitten?

• Sind Sie mit unserer Reinigungsleistung insgesamt zufrieden?
• Gibt es etwas, das unsere Mitarbeiter anders oder besser machen sollen?
• Gibt es Bereiche, die künftig mehr Aufmerksamkeit benötigen?
• Sind Sie mit den eingesetzten Reinigungsmitteln und deren Geruch zufrieden?
• Haben Sie weitere Wünsche oder Hinweise für uns?

Eine kurze Antwort auf diese E-Mail genügt. Ihre Rückmeldung hilft uns, Probleme frühzeitig zu erkennen und unsere Leistung laufend zu verbessern.

Alternativ können Sie uns Ihr Feedback in wenigen Minuten direkt über unsere kurze Kundenzufriedenheitsumfrage senden:
{survey_url}

Wenn Sie diese Qualitätsabfragen künftig nicht mehr erhalten möchten:
{unsubscribe_url}

Mit freundlichen Grüßen
Ihr Team von KG Gebäudereinigung
""".format(
            survey_url=survey_url,
            unsubscribe_url=unsubscribe_url
        ).strip()

    def kundenpflege_mail_html(kunde, survey_url, unsubscribe_url):
        firma = str(kunde.get("firma") or "").strip()
        return """
        <div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.65;color:#111827;">
            <p>Sehr geehrter Kunde,</p>
            <p>wir möchten regelmäßig sicherstellen, dass Sie mit unserer Reinigungsleistung zufrieden sind.</p>
            <p>Dürfen wir Sie kurz um eine Rückmeldung bitten?</p>
            <ul style="padding-left:20px;">
                <li>Sind Sie mit unserer Reinigungsleistung insgesamt zufrieden?</li>
                <li>Gibt es etwas, das unsere Mitarbeiter anders oder besser machen sollen?</li>
                <li>Gibt es Bereiche, die künftig mehr Aufmerksamkeit benötigen?</li>
                <li>Sind Sie mit den eingesetzten Reinigungsmitteln und deren Geruch zufrieden?</li>
                <li>Haben Sie weitere Wünsche oder Hinweise für uns?</li>
            </ul>
            <p>Eine kurze Antwort auf diese E-Mail genügt. Ihre Rückmeldung hilft uns, Probleme frühzeitig zu erkennen und unsere Leistung laufend zu verbessern.</p>

            <div style="margin:24px 0;padding:18px;border:1px solid #dbeafe;background:#f8fbff;border-radius:14px;">
                <div style="font-weight:700;margin-bottom:10px;">Alternativ können Sie uns Ihr Feedback in wenigen Minuten direkt über unsere kurze Kundenzufriedenheitsumfrage senden.</div>
                <a href="{survey_url}" style="display:inline-block;padding:11px 18px;background:#2563eb;color:#ffffff;text-decoration:none;border-radius:9px;font-weight:700;">Zur Kundenzufriedenheitsumfrage</a>
            </div>

            <p style="margin-top:24px;color:#64748b;font-size:13px;">
                Wenn Sie diese Qualitätsabfragen künftig nicht mehr erhalten möchten:
                <a href="{unsubscribe_url}" style="display:inline-block;margin-left:6px;padding:7px 12px;background:#f97316;color:#ffffff;text-decoration:none;border-radius:8px;font-weight:700;">Abbestellen</a>
            </p>

            <p style="margin-top:24px;">Mit freundlichen Grüßen<br>Ihr Team von KG Gebäudereinigung</p>
        </div>
        """.format(
            firma=firma,
            survey_url=survey_url,
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
                ) AS last_sent,
                (
                    SELECT qs.submitted_at
                    FROM kunden_quality_survey qs
                    WHERE qs.kunde_id = k.id
                    ORDER BY qs.submitted_at DESC, qs.id DESC
                    LIMIT 1
                ) AS last_survey_at,
                (
                    SELECT qs.score_gesamt
                    FROM kunden_quality_survey qs
                    WHERE qs.kunde_id = k.id
                    ORDER BY qs.submitted_at DESC, qs.id DESC
                    LIMIT 1
                ) AS score_gesamt,
                (
                    SELECT qs.score_mitarbeiter
                    FROM kunden_quality_survey qs
                    WHERE qs.kunde_id = k.id
                    ORDER BY qs.submitted_at DESC, qs.id DESC
                    LIMIT 1
                ) AS score_mitarbeiter,
                (
                    SELECT qs.score_sauberkeit
                    FROM kunden_quality_survey qs
                    WHERE qs.kunde_id = k.id
                    ORDER BY qs.submitted_at DESC, qs.id DESC
                    LIMIT 1
                ) AS score_sauberkeit,
                (
                    SELECT qs.score_reinigungsmittel
                    FROM kunden_quality_survey qs
                    WHERE qs.kunde_id = k.id
                    ORDER BY qs.submitted_at DESC, qs.id DESC
                    LIMIT 1
                ) AS score_reinigungsmittel,
                (
                    SELECT qs.score_kommunikation
                    FROM kunden_quality_survey qs
                    WHERE qs.kunde_id = k.id
                    ORDER BY qs.submitted_at DESC, qs.id DESC
                    LIMIT 1
                ) AS score_kommunikation,
                (
                    SELECT qs.kommentar
                    FROM kunden_quality_survey qs
                    WHERE qs.kunde_id = k.id
                    ORDER BY qs.submitted_at DESC, qs.id DESC
                    LIMIT 1
                ) AS survey_kommentar
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

            unsubscribe_token = kundenpflege_new_token()
            survey_token = kundenpflege_new_token()
            base_url = request.host_url.rstrip("/")
            unsubscribe_url = base_url + "/qualitaetsmail/abbestellen/" + unsubscribe_token
            survey_url = base_url + "/kundenfeedback/" + survey_token

            body = kundenpflege_mail_text(kunde, survey_url, unsubscribe_url)
            body_html = kundenpflege_mail_html(kunde, survey_url, unsubscribe_url)

            result.append({
                **kunde,
                "subject": "Kurze Qualitätsabfrage zu unserer Reinigung",
                "body": body,
                "body_html": body_html,
                "unsubscribe_token": unsubscribe_token,
                "unsubscribe_url": unsubscribe_url,
                "survey_token": survey_token,
                "survey_url": survey_url
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
        unsubscribe_token = str(data.get("unsubscribe_token") or "").strip()
        survey_token = str(data.get("survey_token") or "").strip()
        status = str(data.get("status") or "sent").strip()

        if kunde_id <= 0 or not recipient or not subject or not body or not unsubscribe_token or not survey_token:
            return jsonify({"ok": False, "message": "Unvollständige Versanddaten."}), 400

        conn = get_db_connection()
        conn.execute("""
            INSERT INTO kunden_quality_mail
                (kunde_id, recipient, subject, body, gmail_id, status, unsubscribe_token, survey_token, sent_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        """, (kunde_id, recipient, subject, body, gmail_id, status, unsubscribe_token, survey_token))
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
                p.unsubscribed_at,
                qs.submitted_at AS survey_submitted_at,
                qs.score_gesamt AS survey_score
            FROM kunden_quality_mail qm
            LEFT JOIN kunden k ON k.id = qm.kunde_id
            LEFT JOIN kunden_quality_pref p ON p.kunde_id = qm.kunde_id
            LEFT JOIN kunden_quality_survey qs ON qs.mail_id = qm.id
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

    @app.route("/kundenfeedback/<token>", methods=["GET", "POST"])
    def kg_ai_kundenfeedback(token):
        ensure_kundenpflege_tables()
        token = str(token or "").strip()

        conn = get_db_connection()
        row = conn.execute("""
            SELECT
                qm.id AS mail_id,
                qm.kunde_id,
                qm.survey_token,
                k.firma,
                qs.id AS survey_id,
                qs.submitted_at
            FROM kunden_quality_mail qm
            JOIN kunden k ON k.id = qm.kunde_id
            LEFT JOIN kunden_quality_survey qs ON qs.mail_id = qm.id
            WHERE qm.survey_token = ?
            LIMIT 1
        """, (token,)).fetchone()

        if not row:
            conn.close()
            return "<h2>Dieser Umfrage-Link ist ungültig oder nicht mehr verfügbar.</h2>", 404

        firma = str(row["firma"] or "Kunde").strip()

        if request.method == "POST":
            if row["survey_id"]:
                conn.close()
                return """
                <!doctype html><html lang="de"><meta charset="utf-8">
                <body style="font-family:Arial,sans-serif;background:#f8fafc;padding:40px;color:#0f172a;">
                <div style="max-width:680px;margin:auto;background:white;border:1px solid #dbeafe;border-radius:22px;padding:36px;box-shadow:0 20px 50px rgba(15,23,42,.08);">
                <h2>Vielen Dank.</h2><p>Ihr Feedback wurde bereits übermittelt.</p>
                </div></body></html>
                """

            score_names = [
                "score_gesamt",
                "score_mitarbeiter",
                "score_sauberkeit",
                "score_reinigungsmittel",
                "score_kommunikation"
            ]

            scores = {}
            try:
                for name in score_names:
                    value = int(request.form.get(name, "0"))
                    if value < 1 or value > 5:
                        raise ValueError(name)
                    scores[name] = value
            except Exception:
                conn.close()
                return "<h2>Bitte bewerten Sie alle fünf Bereiche von 1 bis 5.</h2>", 400

            kommentar = str(request.form.get("kommentar") or "").strip()[:4000]

            conn.execute("""
                INSERT INTO kunden_quality_survey (
                    mail_id,
                    kunde_id,
                    score_gesamt,
                    score_mitarbeiter,
                    score_sauberkeit,
                    score_reinigungsmittel,
                    score_kommunikation,
                    kommentar,
                    submitted_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            """, (
                int(row["mail_id"]),
                int(row["kunde_id"]),
                scores["score_gesamt"],
                scores["score_mitarbeiter"],
                scores["score_sauberkeit"],
                scores["score_reinigungsmittel"],
                scores["score_kommunikation"],
                kommentar
            ))
            conn.commit()
            conn.close()

            average = round(sum(scores.values()) / 5, 1)
            notification_text = (
                f"Neue Kundenzufriedenheitsumfrage\n\n"
                f"Kunde: {firma}\n"
                f"Gesamtzufriedenheit: {scores['score_gesamt']}/5\n"
                f"Mitarbeiter: {scores['score_mitarbeiter']}/5\n"
                f"Sauberkeit & Gründlichkeit: {scores['score_sauberkeit']}/5\n"
                f"Reinigungsmittel / Geruch: {scores['score_reinigungsmittel']}/5\n"
                f"Kommunikation & Zuverlässigkeit: {scores['score_kommunikation']}/5\n"
                f"Durchschnitt: {average}/5\n\n"
                f"Kommentar: {kommentar or '-'}"
            )

            try:
                from app2 import send_gmail_message_direct
                send_gmail_message_direct(
                    "info@kg-reinigung.de",
                    f"Neue Kundenzufriedenheitsumfrage – {firma}",
                    notification_text
                )
            except Exception as mail_error:
                print("KUNDENFEEDBACK MAIL FEHLER:", str(mail_error))

            return """
            <!doctype html>
            <html lang="de">
            <head>
              <meta charset="utf-8">
              <meta name="viewport" content="width=device-width,initial-scale=1">
              <title>Vielen Dank</title>
            </head>
            <body style="margin:0;font-family:Arial,Helvetica,sans-serif;background:linear-gradient(135deg,#eef5ff,#f8fafc);padding:36px;color:#0f172a;">
              <div style="max-width:680px;margin:60px auto;background:#fff;border:1px solid #dbeafe;border-radius:24px;padding:42px;box-shadow:0 24px 70px rgba(37,99,235,.12);text-align:center;">
                <div style="font-size:14px;font-weight:800;color:#2563eb;letter-spacing:.08em;text-transform:uppercase;">KG Gebäudereinigung</div>
                <h1 style="margin:14px 0 10px;font-size:30px;">Vielen Dank für Ihr Feedback.</h1>
                <p style="color:#64748b;font-size:16px;line-height:1.6;">Ihre Rückmeldung wurde erfolgreich übermittelt und hilft uns, unsere Qualität weiter zu verbessern.</p>
              </div>
            </body>
            </html>
            """

        conn.close()

        if row["survey_id"]:
            return """
            <!doctype html><html lang="de"><meta charset="utf-8">
            <body style="font-family:Arial,sans-serif;background:#f8fafc;padding:40px;color:#0f172a;">
            <div style="max-width:680px;margin:auto;background:white;border:1px solid #dbeafe;border-radius:22px;padding:36px;">
            <h2>Vielen Dank.</h2><p>Für diesen Link wurde bereits Feedback übermittelt.</p>
            </div></body></html>
            """

        from html import escape as html_escape
        firma_safe = html_escape(firma)

        rating_rows = [
            ("score_gesamt", "Gesamtzufriedenheit", "Wie zufrieden sind Sie insgesamt mit unserer Reinigungsleistung?"),
            ("score_mitarbeiter", "Mitarbeiter", "Wie zufrieden sind Sie mit der Arbeit unserer Mitarbeiter?"),
            ("score_sauberkeit", "Sauberkeit & Gründlichkeit", "Wie zufrieden sind Sie mit Sauberkeit und Gründlichkeit?"),
            ("score_reinigungsmittel", "Reinigungsmittel / Geruch", "Wie zufrieden sind Sie mit den eingesetzten Reinigungsmitteln und deren Geruch?"),
            ("score_kommunikation", "Kommunikation & Zuverlässigkeit", "Wie zufrieden sind Sie mit Kommunikation und Zuverlässigkeit?")
        ]

        questions_html = []
        for field, title, question in rating_rows:
            buttons = "".join(
                f'<label class="score"><input type="radio" name="{field}" value="{n}" required><span>{n}</span></label>'
                for n in range(1, 6)
            )
            questions_html.append(
                f'<div class="question"><div class="qtitle">{title}</div><div class="qtext">{question}</div>'
                f'<div class="scores">{buttons}</div><div class="scale"><span>1 = nicht zufrieden</span><span>5 = sehr zufrieden</span></div></div>'
            )

        return f"""
        <!doctype html>
        <html lang="de">
        <head>
          <meta charset="utf-8">
          <meta name="viewport" content="width=device-width,initial-scale=1">
          <title>KG Kundenzufriedenheitsumfrage</title>
          <style>
            *{{box-sizing:border-box}}
            body{{margin:0;font-family:Arial,Helvetica,sans-serif;background:linear-gradient(135deg,#eaf2ff 0%,#f8fafc 55%,#eef6ff 100%);color:#0f172a;padding:28px}}
            .wrap{{max-width:820px;margin:22px auto}}
            .hero{{background:linear-gradient(135deg,#0f2f78,#2563eb);color:white;border-radius:26px;padding:34px 36px;box-shadow:0 24px 70px rgba(37,99,235,.2)}}
            .brand{{font-size:13px;font-weight:900;letter-spacing:.14em;text-transform:uppercase;opacity:.9}}
            h1{{margin:10px 0 7px;font-size:32px}}
            .company{{font-size:18px;font-weight:800;opacity:.96}}
            .sub{{margin-top:13px;font-size:15px;line-height:1.6;opacity:.92}}
            form{{margin-top:18px;background:white;border:1px solid #dbeafe;border-radius:24px;padding:28px;box-shadow:0 20px 55px rgba(15,23,42,.08)}}
            .question{{padding:20px 0;border-bottom:1px solid #e2e8f0}}
            .question:first-child{{padding-top:0}}
            .qtitle{{font-size:17px;font-weight:900}}
            .qtext{{color:#64748b;margin-top:5px;font-size:14px}}
            .scores{{display:flex;gap:10px;margin-top:15px;flex-wrap:wrap}}
            .score input{{position:absolute;opacity:0;pointer-events:none}}
            .score span{{display:flex;width:48px;height:48px;align-items:center;justify-content:center;border:2px solid #dbeafe;border-radius:14px;font-weight:900;color:#1d4ed8;cursor:pointer;background:#fff;transition:.15s}}
            .score input:checked + span{{background:#2563eb;color:#fff;border-color:#2563eb;transform:translateY(-2px);box-shadow:0 8px 18px rgba(37,99,235,.22)}}
            .scale{{display:flex;justify-content:space-between;color:#94a3b8;font-size:11px;margin-top:7px;max-width:280px}}
            textarea{{width:100%;min-height:130px;border:1px solid #cbd5e1;border-radius:14px;padding:14px;font:inherit;resize:vertical;outline:none}}
            textarea:focus{{border-color:#2563eb;box-shadow:0 0 0 3px rgba(37,99,235,.12)}}
            .send{{width:100%;margin-top:20px;border:none;border-radius:14px;background:#16a34a;color:white;padding:15px 20px;font-size:16px;font-weight:900;cursor:pointer}}
            .foot{{text-align:center;color:#94a3b8;font-size:12px;margin-top:15px}}
            @media(max-width:620px){{body{{padding:14px}}.hero{{padding:26px 22px}}h1{{font-size:26px}}form{{padding:20px}}}}
          </style>
        </head>
        <body>
          <div class="wrap">
            <div class="hero">
              <div class="brand">KG Gebäudereinigung</div>
              <h1>Kundenzufriedenheitsumfrage</h1>
              <div class="company">{firma_safe}</div>
              <div class="sub">Ihre Rückmeldung dauert nur wenige Minuten. Bitte bewerten Sie die folgenden Bereiche von 1 bis 5.</div>
            </div>
            <form method="post">
              {''.join(questions_html)}
              <div class="question" style="border-bottom:none;">
                <div class="qtitle">Möchten Sie uns noch etwas mitteilen?</div>
                <div class="qtext" style="margin-bottom:12px;">Wünsche, Hinweise oder Verbesserungsvorschläge können Sie uns hier direkt mitteilen.</div>
                <textarea name="kommentar" maxlength="4000" placeholder="Ihre Nachricht an uns ..."></textarea>
              </div>
              <button class="send" type="submit">Feedback senden</button>
              <div class="foot">Vielen Dank, dass Sie uns helfen, unsere Leistung weiter zu verbessern.</div>
            </form>
          </div>
        </body>
        </html>
        """

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