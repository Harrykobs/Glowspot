"""Synthetic demo data for the temporary test environment. Never use real customers here."""
import os

from werkzeug.security import generate_password_hash

from . import services as svc

DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "Glowspot-Demo-2026")


def seed_demo(conn):
    if conn.execute("SELECT 1 FROM customers WHERE is_demo=1").fetchone():
        return "Demo data already present."
    now = svc.now_utc()
    for username, name, role in (("demo.manager", "Demo Manager", "manager"), ("demo.staff", "Demo Staff", "staff")):
        if not conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            conn.execute("INSERT INTO users(username,full_name,password_hash,role,active,must_change_password,created_at)"
                         " VALUES(?,?,?,?,1,0,?)", (username, name, generate_password_hash(DEMO_PASSWORD), role, now))
    manager = conn.execute("SELECT * FROM users WHERE username='demo.manager'").fetchone()
    staff = conn.execute("SELECT * FROM users WHERE username='demo.staff'").fetchone()
    for name, price in (("Glow Basic Package (demo)", 60000), ("Radiance Package (demo)", 100000), ("Complete Care Package (demo)", 180000)):
        conn.execute("INSERT OR IGNORE INTO packages(name,price,active) VALUES(?,?,1)", (name, price))
    rad = conn.execute("SELECT id FROM packages WHERE name LIKE 'Radiance%'").fetchone()["id"]
    reg = lambda n, p, ref="": svc.register_customer(conn, staff, n, p, ref, is_demo=1)
    a = reg("Amina Test", "0700000001")
    reg("Baraka Test", "0700000002", "0700000001")
    c = reg("Chausiku Test", "0700000003", "0700000001")
    reg("Daudi Test", "0700000004", "0700000001")
    reg("Esther Test", "0700000005")
    for cid in (a, a + 1, a + 3):
        svc.create_sale(conn, staff, cid, "skin", paid_raw="15000", method="Cash")
    sale, _ = svc.create_sale(conn, staff, c, "package", package_id=rad, discount_raw="0", paid_raw="50000", method="Mobile money")
    svc.add_payment(conn, staff, sale, "50000", "Cash", None)
    conn.commit()
    return ("Demo data added. Logins: demo.manager / demo.staff, password from DEMO_PASSWORD "
            "(default Glowspot-Demo-2026). All records are synthetic.")
