"""Business rules for the Glowspot referral system.

Everything that changes money, referrals or commissions lives here so the rules
are in one place and can be tested without the web layer.
"""
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP

from werkzeug.security import check_password_hash, generate_password_hash

EAT = timezone(timedelta(hours=3))  # East Africa Time (no daylight saving)
METHODS = ["Cash", "Mobile money", "Bank transfer", "Card"]
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 10
_DUMMY_HASH = generate_password_hash("dummy-password-for-timing")


class RuleError(Exception):
    """A business-rule or validation problem that should be shown to the user."""

    def __init__(self, message, customer_id=None):
        super().__init__(message)
        self.customer_id = customer_id


def now_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def today_eat():
    return datetime.now(EAT).date()


# ---------------------------------------------------------------- helpers
def audit(db, user, action, entity=None, entity_id=None, detail=None, reason=None):
    db.execute(
        "INSERT INTO audit(user_id,username,action,entity,entity_id,detail,reason,created_at)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (user["id"] if user else None, user["username"] if user else None, action, entity, entity_id,
         detail, reason, now_utc()),
    )


def require_reason(reason):
    reason = " ".join((reason or "").split())
    if len(reason) < 5:
        raise RuleError("A reason (at least 5 characters) is required.")
    if len(reason) > 300:
        raise RuleError("The reason is too long (maximum 300 characters).")
    return reason


def parse_amount(value, field="Amount", allow_zero=False):
    s = re.sub(r"[,\s]", "", str(value if value is not None else ""))
    if s == "":
        if allow_zero:
            return 0
        raise RuleError(f"{field} is required.")
    if not re.fullmatch(r"\d{1,9}", s):
        raise RuleError(f"{field} must be a whole number of TZS.")
    n = int(s)
    if n == 0 and not allow_zero:
        raise RuleError(f"{field} must be more than zero.")
    return n


def parse_date(value, user):
    try:
        d = date.fromisoformat((value or "").strip())
    except ValueError:
        raise RuleError("Enter a valid date.")
    t = today_eat()
    if d > t:
        raise RuleError("The date cannot be in the future.")
    if user["role"] != "manager" and (t - d).days > 7:
        raise RuleError("Staff can record dates up to 7 days back. Ask a manager to record older dates.")
    return d.isoformat()


def normalize_phone(raw):
    """Return the canonical form +255XXXXXXXXX or raise RuleError."""
    s = re.sub(r"[\s\-\(\)\.]", "", raw or "")
    if s.startswith("+"):
        s = s[1:]
    elif s.startswith("00"):
        s = s[2:]
    if not s.isdigit():
        raise RuleError("Enter a valid Tanzanian mobile number, for example 0786 189 844.")
    if s.startswith("255") and len(s) == 12:
        digits = s[3:]
    elif s.startswith("0") and len(s) == 10:
        digits = s[1:]
    elif len(s) == 9:
        digits = s
    else:
        raise RuleError("Enter a valid Tanzanian mobile number, for example 0786 189 844.")
    if digits[0] not in "67":
        raise RuleError("Tanzanian mobile numbers start with 06 or 07 (for example 0786 189 844).")
    return "+255" + digits


def format_phone(p):
    if not p or not p.startswith("+255") or len(p) != 13:
        return p or ""
    d = p[4:]
    return f"0{d[:3]} {d[3:6]} {d[6:]}"


def like_escape(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_setting(db, key):
    return db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()["value"]


def s_int(db, key):
    return int(Decimal(get_setting(db, key)))


def s_dec(db, key):
    return Decimal(get_setting(db, key))


def validate_password(pw, username=""):
    if len(pw or "") < 8:
        raise RuleError("Password must be at least 8 characters.")
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        raise RuleError("Password must contain both letters and numbers.")
    if username and pw.lower() == username.lower():
        raise RuleError("Password must not be the same as the username.")


# ---------------------------------------------------------------- users
def attempt_login(db, username, password):
    u = db.execute("SELECT * FROM users WHERE username=?", ((username or "").strip(),)).fetchone()
    if u and u["locked_until"] and u["locked_until"] > now_utc():
        audit(db, None, "login_blocked", "user", u["id"], f"username={u['username']}")
        db.commit()
        raise RuleError("Too many failed attempts. Try again in a few minutes.")
    ok = check_password_hash(u["password_hash"] if u else _DUMMY_HASH, password or "")
    if not u or not u["active"] or not ok:
        if u:
            fails = u["failed_logins"] + 1
            locked = None
            if fails >= MAX_FAILED_LOGINS:
                locked = (datetime.now(timezone.utc) + timedelta(minutes=LOCK_MINUTES)).strftime("%Y-%m-%d %H:%M:%S")
                fails = 0
            db.execute("UPDATE users SET failed_logins=?, locked_until=? WHERE id=?", (fails, locked, u["id"]))
        audit(db, None, "login_failed", "user", u["id"] if u else None, f"username={(username or '')[:40]}")
        db.commit()
        raise RuleError("Invalid username or password.")
    db.execute("UPDATE users SET failed_logins=0, locked_until=NULL, last_login=? WHERE id=?", (now_utc(), u["id"]))
    audit(db, u, "login", "user", u["id"])
    db.commit()
    return u


def create_user(db, actor, username, full_name, role, password):
    username = (username or "").strip()
    full_name = " ".join((full_name or "").split())
    if not re.fullmatch(r"[A-Za-z0-9._-]{3,32}", username):
        raise RuleError("Username must be 3-32 characters: letters, numbers, dot, dash or underscore.")
    if not (2 <= len(full_name) <= 80):
        raise RuleError("Enter the staff member's full name.")
    if role not in ("staff", "manager"):
        raise RuleError("Choose a role.")
    validate_password(password, username)
    try:
        cur = db.execute(
            "INSERT INTO users(username,full_name,password_hash,role,active,must_change_password,created_at)"
            " VALUES(?,?,?,?,1,1,?)",
            (username, full_name, generate_password_hash(password), role, now_utc()))
    except sqlite3.IntegrityError:
        raise RuleError("That username is already taken.")
    audit(db, actor, "user_created", "user", cur.lastrowid, f"{username} ({role})")
    return cur.lastrowid


def update_user(db, actor, uid, full_name, role, active):
    u = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        raise RuleError("Staff account not found.")
    full_name = " ".join((full_name or "").split())
    if not (2 <= len(full_name) <= 80):
        raise RuleError("Enter the staff member's full name.")
    if role not in ("staff", "manager"):
        raise RuleError("Choose a role.")
    active = 1 if active else 0
    if uid == actor["id"] and (not active or role != "manager"):
        raise RuleError("You cannot disable or demote your own account.")
    if u["role"] == "manager" and u["active"] and (role != "manager" or not active):
        others = db.execute("SELECT COUNT(*) c FROM users WHERE role='manager' AND active=1 AND id<>?", (uid,)).fetchone()["c"]
        if others == 0:
            raise RuleError("At least one active manager is required.")
    db.execute("UPDATE users SET full_name=?, role=?, active=? WHERE id=?", (full_name, role, active, uid))
    audit(db, actor, "user_updated", "user", uid, f"{u['username']}: role={role}, active={active}")


def reset_password(db, actor, uid, new_password):
    u = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not u:
        raise RuleError("Staff account not found.")
    validate_password(new_password, u["username"])
    db.execute("UPDATE users SET password_hash=?, must_change_password=1, failed_logins=0, locked_until=NULL WHERE id=?",
               (generate_password_hash(new_password), uid))
    audit(db, actor, "password_reset", "user", uid, u["username"])


def change_own_password(db, user, current, new, confirm):
    row = db.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
    if not check_password_hash(row["password_hash"], current or ""):
        raise RuleError("Your current password is not correct.")
    if new != confirm:
        raise RuleError("The new passwords do not match.")
    if check_password_hash(row["password_hash"], new or ""):
        raise RuleError("Choose a password different from the current one.")
    validate_password(new, row["username"])
    db.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?", (generate_password_hash(new), user["id"]))
    audit(db, user, "password_changed", "user", user["id"])


# ---------------------------------------------------------------- customers
def get_customer(db, cid):
    return db.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()


def register_customer(db, user, name, phone_raw, referrer_phone_raw="", is_demo=0):
    name = " ".join((name or "").split())
    if not (2 <= len(name) <= 80):
        raise RuleError("Enter the customer's full name (2-80 characters).")
    phone = normalize_phone(phone_raw)
    existing = db.execute("SELECT id,name FROM customers WHERE phone=?", (phone,)).fetchone()
    if existing:
        raise RuleError(f"This phone number already belongs to {existing['name']}.", customer_id=existing["id"])
    referrer_id = None
    rraw = (referrer_phone_raw or "").strip()
    if rraw:
        try:
            rphone = normalize_phone(rraw)
        except RuleError as e:
            raise RuleError("Referrer's phone number: " + str(e))
        if rphone == phone:
            raise RuleError("A customer cannot refer themselves.")
        ref = db.execute("SELECT id FROM customers WHERE phone=?", (rphone,)).fetchone()
        if not ref:
            raise RuleError("The referrer's phone number is not a registered customer. Register the referrer first.")
        referrer_id = ref["id"]
    try:
        cur = db.execute(
            "INSERT INTO customers(name,phone,referrer_id,registered_at,registered_by,is_demo) VALUES(?,?,?,?,?,?)",
            (name, phone, referrer_id, now_utc(), user["id"], 1 if is_demo else 0))
    except sqlite3.IntegrityError:
        ex = db.execute("SELECT id,name FROM customers WHERE phone=?", (phone,)).fetchone()
        raise RuleError("This phone number already belongs to another customer.", customer_id=ex["id"] if ex else None)
    audit(db, user, "customer_registered", "customer", cur.lastrowid,
          f"{name} {phone}" + (f", referrer #{referrer_id}" if referrer_id else ""))
    return cur.lastrowid


def search_customers(db, q, user, limit=15):
    q = (q or "").strip()
    if not q:
        return []
    digits = re.sub(r"\D", "", q)
    has_letters = bool(re.search(r"[A-Za-z]", q))
    rows = []
    if digits and not has_letters:
        if len(digits) < 4:
            raise RuleError("Type at least 4 digits of the phone number.")
        if digits.startswith("255"):
            digits = digits[3:]
        elif digits.startswith("0"):
            digits = digits[1:]
        rows = db.execute("SELECT * FROM customers WHERE phone LIKE ? ESCAPE '\\' ORDER BY name LIMIT ?",
                          ("%" + like_escape(digits) + "%", limit)).fetchall()
    elif user["role"] == "manager":
        rows = db.execute("SELECT * FROM customers WHERE name LIKE ? ESCAPE '\\' COLLATE NOCASE ORDER BY name LIMIT ?",
                          ("%" + like_escape(q) + "%", limit)).fetchall()
    else:
        raise RuleError("Search by phone number (at least 4 digits).")
    return rows


def qualifying_referral_count(db, customer_id):
    return db.execute(
        "SELECT COUNT(DISTINCT c.id) n FROM customers c JOIN qualifying_sales q ON q.customer_id=c.id"
        " WHERE c.referrer_id=?", (customer_id,)).fetchone()["n"]


def customer_status(db, customer_id):
    r = db.execute("SELECT 1 FROM sale_totals WHERE customer_id=? AND status='active' AND paid>0", (customer_id,)).fetchone()
    return "returning" if r else "new"


def change_referrer(db, user, customer_id, new_phone_raw, reason):
    """Manager-only: set, replace or clear a customer's referrer. Needs a reason."""
    if user["role"] != "manager":
        raise RuleError("Only a manager can change a referral link.")
    reason = require_reason(reason)
    cust = get_customer(db, customer_id)
    if not cust:
        raise RuleError("Customer not found.")
    new_id = None
    if (new_phone_raw or "").strip():
        phone = normalize_phone(new_phone_raw)
        ref = db.execute("SELECT id FROM customers WHERE phone=?", (phone,)).fetchone()
        if not ref:
            raise RuleError("That referrer's phone number is not a registered customer.")
        new_id = ref["id"]
        if new_id == customer_id:
            raise RuleError("A customer cannot refer themselves.")
        walker, seen = db.execute("SELECT referrer_id FROM customers WHERE id=?", (new_id,)).fetchone()["referrer_id"], set()
        while walker is not None and walker not in seen:
            if walker == customer_id:
                raise RuleError("That would create a circular referral (the new referrer was referred by this customer).")
            seen.add(walker)
            walker = db.execute("SELECT referrer_id FROM customers WHERE id=?", (walker,)).fetchone()["referrer_id"]
    if new_id == cust["referrer_id"]:
        raise RuleError("That is already this customer's referrer.")
    db.execute("UPDATE customers SET referrer_id=? WHERE id=?", (new_id, customer_id))
    db.execute("INSERT INTO referral_changes(customer_id,old_referrer_id,new_referrer_id,reason,changed_by,changed_at)"
               " VALUES(?,?,?,?,?,?)", (customer_id, cust["referrer_id"], new_id, reason, user["id"], now_utc()))
    audit(db, user, "referrer_changed", "customer", customer_id, f"{cust['referrer_id']} -> {new_id}", reason)


# ---------------------------------------------------------------- pricing
def skin_quote(db, customer_id):
    first = s_int(db, "first_skin_price")
    ret = s_int(db, "returning_skin_price")
    need = s_int(db, "min_referrals_for_discount")
    prior = db.execute("SELECT id FROM sales WHERE customer_id=? AND kind='skin' AND status='active'", (customer_id,)).fetchone()
    open_sale = db.execute("SELECT sale_id FROM sale_totals WHERE customer_id=? AND kind='skin' AND status='active'"
                           " AND paid<amount_due", (customer_id,)).fetchone()
    refs = qualifying_referral_count(db, customer_id)
    q = {"first_price": first, "returning_price": ret, "needed": need, "referrals": refs,
         "remaining": max(0, need - refs), "is_first": prior is None,
         "open_sale_id": open_sale["sale_id"] if open_sale else None}
    if prior is None:
        q.update(price=first, qualifies=False, label="First skin analysis")
    elif refs >= need:
        q.update(price=ret, qualifies=True, label="Returning customer price")
    else:
        q.update(price=first, qualifies=False, label="Standard price")
    return q


# ---------------------------------------------------------------- sales and payments
def paid_total(db, sale_id):
    return db.execute("SELECT COALESCE(SUM(amount),0) s FROM payments WHERE sale_id=? AND status='confirmed'",
                      (sale_id,)).fetchone()["s"]


def commission_summary(db, customer_id):
    r = db.execute(
        "SELECT COALESCE(SUM(CASE WHEN status IN ('Pending','Approved','Paid') THEN amount END),0) earned,"
        " COALESCE(SUM(CASE WHEN status='Paid' THEN amount END),0) paid,"
        " COALESCE(SUM(CASE WHEN status='Pending' THEN amount END),0) pending,"
        " COALESCE(SUM(CASE WHEN status='Approved' THEN amount END),0) approved"
        " FROM commissions WHERE referrer_id=?", (customer_id,)).fetchone()
    return {"earned": r["earned"], "paid": r["paid"], "unpaid": r["earned"] - r["paid"],
            "pending": r["pending"], "approved": r["approved"]}


def create_sale(db, user, customer_id, kind, package_id=None, discount_raw="0", paid_raw="0", method=None, date_raw=None):
    cust = get_customer(db, customer_id)
    if not cust:
        raise RuleError("Customer not found.")
    service_date = parse_date(date_raw or today_eat().isoformat(), user)
    is_first, note = 0, None
    if kind == "skin":
        q = skin_quote(db, customer_id)
        if q["open_sale_id"]:
            raise RuleError("This customer already has an unpaid skin analysis. Record the payment on it, or void it, first.")
        original, discount, item = q["price"], 0, "Skin analysis"
        is_first, note = (1 if q["is_first"] else 0), q["label"]
    elif kind == "package":
        pkg = db.execute("SELECT * FROM packages WHERE id=? AND active=1", (package_id or 0,)).fetchone()
        if not pkg:
            raise RuleError("Choose a package.")
        original, item = pkg["price"], pkg["name"]
        discount = parse_amount(discount_raw, "Discount", allow_zero=True)
        if discount >= original:
            raise RuleError("The discount must be less than the package price.")
    else:
        raise RuleError("Choose skin analysis or package.")
    due = original - discount
    paid = parse_amount(paid_raw, "Amount paid", allow_zero=True)
    if paid > due:
        raise RuleError("Amount paid is more than the amount due.")
    if paid > 0 and method not in METHODS:
        raise RuleError("Choose a payment method.")
    cur = db.execute(
        "INSERT INTO sales(customer_id,kind,item_name,original_price,discount,amount_due,is_first_skin,price_note,"
        "service_date,recorded_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (customer_id, kind, item, original, discount, due, is_first, note, service_date, user["id"], now_utc()))
    sale_id = cur.lastrowid
    audit(db, user, "sale_recorded", "sale", sale_id,
          f"{item} for customer #{customer_id}: price {original}, discount {discount}, due {due}")
    awards = _record_payment(db, user, sale_id, paid, method, service_date) if paid > 0 else []
    return sale_id, awards


def add_payment(db, user, sale_id, amount_raw, method, date_raw):
    sale = db.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    if not sale:
        raise RuleError("Sale not found.")
    if sale["status"] != "active":
        raise RuleError("This sale has been voided.")
    amount = parse_amount(amount_raw, "Amount paid")
    if method not in METHODS:
        raise RuleError("Choose a payment method.")
    paid_on = parse_date(date_raw or today_eat().isoformat(), user)
    balance = sale["amount_due"] - paid_total(db, sale_id)
    if amount > balance:
        raise RuleError(f"Amount is more than the outstanding balance (TZS {balance:,}).")
    return _record_payment(db, user, sale_id, amount, method, paid_on)


def _record_payment(db, user, sale_id, amount, method, paid_on):
    sale = db.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    before = paid_total(db, sale_id)
    cur = db.execute("INSERT INTO payments(sale_id,amount,method,paid_on,recorded_by,created_at) VALUES(?,?,?,?,?,?)",
                     (sale_id, amount, method, paid_on, user["id"], now_utc()))
    audit(db, user, "payment_recorded", "payment", cur.lastrowid, f"sale #{sale_id}: TZS {amount} by {method}")
    return _award(db, sale, cur.lastrowid, amount, before + amount)


def _award(db, sale, payment_id, amount, paid_after):
    """Create referral reward / package commission after a CONFIRMED payment."""
    cust = get_customer(db, sale["customer_id"])
    if not cust["referrer_id"]:
        return []
    new = []  # (kind, base, rate_label, amount, reason)
    if sale["kind"] == "package":
        rate = s_dec(db, "package_commission_percent")
        comm = int((Decimal(amount) * rate / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if comm > 0:
            new.append(("package_commission", amount, f"{rate.normalize():f}%", comm,
                        f"{rate.normalize():f}% of TZS {amount:,} paid by {cust['name']} for {sale['item_name']}"))
    elif sale["kind"] == "skin" and sale["is_first_skin"] and paid_after >= sale["amount_due"]:
        exists = db.execute("SELECT 1 FROM commissions WHERE referred_id=? AND kind='skin_reward' AND reversed_at IS NULL",
                            (cust["id"],)).fetchone()
        reward = s_int(db, "referral_reward")
        if not exists and reward > 0:
            new.append(("skin_reward", sale["amount_due"], "Fixed", reward,
                        f"Referral reward: {cust['name']} paid TZS {sale['amount_due']:,} for their first skin analysis"))
    out = []
    for kind, base, label, comm, reason in new:
        cur = db.execute(
            "INSERT INTO commissions(referrer_id,referred_id,sale_id,payment_id,kind,base_amount,rate_label,amount,"
            "earned_at,status) VALUES(?,?,?,?,?,?,?,?,?,'Pending')",
            (cust["referrer_id"], cust["id"], sale["id"], payment_id, kind, base, label, comm, now_utc()))
        ref = get_customer(db, cust["referrer_id"])
        audit(db, None, "commission_created", "commission", cur.lastrowid, f"TZS {comm} to {ref['name']}: {reason}")
        out.append({"id": cur.lastrowid, "kind": kind, "amount": comm, "reason": reason,
                    "referrer_id": ref["id"], "referrer_name": ref["name"], "referrer_phone": ref["phone"],
                    "balance": commission_summary(db, ref["id"])["unpaid"]})
    return out


def _reverse_commission(db, user, c, reason):
    ts = now_utc()
    if c["status"] in ("Pending", "Approved"):
        db.execute("UPDATE commissions SET status='Reversed', reversed_at=?, reversed_by=?, reversal_reason=? WHERE id=?",
                   (ts, user["id"], reason, c["id"]))
    elif c["status"] == "Paid":
        # Already paid out: keep the history, and add a negative adjustment the manager can net off.
        db.execute("UPDATE commissions SET reversed_at=?, reversed_by=?, reversal_reason=? WHERE id=?",
                   (ts, user["id"], reason, c["id"]))
        db.execute(
            "INSERT INTO commissions(referrer_id,referred_id,sale_id,payment_id,kind,base_amount,rate_label,amount,"
            "earned_at,status,reverses_id,reversal_reason) VALUES(?,?,?,?,'adjustment',?,?,?,?,'Approved',?,?)",
            (c["referrer_id"], c["referred_id"], c["sale_id"], c["payment_id"], c["base_amount"],
             f"Reversal of #{c['id']}", -c["amount"], ts, c["id"], reason))
    audit(db, user, "commission_reversed", "commission", c["id"], f"TZS {c['amount']} ({c['status']})", reason)


def reverse_payment(db, user, payment_id, reason, _skip_check=False):
    if user["role"] != "manager":
        raise RuleError("Only a manager can reverse a payment.")
    reason = require_reason(reason)
    pay = db.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
    if not pay or pay["status"] != "confirmed":
        raise RuleError("This payment cannot be reversed (not found or already reversed).")
    db.execute("UPDATE payments SET status='reversed', reversed_reason=?, reversed_by=?, reversed_at=? WHERE id=?",
               (reason, user["id"], now_utc(), payment_id))
    audit(db, user, "payment_reversed", "payment", payment_id, f"sale #{pay['sale_id']}: TZS {pay['amount']}", reason)
    sale = db.execute("SELECT * FROM sales WHERE id=?", (pay["sale_id"],)).fetchone()
    for c in db.execute("SELECT * FROM commissions WHERE payment_id=? AND kind='package_commission' AND reversed_at IS NULL",
                        (payment_id,)).fetchall():
        _reverse_commission(db, user, c, reason)
    if sale["kind"] == "skin" and paid_total(db, sale["id"]) < sale["amount_due"]:
        for c in db.execute("SELECT * FROM commissions WHERE sale_id=? AND kind='skin_reward' AND reversed_at IS NULL",
                            (sale["id"],)).fetchall():
            _reverse_commission(db, user, c, reason)


def void_sale(db, user, sale_id, reason):
    if user["role"] != "manager":
        raise RuleError("Only a manager can void a sale.")
    reason = require_reason(reason)
    sale = db.execute("SELECT * FROM sales WHERE id=?", (sale_id,)).fetchone()
    if not sale or sale["status"] != "active":
        raise RuleError("This sale cannot be voided (not found or already voided).")
    for p in db.execute("SELECT id FROM payments WHERE sale_id=? AND status='confirmed'", (sale_id,)).fetchall():
        reverse_payment(db, user, p["id"], f"Sale voided: {reason}")
    db.execute("UPDATE sales SET status='voided', void_reason=?, voided_by=?, voided_at=? WHERE id=?",
               (reason, user["id"], now_utc(), sale_id))
    audit(db, user, "sale_voided", "sale", sale_id, sale["item_name"], reason)


# ---------------------------------------------------------------- commissions and payouts
def approve_commissions(db, user, ids):
    if user["role"] != "manager":
        raise RuleError("Only a manager can approve commissions.")
    ids = [int(i) for i in ids if str(i).isdigit()]
    if not ids:
        raise RuleError("Select at least one commission.")
    ts, n = now_utc(), 0
    for i in ids:
        cur = db.execute("UPDATE commissions SET status='Approved', approved_by=?, approved_at=? WHERE id=? AND status='Pending'",
                         (user["id"], ts, i))
        n += cur.rowcount
    if n == 0:
        raise RuleError("None of the selected commissions are waiting for approval.")
    audit(db, user, "commissions_approved", "commission", None, f"{n} approved: {ids}")
    return n


def record_payout(db, user, referrer_id, ids, method, date_raw, note=""):
    if user["role"] != "manager":
        raise RuleError("Only a manager can record a payout.")
    if method not in METHODS:
        raise RuleError("Choose a payment method.")
    paid_on = parse_date(date_raw or today_eat().isoformat(), user)
    ids = sorted({int(i) for i in ids if str(i).isdigit()})
    if not ids:
        raise RuleError("Select the approved commissions being paid.")
    marks = ",".join("?" * len(ids))
    rows = db.execute(f"SELECT * FROM commissions WHERE id IN ({marks}) AND referrer_id=? AND status='Approved'",
                      (*ids, referrer_id)).fetchall()
    if len(rows) != len(ids):
        raise RuleError("Some selected commissions are not approved, or belong to another customer.")
    total = sum(r["amount"] for r in rows)
    if total <= 0:
        raise RuleError("The net payout must be more than zero.")
    cur = db.execute("INSERT INTO payouts(referrer_id,amount,method,paid_on,recorded_by,created_at,note) VALUES(?,?,?,?,?,?,?)",
                     (referrer_id, total, method, paid_on, user["id"], now_utc(), (note or "").strip()[:200]))
    db.execute(f"UPDATE commissions SET status='Paid', payout_id=? WHERE id IN ({marks})", (cur.lastrowid, *ids))
    audit(db, user, "payout_recorded", "payout", cur.lastrowid, f"TZS {total} to customer #{referrer_id} via {method}")
    return cur.lastrowid, total


# ---------------------------------------------------------------- settings and packages
_SETTING_RULES = {
    "first_skin_price": ("int", 1), "returning_skin_price": ("int", 1), "referral_reward": ("int", 0),
    "package_commission_percent": ("pct", 0), "min_referrals_for_discount": ("int", 1),
}


def update_settings(db, user, values, reason):
    if user["role"] != "manager":
        raise RuleError("Only a manager can change prices and commission settings.")
    reason = require_reason(reason)
    changed = 0
    for key, (kind, minimum) in _SETTING_RULES.items():
        if key not in values:
            continue
        raw = str(values[key]).strip().replace(",", "")
        try:
            if kind == "pct":
                d = Decimal(raw)
                if d < 0 or d > 100:
                    raise ValueError
                new = f"{d.normalize():f}"
            else:
                if not re.fullmatch(r"\d{1,9}", raw) or int(raw) < minimum:
                    raise ValueError
                new = str(int(raw))
        except (ValueError, ArithmeticError):
            raise RuleError("Check the value for: " + db.execute("SELECT label FROM settings WHERE key=?", (key,)).fetchone()["label"])
        old = get_setting(db, key)
        if Decimal(old) != Decimal(new):
            db.execute("UPDATE settings SET value=? WHERE key=?", (new, key))
            db.execute("INSERT INTO settings_history(key,old_value,new_value,reason,changed_by,changed_at) VALUES(?,?,?,?,?,?)",
                       (key, old, new, reason, user["id"], now_utc()))
            audit(db, user, "setting_changed", "setting", None, f"{key}: {old} -> {new}", reason)
            changed += 1
    if not changed:
        raise RuleError("Nothing was changed.")
    return changed


def save_package(db, user, package_id, name, price_raw, active):
    if user["role"] != "manager":
        raise RuleError("Only a manager can manage packages.")
    name = " ".join((name or "").split())
    if not (2 <= len(name) <= 80):
        raise RuleError("Enter the package name.")
    price = parse_amount(price_raw, "Package price")
    try:
        if package_id:
            old = db.execute("SELECT * FROM packages WHERE id=?", (package_id,)).fetchone()
            if not old:
                raise RuleError("Package not found.")
            db.execute("UPDATE packages SET name=?, price=?, active=? WHERE id=?", (name, price, 1 if active else 0, package_id))
            audit(db, user, "package_updated", "package", package_id,
                  f"{old['name']} {old['price']} -> {name} {price}, active={1 if active else 0}")
        else:
            cur = db.execute("INSERT INTO packages(name,price,active) VALUES(?,?,1)", (name, price))
            audit(db, user, "package_created", "package", cur.lastrowid, f"{name} {price}")
    except sqlite3.IntegrityError:
        raise RuleError("A package with that name already exists.")


# ---------------------------------------------------------------- dashboards
def manager_stats(db):
    today = today_eat().isoformat()
    month = today[:7]
    c = db.execute("SELECT COALESCE(SUM(p.amount),0) s FROM payments p JOIN sales s ON s.id=p.sale_id"
                   " WHERE p.status='confirmed' AND s.status='active' AND p.paid_on=?", (today,)).fetchone()["s"]
    m = db.execute("SELECT COALESCE(SUM(p.amount),0) s FROM payments p JOIN sales s ON s.id=p.sale_id"
                   " WHERE p.status='confirmed' AND s.status='active' AND substr(p.paid_on,1,7)=?", (month,)).fetchone()["s"]
    pend = db.execute("SELECT COUNT(*) n, COALESCE(SUM(amount),0) s FROM commissions WHERE status='Pending'").fetchone()
    appr = db.execute("SELECT COALESCE(SUM(amount),0) s FROM commissions WHERE status='Approved'").fetchone()["s"]
    cust = db.execute("SELECT COUNT(*) n FROM customers").fetchone()["n"]
    return {"today": c, "month": m, "pending_n": pend["n"], "pending_amt": pend["s"], "approved_amt": appr, "customers": cust}
