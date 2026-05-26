import os
import re
import time
import json
import sqlite3
import requests
from datetime import date
from urllib.parse import urljoin, unquote, urlparse

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


# ============================================================
# TOKEN / ENV
# ============================================================

load_dotenv("tokenlar.env")

GMAPS_KEY = os.getenv("GMAPS_KEY", "").strip()
GOOGLE_TEXT_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

GOOGLE_DAILY_LIMIT = int(os.getenv("GOOGLE_DAILY_LIMIT", "900"))
GOOGLE_PAGE_SIZE = 20
GOOGLE_USAGE_FILE = "google_usage.json"

EMAIL_REGEX = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"

KEYWORDS = [
    "impressum", "kontakt", "contact", "imprint", "about",
    "ueber", "über", "kanzlei", "praxis", "team", "standorte",
    "anfahrt", "legal", "datenschutz", "unternehmen", "firma",
    "service", "support", "info", "sekretariat"
]

BLACKLIST_CONTAINS = [
    "example.", "domain.com", "example.com", "example.de",
    "empfaenger.de", "empfänger.de", "absender.de",
    "sentry.io", "sentry.wixpress.com", "wixpress.com",
    "wix.com", "wixsite.com", "wixstatic.com",
    "webador.de", "ionos.com", "goneo.de", "strato.de",
    "jimdo.com", "jimdosite.com", "wordpress.com", "siteground",
    "hosteurope", "one.com", "cloudflare", "google-analytics",
    "googletagmanager", "doubleclick", "facebook.com", "fb.com",
    "instagram.com", "usercentrics.com", "cookiebot.com",
    "consentmanager.net", "@www.", "noreply", "no-reply",
    "donotreply", "do-not-reply",
]

BAD_DOMAIN_ENDINGS = [
    ".webp", ".jpg", ".jpeg", ".png", ".gif", ".svg",
    ".css", ".js", ".json", ".mp4", ".webm", ".avi",
    ".woff", ".woff2", ".ttf", ".ico", ".pdf"
]

GOOD_PREFIXES = [
    "info@", "kontakt@", "contact@", "office@", "mail@", "service@",
    "kanzlei@", "buero@", "büro@", "praxis@", "hello@", "team@",
    "empfang@", "anfrage@", "verwaltung@", "rezeption@",
    "sekretariat@", "dispo@", "vertrieb@", "sales@"
]

BAD_PREFIXES = [
    "datenschutz@", "privacy@", "bewerbung@", "career@", "jobs@", "hr@",
    "karriere@", "recruiting@", "presse@", "pr@", "ir@", "newsletter@",
    "privacypolicy@", "abuse@", "security@", "postmaster@", "hostmaster@",
    "webmaster@"
]

BAD_WEBSITE_CONTAINS = [
    "google.", "facebook.", "instagram.", "twitter.", "x.com",
    "dasoertliche.de", "gelbeseiten.de", "telefonbuch.de",
    "11880.com", "cylex.de", "meinestadt.de", "golocal.de",
    "yelp.de", "doctolib.de", "jameda.de", "sanego.de",
    "bing.com", "linkedin.com", "xing.com"
]

BAD_LEAD_NAME_CONTAINS = [
    "myflexbox", "paketstation", "packstation", "paketshop",
    "parcel locker", "amazon locker", "locker", "dhl paket",
    "dhl packstation", "ups access point", "gls paketshop",
    "dpd pickup", "hermes paketshop", "postfiliale",
    "briefkasten", "geldautomat", "atm", "tankstelle",
    "parkplatz", "haltestelle"
]

GOOGLE_FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.websiteUri",
    "places.googleMapsUri",
    "places.nationalPhoneNumber",
    "places.businessStatus",
    "places.rating",
    "places.userRatingCount",
    "nextPageToken",
])


# ============================================================
# GENEL YARDIMCI FONKSIYONLAR
# ============================================================

def normalize_text(value):
    if value is None:
        return ""
    return " ".join(str(value).replace("\xa0", " ").split()).strip()


def normalize_url(url):
    u = normalize_text(url)

    if not u or u == "-":
        return ""

    if u.startswith("//"):
        u = "https:" + u

    if u.startswith("mailto:") or u.startswith("tel:"):
        return ""

    if "google.com/maps" in u.lower() or "maps.google" in u.lower() or "maps/search" in u.lower():
        return ""

    if not u.startswith(("http://", "https://")):
        u = "https://" + u

    try:
        parsed = urlparse(u)
        if not parsed.netloc:
            return ""

        domain = parsed.netloc.lower()
        if any(bad in domain or bad in u.lower() for bad in BAD_WEBSITE_CONTAINS):
            return ""

        return u
    except Exception:
        return ""


def clean_email(mail):
    m = unquote(str(mail or "")).strip().lower()
    m = m.replace(">", "").replace("<", "").replace('"', "").replace("'", "")
    m = m.replace("mailto:", "").strip()
    m = m.split("?", 1)[0]
    m = m.replace("(at)", "@").replace("[at]", "@").replace("{at}", "@")
    m = m.replace(" at ", "@").replace(" [at] ", "@").replace(" (at) ", "@")
    m = m.replace(" punkt ", ".").replace(" dot ", ".")
    m = m.replace("[dot]", ".").replace("(dot)", ".").replace("{dot}", ".")
    m = m.replace(";", "").replace(",", "")
    return m.strip()


def get_domain_from_url(url):
    try:
        parsed = urlparse(normalize_url(url))
        domain = (parsed.netloc or "").lower().strip()
        domain = domain.replace("www.", "")
        return domain
    except Exception:
        return ""


def get_root_domain(domain):
    domain = normalize_text(domain).lower().replace("www.", "")
    if not domain:
        return ""
    parts = domain.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return domain


def get_url_root_for_key(url):
    try:
        u = normalize_url(url)
        if not u:
            return ""
        return get_domain_from_url(u)
    except Exception:
        return ""


def normalize_phone(value):
    phone = normalize_text(value)
    phone = phone.replace("+49", "0")
    phone = re.sub(r"[^0-9]", "", phone)

    if phone.startswith("0049"):
        phone = "0" + phone[4:]

    if phone.startswith("49") and len(phone) > 8:
        phone = "0" + phone[2:]

    return phone


def is_same_domain(mail, website):
    try:
        mail_domain = clean_email(mail).split("@", 1)[1].replace("www.", "")
        site_domain = get_domain_from_url(website)

        if not mail_domain or not site_domain:
            return False

        if mail_domain == site_domain:
            return True

        if mail_domain.endswith("." + site_domain):
            return True

        if get_root_domain(mail_domain) == get_root_domain(site_domain):
            return True

        return False
    except Exception:
        return False


def is_valid_email(mail, base_url=""):
    m = clean_email(mail)

    if not m:
        return False

    if any(x in m for x in BLACKLIST_CONTAINS):
        return False

    if m.count("@") != 1:
        return False

    if m.startswith("@") or m.endswith("@"):
        return False

    if " " in m:
        return False

    local, domain = m.split("@", 1)
    domain = domain.lower().strip()

    if len(local) < 1 or len(domain) < 4:
        return False

    if "." not in domain:
        return False

    if domain.startswith(".") or domain.endswith("."):
        return False

    if ".." in domain or ".." in local:
        return False

    if any(domain.endswith(x) for x in BAD_DOMAIN_ENDINGS):
        return False

    if local.endswith((".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif", ".pdf")):
        return False

    if not re.search(r"[a-z]", domain):
        return False

    if any(local.startswith(x.replace("@", "")) for x in BAD_PREFIXES):
        return False

    mail_root = get_root_domain(domain)

    if mail_root in {
        "ionos.com", "webador.de", "goneo.de", "jimdo.com", "wordpress.com",
        "wix.com", "wixpress.com", "wixsite.com", "cookiebot.com",
        "consentmanager.net", "usercentrics.com"
    }:
        return False

    site_domain = get_domain_from_url(base_url)
    site_root = get_root_domain(site_domain)

    if site_root and mail_root and mail_root != site_root:
        good_locals = [
            "info", "kontakt", "contact", "office", "mail",
            "service", "hello", "team", "anfrage", "verwaltung",
            "praxis", "kanzlei", "empfang", "rezeption", "sekretariat",
            "dispo", "vertrieb", "sales"
        ]

        if local not in good_locals:
            return False

    return True


def score_email(mail, base_url=""):
    m = clean_email(mail)

    try:
        local, domain = m.split("@", 1)
    except Exception:
        return -9999

    score = 0

    site_domain = get_domain_from_url(base_url)
    site_root = get_root_domain(site_domain)
    mail_root = get_root_domain(domain)

    if any(m.startswith(x) for x in GOOD_PREFIXES):
        score += 120

    if any(m.startswith(x) for x in BAD_PREFIXES):
        score -= 250

    if local in [
        "info", "kontakt", "contact", "office", "mail", "service",
        "hello", "team", "anfrage", "verwaltung", "praxis",
        "kanzlei", "empfang", "rezeption", "sekretariat",
        "dispo", "vertrieb", "sales"
    ]:
        score += 80

    if "info@" in m or "kontakt@" in m or "praxis@" in m or "empfang@" in m:
        score += 50

    if site_root:
        if mail_root == site_root:
            score += 300
        else:
            score -= 180

    return score


def extract_emails_from_text(text, base_url=""):
    text = str(text or "")
    text = text.replace("(at)", "@").replace("[at]", "@").replace("{at}", "@")
    text = text.replace(" at ", "@").replace(" punkt ", ".").replace(" dot ", ".")
    text = text.replace("[dot]", ".").replace("(dot)", ".").replace("{dot}", ".")

    found = set(re.findall(EMAIL_REGEX, text))
    cleaned = []

    for mail in found:
        m = clean_email(mail)
        if is_valid_email(m, base_url):
            cleaned.append(m)

    return sorted(set(cleaned))


def extract_mailtos(page, base_url=""):
    found = set()

    try:
        elements = page.query_selector_all("a[href^='mailto:']")
    except Exception:
        return []

    for el in elements:
        try:
            href = el.get_attribute("href")
            if href:
                m = clean_email(href)
                if is_valid_email(m, base_url):
                    found.add(m)
        except Exception:
            continue

    return sorted(found)


def get_best_email(page, base_url=""):
    emails = set()

    try:
        emails.update(extract_mailtos(page, base_url))
    except Exception:
        pass

    try:
        emails.update(extract_emails_from_text(page.content(), base_url))
    except Exception:
        pass

    try:
        body_text = page.locator("body").inner_text(timeout=2500)
        emails.update(extract_emails_from_text(body_text, base_url))
    except Exception:
        pass

    if not emails:
        return None

    same_domain = [m for m in emails if is_same_domain(m, base_url)]
    if same_domain:
        return sorted(same_domain, key=lambda x: score_email(x, base_url), reverse=True)[0]

    fallback_prefixes = (
        "info@", "kontakt@", "contact@", "office@", "mail@",
        "service@", "anfrage@", "verwaltung@", "kanzlei@",
        "praxis@", "empfang@", "rezeption@", "sekretariat@",
        "dispo@", "vertrieb@", "sales@"
    )

    fallback = [m for m in emails if clean_email(m).startswith(fallback_prefixes)]
    if fallback:
        return sorted(fallback, key=lambda x: score_email(x, base_url), reverse=True)[0]

    return sorted(emails, key=lambda x: score_email(x, base_url), reverse=True)[0]


def accept_cookies(page):
    buttons = [
        "button:has-text('Okay')",
        "button:has-text('OK')",
        "button:has-text('Akzeptieren')",
        "button:has-text('Zustimmen')",
        "button:has-text('Einverstanden')",
        "button:has-text('Accept')",
        "button:has-text('Alle akzeptieren')",
        "button:has-text('Alles akzeptieren')"
    ]

    for b in buttons:
        try:
            locator = page.locator(b)
            if locator.count() > 0:
                locator.first.click(timeout=700)
                page.wait_for_timeout(250)
                return True
        except Exception:
            continue

    return False


def safe_goto(page, url, timeout=5000):
    clean_url = normalize_url(url)

    if not clean_url:
        return False

    try:
        page.goto(clean_url, timeout=timeout, wait_until="domcontentloaded")
        page.wait_for_timeout(350)
        return True
    except PlaywrightTimeoutError:
        return False
    except Exception:
        try:
            page.goto(clean_url, timeout=timeout, wait_until="commit")
            page.wait_for_timeout(250)
            return True
        except Exception:
            return False


def find_candidate_links(page, base_url):
    candidates = []

    try:
        link_items = page.evaluate("""
            () => Array.from(document.querySelectorAll('a')).map(a => ({
                href: a.getAttribute('href') || '',
                text: (a.innerText || a.textContent || '').trim()
            }))
        """)
    except Exception:
        link_items = []

    for item in link_items:
        try:
            href = normalize_text(item.get("href"))
            text = normalize_text(item.get("text")).lower()

            if not href:
                continue

            href_l = href.lower()

            if any(k in text or k in href_l for k in KEYWORDS):
                full_url = urljoin(base_url, href)
                full_url = normalize_url(full_url)

                if full_url:
                    candidates.append(full_url)
        except Exception:
            continue

    extra_paths = [
        "/impressum",
        "/impressum/",
        "/kontakt",
        "/kontakt/",
        "/contact",
        "/contact/",
        "/imprint",
        "/imprint/",
        "/ueber-uns",
        "/ueber-uns/",
        "/über-uns",
        "/team",
        "/standorte",
        "/service",
    ]

    for path in extra_paths:
        full_url = normalize_url(urljoin(base_url, path))
        if full_url:
            candidates.append(full_url)

    seen = set()
    result = []

    base_root = get_root_domain(get_domain_from_url(base_url))

    for x in candidates:
        if x in seen:
            continue

        if base_root:
            x_root = get_root_domain(get_domain_from_url(x))
            if x_root and x_root != base_root:
                continue

        seen.add(x)
        result.append(x)

    return result[:10]


def scrape_fast(page, base_url):
    base_url = normalize_url(base_url)

    if not base_url:
        return None

    row_start = time.time()
    max_per_site_seconds = 8

    def time_left_ms():
        left = max_per_site_seconds - (time.time() - row_start)
        if left <= 0:
            return 0
        return int(left * 1000)

    first_timeout = min(4200, max(1200, time_left_ms()))
    ok = safe_goto(page, base_url, timeout=first_timeout)

    if ok:
        try:
            accept_cookies(page)
        except Exception:
            pass

        try:
            best = get_best_email(page, base_url)
            if best:
                return best
        except Exception:
            pass

    if time_left_ms() <= 0:
        return None

    links = find_candidate_links(page, base_url)

    for link in links:
        if time_left_ms() <= 0:
            return None

        per_link_timeout = min(2200, max(900, time_left_ms()))
        ok = safe_goto(page, link, timeout=per_link_timeout)

        if not ok:
            continue

        try:
            accept_cookies(page)
        except Exception:
            pass

        try:
            best = get_best_email(page, base_url)
            if best:
                return best
        except Exception:
            continue

    return None


# ============================================================
# GOOGLE USAGE
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
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def google_usage_today():
    data = load_json_file(GOOGLE_USAGE_FILE, {})
    today = date.today().isoformat()
    return int(data.get(today, 0))


def add_google_usage(count=1):
    data = load_json_file(GOOGLE_USAGE_FILE, {})
    today = date.today().isoformat()
    data[today] = int(data.get(today, 0)) + int(count)
    save_json_file(GOOGLE_USAGE_FILE, data)


def google_remaining_today():
    return max(0, GOOGLE_DAILY_LIMIT - google_usage_today())


# ============================================================
# GOOGLE PLACES
# ============================================================

def parse_google_address(address):
    address = normalize_text(address)
    street = ""
    plz = ""
    city = ""

    match = re.search(r"^(?P<street>.*?),\s*(?P<plz>\d{5})\s+(?P<city>[^,]+)", address)
    if match:
        return (
            normalize_text(match.group("street")),
            normalize_text(match.group("plz")),
            normalize_text(match.group("city")),
        )

    match = re.search(r"(?P<plz>\d{5})\s+(?P<city>[^,]+)", address)
    if match:
        plz = normalize_text(match.group("plz"))
        city = normalize_text(match.group("city"))

    return street, plz, city


def company_name_looks_bad(name):
    n = normalize_text(name).lower()
    return any(x in n for x in BAD_LEAD_NAME_CONTAINS)


def call_google_text_search(text_query):
    if not GMAPS_KEY:
        return {}, False, "GMAPS_KEY fehlt"

    if google_remaining_today() <= 0:
        return {}, False, f"Google Tageslimit {GOOGLE_DAILY_LIMIT} erreicht"

    payload = {
        "textQuery": text_query,
        "languageCode": "de",
        "regionCode": "DE",
        "pageSize": GOOGLE_PAGE_SIZE,
    }

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GMAPS_KEY,
        "X-Goog-FieldMask": GOOGLE_FIELD_MASK,
    }

    try:
        response = requests.post(
            GOOGLE_TEXT_SEARCH_URL,
            headers=headers,
            json=payload,
            timeout=45,
        )

        add_google_usage(1)

    except Exception as e:
        return {}, False, str(e)

    if response.status_code not in [200, 201]:
        try:
            return {}, False, response.text[:500]
        except Exception:
            return {}, False, f"Google HTTP {response.status_code}"

    try:
        return response.json(), True, ""
    except Exception:
        return {}, False, "Google JSON konnte nicht gelesen werden"


def google_place_to_lead(place, branche_id, branche_name, suchwort, fallback_stadt):
    display = place.get("displayName") or {}
    firma = normalize_text(display.get("text") if isinstance(display, dict) else display)

    if not firma:
        return None

    if company_name_looks_bad(firma):
        return None

    if normalize_text(place.get("businessStatus")) and normalize_text(place.get("businessStatus")) != "OPERATIONAL":
        return None

    formatted_address = normalize_text(place.get("formattedAddress"))
    strasse, plz, ort = parse_google_address(formatted_address)

    if not ort:
        ort = fallback_stadt

    website = normalize_url(place.get("websiteUri"))
    telefon = normalize_text(place.get("nationalPhoneNumber"))

    return {
        "firma": firma,
        "strasse": strasse,
        "plz": plz,
        "stadt": ort,
        "telefon": telefon,
        "website": website,
        "email": "",
        "branche_id": branche_id,
        "branche_name": branche_name,
        "suchwort": suchwort,
        "quelle": "Google",
        "status": "Neu",
        "google_place_id": normalize_text(place.get("id")),
        "google_maps_url": normalize_text(place.get("googleMapsUri")),
        "rating": normalize_text(place.get("rating")),
        "user_rating_count": normalize_text(place.get("userRatingCount")),
    }


def get_item_unique_key(lead):
    website = normalize_url(lead.get("website", ""))
    domain = get_url_root_for_key(website)

    if domain:
        return f"domain:{domain}"

    telefon = normalize_phone(lead.get("telefon", ""))
    if telefon and len(telefon) >= 7:
        return f"phone:{telefon}"

    firma = normalize_text(lead.get("firma", "")).lower()
    plz = normalize_text(lead.get("plz", ""))
    strasse = normalize_text(lead.get("strasse", "")).lower()

    if firma and plz and strasse:
        return f"nameaddr:{firma}|{plz}|{strasse}"

    if firma and plz:
        return f"nameplz:{firma}|{plz}"

    return f"name:{firma}"


def get_leads_from_google(suchwort, stadt, max_results=20, plz=""):
    if plz:
        text_query = f"{suchwort} {plz} {stadt}".strip()
    else:
        text_query = f"{suchwort} {stadt}".strip()

    requested = 20

    rows = []
    seen = set()

    data, ok, error = call_google_text_search(text_query)

    if not ok:
        return rows

    places = data.get("places") or []

    for place in places:
        if len(rows) >= requested:
            break

        lead = google_place_to_lead(
            place=place,
            branche_id=None,
            branche_name="",
            suchwort=suchwort,
            fallback_stadt=stadt
        )

        if not lead:
            continue

        key = get_item_unique_key(lead)

        if not key or key in seen:
            continue

        seen.add(key)
        rows.append(lead)

    return rows


# ============================================================
# DB FUNKSIYONLARI
# ============================================================

def lead_exists(conn, firma, plz, telefon, website):
    website = normalize_url(website)
    telefon_norm = normalize_phone(telefon)

    if website:
        row = conn.execute("SELECT id FROM leads WHERE website = ?", (website,)).fetchone()
        if row:
            return True

    if telefon:
        row = conn.execute("SELECT id FROM leads WHERE telefon = ?", (telefon,)).fetchone()
        if row:
            return True

        if telefon_norm:
            rows = conn.execute("SELECT id, telefon FROM leads WHERE telefon IS NOT NULL AND telefon != ''").fetchall()
            for r in rows:
                try:
                    if normalize_phone(r["telefon"]) == telefon_norm:
                        return True
                except Exception:
                    pass

    if firma and plz:
        row = conn.execute(
            "SELECT id FROM leads WHERE lower(firma) = lower(?) AND plz = ?",
            (firma, plz)
        ).fetchone()
        if row:
            return True

    return False


def ensure_google_maps_column(conn):
    try:
        conn.execute("ALTER TABLE leads ADD COLUMN google_maps_url TEXT")
    except Exception:
        pass


def insert_lead(conn, lead):
    ensure_google_maps_column(conn)

    conn.execute("""
        INSERT INTO leads (
            firma, strasse, plz, stadt, telefon, website, email,
            branche_id, branche_name, suchwort, quelle, status, google_maps_url
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        lead["firma"],
        lead["strasse"],
        lead["plz"],
        lead["stadt"],
        lead["telefon"],
        lead["website"] if lead["website"] else None,
        lead["email"],
        lead["branche_id"],
        lead["branche_name"],
        lead["suchwort"],
        "Google",
        "Neu",
        lead.get("google_maps_url", "")
    ))

# ============================================================
# CRM IMPORT FUNKSIYONU
# app2.py su an run_apify_import import ettigi icin isim KORUNDU.
# Icerik artik Apify degil: Google + Mailbul.
# Manuel kullanimda HER ÇALIŞTIRMA = 1 Google isteği.
# ============================================================

def run_apify_import(db_path, branche_id, branche_name, suchwort, stadt, max_results=20, plz=""):
    result = {
        "success": True,
        "message": "",
        "requested": 20,
        "received": 0,
        "inserted": 0,
        "skipped_duplicates": 0,
        "website_found": 0,
        "email_found": 0,
        "email_missing": 0,
        "google_usage_today": google_usage_today(),
        "source": "Google",
        "plz": plz
    }

    if not GMAPS_KEY:
        result["success"] = False
        result["message"] = "GMAPS_KEY fehlt. tokenlar.env içine GMAPS_KEY ekle."
        return result

    leads = get_leads_from_google(suchwort, stadt, 20, plz=plz)

    for lead in leads:
        lead["branche_id"] = branche_id
        lead["branche_name"] = branche_name

    result["received"] = len(leads)
    result["google_usage_today"] = google_usage_today()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
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

            for lead in leads:
                firma = normalize_text(lead.get("firma", ""))
                plz_lead = normalize_text(lead.get("plz", ""))
                telefon = normalize_text(lead.get("telefon", ""))
                website = normalize_url(lead.get("website", ""))

                if lead_exists(conn, firma, plz_lead, telefon, website):
                    result["skipped_duplicates"] += 1
                    continue

                email = ""

                if website:
                    result["website_found"] += 1
                    page = None

                    try:
                        page = context.new_page()
                        page.set_default_timeout(5000)
                        page.set_default_navigation_timeout(5000)

                        found_mail = scrape_fast(page, website)

                        if found_mail:
                            email = found_mail
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

                lead["email"] = email
                lead["website"] = website

                insert_lead(conn, lead)

                result["inserted"] += 1
                conn.commit()

            try:
                browser.close()
            except Exception:
                pass

    except Exception as e:
        result["success"] = False
        result["message"] = str(e)

    finally:
        conn.commit()
        conn.close()

    result["google_usage_today"] = google_usage_today()

    if result["success"]:
        result["message"] = (
            f'{result["inserted"]} neue Google-Leads gespeichert. '
            f'Website: {result["website_found"]}, '
            f'E-Mail: {result["email_found"]}, '
            f'Duplicate: {result["skipped_duplicates"]}.'
        )

    return result
