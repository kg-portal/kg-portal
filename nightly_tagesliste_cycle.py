# -*- coding: utf-8 -*-
"""
GERCEK BRANCHE -> TAGESLISTE TEST CYCLE

Bu dosya:
1. Sahte firma üretmez.
2. Google API kullanmaz.
3. Mevcut 12'li Branche listesindeki gerçek firmalardan 10 tanesini Tagesliste'ye taşır.
4. Tekrar çalışınca:
   - İşlem görmemiş Tagesliste firmalarını eski Branche listesine geri açar.
   - İşlem görmüş firmaları Tagesliste'den siler.
   - İşlem görmüşler için 6'lı status backup yazar.
   - Sonra Branche listesinden yeni 10 gerçek firma seçip Tagesliste'ye ekler.

Çalıştırma:
    python test_tagesliste_cycle.py
"""

import os
import sys
import sqlite3

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "kg_portal.db")

TARGET_COUNT = 30

PROCESSED_STATUSES = [
    "angerufen",
    "interessiert",
    "besichtigung",
    "kontaktformular",
    "spaeter",
    "verloren"
]


def get_conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def table_columns(cursor, table_name):
    try:
        rows = cursor.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {row[1] for row in rows}
    except Exception:
        return set()


def add_column_if_missing(cursor, table_name, column_name, column_sql):
    cols = table_columns(cursor, table_name)
    if column_name not in cols:
        try:
            cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")
        except Exception:
            pass


def ensure_tables():
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tagesliste_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_lead_id INTEGER,
            firma TEXT,
            branche TEXT,
            ansprechpartner TEXT,
            strasse TEXT,
            plz TEXT,
            ort TEXT,
            telefon TEXT,
            email TEXT,
            website TEXT,
            google_maps_url TEXT,
            quelle TEXT,
            status TEXT DEFAULT 'offen',
            notiz TEXT,
            spaeter_datum TEXT,
            company_key TEXT,
            sort_order INTEGER DEFAULT 0,
            erstellt_am TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    for col_name, col_sql in [
        ("source_lead_id", "source_lead_id INTEGER"),
        ("google_maps_url", "google_maps_url TEXT"),
        ("notiz", "notiz TEXT"),
        ("spaeter_datum", "spaeter_datum TEXT"),
        ("company_key", "company_key TEXT"),
        ("sort_order", "sort_order INTEGER DEFAULT 0"),
    ]:
        add_column_if_missing(cursor, "tagesliste_leads", col_name, col_sql)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tagesliste_status_backup (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tagesliste_id INTEGER NOT NULL,
            source_lead_id INTEGER,
            firma TEXT,
            branche TEXT,
            ansprechpartner TEXT,
            strasse TEXT,
            plz TEXT,
            ort TEXT,
            telefon TEXT,
            email TEXT,
            website TEXT,
            quelle TEXT,
            status TEXT NOT NULL,
            notiz TEXT,
            spaeter_datum TEXT,
            erstellt_am TEXT,
            backup_am TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tagesliste_id, status)
        )
    """)

    try:
        cursor.execute("ALTER TABLE leads ADD COLUMN sort_order INTEGER DEFAULT 0")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE leads ADD COLUMN google_maps_url TEXT")
    except Exception:
        pass

    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_branche_id ON leads(branche_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tagesliste_status ON tagesliste_leads(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_tagesliste_source ON tagesliste_leads(source_lead_id)")
    except Exception:
        pass

    conn.commit()
    conn.close()


def norm(v):
    if v is None:
        return ""
    return str(v).strip()


def to_float(v):
    try:
        return float(str(v or "0").replace(",", "."))
    except Exception:
        return 0.0


def to_int(v):
    try:
        return int(float(str(v or "0").replace(",", ".")))
    except Exception:
        return 0


def quality_score(row):
    score = 0

    if norm(row.get("telefon")):
        score += 40

    if norm(row.get("email")):
        score += 35

    if norm(row.get("website")):
        score += 25

    if norm(row.get("google_maps_url")):
        score += 10

    rating = to_float(row.get("rating"))
    reviews = to_int(row.get("user_rating_count"))

    if rating >= 4.5:
        score += 20
    elif rating >= 4.0:
        score += 14
    elif rating >= 3.5:
        score += 6

    if reviews > 0:
        score += min(25, reviews // 5)

    branche_id = norm(row.get("branche_id"))

    if branche_id in ["1", "2", "9", "10", "11"]:
        score += 10

    if norm(row.get("plz")):
        score += 5

    return score


def cleanup_old_fake_test_data(cursor):
    """
    Önceki hatalı KG TEST sahte firmalarını temizler.
    Gerçek firmalara dokunmaz.
    """
    test_ids = [
        int(r["id"])
        for r in cursor.execute("""
            SELECT id
            FROM tagesliste_leads
            WHERE firma LIKE 'KG TEST%'
               OR quelle = 'KG Test Cycle'
        """).fetchall()
    ]

    for tid in test_ids:
        cursor.execute("""
            DELETE FROM tagesliste_status_backup
            WHERE tagesliste_id = ?
        """, (tid,))

    cursor.execute("""
        DELETE FROM tagesliste_leads
        WHERE firma LIKE 'KG TEST%'
           OR quelle = 'KG Test Cycle'
    """)

    cursor.execute("""
        DELETE FROM leads
        WHERE firma LIKE 'KG TEST%'
           OR quelle = 'KG Test Cycle'
           OR unique_key LIKE 'kg-test-cycle-%'
    """)


def backup_processed_row(cursor, tagesliste_id, status):
    if status not in PROCESSED_STATUSES:
        return 0

    cursor.execute("""
        INSERT OR REPLACE INTO tagesliste_status_backup (
            tagesliste_id,
            source_lead_id,
            firma,
            branche,
            ansprechpartner,
            strasse,
            plz,
            ort,
            telefon,
            email,
            website,
            quelle,
            status,
            notiz,
            spaeter_datum,
            erstellt_am,
            backup_am
        )
        SELECT
            id,
            source_lead_id,
            firma,
            branche,
            ansprechpartner,
            strasse,
            plz,
            ort,
            telefon,
            email,
            website,
            quelle,
            ?,
            notiz,
            spaeter_datum,
            erstellt_am,
            CURRENT_TIMESTAMP
        FROM tagesliste_leads
        WHERE id = ?
    """, (status, tagesliste_id))

    return cursor.rowcount


def reset_current_tagesliste(cursor):
    rows = cursor.execute("""
        SELECT *
        FROM tagesliste_leads
        ORDER BY id ASC
    """).fetchall()

    returned_to_branch = 0
    processed_deleted = 0
    open_deleted = 0
    backup_written = 0

    for row in rows:
        tagesliste_id = int(row["id"] or 0)
        status = norm(row["status"]).lower() or "offen"

        try:
            source_lead_id = int(row["source_lead_id"] or 0)
        except Exception:
            source_lead_id = 0

        if status in ["", "offen"]:
            if source_lead_id > 0:
                cursor.execute("""
                    UPDATE leads
                    SET status = 'Neu'
                    WHERE id = ?
                """, (source_lead_id,))
                returned_to_branch += cursor.rowcount

            cursor.execute("""
                DELETE FROM tagesliste_leads
                WHERE id = ?
            """, (tagesliste_id,))
            open_deleted += cursor.rowcount

        else:
            backup_written += backup_processed_row(cursor, tagesliste_id, status)

            cursor.execute("""
                DELETE FROM tagesliste_leads
                WHERE id = ?
            """, (tagesliste_id,))
            processed_deleted += cursor.rowcount

    return {
        "found_tagesliste": len(rows),
        "returned_to_branch": returned_to_branch,
        "open_deleted": open_deleted,
        "processed_deleted": processed_deleted,
        "backup_written": backup_written,
    }


def company_key(row):
    firma = norm(row.get("firma")).lower()
    telefon = norm(row.get("telefon")).lower()
    email = norm(row.get("email")).lower()
    website = norm(row.get("website")).lower()
    return f"{firma}|{telefon}|{email}|{website}"


def get_max_sort_order(cursor):
    row = cursor.execute("""
        SELECT COALESCE(MAX(sort_order), 0)
        FROM tagesliste_leads
    """).fetchone()
    return int(row[0] or 0)


def select_real_branch_leads(cursor, limit_count):
    rows = cursor.execute("""
        SELECT *
        FROM leads
        WHERE (status IS NULL OR status = '' OR status != 'Tagesliste')
          AND COALESCE(firma, '') != ''
          AND COALESCE(firma, '') NOT LIKE 'KG TEST%'
          AND COALESCE(quelle, '') != 'KG Test Cycle'
        ORDER BY
            CASE WHEN branche_id IS NULL OR branche_id = '' THEN 1 ELSE 0 END,
            branche_id ASC,
            CASE WHEN sort_order IS NULL OR sort_order = 0 THEN 1 ELSE 0 END,
            sort_order ASC,
            CASE WHEN plz IS NULL OR plz = '' THEN 1 ELSE 0 END,
            plz ASC,
            firma ASC
    """).fetchall()

    real_rows = [dict(r) for r in rows]

    real_rows.sort(
        key=lambda r: (
            -quality_score(r),
            norm(r.get("branche_id")),
            norm(r.get("plz")),
            norm(r.get("firma")).lower()
        )
    )

    return real_rows[:int(limit_count)]


def add_real_leads_to_tagesliste(cursor, target_count):
    selected = select_real_branch_leads(cursor, target_count)

    max_sort = get_max_sort_order(cursor)
    added = 0
    skipped = 0

    for row in selected:
        lead_id = int(row.get("id") or 0)
        if lead_id <= 0:
            skipped += 1
            continue

        key = company_key(row)

        existing = cursor.execute("""
            SELECT id
            FROM tagesliste_leads
            WHERE company_key = ?
            LIMIT 1
        """, (key,)).fetchone()

        if existing:
            skipped += 1
            continue

        branche = norm(row.get("branche_name")) or norm(row.get("branche_id"))

        max_sort += 1

        cursor.execute("""
            INSERT OR IGNORE INTO tagesliste_leads (
                source_lead_id,
                firma,
                branche,
                ansprechpartner,
                strasse,
                plz,
                ort,
                telefon,
                email,
                website,
                google_maps_url,
                quelle,
                status,
                company_key,
                sort_order,
                erstellt_am
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'offen', ?, ?, CURRENT_TIMESTAMP)
        """, (
            lead_id,
            norm(row.get("firma")),
            branche,
            norm(row.get("ansprechpartner")),
            norm(row.get("strasse")),
            norm(row.get("plz")),
            norm(row.get("stadt")),
            norm(row.get("telefon")),
            norm(row.get("email")),
            norm(row.get("website")),
            norm(row.get("google_maps_url")),
            norm(row.get("quelle")) or "Branche",
            key,
            max_sort,
        ))

        if cursor.rowcount == 1:
            cursor.execute("""
                UPDATE leads
                SET status = 'Tagesliste'
                WHERE id = ?
            """, (lead_id,))
            added += 1
        else:
            skipped += 1

    return {
        "selected_from_branch": len(selected),
        "added_to_tagesliste": added,
        "skipped": skipped,
    }


def run_cycle():
    ensure_tables()

    conn = get_conn()
    cursor = conn.cursor()

    print("============================================================")
    print("GERCEK BRANCHE -> TAGESLISTE CYCLE START")
    print(f"DB: {DB_PATH}")
    print("============================================================")

    cleanup_old_fake_test_data(cursor)

    reset_result = reset_current_tagesliste(cursor)
    print("1) Tagesliste temizlendi")
    print(reset_result)

    add_result = add_real_leads_to_tagesliste(cursor, TARGET_COUNT)
    print("2) Branche listesinden gerçek firma Tagesliste'ye alındı")
    print(add_result)

    conn.commit()
    conn.close()

    print("============================================================")
    print("FERTIG")
    print("Tarayıcıda /datenbank sayfasını yenile.")
    print("============================================================")


if __name__ == "__main__":
    run_cycle()