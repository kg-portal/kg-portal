# -*- coding: utf-8 -*-
"""
GOOGLE LEAD POOL - AYRI DB SİSTEMİ

Dosya adı:
    google_lead_pool.py

Amaç:
    - Google Places üzerinden kontrollü firma çekmek
    - Firmaları mevcut kg_portal.db içine DEĞİL, ayrı DB içine kaydetmek
    - Günlük / aylık Google istek limitini aşmamak
    - Aynı aramayı ve aynı firmayı tekrar tekrar çekmemek
    - Sonradan AI analiz veya CRM aktarımı için temiz havuz oluşturmak

DB:
    data/google_leads_pool.db

Gerekli:
    tokenlar.env içinde GMAPS_KEY olmalı.
    lead_importer.py aynı klasörde olmalı.
"""

import os
import sys
import json
import sqlite3
import time
import re
import requests
from datetime import date, datetime

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from playwright.sync_api import sync_playwright

from lead_importer import (
    scrape_fast,
    normalize_text,
    normalize_url,
    get_item_unique_key,
    google_usage_today,
)

# ============================================================
# AYARLAR
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

# TEK DB: artik Google leadler direkt ana CRM veritabanina kaydedilecek
POOL_DB_PATH = os.path.join(DATA_DIR, "kg_portal.db")

POOL_USAGE_FILE = os.path.join(DATA_DIR, "google_pool_usage.json")

# Günlük maksimum Google Text Search isteği
DAILY_GOOGLE_REQUEST_LIMIT = 30

# Aylık güvenli limit. 1000 hak varsa 900 güvenli kalır.
MONTHLY_GOOGLE_REQUEST_LIMIT = 900

# Her Google Text Search isteği pageSize=20 döndürür.
# 30 istek ≈ teorik 600 firma.
GOOGLE_RESULTS_PER_REQUEST = 20

# Her iş arasında küçük bekleme. Çok agresif çalışmasın.
WAIT_SECONDS_BETWEEN_JOBS = 2

# Mail arama açık mı?
ENABLE_EMAIL_SCRAPE = True


# ============================================================
# ENV LADEN
# Lokal tokenlar.env / Render Environment
# ============================================================

if load_dotenv:
    try:
        load_dotenv(os.path.join(BASE_DIR, "tokenlar.env"))
    except Exception:
        pass


# ============================================================
# GOOGLE PLACES TEXT SEARCH
# get_leads_from_google artik bu dosyanin icinde.
# lead_importer.py icinden beklemiyoruz.
# ============================================================

def parse_google_address(formatted_address):
    text = normalize_text(formatted_address)
    strasse = ""
    plz = ""
    stadt = ""

    if not text:
        return strasse, plz, stadt

    parts = [p.strip() for p in text.split(",") if p.strip()]

    if parts:
        strasse = parts[0]

    for part in parts[1:]:
        match = re.search(r"\b(\d{5})\b\s+(.+)", part)
        if match:
            plz = match.group(1).strip()
            stadt = match.group(2).strip()
            break

    if not plz:
        match = re.search(r"\b(\d{5})\b\s+([A-Za-zÄÖÜäöüß\-\s]+)", text)
        if match:
            plz = match.group(1).strip()
            stadt = match.group(2).strip()

    stadt = stadt.replace("Germany", "").replace("Deutschland", "").strip(" ,")

    return strasse, plz, stadt


def get_leads_from_google(suchwort="", stadt="", max_results=20, plz=""):
    """
    Google Places Text Search.
    Pro Aufruf = 1 Google Text Search Request.
    """

    api_key = os.getenv("GMAPS_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GMAPS_KEY fehlt. Render Environment içinde GMAPS_KEY yok.")

    suchwort = normalize_text(suchwort)
    stadt = normalize_text(stadt)
    plz = normalize_text(plz)

    query_parts = [suchwort, plz, stadt]
    text_query = " ".join([p for p in query_parts if p]).strip()

    if not text_query:
        return []

    url = "https://places.googleapis.com/v1/places:searchText"

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "places.id,"
            "places.displayName,"
            "places.formattedAddress,"
            "places.nationalPhoneNumber,"
            "places.websiteUri,"
            "places.googleMapsUri,"
            "places.rating,"
            "places.userRatingCount"
        )
    }

    payload = {
        "textQuery": text_query,
        "languageCode": "de",
        "regionCode": "DE",
        "pageSize": int(max_results or 20)
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)

    if response.status_code >= 400:
        raise RuntimeError(
            f"Google Places Fehler {response.status_code}: {response.text[:700]}"
        )

    data = response.json()
    places = data.get("places", []) or []

    leads = []

    for place in places:
        display_name = place.get("displayName", {}) or {}
        firma = normalize_text(display_name.get("text", "") or "")

        formatted_address = normalize_text(place.get("formattedAddress", "") or "")
        strasse, found_plz, found_stadt = parse_google_address(formatted_address)

        if not firma:
            continue

        leads.append({
            "firma": firma,
            "strasse": strasse,
            "plz": found_plz,
            "stadt": found_stadt or stadt,
            "telefon": normalize_text(place.get("nationalPhoneNumber", "") or ""),
            "email": "",
            "website": normalize_url(place.get("websiteUri", "") or ""),
            "google_place_id": normalize_text(place.get("id", "") or ""),
            "google_maps_url": normalize_text(place.get("googleMapsUri", "") or ""),
            "rating": normalize_text(place.get("rating", "") or ""),
            "user_rating_count": normalize_text(place.get("userRatingCount", "") or ""),
        })

    return leads

# ============================================================
# ARAMA KUYRUĞU
# Gastronomi yok.
# Buraya yeni şehir / meslek eklenebilir.
# 12 kutunun tamamı dahildir.
# ============================================================

SEARCH_PLAN = [
    # 1 - Büro & Verwaltung
    {"branche_id": "1", "branche_name": "Büro & Verwaltung", "suchwort": "Rechtsanwalt", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "1", "branche_name": "Büro & Verwaltung", "suchwort": "Steuerberater", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "1", "branche_name": "Büro & Verwaltung", "suchwort": "Notar", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "1", "branche_name": "Büro & Verwaltung", "suchwort": "Immobilienbüro", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "1", "branche_name": "Büro & Verwaltung", "suchwort": "Versicherungsbüro", "stadt": "Duisburg", "plz": ""},

    # 2 - Medizin & Gesundheit
    {"branche_id": "2", "branche_name": "Medizin & Gesundheit", "suchwort": "Arztpraxis", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "2", "branche_name": "Medizin & Gesundheit", "suchwort": "Zahnarztpraxis", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "2", "branche_name": "Medizin & Gesundheit", "suchwort": "Physiotherapie", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "2", "branche_name": "Medizin & Gesundheit", "suchwort": "Ergotherapie", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "2", "branche_name": "Medizin & Gesundheit", "suchwort": "Apotheke", "stadt": "Duisburg", "plz": ""},

    # 3 - Pflege & Soziales
    {"branche_id": "3", "branche_name": "Pflege & Soziales", "suchwort": "Pflegedienst", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "3", "branche_name": "Pflege & Soziales", "suchwort": "Tagespflege", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "3", "branche_name": "Pflege & Soziales", "suchwort": "Seniorenbetreuung", "stadt": "Duisburg", "plz": ""},

    # 4 - Bildung & Betreuung
    {"branche_id": "4", "branche_name": "Bildung & Betreuung", "suchwort": "Kita", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "4", "branche_name": "Bildung & Betreuung", "suchwort": "Kindergarten", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "4", "branche_name": "Bildung & Betreuung", "suchwort": "Fahrschule", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "4", "branche_name": "Bildung & Betreuung", "suchwort": "Nachhilfe", "stadt": "Duisburg", "plz": ""},

    # 5 - Einzelhandel & Verkaufsflächen / keine Gastronomie
    {"branche_id": "5", "branche_name": "Einzelhandel & Verkaufsflächen", "suchwort": "Optiker", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "5", "branche_name": "Einzelhandel & Verkaufsflächen", "suchwort": "Hörgeräteakustiker", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "5", "branche_name": "Einzelhandel & Verkaufsflächen", "suchwort": "Möbelhaus", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "5", "branche_name": "Einzelhandel & Verkaufsflächen", "suchwort": "Sanitätshaus", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "5", "branche_name": "Einzelhandel & Verkaufsflächen", "suchwort": "Küchenstudio", "stadt": "Duisburg", "plz": ""},

    # 6 - Fitness, Sport & Freizeit
    {"branche_id": "6", "branche_name": "Fitness, Sport & Freizeit", "suchwort": "Fitnessstudio", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "6", "branche_name": "Fitness, Sport & Freizeit", "suchwort": "Yoga Studio", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "6", "branche_name": "Fitness, Sport & Freizeit", "suchwort": "Tanzschule", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "6", "branche_name": "Fitness, Sport & Freizeit", "suchwort": "Kampfsportschule", "stadt": "Duisburg", "plz": ""},

    # 7 - Industrie & Produktion
    {"branche_id": "7", "branche_name": "Industrie & Produktion", "suchwort": "Produktionshalle", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "7", "branche_name": "Industrie & Produktion", "suchwort": "Maschinenbau", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "7", "branche_name": "Industrie & Produktion", "suchwort": "Metallverarbeitung", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "7", "branche_name": "Industrie & Produktion", "suchwort": "Druckerei", "stadt": "Duisburg", "plz": ""},

    # 8 - Lager, Logistik & Großhandel
    {"branche_id": "8", "branche_name": "Lager, Logistik & Großhandel", "suchwort": "Lagerhalle", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "8", "branche_name": "Lager, Logistik & Großhandel", "suchwort": "Logistikzentrum", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "8", "branche_name": "Lager, Logistik & Großhandel", "suchwort": "Spedition", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "8", "branche_name": "Lager, Logistik & Großhandel", "suchwort": "Großhandel", "stadt": "Duisburg", "plz": ""},

    # 9 - Immobilien & Hausverwaltung
    {"branche_id": "9", "branche_name": "Immobilien & Hausverwaltung", "suchwort": "Hausverwaltung", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "9", "branche_name": "Immobilien & Hausverwaltung", "suchwort": "WEG Verwaltung", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "9", "branche_name": "Immobilien & Hausverwaltung", "suchwort": "Immobilienverwaltung", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "9", "branche_name": "Immobilien & Hausverwaltung", "suchwort": "Wohnungsbaugesellschaft", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "9", "branche_name": "Immobilien & Hausverwaltung", "suchwort": "Immobilienmakler", "stadt": "Duisburg", "plz": ""},

    # 10 - Finanzen, Versicherung & Beratung
    {"branche_id": "10", "branche_name": "Finanzen, Versicherung & Beratung", "suchwort": "Versicherungsmakler", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "10", "branche_name": "Finanzen, Versicherung & Beratung", "suchwort": "Finanzberatung", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "10", "branche_name": "Finanzen, Versicherung & Beratung", "suchwort": "Buchhaltungsbüro", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "10", "branche_name": "Finanzen, Versicherung & Beratung", "suchwort": "Lohnbüro", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "10", "branche_name": "Finanzen, Versicherung & Beratung", "suchwort": "Unternehmensberatung", "stadt": "Duisburg", "plz": ""},

    # 11 - Handwerk, Technik & Service
    {"branche_id": "11", "branche_name": "Handwerk, Technik & Service", "suchwort": "Elektrobetrieb", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "11", "branche_name": "Handwerk, Technik & Service", "suchwort": "SHK Betrieb", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "11", "branche_name": "Handwerk, Technik & Service", "suchwort": "Malerbetrieb", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "11", "branche_name": "Handwerk, Technik & Service", "suchwort": "Autowerkstatt", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "11", "branche_name": "Handwerk, Technik & Service", "suchwort": "Gebäudetechnik", "stadt": "Duisburg", "plz": ""},

    # 12 - Sonstige Gewerbe & Dienstleister
    {"branche_id": "12", "branche_name": "Sonstige Gewerbe & Dienstleister", "suchwort": "Agentur", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "12", "branche_name": "Sonstige Gewerbe & Dienstleister", "suchwort": "Fotostudio", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "12", "branche_name": "Sonstige Gewerbe & Dienstleister", "suchwort": "Kosmetikstudio", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "12", "branche_name": "Sonstige Gewerbe & Dienstleister", "suchwort": "Friseur", "stadt": "Duisburg", "plz": ""},
    {"branche_id": "12", "branche_name": "Sonstige Gewerbe & Dienstleister", "suchwort": "Verein", "stadt": "Duisburg", "plz": ""},
]


# ============================================================
# JSON USAGE / LIMIT
# ============================================================

def load_json_file(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return default


def save_json_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def current_month_key():
    return date.today().strftime("%Y-%m")


def current_day_key():
    return date.today().isoformat()


def pool_usage_data():
    return load_json_file(POOL_USAGE_FILE, {"daily": {}, "monthly": {}})


def pool_usage_today():
    data = pool_usage_data()
    return int(data.get("daily", {}).get(current_day_key(), 0) or 0)


def pool_usage_month():
    data = pool_usage_data()
    return int(data.get("monthly", {}).get(current_month_key(), 0) or 0)


def add_pool_usage(count=1):
    data = pool_usage_data()
    data.setdefault("daily", {})
    data.setdefault("monthly", {})

    day_key = current_day_key()
    month_key = current_month_key()

    data["daily"][day_key] = int(data["daily"].get(day_key, 0) or 0) + int(count)
    data["monthly"][month_key] = int(data["monthly"].get(month_key, 0) or 0) + int(count)

    save_json_file(POOL_USAGE_FILE, data)


def remaining_daily_pool_requests():
    return max(0, DAILY_GOOGLE_REQUEST_LIMIT - pool_usage_today())


def remaining_monthly_pool_requests():
    return max(0, MONTHLY_GOOGLE_REQUEST_LIMIT - pool_usage_month())


def can_run_google_request():
    return remaining_daily_pool_requests() > 0 and remaining_monthly_pool_requests() > 0


# ============================================================
# DB SETUP
# ============================================================

def get_conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(POOL_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_pool_db():
    conn = get_conn()
    cursor = conn.cursor()

    # Ana CRM leads tablosu yoksa oluşturur. Varsa bozmaz.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            branche_id TEXT,
            branche_name TEXT,
            suchwort TEXT,
            firma TEXT,
            ansprechpartner TEXT,
            strasse TEXT,
            plz TEXT,
            stadt TEXT,
            telefon TEXT,
            email TEXT,
            website TEXT,
            quelle TEXT,
            status TEXT DEFAULT 'Neu',
            sort_order INTEGER DEFAULT 0,
            unique_key TEXT,
            google_place_id TEXT,
            google_maps_url TEXT,
            rating TEXT,
            user_rating_count TEXT,
            erstellt_am TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Eksik kolon varsa ekler. Varsa geçer.
    for col_sql in [
        "ALTER TABLE leads ADD COLUMN branche_name TEXT",
        "ALTER TABLE leads ADD COLUMN suchwort TEXT",
        "ALTER TABLE leads ADD COLUMN ansprechpartner TEXT",
        "ALTER TABLE leads ADD COLUMN sort_order INTEGER DEFAULT 0",
        "ALTER TABLE leads ADD COLUMN unique_key TEXT",
        "ALTER TABLE leads ADD COLUMN google_place_id TEXT",
        "ALTER TABLE leads ADD COLUMN google_maps_url TEXT",
        "ALTER TABLE leads ADD COLUMN rating TEXT",
        "ALTER TABLE leads ADD COLUMN user_rating_count TEXT",
        "ALTER TABLE leads ADD COLUMN erstellt_am TEXT DEFAULT CURRENT_TIMESTAMP"
    ]:
        try:
            cursor.execute(col_sql)
        except Exception:
            pass

    # Google günlük job tablosu artik ana DB icinde duracak.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS google_pool_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_key TEXT UNIQUE,
            branche_id TEXT,
            branche_name TEXT,
            suchwort TEXT,
            stadt TEXT,
            plz TEXT,
            query_text TEXT,
            status TEXT DEFAULT 'offen',
            google_requests INTEGER DEFAULT 0,
            received INTEGER DEFAULT 0,
            inserted INTEGER DEFAULT 0,
            duplicates INTEGER DEFAULT 0,
            email_found INTEGER DEFAULT 0,
            email_missing INTEGER DEFAULT 0,
            last_error TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT,
            finished_at TEXT
        )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_branche ON leads(branche_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_stadt ON leads(stadt)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_unique_key ON leads(unique_key)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_google_pool_jobs_status ON google_pool_jobs(status)")

    conn.commit()
    conn.close()


def build_query_text(job):
    parts = [
        normalize_text(job.get("suchwort", "")),
        normalize_text(job.get("plz", "")),
        normalize_text(job.get("stadt", "")),
    ]
    return " ".join([p for p in parts if p]).strip()


def build_job_key(job):
    return "|".join([
        normalize_text(job.get("branche_id", "")).lower(),
        normalize_text(job.get("branche_name", "")).lower(),
        normalize_text(job.get("suchwort", "")).lower(),
        normalize_text(job.get("stadt", "")).lower(),
        normalize_text(job.get("plz", "")).lower(),
    ])


def reset_stuck_running_jobs():
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE google_pool_jobs
        SET status = 'offen',
            started_at = NULL,
            last_error = 'Vorheriger Lauf wurde abgebrochen. Job neu geöffnet.'
        WHERE status = 'laeuft'
    """)

    reset_count = cursor.rowcount
    conn.commit()
    conn.close()

    return reset_count


def seed_jobs_from_plan():
    conn = get_conn()
    cursor = conn.cursor()

    inserted = 0

    for job in SEARCH_PLAN:
        job_data = {
            "branche_id": normalize_text(job.get("branche_id", "")),
            "branche_name": normalize_text(job.get("branche_name", "")),
            "suchwort": normalize_text(job.get("suchwort", "")),
            "stadt": normalize_text(job.get("stadt", "")),
            "plz": normalize_text(job.get("plz", "")),
        }
        job_key = build_job_key(job_data)
        query_text = build_query_text(job_data)

        cursor.execute("""
            INSERT OR IGNORE INTO google_pool_jobs
                (job_key, branche_id, branche_name, suchwort, stadt, plz, query_text, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'offen')
        """, (
            job_key,
            job_data["branche_id"],
            job_data["branche_name"],
            job_data["suchwort"],
            job_data["stadt"],
            job_data["plz"],
            query_text,
        ))

        if cursor.rowcount == 1:
            inserted += 1

    conn.commit()
    conn.close()
    return inserted


def get_next_open_jobs(limit):
    conn = get_conn()
    rows = conn.execute("""
        SELECT *
        FROM google_pool_jobs
        WHERE status = 'offen'
        ORDER BY id ASC
        LIMIT ?
    """, (int(limit),)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def mark_job_started(job_id):
    conn = get_conn()
    conn.execute("""
        UPDATE google_pool_jobs
        SET status = 'laeuft', started_at = CURRENT_TIMESTAMP, last_error = NULL
        WHERE id = ?
    """, (job_id,))
    conn.commit()
    conn.close()


def mark_job_finished(job_id, result):
    conn = get_conn()
    conn.execute("""
        UPDATE google_pool_jobs
        SET
            status = 'fertig',
            google_requests = google_requests + 1,
            received = ?,
            inserted = ?,
            duplicates = ?,
            email_found = ?,
            email_missing = ?,
            finished_at = CURRENT_TIMESTAMP,
            last_error = NULL
        WHERE id = ?
    """, (
        int(result.get("received", 0)),
        int(result.get("inserted", 0)),
        int(result.get("duplicates", 0)),
        int(result.get("email_found", 0)),
        int(result.get("email_missing", 0)),
        job_id,
    ))
    conn.commit()
    conn.close()


def mark_job_error(job_id, error_text):
    conn = get_conn()
    conn.execute("""
        UPDATE google_pool_jobs
        SET status = 'fehler', finished_at = CURRENT_TIMESTAMP, last_error = ?
        WHERE id = ?
    """, (str(error_text)[:500], job_id))
    conn.commit()
    conn.close()


def pool_lead_exists(conn, lead):
    key = get_item_unique_key(lead)
    firma = normalize_text(lead.get("firma", ""))
    telefon = normalize_text(lead.get("telefon", ""))
    website = normalize_url(lead.get("website", ""))
    google_place_id = normalize_text(lead.get("google_place_id", ""))

    if key:
        row = conn.execute("""
            SELECT id FROM leads
            WHERE unique_key = ?
            LIMIT 1
        """, (key,)).fetchone()
        if row:
            return True

    if google_place_id:
        row = conn.execute("""
            SELECT id FROM leads
            WHERE google_place_id = ?
            LIMIT 1
        """, (google_place_id,)).fetchone()
        if row:
            return True

    if firma and (telefon or website):
        row = conn.execute("""
            SELECT id FROM leads
            WHERE lower(COALESCE(firma, '')) = lower(?)
            AND (
                COALESCE(telefon, '') = ?
                OR COALESCE(website, '') = ?
            )
            LIMIT 1
        """, (firma, telefon, website)).fetchone()
        if row:
            return True

    return False


def insert_pool_lead(conn, lead, job, email=""):
    key = get_item_unique_key(lead)
    if not key:
        return False

    branche_id = normalize_text(job.get("branche_id", ""))
    branche_name = normalize_text(job.get("branche_name", ""))
    suchwort = normalize_text(job.get("suchwort", ""))

    firma = normalize_text(lead.get("firma", ""))
    strasse = normalize_text(lead.get("strasse", ""))
    plz = normalize_text(lead.get("plz", ""))
    stadt = normalize_text(lead.get("stadt", ""))
    telefon = normalize_text(lead.get("telefon", ""))
    website = normalize_url(lead.get("website", ""))
    email = normalize_text(email)

    google_place_id = normalize_text(lead.get("google_place_id", ""))
    google_maps_url = normalize_text(lead.get("google_maps_url", ""))
    rating = normalize_text(lead.get("rating", ""))
    user_rating_count = normalize_text(lead.get("user_rating_count", ""))

    # Sortierung: yeni gelen otomatik firmalar ilgili bransta listenin sonuna gelsin
    max_sort_row = conn.execute("""
        SELECT COALESCE(MAX(sort_order), 0)
        FROM leads
        WHERE branche_id = ?
    """, (branche_id,)).fetchone()

    next_sort_order = int(max_sort_row[0] or 0) + 1

    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO leads (
            unique_key,
            branche_id,
            branche_name,
            suchwort,
            firma,
            strasse,
            plz,
            stadt,
            telefon,
            email,
            website,
            quelle,
            status,
            sort_order,
            google_place_id,
            google_maps_url,
            rating,
            user_rating_count,
            erstellt_am
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Google', 'Neu', ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
    """, (
        key,
        branche_id,
        branche_name,
        suchwort,
        firma,
        strasse,
        plz,
        stadt,
        telefon,
        email,
        website,
        next_sort_order,
        google_place_id,
        google_maps_url,
        rating,
        user_rating_count
    ))

    return cursor.rowcount == 1


# ============================================================
# JOB AUSFÜHRUNG
# ============================================================

def run_one_job(job, browser_context=None):
    result = {
        "received": 0,
        "inserted": 0,
        "duplicates": 0,
        "email_found": 0,
        "email_missing": 0,
    }

    if not can_run_google_request():
        raise RuntimeError("Google Tages-/Monatslimit erreicht.")

    query_text = build_query_text(job)
    print(f"\n🔎 Google Suche: {query_text}")

    leads = get_leads_from_google(
        suchwort=job.get("suchwort", ""),
        stadt=job.get("stadt", ""),
        max_results=GOOGLE_RESULTS_PER_REQUEST,
        plz=job.get("plz", ""),
    )

    # get_leads_from_google macht intern genau 1 Google Text Search request.
    add_pool_usage(1)

    result["received"] = len(leads)

    conn = get_conn()

    try:
        for lead in leads:
            lead["branche_id"] = job.get("branche_id", "")
            lead["branche_name"] = job.get("branche_name", "")
            lead["suchwort"] = job.get("suchwort", "")

            if pool_lead_exists(conn, lead):
                result["duplicates"] += 1
                continue

            website = normalize_url(lead.get("website", ""))
            email = ""

            if ENABLE_EMAIL_SCRAPE and website and browser_context:
                page = None
                try:
                    page = browser_context.new_page()
                    page.set_default_timeout(5000)
                    page.set_default_navigation_timeout(5000)
                    found = scrape_fast(page, website)
                    if found:
                        email = found
                        result["email_found"] += 1
                    else:
                        result["email_missing"] += 1
                except Exception:
                    result["email_missing"] += 1
                finally:
                    try:
                        if page:
                            page.close()
                    except Exception:
                        pass
            else:
                result["email_missing"] += 1

            inserted = insert_pool_lead(conn, lead, job, email=email)
            if inserted:
                result["inserted"] += 1
                conn.commit()
            else:
                result["duplicates"] += 1

    finally:
        conn.commit()
        conn.close()

    print(
        f"✅ Ergebnis: received={result['received']} | inserted={result['inserted']} | "
        f"duplicate={result['duplicates']} | email={result['email_found']}"
    )

    return result


def run_daily_pool():
    init_pool_db()
    reset_jobs = reset_stuck_running_jobs()
    new_jobs = seed_jobs_from_plan()

    print("============================================================")
    print("KG GOOGLE LEAD POOL - TEK DB")
    print("============================================================")
    print(f"DB: {POOL_DB_PATH}")
    print("Ziel: kg_portal.db -> leads")
    print(f"Yeni eklenen job: {new_jobs}")
    print(f"Yarim kalan job yeniden acildi: {reset_jobs}")
    print(f"Bugunku pool Google istek: {pool_usage_today()} / {DAILY_GOOGLE_REQUEST_LIMIT}")
    print(f"Bu ay pool Google istek: {pool_usage_month()} / {MONTHLY_GOOGLE_REQUEST_LIMIT}")
    print(f"lead_importer gunluk sayac: {google_usage_today()}")

    available_today = remaining_daily_pool_requests()
    available_month = remaining_monthly_pool_requests()
    max_jobs = min(available_today, available_month, DAILY_GOOGLE_REQUEST_LIMIT)

    if max_jobs <= 0:
        print("⛔ Limit dolu. Bugün/ay bu sistem Google isteği atmayacak.")
        return

    jobs = get_next_open_jobs(max_jobs)

    if not jobs:
        print("ℹ️ Açık job yok. SEARCH_PLAN içine yeni aramalar eklenebilir.")
        return

    print(f"> Calisacak job sayisi: {len(jobs)}")

    if ENABLE_EMAIL_SCRAPE:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-http2", "--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(ignore_https_errors=True, locale="de-DE")

            def handle_route(route):
                try:
                    if route.request.resource_type in ["image", "media", "font", "stylesheet"]:
                        return route.abort()
                except Exception:
                    pass
                return route.continue_()

            try:
                context.route("**/*", handle_route)
            except Exception:
                pass

            try:
                for job in jobs:
                    if not can_run_google_request():
                        print("⛔ Limit doldu. Kalan joblar bekleyecek.")
                        break

                    mark_job_started(job["id"])
                    try:
                        result = run_one_job(job, browser_context=context)
                        mark_job_finished(job["id"], result)
                    except Exception as e:
                        print(f"❌ Job Fehler: {e}")
                        mark_job_error(job["id"], str(e))

                    time.sleep(WAIT_SECONDS_BETWEEN_JOBS)

            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    else:
        for job in jobs:
            if not can_run_google_request():
                print("⛔ Limit doldu. Kalan joblar bekleyecek.")
                break

            mark_job_started(job["id"])
            try:
                result = run_one_job(job, browser_context=None)
                mark_job_finished(job["id"], result)
            except Exception as e:
                print(f"❌ Job Fehler: {e}")
                mark_job_error(job["id"], str(e))

            time.sleep(WAIT_SECONDS_BETWEEN_JOBS)

    print("============================================================")
    print("FERTIG")
    print(f"Bugunku pool Google istek: {pool_usage_today()} / {DAILY_GOOGLE_REQUEST_LIMIT}")
    print(f"Bu ay pool Google istek: {pool_usage_month()} / {MONTHLY_GOOGLE_REQUEST_LIMIT}")
    print("============================================================")


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    run_daily_pool()
