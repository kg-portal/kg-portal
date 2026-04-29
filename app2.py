# =====================================================
# APP2.PY
# DATENBANK / BESICHTIGUNG / ANGEBOTVORLAGE / LEISTUNGSVERZEICHNIS
# =====================================================

from flask import render_template, request, jsonify, Response
from urllib.parse import unquote
import sqlite3
import os
import re
from html import escape
from email.utils import parsedate_to_datetime
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

    conn.commit()
    conn.close()


def register_app2_routes(app, login_required):

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
            msg["From"] = f"Damla Kicci <{from_email}>"
            msg["To"] = to_email
            msg["Subject"] = subject

            if cc_email:
                msg["Cc"] = cc_email

            if bcc_email:
                msg["Bcc"] = bcc_email

            msg.set_content(message_text)
            msg.add_alternative(body_html, subtype="html")
    
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

        tagesliste_leads = conn.execute("""
            SELECT *
            FROM tagesliste_leads
            ORDER BY id ASC
        """).fetchall()

        tagesliste_leads = [dict(row) for row in tagesliste_leads]

        conn.close()

        return render_template(
            "datenbank.html",
            tagesliste_leads=tagesliste_leads
        )

# =====================================================
# APP2 - BÖLÜM 1.1 - APIFY LEAD IMPORT API
# =====================================================

    @app.route("/api/datenbank/apify-import", methods=["POST"])
    @login_required
    def app2_apify_import():
        data = request.get_json() or {}

        branche_id = str(data.get("branche_id", "")).strip()
        branche_name = str(data.get("branche_name", "")).strip()
        suchwort = str(data.get("suchwort", "")).strip()
        stadt = str(data.get("stadt", "")).strip()

        try:
            anzahl = int(data.get("anzahl", 30))
        except:
            anzahl = 30

        if not branche_id or not branche_name or not suchwort or not stadt:
            return jsonify({
                "success": False,
                "message": "Branche, Suchwort und Stadt sind Pflichtfelder."
            }), 400

        if anzahl < 1:
            anzahl = 30

        if anzahl > 100:
            anzahl = 100

        result = run_apify_import(
            db_path=DB_PATH,
            branche_id=branche_id,
            branche_name=branche_name,
            suchwort=suchwort,
            stadt=stadt,
            max_results=anzahl
        )

        return jsonify(result)


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

        if branche_id:
            leads = conn.execute("""
                SELECT *
                FROM leads
                WHERE branche_id = ?
                AND (status IS NULL OR status != 'Tagesliste')
                ORDER BY 
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
        cursor = conn.cursor()

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
                quelle,
                status,
                company_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            quelle,
            "offen",
            company_key
        ))

        inserted = cursor.rowcount

        if inserted == 1 and source_lead_id > 0:
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

        if tagesliste_id <= 0:
            return jsonify({
                "ok": False,
                "message": "Tagesliste-ID fehlt."
            }), 400

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

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
        spaeter_datum = str(data.get("spaeter_datum") or "").strip()

        erlaubte_status = [
            "offen",
            "angerufen",
            "interessiert",
            "besichtigung",
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

        if status == "spaeter":
            cursor.execute("""
                UPDATE tagesliste_leads
                SET
                    status = ?,
                    spaeter_datum = ?
                WHERE id = ?
            """, (status, spaeter_datum, tagesliste_id))
        else:
            cursor.execute("""
                UPDATE tagesliste_leads
                SET
                    status = ?,
                    spaeter_datum = NULL
                WHERE id = ?
            """, (status, tagesliste_id))

        conn.commit()
        conn.close()

        return jsonify({
            "ok": True,
            "message": "Status wurde gespeichert."
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
            "kontaktformular",
            "spaeter",
            "verloren"
        ]

        if status not in erlaubte_status:
            return jsonify([])

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

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
                erstellt_am
            FROM tagesliste_leads
            WHERE status = ?
            ORDER BY id DESC
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
            FROM tagesliste_leads
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

        counts = {
            "angerufen": 0,
            "interessiert": 0,
            "besichtigung": 0,
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
        return render_template(
            "angebotvorlage.html",
            Kunde=request.args.get("Kunde", ""),
            Objekt=request.args.get("Objekt", ""),
            Adresse=request.args.get("Adresse", ""),
            Plz=request.args.get("Plz", ""),
            Ort=request.args.get("Ort", ""),
            Leistungsart=request.args.get("Leistungsart", ""),
            Nr=request.args.get("Nr", ""),
            Datum=request.args.get("Datum", "")
        )


# =====================================================
# APP2 - BÖLÜM 4 - LEISTUNGSVERZEICHNIS
# =====================================================

    @app.route("/leistungsverzeichnis")
    @app.route("/Leistungsverzeichnis.html")
    @app.route("/leistungsverzeichnis.html")
    @login_required
    def app2_leistungsverzeichnis():
        return render_template(
            "Leistungsverzeichnis.html",
            Kunde=request.args.get("Kunde", ""),
            Objekt=request.args.get("Objekt", ""),
            Adresse=request.args.get("Adresse", ""),
            Plz=request.args.get("Plz", ""),
            Ort=request.args.get("Ort", ""),
            Leistungsart=request.args.get("Leistungsart", ""),
            Datum=request.args.get("Datum", "")
        )
