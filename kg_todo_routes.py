# =====================================================
# KG TODO ROUTES
# To-Do / Monatsplan / Wiederkehrende Aufgaben
# =====================================================

from flask import render_template, request, redirect, url_for, jsonify
from datetime import datetime, date, timedelta
import calendar


# =====================================================
# HILFSFUNKTIONEN
# =====================================================

def kg_todo_today():
    return date.today()


def kg_todo_date_text(value):
    value = str(value or "").strip()

    if not value:
        return ""

    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%d.%m.%Y")
    except Exception:
        return value


def kg_todo_month_start(target_date=None):
    d = target_date or kg_todo_today()
    return date(d.year, d.month, 1)


def kg_todo_month_end(target_date=None):
    d = target_date or kg_todo_today()
    last_day = calendar.monthrange(d.year, d.month)[1]
    return date(d.year, d.month, last_day)


def kg_todo_last_week_start(target_date=None):
    """
    Ayın son haftasının Pazartesi gününü bulur.
    Örnek: ay sonu hangi haftadaysa, o haftanın pazartesisi.
    """
    end_date = kg_todo_month_end(target_date)
    return end_date - timedelta(days=end_date.weekday())


def kg_todo_business_days_before_month_end(days=5, target_date=None):
    """
    Ay sonundan geriye doğru iş günü sayar.
    Cumartesi/Pazar sayılmaz.
    """
    current = kg_todo_month_end(target_date)
    count = 0

    while count < days:
        current = current - timedelta(days=1)
        if current.weekday() < 5:
            count += 1

    return current


def kg_todo_template_due_date(template, target_date=None):
    """
    task_templates satırına göre o ay için deadline hesaplar.
    """
    d = target_date or kg_todo_today()
    business_rule = str(template["business_day_rule"] or "").strip()
    due_day = template["due_day"]

    if business_rule == "last_week_start":
        return kg_todo_last_week_start(d).strftime("%Y-%m-%d")

    if business_rule == "5_business_days_before_month_end":
        return kg_todo_business_days_before_month_end(5, d).strftime("%Y-%m-%d")

    if due_day:
        try:
            due_day = int(due_day)
            last_day = calendar.monthrange(d.year, d.month)[1]
            safe_day = min(due_day, last_day)
            return date(d.year, d.month, safe_day).strftime("%Y-%m-%d")
        except Exception:
            return ""

    return ""


# =====================================================
# DB TABLOLARI
# =====================================================

def ensure_todo_tables(conn):
    # Eski todos tablosu korunur.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS todos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL,
            done INTEGER DEFAULT 0,
            deadline TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        conn.execute("ALTER TABLE todos ADD COLUMN deadline TEXT")
    except Exception:
        pass

    # Yeni To-Do alanları: eski tablo bozulmadan genişletilir.
    todo_columns = [
        ("description", "TEXT"),
        ("category", "TEXT"),
        ("priority", "TEXT DEFAULT 'normal'"),
        ("status", "TEXT DEFAULT 'open'"),
        ("due_time", "TEXT"),
        ("period_type", "TEXT DEFAULT 'once'"),
        ("source", "TEXT DEFAULT 'manual'"),
        ("created_by", "TEXT"),
        ("assigned_to", "TEXT"),
        ("reminder_enabled", "INTEGER DEFAULT 0"),
        ("reminder_sent", "INTEGER DEFAULT 0"),
        ("ai_note", "TEXT"),
        ("template_id", "INTEGER"),
        ("amount", "REAL"),
        ("amount_text", "TEXT"),
        ("updated_at", "TEXT"),
        ("completed_at", "TEXT")
    ]

    for col_name, col_type in todo_columns:
        try:
            conn.execute(f"ALTER TABLE todos ADD COLUMN {col_name} {col_type}")
        except Exception:
            pass

    # Sabit aylık plan şablonları.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT,
            description TEXT,
            amount REAL,
            amount_text TEXT,
            frequency TEXT DEFAULT 'monthly',
            day_rule TEXT,
            due_day INTEGER,
            business_day_rule TEXT,
            source_module TEXT,
            requires_approval INTEGER DEFAULT 0,
            whatsapp_enabled INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            sort_order INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    template_columns = [
        ("amount_text", "TEXT"),
        ("business_day_rule", "TEXT"),
        ("source_module", "TEXT"),
        ("requires_approval", "INTEGER DEFAULT 0"),
        ("whatsapp_enabled", "INTEGER DEFAULT 0"),
        ("active", "INTEGER DEFAULT 1"),
        ("sort_order", "INTEGER DEFAULT 0")
    ]

    for col_name, col_type in template_columns:
        try:
            conn.execute(f"ALTER TABLE task_templates ADD COLUMN {col_name} {col_type}")
        except Exception:
            pass

    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_todos_deadline ON todos(deadline)")
    except Exception:
        pass

    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_todos_template_deadline ON todos(template_id, deadline)")
    except Exception:
        pass

    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_task_templates_active_sort ON task_templates(active, sort_order)")
    except Exception:
        pass

    conn.commit()


# =====================================================
# SEED - SABIT MONATSPLAN
# =====================================================

def seed_todo_templates(conn):
    existing = conn.execute("SELECT COUNT(*) FROM task_templates").fetchone()[0]

    if existing and existing > 0:
        return

    templates = [
        {
            "title": "Wohnungsmiete zahlen",
            "category": "Private Zahlungen",
            "description": "Private Wohnungsmiete monatlich zahlen.",
            "amount": 1180.00,
            "amount_text": "1.180,00 €",
            "frequency": "monthly",
            "day_rule": "Jeden Monat am 01.",
            "due_day": 1,
            "business_day_rule": "",
            "source_module": "manual",
            "requires_approval": 0,
            "whatsapp_enabled": 0,
            "sort_order": 10
        },
        {
            "title": "Garage 1 zahlen",
            "category": "Private Zahlungen",
            "description": "Garage 1 monatlich zahlen.",
            "amount": 120.00,
            "amount_text": "120,00 €",
            "frequency": "monthly",
            "day_rule": "Jeden Monat am 01.",
            "due_day": 1,
            "business_day_rule": "",
            "source_module": "manual",
            "requires_approval": 0,
            "whatsapp_enabled": 0,
            "sort_order": 20
        },
        {
            "title": "Stellplatz + Garage 2 zahlen",
            "category": "Private Zahlungen",
            "description": "Stellplatz + Garage 2 monatlich zahlen.",
            "amount": 100.00,
            "amount_text": "100,00 €",
            "frequency": "monthly",
            "day_rule": "Jeden Monat am 01.",
            "due_day": 1,
            "business_day_rule": "",
            "source_module": "manual",
            "requires_approval": 0,
            "whatsapp_enabled": 0,
            "sort_order": 30
        },
        {
            "title": "Garage zahlen",
            "category": "Private Zahlungen",
            "description": "Weitere Garage monatlich zahlen.",
            "amount": 90.00,
            "amount_text": "90,00 €",
            "frequency": "monthly",
            "day_rule": "Jeden Monat am 01.",
            "due_day": 1,
            "business_day_rule": "",
            "source_module": "manual",
            "requires_approval": 0,
            "whatsapp_enabled": 0,
            "sort_order": 40
        },
        {
            "title": "Büromiete zahlen",
            "category": "Büromiete",
            "description": "Kiefer & Zehner Liegenschaften-Anlage AG - Miete + Nebenkosten Fliederstr. 59.",
            "amount": 575.53,
            "amount_text": "575,53 €",
            "frequency": "monthly",
            "day_rule": "Jeden Monat am 01.",
            "due_day": 1,
            "business_day_rule": "",
            "source_module": "bank_cache",
            "requires_approval": 0,
            "whatsapp_enabled": 0,
            "sort_order": 50
        },
        {
            "title": "DEURAG / ALLRECHT 4301675 kontrollieren",
            "category": "SEPA-Kontrolle",
            "description": "DEURAG Abbuchung 57,66 € im Bankkonto kontrollieren.",
            "amount": 57.66,
            "amount_text": "57,66 €",
            "frequency": "monthly",
            "day_rule": "Ca. jeden 13.",
            "due_day": 13,
            "business_day_rule": "",
            "source_module": "bank_cache",
            "requires_approval": 0,
            "whatsapp_enabled": 0,
            "sort_order": 60
        },
        {
            "title": "DEURAG / ALLRECHT 4301657 kontrollieren",
            "category": "SEPA-Kontrolle",
            "description": "DEURAG Abbuchung 20,10 € im Bankkonto kontrollieren.",
            "amount": 20.10,
            "amount_text": "20,10 €",
            "frequency": "monthly",
            "day_rule": "Ca. jeden 13.",
            "due_day": 13,
            "business_day_rule": "",
            "source_module": "bank_cache",
            "requires_approval": 0,
            "whatsapp_enabled": 0,
            "sort_order": 70
        },
        {
            "title": "IKK classic Vereinbarung kontrollieren",
            "category": "SEPA-Kontrolle",
            "description": "IKK classic Abbuchung 200,00 € im Bankkonto kontrollieren.",
            "amount": 200.00,
            "amount_text": "200,00 €",
            "frequency": "monthly",
            "day_rule": "Ca. jeden 15.",
            "due_day": 15,
            "business_day_rule": "",
            "source_module": "bank_cache",
            "requires_approval": 0,
            "whatsapp_enabled": 0,
            "sort_order": 80
        },
        {
            "title": "IKK classic Beitrag kontrollieren",
            "category": "SEPA-Kontrolle",
            "description": "IKK classic Beitrag 644,20 € im Bankkonto kontrollieren.",
            "amount": 644.20,
            "amount_text": "644,20 €",
            "frequency": "monthly",
            "day_rule": "Ca. jeden 15.",
            "due_day": 15,
            "business_day_rule": "",
            "source_module": "bank_cache",
            "requires_approval": 0,
            "whatsapp_enabled": 0,
            "sort_order": 90
        },
        {
            "title": "Stundenzettel-Erinnerung vorbereiten",
            "category": "Mitarbeiter / Stundenzettel",
            "description": "WhatsApp-Erinnerung für Mitarbeiter vorbereiten. Versand nur nach Freigabe.",
            "amount": None,
            "amount_text": "",
            "frequency": "monthly",
            "day_rule": "Jeden Monat am 20.",
            "due_day": 20,
            "business_day_rule": "",
            "source_module": "whatsapp_ai",
            "requires_approval": 1,
            "whatsapp_enabled": 1,
            "sort_order": 100
        },
        {
            "title": "Kundenrechnungen vorbereiten",
            "category": "Kunden / Rechnungen",
            "description": "Kundenrechnungen für den Monat vorbereiten und später über Lexware prüfen.",
            "amount": None,
            "amount_text": "",
            "frequency": "monthly",
            "day_rule": "Letzte Monatswoche",
            "due_day": None,
            "business_day_rule": "last_week_start",
            "source_module": "lexware",
            "requires_approval": 0,
            "whatsapp_enabled": 0,
            "sort_order": 110
        },
        {
            "title": "SEPA-Kunden prüfen",
            "category": "Kunden / Rechnungen",
            "description": "SEPA-Kunden und Lastschriften vorbereiten / kontrollieren.",
            "amount": None,
            "amount_text": "",
            "frequency": "monthly",
            "day_rule": "Letzte Monatswoche",
            "due_day": None,
            "business_day_rule": "last_week_start",
            "source_module": "lexware_bank",
            "requires_approval": 0,
            "whatsapp_enabled": 0,
            "sort_order": 120
        },
        {
            "title": "USt-Voranmeldung vorbereiten",
            "category": "Steuer / Buchhaltung",
            "description": "USt-Voranmeldung für den Vormonat vorbereiten und bis spätestens zum 10. prüfen.",
            "amount": None,
            "amount_text": "",
            "frequency": "monthly",
            "day_rule": "Bis zum 10.",
            "due_day": 10,
            "business_day_rule": "",
            "source_module": "buchhaltung",
            "requires_approval": 0,
            "whatsapp_enabled": 0,
            "sort_order": 130
        },
        {
            "title": "Lohnabrechnung vorbereiten",
            "category": "Lohn / Lexware / Knappschaft",
            "description": "Stunden, Krank, Urlaub und fehlende Angaben für Lexware Lohnabrechnung prüfen.",
            "amount": None,
            "amount_text": "",
            "frequency": "monthly",
            "day_rule": "5 Arbeitstage vor Monatsende",
            "due_day": None,
            "business_day_rule": "5_business_days_before_month_end",
            "source_module": "lexware_lohn",
            "requires_approval": 0,
            "whatsapp_enabled": 0,
            "sort_order": 140
        }
    ]

    for item in templates:
        conn.execute("""
            INSERT INTO task_templates
                (
                    title,
                    category,
                    description,
                    amount,
                    amount_text,
                    frequency,
                    day_rule,
                    due_day,
                    business_day_rule,
                    source_module,
                    requires_approval,
                    whatsapp_enabled,
                    sort_order
                )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item["title"],
            item["category"],
            item["description"],
            item["amount"],
            item["amount_text"],
            item["frequency"],
            item["day_rule"],
            item["due_day"],
            item["business_day_rule"],
            item["source_module"],
            item["requires_approval"],
            item["whatsapp_enabled"],
            item["sort_order"]
        ))

    conn.commit()


# =====================================================
# MONATSPLAN -> TODOS ERZEUGEN
# =====================================================

def generate_monthly_todos_from_templates(conn, target_date=None):
    """
    Aktif task_templates kayıtlarından bu ay için gerçek todos üretir.
    Aynı template + aynı deadline varsa tekrar üretmez.
    """
    target_date = target_date or kg_todo_today()

    templates = conn.execute("""
        SELECT *
        FROM task_templates
        WHERE active = 1
        ORDER BY sort_order ASC, id ASC
    """).fetchall()

    created_count = 0

    for tpl in templates:
        due_date = kg_todo_template_due_date(tpl, target_date)

        if not due_date:
            continue

        existing = conn.execute("""
            SELECT id
            FROM todos
            WHERE template_id = ?
            AND deadline = ?
            LIMIT 1
        """, (tpl["id"], due_date)).fetchone()

        if existing:
            continue

        conn.execute("""
            INSERT INTO todos
                (
                    task,
                    done,
                    deadline,
                    description,
                    category,
                    priority,
                    status,
                    period_type,
                    source,
                    created_by,
                    template_id,
                    amount,
                    amount_text,
                    ai_note
                )
            VALUES (?, 0, ?, ?, ?, ?, 'open', 'monthly', 'template', 'KG System', ?, ?, ?, ?)
        """, (
            tpl["title"],
            due_date,
            tpl["description"],
            tpl["category"],
            "hoch" if tpl["requires_approval"] else "normal",
            tpl["id"],
            tpl["amount"],
            tpl["amount_text"],
            tpl["day_rule"]
        ))

        created_count += 1

    conn.commit()
    return created_count


# =====================================================
# TODO INDEX HILFSFUNKTION
# =====================================================

def build_grouped_todos(todos):
    grouped_todos = {}

    for todo in todos:
        if todo["deadline"]:
            try:
                dt = datetime.strptime(todo["deadline"], "%Y-%m-%d")
                kw = dt.isocalendar()[1]
                key = f"KW {kw} ({dt.strftime('%d.%m.%Y')})"
            except Exception:
                key = "Ungeplant"
        else:
            key = "Ungeplant"

        if key not in grouped_todos:
            grouped_todos[key] = []

        grouped_todos[key].append(todo)

    return grouped_todos


def build_monthly_plan_templates(conn):
    rows = conn.execute("""
        SELECT *
        FROM task_templates
        WHERE active = 1
        ORDER BY sort_order ASC, id ASC
    """).fetchall()

    grouped = {}

    for row in rows:
        category = row["category"] or "Sonstiges"

        if category not in grouped:
            grouped[category] = []

        grouped[category].append(row)

    return grouped


# =====================================================
# ROUTE REGISTER
# =====================================================

def register_kg_todo_routes(app, login_required, get_db_connection):

    def kg_todo_bootstrap():
        conn = get_db_connection()
        ensure_todo_tables(conn)
        seed_todo_templates(conn)
        conn.close()

    kg_todo_bootstrap()

    @app.route("/todo")
    @login_required
    def todo_index():
        conn = get_db_connection()
        ensure_todo_tables(conn)
        seed_todo_templates(conn)

        # Bu ayın şablon görevlerini gerçek To-Do olarak oluştur.
        generated_count = generate_monthly_todos_from_templates(conn)

        todos = conn.execute("""
            SELECT *
            FROM todos
            ORDER BY
                CASE WHEN deadline IS NULL OR deadline = '' THEN 1 ELSE 0 END,
                deadline ASC,
                id ASC
        """).fetchall()

        monthly_plan_templates = build_monthly_plan_templates(conn)

        conn.close()

        grouped_todos = build_grouped_todos(todos)
        now_date = kg_todo_today().strftime("%Y-%m-%d")

        return render_template(
            "todo.html",
            grouped_todos=grouped_todos,
            total_count=len(todos),
            now_date=now_date,
            monthly_plan_templates=monthly_plan_templates,
            generated_count=generated_count
        )

    @app.route("/todo/add", methods=["POST"])
    @login_required
    def add_todo():
        task = request.form.get("task")
        deadline = request.form.get("deadline")
        category = request.form.get("category") or "Manuell"
        priority = request.form.get("priority") or "normal"

        if task:
            conn = get_db_connection()
            ensure_todo_tables(conn)

            conn.execute("""
                INSERT INTO todos
                    (
                        task,
                        deadline,
                        category,
                        priority,
                        status,
                        period_type,
                        source,
                        created_by
                    )
                VALUES (?, ?, ?, ?, 'open', 'once', 'manual', 'KG Portal')
            """, (
                task,
                deadline,
                category,
                priority
            ))

            conn.commit()
            conn.close()

        return redirect(url_for("todo_index"))

    @app.route("/todo/toggle/<int:id>")
    @login_required
    def toggle_todo(id):
        conn = get_db_connection()
        ensure_todo_tables(conn)

        row = conn.execute("SELECT done FROM todos WHERE id = ?", (id,)).fetchone()

        if row:
            new_done = 0 if int(row["done"] or 0) == 1 else 1

            if new_done == 1:
                conn.execute("""
                    UPDATE todos
                    SET done = 1,
                        status = 'done',
                        completed_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (id,))
            else:
                conn.execute("""
                    UPDATE todos
                    SET done = 0,
                        status = 'open',
                        completed_at = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                """, (id,))

            conn.commit()

        conn.close()
        return redirect(url_for("todo_index"))

    @app.route("/todo/delete/<int:id>")
    @login_required
    def delete_todo(id):
        conn = get_db_connection()
        ensure_todo_tables(conn)
        conn.execute("DELETE FROM todos WHERE id = ?", (id,))
        conn.commit()
        conn.close()
        return redirect(url_for("todo_index"))

    @app.route("/todo/get/<int:id>")
    @login_required
    def get_todo(id):
        conn = get_db_connection()
        ensure_todo_tables(conn)
        todo = conn.execute("SELECT * FROM todos WHERE id = ?", (id,)).fetchone()
        conn.close()

        if todo:
            data = dict(todo)
            data["deadline_text"] = kg_todo_date_text(data.get("deadline"))
            return jsonify(data)

        return jsonify({"error": "Not found"}), 404

    @app.route("/todo/update", methods=["POST"])
    @login_required
    def update_todo():
        todo_id = request.form.get("id")
        task = request.form.get("task")
        deadline = request.form.get("deadline")
        category = request.form.get("category") or None
        priority = request.form.get("priority") or None

        conn = get_db_connection()
        ensure_todo_tables(conn)

        conn.execute("""
            UPDATE todos
            SET task = ?,
                deadline = ?,
                category = COALESCE(?, category),
                priority = COALESCE(?, priority),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            task,
            deadline,
            category,
            priority,
            todo_id
        ))

        conn.commit()
        conn.close()

        return redirect(url_for("todo_index"))

    @app.route("/api/todo/monthly-plan")
    @login_required
    def api_todo_monthly_plan():
        conn = get_db_connection()
        ensure_todo_tables(conn)
        rows = conn.execute("""
            SELECT *
            FROM task_templates
            WHERE active = 1
            ORDER BY sort_order ASC, id ASC
        """).fetchall()
        conn.close()

        return jsonify({
            "ok": True,
            "items": [dict(row) for row in rows]
        })

    @app.route("/api/todo/generate-monthly", methods=["POST", "GET"])
    @login_required
    def api_todo_generate_monthly():
        conn = get_db_connection()
        ensure_todo_tables(conn)
        seed_todo_templates(conn)
        created_count = generate_monthly_todos_from_templates(conn)
        conn.close()

        return jsonify({
            "ok": True,
            "created_count": created_count
        })