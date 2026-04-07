import os
import csv
import glob
import sqlite3
from datetime import datetime
from typing import Optional, Dict, List

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "kg_portal.db")

# CSV dosyaları bu klasöre düşüyor
CSV_GLOB = os.path.join(DATA_DIR, "*.csv")

# StarMoney'den gelen kontonummer -> portal slug eşleştirmesi
# Gerekirse sonradan buraya yeni hesap eklersin.
ACCOUNT_MAP = {
    "200325934": "geschäftskonto-kg-gebäudereinigung",
    "200448785": "geschäftskonto-amazon-energie",
    "1301528228": "damla-privat",
    # Murat csv'de gelirse buraya ekle
    # "XXXXXXXXX": "murat-privat",
}

# İsteğe bağlı: UI'da görünen kısa adlar
KONTO_DISPLAY_MAP = {
    "geschäftskonto-kg-gebäudereinigung": "KG",
    "geschäftskonto-amazon-energie": "Amazon",
    "damla-privat": "Damla",
    "murat-privat": "Murat",
}


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
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


def normalize_header(text: str) -> str:
    if text is None:
        return ""
    text = str(text).replace("\ufeff", "").strip().strip('"').strip("'")
    return text.lower()


def clean_value(val) -> str:
    if val is None:
        return ""
    return str(val).replace("\ufeff", "").strip().strip('"').strip("'").strip()


def find_latest_csv() -> Optional[str]:
    files = glob.glob(CSV_GLOB)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def detect_delimiter(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        sample = f.read(4096)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,")
        return dialect.delimiter
    except Exception:
        return ";"


def parse_german_number(val: str) -> float:
    s = clean_value(val)
    if not s:
        return 0.0

    s = s.replace("€", "").replace("EUR", "").replace(" ", "")

    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")

    try:
        return float(s)
    except Exception:
        return 0.0


def parse_date(val: str) -> str:
    s = clean_value(val)
    if not s:
        return "2026-01-01"

    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass

    return "2026-01-01"


def find_col(row: Dict[str, str], *possible_names: str) -> str:
    normalized = {normalize_header(k): v for k, v in row.items()}
    for name in possible_names:
        key = normalize_header(name)
        if key in normalized:
            return normalized[key]
    return ""


def detect_amount(row: Dict[str, str]) -> float:
    direct = find_col(
        row,
        "betrag",
        "umsatz",
        "wert",
        "betrag (eur)",
        "buchungsbetrag"
    )
    if clean_value(direct):
        return parse_german_number(direct)

    soll = parse_german_number(find_col(row, "soll", "sollbetrag"))
    haben = parse_german_number(find_col(row, "haben", "habenbetrag"))

    if haben != 0:
        return haben
    if soll != 0:
        return -abs(soll)

    return 0.0


def detect_account_slug(row: Dict[str, str]) -> Optional[str]:
    kontonummer = clean_value(find_col(
        row,
        "kontonummer",
        "konto",
        "konto nr",
        "konto-nr",
        "kontonr"
    ))

    iban = clean_value(find_col(row, "iban"))
    kontoname = clean_value(find_col(row, "kontoname", "konto-bezeichnung", "bezeichnung"))

    if kontonummer in ACCOUNT_MAP:
        return ACCOUNT_MAP[kontonummer]

    if iban:
        iban_digits = "".join(ch for ch in iban if ch.isdigit())
        for konto_nr, slug in ACCOUNT_MAP.items():
            if iban_digits.endswith(konto_nr):
                return slug

    name_upper = kontoname.upper()
    if "AMAZON" in name_upper or "ENERGIE" in name_upper:
        return "geschäftskonto-amazon-energie"
    if "KOMFORT" in name_upper or "DAMLA" in name_upper:
        return "damla-privat"
    if "GESCHÄFTSKONTO" in name_upper or "GESCHAEFTSKONTO" in name_upper:
        return "geschäftskonto-kg-gebäudereinigung"

    return None


def build_transaction_id(account_slug: str, datum: str, amount: float, payee: str, purpose: str, idx: int) -> str:
    raw = f"{account_slug}|{datum}|{amount:.2f}|{payee}|{purpose}|{idx}"
    return raw[:240]


def sync_starmoney_to_db() -> Dict[str, str]:
    ensure_balance_table()

    latest_csv = find_latest_csv()
    if not latest_csv:
        conn = get_db_connection()
        conn.execute("DELETE FROM bank_cache")
        conn.execute("DELETE FROM bank_balance_cache")
        conn.commit()
        conn.close()
        return {"ok": "0", "message": "CSV yok, DB temizlendi"}

    delimiter = detect_delimiter(latest_csv)

    conn = get_db_connection()
    cursor = conn.cursor()

    inserted = 0
    skipped = 0
    saldo_by_slug: Dict[str, tuple] = {}

    with open(latest_csv, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter=delimiter)

        for idx, row in enumerate(reader, start=1):
            account_slug = detect_account_slug(row)
            if not account_slug:
                skipped += 1
                continue

            datum = parse_date(find_col(row, "datum", "buchungstag", "wertstellung", "buchungsdatum"))
            payee = clean_value(find_col(
                row,
                "auftraggeber/empfänger",
                "begünstigter / auftraggeber",
                "begünstigter",
                "auftraggeber",
                "empfänger",
                "begünstigter/absender - name",
                "begünstigter / absender - name",
                "begünstigter/absender-name",
                "name"
            ))
            
            if not payee:
                payee = clean_value(find_col(row, "buchungstext"))

            if not payee:
                payee = "Unbekannt"

            purpose = clean_value(find_col(
                row,
                "verwendungszweck",
                "zweck",
                "buchungstext",
                "textschlüssel ergänzung",
                "beschreibung"
            ))

            amount = detect_amount(row)

            transaction_id = build_transaction_id(account_slug, datum, amount, payee, purpose, idx)

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

            saldo_raw = find_col(row, "saldo", "kontostand", "neuer saldo")
            if clean_value(saldo_raw):
                current_date = datum
                current_saldo = parse_german_number(saldo_raw)

                if account_slug not in saldo_by_slug:
                    saldo_by_slug[account_slug] = (current_date, current_saldo)
                else:
                    old_date, _ = saldo_by_slug[account_slug]
                    if current_date > old_date:
                        saldo_by_slug[account_slug] = (current_date, current_saldo)

    for slug, (date, balance) in saldo_by_slug.items():
        cursor.execute("""
            INSERT INTO bank_balance_cache (account_slug, balance, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(account_slug) DO UPDATE SET
                balance = excluded.balance,
                updated_at = CURRENT_TIMESTAMP
        """, (slug, balance))

    conn.commit()
    conn.close()

    return {
        "ok": "1",
        "message": f"CSV işlendi: {os.path.basename(latest_csv)} | yeni: {inserted} | atlanan: {skipped}"
    }


def get_starmoney_transactions(account_slug: str, month: Optional[int] = None, year: Optional[int] = None) -> List[dict]:
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


def get_starmoney_balance(account_slug: str) -> float:
    ensure_balance_table()
    conn = get_db_connection()
    row = conn.execute("""
        SELECT balance FROM bank_balance_cache WHERE account_slug = ?
    """, (account_slug,)).fetchone()
    conn.close()
    return float(row["balance"]) if row else 0.0


def get_starmoney_all_balances() -> Dict[str, float]:
    ensure_balance_table()
    balances = {
        "geschäftskonto-kg-gebäudereinigung": 0.0,
        "geschäftskonto-amazon-energie": 0.0,
        "damla-privat": 0.0,
        "murat-privat": 0.0,
    }

    conn = get_db_connection()
    rows = conn.execute("SELECT account_slug, balance FROM bank_balance_cache").fetchall()
    conn.close()

    for r in rows:
        if r["account_slug"] in balances:
            balances[r["account_slug"]] = float(r["balance"] or 0)

    return balances


def get_starmoney_monthly_totals(year: int = 2026) -> Dict[str, Dict[int, float]]:
    result = {
        "geschäftskonto-kg-gebäudereinigung": {m: 0.0 for m in range(1, 13)},
        "geschäftskonto-amazon-energie": {m: 0.0 for m in range(1, 13)},
        "damla-privat": {m: 0.0 for m in range(1, 13)},
        "murat-privat": {m: 0.0 for m in range(1, 13)},
    }

    conn = get_db_connection()
    rows = conn.execute("""
        SELECT account_slug, substr(datum, 6, 2) AS monat, SUM(amount) AS total
        FROM bank_cache
        WHERE substr(datum, 1, 4) = ?
        GROUP BY account_slug, monat
    """, (str(year),)).fetchall()
    conn.close()

    for r in rows:
        slug = r["account_slug"]
        monat = int(r["monat"])
        if slug in result:
            result[slug][monat] = float(r["total"] or 0)

    return result


if __name__ == "__main__":
    res = sync_starmoney_to_db()
    print(res["message"])
    print(get_starmoney_all_balances())