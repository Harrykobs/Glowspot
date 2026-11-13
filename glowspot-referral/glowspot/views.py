"""Web routes."""
import os
import secrets
from functools import wraps

from flask import (Blueprint, abort, after_this_request, flash, g, jsonify, redirect, render_template,
                   request, session, url_for, Response)

from . import db as dbm
from . import reports as rpt
from . import services as svc
from .services import RuleError

bp = Blueprint("main", __name__)


def d():
    return dbm.get_db()


def login_required(f):
    @wraps(f)
    def wrapper(*a, **k):
        if not g.user:
            return redirect(url_for("main.login", next=request.full_path.rstrip("?")))
        if g.user["must_change_password"] and request.endpoint not in ("main.change_password", "main.logout"):
            return redirect(url_for("main.change_password"))
        return f(*a, **k)
    return wrapper


def manager_required(f):
    @wraps(f)
    @login_required
    def wrapper(*a, **k):
        if g.user["role"] != "manager":
            abort(403)
        return f(*a, **k)
    return wrapper


def safe_next(n):
    return n if n and n.startswith("/") and not n.startswith("//") and "\\" not in n else url_for("main.home")


# ------------------------------------------------------------------ auth
@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("main.home"))
    if request.method == "POST":
        try:
            u = svc.attempt_login(d(), request.form.get("username"), request.form.get("password"))
        except RuleError as e:
            flash(str(e), "error")
            return render_template("login.html", username=request.form.get("username", "")), 401
        session.clear()
        session["uid"] = u["id"]
        session["csrf"] = secrets.token_urlsafe(32)
        session.permanent = True
        return redirect(safe_next(request.args.get("next")))
    return render_template("login.html", username="")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    svc.audit(d(), g.user, "logout", "user", g.user["id"])
    d().commit()
    session.clear()
    return redirect(url_for("main.login"))


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    if request.method == "POST":
        try:
            svc.change_own_password(d(), g.user, request.form.get("current"), request.form.get("new"), request.form.get("confirm"))
            d().commit()
            flash("Password changed.", "ok")
            return redirect(url_for("main.home"))
        except RuleError as e:
            d().rollback()
            flash(str(e), "error")
    return render_template("change_password.html")


@bp.route("/healthz")
def healthz():
    d().execute("SELECT 1")
    return Response("ok", mimetype="text/plain")


# ------------------------------------------------------------------ home and customers
@bp.route("/")
@login_required
def home():
    db = d()
    mgr = g.user["role"] == "manager"
    q = ("SELECT x.id, x.item_name, x.service_date, x.amount_due, c.id cid, c.name, u.full_name by_name, t.paid "
         "FROM sales x JOIN customers c ON c.id=x.customer_id JOIN users u ON u.id=x.recorded_by "
         "JOIN sale_totals t ON t.sale_id=x.id WHERE x.status='active' ")
    recent = db.execute(q + ("" if mgr else "AND x.recorded_by=? ") + "ORDER BY x.id DESC LIMIT 8",
                        () if mgr else (g.user["id"],)).fetchall()
    return render_template("home.html", stats=svc.manager_stats(db) if mgr else None, recent=recent)


@bp.route("/customers")
@login_required
def customers():
    db, q = d(), request.args.get("q", "").strip()
    rows, total, page = [], 0, max(1, request.args.get("page", 1, type=int))
    if q:
        try:
            rows = svc.search_customers(db, q, g.user)
        except RuleError as e:
            flash(str(e), "error")
        if len(rows) == 1:
            return redirect(url_for("main.customer", cid=rows[0]["id"]))
    elif g.user["role"] == "manager":
        total = db.execute("SELECT COUNT(*) n FROM customers").fetchone()["n"]
        rows = db.execute("SELECT * FROM customers ORDER BY registered_at DESC, id DESC LIMIT 50 OFFSET ?", ((page - 1) * 50,)).fetchall()
    info = {r["id"]: {"status": svc.customer_status(db, r["id"]), "unpaid": svc.commission_summary(db, r["id"])["unpaid"]} for r in rows}
    return render_template("customers.html", q=q, rows=rows, info=info, total=total, page=page)


@bp.route("/customers/new", methods=["GET", "POST"])
@login_required
def customer_new():
    form = request.form if request.method == "POST" else request.args
    if request.method == "POST":
        try:
            cid = svc.register_customer(d(), g.user, form.get("name"), form.get("phone"), form.get("referrer_phone"))
            d().commit()
            flash("Customer registered.", "ok")
            return redirect(url_for("main.customer", cid=cid))
        except RuleError as e:
            d().rollback()
            flash(str(e), "error")
            dup = e.customer_id
            return render_template("customer_new.html", form=form, dup=dup), 400
    return render_template("customer_new.html", form=form, dup=None)


@bp.route("/customers/<int:cid>")
@login_required
def customer(cid):
    db = d()
    c = svc.get_customer(db, cid)
    if not c:
        abort(404)
    referrer = svc.get_customer(db, c["referrer_id"]) if c["referrer_id"] else None
    referred = db.execute(
        "SELECT c.id,c.name,c.phone,c.registered_at,(SELECT MIN(service_date) FROM qualifying_sales q WHERE q.customer_id=c.id) qualified_on"
        " FROM customers c WHERE c.referrer_id=? ORDER BY c.registered_at", (cid,)).fetchall()
    sales = db.execute("SELECT x.*, t.paid, u.full_name by_name FROM sales x JOIN sale_totals t ON t.sale_id=x.id "
                       "JOIN users u ON u.id=x.recorded_by WHERE x.customer_id=? ORDER BY x.service_date DESC, x.id DESC", (cid,)).fetchall()
    ledger = db.execute(
        "SELECT m.*, r.name referred_name, x.item_name FROM commissions m JOIN customers r ON r.id=m.referred_id "
        "JOIN sales x ON x.id=m.sale_id WHERE m.referrer_id=? ORDER BY m.earned_at DESC, m.id DESC", (cid,)).fetchall()
    payouts = db.execute("SELECT p.*, u.full_name by_name FROM payouts p JOIN users u ON u.id=p.recorded_by "
                         "WHERE p.referrer_id=? ORDER BY p.paid_on DESC, p.id DESC", (cid,)).fetchall()
    changes = db.execute("SELECT h.*, o.name old_name, n.name new_name, u.full_name by_name FROM referral_changes h "
                         "LEFT JOIN customers o ON o.id=h.old_referrer_id LEFT JOIN customers n ON n.id=h.new_referrer_id "
                         "JOIN users u ON u.id=h.changed_by WHERE h.customer_id=? ORDER BY h.id DESC", (cid,)).fetchall()
    return render_template("customer.html", c=c, referrer=referrer, referred=referred, sales=sales, ledger=ledger,
                           payouts=payouts, changes=changes, summary=svc.commission_summary(db, cid),
                           status=svc.customer_status(db, cid), quote=svc.skin_quote(db, cid),
                           qualifying=svc.qualifying_referral_count(db, cid))


@bp.route("/customers/<int:cid>/referrer", methods=["POST"])
@manager_required
def customer_referrer(cid):
    try:
        svc.change_referrer(d(), g.user, cid, request.form.get("referrer_phone"), request.form.get("reason"))
        d().commit()
        flash("Referral link updated and recorded.", "ok")
    except RuleError as e:
        d().rollback()
        flash(str(e), "error")
    return redirect(url_for("main.customer", cid=cid))


@bp.route("/api/referrer")
@login_required
def api_referrer():
    try:
        p = svc.normalize_phone(request.args.get("phone", ""))
    except RuleError as e:
        return jsonify(found=False, message=str(e))
    r = d().execute("SELECT name FROM customers WHERE phone=?", (p,)).fetchone()
    own = request.args.get("own", "")
    if own and _valid(own) and svc.normalize_phone(own) == p:
        return jsonify(found=False, message="A customer cannot refer themselves.")
    return jsonify(found=True, name=r["name"]) if r else jsonify(found=False, message="Not a registered customer yet.")


def _valid(raw):
    try:
        svc.normalize_phone(raw)
        return True
    except RuleError:
        return False


# ------------------------------------------------------------------ sales
@bp.route("/customers/<int:cid>/sales/new", methods=["GET", "POST"])
@login_required
def sale_new(cid):
    db = d()
    c = svc.get_customer(db, cid)
    if not c:
        abort(404)
    packages = db.execute("SELECT * FROM packages WHERE active=1 ORDER BY price").fetchall()
    form = request.form if request.method == "POST" else request.args
    if request.method == "POST":
        try:
            sale_id, awards = svc.create_sale(
                db, g.user, cid, form.get("kind"), package_id=form.get("package_id", type=int),
                discount_raw=form.get("discount"), paid_raw=form.get("paid"), method=form.get("method"), date_raw=form.get("date"))
            db.commit()
            flash("Sale recorded.", "ok")
            for a in awards:
                flash(f"{svc.format_phone(a['referrer_phone'])} {a['referrer_name']} earned TZS {a['amount']:,}. "
                      f"{a['reason']}. Unpaid balance now TZS {a['balance']:,}.", "reward")
            return redirect(url_for("main.sale", sid=sale_id))
        except RuleError as e:
            db.rollback()
            flash(str(e), "error")
    return render_template("sale_new.html", c=c, quote=svc.skin_quote(db, cid), packages=packages, form=form)


@bp.route("/sales/<int:sid>")
@login_required
def sale(sid):
    db = d()
    s = db.execute("SELECT x.*, u.full_name by_name FROM sales x JOIN users u ON u.id=x.recorded_by WHERE x.id=?", (sid,)).fetchone()
    if not s:
        abort(404)
    c = svc.get_customer(db, s["customer_id"])
    pays = db.execute("SELECT p.*, u.full_name by_name FROM payments p JOIN users u ON u.id=p.recorded_by WHERE p.sale_id=? ORDER BY p.id", (sid,)).fetchall()
    comms = db.execute("SELECT m.*, r.name referrer_name, r.phone referrer_phone FROM commissions m JOIN customers r ON r.id=m.referrer_id "
                       "WHERE m.sale_id=? ORDER BY m.id", (sid,)).fetchall()
    paid = svc.paid_total(db, sid)
    return render_template("sale.html", s=s, c=c, pays=pays, comms=comms, paid=paid, balance=s["amount_due"] - paid)


@bp.route("/sales/<int:sid>/payments", methods=["POST"])
@login_required
def sale_payment(sid):
    try:
        awards = svc.add_payment(d(), g.user, sid, request.form.get("amount"), request.form.get("method"), request.form.get("date"))
        d().commit()
        flash("Payment recorded.", "ok")
        for a in awards:
            flash(f"{svc.format_phone(a['referrer_phone'])} {a['referrer_name']} earned TZS {a['amount']:,}. "
                  f"{a['reason']}. Unpaid balance now TZS {a['balance']:,}.", "reward")
    except RuleError as e:
        d().rollback()
        flash(str(e), "error")
    return redirect(url_for("main.sale", sid=sid))


@bp.route("/payments/<int:pid>/reverse", methods=["POST"])
@manager_required
def payment_reverse(pid):
    row = d().execute("SELECT sale_id FROM payments WHERE id=?", (pid,)).fetchone()
    if not row:
        abort(404)
    try:
        svc.reverse_payment(d(), g.user, pid, request.form.get("reason"))
        d().commit()
        flash("Payment reversed. Related commission was reversed or adjusted.", "ok")
    except RuleError as e:
        d().rollback()
        flash(str(e), "error")
    return redirect(url_for("main.sale", sid=row["sale_id"]))


@bp.route("/sales/<int:sid>/void", methods=["POST"])
@manager_required
def sale_void(sid):
    try:
        svc.void_sale(d(), g.user, sid, request.form.get("reason"))
        d().commit()
        flash("Sale voided. All its payments and commissions were reversed.", "ok")
    except RuleError as e:
        d().rollback()
        flash(str(e), "error")
    return redirect(url_for("main.sale", sid=sid))


# ------------------------------------------------------------------ commissions and payouts
@bp.route("/commissions")
@manager_required
def commissions():
    db, a = d(), request.args
    where, p = ["1=1"], []
    status = a.get("status", "Pending")
    if status and status != "all":
        where.append("m.status=?"); p.append(status)
    if a.get("kind"):
        where.append("m.kind=?"); p.append(a["kind"])
    if a.get("q"):
        like = "%" + svc.like_escape(a["q"].strip()) + "%"
        digits = "".join(ch for ch in a["q"] if ch.isdigit())
        where.append("(r.name LIKE ? ESCAPE '\\' OR r.phone LIKE ? ESCAPE '\\')")
        p += [like, "%" + svc.like_escape(digits.lstrip("0") or "zzz") + "%"]
    if a.get("start"):
        where.append("date(m.earned_at,'+3 hours')>=?"); p.append(a["start"])
    if a.get("end"):
        where.append("date(m.earned_at,'+3 hours')<=?"); p.append(a["end"])
    rows = db.execute(
        "SELECT m.*, r.name referrer_name, r.phone referrer_phone, c.name referred_name, x.item_name FROM commissions m "
        "JOIN customers r ON r.id=m.referrer_id JOIN customers c ON c.id=m.referred_id JOIN sales x ON x.id=m.sale_id "
        "WHERE " + " AND ".join(where) + " ORDER BY m.earned_at DESC, m.id DESC LIMIT 300", p).fetchall()
    return render_template("commissions.html", rows=rows, status=status, a=a)


@bp.route("/commissions/approve", methods=["POST"])
@manager_required
def commissions_approve():
    try:
        n = svc.approve_commissions(d(), g.user, request.form.getlist("ids"))
        d().commit()
        flash(f"{n} commission(s) approved.", "ok")
    except RuleError as e:
        d().rollback()
        flash(str(e), "error")
    return redirect(request.form.get("back") if (request.form.get("back") or "").startswith("/commissions") else url_for("main.commissions"))


@bp.route("/customers/<int:cid>/payout", methods=["GET", "POST"])
@manager_required
def payout_new(cid):
    db = d()
    c = svc.get_customer(db, cid)
    if not c:
        abort(404)
    if request.method == "POST":
        try:
            pid, total = svc.record_payout(db, g.user, cid, request.form.getlist("ids"), request.form.get("method"),
                                           request.form.get("date"), request.form.get("note"))
            db.commit()
            flash(f"Payout of TZS {total:,} recorded.", "ok")
            return redirect(url_for("main.customer", cid=cid))
        except RuleError as e:
            db.rollback()
            flash(str(e), "error")
    rows = db.execute("SELECT m.*, r.name referred_name, x.item_name FROM commissions m JOIN customers r ON r.id=m.referred_id "
                      "JOIN sales x ON x.id=m.sale_id WHERE m.referrer_id=? AND m.status='Approved' ORDER BY m.earned_at", (cid,)).fetchall()
    return render_template("payout_new.html", c=c, rows=rows, summary=svc.commission_summary(db, cid))


@bp.route("/payouts")
@manager_required
def payouts():
    rows = d().execute("SELECT p.*, c.name, c.phone, u.full_name by_name FROM payouts p JOIN customers c ON c.id=p.referrer_id "
                       "JOIN users u ON u.id=p.recorded_by ORDER BY p.id DESC LIMIT 300").fetchall()
    return render_template("payouts.html", rows=rows)


# ------------------------------------------------------------------ administration
@bp.route("/staff", methods=["GET", "POST"])
@manager_required
def staff():
    db = d()
    if request.method == "POST":
        try:
            svc.create_user(db, g.user, request.form.get("username"), request.form.get("full_name"),
                            request.form.get("role"), request.form.get("password"))
            db.commit()
            flash("Staff account created. They must set their own password at first login.", "ok")
            return redirect(url_for("main.staff"))
        except RuleError as e:
            db.rollback()
            flash(str(e), "error")
    return render_template("staff.html", users=db.execute("SELECT * FROM users ORDER BY active DESC, role, full_name").fetchall())


@bp.route("/staff/<int:uid>/update", methods=["POST"])
@manager_required
def staff_update(uid):
    try:
        svc.update_user(d(), g.user, uid, request.form.get("full_name"), request.form.get("role"), request.form.get("active") == "1")
        d().commit()
        flash("Account updated.", "ok")
    except RuleError as e:
        d().rollback()
        flash(str(e), "error")
    return redirect(url_for("main.staff"))


@bp.route("/staff/<int:uid>/password", methods=["POST"])
@manager_required
def staff_password(uid):
    try:
        svc.reset_password(d(), g.user, uid, request.form.get("password"))
        d().commit()
        flash("Password reset. They must change it at next login.", "ok")
    except RuleError as e:
        d().rollback()
        flash(str(e), "error")
    return redirect(url_for("main.staff"))


@bp.route("/settings", methods=["GET", "POST"])
@manager_required
def settings():
    db = d()
    if request.method == "POST":
        try:
            svc.update_settings(db, g.user, request.form, request.form.get("reason"))
            db.commit()
            flash("Settings saved. New prices and rates apply to future transactions only.", "ok")
            return redirect(url_for("main.settings"))
        except RuleError as e:
            db.rollback()
            flash(str(e), "error")
    rows = db.execute("SELECT * FROM settings ORDER BY key").fetchall()
    hist = db.execute("SELECT h.*, s.label, u.full_name by_name FROM settings_history h JOIN settings s ON s.key=h.key "
                      "JOIN users u ON u.id=h.changed_by ORDER BY h.id DESC LIMIT 50").fetchall()
    return render_template("settings.html", rows=rows, hist=hist)


@bp.route("/packages", methods=["GET", "POST"])
@manager_required
def packages():
    db = d()
    if request.method == "POST":
        try:
            svc.save_package(db, g.user, request.form.get("id", type=int), request.form.get("name"), request.form.get("price"),
                             request.form.get("active") == "1" if request.form.get("id") else True)
            db.commit()
            flash("Package saved.", "ok")
            return redirect(url_for("main.packages"))
        except RuleError as e:
            db.rollback()
            flash(str(e), "error")
    return render_template("packages.html", rows=db.execute("SELECT * FROM packages ORDER BY active DESC, price").fetchall())


# ------------------------------------------------------------------ reports, audit, backup
@bp.route("/reports")
@manager_required
def reports_index():
    try:
        s, e = rpt.parse_range(request.args)
    except RuleError as ex:
        flash(str(ex), "error")
        s, e = rpt.parse_range({})
    return render_template("reports.html", reports=rpt.REPORTS, start=s, end=e)


@bp.route("/reports/<key>")
@manager_required
def report(key):
    try:
        rep = rpt.build(d(), key, request.args)
    except RuleError as e:
        flash(str(e), "error")
        return redirect(url_for("main.reports_index"))
    return render_template("report.html", rep=rep, group=request.args.get("group", "day"))


@bp.route("/reports/<key>/export/<fmt>")
@manager_required
def report_export(key, fmt):
    if fmt not in ("xlsx", "pdf"):
        abort(404)
    try:
        rep = rpt.build(d(), key, request.args)
    except RuleError as e:
        flash(str(e), "error")
        return redirect(url_for("main.reports_index"))
    svc.audit(d(), g.user, "report_exported", "report", None, f"{key} {fmt} {rep['start']}..{rep['end']}")
    d().commit()
    data = rpt.to_xlsx(rep) if fmt == "xlsx" else rpt.to_pdf(rep)
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if fmt == "xlsx" else "application/pdf"
    name = f"glowspot-{key}-{rep['start']}-to-{rep['end']}.{fmt}"
    return Response(data, mimetype=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@bp.route("/audit")
@manager_required
def audit_log():
    db, a = d(), request.args
    where, p = ["1=1"], []
    if a.get("action"):
        where.append("action=?"); p.append(a["action"])
    if a.get("q"):
        where.append("(username LIKE ? ESCAPE '\\' OR detail LIKE ? ESCAPE '\\' OR reason LIKE ? ESCAPE '\\')")
        p += ["%" + svc.like_escape(a["q"]) + "%"] * 3
    page = max(1, a.get("page", 1, type=int))
    rows = db.execute("SELECT * FROM audit WHERE " + " AND ".join(where) + " ORDER BY id DESC LIMIT 100 OFFSET ?", (*p, (page - 1) * 100)).fetchall()
    actions = [r["action"] for r in db.execute("SELECT DISTINCT action FROM audit ORDER BY action").fetchall()]
    return render_template("audit.html", rows=rows, actions=actions, a=a, page=page)


@bp.route("/backup")
@manager_required
def backup():
    return render_template("backup.html")


@bp.route("/backup/download", methods=["POST"])
@manager_required
def backup_download():
    # PostgreSQL backups are managed through Supabase or pg_dump.
    flash("Use the Supabase dashboard database backup/export tools for PostgreSQL backups.", "error")
    return redirect(url_for("main.backup"))
