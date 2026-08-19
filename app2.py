# =====================================================
# APP2.PY
# DATENBANK / BESICHTIGUNG / ANGEBOTVORLAGE / LEISTUNGSVERZEICHNIS
# =====================================================

from flask import render_template, request, jsonify, Response
from urllib.parse import unquote
import sqlite3
import os
import re
import shutil
import subprocess
from html import escape
from email.utils import parsedate_to_datetime
from datetime import datetime
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from lead_importer import run_apify_import

import base64
import json
from email.message import EmailMessage
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "data", "kg_portal.db")


# =====================================================
# KG-MAIL GMAIL API HILFSFUNKTIONEN
# kgmailtest.py gerekmez; her şey bu dosyanın içinde.
# =====================================================

GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

STATIC_DIR = os.path.join(BASE_DIR, "static")
RENDER_SECRET_DIR = "/etc/secrets"

def gmail_file_path(filename):
    render_path = os.path.join(RENDER_SECRET_DIR, filename)
    if os.path.exists(render_path):
        return render_path
    return os.path.join(STATIC_DIR, filename)

GMAIL_TOKEN_FILE = gmail_file_path("token.json")
GMAIL_CREDENTIALS_FILE = gmail_file_path("credentials.json")
GMAIL_DIR = os.path.dirname(GMAIL_CREDENTIALS_FILE)



def find_gmail_credentials_file():
    if os.path.exists(GMAIL_CREDENTIALS_FILE):
        return GMAIL_CREDENTIALS_FILE

    json_files = [
        f for f in os.listdir(GMAIL_DIR)
        if f.lower().endswith(".json") and f.lower() != "token.json"
    ]

    if not json_files:
        raise FileNotFoundError(
            "credentials.json bulunamadı. Dosyayı static klasörüne koy."
        )

    return os.path.join(GMAIL_DIR, json_files[0])

def get_gmail_service():
    creds = None
    credentials_path = find_gmail_credentials_file()

    if os.path.exists(GMAIL_TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_FILE, GMAIL_SCOPES)
        if creds and hasattr(creds, "has_scopes") and not creds.has_scopes(GMAIL_SCOPES):
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)

        if not GMAIL_TOKEN_FILE.startswith("/etc/secrets"):
            with open(GMAIL_TOKEN_FILE, "w", encoding="utf-8") as token:
                token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


def get_header(headers, name):
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def decode_body(data):
    if not data:
        return ""

    try:
        decoded = base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="ignore")
        return decoded.strip()
    except Exception:
        return ""


def extract_text_from_payload(payload):
    if not payload:
        return ""

    body_data = payload.get("body", {}).get("data")
    mime_type = payload.get("mimeType", "")

    if body_data and mime_type == "text/plain":
        return decode_body(body_data)

    parts = payload.get("parts", [])
    for part in parts:
        text = extract_text_from_payload(part)
        if text:
            return text

    if body_data:
        return decode_body(body_data)

    return ""



# =====================================================
# KG-MAIL GMAIL API - ORTAK PARSER / HTML / ATTACHMENT HILFSFUNKTIONEN
# Bu bölüm sonraki adımlarda INBOX / GESENDET / ENTWÜRFE / WICHTIG
# route'larını bozmadan ortak sisteme almak için eklendi.
# =====================================================

def extract_html_from_payload(payload):
    if not payload:
        return ""

    body_data = payload.get("body", {}).get("data")
    mime_type = payload.get("mimeType", "")

    if body_data and mime_type == "text/html":
        return decode_body(body_data)

    parts = payload.get("parts", [])
    for part in parts:
        html_body = extract_html_from_payload(part)
        if html_body:
            return html_body

    return ""


def get_part_header(part, name):
    for h in part.get("headers", []) or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def normalize_cid(value):
    return (value or "").strip().strip("<>").replace("cid:", "")


def clean_mail_html(html_body, message_id="", inline_images=None):
    if not html_body:
        return ""

    inline_images = inline_images or {}

    try:
        soup = BeautifulSoup(html_body, "html.parser")

        for tag in soup(["script", "style", "head", "meta", "title"]):
            tag.decompose()

        for img in soup.find_all("img"):
            src = img.get("src", "")
            if src.lower().startswith("cid:"):
                cid = normalize_cid(src)
                inline = inline_images.get(cid)
                if inline and message_id:
                    img["src"] = f"/api/gmail/attachment/{message_id}/{inline['attachmentId']}"
                    img["style"] = "max-width:100%;height:auto;"
                else:
                    img.decompose()

        for tag in soup.find_all(True):
            allowed = {}
            for key, value in list(tag.attrs.items()):
                k = key.lower()
                if k in ["href", "src", "alt", "title", "width", "height", "style", "colspan", "rowspan", "cellpadding", "cellspacing", "border", "align", "valign"]:
                    allowed[key] = value
            tag.attrs = allowed

            if tag.name == "a" and tag.get("href"):
                tag["target"] = "_blank"
                tag["rel"] = "noopener noreferrer"

        return str(soup)
    except Exception:
        return ""


def extract_inline_images_from_payload(payload):
    inline_images = {}

    def walk_parts(part):
        filename = part.get("filename", "") or ""
        mime_type = part.get("mimeType", "") or ""
        body = part.get("body", {}) or {}
        attachment_id = body.get("attachmentId", "") or ""
        content_id = normalize_cid(get_part_header(part, "Content-ID"))
        disposition = (get_part_header(part, "Content-Disposition") or "").lower()

        is_inline = "inline" in disposition or bool(content_id)
        is_image = mime_type.lower().startswith("image/")

        if attachment_id and content_id and is_image and is_inline:
            inline_images[content_id] = {
                "filename": filename,
                "mimeType": mime_type,
                "attachmentId": attachment_id,
                "size": body.get("size", 0)
            }

        for child in part.get("parts", []) or []:
            walk_parts(child)

    if payload:
        walk_parts(payload)

    return inline_images


def extract_attachments_from_payload(payload):
    attachments = []

    def walk_parts(part):
        filename = part.get("filename", "") or ""
        mime_type = part.get("mimeType", "") or ""
        body = part.get("body", {}) or {}
        attachment_id = body.get("attachmentId", "") or ""
        content_id = normalize_cid(get_part_header(part, "Content-ID"))
        disposition = (get_part_header(part, "Content-Disposition") or "").lower()

        is_inline = "inline" in disposition or bool(content_id)
        is_signature_image = mime_type.lower().startswith("image/") and is_inline

        if filename and attachment_id and not is_signature_image:
            attachments.append({
                "filename": filename,
                "mimeType": mime_type,
                "attachmentId": attachment_id,
                "size": body.get("size", 0)
            })

        for child in part.get("parts", []) or []:
            walk_parts(child)

    if payload:
        walk_parts(payload)

    return attachments

def gmail_alias_key(raw_address):
    text = (raw_address or "").lower()

    if "m.kicci@kg-reinigung.de" in text:
        return "murat"

    if "bestellung@kg-reinigung.de" in text:
        return "bestellung"

    if "rechnung@kg-reinigung.de" in text:
        return "rechnung"

    if "info@kg-reinigung.de" in text:
        return "info"

    return "info"


def build_gmail_mail_object(full_msg, index, box_name, tag_text, tag_class):
    payload = full_msg.get("payload", {})
    headers = payload.get("headers", [])
    message_id = full_msg.get("id", str(index))

    sender_raw = get_header(headers, "From")
    subject = get_header(headers, "Subject") or "(Ohne Betreff)"
    date_raw = get_header(headers, "Date")
    to_raw = get_header(headers, "To")
    cc_raw = get_header(headers, "Cc")
    delivered_to_raw = get_header(headers, "Delivered-To")
    alias_raw = " ".join([to_raw, cc_raw, delivered_to_raw])

    try:
        date_text = parsedate_to_datetime(date_raw).strftime("%d.%m.%Y %H:%M")
    except Exception:
        date_text = date_raw or ""

    snippet = full_msg.get("snippet", "") or ""

    html_body = extract_html_from_payload(payload)
    text_body = extract_text_from_payload(payload) or snippet
    inline_images = extract_inline_images_from_payload(payload)

    if html_body:
        body_html = clean_mail_html(html_body, message_id=message_id, inline_images=inline_images)
    else:
        body_clean = (text_body or snippet or "").replace("\r", "").replace("\n\n", "\n").strip()
        body_html = "<p>" + escape(body_clean).replace("\n", "<br>") + "</p>"

    email_match = re.search(r"<([^>]+)>", sender_raw)
    sender_email = email_match.group(1) if email_match else sender_raw

    sender_name = sender_raw.split("<")[0].strip().replace('"', "")
    if not sender_name:
        sender_name = sender_email

    label_ids = full_msg.get("labelIds", [])
    unread = "UNREAD" in label_ids
    attachments = extract_attachments_from_payload(payload)

    internal_date = 0
    try:
        internal_date = int(full_msg.get("internalDate", 0) or 0)
    except Exception:
        internal_date = 0

    return {
        "id": message_id,
        "gmail_id": message_id,
        "internal_date": internal_date,
        "box": box_name,
        "alias": gmail_alias_key(alias_raw),
        "label": "gmail",
        "unread": unread,
        "attach": len(attachments) > 0,
        "has_attachment": len(attachments) > 0,
        "company": sender_name,
        "from": sender_name,
        "email": sender_email,
        "to": to_raw,
        "subject": subject,
        "time": date_text,
        "tag": tag_text,
        "tagClass": tag_class,
        "preview": snippet[:180],
        "body": body_html,
        "body_html": body_html,
        "files": [a.get("filename", "") for a in attachments],
        "attachments": attachments
    }

# =====================================================
# KG-MAIL CACHE / OKUNDU / HIZLI SENKRON HILFSFUNKTIONEN
# Gmail full body ve attachment metadata aynı şekilde korunur.
# Attachment/PDF route'una dokunmaz.
# =====================================================

def ensure_gmail_cache_table():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS gmail_mail_cache (
            gmail_id TEXT PRIMARY KEY,
            box TEXT,
            internal_date INTEGER DEFAULT 0,
            subject TEXT,
            sender TEXT,
            email TEXT,
            time_text TEXT,
            unread INTEGER DEFAULT 0,
            payload_json TEXT NOT NULL,
            synced_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    try:
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gmail_cache_box_date ON gmail_mail_cache(box, internal_date DESC)")
    except Exception:
        pass
    conn.commit()
    conn.close()

def gmail_cache_get_ids(box_name, ids):
    if not ids:
        return set()
    ensure_gmail_cache_table()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    qmarks = ",".join(["?"] * len(ids))
    rows = cursor.execute(
        f"SELECT gmail_id FROM gmail_mail_cache WHERE box = ? AND gmail_id IN ({qmarks})",
        [box_name] + ids
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}

def gmail_cache_upsert(mail):
    ensure_gmail_cache_table()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO gmail_mail_cache
            (gmail_id, box, internal_date, subject, sender, email, time_text, unread, payload_json, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(gmail_id) DO UPDATE SET
            box=excluded.box,
            internal_date=excluded.internal_date,
            subject=excluded.subject,
            sender=excluded.sender,
            email=excluded.email,
            time_text=excluded.time_text,
            unread=excluded.unread,
            payload_json=excluded.payload_json,
            synced_at=CURRENT_TIMESTAMP
    """, (
        mail.get("gmail_id") or mail.get("id"),
        mail.get("box", "inbox"),
        int(mail.get("internal_date", 0) or 0),
        mail.get("subject", ""),
        mail.get("from", ""),
        mail.get("email", ""),
        mail.get("time", ""),
        1 if mail.get("unread") else 0,
        json.dumps(mail, ensure_ascii=False)
    ))
    conn.commit()
    conn.close()

def gmail_cache_fetch(box_name, limit=25, offset=0):
    ensure_gmail_cache_table()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    total = conn.execute(
        "SELECT COUNT(*) FROM gmail_mail_cache WHERE box = ?",
        (box_name,)
    ).fetchone()[0]
    rows = conn.execute("""
        SELECT payload_json
        FROM gmail_mail_cache
        WHERE box = ?
        ORDER BY internal_date DESC, gmail_id DESC
        LIMIT ? OFFSET ?
    """, (box_name, limit, offset)).fetchall()
    conn.close()
    mails = []
    for row in rows:
        try:
            mails.append(json.loads(row["payload_json"]))
        except Exception:
            pass
    next_token = f"cache:{offset + limit}" if (offset + limit) < total else ""
    return mails, next_token, total

def gmail_cache_count(box_name):
    ensure_gmail_cache_table()
    conn = sqlite3.connect(DB_PATH)
    count = conn.execute(
        "SELECT COUNT(*) FROM gmail_mail_cache WHERE box = ?",
        (box_name,)
    ).fetchone()[0]
    conn.close()
    return int(count or 0)

def gmail_label_for_box(box_name):
    return {
        "inbox": "INBOX",
        "sent": "SENT",
        "draft": "DRAFT",
        "star": "STARRED",
        "spam": "SPAM"
    }.get(box_name, "INBOX")

def gmail_tag_for_box(box_name):
    if box_name == "sent":
        return "Gesendet", "green"
    if box_name == "draft":
        return "Entwurf", "orange"
    if box_name == "star":
        return "Wichtig", "purple"
    if box_name == "spam":
        return "Junk", "orange"
    return "Gmail", "blue"

def gmail_sync_box_to_cache(service, box_name="inbox", max_results=25, force=False):
    label_id = gmail_label_for_box(box_name)
    tag_text, tag_class = gmail_tag_for_box(box_name)

    if box_name == "draft":
        result = service.users().drafts().list(userId="me", maxResults=max_results).execute()
        draft_items = result.get("drafts", []) or []
        ids = [d.get("message", {}).get("id") for d in draft_items if d.get("message", {}).get("id")]
        existing = gmail_cache_get_ids(box_name, ids)
        new_count = 0
        for index, draft in enumerate(draft_items, start=1):
            msg_id = draft.get("message", {}).get("id")
            if not msg_id:
                continue
            if msg_id in existing and not force:
                continue
            draft_full = service.users().drafts().get(userId="me", id=draft["id"], format="full").execute()
            full_msg = draft_full.get("message", {})
            mail = build_gmail_mail_object(full_msg, index, box_name, tag_text, tag_class)
            mail["draft_id"] = draft["id"]
            gmail_cache_upsert(mail)
            if msg_id not in existing:
                new_count += 1
        return new_count

    result = service.users().messages().list(userId="me", labelIds=[label_id], maxResults=max_results).execute()
    msg_items = result.get("messages", []) or []
    ids = [m.get("id") for m in msg_items if m.get("id")]
    existing = gmail_cache_get_ids(box_name, ids)
    new_count = 0
    for index, msg in enumerate(msg_items, start=1):
        msg_id = msg.get("id")
        if not msg_id:
            continue
        if msg_id in existing and not force:
            continue
        full_msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        mail = build_gmail_mail_object(full_msg, index, box_name, tag_text, tag_class)
        gmail_cache_upsert(mail)
        if msg_id not in existing:
            new_count += 1
    return new_count


def gmail_ensure_cache_size(service, box_name="inbox", needed_count=25):
    """Scroll aşağı indikçe cache'i Gmail'den sayfa sayfa büyütür.
    İlk 25'ten sonra 50, 75, 100... şeklinde eski mailleri çeker.
    Mevcut PDF/attachment sistemine dokunmaz.
    """
    ensure_gmail_cache_table()

    try:
        needed_count = int(needed_count or 25)
    except Exception:
        needed_count = 25

    if needed_count < 25:
        needed_count = 25

    cached_count = gmail_cache_count(box_name)
    if cached_count >= needed_count:
        return 0

    label_id = gmail_label_for_box(box_name)
    tag_text, tag_class = gmail_tag_for_box(box_name)
    new_count = 0

    if box_name == "draft":
        result = service.users().drafts().list(userId="me", maxResults=min(100, needed_count)).execute()
        draft_items = result.get("drafts", []) or []
        ids = [d.get("message", {}).get("id") for d in draft_items if d.get("message", {}).get("id")]
        existing = gmail_cache_get_ids(box_name, ids)
        for index, draft in enumerate(draft_items, start=1):
            if gmail_cache_count(box_name) >= needed_count:
                break
            msg_id = draft.get("message", {}).get("id")
            if not msg_id or msg_id in existing:
                continue
            draft_full = service.users().drafts().get(userId="me", id=draft["id"], format="full").execute()
            full_msg = draft_full.get("message", {})
            mail = build_gmail_mail_object(full_msg, index, box_name, tag_text, tag_class)
            mail["draft_id"] = draft["id"]
            gmail_cache_upsert(mail)
            new_count += 1
        return new_count

    page_token = None
    fetched_total = 0

    while gmail_cache_count(box_name) < needed_count:
        list_call = service.users().messages().list(
            userId="me",
            labelIds=[label_id],
            maxResults=25,
            pageToken=page_token
        )
        result = list_call.execute()
        msg_items = result.get("messages", []) or []
        if not msg_items:
            break

        ids = [m.get("id") for m in msg_items if m.get("id")]
        existing = gmail_cache_get_ids(box_name, ids)

        for msg in msg_items:
            msg_id = msg.get("id")
            if not msg_id:
                continue
            fetched_total += 1
            if msg_id in existing:
                continue
            full_msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
            mail = build_gmail_mail_object(full_msg, fetched_total, box_name, tag_text, tag_class)
            gmail_cache_upsert(mail)
            new_count += 1

            if gmail_cache_count(box_name) >= needed_count:
                break

        page_token = result.get("nextPageToken")
        if not page_token:
            break

    return new_count


def gmail_box_response(box_name="inbox", force=False):
    try:
        ensure_gmail_cache_table()
        page_token = request.args.get("pageToken", "").strip()
        refresh = str(request.args.get("refresh", "")).strip() == "1" or force
        page_size = 25
        new_count = 0

        # ÖNEMLİ:
        # Cache'te bulunan eski mailler için Gmail API'ye hiç gitme.
        # Böylece scroll sırasında bellekte olan sayfalar anında açılır.
        if page_token.startswith("cache:"):
            try:
                offset = int(page_token.split(":", 1)[1])
            except Exception:
                offset = 0

            mails, next_token, total = gmail_cache_fetch(box_name, page_size, offset)
            if len(mails) == page_size:
                return jsonify({
                    "ok": True,
                    "fromCache": True,
                    "newCount": 0,
                    "count": len(mails),
                    "total": total,
                    "messages": mails,
                    "nextPageToken": f"cache:{offset + page_size}"
                })

            # Sadece cache'te bu sayfa eksikse Gmail'den eski mailleri tamamla.
            service = get_gmail_service()
            gmail_ensure_cache_size(service, box_name=box_name, needed_count=offset + page_size)

            mails, next_token, total = gmail_cache_fetch(box_name, page_size, offset)
            if len(mails) == page_size:
                next_token = f"cache:{offset + page_size}"
            else:
                next_token = ""

            return jsonify({
                "ok": True,
                "fromCache": True,
                "newCount": 0,
                "count": len(mails),
                "total": total,
                "messages": mails,
                "nextPageToken": next_token
            })

        # İlk sayfa cache'te varsa Gmail API'ye gitmeden direkt göster.
        # refresh/sync gelirse sadece yeni ID var mı diye bakılır; mevcut eski mailler tekrar çekilmez.
        cached_count = gmail_cache_count(box_name)
        if refresh or cached_count == 0:
            service = get_gmail_service()
            new_count = gmail_sync_box_to_cache(
                service,
                box_name=box_name,
                max_results=page_size,
                force=False
            )

        mails, next_token, total = gmail_cache_fetch(box_name, page_size, 0)
        if len(mails) == page_size:
            next_token = f"cache:{page_size}"
        else:
            next_token = ""

        return jsonify({
            "ok": True,
            "fromCache": True,
            "newCount": new_count,
            "count": len(mails),
            "total": total,
            "messages": mails,
            "nextPageToken": next_token
        })
    except Exception as e:
        return jsonify({"ok": False, "message": str(e)}), 500

def ensure_tagesliste_table():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
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
            quelle TEXT,
            status TEXT DEFAULT 'offen',
            notiz TEXT,
            spaeter_datum TEXT,
            company_key TEXT UNIQUE,
            erstellt_am TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tagesliste_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tagesliste_id INTEGER NOT NULL,
            status TEXT NOT NULL,
            datum TEXT DEFAULT (date('now','localtime')),
            erstellt_am TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(tagesliste_id, status, datum)
        )
    """)
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
        cursor.execute("ALTER TABLE tagesliste_leads ADD COLUMN source_lead_id INTEGER")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE tagesliste_leads ADD COLUMN notiz TEXT")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE tagesliste_leads ADD COLUMN spaeter_datum TEXT")
    except Exception:
        pass

    # Tagesliste: Anrufen ve Interessiert tıklanma zamanı
    try:
        cursor.execute("ALTER TABLE tagesliste_leads ADD COLUMN angerufen_am TEXT")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE tagesliste_leads ADD COLUMN interessiert_am TEXT")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE tagesliste_status_backup ADD COLUMN angerufen_am TEXT")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE tagesliste_status_backup ADD COLUMN interessiert_am TEXT")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE tagesliste_leads ADD COLUMN sort_order INTEGER DEFAULT 0")
    except Exception:
        pass

    # BESICHTIGUNG -> ANGEBOT veri kaydı
    try:
        cursor.execute("ALTER TABLE tagesliste_leads ADD COLUMN besichtigung_data_json TEXT")
    except Exception:
        pass

    # 4 sayfalık Angebot şablonuna basılacak hazır değişkenler
    try:
        cursor.execute("ALTER TABLE tagesliste_leads ADD COLUMN angebot_vars_json TEXT")
    except Exception:
        pass

    # Angebot numarası ve değiştirilebilir teklif tarihi
    try:
        cursor.execute("ALTER TABLE tagesliste_leads ADD COLUMN angebot_nr TEXT")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE tagesliste_leads ADD COLUMN angebot_datum TEXT")
    except Exception:
        pass

    # Hesaplama sonucu
    try:
        cursor.execute("ALTER TABLE tagesliste_leads ADD COLUMN angebot_netto REAL DEFAULT 0")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE tagesliste_leads ADD COLUMN angebot_mwst REAL DEFAULT 0")
    except Exception:
        pass

    try:
        cursor.execute("ALTER TABLE tagesliste_leads ADD COLUMN angebot_brutto REAL DEFAULT 0")
    except Exception:
        pass

    conn.commit()
    conn.close()


# =====================================================
# APP2 - ANGEBOT HESAPLAMA HILFSFUNKTIONEN
# Bu bölüm şimdilik SADECE hazırlık.
# Henüz Speichern/Senden akışına bağlanmadı.
# =====================================================

ANGEBOT_LEISTUNGSWERTE = {
    "buero": 170,     # Büro: 170 m² / saat
    "wc": 55,         # WC: 55 m² / saat
    "kueche": 110,    # Küche: 110 m² / saat
    "flur": 250       # Flur: 250 m² / saat
}

ANGEBOT_MONATSFAKTOR = 4.33
ANGEBOT_STUNDENSATZ = 35.0



def angebot_parse_number(value):
    text = str(value or "").strip().replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except Exception:
        return 0.0


def angebot_parse_weekly_frequency(value):
    """
    Örnek:
    '1x wöchentlich' -> 1
    '3x pro Woche'   -> 3
    '5'              -> 5
    boş              -> 0
    """
    text = str(value or "").strip().lower()
    match = re.search(r"(\d+)", text)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except Exception:
        return 0


def angebot_area_key(value):
    text = str(value or "").strip().lower()

    if (
        "büro" in text
        or "buero" in text
        or "büroraum" in text
        or "büroraum" in text
        or "besprechungsraum" in text
        or "serverraum" in text
        or "arbeitsplatz" in text
    ):
        return "buero"

    if "wc" in text or "sanitär" in text or "sanitaer" in text:
        return "wc"

    if "küche" in text or "kueche" in text:
        return "kueche"

    if "flur" in text or "gang" in text:
        return "flur"

    return ""


def angebot_format_euro(value):
    try:
        value = float(value or 0)
    except Exception:
        value = 0.0

    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


def angebot_build_leistungsart_text(leistungen):
    clean = []

    for item in leistungen or []:
        text = str(item or "").strip()
        text = text.replace(" Details", "").strip()

        if text.lower() == "leistungen":
            continue

        if text and text not in clean:
            clean.append(text)

    if not clean:
        return ""

    if len(clean) == 1:
        return clean[0]

    if len(clean) == 2:
        return clean[0] + " und " + clean[1]

    return ", ".join(clean[:-1]) + " und " + clean[-1]


def angebot_calculate_from_besichtigung(besichtigung_data):
    """
    4 sayfalık Angebot için temel m² hesaplama motoru.
    Şimdilik sadece Büro / WC / Küche / Flur hesaplar.
    Extras sonraki adımda eklenecek.
    """
    if not isinstance(besichtigung_data, dict):
        besichtigung_data = {}

    raeume = besichtigung_data.get("raeume") or []
    if not isinstance(raeume, list):
        raeume = []

    details = []
    total_monat_stunden = 0.0

    for row in raeume:
        if not isinstance(row, dict):
            continue

        typ_text = " ".join([
            str(row.get("typ") or ""),
            str(row.get("name") or "")
        ])

        area_key = angebot_area_key(typ_text)

        if not area_key:
            area_key = angebot_area_key(str(row.get("section") or ""))

        if area_key not in ANGEBOT_LEISTUNGSWERTE:
            continue

        m2 = angebot_parse_number(row.get("m2"))
        frequenz = angebot_parse_weekly_frequency(row.get("haeufigkeit"))

        if m2 <= 0 or frequenz <= 0:
            continue

        leistungswert = ANGEBOT_LEISTUNGSWERTE[area_key]

        stunden_pro_einsatz = m2 / leistungswert
        monat_stunden = stunden_pro_einsatz * frequenz * ANGEBOT_MONATSFAKTOR

        total_monat_stunden += monat_stunden

        details.append({
            "bereich": area_key,
            "m2": round(m2, 2),
            "frequenz": frequenz,
            "leistungswert": leistungswert,
            "stunden_pro_einsatz": round(stunden_pro_einsatz, 2),
            "monat_stunden": round(monat_stunden, 2)
        })

    netto = total_monat_stunden * ANGEBOT_STUNDENSATZ
    mwst = netto * 0.19
    brutto = netto + mwst

    leistungsart_text = angebot_build_leistungsart_text(
        besichtigung_data.get("leistungen") or []
    )

    if not leistungsart_text:
        leistungsart_text = "Unterhaltsreinigung"

    return {
        "details": details,
        "monat_stunden": round(total_monat_stunden, 2),
        "stundensatz": ANGEBOT_STUNDENSATZ,
        "netto": round(netto, 2),
        "mwst": round(mwst, 2),
        "brutto": round(brutto, 2),
        "leistung_1": leistungsart_text,
        "einheiten_1": "monatlich",
        "preis_1": angebot_format_euro(netto)
    }


def angebot_today_de():
    from datetime import datetime
    return datetime.now().strftime("%d.%m.%Y")


def angebot_next_number(cursor):
    rows = cursor.execute("""
        SELECT angebot_nr
        FROM tagesliste_leads
        WHERE angebot_nr IS NOT NULL
        AND angebot_nr != ''
    """).fetchall()

    # Yeni doğru Angebot-Nummer sistemi:
    # İlk yeni numara AN-2265 olacak.
    # 2165 gibi hatalı eski numaralar dikkate alınmayacak.
    max_nr = 2264

    for row in rows:
        raw = str(row[0] or "")
        match = re.search(r"(\d+)", raw)

        if match:
            try:
                nummer = int(match.group(1))

                # Sadece yeni doğru seri dikkate alınır: 2265 ve sonrası.
                if nummer >= 2265 and nummer > max_nr:
                    max_nr = nummer

            except Exception:
                pass

    return str(max_nr + 1)


def angebot_extract_ausfuehrungszeitraum(besichtigung_data):
    if not isinstance(besichtigung_data, dict):
        return ""

    # Şimdilik Starttermin varsa onu yakalamaya çalışır.
    # Bulamazsa boş bırakır; sonra istersen formatı netleştiririz.
    sonstiges = besichtigung_data.get("sonstiges") or []

    if isinstance(sonstiges, list):
        for item in sonstiges:
            if not isinstance(item, dict):
                continue

            label = str(item.get("label") or "").lower()
            values = item.get("values") or []

            if "start" in label or "beginn" in label:
                if values:
                    return str(values[0] or "").strip()

    return ""


def angebot_build_template_vars(besichtigung_data, berechnung, nr, datum):
    if not isinstance(besichtigung_data, dict):
        besichtigung_data = {}

    if not isinstance(berechnung, dict):
        berechnung = {}

    kunde = besichtigung_data.get("kunde") or {}
    if not isinstance(kunde, dict):
        kunde = {}

    firma = str(kunde.get("firma") or "").strip()
    ansprechpartner = str(kunde.get("ansprechpartner") or "").strip()
    adresse = str(kunde.get("strasse") or "").strip()
    plz = str(kunde.get("plz") or "").strip()
    ort = str(kunde.get("ort") or "").strip()

    leistungsart = angebot_build_leistungsart_text(
        besichtigung_data.get("leistungen") or []
    )

    if not leistungsart:
        leistungsart = berechnung.get("leistung_1") or "Unterhaltsreinigung"

    ausfuehrungszeitraum = angebot_extract_ausfuehrungszeitraum(besichtigung_data)

    kunde_text = firma
    if ansprechpartner:
        kunde_text = firma + "<br>" + ansprechpartner

    return {
        "Nr": str(nr or "").strip(),
        "Datum": str(datum or "").strip(),

        "Kunde": kunde_text,
        "Ansprechpartner": ansprechpartner,
        "Objekt": "",
        "Adresse": adresse,
        "Plz": plz,
        "Ort": ort,
        "Leistungsart": leistungsart,

        "Leistung_1": berechnung.get("leistung_1") or leistungsart,
        "Einheiten_1": berechnung.get("einheiten_1") or "monatlich pauschal",
        "Preis_1": berechnung.get("preis_1") or "0,00 €",

        "Leistung_2": "",
        "Einheiten_2": "",
        "Preis_2": "",

        "Leistung_3": "",
        "Einheiten_3": "",
        "Preis_3": "",

        "Leistung_4": "",
        "Einheiten_4": "",
        "Preis_4": "",

        "Ausführungszeitraum": ausfuehrungszeitraum
    }


def register_app2_routes(app, login_required):

    @app.route("/datenbank/kalender-mini")
    @login_required
    def app2_datenbank_kalender_mini():
        return render_template(
            "kalender.html",
            kalender_mini=True,
            kalender_source=request.args.get("source", ""),
            tagesliste_id=request.args.get("tagesliste_id", ""),
            firma=request.args.get("firma", ""),
            ansprechpartner=request.args.get("ansprechpartner", ""),
            telefon=request.args.get("telefon", ""),
            email=request.args.get("email", ""),
            adresse=request.args.get("adresse", ""),
            ort=request.args.get("ort", "")
        )

# =====================================================
# APP2 - KG-MAIL API - COUNTS
# =====================================================

    @app.route("/api/gmail/counts")
    @login_required
    def app2_gmail_counts():
        try:
            service = get_gmail_service()

            def exact_message_count(label_id):
                total = 0
                page_token = None

                while True:
                    request_call = service.users().messages().list(
                        userId="me",
                        labelIds=[label_id],
                        maxResults=500,
                        pageToken=page_token
                    )

                    result = request_call.execute()
                    items = result.get("messages", []) or []
                    total += len(items)

                    page_token = result.get("nextPageToken")
                    if not page_token:
                        break

                return total

            def exact_query_id_count(*queries):
                ids = set()

                for query_text in queries:
                    page_token = None

                    while True:
                        request_call = service.users().messages().list(
                            userId="me",
                            q=query_text,
                            maxResults=500,
                            pageToken=page_token
                        )

                        result = request_call.execute()
                        items = result.get("messages", []) or []

                        for item in items:
                            msg_id = item.get("id")
                            if msg_id:
                                ids.add(msg_id)

                        page_token = result.get("nextPageToken")
                        if not page_token:
                            break

                return len(ids)

            def exact_draft_count():
                total = 0
                page_token = None

                while True:
                    request_call = service.users().drafts().list(
                        userId="me",
                        maxResults=500,
                        pageToken=page_token
                    )

                    result = request_call.execute()
                    items = result.get("drafts", []) or []
                    total += len(items)

                    page_token = result.get("nextPageToken")
                    if not page_token:
                        break

                return total

            counts = {
                "inbox": exact_message_count("INBOX"),
                "sent": exact_message_count("SENT"),
                "draft": exact_draft_count(),
                "star": exact_message_count("STARRED"),
                "spam": exact_message_count("SPAM")
            }

            aliases = {
                "info": exact_query_id_count(
                    "in:inbox to:info@kg-reinigung.de",
                    "in:inbox deliveredto:info@kg-reinigung.de"
                ),
                "murat": exact_query_id_count(
                    "in:inbox to:m.kicci@kg-reinigung.de",
                    "in:inbox deliveredto:m.kicci@kg-reinigung.de"
                ),
                "bestellung": exact_query_id_count(
                    "in:inbox to:bestellung@kg-reinigung.de",
                    "in:inbox deliveredto:bestellung@kg-reinigung.de"
                ),
                "rechnung": exact_query_id_count(
                    "in:inbox to:rechnung@kg-reinigung.de",
                    "in:inbox deliveredto:rechnung@kg-reinigung.de"
                )
            }

            return jsonify({
                "ok": True,
                "counts": counts,
                "aliases": aliases
            })

        except Exception as e:
            return jsonify({
                "ok": False,
                "counts": {
                    "inbox": 0,
                    "sent": 0,
                    "draft": 0,
                    "star": 0,
                    "spam": 0
                },
                "aliases": {
                    "info": 0,
                    "murat": 0,
                    "bestellung": 0,
                    "rechnung": 0
                },
                "message": str(e)
            }), 500

# =====================================================
# APP2 - KG-MAIL MODUL
# =====================================================

    @app.route("/gmail")
    @app.route("/gmail.html")
    @login_required
    def app2_gmail():
        return render_template("gmail.html")

    @app.route("/datenbank/mail-mini")
    @login_required
    def app2_gmail_mini():
        return render_template(
            "gmail_mini.html",
            empfaenger=request.args.get("empfaenger", ""),
            firma=request.args.get("firma", ""),
            ansprechpartner=request.args.get("ansprechpartner", ""),
            datum=request.args.get("datum", ""),
            uhrzeit=request.args.get("uhrzeit", ""),
            adresse=request.args.get("adresse", ""),
            ort=request.args.get("ort", ""),
            telefon=request.args.get("telefon", ""),

            modus=request.args.get("modus", ""),
            angebot_id=request.args.get("angebot_id", ""),
            angebot_nr=request.args.get("angebot_nr", ""),
            angebot_datum=request.args.get("angebot_datum", ""),
            angebot_netto=request.args.get("angebot_netto", ""),
            leistungsart=request.args.get("leistungsart", "")
        )

# =====================================================
# APP2 - ANGEBOT + LEISTUNGSVERZEICHNIS PDF ANHÄNGE
# Mail Mini için iki PDF üretir:
# 1) Angebot PDF
# 2) Leistungsverzeichnis PDF
# =====================================================

    @app.route("/api/datenbank/angebot-mail-attachments")
    @login_required
    def app2_angebot_mail_attachments():
        angebot_id = request.args.get("angebot_id", "").strip()

        if not angebot_id:
            return jsonify({
                "ok": False,
                "message": "angebot_id fehlt."
            }), 400

        try:
            angebot_id_int = int(angebot_id)
        except Exception:
            angebot_id_int = 0

        if angebot_id_int <= 0:
            return jsonify({
                "ok": False,
                "message": "Ungültige Angebot-ID."
            }), 400

        try:
            from playwright.sync_api import sync_playwright

            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row

            row = conn.execute("""
                SELECT angebot_nr
                FROM tagesliste_leads
                WHERE id = ?
            """, (angebot_id_int,)).fetchone()

            angebot_nr = ""
            if row:
                angebot_nr = str(row["angebot_nr"] or "").strip()

            angebot_nr = re.sub(r"(?i)^AN-", "", angebot_nr).strip()

            if not angebot_nr:
                angebot_nr = angebot_next_number(conn.cursor())
                conn.execute("""
                    UPDATE tagesliste_leads
                    SET angebot_nr = ?
                    WHERE id = ?
                """, (angebot_nr, angebot_id_int))
                conn.commit()

            conn.close()

            base_url = request.host_url.rstrip("/")

            session_cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
            session_cookie_value = request.cookies.get(session_cookie_name, "")

            pdf_jobs = [
                {
                    "filename": f"Angebot_AN-{angebot_nr}.pdf",
                    "url": f"{base_url}/angebotvorlage?angebot_id={angebot_id_int}&print=1"
                },
                {
                    "filename": f"Leistungsverzeichnis_AN-{angebot_nr}.pdf",
                    "url": f"{base_url}/leistungsverzeichnis?angebot_id={angebot_id_int}&print=1"
                }
            ]

            attachments = []

            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox"]
                )

                context = browser.new_context(
                    viewport={
                        "width": 1240,
                        "height": 1754
                    },
                    device_scale_factor=1
                )

                if session_cookie_value:
                    context.add_cookies([{
                        "name": session_cookie_name,
                        "value": session_cookie_value,
                        "url": base_url
                    }])

                page = context.new_page()

                for job in pdf_jobs:
                    page.goto(
                        job["url"],
                        wait_until="domcontentloaded",
                        timeout=20000
                    )
                    page.emulate_media(media="print")

                    pdf_bytes = page.pdf(
                        format="A4",
                        print_background=True,
                        prefer_css_page_size=True,
                        scale=1,
                        margin={
                            "top": "0mm",
                            "right": "0mm",
                            "bottom": "0mm",
                            "left": "0mm"
                        }
                    )

                    attachments.append({
                        "filename": job["filename"],
                        "mimeType": "application/pdf",
                        "data": base64.b64encode(pdf_bytes).decode("utf-8")
                    })

                browser.close()

            return jsonify({
                "ok": True,
                "attachments": attachments
            })

        except Exception as e:
            return jsonify({
                "ok": False,
                "message": "PDF-Anhänge konnten nicht erstellt werden: " + str(e)
            }), 500



# =====================================================
# APP2 - KG-MAIL API - INBOX
# =====================================================

    @app.route("/api/gmail/inbox")
    @login_required
    def app2_gmail_inbox():
        return gmail_box_response("inbox")


# =====================================================
# APP2 - KG-MAIL API - GESENDET
# =====================================================

    @app.route("/api/gmail/sent")
    @login_required
    def app2_gmail_sent():
        return gmail_box_response("sent")


# =====================================================
# APP2 - KG-MAIL API - ENTWÜRFE
# =====================================================

    @app.route("/api/gmail/drafts")
    @login_required
    def app2_gmail_drafts():
        return gmail_box_response("draft")


# =====================================================
# APP2 - KG-MAIL API - WICHTIG
# =====================================================

    @app.route("/api/gmail/starred")
    @login_required
    def app2_gmail_starred():
        return gmail_box_response("star")



# =====================================================
# APP2 - KG-MAIL API - JUNK / SPAM
# =====================================================

    @app.route("/api/gmail/spam")
    @login_required
    def app2_gmail_spam():
        return gmail_box_response("spam")

# =====================================================
# WEBSITE FORMU - GMAIL API
# =====================================================

    @app.route("/api/website-anfrage", methods=["POST", "OPTIONS"])
    def app2_website_anfrage():
        allowed_origins = {
            "https://www.kg-reinigung.de",
            "https://kg-reinigung.de",
            "https://dahlia-herring-fh3l.squarespace.com"
        }

        origin = str(request.headers.get("Origin") or "").rstrip("/")

        def cevap(payload, status=200):
            response = jsonify(payload)
            response.status_code = status

            if origin in allowed_origins:
                response.headers["Access-Control-Allow-Origin"] = origin
                response.headers["Vary"] = "Origin"

            response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        if request.method == "OPTIONS":
            if origin not in allowed_origins:
                return cevap({
                    "ok": False,
                    "message": "Origin nicht erlaubt."
                }, 403)

            return cevap({"ok": True})

        if origin and origin not in allowed_origins:
            return cevap({
                "ok": False,
                "message": "Origin nicht erlaubt."
            }, 403)

        try:
            data = request.get_json(silent=True) or {}

            # Görünmez spam alanı doluysa bot kabul edilir.
            if str(data.get("website") or "").strip():
                return cevap({"ok": True})

            firmenname = str(
                data.get("firmenname") or ""
            ).strip()[:150]

            ansprechpartner = str(
                data.get("ansprechpartner") or ""
            ).strip()[:150]

            email = str(
                data.get("email") or ""
            ).strip()[:254]

            telefon = str(
                data.get("telefon") or ""
            ).strip()[:80]

            reinigung = str(
                data.get("reinigung") or ""
            ).strip()[:150]

            intervall = str(
                data.get("intervall") or "Noch nicht sicher"
            ).strip()[:150]

            einsatzort = str(
                data.get("einsatzort") or ""
            ).strip()[:180]

            flaeche = str(
                data.get("flaeche") or ""
            ).strip()[:100]

            nachricht = str(
                data.get("nachricht") or ""
            ).strip()[:5000]

            datenschutz = data.get("datenschutz") is True

            if not all([
                ansprechpartner,
                email,
                telefon,
                reinigung,
                nachricht,
                datenschutz
            ]):
                return cevap({
                    "ok": False,
                    "message": "Bitte füllen Sie alle Pflichtfelder aus."
                }, 400)

            if not re.fullmatch(
                r"[^\s@]+@[^\s@]+\.[^\s@]+",
                email
            ):
                return cevap({
                    "ok": False,
                    "message": "Bitte geben Sie eine gültige E-Mail-Adresse ein."
                }, 400)

            message_text = "\n".join([
                "Neue Angebotsanfrage über kg-reinigung.de",
                "",
                f"Firmenname: {firmenname or '-'}",
                f"Ansprechpartner: {ansprechpartner}",
                f"E-Mail-Adresse: {email}",
                f"Telefonnummer: {telefon}",
                f"Gewünschte Reinigung: {reinigung}",
                f"Reinigungsintervall: {intervall or '-'}",
                f"PLZ / Einsatzort: {einsatzort or '-'}",
                f"Ungefähre Fläche: {flaeche or '-'}",
                "",
                "Weitere Informationen:",
                nachricht,
                "",
                "Datenschutz akzeptiert: Ja"
            ])

            def table_row(label, value):
                safe_label = escape(str(label or ""))
                safe_value = escape(
                    str(value or "-")
                ).replace("\n", "<br>")

                return f"""
                <tr>
                    <td style="
                        width:34%;
                        padding:12px 14px;
                        border:1px solid #d7deea;
                        background:#f6f8fc;
                        color:#1f2937;
                        font-weight:700;
                    ">
                        {safe_label}
                    </td>

                    <td style="
                        padding:12px 14px;
                        border:1px solid #d7deea;
                        background:#ffffff;
                        color:#1f2937;
                    ">
                        {safe_value}
                    </td>
                </tr>
                """

            internal_html = f"""
            <div style="
                max-width:760px;
                margin:0 auto;
                font-family:Arial,Helvetica,sans-serif;
                color:#1f2937;
            ">
                <div style="
                    padding:20px;
                    color:#ffffff;
                    background:#064cff;
                    font-size:21px;
                    font-weight:700;
                ">
                    Neue Angebotsanfrage über kg-reinigung.de
                </div>

                <table style="
                    width:100%;
                    border-collapse:collapse;
                    background:#ffffff;
                ">
                    {table_row("Firmenname", firmenname or "-")}
                    {table_row("Ansprechpartner", ansprechpartner)}
                    {table_row("E-Mail-Adresse", email)}
                    {table_row("Telefonnummer", telefon)}
                    {table_row("Gewünschte Reinigung", reinigung)}
                    {table_row("Reinigungsintervall", intervall or "-")}
                    {table_row("PLZ / Einsatzort", einsatzort or "-")}
                    {table_row("Ungefähre Fläche", flaeche or "-")}
                    {table_row("Weitere Informationen", nachricht)}
                    {table_row("Datenschutz akzeptiert", "Ja")}
                </table>

                <div style="
                    padding:14px 5px;
                    color:#6b7280;
                    font-size:12px;
                    text-align:center;
                ">
                    Automatisch übermittelt durch das Anfrageformular
                    auf kg-reinigung.de
                </div>
            </div>
            """

            msg = EmailMessage()
            msg["From"] = (
                "KG-Gebäudereinigung <info@kg-reinigung.de>"
            )
            msg["To"] = "info@kg-reinigung.de"
            msg["Reply-To"] = email
            msg["Subject"] = (
                f"Neue Website-Anfrage – {ansprechpartner}"
            )

            msg.set_content(message_text)
            msg.add_alternative(
                internal_html,
                subtype="html"
            )

            raw_message = base64.urlsafe_b64encode(
                msg.as_bytes()
            ).decode("utf-8")

            service = get_gmail_service()

            sent = service.users().messages().send(
                userId="me",
                body={"raw": raw_message}
            ).execute()

            # MÜŞTERİYE OTOMATİK ALINDI ONAYI
            try:
                customer_subject = (
                    "Eingangsbestätigung Ihrer Anfrage – "
                    "KG-Gebäudereinigung"
                )

                customer_plain = """Sehr geehrte Damen und Herren,

vielen Dank für Ihre Anfrage und Ihr Interesse an den Dienstleistungen der KG-Gebäudereinigung.

Hiermit bestätigen wir Ihnen den erfolgreichen Eingang Ihrer Anfrage. Ihre Angaben wurden zur Bearbeitung aufgenommen und werden von uns sorgfältig geprüft.

Wir werden uns schnellstmöglich mit Ihnen in Verbindung setzen.

Bei Fragen stehen wir Ihnen gern zur Verfügung.

Vielen Dank.

Mit freundlichen Grüßen

Damla Kicci
Inhaberin

KG-Gebäudereinigung
Fliederstr. 59
47055 Duisburg - Wanheimerort

0203 / 47 96 68 22
0163 / 194 70 55

info@kg-reinigung.de
www.kg-reinigung.de
"""

                customer_html = """
                <div style="
                    max-width:650px;
                    font-family:Arial,Helvetica,sans-serif;
                    font-size:15px;
                    line-height:1.65;
                    color:#202124;
                ">
                    <p>Sehr geehrte Damen und Herren,</p>

                    <p>
                        vielen Dank für Ihre Anfrage und Ihr Interesse
                        an den Dienstleistungen der
                        KG-Gebäudereinigung.
                    </p>

                    <p>
                        Hiermit bestätigen wir Ihnen den erfolgreichen
                        Eingang Ihrer Anfrage. Ihre Angaben wurden zur
                        Bearbeitung aufgenommen und werden von uns
                        sorgfältig geprüft.
                    </p>

                    <p>
                        Wir werden uns schnellstmöglich mit Ihnen in
                        Verbindung setzen.
                    </p>

                    <p>
                        Bei Fragen stehen wir Ihnen gern zur Verfügung.
                    </p>

                    <p>Vielen Dank.</p>

                    <p>Mit freundlichen Grüßen</p>

                    <div style="
                        max-width:520px;
                        margin-top:24px;
                        padding-top:18px;
                        border-top:2px solid #064cff;
                    ">
                        <strong style="
                            color:#064cff;
                            font-size:18px;
                        ">
                            Damla Kicci
                        </strong>
                        <br>

                        Inhaberin
                        <br><br>

                        <strong>KG-Gebäudereinigung</strong>
                        <br>

                        Fliederstr. 59
                        <br>

                        47055 Duisburg - Wanheimerort
                        <br><br>

                        0203 / 47 96 68 22
                        <br>

                        0163 / 194 70 55
                        <br><br>

                        <a
                            href="mailto:info@kg-reinigung.de"
                            style="color:#064cff;"
                        >
                            info@kg-reinigung.de
                        </a>
                        <br>

                        <a
                            href="https://www.kg-reinigung.de"
                            style="color:#064cff;"
                        >
                            www.kg-reinigung.de
                        </a>
                    </div>
                </div>
                """

                customer_msg = EmailMessage()

                customer_msg["From"] = (
                    "KG-Gebäudereinigung "
                    "<info@kg-reinigung.de>"
                )

                # Formda müşterinin yazdığı e-posta adresi
                customer_msg["To"] = email

                customer_msg["Reply-To"] = (
                    "info@kg-reinigung.de"
                )

                customer_msg["Subject"] = customer_subject

                customer_msg["Auto-Submitted"] = (
                    "auto-replied"
                )

                customer_msg["X-Auto-Response-Suppress"] = (
                    "All"
                )

                customer_msg.set_content(customer_plain)

                customer_msg.add_alternative(
                    customer_html,
                    subtype="html"
                )

                customer_raw = base64.urlsafe_b64encode(
                    customer_msg.as_bytes()
                ).decode("utf-8")

                service.users().messages().send(
                    userId="me",
                    body={"raw": customer_raw}
                ).execute()

            except Exception as customer_mail_error:
                # Otomatik cevapta hata olsa bile bize gelen
                # asıl müşteri başvurusu kaybolmaz.
                print(
                    "WEBSITE AUTO-ANTWORT FEHLER:",
                    str(customer_mail_error)
                )


            return cevap({
                "ok": True,
                "message": "Ihre Anfrage wurde erfolgreich übermittelt.",
                "request_id": sent.get("id", "")
            })

        except Exception as e:
            print("WEBSITE ANFRAGE FEHLER:", str(e))

            return cevap({
                "ok": False,
                "message": "Die Anfrage konnte nicht gesendet werden."
            }, 500)

# =====================================================
# APP2 - KG-MAIL API - MAIL SENDEN
# =====================================================

    @app.route("/api/gmail/send", methods=["POST"])
    @login_required
    def app2_gmail_send():
        try:
            data = request.get_json(silent=True) or {}

            from_email = str(data.get("from") or "info@kg-reinigung.de").strip()
            to_email = str(data.get("to") or "").strip()
            cc_email = str(data.get("cc") or "").strip()
            bcc_email = str(data.get("bcc") or "").strip()
            subject = str(data.get("subject") or "").strip()
            message_text = str(data.get("message") or "").strip()
            signature_html = str(data.get("signature") or "").strip()
            attachments = data.get("attachments") or []

            if not to_email:
                return jsonify({
                    "ok": False,
                    "message": "Empfänger fehlt."
                }), 400

            if not subject:
                return jsonify({
                    "ok": False,
                    "message": "Betreff fehlt."
                }), 400

            plain_html = escape(message_text).replace("\n", "<br>")
            body_html = f"""
            <div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.6;color:#111827;">
                {plain_html}
                <br><br>
                {signature_html}
            </div>
            """

            msg = EmailMessage()
            msg["From"] = f"KG-Gebäudereinigung <{from_email}>"
            msg["To"] = to_email
            msg["Subject"] = subject

            if cc_email:
                msg["Cc"] = cc_email

            if bcc_email:
                msg["Bcc"] = bcc_email

            msg.set_content(message_text or " ")
            msg.add_alternative(body_html, subtype="html")

            if "cid:kg-logo" in body_html:
                try:
                    logo_path = os.path.join(STATIC_DIR, "KG Yeni Logo.png")

                    if os.path.exists(logo_path):
                        with open(logo_path, "rb") as logo_file:
                            logo_bytes = logo_file.read()

                        html_part = msg.get_payload()[-1]
                        html_part.add_related(
                            logo_bytes,
                            maintype="image",
                            subtype="png",
                            cid="<kg-logo>",
                            filename="KG Yeni Logo.png"
                        )
                except Exception as logo_error:
                    print("GMAIL INLINE LOGO FEHLER:", str(logo_error))
    
            for att in attachments:
                try:
                    filename = str(att.get("filename") or "anhang").replace('"', "").strip()
                    mime_type = str(att.get("mimeType") or "application/octet-stream").strip()
                    encoded_data = str(att.get("data") or "").strip()

                    if not encoded_data:
                        continue

                    missing_padding = len(encoded_data) % 4
                    if missing_padding:
                        encoded_data += "=" * (4 - missing_padding)

                    file_bytes = base64.b64decode(encoded_data)

                    if "/" in mime_type:
                        maintype, subtype = mime_type.split("/", 1)
                    else:
                        maintype, subtype = "application", "octet-stream"

                    msg.add_attachment(
                        file_bytes,
                        maintype=maintype,
                        subtype=subtype,
                        filename=filename
                    )
                except Exception as attach_error:
                    print("GMAIL SEND ATTACHMENT FEHLER:", str(attach_error))

            raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

            service = get_gmail_service()
            sent = service.users().messages().send(
                userId="me",
                body={
                    "raw": raw_message
                }
            ).execute()

            try:
                ensure_gmail_cache_table()
                conn = sqlite3.connect(DB_PATH)
                conn.execute("DELETE FROM gmail_mail_cache WHERE box = ?", ("sent",))
                conn.commit()
                conn.close()
            except Exception:
                pass

            return jsonify({
                "ok": True,
                "message": "Mail wurde erfolgreich gesendet.",
                "gmail_id": sent.get("id", "")
            })

        except Exception as e:
            return jsonify({
                "ok": False,
                "message": str(e)
            }), 500


# =====================================================
# APP2 - KG-MAIL API - EINZELNE MAIL FRISCH LADEN
# =====================================================

    @app.route("/api/gmail/message/<message_id>")
    @login_required
    def app2_gmail_single_message(message_id):
        try:
            service = get_gmail_service()

            full_msg = service.users().messages().get(
                userId="me",
                id=message_id,
                format="full"
            ).execute()

            label_ids = full_msg.get("labelIds", []) or []
            box_name = "inbox"
            tag_text = "Gmail"
            tag_class = "blue"

            if "SENT" in label_ids:
                box_name = "sent"
                tag_text = "Gesendet"
                tag_class = "green"
            elif "DRAFT" in label_ids:
                box_name = "draft"
                tag_text = "Entwurf"
                tag_class = "orange"
            elif "STARRED" in label_ids:
                box_name = "star"
                tag_text = "Wichtig"
                tag_class = "purple"
            elif "SPAM" in label_ids:
                box_name = "spam"
                tag_text = "Junk"
                tag_class = "orange"

            mail = build_gmail_mail_object(
                full_msg=full_msg,
                index=1,
                box_name=box_name,
                tag_text=tag_text,
                tag_class=tag_class
            )

            gmail_cache_upsert(mail)

            return jsonify({
                "ok": True,
                "message": mail
            })

        except Exception as e:
            return jsonify({
                "ok": False,
                "message": str(e)
            }), 500


# =====================================================
# APP2 - KG-MAIL API - ATTACHMENT ÖFFNEN / HERUNTERLADEN
# =====================================================

    @app.route("/api/gmail/attachment/<message_id>/<path:attachment_id>")
    @login_required
    def app2_gmail_attachment(message_id, attachment_id):
        try:
            message_id = unquote(str(message_id or "")).strip()
            attachment_id = unquote(str(attachment_id or "")).strip()

            if not message_id or not attachment_id:
                return jsonify({
                    "ok": False,
                    "message": "message_id oder attachment_id fehlt."
                }), 400

            cache = getattr(app2_gmail_attachment, "_cache", {})
            cache_key = f"{message_id}|{attachment_id}"

            if cache_key in cache:
                cached = cache[cache_key]
                file_data = cached["file_data"]
                filename = cached["filename"]
                mime_type = cached["mime_type"]
            else:
                service = get_gmail_service()

                filename = request.args.get("filename", "").strip() or "anhang"
                mime_type = request.args.get("mimeType", "").strip() or "application/octet-stream"

                if filename == "anhang" or mime_type == "application/octet-stream":
                    try:
                        full_msg = service.users().messages().get(
                            userId="me",
                            id=message_id,
                            format="full"
                        ).execute()

                        def find_attachment(part):
                            nonlocal filename, mime_type

                            body = part.get("body", {}) or {}
                            if body.get("attachmentId") == attachment_id:
                                filename = part.get("filename") or filename
                                mime_type = part.get("mimeType") or mime_type
                                return True

                            for child in part.get("parts", []) or []:
                                if find_attachment(child):
                                    return True

                            return False

                        find_attachment(full_msg.get("payload", {}))
                    except Exception:
                        pass

                filename_lower = filename.lower()

                if filename_lower.endswith(".pdf"):
                    mime_type = "application/pdf"
                elif filename_lower.endswith(".xlsx"):
                    mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                elif filename_lower.endswith(".xls"):
                    mime_type = "application/vnd.ms-excel"
                elif filename_lower.endswith(".docx"):
                    mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                elif filename_lower.endswith(".doc"):
                    mime_type = "application/msword"
                elif filename_lower.endswith(".png"):
                    mime_type = "image/png"
                elif filename_lower.endswith(".jpg") or filename_lower.endswith(".jpeg"):
                    mime_type = "image/jpeg"

                attachment = service.users().messages().attachments().get(
                    userId="me",
                    messageId=message_id,
                    id=attachment_id
                ).execute()

                data = attachment.get("data", "") or ""
                missing_padding = len(data) % 4
                if missing_padding:
                    data += "=" * (4 - missing_padding)

                file_data = base64.urlsafe_b64decode(data.encode("utf-8"))

                cache[cache_key] = {
                    "file_data": file_data,
                    "filename": filename,
                    "mime_type": mime_type
                }

                if len(cache) > 50:
                    first_key = next(iter(cache))
                    cache.pop(first_key, None)

                app2_gmail_attachment._cache = cache

            safe_filename = filename.replace('"', "")

            headers = {
                "Content-Disposition": f'inline; filename="{safe_filename}"',
                "Content-Type": mime_type,
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, max-age=3600",
                "Accept-Ranges": "bytes"
            }

            range_header = request.headers.get("Range", None)

            if range_header and mime_type == "application/pdf":
                size = len(file_data)
                byte_range = range_header.replace("bytes=", "").split("-")

                try:
                    start = int(byte_range[0]) if byte_range[0] else 0
                    end = int(byte_range[1]) if len(byte_range) > 1 and byte_range[1] else size - 1
                except Exception:
                    start = 0
                    end = size - 1

                end = min(end, size - 1)
                chunk = file_data[start:end + 1]

                headers.update({
                    "Content-Range": f"bytes {start}-{end}/{size}",
                    "Content-Length": str(len(chunk))
                })

                return Response(
                    chunk,
                    status=206,
                    mimetype=mime_type,
                    headers=headers
                )

            headers["Content-Length"] = str(len(file_data))

            return Response(
                file_data,
                mimetype=mime_type,
                headers=headers
            )

        except Exception as e:
            print("GMAIL ATTACHMENT FEHLER:", str(e))
            return jsonify({
                "ok": False,
                "message": str(e)
            }), 500

# =====================================================
# APP2 - BÖLÜM 1 - DATENBANK
# =====================================================

    @app.route("/datenbank")
    @app.route("/datenbank.html")
    @login_required
    def app2_datenbank():
        ensure_tagesliste_table()

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        tagesliste_leads = cursor.execute("""
            SELECT *
            FROM tagesliste_leads
            WHERE status IS NULL OR status = '' OR status = 'offen'
            ORDER BY CASE WHEN sort_order IS NULL OR sort_order = 0 THEN id ELSE sort_order END ASC, id ASC
        """).fetchall()

        tagesliste_leads = [dict(row) for row in tagesliste_leads]

        try:
            total_leads_count = cursor.execute("""
                SELECT COUNT(*)
                FROM leads
            """).fetchone()[0]
        except Exception:
            total_leads_count = 0

        stats_row = cursor.execute("""
            SELECT
                COUNT(*) AS total_leads,

                SUM(CASE WHEN status IS NULL OR status = 'offen' THEN 1 ELSE 0 END) AS tagesliste_total,

                SUM(CASE WHEN status = 'angerufen' THEN 1 ELSE 0 END) AS today_angerufen,
                SUM(CASE WHEN status = 'interessiert' THEN 1 ELSE 0 END) AS today_interessiert,
                SUM(CASE WHEN status = 'besichtigung' THEN 1 ELSE 0 END) AS today_besichtigung
            FROM tagesliste_leads
        """).fetchone()

        month_row = cursor.execute("""
            SELECT
                SUM(CASE WHEN status = 'angerufen' THEN 1 ELSE 0 END) AS month_angerufen,
                SUM(CASE WHEN status = 'interessiert' THEN 1 ELSE 0 END) AS month_interessiert,
                SUM(CASE WHEN status = 'besichtigung' THEN 1 ELSE 0 END) AS month_besichtigung,
                SUM(CASE WHEN status = 'spaeter' THEN 1 ELSE 0 END) AS month_spaeter
            FROM tagesliste_status_history
            WHERE strftime('%Y-%m', datum) = strftime('%Y-%m', 'now', 'localtime')
        """).fetchone()

        month_label = cursor.execute("""
            SELECT strftime('%m.%Y', 'now', 'localtime')
        """).fetchone()[0]

        datenbank_stats = {
            "total_leads": int(total_leads_count or 0),
            "tagesliste_total": int(stats_row["tagesliste_total"] or 0),

            "month_angerufen": int(month_row["month_angerufen"] or 0),
            "month_interessiert": int(month_row["month_interessiert"] or 0),
            "month_besichtigung": int(month_row["month_besichtigung"] or 0),
            "month_spaeter": int(month_row["month_spaeter"] or 0),
            "today_angerufen": int(stats_row["today_angerufen"] or 0),
            "today_interessiert": int(stats_row["today_interessiert"] or 0),
            "today_besichtigung": int(stats_row["today_besichtigung"] or 0),

            "month_label": month_label or ""
        }

        conn.close()

        return render_template(
            "datenbank.html",
            tagesliste_leads=tagesliste_leads,
            datenbank_stats=datenbank_stats
        )

# =====================================================
# APP2 - BESTANDSKUNDEN ARAMA API
# =====================================================

    @app.route("/api/datenbank/bestandskunden-search")
    @login_required
    def app2_bestandskunden_search():
        suchtext = str(request.args.get("q", "") or "").strip()

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        if suchtext:
            like_value = f"%{suchtext}%"

            rows = conn.execute("""
                SELECT
                    id,
                    firma,
                    ort,
                    strasse,
                    plz,
                    ansprechpartner_name,
                    telefon,
                    email,
                    kundennummer
                FROM kunden
                WHERE
                    firma LIKE ?
                    OR ansprechpartner_name LIKE ?
                    OR ort LIKE ?
                    OR strasse LIKE ?
                    OR plz LIKE ?
                    OR telefon LIKE ?
                    OR email LIKE ?
                    OR kundennummer LIKE ?
                ORDER BY firma ASC
                LIMIT 20
            """, (
                like_value,
                like_value,
                like_value,
                like_value,
                like_value,
                like_value,
                like_value,
                like_value
            )).fetchall()

        else:
            rows = conn.execute("""
                SELECT
                    id,
                    firma,
                    ort,
                    strasse,
                    plz,
                    ansprechpartner_name,
                    telefon,
                    email,
                    kundennummer
                FROM kunden
                ORDER BY firma ASC
                LIMIT 20
            """).fetchall()

        conn.close()

        return jsonify({
            "ok": True,
            "kunden": [dict(row) for row in rows]
        })


# =====================================================
# APP2 - DIREKTES ANGEBOT FÜR BESTANDSKUNDE
# Bestandskunde wird nur zur Besichtigungsliste hinzugefügt.
# Ein Angebot wird noch nicht erstellt.
# =====================================================

    @app.route("/api/datenbank/direktes-angebot-bestandskunde", methods=["POST"])
    @login_required
    def app2_direktes_angebot_bestandskunde():
        ensure_tagesliste_table()

        data = request.get_json(silent=True) or {}

        try:
            kunde_id = int(data.get("kunde_id") or 0)
        except Exception:
            kunde_id = 0

        if kunde_id <= 0:
            return jsonify({
                "ok": False,
                "message": "Kunden-ID fehlt."
            }), 400

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        kunde = cursor.execute("""
            SELECT
                id,
                firma,
                ort,
                strasse,
                plz,
                ansprechpartner_name,
                telefon,
                email,
                kundennummer
            FROM kunden
            WHERE id = ?
        """, (kunde_id,)).fetchone()

        if not kunde:
            conn.close()

            return jsonify({
                "ok": False,
                "message": "Bestandskunde wurde nicht gefunden."
            }), 404

        besichtigung_data = {
            "direktes_angebot": True,
            "quelle": "Bestandskunde",
            "kunde": {
                "kunden_id": kunde["id"],
                "kundennummer": kunde["kundennummer"] or "",
                "firma": kunde["firma"] or "",
                "ansprechpartner": kunde["ansprechpartner_name"] or "",
                "strasse": kunde["strasse"] or "",
                "plz": kunde["plz"] or "",
                "ort": kunde["ort"] or "",
                "telefon": kunde["telefon"] or "",
                "email": kunde["email"] or ""
            },
            "leistungen": [],
            "raeume": [],
            "bereiche": [],
            "sonstiges": [],
            "notizen": []
        }

        company_key = (
            "direktes-angebot-bestandskunde-"
            + str(kunde_id)
            + "-"
            + datetime.now().strftime("%Y%m%d%H%M%S%f")
        )

        cursor.execute("""
            INSERT INTO tagesliste_leads (
                firma,
                ansprechpartner,
                strasse,
                plz,
                ort,
                telefon,
                email,
                quelle,
                status,
                notiz,
                company_key,
                besichtigung_data_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            kunde["firma"] or "",
            kunde["ansprechpartner_name"] or "",
            kunde["strasse"] or "",
            kunde["plz"] or "",
            kunde["ort"] or "",
            kunde["telefon"] or "",
            kunde["email"] or "",
            "Direktes Angebot · Bestandskunde",
            "besichtigung",
            "Bestandskunde · Angebotsvorbereitung",
            company_key,
            json.dumps(besichtigung_data, ensure_ascii=False)
        ))

        besichtigung_id = cursor.lastrowid

        cursor.execute("""
            INSERT OR IGNORE INTO tagesliste_status_history (
                tagesliste_id,
                status,
                datum
            )
            VALUES (?, 'besichtigung', date('now', 'localtime'))
        """, (besichtigung_id,))

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Bestandskunde wurde zur Besichtigungsliste hinzugefügt.",
            "besichtigung_id": besichtigung_id
        })

# =====================================================
# APP2 - DIREKTES ANGEBOT FÜR NEUKUNDE
# Neukunde wird gespeichert und zur Besichtigungsliste hinzugefügt.
# Ein Angebot wird noch nicht erstellt.
# =====================================================

    @app.route("/api/datenbank/direktes-angebot-neukunde", methods=["POST"])
    @login_required
    def app2_direktes_angebot_neukunde():
        ensure_tagesliste_table()

        data = request.get_json(silent=True) or {}

        firma = str(data.get("firma") or "").strip()
        ansprechpartner = str(data.get("ansprechpartner") or "").strip()
        strasse = str(data.get("strasse") or "").strip()
        plz = str(data.get("plz") or "").strip()
        ort = str(data.get("ort") or "").strip()
        telefon = str(data.get("telefon") or "").strip()
        email = str(data.get("email") or "").strip()

        if not firma:
            return jsonify({
                "ok": False,
                "message": "Firmenname fehlt."
            }), 400

        if not ort:
            return jsonify({
                "ok": False,
                "message": "Ort fehlt."
            }), 400

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        besichtigung_data = {
            "direktes_angebot": True,
            "quelle": "Neukunde",
            "kunde": {
                "firma": firma,
                "ansprechpartner": ansprechpartner,
                "strasse": strasse,
                "plz": plz,
                "ort": ort,
                "telefon": telefon,
                "email": email
            },
            "leistungen": [],
            "raeume": [],
            "bereiche": [],
            "sonstiges": [],
            "notizen": []
        }

        company_key = (
            "direktes-angebot-neukunde-"
            + datetime.now().strftime("%Y%m%d%H%M%S%f")
        )

        cursor.execute("""
            INSERT INTO tagesliste_leads (
                firma,
                branche,
                ansprechpartner,
                strasse,
                plz,
                ort,
                telefon,
                email,
                quelle,
                status,
                notiz,
                company_key,
                besichtigung_data_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            firma,
            "Direktes Angebot",
            ansprechpartner,
            strasse,
            plz,
            ort,
            telefon,
            email,
            "Direktes Angebot · Neukunde",
            "besichtigung",
            "Neukunde · Angebotsvorbereitung",
            company_key,
            json.dumps(besichtigung_data, ensure_ascii=False)
        ))

        besichtigung_id = cursor.lastrowid

        cursor.execute("""
            INSERT OR IGNORE INTO tagesliste_status_history (
                tagesliste_id,
                status,
                datum
            )
            VALUES (?, 'besichtigung', date('now', 'localtime'))
        """, (besichtigung_id,))

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Neukunde wurde gespeichert und zur Besichtigungsliste hinzugefügt.",
            "besichtigung_id": besichtigung_id
        })
    # =====================================================
    # KG SCAN APP - CRM API
    # GET  = Besichtigung listesini iPhone'a yollar
    # POST = Tarama sonucunu ilgili Besichtigung kaydina yazar
    # =====================================================

    def kg_scan_token_ok():
        expected_token = os.getenv("KG_SCAN_API_TOKEN", "").strip()
        given_token = request.headers.get("X-KG-SCAN-TOKEN", "").strip()

        return bool(
            expected_token
            and given_token
            and given_token == expected_token
        )


    @app.route("/internal/kg-scan/neukunde", methods=["POST"])
    def kg_scan_neukunde():
        if not kg_scan_token_ok():
            return jsonify({
                "ok": False,
                "message": "Unauthorized"
            }), 403

        ensure_tagesliste_table()

        data = request.get_json(silent=True) or {}

        firma = str(data.get("firma") or "").strip()
        ansprechpartner = str(data.get("ansprechpartner") or "").strip()
        strasse = str(data.get("strasse") or "").strip()
        plz = str(data.get("plz") or "").strip()
        ort = str(data.get("ort") or "").strip()
        telefon = str(data.get("telefon") or "").strip()
        email = str(data.get("email") or "").strip()

        if not firma:
            return jsonify({
                "ok": False,
                "message": "Firmenname fehlt."
            }), 400

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        besichtigung_data = {
            "direktes_angebot": True,
            "quelle": "KG Scan Neukunde",
            "kunde": {
                "firma": firma,
                "ansprechpartner": ansprechpartner,
                "strasse": strasse,
                "plz": plz,
                "ort": ort,
                "telefon": telefon,
                "email": email
            },
            "leistungen": [],
            "raeume": [],
            "bereiche": [],
            "sonstiges": [],
            "notizen": []
        }

        company_key = (
            "kg-scan-neukunde-"
            + datetime.now().strftime("%Y%m%d%H%M%S%f")
        )

        cursor.execute("""
            INSERT INTO tagesliste_leads (
                firma,
                branche,
                ansprechpartner,
                strasse,
                plz,
                ort,
                telefon,
                email,
                quelle,
                status,
                notiz,
                company_key,
                besichtigung_data_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            firma,
            "KG Scan",
            ansprechpartner,
            strasse,
            plz,
            ort,
            telefon,
            email,
            "KG Scan · Neukunde",
            "besichtigung",
            "Neukunde über KG Scan",
            company_key,
            json.dumps(
                besichtigung_data,
                ensure_ascii=False
            )
        ))

        besichtigung_id = cursor.lastrowid

        cursor.execute("""
            INSERT OR IGNORE INTO tagesliste_status_history (
                tagesliste_id,
                status,
                datum
            )
            VALUES (?, 'besichtigung', date('now', 'localtime'))
        """, (
            besichtigung_id,
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Neukunde wurde gespeichert.",
            "besichtigung_id": besichtigung_id
        })


    @app.route("/internal/kg-scan/besichtigungen", methods=["GET"])
    def kg_scan_besichtigungen():

        if not kg_scan_token_ok():
            return jsonify({
                "ok": False,
                "message": "Unauthorized"
            }), 403

        ensure_tagesliste_table()

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT
                id,
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
                besichtigung_data_json
            FROM tagesliste_leads
            WHERE status = 'besichtigung'
            ORDER BY id DESC
        """).fetchall()

        conn.close()

        termine = []

        for row in rows:
            termine.append({
                "id": row["id"],
                "firma": row["firma"] or "",
                "branche": row["branche"] or "",
                "ansprechpartner": row["ansprechpartner"] or "",
                "strasse": row["strasse"] or "",
                "plz": row["plz"] or "",
                "ort": row["ort"] or "",
                "telefon": row["telefon"] or "",
                "email": row["email"] or "",
                "website": row["website"] or "",
                "quelle": row["quelle"] or "",
                "status": row["status"] or "besichtigung",
                "notiz": row["notiz"] or "",
                "besichtigung_data_json": row["besichtigung_data_json"] or ""
            })

        return jsonify({
            "ok": True,
            "count": len(termine),
            "termine": termine
        })

    @app.route("/internal/kg-scan/model", methods=["POST"])
    def kg_scan_model_upload():

        if not kg_scan_token_ok():
            return jsonify({
                "ok": False,
                "message": "Unauthorized"
            }), 403

        try:
            tagesliste_id = int(
                request.form.get("tagesliste_id") or 0
            )
        except Exception:
            tagesliste_id = 0

        if tagesliste_id <= 0:
            return jsonify({
                "ok": False,
                "message": "Besichtigung-ID fehlt."
            }), 400

        model_file = request.files.get("model")

        if not model_file:
            return jsonify({
                "ok": False,
                "message": "USDZ-Datei fehlt."
            }), 400

        original_name = str(
            model_file.filename or ""
        ).strip()

        if not original_name.lower().endswith(".usdz"):
            return jsonify({
                "ok": False,
                "message": "Nur USDZ-Dateien sind erlaubt."
            }), 400

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        row = conn.execute("""
            SELECT id, besichtigung_data_json
            FROM tagesliste_leads
            WHERE id = ?
            AND status = 'besichtigung'
        """, (tagesliste_id,)).fetchone()

        if not row:
            conn.close()

            return jsonify({
                "ok": False,
                "message": "Besichtigung wurde nicht gefunden."
            }), 404

        model_dir = os.path.join(
            STATIC_DIR,
            "kg_scan_models",
            "scans"
        )

        os.makedirs(
            model_dir,
            exist_ok=True
        )

        usdz_filename = (
            f"besichtigung_{tagesliste_id}.usdz"
        )

        usdz_path = os.path.join(
            model_dir,
            usdz_filename
        )

        flattened_filename = (
            f"besichtigung_{tagesliste_id}_flat.usdc"
        )

        flattened_path = os.path.join(
            model_dir,
            flattened_filename
        )

        glb_filename = (
            f"besichtigung_{tagesliste_id}.glb"
        )

        glb_path = os.path.join(
            model_dir,
            glb_filename
        )

        model_file.save(
            usdz_path
        )

        try:
            from pxr import Usd

            stage = Usd.Stage.Open(
                usdz_path
            )

            if not stage:
                raise RuntimeError(
                    "USDZ konnte von OpenUSD nicht geöffnet werden."
                )

            if os.path.exists(
                flattened_path
            ):
                os.remove(
                    flattened_path
                )

            export_ok = stage.Export(
                flattened_path
            )

            if not export_ok:
                raise RuntimeError(
                    "USDZ konnte nicht geflattet werden."
                )

        except Exception as e:
            conn.close()

            return jsonify({
                "ok": False,
                "message": (
                    "OpenUSD-Fehler: "
                    + str(e)
                )
            }), 500

        blender_path = (
            os.environ.get("BLENDER_BIN")
            or shutil.which("blender")
            or shutil.which("blender.exe")
            or r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
        )

        if not blender_path:
            conn.close()

            return jsonify({
                "ok": False,
                "message": (
                    "Blender wurde nicht gefunden."
                )
            }), 500

        try:
            if os.path.exists(
                glb_path
            ):
                os.remove(
                    glb_path
                )

            blender_script = (
                "import bpy,sys;"
                "src=sys.argv[sys.argv.index('--')+1];"
                "dst=sys.argv[sys.argv.index('--')+2];"
                "bpy.ops.object.select_all(action='SELECT');"
                "bpy.ops.object.delete(use_global=False);"
                "bpy.ops.wm.usd_import(filepath=src);"
                "bpy.ops.export_scene.gltf("
                "filepath=dst,"
                "export_format='GLB'"
                ")"
            )

            result = subprocess.run(
                [
                    blender_path,
                    "--background",
                    "--factory-startup",
                    "--python-expr",
                    blender_script,
                    "--",
                    flattened_path,
                    glb_path
                ],
                capture_output=True,
                text=True,
                timeout=180
            )

            if (
                result.returncode != 0
                or not os.path.exists(glb_path)
                or os.path.getsize(glb_path) <= 0
            ):
                raise RuntimeError(
                    result.stderr
                    or result.stdout
                    or "GLB-Konvertierung fehlgeschlagen."
                )

        except Exception as e:
            conn.close()

            return jsonify({
                "ok": False,
                "message": (
                    "GLB-Konvertierungsfehler: "
                    + str(e)
                )
            }), 500

        model_url = (
            f"/static/kg_scan_models/scans/"
            f"{glb_filename}"
        )

        fresh_row = conn.execute("""
            SELECT besichtigung_data_json
            FROM tagesliste_leads
            WHERE id = ?
        """, (
            tagesliste_id,
        )).fetchone()

        existing = {}

        if (
            fresh_row
            and fresh_row["besichtigung_data_json"]
        ):
            try:
                existing = json.loads(
                    fresh_row["besichtigung_data_json"]
                )
            except Exception:
                existing = {}

        if not isinstance(existing, dict):
            existing = {}

        kg_scan = existing.get("kg_scan") or {}

        if not isinstance(kg_scan, dict):
            kg_scan = {}

        kg_scan["model_id"] = glb_filename
        kg_scan["model_url"] = model_url
        kg_scan["model_source_usdz"] = usdz_filename
        kg_scan["model_updated_at"] = (
            datetime.now().isoformat()
        )

        existing["kg_scan"] = kg_scan

        conn.execute("""
            UPDATE tagesliste_leads
            SET besichtigung_data_json = ?
            WHERE id = ?
        """, (
            json.dumps(
                existing,
                ensure_ascii=False
            ),
            tagesliste_id
        ))

        conn.commit()
        conn.close()

        try:
            if os.path.exists(
                flattened_path
            ):
                os.remove(
                    flattened_path
                )
        except Exception:
            pass

        return jsonify({
            "ok": True,
            "model_id": glb_filename,
            "model_url": model_url
        })

    @app.route("/internal/kg-scan/previews", methods=["POST"])
    def kg_scan_previews_upload():

        if not kg_scan_token_ok():
            return jsonify({
                "ok": False,
                "message": "Unauthorized"
            }), 403

        try:
            tagesliste_id = int(
                request.form.get("tagesliste_id") or 0
            )
        except Exception:
            tagesliste_id = 0

        if tagesliste_id <= 0:
            return jsonify({
                "ok": False,
                "message": "Besichtigung-ID fehlt."
            }), 400

        preview_2d = request.files.get("preview_2d")
        preview_3d = request.files.get("preview_3d")

        if not preview_2d or not preview_3d:
            return jsonify({
                "ok": False,
                "message": "2D- oder 3D-Vorschau fehlt."
            }), 400

        conn = sqlite3.connect(
            DB_PATH,
            timeout=30
        )

        conn.execute(
            "PRAGMA busy_timeout = 30000"
        )

        conn.row_factory = sqlite3.Row

        row = conn.execute("""
            SELECT id, besichtigung_data_json
            FROM tagesliste_leads
            WHERE id = ?
            AND status = 'besichtigung'
        """, (tagesliste_id,)).fetchone()

        if not row:
            conn.close()

            return jsonify({
                "ok": False,
                "message": "Besichtigung wurde nicht gefunden."
            }), 404

        preview_dir = os.path.join(
            os.path.dirname(DB_PATH),
            "kg_scan_previews"
        )
        os.makedirs(
            preview_dir,
            exist_ok=True
        )

        preview_2d_filename = (
            f"besichtigung_{tagesliste_id}_2d.png"
        )

        preview_3d_filename = (
            f"besichtigung_{tagesliste_id}_3d.png"
        )

        preview_2d_path = os.path.join(
            preview_dir,
            preview_2d_filename
        )

        preview_3d_path = os.path.join(
            preview_dir,
            preview_3d_filename
        )

        preview_2d.save(
            preview_2d_path
        )

        preview_3d.save(
            preview_3d_path
        )

        preview_2d_url = (
            f"/internal/kg-scan/preview/"
            f"{preview_2d_filename}"
        )

        preview_3d_url = (
            f"/internal/kg-scan/preview/"
            f"{preview_3d_filename}"
        )
        existing = {}

        if row["besichtigung_data_json"]:
            try:
                existing = json.loads(
                    row["besichtigung_data_json"]
                )
            except Exception:
                existing = {}

        if not isinstance(existing, dict):
            existing = {}

        kg_scan = existing.get("kg_scan") or {}

        if not isinstance(kg_scan, dict):
            kg_scan = {}

        kg_scan["preview_2d_url"] = (
            preview_2d_url
        )

        kg_scan["preview_3d_url"] = (
            preview_3d_url
        )

        kg_scan["preview_updated_at"] = (
            datetime.now().isoformat()
        )

        existing["kg_scan"] = kg_scan

        conn.execute("""
            UPDATE tagesliste_leads
            SET besichtigung_data_json = ?
            WHERE id = ?
        """, (
            json.dumps(
                existing,
                ensure_ascii=False
            ),
            tagesliste_id
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "preview_2d_url": preview_2d_url,
            "preview_3d_url": preview_3d_url
        })

    @app.route(
        "/internal/kg-scan/preview/<filename>",
        methods=["GET"]
    )
    @login_required
    def kg_scan_preview_file(filename):

        safe_filename = os.path.basename(
            filename
        )

        if (
            safe_filename != filename
            or not safe_filename.lower().endswith(".png")
        ):
            return Response(
                status=404
            )

        preview_dir = os.path.join(
            os.path.dirname(DB_PATH),
            "kg_scan_previews"
        )

        preview_path = os.path.join(
            preview_dir,
            safe_filename
        )

        if not os.path.isfile(
            preview_path
        ):
            return Response(
                status=404
            )

        with open(
            preview_path,
            "rb"
        ) as preview_file:
            preview_data = preview_file.read()

        return Response(
            preview_data,
            mimetype="image/png",
            headers={
                "Cache-Control": "no-store"
            }
        )

    @app.route("/internal/kg-scan/result", methods=["POST"])
    def kg_scan_result():
        if not kg_scan_token_ok():
            return jsonify({
                "ok": False,
                "message": "Unauthorized"
            }), 403

        ensure_tagesliste_table()

        data = request.get_json(silent=True) or {}

        try:
            tagesliste_id = int(data.get("tagesliste_id") or 0)
        except Exception:
            tagesliste_id = 0

        if tagesliste_id <= 0:
            return jsonify({
                "ok": False,
                "message": "Besichtigung-ID fehlt."
            }), 400

        scan_rooms = data.get("raeume") or []

        if not isinstance(scan_rooms, list):
            scan_rooms = []

        conn = sqlite3.connect(
            DB_PATH,
            timeout=30
        )
        conn.execute(
            "PRAGMA busy_timeout = 30000"
        )
        conn.row_factory = sqlite3.Row

        row = conn.execute("""
            SELECT
                firma,
                ansprechpartner,
                strasse,
                plz,
                ort,
                telefon,
                email,
                besichtigung_data_json
            FROM tagesliste_leads
            WHERE id = ?
            AND status = 'besichtigung'
        """, (tagesliste_id,)).fetchone()

        if not row:
            conn.close()

            return jsonify({
                "ok": False,
                "message": "Besichtigung wurde nicht gefunden."
            }), 404

        existing = {}

        if row["besichtigung_data_json"]:
            try:
                existing = json.loads(row["besichtigung_data_json"])
            except Exception:
                existing = {}

        if not isinstance(existing, dict):
            existing = {}

        vorhandene_leistungen = existing.get("leistungen")

        if not isinstance(vorhandene_leistungen, list):
            vorhandene_leistungen = []

        if not vorhandene_leistungen:
            vorhandene_leistungen = ["Unterhaltsreinigung"]

        neue_raeume = []

        for index, room in enumerate(scan_rooms, start=1):

            if not isinstance(room, dict):
                continue

            typ = str(
                room.get("typ")
                or room.get("type")
                or room.get("roomType")
                or "Büroraum"
            ).strip()

            name = str(
                room.get("name")
                or f"{typ} {index}"
            ).strip()

            try:
                m2 = float(
                    str(
                        room.get("m2")
                        or room.get("area")
                        or 0
                    ).replace(",", ".")
                )
            except Exception:
                m2 = 0.0

            alte_frequenz = ""

            for old_room in existing.get("raeume") or []:
                if not isinstance(old_room, dict):
                    continue

                old_name = str(old_room.get("name") or "").strip().lower()
                old_typ = str(old_room.get("typ") or "").strip().lower()

                if (
                    old_name == name.lower()
                    or (
                        old_typ == typ.lower()
                        and not alte_frequenz
                    )
                ):
                    alte_frequenz = str(
                        old_room.get("haeufigkeit") or ""
                    ).strip()
                    break

            neue_raeume.append({
                "section": "Unterhaltsreinigung",
                "typ": typ,
                "name": name,
                "m2": round(m2, 2),
                "haeufigkeit": alte_frequenz
            })

        scan_data = {
            "quelle": "KG Scan",
            "kunde": {
                "firma": row["firma"] or "",
                "ansprechpartner": row["ansprechpartner"] or "",
                "strasse": row["strasse"] or "",
                "plz": row["plz"] or "",
                "ort": row["ort"] or "",
                "telefon": row["telefon"] or "",
                "email": row["email"] or ""
            },
            "leistungen": vorhandene_leistungen,
            "raeume": neue_raeume,
            "elemente": existing.get("elemente") or {
                "arbeitstische": "0",
                "pc_monitor": "0",
                "muellbehaelter": "0"
            },
            "sonstiges": existing.get("sonstiges") or [],
            "einsatzzeiten": existing.get("einsatzzeiten") or [],
            "notiz": existing.get("notiz") or "",
            "kg_scan": {
                "windows": data.get("windows") or 0,
                "window_area": data.get("window_area") or 0,
                "doors": data.get("doors") or 0,
                "tables": data.get("tables") or 0,
                "chairs": data.get("chairs") or 0,
                "printers": data.get("printers") or 0,
                "toilets": data.get("toilets") or 0,
                "urinals": data.get("urinals") or 0,
                "sinks": data.get("sinks") or 0,
                "geometry": data.get("geometry") or [],
                "model_id": data.get("model_id") or "",
                "gescannt_am": datetime.now().isoformat()
            }
        }

        conn.execute("""
            UPDATE tagesliste_leads
            SET besichtigung_data_json = ?
            WHERE id = ?
        """, (
            json.dumps(scan_data, ensure_ascii=False),
            tagesliste_id
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Scan wurde in CRM gespeichert.",
            "tagesliste_id": tagesliste_id,
            "besichtigung_data": scan_data
        })

# =====================================================
# APP2 - BÖLÜM 1.1 - APIFY LEAD IMPORT API
# =====================================================
    @app.route("/api/datenbank/apify-import", methods=["POST"])
    @login_required
    def app2_apify_import():
        try:
            data = request.get_json(silent=True) or {}

            branche_id = str(data.get("branche_id", "")).strip()
            branche_name = str(data.get("branche_name", "")).strip()
            suchwort = str(data.get("suchwort", "")).strip()

            # Manuel import: ekrandan hangi şehir girildiyse onu kullan.
            # Örn: Moers, Duisburg, Düsseldorf, Ratingen...
            stadt = str(data.get("stadt", "")).strip()

            plz = str(data.get("plz", "")).strip()

            # PLZ şehirden bağımsız olsun. Sadece 5 haneli değilse temizle.
            if plz and not re.fullmatch(r"\d{5}", plz):
                plz = ""

            try:
                radius_km = int(data.get("radius_km", 10) or 10)
            except Exception:
                radius_km = 10

            if radius_km < 1 or radius_km > 50:
                return jsonify({
                    "success": False,
                    "message": "Umkreis bitte zwischen 1 und 50 km eingeben."
                }), 400

            try:
                google_requests = int(data.get("google_requests", 1) or 1)
            except Exception:
                google_requests = 1


            if google_requests < 1 or google_requests > 30:
                return jsonify({
                    "success": False,
                    "message": "Google-Anfragen bitte zwischen 1 und 30 eingeben."
                }), 400

            max_results = google_requests * 20

            if not stadt:
                return jsonify({
                    "success": False,
                    "message": "Stadt ist Pflichtfeld."
                }), 400
            if not branche_id or not branche_name or not suchwort:
                return jsonify({
                    "success": False,
                    "message": "Branche und Suchwort sind Pflichtfelder."
                }), 400

            result = run_apify_import(
                db_path=DB_PATH,
                branche_id=branche_id,
                branche_name=branche_name,
                suchwort=suchwort,
                stadt=stadt,
                max_results=max_results,
                plz=plz,
                radius_km=radius_km
            )

            if isinstance(result, dict):
                result["google_requests"] = google_requests
                result["requested_company_target"] = max_results
                result["email_found"] = 0
                result["email_missing"] = 0

            return jsonify(result)

        except Exception as e:
            return jsonify({
                "success": False,
                "message": "Import Fehler: " + str(e)
            }), 500


# =====================================================
# APP2 - YENİ BÖLÜM - BRANCHEN DETAIL ROUTE
# =====================================================

    @app.route("/branche-detail")
    @app.route("/branche-detail.html")
    @login_required
    def app2_branche_detail():
        branche = request.args.get("branche", "").strip()

        branche_map = {
            "buero-verwaltung": "1",
            "medizin-gesundheit": "2",
            "pflege-soziales": "3",
            "bildung-betreuung": "4",
            "einzelhandel-verkaufsflaechen": "5",
            "fitness-sport-freizeit": "6",
            "industrie-produktion": "7",
            "lager-logistik-grosshandel": "8",
            "immobilien-hausverwaltung": "9",
            "finanzen-versicherung-beratung": "10",
            "it-medien-kommunikation": "11",
            "sonstige": "12"
        }

        branche_id = branche_map.get(branche, "")

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        try:
            conn.execute("ALTER TABLE leads ADD COLUMN sort_order INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass

        if branche_id:
            leads = conn.execute("""
                SELECT *
                FROM leads
                WHERE branche_id = ?
                AND (status IS NULL OR status != 'Tagesliste')
                ORDER BY
                    CASE WHEN sort_order IS NULL OR sort_order = 0 THEN 1 ELSE 0 END,
                    sort_order ASC,
                    CASE WHEN plz IS NULL OR plz = '' THEN 1 ELSE 0 END,
                    plz ASC,
                    firma ASC
            """, (branche_id,)).fetchall()
        else:
            leads = conn.execute("""
                SELECT *
                FROM leads
                WHERE status IS NULL OR status != 'Tagesliste'
                ORDER BY
                    CASE WHEN sort_order IS NULL OR sort_order = 0 THEN 1 ELSE 0 END,
                    sort_order ASC,
                    CASE WHEN plz IS NULL OR plz = '' THEN 1 ELSE 0 END,
                    plz ASC,
                    firma ASC
            """).fetchall()
        leads = [dict(row) for row in leads]

        conn.close()

        return render_template(
            "branche_detail.html",
            leads=leads,
            branche=branche,
            branche_id=branche_id
        )

# =====================================================
# APP2 - BRANCHEN DETAIL SORTIERUNG SPEICHERN
# =====================================================

    @app.route("/api/branche-detail/save-order", methods=["POST"])
    @login_required
    def app2_branche_detail_save_order():
        data = request.get_json(silent=True) or {}
        ids = data.get("ids") or []

        if not isinstance(ids, list):
            return jsonify({
                "ok": False,
                "message": "Ungültige Reihenfolge."
            }), 400

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        try:
            cursor.execute("ALTER TABLE leads ADD COLUMN sort_order INTEGER DEFAULT 0")
        except Exception:
            pass

        for index, lead_id in enumerate(ids, start=1):
            try:
                cursor.execute("""
                    UPDATE leads
                    SET sort_order = ?
                    WHERE id = ?
                """, (index, int(lead_id)))
            except Exception:
                pass

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Reihenfolge gespeichert."
        })


# =====================================================
# APP2 - BRANCHEN DETAIL MANUELLE FIRMA HINZUFÜGEN
# =====================================================

    @app.route("/api/branche-detail/manual-add", methods=["POST"])
    @login_required
    def app2_branche_detail_manual_add():
        data = request.get_json(silent=True) or {}

        branche_id = str(data.get("branche_id") or "").strip()
        firma = str(data.get("firma") or "").strip()
        strasse = str(data.get("strasse") or "").strip()
        plz = str(data.get("plz") or "").strip()
        stadt = str(data.get("stadt") or "").strip()
        telefon = str(data.get("telefon") or "").strip()
        email = str(data.get("email") or "").strip()
        website = str(data.get("website") or "").strip()

        if not firma:
            return jsonify({
                "ok": False,
                "message": "Firma fehlt."
            }), 400

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        try:
            cursor.execute("ALTER TABLE leads ADD COLUMN sort_order INTEGER DEFAULT 0")
        except Exception:
            pass

        cursor.execute("""
            INSERT INTO leads
            (
                branche_id,
                firma,
                strasse,
                plz,
                stadt,
                telefon,
                email,
                website,
                quelle,
                status,
                sort_order
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            branche_id,
            firma,
            strasse,
            plz,
            stadt,
            telefon,
            email,
            website,
            "Manuell",
            "Neu",
            0
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Firma wurde gespeichert."
        })


# =====================================================
# APP2 - BRANCHEN DETAIL FIRMA LÖSCHEN
# =====================================================

    @app.route("/api/branche-detail/delete-lead", methods=["POST"])
    @login_required
    def app2_branche_detail_delete_lead():
        data = request.get_json(silent=True) or {}

        try:
            lead_id = int(data.get("lead_id") or 0)
        except:
            lead_id = 0

        if lead_id <= 0:
            return jsonify({
                "ok": False,
                "message": "Lead-ID fehlt."
            }), 400

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM leads
            WHERE id = ?
        """, (lead_id,))

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Firma wurde gelöscht."
        })

# =====================================================
# APP2 - YENİ BÖLÜM - TAGESLISTE KAYIT ROUTE
# =====================================================

    @app.route("/datenbank/tagesliste-add", methods=["POST"])
    @login_required
    def app2_tagesliste_add():
        ensure_tagesliste_table()

        data = request.get_json(silent=True) or {}

        firma = data.get("firma", "").strip()
        branche = data.get("branche", "").strip()
        ansprechpartner = data.get("ansprechpartner", "").strip()
        strasse = data.get("strasse", "").strip()
        plz = data.get("plz", "").strip()
        ort = data.get("ort", "").strip()
        telefon = data.get("telefon", "").strip()
        email = data.get("email", "").strip()
        website = data.get("website", "").strip()
        google_maps_url = data.get("google_maps_url", "").strip()
       
        quelle = data.get("quelle", "Branchenliste").strip()

        try:
            source_lead_id = int(data.get("source_lead_id") or 0)
        except:
            source_lead_id = 0

        if not firma:
            return jsonify({
                "ok": False,
                "message": "Firma fehlt."
            }), 400

        company_key = f"{firma}|{telefon}|{email}".lower()

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        try:
            cursor.execute("ALTER TABLE tagesliste_leads ADD COLUMN google_maps_url TEXT")
        except Exception:
            pass

        if not google_maps_url and source_lead_id > 0:
            try:
                row = cursor.execute(
                    "SELECT google_maps_url FROM leads WHERE id = ?",
                    (source_lead_id,)
                ).fetchone()
                if row:
                    google_maps_url = row["google_maps_url"] or ""
            except Exception:
                google_maps_url = ""

        cursor.execute("""
            INSERT OR IGNORE INTO tagesliste_leads
            (
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
                company_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
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
            "offen",
            company_key
        ))

        inserted = cursor.rowcount
        new_id = cursor.lastrowid if inserted == 1 else 0

        if source_lead_id > 0:
            cursor.execute("""
                UPDATE leads
                SET status = 'Tagesliste'
                WHERE id = ?
            """, (source_lead_id,))

        conn.commit()
        conn.close()

        if inserted == 0:
            return jsonify({
                "ok": True,
                "duplicate": True,
                "message": "Firma ist bereits in der Tagesliste."
            })

        return jsonify({
            "ok": True,
            "duplicate": False,
            "id": new_id,
            "message": "Firma wurde zur Tagesliste hinzugefügt."
        })

# =====================================================
# APP2 - TAGESLISTE KONTAKTDATEN + NOTIZ SPEICHERN
# =====================================================

    @app.route("/datenbank/tagesliste-update", methods=["POST"])
    @login_required
    def app2_tagesliste_update():
        ensure_tagesliste_table()

        data = request.get_json(silent=True) or {}

        try:
            tagesliste_id = int(data.get("id") or 0)
        except:
            tagesliste_id = 0

        ansprechpartner = str(data.get("ansprechpartner") or "").strip()
        telefon = str(data.get("telefon") or "").strip()
        email = str(data.get("email") or "").strip()
        website = str(data.get("website") or "").strip()
        notiz = str(data.get("notiz") or "")

        if tagesliste_id <= 0:
            return jsonify({
                "ok": False,
                "message": "Tagesliste-ID fehlt."
            }), 400

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE tagesliste_leads
            SET
                ansprechpartner = ?,
                telefon = ?,
                email = ?,
                website = ?,
                notiz = ?
            WHERE id = ?
        """, (
            ansprechpartner,
            telefon,
            email,
            website,
            notiz,
            tagesliste_id
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Kontaktdaten wurden gespeichert."
        })


# =====================================================
# APP2 - TAGESLISTE KONTAKTDATEN SPEICHERN
# Ansprechpartner / Telefon / E-Mail / Webseite
# =====================================================

    @app.route("/datenbank/tagesliste-contact-update", methods=["POST"])
    @login_required
    def app2_tagesliste_contact_update():
        ensure_tagesliste_table()

        data = request.get_json(silent=True) or {}

        try:
            tagesliste_id = int(data.get("id") or 0)
        except:
            tagesliste_id = 0

        ansprechpartner = str(data.get("ansprechpartner") or "").strip()
        telefon = str(data.get("telefon") or "").strip()
        email = str(data.get("email") or "").strip()
        website = str(data.get("website") or "").strip()

        if tagesliste_id <= 0:
            return jsonify({
                "ok": False,
                "message": "Tagesliste-ID fehlt."
            }), 400

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE tagesliste_leads
            SET
                ansprechpartner = ?,
                telefon = ?,
                email = ?,
                website = ?
            WHERE id = ?
        """, (
            ansprechpartner,
            telefon,
            email,
            website,
            tagesliste_id
        ))

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Kontaktdaten wurden gespeichert."
        })

# =====================================================
# BESICHTIGUNG KAYDINI SİL
# =====================================================

    @app.route("/api/datenbank/besichtigung-delete", methods=["POST"])
    @login_required
    def app2_besichtigung_delete():
        ensure_tagesliste_table()

        data = request.get_json(silent=True) or {}

        try:
            tagesliste_id = int(data.get("id") or 0)
        except Exception:
            tagesliste_id = 0

        if tagesliste_id <= 0:
            return jsonify({
                "ok": False,
                "message": "Besichtigung-ID fehlt."
            }), 400

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM tagesliste_leads
            WHERE id = ?
            AND status = 'besichtigung'
        """, (tagesliste_id,))

        cursor.execute("""
            DELETE FROM tagesliste_status_backup
            WHERE tagesliste_id = ?
            AND status = 'besichtigung'
        """, (tagesliste_id,))

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Besichtigung wurde gelöscht."
        })

# =====================================================
# STATUS LİSTESİNDEKİ TEK KAYDI SİL
# =====================================================

    @app.route("/api/datenbank/status-record-delete", methods=["POST"])
    @login_required
    def app2_status_record_delete():
        ensure_tagesliste_table()

        data = request.get_json(silent=True) or {}

        try:
            tagesliste_id = int(data.get("id") or 0)
        except Exception:
            tagesliste_id = 0

        status = str(data.get("status") or "").strip().lower()

        erlaubte_status = [
            "angerufen",
            "interessiert",
            "besichtigung",
            "angebot",
            "kontaktformular",
            "spaeter",
            "verloren"
        ]

        if tagesliste_id <= 0:
            return jsonify({
                "ok": False,
                "message": "Tagesliste-ID fehlt."
            }), 400

        if status not in erlaubte_status:
            return jsonify({
                "ok": False,
                "message": "Ungültiger Status."
            }), 400

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            DELETE FROM tagesliste_status_backup
            WHERE tagesliste_id = ?
            AND status = ?
        """, (tagesliste_id, status))

        cursor.execute("""
            DELETE FROM tagesliste_status_history
            WHERE tagesliste_id = ?
            AND status = ?
        """, (tagesliste_id, status))

        if status == "angerufen":
            cursor.execute("""
                UPDATE tagesliste_leads
                SET angerufen_am = NULL
                WHERE id = ?
            """, (tagesliste_id,))

        elif status == "interessiert":
            cursor.execute("""
                UPDATE tagesliste_leads
                SET interessiert_am = NULL
                WHERE id = ?
            """, (tagesliste_id,))

        cursor.execute("""
            UPDATE tagesliste_leads
            SET
                status = CASE
                    WHEN status = ? THEN 'offen'
                    ELSE status
                END,
                spaeter_datum = CASE
                    WHEN ? = 'spaeter' THEN NULL
                    ELSE spaeter_datum
                END
            WHERE id = ?
        """, (status, status, tagesliste_id))

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Eintrag wurde gelöscht."
        })

# =====================================================
# APP2 - TAGESLISTE'DEN GERİ AL / SİL
# =====================================================

    @app.route("/datenbank/tagesliste-remove", methods=["POST"])
    @login_required
    def app2_tagesliste_remove():
        ensure_tagesliste_table()

        data = request.get_json(silent=True) or {}

        try:
            tagesliste_id = int(data.get("id") or 0)
        except:
            tagesliste_id = 0

        try:
            source_lead_id = int(data.get("source_lead_id") or 0)
        except:
            source_lead_id = 0

        status = str(data.get("status") or "offen").strip().lower()
        statuses_raw = data.get("statuses") or []
        erlaubte_backup_status = ["angerufen", "interessiert", "besichtigung", "kontaktformular", "spaeter", "verloren"]

        if not isinstance(statuses_raw, list):
            statuses_raw = []

        backup_statuses = []
        for s in statuses_raw:
            s = str(s or "").strip().lower()
            if s in erlaubte_backup_status and s not in backup_statuses:
                backup_statuses.append(s)

        if not backup_statuses and status in erlaubte_backup_status:
            backup_statuses = [status]

        if tagesliste_id <= 0:
            return jsonify({
                "ok": False,
                "message": "Tagesliste-ID fehlt."
            }), 400

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        for backup_status in backup_statuses:
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
            """, (backup_status, tagesliste_id))

        cursor.execute("""
            DELETE FROM tagesliste_leads
            WHERE id = ?
        """, (tagesliste_id,))

        if source_lead_id > 0 and status == "offen":
            cursor.execute("""
                UPDATE leads
                SET status = 'Neu'
                WHERE id = ?
            """, (source_lead_id,))

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Firma wurde aus der Tagesliste entfernt."
        })

# =====================================================
# APP2 - TAGESLISTE NOTIZ SPEICHERN
# =====================================================

    @app.route("/datenbank/tagesliste-note", methods=["POST"])
    @login_required
    def app2_tagesliste_note():
        ensure_tagesliste_table()

        data = request.get_json(silent=True) or {}

        try:
            tagesliste_id = int(data.get("id") or 0)
        except:
            tagesliste_id = 0

        notiz = str(data.get("notiz") or "")

        if tagesliste_id <= 0:
            return jsonify({
                "ok": False,
                "message": "Tagesliste-ID fehlt."
            }), 400

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE tagesliste_leads
            SET notiz = ?
            WHERE id = ?
        """, (notiz, tagesliste_id))

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Notiz wurde gespeichert."
        })    
    
# =====================================================
# APP2 - TAGESLISTE STATUS SPEICHERN
# =====================================================

    @app.route("/datenbank/tagesliste-status", methods=["POST"])
    @login_required
    def app2_tagesliste_status():
        ensure_tagesliste_table()

        data = request.get_json(silent=True) or {}

        try:
            tagesliste_id = int(data.get("id") or 0)
        except:
            tagesliste_id = 0

        status = str(data.get("status") or "offen").strip().lower()
        reset_status = str(data.get("reset_status") or "").strip().lower()
        spaeter_datum = str(data.get("spaeter_datum") or "").strip()

        besichtigung_data_json = data.get("besichtigung_data_json", "")

        if isinstance(besichtigung_data_json, (dict, list)):
            besichtigung_data_json = json.dumps(besichtigung_data_json, ensure_ascii=False)
        else:
            besichtigung_data_json = str(besichtigung_data_json or "").strip()

        erlaubte_status = [
            "offen",
            "angerufen",
            "interessiert",
            "besichtigung",
            "angebot",
            "kontaktformular",
            "spaeter",
            "verloren"
        ]

        if status not in erlaubte_status:
            return jsonify({
                "ok": False,
                "message": "Ungültiger Status."
            }), 400

        if tagesliste_id <= 0:
            return jsonify({
                "ok": False,
                "message": "Tagesliste-ID fehlt."
            }), 400

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        status_zeit = ""

        berlin_zeit = datetime.now(ZoneInfo("Europe/Berlin"))
        db_zeit = berlin_zeit.strftime("%Y-%m-%d %H:%M:%S")
        status_zeit = berlin_zeit.strftime("%d.%m.%Y %H:%M Uhr")

        if status == "angerufen":
            cursor.execute("""
                UPDATE tagesliste_leads
                SET angerufen_am = ?
                WHERE id = ?
            """, (db_zeit, tagesliste_id))

        elif status == "interessiert":
            cursor.execute("""
                UPDATE tagesliste_leads
                SET interessiert_am = ?
                WHERE id = ?
            """, (db_zeit, tagesliste_id))

        else:
            status_zeit = ""


        if status == "spaeter":
            cursor.execute("""
                UPDATE tagesliste_leads
                SET
                    status = 'offen',
                    spaeter_datum = ?,
                    besichtigung_data_json = COALESCE(NULLIF(?, ''), besichtigung_data_json)
                WHERE id = ?
            """, (spaeter_datum, besichtigung_data_json, tagesliste_id))

        elif status == "besichtigung":
            cursor.execute("""
                UPDATE tagesliste_leads
                SET
                    status = CASE
                        WHEN quelle LIKE 'Direktes Angebot · %'
                        THEN 'besichtigung'
                        ELSE 'offen'
                    END,
                    spaeter_datum = NULL,
                    besichtigung_data_json = COALESCE(NULLIF(?, ''), besichtigung_data_json),
                    angebot_vars_json = NULL,
                    angebot_nr = NULL,
                    angebot_datum = NULL,
                    angebot_netto = NULL,
                    angebot_mwst = NULL,
                    angebot_brutto = NULL
                WHERE id = ?
            """, (besichtigung_data_json, tagesliste_id))

        else:
            cursor.execute("""
                UPDATE tagesliste_leads
                SET
                    status = 'offen',
                    spaeter_datum = NULL,
                    besichtigung_data_json = COALESCE(NULLIF(?, ''), besichtigung_data_json)
                WHERE id = ?
            """, (besichtigung_data_json, tagesliste_id))


        if status == "offen":

            if reset_status == "angerufen":
                cursor.execute("""
                    UPDATE tagesliste_leads
                    SET angerufen_am = NULL
                    WHERE id = ?
                """, (tagesliste_id,))

            elif reset_status == "interessiert":
                cursor.execute("""
                    UPDATE tagesliste_leads
                    SET interessiert_am = NULL
                    WHERE id = ?
                """, (tagesliste_id,))

            cursor.execute("""
                DELETE FROM tagesliste_status_history
                WHERE tagesliste_id = ?
                AND status = ?
            """, (tagesliste_id, reset_status))

            cursor.execute("""
                DELETE FROM tagesliste_status_backup
                WHERE tagesliste_id = ?
                AND status = ?
            """, (tagesliste_id, reset_status))


        elif status in ["angerufen", "interessiert", "besichtigung", "angebot", "kontaktformular", "spaeter", "verloren"]:
            cursor.execute("""
                INSERT OR IGNORE INTO tagesliste_status_history
                    (tagesliste_id, status, datum)
                VALUES
                    (?, ?, date('now','localtime'))
            """, (tagesliste_id, status))

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
                    angerufen_am,
                    interessiert_am,
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
                    angerufen_am,
                    interessiert_am,
                    CURRENT_TIMESTAMP
                FROM tagesliste_leads
                WHERE id = ?
            """, (status, tagesliste_id))


        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Status wurde gespeichert.",
            "status_zeit": status_zeit
        })

# =====================================================
# APP2 - ANGEBOT PREVIEW / HESAPLAMA TEST
# Bu route henüz firmayı taşımaz, Warteliste'ye kayıt açmaz.
# Sadece mevcut Besichtigung verisinden Netto hesaplar.
# =====================================================

    @app.route("/datenbank/angebot-preview", methods=["POST"])
    @login_required
    def app2_angebot_preview():
        ensure_tagesliste_table()

        data = request.get_json(silent=True) or {}

        try:
            tagesliste_id = int(data.get("id") or 0)
        except Exception:
            tagesliste_id = 0

        besichtigung_data = data.get("besichtigung_data_json") or {}

        if isinstance(besichtigung_data, str):
            try:
                besichtigung_data = json.loads(besichtigung_data)
            except Exception:
                besichtigung_data = {}

        if not isinstance(besichtigung_data, dict):
            besichtigung_data = {}

        # Eğer frontend veri göndermediyse, DB'deki son Speichern kaydını oku.
        if not besichtigung_data and tagesliste_id > 0:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            row = conn.execute("""
                SELECT besichtigung_data_json
                FROM tagesliste_leads
                WHERE id = ?
            """, (tagesliste_id,)).fetchone()
            conn.close()

            if row and row["besichtigung_data_json"]:
                try:
                    besichtigung_data = json.loads(row["besichtigung_data_json"])
                except Exception:
                    besichtigung_data = {}

        berechnung = angebot_calculate_from_besichtigung(besichtigung_data)

        return jsonify({
            "ok": True,
            "message": "Angebot Preview berechnet.",
            "tagesliste_id": tagesliste_id,
            "berechnung": berechnung
        })


# =====================================================
# APP2 - ANGEBOT SENDEN / WARTELISTEYE AKTAR
# Bu route çağrılınca Besichtigung kaydı Angebot durumuna geçer.
# Frontend butonu sonraki adımda bağlanacak.
# =====================================================

    @app.route("/datenbank/angebot-senden", methods=["POST"])
    @login_required
    def app2_angebot_senden():
        ensure_tagesliste_table()

        data = request.get_json(silent=True) or {}

        try:
            tagesliste_id = int(data.get("id") or 0)
        except Exception:
            tagesliste_id = 0

        if tagesliste_id <= 0:
            return jsonify({
                "ok": False,
                "message": "Tagesliste-ID fehlt."
            }), 400

        besichtigung_data = data.get("besichtigung_data_json") or {}

        if isinstance(besichtigung_data, str):
            try:
                besichtigung_data = json.loads(besichtigung_data)
            except Exception:
                besichtigung_data = {}

        if not isinstance(besichtigung_data, dict):
            besichtigung_data = {}

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        row = cursor.execute("""
            SELECT *
            FROM tagesliste_leads
            WHERE id = ?
        """, (tagesliste_id,)).fetchone()

        if not row:
            backup_row = cursor.execute("""
                SELECT *
                FROM tagesliste_status_backup
                WHERE tagesliste_id = ?
                AND status = 'besichtigung'
                ORDER BY backup_am DESC
                LIMIT 1
            """, (tagesliste_id,)).fetchone()

            if not backup_row:
                conn.close()
                return jsonify({
                    "ok": False,
                    "message": "Firma wurde nicht gefunden."
                }), 404

            cursor.execute("""
                INSERT OR IGNORE INTO tagesliste_leads (
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
                    status,
                    notiz,
                    spaeter_datum,
                    company_key,
                    erstellt_am
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'besichtigung', ?, NULL, ?, COALESCE(?, CURRENT_TIMESTAMP))
            """, (
                int(backup_row["tagesliste_id"] or tagesliste_id),
                backup_row["source_lead_id"],
                backup_row["firma"],
                backup_row["branche"],
                backup_row["ansprechpartner"],
                backup_row["strasse"],
                backup_row["plz"],
                backup_row["ort"],
                backup_row["telefon"],
                backup_row["email"],
                backup_row["website"],
                backup_row["quelle"],
                backup_row["notiz"],
                f"{backup_row['firma']}|{backup_row['telefon']}|{backup_row['email']}".lower(),
                backup_row["erstellt_am"]
            ))

            row = cursor.execute("""
                SELECT *
                FROM tagesliste_leads
                WHERE id = ?
            """, (tagesliste_id,)).fetchone()

            if not row:
                conn.close()
                return jsonify({
                    "ok": False,
                    "message": "Firma konnte nicht wiederhergestellt werden."
                }), 500

        if not besichtigung_data:
            try:
                besichtigung_data = json.loads(row["besichtigung_data_json"] or "{}")
            except Exception:
                besichtigung_data = {}

        berechnung = angebot_calculate_from_besichtigung(besichtigung_data)

        angebot_nr = str(row["angebot_nr"] or "").strip()
        if not angebot_nr:
            angebot_nr = angebot_next_number(cursor)

        angebot_datum = str(row["angebot_datum"] or "").strip()
        if not angebot_datum:
            angebot_datum = angebot_today_de()

        angebot_vars = angebot_build_template_vars(
            besichtigung_data=besichtigung_data,
            berechnung=berechnung,
            nr=angebot_nr,
            datum=angebot_datum
        )

        cursor.execute("""
            UPDATE tagesliste_leads
            SET
                status = 'angebot',
                spaeter_datum = NULL,
                besichtigung_data_json = ?,
                angebot_vars_json = ?,
                angebot_nr = ?,
                angebot_datum = ?,
                angebot_netto = ?,
                angebot_mwst = ?,
                angebot_brutto = ?
            WHERE id = ?
        """, (
            json.dumps(besichtigung_data, ensure_ascii=False),
            json.dumps(angebot_vars, ensure_ascii=False),
            angebot_nr,
            angebot_datum,
            float(berechnung.get("netto") or 0),
            float(berechnung.get("mwst") or 0),
            float(berechnung.get("brutto") or 0),
            tagesliste_id
        ))

        cursor.execute("""
            INSERT OR IGNORE INTO tagesliste_status_history
                (tagesliste_id, status, datum)
            VALUES
                (?, 'angebot', date('now','localtime'))
        """, (tagesliste_id,))

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Angebot wurde erstellt.",
            "tagesliste_id": tagesliste_id,
            "angebot_nr": angebot_nr,
            "angebot_datum": angebot_datum,
            "berechnung": berechnung,
            "angebot_vars": angebot_vars
        })


# =====================================================
# APP2 - TAGESLISTE STATUSA GÖRE FİRMA LİSTESİ
# =====================================================

    @app.route("/api/datenbank/by-status")
    @login_required
    def app2_datenbank_by_status():
        ensure_tagesliste_table()

        status = str(request.args.get("status") or "").strip().lower()

        erlaubte_status = [
            "angerufen",
            "interessiert",
            "besichtigung",
            "angebot",
            "kontaktformular",
            "spaeter",
            "verloren"
        ]

        if status not in erlaubte_status:
            return jsonify([])

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        if status == "angebot":
            rows = conn.execute("""
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
                    status,
                    notiz,
                    spaeter_datum,
                    erstellt_am,
                    besichtigung_data_json,
                    angebot_vars_json,
                    angebot_nr,
                    angebot_datum,
                    angebot_netto,
                    angebot_mwst,
                    angebot_brutto
                FROM tagesliste_leads
                WHERE status = 'angebot'
                ORDER BY id DESC
            """).fetchall()

        elif status == "besichtigung":
            rows = conn.execute("""
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
                    status,
                    notiz,
                    spaeter_datum,
                    erstellt_am,
                    besichtigung_data_json,
                    angebot_vars_json,
                    angebot_nr,
                    angebot_datum,
                    angebot_netto,
                    angebot_mwst,
                    angebot_brutto
                FROM tagesliste_leads
                WHERE status = 'besichtigung'

                UNION ALL

                SELECT
                    b.tagesliste_id AS id,
                    b.source_lead_id,
                    b.firma,
                    b.branche,
                    b.ansprechpartner,
                    b.strasse,
                    b.plz,
                    b.ort,
                    b.telefon,
                    b.email,
                    b.website,
                    b.quelle,
                    b.status,
                    b.notiz,
                    b.spaeter_datum,
                    b.erstellt_am,
                    NULL AS besichtigung_data_json,
                    NULL AS angebot_vars_json,
                    NULL AS angebot_nr,
                    NULL AS angebot_datum,
                    0 AS angebot_netto,
                    0 AS angebot_mwst,
                    0 AS angebot_brutto
                FROM tagesliste_status_backup b
                WHERE b.status = 'besichtigung'
                AND NOT EXISTS (
                    SELECT 1
                    FROM tagesliste_leads t
                    WHERE t.id = b.tagesliste_id
                )

                ORDER BY id DESC
            """).fetchall()

        else:
            rows = conn.execute("""
                SELECT
                    tagesliste_id AS id,
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
                    angerufen_am,
                    interessiert_am,
                    (
                        SELECT GROUP_CONCAT(b2.status, ',')
                        FROM tagesliste_status_backup b2
                        WHERE b2.tagesliste_id = tagesliste_status_backup.tagesliste_id
                    ) AS status_history
                FROM tagesliste_status_backup


                WHERE status = ?
                ORDER BY backup_am DESC, tagesliste_id DESC
            """, (status,)).fetchall()

        result = [dict(row) for row in rows]

        conn.close()

        return jsonify(result)


# =====================================================
# APP2 - TAGESLISTE STATUS SAYILARI
# =====================================================

    @app.route("/api/datenbank/status-counts")
    @login_required
    def app2_datenbank_status_counts():
        ensure_tagesliste_table()

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT status, COUNT(*) AS count
            FROM tagesliste_status_backup
            WHERE status IN (
                'angerufen',
                'interessiert',
                'besichtigung',
                'kontaktformular',
                'spaeter',
                'verloren'
            )
            GROUP BY status
        """).fetchall()

        tagesliste_total = conn.execute("""
            SELECT COUNT(*) AS count
            FROM tagesliste_leads
            WHERE status IS NULL OR status = 'offen'
        """).fetchone()["count"]

        angebot_total = conn.execute("""
            SELECT COUNT(*) AS count
            FROM tagesliste_leads
            WHERE status = 'angebot'
        """).fetchone()["count"]

        month_row = conn.execute("""
            SELECT
                SUM(CASE WHEN status = 'angerufen' THEN 1 ELSE 0 END) AS month_angerufen,
                SUM(CASE WHEN status = 'interessiert' THEN 1 ELSE 0 END) AS month_interessiert,
                SUM(CASE WHEN status = 'besichtigung' THEN 1 ELSE 0 END) AS month_besichtigung,
                SUM(CASE WHEN status = 'spaeter' THEN 1 ELSE 0 END) AS month_spaeter
            FROM tagesliste_status_history
            WHERE strftime('%Y-%m', datum) = strftime('%Y-%m', 'now', 'localtime')
        """).fetchone()

        counts = {
            "tagesliste": int(tagesliste_total or 0),
            "month_angerufen": int(month_row["month_angerufen"] or 0),
            "month_interessiert": int(month_row["month_interessiert"] or 0),
            "month_besichtigung": int(month_row["month_besichtigung"] or 0),
            "month_spaeter": int(month_row["month_spaeter"] or 0),
            "angerufen": 0,
            "interessiert": 0,
            "besichtigung": 0,
            "angebot": int(angebot_total or 0),
            "kontaktformular": 0,
            "spaeter": 0,
            "verloren": 0
        }

        for row in rows:
            counts[row["status"]] = row["count"]

        conn.close()

        return jsonify(counts)


# =====================================================
# APP2 - BÖLÜM 2 - BESICHTIGUNG
# =====================================================

    @app.route("/besichtigung")
    @app.route("/besichtigung.html")
    @login_required
    def app2_besichtigung():
        return render_template(
            "besichtigung.html",
            Kunde=request.args.get("Kunde", ""),
            Adresse=request.args.get("Adresse", ""),
            Plz=request.args.get("Plz", ""),
            Ort=request.args.get("Ort", ""),
            Leistungsart=request.args.get("Leistungsart", ""),
            Ansprechpartner=request.args.get("Ansprechpartner", ""),
            Telefon=request.args.get("Telefon", ""),
            Email=request.args.get("Email", "")
        )


# =====================================================
# APP2 - BÖLÜM 3 - ANGEBOTVORLAGE
# =====================================================

    @app.route("/angebotvorlage")
    @app.route("/angebotvorlage.html")
    @login_required
    def app2_angebotvorlage():
        angebot_id = request.args.get("angebot_id", "").strip()

        if angebot_id:
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row

                row = conn.execute("""
                    SELECT angebot_vars_json
                    FROM tagesliste_leads
                    WHERE id = ?
                    AND angebot_vars_json IS NOT NULL
                    AND angebot_vars_json != ''
                """, (angebot_id,)).fetchone()

                conn.close()

                if row and row["angebot_vars_json"]:
                    angebot_vars = json.loads(row["angebot_vars_json"])

                    return render_template(
                        "angebotvorlage.html",
                        **angebot_vars
                    )
            except Exception as e:
                print("ANGEBOTVORLAGE DB FEHLER:", str(e))

        return render_template(
            "angebotvorlage.html",
            Kunde=request.args.get("Kunde", ""),
            Objekt=request.args.get("Objekt", ""),
            Adresse=request.args.get("Adresse", ""),
            Plz=request.args.get("Plz", ""),
            Ort=request.args.get("Ort", ""),
            Leistungsart=request.args.get("Leistungsart", ""),
            Nr=request.args.get("Nr", ""),
            Datum=request.args.get("Datum", ""),
            Leistung_1=request.args.get("Leistung_1", ""),
            Einheiten_1=request.args.get("Einheiten_1", ""),
            Preis_1=request.args.get("Preis_1", ""),
            Leistung_2=request.args.get("Leistung_2", ""),
            Einheiten_2=request.args.get("Einheiten_2", ""),
            Preis_2=request.args.get("Preis_2", ""),
            Leistung_3=request.args.get("Leistung_3", ""),
            Einheiten_3=request.args.get("Einheiten_3", ""),
            Preis_3=request.args.get("Preis_3", ""),
            Leistung_4=request.args.get("Leistung_4", ""),
            Einheiten_4=request.args.get("Einheiten_4", ""),
            Preis_4=request.args.get("Preis_4", ""),
            Ausführungszeitraum=request.args.get("Ausführungszeitraum", "")
        )


# =====================================================
# APP2 - BÖLÜM 4 - LEISTUNGSVERZEICHNIS
# Angebot'a dokunmaz. Sadece Angebot ID ile kaydedilmiş
# besichtigung_data_json içinden LV değişkenlerini hazırlar.
# =====================================================

    def lv_clean(value):
        return str(value or "").replace("Details", "").strip()

    def lv_join_de(items):
        items = [lv_clean(x) for x in items if lv_clean(x)]
        if not items:
            return ""
        if len(items) == 1:
            return items[0]
        if len(items) == 2:
            return items[0] + " und " + items[1]
        return ", ".join(items[:-1]) + " und " + items[-1]

    def lv_short_freq(value):
        text = lv_clean(value)

        if not text or text == "-":
            return "-"

        replacements = {
            "Täglich": "tägl.",
            "täglich": "tägl.",
            "1x wöchentlich": "1x wöch.",
            "2x wöchentlich": "2x wöch.",
            "3x wöchentlich": "3x wöch.",
            "4x wöchentlich": "4x wöch.",
            "5x wöchentlich": "5x wöch.",
            "14-tägig": "14-tägl.",
            "1x monatlich": "1x monatl.",
            "monatlich": "monatl.",
            "1x jährlich": "1x jährl.",
            "jährlich": "jährl."
        }

        for old, new in replacements.items():
            text = text.replace(old, new)

        return text

    def lv_parse_json(raw):
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw or "{}")
        except Exception:
            return {}

    def lv_area_freq(besichtigung_data, area_keys):
        raeume = besichtigung_data.get("raeume") or []
        if not isinstance(raeume, list):
            return "-"

        freqs = []

        for row in raeume:
            text = (
                lv_clean(row.get("typ", "")) + " " +
                lv_clean(row.get("name", ""))
            ).lower()

            if any(k in text for k in area_keys):
                freq = lv_clean(row.get("haeufigkeit", ""))
                if freq and freq.lower() not in ["bitte wählen", "bitte waehlen"]:
                    if freq not in freqs:
                        freqs.append(freq)

        return lv_short_freq(freqs[0]) if freqs else "-"

    def lv_find_sonstiges_value(besichtigung_data, label_key):
        rows = besichtigung_data.get("sonstiges") or []
        if not isinstance(rows, list):
            return ""

        label_key = label_key.lower()

        for row in rows:
            label = lv_clean(row.get("label", "")).lower()
            values = row.get("values") or []

            if label_key in label and values:
                return lv_clean(values[0])

        return ""

    def lv_find_sonstiges_next_freq(besichtigung_data, start_label_key, fallback="-"):
        rows = besichtigung_data.get("sonstiges") or []
        if not isinstance(rows, list):
            return fallback

        start_label_key = start_label_key.lower()

        for i, row in enumerate(rows):
            label = lv_clean(row.get("label", "")).lower()
            values = row.get("values") or []
            first_value = lv_clean(values[0]) if values else ""

            if start_label_key in label:
                if first_value.lower() in ["", "keine", "ohne", "bitte wählen", "bitte waehlen"]:
                    return "-"

                for next_row in rows[i + 1:i + 4]:
                    next_label = lv_clean(next_row.get("label", "")).lower()
                    next_values = next_row.get("values") or []

                    if "häufig" in next_label or "haeufig" in next_label:
                        if next_values:
                            val = lv_clean(next_values[0])
                            if val and val.lower() not in ["bitte wählen", "bitte waehlen"]:
                                return lv_short_freq(val)

                return fallback

        return fallback

    def lv_sonstiges_values(row):
        if not isinstance(row, dict):
            return []

        values = row.get("values") or []
        active = row.get("active") or []

        if not isinstance(values, list):
            values = [values]

        if not isinstance(active, list):
            active = [active]

        result = []

        for item in values + active:
            text = lv_clean(item)
            lower = text.lower()

            if not text:
                continue

            if lower in ["bitte wählen", "bitte waehlen", "ohne", "keine", "mit", "ja", "nein"]:
                continue

            if text not in result:
                result.append(text)

        return result

    def lv_find_sonstiges_row(besichtigung_data, label_keys):
        rows = besichtigung_data.get("sonstiges") or []

        if not isinstance(rows, list):
            return None

        keys = [str(k or "").lower() for k in label_keys]

        for row in rows:
            if not isinstance(row, dict):
                continue

            label = lv_clean(row.get("label", "")).lower()

            if any(k in label for k in keys):
                return row

        return None

    def lv_build_elektro_text(besichtigung_data):
        row = lv_find_sonstiges_row(
            besichtigung_data,
            ["welche geräte", "welche geraete", "mehrfachauswahl"]
        )

        selected = lv_sonstiges_values(row)

        mapping = {
            "pc": "PC",
            "bildschirm": "Bildschirm",
            "bildschirme": "Bildschirm",
            "telefon": "Telefon",
            "telefone": "Telefon",
            "drucker": "Drucker"
        }

        geraete = []

        for item in selected:
            key = item.lower().strip()
            text = mapping.get(key, item)

            if text not in geraete:
                geraete.append(text)

        if geraete:
            return lv_join_de(geraete) + " trocken/nebel-feucht entstauben"

        return "PC, Bildschirme, Telefone und Drucker trocken/nebel-feucht entstauben"

    def lv_build_spuelmaschine_text(besichtigung_data):
        row = lv_find_sonstiges_row(
            besichtigung_data,
            ["geschirrspüler", "geschirrspueler", "geschirr"]
        )

        values = lv_sonstiges_values(row)

        if not values:
            return "Geschirrspüler einräumen / ausräumen, sofern in der Besichtigung ausgewählt"

        action = values[0].lower()

        if "ein" in action and "aus" in action:
            return "Geschirrspüler einräumen und ausräumen"

        if "ein" in action:
            return "Geschirrspüler einräumen"

        if "aus" in action:
            return "Geschirrspüler ausräumen"

        return "Geschirrspüler " + values[0].lower()

    def lv_find_sonstiges_raw_value(besichtigung_data, label_key):
        rows = besichtigung_data.get("sonstiges") or []
        if not isinstance(rows, list):
            return ""

        label_key = str(label_key or "").lower()

        for row in rows:
            if not isinstance(row, dict):
                continue

            label = lv_clean(row.get("label", "")).lower()
            values = row.get("values") or []

            if label_key in label and values:
                return lv_clean(values[0])

        return ""

    def lv_find_sonstiges_next_value(besichtigung_data, start_label_key, next_label_keys, fallback=""):
        rows = besichtigung_data.get("sonstiges") or []
        if not isinstance(rows, list):
            return fallback

        start_label_key = str(start_label_key or "").lower()

        if isinstance(next_label_keys, str):
            next_label_keys = [next_label_keys]

        next_label_keys = [str(k or "").lower() for k in next_label_keys]

        for i, row in enumerate(rows):
            if not isinstance(row, dict):
                continue

            label = lv_clean(row.get("label", "")).lower()
            values = row.get("values") or []
            first_value = lv_clean(values[0]) if values else ""

            if start_label_key in label:
                if first_value.lower() in ["", "keine", "ohne", "nein", "bitte wählen", "bitte waehlen"]:
                    return ""

                for next_row in rows[i + 1:i + 5]:
                    if not isinstance(next_row, dict):
                        continue

                    next_label = lv_clean(next_row.get("label", "")).lower()
                    next_values = next_row.get("values") or []

                    if any(k in next_label for k in next_label_keys):
                        if next_values:
                            val = lv_clean(next_values[0])
                            if val and val.lower() not in ["bitte wählen", "bitte waehlen", "keine", "ohne"]:
                                return val

                return fallback

        return fallback

    def lv_is_active_choice(value):
        text = lv_clean(value).lower()
        return text not in ["", "keine", "ohne", "nein", "bitte wählen", "bitte waehlen"]

    def lv_add_zusatz(items, text, freq):
        text = lv_clean(text)
        freq = lv_clean(freq) or "-"

        if not text:
            return

        for item in items:
            if item.get("text") == text:
                return

        items.append({
            "text": text,
            "freq": freq
        })

    def lv_build_einsatz_text(besichtigung_data):
        result = []

        root_values = (
            besichtigung_data.get("einsatztage")
            or besichtigung_data.get("einsatzzeiten")
            or []
        )

        if isinstance(root_values, list):
            for item in root_values:
                if isinstance(item, dict):
                    tag = lv_clean(
                        item.get("tag")
                        or item.get("day")
                        or item.get("wochentag")
                        or item.get("label")
                    )
                    zeit = lv_clean(
                        item.get("uhrzeit")
                        or item.get("time")
                        or item.get("zeit")
                        or item.get("ab")
                    )

                    if tag:
                        if zeit:
                            result.append(tag + " ab " + zeit + " Uhr")
                        else:
                            result.append(tag)
                else:
                    text = lv_clean(item)
                    if text:
                        result.append(text)

        if result:
            return ", ".join(dict.fromkeys(result))

        row = lv_find_sonstiges_row(
            besichtigung_data,
            ["einsatztage", "uhrzeiten"]
        )

        if isinstance(row, dict):
            values = row.get("values") or []
            active = row.get("active") or []

            combined = []
            for item in values + active:
                text = lv_clean(item)
                if text and text.lower() not in ["bitte wählen", "bitte waehlen"]:
                    combined.append(text)

            if combined:
                return ", ".join(dict.fromkeys(combined))

        return ""

    def lv_build_zusatzleistungen(besichtigung_data):
        items = []

        stehlampen = lv_find_sonstiges_raw_value(besichtigung_data, "stehlampen")
        if lv_is_active_choice(stehlampen):
            stueck = lv_find_sonstiges_next_value(
                besichtigung_data,
                "stehlampen",
                ["stückzahl", "stueckzahl"]
            )
            freq = lv_find_sonstiges_next_freq(besichtigung_data, "stehlampen", "-")

            angabe = []
            if stueck:
                angabe.append(stueck + " Stück")
            if freq and freq != "-":
                angabe.append(freq)

            lv_add_zusatz(
                items,
                "Stehlampen reinigen / entstauben",
                " · ".join(angabe) if angabe else "-"
            )

        regale = lv_find_sonstiges_raw_value(besichtigung_data, "regale")
        if lv_is_active_choice(regale):
            stueck = lv_find_sonstiges_next_value(
                besichtigung_data,
                "regale",
                ["stückzahl", "stueckzahl"]
            )
            freq = lv_find_sonstiges_next_freq(besichtigung_data, "regale", "-")

            angabe = []
            if stueck:
                angabe.append(stueck + " Stück")
            if freq and freq != "-":
                angabe.append(freq)

            lv_add_zusatz(
                items,
                "Regale reinigen / entstauben",
                " · ".join(angabe) if angabe else "-"
            )

        etage = lv_find_sonstiges_raw_value(besichtigung_data, "etage")
        bis_inkl = lv_find_sonstiges_next_value(
            besichtigung_data,
            "etage",
            ["bis inkl", "bis"]
        )

        if lv_is_active_choice(etage):
            if bis_inkl:
                etage_text = etage + " bis inkl. " + bis_inkl
            else:
                etage_text = etage

            lv_add_zusatz(items, "Etage / Reinigungsbereich", etage_text)

        starttermin = lv_find_sonstiges_raw_value(besichtigung_data, "starttermin")
        if lv_is_active_choice(starttermin):
            lv_add_zusatz(items, "Starttermin", starttermin)

        einsatz_text = lv_build_einsatz_text(besichtigung_data)
        if einsatz_text:
            lv_add_zusatz(items, "Einsatztage & Uhrzeiten", einsatz_text)

        notiz = (
            lv_find_sonstiges_raw_value(besichtigung_data, "notizen")
            or lv_find_sonstiges_raw_value(besichtigung_data, "sonderwünsche")
            or lv_find_sonstiges_raw_value(besichtigung_data, "sonderwuensche")
        )

        if lv_is_active_choice(notiz):
            notiz_lower = notiz.lower()
            if "hier können sie" not in notiz_lower and "hier koennen sie" not in notiz_lower:
                lv_add_zusatz(items, "Individuelle Notizen / Sonderwünsche", notiz)

        return items

    def lv_build_vars_from_besichtigung(row):
        besichtigung_data = lv_parse_json(row["besichtigung_data_json"] if row else "{}")

        kunde = besichtigung_data.get("kunde") or {}
        leistungen = [
            lv_clean(x)
            for x in (besichtigung_data.get("leistungen") or [])
            if lv_clean(x).lower() not in ["leistungen", "elemente", "sonstiges"]
        ]

        firma = lv_clean(kunde.get("firma")) or lv_clean(row["firma"] if row else "")
        adresse = lv_clean(kunde.get("strasse")) or lv_clean(row["strasse"] if row else "")
        plz = lv_clean(kunde.get("plz")) or lv_clean(row["plz"] if row else "")
        ort = lv_clean(kunde.get("ort")) or lv_clean(row["ort"] if row else "")

        leistungsart = lv_join_de(leistungen)
        if not leistungsart:
            leistungsart = lv_clean(row["branche"] if row else "")

        freq_buero = lv_area_freq(besichtigung_data, ["büro", "buero", "büroraum", "besprechungsraum"])
        freq_wc = lv_area_freq(besichtigung_data, ["wc", "sanitär", "sanitaer"])
        freq_kueche = lv_area_freq(besichtigung_data, ["küche", "kueche"])
        freq_flur = lv_area_freq(besichtigung_data, ["flur"])

        top_freq_parts = []
        if freq_buero != "-":
            top_freq_parts.append("Büro: " + freq_buero)
        if freq_wc != "-":
            top_freq_parts.append("WC: " + freq_wc)
        if freq_kueche != "-":
            top_freq_parts.append("Küche: " + freq_kueche)
        if freq_flur != "-":
            top_freq_parts.append("Flur: " + freq_flur)

        freq_schreibtische = lv_find_sonstiges_next_freq(besichtigung_data, "schreibtische", "-")
        freq_elektro = lv_find_sonstiges_next_freq(besichtigung_data, "elektro", "-")
        freq_spuelmaschine = lv_find_sonstiges_next_freq(besichtigung_data, "geschirr", "-")

        text_elektro = lv_build_elektro_text(besichtigung_data)
        text_spuelmaschine = lv_build_spuelmaschine_text(besichtigung_data)
        zusatzleistungen = lv_build_zusatzleistungen(besichtigung_data)

        angebot_nr = lv_clean(row["angebot_nr"] if row and "angebot_nr" in row.keys() else "")
        angebot_nr = re.sub(r"(?i)^AN-", "", angebot_nr).strip()

        return {
            "Nr": angebot_nr,
            "Kunde": firma,
            "Objekt": "",
            "Adresse": adresse,
            "Plz": plz,
            "Ort": ort,
            "Leistungsart": leistungsart,
            "Haeufigkeit": "<br>".join(top_freq_parts) if top_freq_parts else "-",
            "Zusatzleistungen": zusatzleistungen,

            "Freq_Schreibtische": freq_schreibtische,
            "Freq_Elektro": freq_elektro,
            "Text_Elektro": text_elektro,
            "Freq_Muell": freq_buero,
            "Freq_Tueren": freq_buero,
            "Freq_Boden_Teppich": freq_buero,

            "Freq_Kueche_Flaechen": freq_kueche,
            "Freq_Spuelmaschine": freq_spuelmaschine,
            "Text_Spuelmaschine": text_spuelmaschine,
            "Freq_Kuechengeraete": freq_kueche,
            "Freq_Kueche_Muell": freq_kueche,
            "Freq_Boden_Kueche": freq_kueche,

            "Freq_WC": freq_wc,
            "Freq_Waschbecken": freq_wc,
            "Freq_Auffuellen": freq_wc,
            "Freq_Sanitaer_Muell": freq_wc,
            "Freq_Boden_Sanitaer": freq_wc,

            "Freq_Flur_Kontakt": freq_flur,
            "Freq_Flur_Sicht": freq_flur,
            "Freq_Flur_Boden": freq_flur,
        }

    @app.route("/leistungsverzeichnis")
    @app.route("/Leistungsverzeichnis.html")
    @app.route("/leistungsverzeichnis.html")
    @login_required
    def app2_leistungsverzeichnis():
        angebot_id = request.args.get("angebot_id", "").strip()

        if angebot_id:
            try:
                angebot_id_int = int(angebot_id)
            except Exception:
                angebot_id_int = 0

            if angebot_id_int > 0:
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                row = conn.execute("""
                    SELECT
                        id,
                        firma,
                        branche,
                        strasse,
                        plz,
                        ort,
                        angebot_nr,
                        besichtigung_data_json
                    FROM tagesliste_leads
                    WHERE id = ?
                """, (angebot_id_int,)).fetchone()
                conn.close()

                if row:
                    lv_vars = lv_build_vars_from_besichtigung(row)
                    return render_template("Leistungsverzeichnis.html", **lv_vars)

        return render_template(
            "Leistungsverzeichnis.html",
            Nr=request.args.get("Nr", ""),
            Kunde=request.args.get("Kunde", ""),
            Objekt=request.args.get("Objekt", ""),
            Adresse=request.args.get("Adresse", ""),
            Plz=request.args.get("Plz", ""),
            Ort=request.args.get("Ort", ""),
            Leistungsart=request.args.get("Leistungsart", ""),
            Haeufigkeit=request.args.get("Haeufigkeit", "-"),
            Zusatzleistungen=[],

            Freq_Schreibtische="-",
            Freq_Elektro="-",
            Freq_Muell="-",
            Freq_Tueren="-",
            Freq_Boden_Teppich="-",

            Freq_Kueche_Flaechen="-",
            Freq_Spuelmaschine="-",
            Freq_Kuechengeraete="-",
            Freq_Kueche_Muell="-",
            Freq_Boden_Kueche="-",

            Freq_WC="-",
            Freq_Waschbecken="-",
            Freq_Auffuellen="-",
            Freq_Sanitaer_Muell="-",
            Freq_Boden_Sanitaer="-",

            Freq_Flur_Kontakt="-",
            Freq_Flur_Sicht="-",
            Freq_Flur_Boden="-"
        )
