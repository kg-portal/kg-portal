"""Nur-Lese-Schnittstelle für den KG Agent (Süper Program).

- Öffnet die Datenbank ausschließlich im Nur-Lese-Modus (sqlite ``mode=ro``):
  Ändern, Löschen oder Senden ist technisch unmöglich.
- Nur mit normalem CRM-Login erreichbar (login_required).
- Geheime Spalten (Passwörter, Tokens, Zugangscodes, Roh-Mail-Daten) werden
  nie ausgegeben.
- Bestehende Routen und Funktionen werden nicht verändert; diese Datei
  fügt nur zwei GET-Routen hinzu.
"""

import os
import re
import sqlite3

from flask import jsonify, request


SECRET_COLUMN = re.compile(
    r"(passw|token|secret|access_code|api_?key|payload_json|refresh|cookie|session_id)",
    re.I,
)
MAX_LIMIT = 500


def register_agent_read_routes(app, login_required, db_path):
    def connect():
        path = os.path.abspath(db_path)
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def table_names(conn):
        return [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]

    def visible_columns(conn, table):
        cols = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
        return [c for c in cols if not SECRET_COLUMN.search(c)]

    @app.route("/api/agent-read/schema")
    @login_required
    def agent_read_schema():
        conn = connect()
        try:
            result = []
            for table in table_names(conn):
                count = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                result.append({
                    "table": table,
                    "rows": count,
                    "columns": visible_columns(conn, table),
                })
            return jsonify(ok=True, tables=result)
        finally:
            conn.close()

    @app.route("/api/agent-read/table/<name>")
    @login_required
    def agent_read_table(name):
        conn = connect()
        try:
            if name not in table_names(conn):
                return jsonify(ok=False, error="Tabelle unbekannt"), 404
            cols = visible_columns(conn, name)
            if not cols:
                return jsonify(ok=True, table=name, total=0, rows=[])

            where, params = [], []

            # Volltextsuche über alle sichtbaren Spalten
            q = (request.args.get("q") or "").strip()
            if q:
                like = f"%{q}%"
                where.append("(" + " OR ".join(f'CAST("{c}" AS TEXT) LIKE ?' for c in cols) + ")")
                params.extend([like] * len(cols))

            # Filter: spalte=wert, spalte__from=…, spalte__to=…, spalte__like=…
            for key, value in request.args.items():
                base, _, op = key.partition("__")
                if base not in cols:
                    continue
                if op == "":
                    where.append(f'CAST("{base}" AS TEXT) = ?')
                elif op == "from":
                    where.append(f'"{base}" >= ?')
                elif op == "to":
                    where.append(f'"{base}" <= ?')
                elif op == "like":
                    where.append(f'CAST("{base}" AS TEXT) LIKE ?')
                    value = f"%{value}%"
                else:
                    continue
                params.append(value)

            sql_where = (" WHERE " + " AND ".join(where)) if where else ""
            total = conn.execute(f'SELECT COUNT(*) FROM "{name}"{sql_where}', params).fetchone()[0]

            order = request.args.get("order")
            if order not in cols:
                order = "id" if "id" in cols else cols[0]
            direction = "DESC" if request.args.get("desc", "1") != "0" else "ASC"

            try:
                limit = max(1, min(MAX_LIMIT, int(request.args.get("limit", 50))))
                offset = max(0, int(request.args.get("offset", 0)))
            except ValueError:
                limit, offset = 50, 0

            select = ", ".join(f'"{c}"' for c in cols)
            rows = conn.execute(
                f'SELECT {select} FROM "{name}"{sql_where} '
                f'ORDER BY "{order}" {direction} LIMIT ? OFFSET ?',
                params + [limit, offset],
            ).fetchall()
            return jsonify(ok=True, table=name, total=total, rows=[dict(r) for r in rows])
        finally:
            conn.close()
