"""Database layer (SQLite). All SQL is parameterised."""
import sqlite3
from flask import g, current_app

DEFAULT_SETTINGS = {
    "first_skin_price": ("15000", "Skin analysis price: first visit and standard price (TZS)"),
    "returning_skin_price": ("10000", "Skin analysis price for returning customers who qualify (TZS)"),
    "referral_reward": ("2000", "Reward for a referred customer's first skin analysis (TZS)"),
    "package_commission_percent": ("8", "Package commission on each confirmed payment (%)"),
    "min_referrals_for_discount": ("2", "Qualifying referrals needed for the returning-customer price"),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  full_name TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('staff','manager')),
  active INTEGER NOT NULL DEFAULT 1,
  must_change_password INTEGER NOT NULL DEFAULT 0,
  failed_logins INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  created_at TEXT NOT NULL,
  last_login TEXT
);
CREATE TABLE IF NOT EXISTS customers(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  phone TEXT NOT NULL UNIQUE,
  referrer_id INTEGER REFERENCES customers(id),
  registered_at TEXT NOT NULL,
  registered_by INTEGER NOT NULL REFERENCES users(id),
  is_demo INTEGER NOT NULL DEFAULT 0,
  CHECK(referrer_id IS NULL OR referrer_id <> id)
);
CREATE INDEX IF NOT EXISTS idx_customers_referrer ON customers(referrer_id);
CREATE TABLE IF NOT EXISTS referral_changes(
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  old_referrer_id INTEGER REFERENCES customers(id),
  new_referrer_id INTEGER REFERENCES customers(id),
  reason TEXT NOT NULL,
  changed_by INTEGER NOT NULL REFERENCES users(id),
  changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings(
  key TEXT PRIMARY KEY, value TEXT NOT NULL, label TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings_history(
  id INTEGER PRIMARY KEY, key TEXT NOT NULL, old_value TEXT, new_value TEXT NOT NULL,
  reason TEXT NOT NULL, changed_by INTEGER NOT NULL REFERENCES users(id), changed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS packages(
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  price INTEGER NOT NULL CHECK(price > 0),
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS sales(
  id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  kind TEXT NOT NULL CHECK(kind IN ('skin','package')),
  item_name TEXT NOT NULL,
  original_price INTEGER NOT NULL,
  discount INTEGER NOT NULL DEFAULT 0,
  amount_due INTEGER NOT NULL,
  is_first_skin INTEGER NOT NULL DEFAULT 0,
  price_note TEXT,
  service_date TEXT NOT NULL,
  recorded_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','voided')),
  void_reason TEXT, voided_by INTEGER REFERENCES users(id), voided_at TEXT,
  CHECK(discount >= 0 AND amount_due > 0 AND amount_due = original_price - discount)
);
CREATE INDEX IF NOT EXISTS idx_sales_customer ON sales(customer_id);
CREATE TABLE IF NOT EXISTS payments(
  id INTEGER PRIMARY KEY,
  sale_id INTEGER NOT NULL REFERENCES sales(id),
  amount INTEGER NOT NULL CHECK(amount > 0),
  method TEXT NOT NULL CHECK(method IN ('Cash','Mobile money','Bank transfer','Card')),
  paid_on TEXT NOT NULL,
  recorded_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'confirmed' CHECK(status IN ('confirmed','reversed')),
  reversed_reason TEXT, reversed_by INTEGER REFERENCES users(id), reversed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_payments_sale ON payments(sale_id);
CREATE TABLE IF NOT EXISTS payouts(
  id INTEGER PRIMARY KEY,
  referrer_id INTEGER NOT NULL REFERENCES customers(id),
  amount INTEGER NOT NULL,
  method TEXT NOT NULL CHECK(method IN ('Cash','Mobile money','Bank transfer','Card')),
  paid_on TEXT NOT NULL,
  recorded_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  note TEXT
);
CREATE TABLE IF NOT EXISTS commissions(
  id INTEGER PRIMARY KEY,
  referrer_id INTEGER NOT NULL REFERENCES customers(id),
  referred_id INTEGER NOT NULL REFERENCES customers(id),
  sale_id INTEGER NOT NULL REFERENCES sales(id),
  payment_id INTEGER REFERENCES payments(id),
  kind TEXT NOT NULL CHECK(kind IN ('skin_reward','package_commission','adjustment')),
  base_amount INTEGER NOT NULL,
  rate_label TEXT NOT NULL,
  amount INTEGER NOT NULL,
  earned_at TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('Pending','Approved','Paid','Reversed')),
  approved_by INTEGER REFERENCES users(id), approved_at TEXT,
  payout_id INTEGER REFERENCES payouts(id),
  reverses_id INTEGER REFERENCES commissions(id),
  reversed_at TEXT, reversed_by INTEGER REFERENCES users(id), reversal_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_comm_referrer ON commissions(referrer_id);
CREATE INDEX IF NOT EXISTS idx_comm_sale ON commissions(sale_id);
-- One active first-skin-analysis reward per referred customer (database-level guard).
CREATE UNIQUE INDEX IF NOT EXISTS uq_skin_reward ON commissions(referred_id)
  WHERE kind='skin_reward' AND reversed_at IS NULL;
-- One package commission per confirmed payment.
CREATE UNIQUE INDEX IF NOT EXISTS uq_pkg_commission ON commissions(payment_id)
  WHERE kind='package_commission';
CREATE TABLE IF NOT EXISTS audit(
  id INTEGER PRIMARY KEY,
  user_id INTEGER, username TEXT,
  action TEXT NOT NULL, entity TEXT, entity_id INTEGER,
  detail TEXT, reason TEXT, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit(created_at);
CREATE VIEW IF NOT EXISTS sale_totals AS
  SELECT s.id AS sale_id, s.customer_id, s.kind, s.amount_due, s.status, s.service_date, s.is_first_skin,
         COALESCE((SELECT SUM(p.amount) FROM payments p WHERE p.sale_id=s.id AND p.status='confirmed'),0) AS paid
  FROM sales s;
-- A "qualifying paid visit": package with a confirmed payment, or skin analysis paid in full.
CREATE VIEW IF NOT EXISTS qualifying_sales AS
  SELECT * FROM sale_totals
  WHERE status='active' AND ((kind='package' AND paid>0) OR (kind='skin' AND paid>=amount_due));
"""


def connect(path):
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        if exc is not None:
            conn.rollback()
        conn.close()


def init_db(conn):
    """Create tables if missing and insert default settings. Safe to run repeatedly."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    for key, (value, label) in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key,value,label) VALUES(?,?,?)", (key, value, label))
    conn.execute("PRAGMA user_version=1")
    conn.commit()
