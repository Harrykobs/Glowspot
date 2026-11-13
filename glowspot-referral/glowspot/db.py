"""PostgreSQL database layer for Supabase.

The application uses a small DB-API compatibility wrapper so existing parameterised
queries can be migrated incrementally. PostgreSQL is the production database.
"""
from pathlib import Path
import re

import psycopg
class HybridRow(dict):
    """Dictionary row that also supports SQLite-style numeric column access."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def hybrid_row(cursor):
    # PostgreSQL commands such as CREATE TABLE have no result description.
    # They never produce rows, but psycopg still asks the row factory to
    # provide a maker while processing the command result.
    if cursor.description is None:
        return lambda values: values
    names = [column.name for column in cursor.description]
    return lambda values: HybridRow(zip(names, values))
from flask import g, current_app

DEFAULT_SETTINGS = {
    "first_skin_price": ("15000", "Skin analysis price: first visit and standard price (TZS)"),
    "returning_skin_price": ("10000", "Skin analysis price for returning customers who qualify (TZS)"),
    "referral_reward": ("2000", "Reward for a referred customer's first skin analysis (TZS)"),
    "package_commission_percent": ("8", "Package commission on each confirmed payment (%)"),
    "min_referrals_for_discount": ("2", "Qualifying referrals needed for the returning-customer price"),
}


class Cursor:
    def __init__(self, cursor, lastrowid=None):
        self._cursor = cursor
        self.lastrowid = lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def __iter__(self):
        return iter(self._cursor)


class Connection:
    def __init__(self, conn):
        self._conn = conn

    @staticmethod
    def _sql(sql):
        # Existing application SQL uses DB-API question-mark placeholders.
        # PostgreSQL does not support SQLite's NOCASE collation or rowid alias.
        sql = sql.replace('COLLATE NOCASE', '')
        sql = re.sub(r'ORDER\s+BY\s+rowid\b', 'ORDER BY key', sql, flags=re.I)
        return sql.replace('?', '%s')
    def execute(self, sql, params=()):
        sql = self._sql(sql)
        match = re.match(r"\s*INSERT\s+INTO\s+([a-z_]+)", sql, flags=re.I)
        wants_id = bool(match and match.group(1).lower() in {"users","customers","referral_changes","settings_history","packages","sales","payments","payouts","commissions","audit"} and not re.search(r"\bRETURNING\b", sql, flags=re.I))
        if wants_id:
            sql = sql.rstrip().rstrip(";") + " RETURNING id"
        cur = self._conn.cursor()
        cur.execute(sql, params or ())
        lastrowid = None
        if wants_id:
            row = cur.fetchone()
            lastrowid = row["id"] if row else None
            cur = self._conn.cursor()
        return Cursor(cur, lastrowid)

    def executemany(self, sql, seq):
        cur = self._conn.cursor()
        cur.executemany(self._sql(sql), seq)
        return Cursor(cur)

    def executescript(self, script):
        """Execute a trusted schema script containing multiple PostgreSQL statements.

        Psycopg uses the simple-query protocol for multi-statement scripts when
        prepare=False and no bound parameters are supplied.
        """
        cur = self._conn.cursor()
        cur.execute(script, prepare=False)
        return Cursor(cur)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def connect(database_url):
    if not database_url:
        raise RuntimeError("DATABASE_URL must be configured. Set it to your Supabase PostgreSQL connection string.")
    conn = psycopg.connect(database_url, connect_timeout=15, row_factory=hybrid_row)
    return Connection(conn)


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE_URL"])
    return g.db


def close_db(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        if exc is not None:
            conn.rollback()
        conn.close()


def init_db(conn):
    """Apply the versioned PostgreSQL schema and ensure default settings exist."""
    schema_path = Path(__file__).resolve().parent.parent / "migrations" / "001_postgres_schema.sql"
    if not schema_path.exists():
        raise RuntimeError(f"PostgreSQL schema migration not found: {schema_path}")
    try:
        conn.executescript(schema_path.read_text(encoding="utf-8"))
        for key, (value, label) in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT INTO settings(key,value,label) VALUES(?,?,?) ON CONFLICT (key) DO NOTHING",
                (key, value, label),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
