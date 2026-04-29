import os
import re
import time
import sqlite3
import requests
from urllib.parse import urljoin, unquote, urlparse

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError


load_dotenv("tokenlar.env")

APIFY_TOKEN = os.getenv("APIFY_TOKEN", "").strip()
APIFY_URL = "https://api.apify.com/v2/acts/compass~crawler-google-places/run-sync-get-dataset-items"
HEADERS = {
    "Content-Type": "application/json"
}

EMAIL_REGEX = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"

KEYWORDS = [
    "impressum", "kontakt", "contact", "imprint", "about",
    "ueber", "über", "kanzlei", "praxis", "team", "standorte"
]

BLACKLIST_CONTAINS = [
    "example.", "domain.com", "sentry.io", "sentry.wixpress.com",
    "wixpress.com", "wix.com", "wixsite.com", "wixstatic.com",
    "webador.de", "ionos.com", "goneo.de", "strato.de",
    "jimdo.com", "jimdosite.com", "wordpress.com", "siteground",
    "hosteurope", "one.com", "cloudflare", "google-analytics",
    "googletagmanager", "doubleclick", "facebook.com", "fb.com",
    "usercentrics.com", "cookiebot.com", "consentmanager.net",
    "@www.", "noreply", "no-reply", "donotreply", "do-not-reply",
]

BAD_DOMAIN_ENDINGS = [
    ".webp", ".jpg", ".jpeg", ".png", ".gif", ".svg",
    ".css", ".js", ".json", ".mp4", ".webm", ".avi",
    ".woff", ".woff2", ".ttf", ".ico"
]

GOOD_PREFIXES = [
    "info@", "kontakt@", "contact@", "office@", "mail@", "service@",
    "kanzlei@", "buero@", "büro@", "praxis@", "hello@", "team@",
    "empfang@", "anfrage@", "verwaltung@"
]

BAD_PREFIXES = [
    "datenschutz@", "privacy@", "bewerbung@", "career@", "jobs@", "hr@",
    "karriere@", "recruiting@", "presse@", "pr@", "ir@", "newsletter@",
    "privacypolicy@", "abuse@", "security@", "postmaster@", "hostmaster@"
]


def normalize_text(value):
    if value is None:
        return ""
    return str(value).strip()


def normalize_url(url):
    u = str(url or "").strip()

    if not u or u == "-":
        return ""

    u = u.replace(" ", "")

    if "google.com/maps" in u.lower() or "maps.google" in u.lower() or "maps/search" in u.lower():
        return ""

    if not u.startswith(("http://", "https://")):
        u = "https://" + u

    try:
        parsed = urlparse(u)
        if not parsed.netloc:
            return ""

        domain = parsed.netloc.lower()
        if "google.com" in domain and "/maps" in parsed.path.lower():
            return ""

        return u
    except:
        return ""


def clean_email(mail):
    m = unquote(str(mail)).strip().lower()
    m = m.replace(">", "").replace("<", "").replace('"', "").replace("'", "")
    m = m.replace("mailto:", "").strip()
    m = m.replace("(at)", "@").replace("[at]", "@").replace(" at ", "@")
    m = m.replace(" [at] ", "@").replace(" (at) ", "@")
    m = m.replace(";", "").replace(",", "")
    return m


def get_domain_from_url(url):
    try:
        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower().strip()
        domain = domain.replace("www.", "")
        return domain
    except:
        return ""


def get_root_domain(domain):
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
        parsed = urlparse(u)
        domain = (parsed.netloc or "").lower().strip()
        domain = domain.replace("www.", "")
        return domain
    except:
        return ""


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

        return False
    except:
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
    if local.endswith((".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif")):
        return False
    if not re.search(r"[a-z]", domain):
        return False

    parts = domain.split(".")
    if len(parts[-1]) < 2:
        return False

    if any(local.startswith(x.replace("@", "")) for x in BAD_PREFIXES):
        return False

    site_domain = get_domain_from_url(base_url)
    site_root = get_root_domain(site_domain)
    mail_root = get_root_domain(domain)

    if domain.endswith("wixpress.com") or domain.endswith("wix.com") or domain.endswith("wixsite.com"):
        return False

    if mail_root in {
        "ionos.com", "webador.de", "goneo.de", "jimdo.com", "wordpress.com",
        "wix.com", "wixpress.com", "wixsite.com", "cookiebot.com",
        "consentmanager.net", "usercentrics.com"
    }:
        return False

    if site_root and mail_root and mail_root != site_root:
        good_locals = [
            "info", "kontakt", "contact", "office", "mail",
            "service", "hello", "team", "anfrage", "verwaltung"
        ]
        if local not in good_locals:
            return False

    return True


def score_email(mail, base_url=""):
    m = clean_email(mail)

    try:
        local, domain = m.split("@", 1)
    except:
        return -999

    score = 0
    site_domain = get_domain_from_url(base_url)
    site_root = get_root_domain(site_domain)
    mail_root = get_root_domain(domain)

    if any(m.startswith(x) for x in GOOD_PREFIXES):
        score += 120

    if any(m.startswith(x) for x in BAD_PREFIXES):
        score -= 250

    if local in ["info", "kontakt", "contact", "office", "mail", "service", "hello", "team", "anfrage", "verwaltung"]:
        score += 80

    if "info@" in m or "kontakt@" in m or "praxis@" in m or "empfang@" in m:
        score += 50

    if site_root:
        if mail_root == site_root:
            score += 300
        else:
            score -= 250

    return score


def extract_emails_from_text(text, base_url=""):
    found = set(re.findall(EMAIL_REGEX, text or ""))
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
    except:
        return []

    for el in elements:
        try:
            href = el.get_attribute("href")
            if href:
                m = clean_email(href)
                if is_valid_email(m, base_url):
                    found.add(m)
        except:
            continue

    return sorted(found)


def get_best_email(page, base_url=""):
    emails = set()

    try:
        emails.update(extract_mailtos(page, base_url))
    except:
        pass

    try:
        emails.update(extract_emails_from_text(page.content(), base_url))
    except:
        pass

    try:
        body_text = page.locator("body").inner_text(timeout=2500)
        emails.update(extract_emails_from_text(body_text, base_url))
    except:
        pass

    if not emails:
        return None

    same_domain = [m for m in emails if is_same_domain(m, base_url)]

    if same_domain:
        ranked_same = sorted(same_domain, key=lambda x: score_email(x, base_url), reverse=True)
        return ranked_same[0]

    fallback_prefixes = ("info@", "kontakt@", "contact@", "office@", "mail@", "service@", "anfrage@", "verwaltung@")
    fallback = [m for m in emails if clean_email(m).startswith(fallback_prefixes)]

    if fallback:
        ranked_fallback = sorted(fallback, key=lambda x: score_email(x, base_url), reverse=True)
        return ranked_fallback[0]

    return None


def accept_cookies(page):
    buttons = [
        "button:has-text('Okay')",
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
        except:
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
    except:
        try:
            page.goto(clean_url, timeout=timeout, wait_until="commit")
            page.wait_for_timeout(250)
            return True
        except:
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
    except:
        link_items = []

    for item in link_items:
        try:
            href = (item.get("href") or "").strip()
            text = (item.get("text") or "").strip().lower()

            if not href:
                continue

            href_l = href.lower()

            if any(k in text or k in href_l for k in KEYWORDS):
                full_url = urljoin(base_url, href)
                full_url = normalize_url(full_url)

                if full_url:
                    candidates.append(full_url)
        except:
            continue

    extra_paths = [
        "/impressum",
        "/kontakt",
        "/kontakt/",
        "/contact",
        "/imprint",
        "/ueber-uns",
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

    for x in candidates:
        if x not in seen:
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
        except:
            pass

        try:
            best = get_best_email(page, base_url)
            if best:
                return best
        except:
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
        except:
            pass

        try:
            best = get_best_email(page, base_url)
            if best:
                return best
        except:
            continue

    return None


def parse_address(address):
    if not address or address == "-":
        return "", "", ""

    parts = [p.strip() for p in str(address).split(",")]

    street = parts[0] if len(parts) > 0 else ""
    plz = ""
    city = ""

    for part in parts[1:]:
        m = re.search(r"(\d{5})\s+(.+)", part.strip())
        if m:
            plz = m.group(1).strip()
            city = m.group(2).strip()
            break

    if not city and len(parts) > 1:
        city = parts[1].strip()

    return street, plz, city


def get_item_value(item, *keys):
    for key in keys:
        value = normalize_text(item.get(key))
        if value and value != "-":
            return value
    return ""


def get_item_unique_key(item):
    name = normalize_text(item.get("title") or item.get("name")).lower()
    address = normalize_text(item.get("address")).lower()
    website = normalize_url(item.get("website"))

    domain = get_url_root_for_key(website)

    if domain:
        return f"domain:{domain}"

    return f"nameaddr:{name}|{address}"


def get_leads_from_apify(suchwort, stadt, max_results):
    location_query = f"{stadt}, Germany"

    payload = {
        "language": "de",
        "locationQuery": location_query,
        "maxCrawledPlacesPerSearch": max_results,
        "maxCrawledPlaces": max_results,
        "maxImages": 0,
        "maxReviews": 0,
        "maxQuestions": 0,
        "searchStringsArray": [
            suchwort
        ]
    }

    response = requests.post(
        APIFY_URL,
        params={"token": APIFY_TOKEN},
        headers=HEADERS,
        json=payload,
        timeout=300
    )

    if response.status_code not in [200, 201]:
        return []

    try:
        data = response.json()
    except:
        return []

    if not isinstance(data, list):
        return []

    clean = []
    seen = set()

    for item in data:
        if len(clean) >= max_results:
            break

        name = normalize_text(item.get("title") or item.get("name"))
        if not name:
            continue

        key = get_item_unique_key(item)

        if not key or key in seen:
            continue

        seen.add(key)
        clean.append(item)

    return clean


def lead_exists(conn, firma, plz, telefon, website):
    if website:
        row = conn.execute("SELECT id FROM leads WHERE website = ?", (website,)).fetchone()
        if row:
            return True

    if telefon:
        row = conn.execute("SELECT id FROM leads WHERE telefon = ?", (telefon,)).fetchone()
        if row:
            return True

    if firma and plz:
        row = conn.execute(
            "SELECT id FROM leads WHERE lower(firma) = lower(?) AND plz = ?",
            (firma, plz)
        ).fetchone()
        if row:
            return True

    return False


def insert_lead(conn, lead):
    conn.execute("""
        INSERT INTO leads (
            firma, strasse, plz, stadt, telefon, website, email,
            branche_id, branche_name, suchwort, quelle, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        "Apify",
        "Neu"
    ))


def run_apify_import(db_path, branche_id, branche_name, suchwort, stadt, max_results):
    result = {
        "success": True,
        "message": "",
        "requested": max_results,
        "received": 0,
        "inserted": 0,
        "skipped_duplicates": 0,
        "website_found": 0,
        "email_found": 0,
        "email_missing": 0
    }

    leads = get_leads_from_apify(suchwort, stadt, max_results)
    result["received"] = len(leads)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-http2", "--disable-blink-features=AutomationControlled"]
            )

            context = browser.new_context(ignore_https_errors=True)

            def handle_route(route):
                if route.request.resource_type in ["image", "media", "font", "stylesheet"]:
                    return route.abort()
                return route.continue_()

            context.route("**/*", handle_route)

            for item in leads:
                firma = get_item_value(item, "title", "name")
                address = get_item_value(item, "address")
                telefon = get_item_value(item, "phone", "phoneNumber", "telephone")
                website = normalize_url(get_item_value(item, "website"))

                strasse, plz, ort = parse_address(address)

                if not ort:
                    ort = stadt

                if lead_exists(conn, firma, plz, telefon, website):
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

                    except:
                        result["email_missing"] += 1

                    finally:
                        try:
                            if page:
                                page.close()
                        except:
                            pass
                else:
                    result["email_missing"] += 1

                insert_lead(conn, {
                    "firma": firma,
                    "strasse": strasse,
                    "plz": plz,
                    "stadt": ort,
                    "telefon": telefon,
                    "website": website,
                    "email": email,
                    "branche_id": branche_id,
                    "branche_name": branche_name,
                    "suchwort": suchwort
                })

                result["inserted"] += 1
                conn.commit()

            browser.close()

    except Exception as e:
        result["success"] = False
        result["message"] = str(e)

    finally:
        conn.commit()
        conn.close()

    if result["success"]:
        result["message"] = f'{result["inserted"]} neue Leads gespeichert.'

    return result