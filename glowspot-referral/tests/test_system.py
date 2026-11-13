"""Automated tests for the Glowspot referral system.

Run:  python -m unittest discover -s tests -v
"""
import io
import os
import re
import psycopg
import unittest
import zipfile
from datetime import timedelta

from werkzeug.security import generate_password_hash

from glowspot import create_app
from glowspot import db as dbm
from glowspot import reports as rpt
from glowspot import services as svc
from glowspot.services import RuleError

PW = "Test-Pass-123"


def make_db():
    """Use an explicitly dedicated PostgreSQL test database; never fall back to SQLite."""
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set TEST_DATABASE_URL to a dedicated disposable PostgreSQL test database.")
    conn = dbm.connect(database_url)
    dbm.init_db(conn)
    conn.execute(
        "TRUNCATE TABLE audit, commissions, payouts, payments, sales, packages, "
        "settings_history, referral_changes, customers, users, settings RESTART IDENTITY CASCADE"
    )
    for u, r in (("mgr", "manager"), ("stf", "staff")):
        conn.execute("INSERT INTO users(username,full_name,password_hash,role,active,must_change_password,created_at)"
                     " VALUES(?,?,?,?,1,0,?)", (u, u.upper(), generate_password_hash(PW), r, svc.now_utc()))
    conn.commit()
    return database_url, conn


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.database_url, self.db = make_db()
        self.mgr = self.db.execute("SELECT * FROM users WHERE username='mgr'").fetchone()
        self.stf = self.db.execute("SELECT * FROM users WHERE username='stf'").fetchone()
        self.db.execute("INSERT INTO packages(name,price) VALUES('Radiance',100000)")
        self.pkg = self.db.execute("SELECT id FROM packages").fetchone()["id"]
        self.n = 0

    def tearDown(self):
        self.db.close()
        

    def cust(self, name, ref=""):
        self.n += 1
        return svc.register_customer(self.db, self.stf, name, f"07000000{self.n:02d}", ref)

    def phone(self, cid):
        return svc.get_customer(self.db, cid)["phone"]

    def skin(self, cid, paid="15000"):
        return svc.create_sale(self.db, self.stf, cid, "skin", paid_raw=paid, method="Cash")

    def unpaid(self, cid):
        return svc.commission_summary(self.db, cid)["unpaid"]

    # ---- phone and registration
    def test_phone_normalisation(self):
        for raw in ("0786189844", "0786 189 844", "+255786189844", "255786189844", "786189844", "00255 786-189-844"):
            self.assertEqual(svc.normalize_phone(raw), "+255786189844", raw)
        for bad in ("", "12345", "0586189844", "07861898", "abc", "+254786189844"):
            with self.assertRaises(RuleError, msg=bad):
                svc.normalize_phone(bad)
        self.assertEqual(svc.format_phone("+255786189844"), "0786 189 844")

    def test_duplicate_phone_rejected_in_any_format(self):
        a = self.cust("Amina")
        with self.assertRaises(RuleError) as cm:
            svc.register_customer(self.db, self.stf, "Other", "+255 700 000 001")
        self.assertEqual(cm.exception.customer_id, a)

    def test_self_referral_and_unknown_referrer(self):
        with self.assertRaises(RuleError):
            svc.register_customer(self.db, self.stf, "Solo", "0711111111", "0711111111")
        with self.assertRaises(RuleError):
            svc.register_customer(self.db, self.stf, "Solo", "0711111111", "0722222222")
        self.assertIsNone(self.db.execute("SELECT 1 FROM customers WHERE phone='+255711111111'").fetchone())

    # ---- skin analysis rewards
    def test_first_skin_reward_2000(self):
        a = self.cust("Amina")
        b = self.cust("Baraka", self.phone(a))
        sale, awards = self.skin(b)
        self.assertEqual([x["amount"] for x in awards], [2000])
        self.assertEqual(awards[0]["referrer_id"], a)
        self.assertEqual(self.unpaid(a), 2000)

    def test_no_reward_until_first_skin_fully_paid(self):
        a = self.cust("Amina")
        b = self.cust("Baraka", self.phone(a))
        sale, awards = self.skin(b, "10000")
        self.assertEqual(awards, [])
        self.assertEqual(self.unpaid(a), 0)
        awards = svc.add_payment(self.db, self.stf, sale, "5000", "Cash", None)
        self.assertEqual([x["amount"] for x in awards], [2000])

    def test_reward_only_once_per_referred_customer(self):
        a = self.cust("Amina")
        b = self.cust("Baraka", self.phone(a))
        self.skin(b)
        sale2, awards = self.skin(b)  # second skin analysis (returning) earns nothing
        self.assertEqual(awards, [])
        self.assertEqual(self.unpaid(a), 2000)
        with self.assertRaises(psycopg.IntegrityError):  # database-level guard
            self.db.execute("INSERT INTO commissions(referrer_id,referred_id,sale_id,kind,base_amount,rate_label,amount,earned_at,status)"
                            " VALUES(?,?,1,'skin_reward',15000,'Fixed',2000,'2026-01-01 00:00:00','Pending')", (a, b))

    def test_returning_customers_own_visit_earns_no_reward_for_their_referrer(self):
        z = self.cust("Zed")
        a = self.cust("Amina", self.phone(z))
        self.skin(a)
        self.assertEqual(self.unpaid(z), 2000)
        sale, awards = self.skin(a)
        self.assertEqual(awards, [])
        self.assertEqual(self.unpaid(z), 2000)

    def test_returning_price_needs_two_qualifying_referrals(self):
        a = self.cust("Amina")
        self.skin(a)
        self.assertEqual(svc.skin_quote(self.db, a)["price"], 15000)  # standard price, no referrals
        b = self.cust("Baraka", self.phone(a))
        c = self.cust("Chausiku", self.phone(a))
        self.skin(b)
        q = svc.skin_quote(self.db, a)
        self.assertEqual((q["price"], q["qualifies"], q["referrals"], q["remaining"]), (15000, False, 1, 1))
        self.skin(c, "10000")  # partial payment does not qualify
        self.assertEqual(svc.skin_quote(self.db, a)["referrals"], 1)
        sale = self.db.execute("SELECT id FROM sales WHERE customer_id=?", (c,)).fetchone()["id"]
        svc.add_payment(self.db, self.stf, sale, "5000", "Cash", None)
        q = svc.skin_quote(self.db, a)
        self.assertEqual((q["price"], q["qualifies"], q["referrals"]), (10000, True, 2))
        sale_a, awards = self.skin(a, "10000")
        self.assertEqual(awards, [])
        self.assertEqual(self.db.execute("SELECT amount_due FROM sales WHERE id=?", (sale_a,)).fetchone()["amount_due"], 10000)

    def test_registered_but_unpaid_referral_does_not_count_and_package_visit_counts(self):
        a = self.cust("Amina")
        self.skin(a)
        b = self.cust("Baraka", self.phone(a))
        c = self.cust("Chausiku", self.phone(a))
        self.assertEqual(svc.qualifying_referral_count(self.db, a), 0)
        svc.create_sale(self.db, self.stf, b, "package", package_id=self.pkg, discount_raw="0", paid_raw="1000", method="Cash")
        self.assertEqual(svc.qualifying_referral_count(self.db, a), 1)
        self.assertEqual(c and svc.qualifying_referral_count(self.db, a), 1)

    def test_discount_for_returning_customer_but_first_visit_always_standard(self):
        a = self.cust("Amina")
        b = self.cust("Baraka", self.phone(a))
        c = self.cust("Chausiku", self.phone(a))
        self.skin(b)
        self.skin(c)
        self.assertEqual(svc.qualifying_referral_count(self.db, a), 2)
        q = svc.skin_quote(self.db, a)  # Amina has qualifying referrals but has never had a skin analysis
        self.assertEqual((q["price"], q["is_first"]), (15000, True))

    def test_unpaid_skin_analysis_blocks_a_second_one(self):
        a = self.cust("Amina")
        self.skin(a, "0")
        with self.assertRaises(RuleError):
            self.skin(a)

    # ---- package commission
    def test_package_commission_full_payment(self):
        a = self.cust("Amina")
        b = self.cust("Baraka", self.phone(a))
        sale, awards = svc.create_sale(self.db, self.stf, b, "package", package_id=self.pkg, discount_raw="0", paid_raw="100000", method="Mobile money")
        self.assertEqual([x["amount"] for x in awards], [8000])
        self.assertEqual(self.unpaid(a), 8000)

    def test_package_commission_on_each_instalment(self):
        a = self.cust("Amina")
        b = self.cust("Baraka", self.phone(a))
        sale, awards = svc.create_sale(self.db, self.stf, b, "package", package_id=self.pkg, discount_raw="0", paid_raw="50000", method="Cash")
        self.assertEqual([x["amount"] for x in awards], [4000])
        awards = svc.add_payment(self.db, self.stf, sale, "50000", "Cash", None)
        self.assertEqual([x["amount"] for x in awards], [4000])
        self.assertEqual(self.unpaid(a), 8000)

    def test_package_commission_after_discount_and_not_on_unpaid_balance(self):
        a = self.cust("Amina")
        b = self.cust("Baraka", self.phone(a))
        sale, awards = svc.create_sale(self.db, self.stf, b, "package", package_id=self.pkg, discount_raw="20000", paid_raw="30000", method="Cash")
        self.assertEqual([x["amount"] for x in awards], [2400])  # 8% of 30,000 actually collected
        row = self.db.execute("SELECT original_price,discount,amount_due FROM sales WHERE id=?", (sale,)).fetchone()
        self.assertEqual(tuple(row), (100000, 20000, 80000))
        sale0, awards = svc.create_sale(self.db, self.stf, b, "package", package_id=self.pkg, paid_raw="0")
        self.assertEqual(awards, [])  # nothing paid, no commission

    def test_no_commission_without_referrer_and_overpayment_rejected(self):
        b = self.cust("Baraka")
        sale, awards = self.skin(b)
        self.assertEqual(awards, [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) c FROM commissions").fetchone()["c"], 0)
        with self.assertRaises(RuleError):
            svc.add_payment(self.db, self.stf, sale, "1", "Cash", None)
        with self.assertRaises(RuleError):
            svc.create_sale(self.db, self.stf, b, "package", package_id=self.pkg, paid_raw="100001", method="Cash")

    def test_commission_rounds_half_up_to_whole_shillings(self):
        a = self.cust("Amina")
        b = self.cust("Baraka", self.phone(a))
        sale, awards = svc.create_sale(self.db, self.stf, b, "package", package_id=self.pkg, paid_raw="1006", method="Cash")
        self.assertEqual(awards[0]["amount"], 80)  # 80.48 -> 80
        awards = svc.add_payment(self.db, self.stf, sale, "1000", "Cash", None)
        self.assertEqual(awards[0]["amount"], 80)

    # ---- reversals
    def test_reverse_payment_reverses_pending_commission(self):
        a = self.cust("Amina")
        b = self.cust("Baraka", self.phone(a))
        sale, awards = svc.create_sale(self.db, self.stf, b, "package", package_id=self.pkg, paid_raw="100000", method="Cash")
        pid = self.db.execute("SELECT id FROM payments").fetchone()["id"]
        with self.assertRaises(RuleError):
            svc.reverse_payment(self.db, self.stf, pid, "staff try")  # staff not allowed
        with self.assertRaises(RuleError):
            svc.reverse_payment(self.db, self.mgr, pid, "")  # reason required
        svc.reverse_payment(self.db, self.mgr, pid, "Customer refunded")
        self.assertEqual(self.unpaid(a), 0)
        self.assertEqual(self.db.execute("SELECT status FROM commissions").fetchone()["status"], "Reversed")
        self.assertEqual(self.db.execute("SELECT COUNT(*) c FROM audit WHERE action='payment_reversed' AND reason='Customer refunded'").fetchone()["c"], 1)
        with self.assertRaises(RuleError):
            svc.reverse_payment(self.db, self.mgr, pid, "again")

    def test_reversing_one_skin_payment_withdraws_reward_and_it_can_be_earned_again(self):
        a = self.cust("Amina")
        b = self.cust("Baraka", self.phone(a))
        sale, _ = self.skin(b, "10000")
        svc.add_payment(self.db, self.stf, sale, "5000", "Cash", None)
        self.assertEqual(self.unpaid(a), 2000)
        first_pay = self.db.execute("SELECT id FROM payments ORDER BY id").fetchone()["id"]
        svc.reverse_payment(self.db, self.mgr, first_pay, "Entered wrongly")
        self.assertEqual(self.unpaid(a), 0)
        awards = svc.add_payment(self.db, self.stf, sale, "10000", "Cash", None)
        self.assertEqual([x["amount"] for x in awards], [2000])

    def test_reversing_a_paid_out_commission_creates_negative_adjustment(self):
        a = self.cust("Amina")
        b = self.cust("Baraka", self.phone(a))
        self.skin(b)
        cid = self.db.execute("SELECT id FROM commissions").fetchone()["id"]
        svc.approve_commissions(self.db, self.mgr, [cid])
        svc.record_payout(self.db, self.mgr, a, [cid], "Cash", None)
        s = svc.commission_summary(self.db, a)
        self.assertEqual((s["earned"], s["paid"], s["unpaid"]), (2000, 2000, 0))
        pid = self.db.execute("SELECT id FROM payments").fetchone()["id"]
        svc.reverse_payment(self.db, self.mgr, pid, "Refund after payout")
        s = svc.commission_summary(self.db, a)
        self.assertEqual((s["earned"], s["paid"], s["unpaid"]), (0, 2000, -2000))  # referrer owes 2,000 back
        adj = self.db.execute("SELECT * FROM commissions WHERE kind='adjustment'").fetchone()
        self.assertEqual((adj["amount"], adj["status"]), (-2000, "Approved"))

    def test_void_sale_reverses_everything(self):
        a = self.cust("Amina")
        b = self.cust("Baraka", self.phone(a))
        sale, _ = svc.create_sale(self.db, self.stf, b, "package", package_id=self.pkg, paid_raw="50000", method="Cash")
        svc.add_payment(self.db, self.stf, sale, "20000", "Cash", None)
        self.assertEqual(self.unpaid(a), 5600)
        with self.assertRaises(RuleError):
            svc.void_sale(self.db, self.stf, sale, "not allowed")
        svc.void_sale(self.db, self.mgr, sale, "Entered for wrong customer")
        self.assertEqual(self.unpaid(a), 0)
        self.assertEqual(svc.qualifying_referral_count(self.db, a), 0)
        with self.assertRaises(RuleError):
            svc.add_payment(self.db, self.stf, sale, "1000", "Cash", None)

    # ---- referral link control
    def test_referral_link_change_rules(self):
        a, b, c = self.cust("Amina"), self.cust("Baraka"), self.cust("Chausiku")
        with self.assertRaises(RuleError):
            svc.change_referrer(self.db, self.stf, b, self.phone(a), "staff attempt")
        with self.assertRaises(RuleError):
            svc.change_referrer(self.db, self.mgr, b, self.phone(a), "")
        with self.assertRaises(RuleError):
            svc.change_referrer(self.db, self.mgr, b, self.phone(b), "self refer")
        svc.change_referrer(self.db, self.mgr, b, self.phone(a), "Customer confirmed Amina referred her")
        self.assertEqual(svc.get_customer(self.db, b)["referrer_id"], a)
        with self.assertRaises(RuleError):  # circular: Amina cannot be referred by Baraka
            svc.change_referrer(self.db, self.mgr, a, self.phone(b), "circular")
        svc.change_referrer(self.db, self.mgr, b, self.phone(c), "Corrected after checking")
        hist = self.db.execute("SELECT * FROM referral_changes WHERE customer_id=? ORDER BY id", (b,)).fetchall()
        self.assertEqual([(h["old_referrer_id"], h["new_referrer_id"]) for h in hist], [(None, a), (a, c)])

    # ---- approvals and payouts
    def test_approval_and_payout_flow(self):
        a = self.cust("Amina")
        b = self.cust("Baraka", self.phone(a))
        self.skin(b)
        svc.create_sale(self.db, self.stf, b, "package", package_id=self.pkg, paid_raw="100000", method="Cash")
        ids = [r["id"] for r in self.db.execute("SELECT id FROM commissions ORDER BY id")]
        with self.assertRaises(RuleError):
            svc.record_payout(self.db, self.mgr, a, ids, "Cash", None)  # still pending
        with self.assertRaises(RuleError):
            svc.approve_commissions(self.db, self.stf, ids)
        self.assertEqual(svc.approve_commissions(self.db, self.mgr, ids), 2)
        _, total = svc.record_payout(self.db, self.mgr, a, ids, "Mobile money", None, "Sent by M-Pesa")
        self.assertEqual(total, 10000)
        s = svc.commission_summary(self.db, a)
        self.assertEqual((s["earned"], s["paid"], s["unpaid"]), (10000, 10000, 0))
        with self.assertRaises(RuleError):
            svc.record_payout(self.db, self.mgr, a, ids, "Cash", None)  # cannot pay twice

    # ---- settings
    def test_settings_change_is_recorded_and_applies_to_new_sales_only(self):
        a = self.cust("Amina")
        b = self.cust("Baraka", self.phone(a))
        c = self.cust("Chausiku", self.phone(a))
        self.skin(b)
        with self.assertRaises(RuleError):
            svc.update_settings(self.db, self.stf, {"referral_reward": "3000"}, "staff")
        with self.assertRaises(RuleError):
            svc.update_settings(self.db, self.mgr, {"referral_reward": "3000"}, "")
        with self.assertRaises(RuleError):
            svc.update_settings(self.db, self.mgr, {"package_commission_percent": "150"}, "too high")
        svc.update_settings(self.db, self.mgr, {"referral_reward": "3000", "package_commission_percent": "10"}, "New promotion")
        self.assertEqual(self.unpaid(a), 2000)  # earlier reward untouched
        self.skin(c)
        self.assertEqual(self.unpaid(a), 5000)
        d = self.cust("Daudi", self.phone(a))
        svc.create_sale(self.db, self.stf, d, "package", package_id=self.pkg, paid_raw="10000", method="Cash")
        self.assertEqual(self.unpaid(a), 6000)
        self.assertEqual(self.db.execute("SELECT COUNT(*) c FROM settings_history").fetchone()["c"], 2)

    # ---- dates, search, users
    def test_staff_date_limits(self):
        a = self.cust("Amina")
        future = (svc.today_eat() + timedelta(days=1)).isoformat()
        old = (svc.today_eat() - timedelta(days=30)).isoformat()
        with self.assertRaises(RuleError):
            svc.create_sale(self.db, self.stf, a, "skin", paid_raw="0", date_raw=future)
        with self.assertRaises(RuleError):
            svc.create_sale(self.db, self.stf, a, "skin", paid_raw="0", date_raw=old)
        svc.create_sale(self.db, self.mgr, a, "skin", paid_raw="0", date_raw=old)

    def test_search_rules(self):
        a = self.cust("Amina")
        self.assertEqual([r["id"] for r in svc.search_customers(self.db, "0700 000 001", self.stf)], [a])
        self.assertEqual([r["id"] for r in svc.search_customers(self.db, "0000", self.stf)], [a])
        with self.assertRaises(RuleError):
            svc.search_customers(self.db, "070", self.stf)
        with self.assertRaises(RuleError):
            svc.search_customers(self.db, "Amina", self.stf)  # staff cannot browse by name
        self.assertEqual(len(svc.search_customers(self.db, "amin", self.mgr)), 1)
        self.assertEqual(svc.search_customers(self.db, "%", self.mgr), [])  # wildcard is escaped
        self.assertEqual(svc.search_customers(self.db, "x' OR '1'='1", self.mgr), [])

    def test_user_rules(self):
        with self.assertRaises(RuleError):
            svc.create_user(self.db, self.mgr, "newbie", "New Staff", "staff", "short")
        with self.assertRaises(RuleError):
            svc.create_user(self.db, self.mgr, "newbie", "New Staff", "staff", "onlyletters")
        svc.create_user(self.db, self.mgr, "newbie", "New Staff", "staff", "Pass1234")
        with self.assertRaises(RuleError):
            svc.create_user(self.db, self.mgr, "NEWBIE", "Dup", "staff", "Pass1234")
        with self.assertRaises(RuleError):
            svc.update_user(self.db, self.mgr, self.mgr["id"], "Mgr", "manager", False)  # cannot disable self
        stored = self.db.execute("SELECT password_hash FROM users WHERE username='newbie'").fetchone()["password_hash"]
        self.assertNotIn("Pass1234", stored)

    def test_login_lockout(self):
        for _ in range(svc.MAX_FAILED_LOGINS):
            with self.assertRaises(RuleError):
                svc.attempt_login(self.db, "stf", "wrong")
        with self.assertRaises(RuleError) as cm:
            svc.attempt_login(self.db, "stf", PW)  # correct password is refused while locked
        self.assertIn("Too many", str(cm.exception))


class ReportsTest(unittest.TestCase):
    def setUp(self):
        self.database_url, self.db = make_db()
        self.stf = self.db.execute("SELECT * FROM users WHERE username='stf'").fetchone()
        self.db.execute("INSERT INTO packages(name,price) VALUES('Radiance',100000)")
        a = svc.register_customer(self.db, self.stf, "Amina", "0700000001")
        b = svc.register_customer(self.db, self.stf, "Baraka", "0700000002", "0700000001")
        c = svc.register_customer(self.db, self.stf, "Chausiku", "0700000003", "0700000001")
        svc.create_sale(self.db, self.stf, a, "skin", paid_raw="15000", method="Cash")
        svc.create_sale(self.db, self.stf, b, "skin", paid_raw="15000", method="Cash")
        svc.create_sale(self.db, self.stf, c, "package", package_id=1, paid_raw="100000", method="Cash")
        self.db.commit()
        self.args = {"start": svc.today_eat().replace(day=1).isoformat(), "end": svc.today_eat().isoformat()}

    def tearDown(self):
        self.db.close()
        

    def test_report_contents(self):
        r = rpt.build(self.db, "customers", self.args)
        self.assertEqual(len([x for x in r["rows"] if x[0] == "New"]), 3)
        r = rpt.build(self.db, "referrals", self.args)
        self.assertEqual(len(r["rows"]), 2)
        r = rpt.build(self.db, "sales", self.args)
        self.assertEqual(len(r["rows"]), 3)
        r = rpt.build(self.db, "commissions", self.args)
        self.assertEqual(sorted(x[8] for x in r["rows"]), [2000, 8000])
        r = rpt.build(self.db, "balances", self.args)
        self.assertEqual((r["rows"][0][0], r["rows"][0][6]), ("Amina", 10000))
        r = rpt.build(self.db, "top", self.args)
        self.assertEqual((r["rows"][0][1], r["rows"][0][3]), ("Amina", 2))
        for g_ in ("day", "week", "month"):
            r = rpt.build(self.db, "trend", {**self.args, "group": g_})
            self.assertEqual(sum(x[2] for x in r["rows"]), 130000)
            self.assertEqual(sum(x[5] for x in r["rows"]), 10000)

    def test_returning_customers_report(self):
        a = svc.get_customer(self.db, 1)["id"]
        old = "2026-01-05"
        self.db.execute("UPDATE payments SET paid_on=? WHERE id=1", (old,))
        svc.create_sale(self.db, self.stf, a, "package", package_id=1, paid_raw="1000", method="Cash")
        r = rpt.build(self.db, "customers", self.args)
        self.assertIn("Amina", [x[1] for x in r["rows"] if x[0] == "Returning"])

    def test_exports_are_valid_files(self):
        for key in rpt.REPORTS:
            rep = rpt.build(self.db, key, self.args)
            x = rpt.to_xlsx(rep)
            self.assertTrue(zipfile.is_zipfile(io.BytesIO(x)), key)
            self.assertTrue(rpt.to_pdf(rep).startswith(b"%PDF"), key)

    def test_bad_dates_rejected(self):
        with self.assertRaises(RuleError):
            rpt.parse_range({"start": "2026-02-01", "end": "2026-01-01"})
        with self.assertRaises(RuleError):
            rpt.parse_range({"start": "nonsense"})


class WebTest(unittest.TestCase):
    def setUp(self):
        self.database_url, conn = make_db()
        conn.execute("INSERT INTO packages(name,price) VALUES('Radiance',100000)")
        conn.execute("INSERT INTO users(username,full_name,password_hash,role,active,must_change_password,created_at)"
                     " VALUES('fresh','Fresh User',?,'staff',1,1,?)", (generate_password_hash(PW), svc.now_utc()))
        conn.commit()
        conn.close()
        self.app = create_app({"DATABASE_URL": self.database_url, "TESTING": True, "SECRET_KEY": "test-secret"})

    def tearDown(self):
        

    def token(self, c, page="/"):
        return re.search(r'name="csrf_token" value="([^"]+)"', c.get(page).get_data(as_text=True)).group(1)

    def login(self, user, pw=PW):
        c = self.app.test_client()
        r = c.post("/login", data={"username": user, "password": pw, "csrf_token": self.token(c, "/login")})
        return c, r

    def post(self, c, path, data, page="/"):
        data = {**data, "csrf_token": self.token(c, page)}
        return c.post(path, data=data, follow_redirects=True)

    def test_login_logout_and_protection(self):
        c = self.app.test_client()
        self.assertEqual(c.get("/").status_code, 302)  # must log in
        self.assertEqual(c.get("/customers/1").status_code, 302)
        self.assertEqual(self.login("stf", "wrong")[1].status_code, 401)
        c, r = self.login("stf")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.get("/").status_code, 200)
        self.post(c, "/logout", {})
        self.assertEqual(c.get("/").status_code, 302)

    def test_csrf_enforced(self):
        c, _ = self.login("stf")
        self.assertEqual(c.post("/customers/new", data={"name": "X Y", "phone": "0711111111"}).status_code, 400)
        self.assertEqual(c.post("/customers/new", data={"name": "X Y", "phone": "0711111111", "csrf_token": "bad"}).status_code, 400)

    def test_role_permissions(self):
        s, _ = self.login("stf")
        for path in ("/reports", "/staff", "/commissions", "/settings", "/packages", "/audit", "/backup", "/payouts",
                     "/reports/sales/export/xlsx"):
            self.assertEqual(s.get(path).status_code, 403, path)
        self.assertEqual(self.post(s, "/commissions/approve", {"ids": "1"}).status_code, 403)
        m, _ = self.login("mgr")
        for path in ("/reports", "/staff", "/commissions", "/settings", "/packages", "/audit", "/backup", "/payouts", "/customers"):
            self.assertEqual(m.get(path).status_code, 200, path)

    def test_forced_password_change(self):
        c, r = self.login("fresh")
        self.assertEqual(c.get("/customers").headers["Location"].split("?")[0].endswith("/account/password"), True)
        self.post(c, "/account/password", {"current": PW, "new": "Newer-Pass-9", "confirm": "Newer-Pass-9"}, "/account/password")
        self.assertEqual(c.get("/customers").status_code, 200)

    def test_disabled_user_loses_access_immediately(self):
        c, _ = self.login("stf")
        self.assertEqual(c.get("/").status_code, 200)
        conn = dbm.connect(self.database_url)
        conn.execute("UPDATE users SET active=0 WHERE username='stf'")
        conn.commit()
        conn.close()
        self.assertEqual(c.get("/").status_code, 302)

    def test_full_shop_workflow_through_the_website(self):
        c, _ = self.login("stf")
        self.post(c, "/customers/new", {"name": "Amina Test", "phone": "0700000001", "referrer_phone": ""}, "/customers/new")
        r = self.post(c, "/customers/new", {"name": "Baraka Test", "phone": "0700 000 002", "referrer_phone": "0700000001"}, "/customers/new")
        self.assertIn("Customer registered", r.get_data(as_text=True))
        r = self.post(c, "/customers/new", {"name": "Dup", "phone": "+255700000002", "referrer_phone": ""}, "/customers/new")
        self.assertIn("already belongs to Baraka Test", r.get_data(as_text=True))
        r = self.post(c, "/customers/new", {"name": "Self", "phone": "0700000009", "referrer_phone": "0700000009"}, "/customers/new")
        self.assertIn("cannot refer themselves", r.get_data(as_text=True))
        r = self.post(c, "/customers/new", {"name": "Ghost", "phone": "0700000008", "referrer_phone": "0799999999"}, "/customers/new")
        self.assertIn("not a registered customer", r.get_data(as_text=True))
        r = c.get("/customers?q=0700000002", follow_redirects=True)
        self.assertIn("Baraka Test", r.get_data(as_text=True))
        self.assertIn("TZS 15,000", r.get_data(as_text=True))
        r = self.post(c, "/customers/2/sales/new", {"kind": "skin", "paid": "15000", "method": "Cash", "date": svc.today_eat().isoformat()}, "/customers/2/sales/new")
        html = r.get_data(as_text=True)
        self.assertIn("earned TZS 2,000", html)
        self.assertIn("Unpaid balance now TZS 2,000", html)
        r = self.post(c, "/customers/2/sales/new", {"kind": "package", "package_id": "1", "discount": "0", "paid": "50000", "method": "Mobile money", "date": svc.today_eat().isoformat()}, "/customers/2/sales/new")
        self.assertIn("earned TZS 4,000", r.get_data(as_text=True))
        r = c.get("/customers/1")
        self.assertIn("TZS 6,000", r.get_data(as_text=True))

    def test_manager_workflow_and_exports(self):
        s, _ = self.login("stf")
        self.post(s, "/customers/new", {"name": "Amina Test", "phone": "0700000001"}, "/customers/new")
        self.post(s, "/customers/new", {"name": "Baraka Test", "phone": "0700000002", "referrer_phone": "0700000001"}, "/customers/new")
        self.post(s, "/customers/2/sales/new", {"kind": "skin", "paid": "15000", "method": "Cash", "date": svc.today_eat().isoformat()}, "/customers/2/sales/new")
        m, _ = self.login("mgr")
        self.post(m, "/commissions/approve", {"ids": "1"}, "/commissions")
        r = self.post(m, "/customers/1/payout", {"ids": "1", "method": "Cash", "date": svc.today_eat().isoformat()}, "/customers/1/payout")
        self.assertIn("Payout of TZS 2,000 recorded", r.get_data(as_text=True))
        r = self.post(m, "/payments/1/reverse", {"reason": ""}, "/sales/1")
        self.assertIn("reason", r.get_data(as_text=True).lower())
        r = self.post(m, "/customers/2/referrer", {"referrer_phone": "", "reason": "Wrong person entered"}, "/customers/2")
        self.assertIn("Referral link updated", r.get_data(as_text=True))
        for key in rpt.REPORTS:
            x = m.get(f"/reports/{key}/export/xlsx")
            self.assertEqual(x.status_code, 200)
            self.assertTrue(zipfile.is_zipfile(io.BytesIO(x.data)))
            p = m.get(f"/reports/{key}/export/pdf")
            self.assertTrue(p.data.startswith(b"%PDF"))
            self.assertEqual(m.get(f"/reports/{key}").status_code, 200)
        self.assertIn(b"Payout", m.get("/payouts").data)
        b = self.post(m, "/backup/download", {}, "/backup")
        self.assertTrue(b.data.startswith(b"SQLite format 3"))
        self.assertIn("report_exported", m.get("/audit").get_data(as_text=True))

    def test_security_headers_xss_and_injection(self):
        c, _ = self.login("stf")
        r = self.post(c, "/customers/new", {"name": "<script>alert(1)</script>", "phone": "0711111111"}, "/customers/new")
        html = r.get_data(as_text=True)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        h = c.get("/").headers
        self.assertIn("default-src 'self'", h["Content-Security-Policy"])
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertEqual(c.get("/customers?q=%27%20OR%201%3D1%20--").status_code, 200)
        self.assertEqual(c.get("/customers?q=1234%27%3BDROP%20TABLE%20customers%3B--").status_code, 200)
        self.assertEqual(c.get("/login?next=//evil.example").status_code, 302)  # already logged in
        c2 = self.app.test_client()
        r = c2.post("/login?next=//evil.example", data={"username": "stf", "password": PW, "csrf_token": self.token(c2, "/login")})
        self.assertNotIn("evil.example", r.headers["Location"])

    def test_pages_have_mobile_viewport_and_health_check(self):
        c, _ = self.login("mgr")
        self.assertIn('name="viewport" content="width=device-width, initial-scale=1"', c.get("/").get_data(as_text=True))
        self.assertEqual(self.app.test_client().get("/healthz").data, b"ok")
        self.assertEqual(c.get("/nonexistent").status_code, 404)


if __name__ == "__main__":
    unittest.main()
