import os
import sqlite3
import hashlib
import traceback
import time
from datetime import datetime, date
from typing import Optional, Dict, List

from fints.client import FinTS3PinTanClient, NeedTANResponse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "kg_portal.db")

BLZ = "35050000"
BIC = "DUISDE33XXX"
FINTS_URL = "https://banking-rl4.s-fints-pt-rl.de/fints30"
PRODUCT_ID = "7118F3D8B73C2D41243E56683"

USER_ID = "271191"
CUSTOMER_ID = None

ACCOUNT_MAP = {
    "200325934": "geschäftskonto-kg-gebäudereinigung",
    "1301528228": "damla-privat",
}

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn

def ensure_balance_table():
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bank_balance_cache (
            account_slug TEXT PRIMARY KEY,
            balance REAL DEFAULT 0,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def get_fints_pin() -> str:
    pin = os.getenv("FINTS_PIN", "").strip()
    if not pin:
        raise RuntimeError("FINTS_PIN environment variable bulunamadi.")
    return pin

def handle_need_tan(client, result, title="BANKA TAN ISTEDI"):
    """
    PushTAN (decoupled) onayini terminal girdisi olmadan otomatik kontrol eder.
    Kullanici telefondan onay verdiginde ayni FinTS oturumu devam eder.
    """
    if not isinstance(result, NeedTANResponse):
        return result

    challenge = (
        getattr(result, "challenge_html", None)
        or getattr(result, "challenge", None)
        or "PushTAN uygulamasindan islemi onaylayin."
    )

    if not getattr(result, "decoupled", False):
        raise RuntimeError(
            f"{title} | Banka manuel TAN kodu istiyor; "
            f"bu akista sadece PushTAN destekleniyor. Challenge: {challenge}"
        )

    print("=" * 72)
    print(title)
    print(challenge)
    print("PushTAN onayi bekleniyor...")
    print("=" * 72)

    # Bankaya cok sik durum sorgusu gondermemek icin once 5 saniye bekle.
    time.sleep(5)

    # Yaklasik 2 dakika boyunca telefondaki onayi otomatik kontrol et.
    max_poll = 24
    for poll_no in range(1, max_poll + 1):
        result = client.send_tan(result, "")

        if not isinstance(result, NeedTANResponse):
            print("PushTAN onaylandi; FinTS islemi devam ediyor.")
            return result

        if not getattr(result, "decoupled", False):
            raise RuntimeError(
                "PushTAN kontrolu sirasinda banka manuel TAN koduna gecti."
            )

        if poll_no < max_poll:
            print(f"PushTAN henuz onaylanmadi ({poll_no}/{max_poll}).")
            time.sleep(5)

    raise RuntimeError(
        "PushTAN onayi 2 dakika icinde gelmedi. "
        "Sparkasse uygulamasinda onay verip tekrar deneyin."
    )

def account_to_slug(account) -> Optional[str]:
    konto = str(getattr(account, "accountnumber", "") or "").strip()
    iban = str(getattr(account, "iban", "") or "").strip()

    if konto in ACCOUNT_MAP:
        return ACCOUNT_MAP[konto]

    iban_digits = "".join(ch for ch in iban if ch.isdigit())
    for konto_nr, slug in ACCOUNT_MAP.items():
        if iban_digits.endswith(konto_nr):
            return slug

    return None

def tx_field(tx, *names, default=""):
    for name in names:
        if hasattr(tx, name):
            val = getattr(tx, name)
            if val is None:
                continue
            if isinstance(val, str) and val == "":
                continue
            return val

    data = getattr(tx, "data", None)
    if data and hasattr(data, "get"):
        for name in names:
            val = data.get(name)
            if val is None:
                continue
            if isinstance(val, str) and val == "":
                continue
            return val

    return default

def tx_date_to_iso(tx) -> str:
    raw = tx_field(tx, "booking_date", "date", "entry_date", default="")
    if not raw:
        return datetime.now().strftime("%Y-%m-%d")

    if isinstance(raw, date):
        return raw.strftime("%Y-%m-%d")

    s = str(raw).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass

    return datetime.now().strftime("%Y-%m-%d")

def normalize_amount(val) -> float:
    if val is None:
        return 0.0
    try:
        if hasattr(val, "amount"):
            raw = getattr(val, "amount")
            return float(str(raw).replace(",", "."))
        return float(str(val).replace(",", "."))
    except Exception:
        return 0.0

def build_transaction_id(account_slug: str, datum: str, amount: float, payee: str, purpose: str) -> str:
    raw = f"{account_slug}|{datum}|{amount:.2f}|{payee}|{purpose}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def sync_fints_to_db(start_date: Optional[date] = None, end_date: Optional[date] = None) -> Dict[str, str]:
    ensure_balance_table()

    if start_date is None:
        start_date = date(datetime.now().year, 1, 1)
    if end_date is None:
        end_date = date.today()

    pin = get_fints_pin()

    inserted = 0
    skipped = 0

    conn = get_db_connection()
    cursor = conn.cursor()
    client = None

    try:
        client = FinTS3PinTanClient(
            BLZ,
            user_id=USER_ID,
            pin=pin,
            server=FINTS_URL,
            product_id=PRODUCT_ID,
            customer_id=CUSTOMER_ID
        )

        with client:
            if isinstance(client.init_tan_response, NeedTANResponse):
                client.init_tan_response = handle_need_tan(
                    client,
                    client.init_tan_response,
                    title="DIALOG BASLANGICI ICIN TAN GEREKIYOR"
                )

            accounts = client.get_sepa_accounts()
            accounts = handle_need_tan(
                client,
                accounts,
                title="HESAPLARI ALMAK ICIN TAN GEREKIYOR"
            )

            if not isinstance(accounts, list):
                raise Exception(f"Hesap sonucu beklenmeyen tipte geldi: {type(accounts)}")

            for account in accounts:
                account_slug = account_to_slug(account)
                if not account_slug:
                    continue

                result = client.get_transactions(
                    account=account,
                    start_date=start_date,
                    end_date=end_date,
                    include_pending=False
                )

                result = handle_need_tan(
                    client,
                    result,
                    title=f"{account_slug} ICIN TAN GEREKIYOR"
                )

                if not isinstance(result, list):
                    continue

                for tx in result:
                    datum = tx_date_to_iso(tx)
                    payee = str(tx_field(tx, "applicant_name", "recipient_name", default="")).strip() or "Unbekannt"
                    purpose = str(tx_field(tx, "purpose", "transaction_details", default="")).strip()
                    amount = normalize_amount(tx_field(tx, "amount", default=0))

                    transaction_id = build_transaction_id(account_slug, datum, amount, payee, purpose)

                    cursor.execute("""
                        INSERT INTO bank_cache (transaction_id, account_slug, payee, datum, description, amount)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(transaction_id) DO NOTHING
                    """, (
                        transaction_id,
                        account_slug,
                        payee,
                        datum,
                        purpose,
                        amount
                    ))

                    if cursor.rowcount == 1:
                        inserted += 1
                    else:
                        skipped += 1

        conn.commit()

        return {
            "ok": "1",
            "message": f"FinTS işlendi | yeni: {inserted} | atlanan: {skipped}"
        }

    except Exception as e:
        traceback.print_exc()
        return {
            "ok": "0",
            "message": str(e)
        }

    finally:
        try:
            if client:
                client.close()
        except Exception:
            pass
        conn.close()

def get_fints_transactions(account_slug: str, month: Optional[int] = None, year: Optional[int] = None) -> List[dict]:
    conn = get_db_connection()

    sql = """
        SELECT payee, datum, description, amount
        FROM bank_cache
        WHERE account_slug = ?
    """
    params = [account_slug]

    if month and year:
        sql += " AND datum LIKE ? "
        params.append(f"{year}-{int(month):02d}-%")

    sql += " ORDER BY datum DESC, rowid DESC "

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    result = []
    for r in rows:
        try:
            formatted = datetime.strptime(r["datum"], "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            formatted = r["datum"]

        result.append({
            "payee": r["payee"],
            "date": formatted,
            "description": r["description"],
            "amount": float(r["amount"] or 0)
        })

    return result

def get_fints_balance(account_slug: str) -> float:
    ensure_balance_table()
    conn = get_db_connection()
    row = conn.execute("""
        SELECT balance FROM bank_balance_cache WHERE account_slug = ?
    """, (account_slug,)).fetchone()
    conn.close()
    return float(row["balance"]) if row else 0.0

def get_fints_all_balances() -> Dict[str, float]:
    ensure_balance_table()
    conn = get_db_connection()
    rows = conn.execute("SELECT account_slug, balance FROM bank_balance_cache").fetchall()
    conn.close()

    balances = {
        "geschäftskonto-kg-gebäudereinigung": 0.0,
        "damla-privat": 0.0,
        "murat-privat": 0.0,
    }

    for row in rows:
        slug = row["account_slug"]
        if slug in balances:
            balances[slug] = float(row["balance"] or 0)

    return balances
