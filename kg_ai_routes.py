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