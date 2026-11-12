"""Reports (HTML view + Excel/PDF export). Dates are inclusive, East Africa Time."""
import io
from datetime import date, timedelta
from xml.sax.saxutils import escape

from .services import RuleError, format_phone, today_eat

REG_DATE = "date(c.registered_at,'+3 hours')"


def parse_range(args):
    t = today_eat()
    try:
        start = date.fromisoformat(args.get("start") or t.replace(day=1).isoformat())
        end = date.fromisoformat(args.get("end") or t.isoformat())
    except ValueError:
        raise RuleError("Enter valid dates.")
    if start > end:
        raise RuleError("The start date must be on or before the end date.")
    return start.isoformat(), end.isoformat()


def _rep(key, title, columns, rows, summary=None, money=()):
    return {"key": key, "title": title, "columns": columns, "rows": rows, "summary": summary or [], "money": set(money)}


def customers_report(db, s, e, **_):
    paid_sub = ("COALESCE((SELECT SUM(p.amount) FROM payments p JOIN sales x ON x.id=p.sale_id WHERE x.customer_id=c.id"
                " AND p.status='confirmed' AND x.status='active' AND p.paid_on BETWEEN ? AND ?),0)")
    rows = []
    new = db.execute(
        f"SELECT c.name,c.phone,{REG_DATE} reg,r.name rname,{paid_sub} paid FROM customers c "
        f"LEFT JOIN customers r ON r.id=c.referrer_id WHERE {REG_DATE} BETWEEN ? AND ? ORDER BY c.registered_at",
        (s, e, s, e)).fetchall()
    for r in new:
        rows.append(["New", r["name"], format_phone(r["phone"]), r["reg"], r["rname"] or "-", r["paid"]])
    ret = db.execute(
        f"SELECT c.name,c.phone,{REG_DATE} reg,r.name rname,{paid_sub} paid FROM customers c "
        "LEFT JOIN customers r ON r.id=c.referrer_id "
        "JOIN (SELECT x.customer_id cid, MIN(p.paid_on) fp FROM payments p JOIN sales x ON x.id=p.sale_id "
        "      WHERE p.status='confirmed' AND x.status='active' GROUP BY x.customer_id) f ON f.cid=c.id "
        "WHERE f.fp < ? AND EXISTS (SELECT 1 FROM payments p JOIN sales x ON x.id=p.sale_id WHERE x.customer_id=c.id "
        "  AND p.status='confirmed' AND x.status='active' AND p.paid_on BETWEEN ? AND ?) ORDER BY c.name",
        (s, e, s, s, e)).fetchall()
    for r in ret:
        rows.append(["Returning", r["name"], format_phone(r["phone"]), r["reg"], r["rname"] or "-", r["paid"]])
    return _rep("customers", "New and returning customers",
                ["Type", "Customer", "Phone", "Registered", "Referred by", "Paid in period (TZS)"], rows,
                [("New customers", len(new)), ("Returning customers", len(ret))], money=[5])


def referrals_report(db, s, e, **_):
    q = db.execute(
        f"SELECT r.name rn,r.phone rp,c.name,c.phone,{REG_DATE} reg,"
        "CASE WHEN EXISTS(SELECT 1 FROM qualifying_sales q WHERE q.customer_id=c.id) THEN 'Yes' ELSE 'No' END ok "
        f"FROM customers c JOIN customers r ON r.id=c.referrer_id WHERE {REG_DATE} BETWEEN ? AND ? "
        "ORDER BY r.name, c.registered_at", (s, e)).fetchall()
    rows = [[r["rn"], format_phone(r["rp"]), r["name"], format_phone(r["phone"]), r["reg"], r["ok"]] for r in q]
    return _rep("referrals", "Customers referred by each referrer",
                ["Referrer", "Referrer phone", "Referred customer", "Phone", "Registered", "Qualifying paid visit"], rows,
                [("Referred customers", len(q)), ("Referrers", len({r["rp"] for r in q})),
                 ("With qualifying paid visit", sum(1 for r in q if r["ok"] == "Yes"))])


def sales_report(db, s, e, **_):
    q = db.execute(
        "SELECT x.service_date d,c.name,c.phone,x.kind,x.item_name,x.original_price,x.discount,x.amount_due,t.paid "
        "FROM sales x JOIN customers c ON c.id=x.customer_id JOIN sale_totals t ON t.sale_id=x.id "
        "WHERE x.status='active' AND x.service_date BETWEEN ? AND ? ORDER BY x.service_date, x.id", (s, e)).fetchall()
    rows = [[r["d"], r["name"], format_phone(r["phone"]), "Skin analysis" if r["kind"] == "skin" else "Package",
             r["item_name"], r["original_price"], r["discount"], r["amount_due"], r["paid"], r["amount_due"] - r["paid"]] for r in q]
    skin = sum(r["paid"] for r in q if r["kind"] == "skin")
    pkg = sum(r["paid"] for r in q if r["kind"] == "package")
    return _rep("sales", "Skin analysis and package sales",
                ["Date", "Customer", "Phone", "Type", "Item", "Original price", "Discount", "Amount due", "Collected", "Balance"],
                rows, [("Skin analysis collected (TZS)", f"{skin:,}"), ("Package collected (TZS)", f"{pkg:,}"),
                       ("Sales recorded", len(q))], money=[5, 6, 7, 8, 9])


def commissions_report(db, s, e, **_):
    q = db.execute(
        "SELECT date(m.earned_at,'+3 hours') d, r.name rn, r.phone rp, c.name cn, m.kind, x.item_name, m.base_amount,"
        "m.rate_label, m.amount, m.status FROM commissions m JOIN customers r ON r.id=m.referrer_id "
        "JOIN customers c ON c.id=m.referred_id JOIN sales x ON x.id=m.sale_id "
        "WHERE date(m.earned_at,'+3 hours') BETWEEN ? AND ? ORDER BY m.earned_at, m.id", (s, e)).fetchall()
    names = {"skin_reward": "Skin analysis reward", "package_commission": "Package commission", "adjustment": "Reversal adjustment"}
    rows = [[r["d"], r["rn"], format_phone(r["rp"]), r["cn"], names[r["kind"]], r["item_name"], r["base_amount"],
             r["rate_label"], r["amount"], r["status"]] for r in q]
    live = [r for r in q if r["status"] != "Reversed"]
    tot = lambda k: sum(r["amount"] for r in live if r["kind"] == k)
    return _rep("commissions", "Skin analysis rewards and package commissions",
                ["Date earned", "Referrer", "Referrer phone", "Referred customer", "Type", "Service / package",
                 "Amount paid (TZS)", "Rate", "Commission (TZS)", "Status"], rows,
                [("Skin analysis rewards (TZS)", f"{tot('skin_reward'):,}"),
                 ("Package commissions (TZS)", f"{tot('package_commission'):,}"),
                 ("Reversal adjustments (TZS)", f"{tot('adjustment'):,}")], money=[6, 8])


def balances_report(db, s, e, **_):
    q = db.execute(
        "SELECT r.name,r.phone,"
        "COALESCE(SUM(CASE WHEN m.status IN ('Pending','Approved','Paid') THEN m.amount END),0) earned,"
        "COALESCE(SUM(CASE WHEN m.status='Paid' THEN m.amount END),0) paid,"
        "COALESCE(SUM(CASE WHEN m.status='Pending' THEN m.amount END),0) pending,"
        "COALESCE(SUM(CASE WHEN m.status='Approved' THEN m.amount END),0) approved,"
        "COALESCE((SELECT SUM(p.amount) FROM payouts p WHERE p.referrer_id=r.id AND p.paid_on BETWEEN ? AND ?),0) period_paid "
        "FROM commissions m JOIN customers r ON r.id=m.referrer_id GROUP BY r.id ORDER BY (earned-paid) DESC, r.name",
        (s, e)).fetchall()
    rows = [[r["name"], format_phone(r["phone"]), r["earned"], r["paid"], r["pending"], r["approved"],
             r["earned"] - r["paid"], r["period_paid"]] for r in q]
    return _rep("balances", "Paid and unpaid commissions",
                ["Referrer", "Phone", "Total earned", "Total paid", "Pending", "Approved (ready to pay)", "Unpaid balance",
                 "Paid out in period"], rows,
                [("Total unpaid (TZS)", f"{sum(r[6] for r in rows):,}"), ("Paid out in period (TZS)", f"{sum(r[7] for r in rows):,}"),
                 ("Balances are as of today; the last column uses the selected dates.", "")], money=[2, 3, 4, 5, 6, 7])


def _bucket(d, group):
    dt = date.fromisoformat(d)
    if group == "month":
        return dt.strftime("%Y-%m")
    if group == "week":
        return "Week of " + (dt - timedelta(days=dt.weekday())).isoformat()
    return d


def trend_report(db, s, e, group="day", **_):
    group = group if group in ("day", "week", "month") else "day"
    data = {}

    def add(sql, params, idx):
        for r in db.execute(sql, params).fetchall():
            row = data.setdefault(_bucket(r["d"], group), [0, 0, 0, 0, 0])
            row[idx] += r["v"]

    add("SELECT p.paid_on d, SUM(p.amount) v FROM payments p JOIN sales x ON x.id=p.sale_id WHERE p.status='confirmed' "
        "AND x.status='active' AND p.paid_on BETWEEN ? AND ? GROUP BY p.paid_on", (s, e), 1)
    add("SELECT service_date d, COUNT(*) v FROM sales WHERE status='active' AND service_date BETWEEN ? AND ? GROUP BY service_date", (s, e), 0)
    add("SELECT date(registered_at,'+3 hours') d, COUNT(*) v FROM customers WHERE date(registered_at,'+3 hours') BETWEEN ? AND ? "
        "GROUP BY 1", (s, e), 2)
    add("SELECT date(registered_at,'+3 hours') d, COUNT(*) v FROM customers WHERE referrer_id IS NOT NULL AND "
        "date(registered_at,'+3 hours') BETWEEN ? AND ? GROUP BY 1", (s, e), 3)
    add("SELECT date(earned_at,'+3 hours') d, SUM(amount) v FROM commissions WHERE status IN ('Pending','Approved','Paid') "
        "AND date(earned_at,'+3 hours') BETWEEN ? AND ? GROUP BY 1", (s, e), 4)
    rows = [[k, *v] for k, v in sorted(data.items())]
    return _rep("trend", f"Sales and referrals by {group}",
                ["Period", "Sales recorded", "Amount collected (TZS)", "New customers", "Referred customers", "Commission earned (TZS)"],
                rows, [("Amount collected (TZS)", f"{sum(r[2] for r in rows):,}"),
                       ("Commission earned (TZS)", f"{sum(r[5] for r in rows):,}")], money=[2, 5])


def top_report(db, s, e, **_):
    q = db.execute(
        "SELECT r.name, r.phone, COUNT(DISTINCT c.id) n FROM customers c JOIN customers r ON r.id=c.referrer_id "
        "JOIN (SELECT customer_id, MIN(service_date) fq FROM qualifying_sales GROUP BY customer_id) q ON q.customer_id=c.id "
        "WHERE q.fq BETWEEN ? AND ? GROUP BY r.id ORDER BY n DESC, r.name LIMIT 20", (s, e)).fetchall()
    rows = [[i + 1, r["name"], format_phone(r["phone"]), r["n"]] for i, r in enumerate(q)]
    return _rep("top", "Customers who referred the most qualifying customers",
                ["Rank", "Referrer", "Phone", "Qualifying customers referred"], rows,
                [("Qualifying = referred customer's first qualifying paid visit falls in the period", "")])


REPORTS = {
    "customers": ("New and returning customers", customers_report),
    "referrals": ("Customers referred by each referrer", referrals_report),
    "sales": ("Skin analysis and package sales", sales_report),
    "commissions": ("Rewards and package commissions", commissions_report),
    "balances": ("Paid and unpaid commissions", balances_report),
    "trend": ("Sales and referrals by day, week or month", trend_report),
    "top": ("Top referrers", top_report),
}


def build(db, key, args):
    if key not in REPORTS:
        raise RuleError("Unknown report.")
    s, e = parse_range(args)
    rep = REPORTS[key][1](db, s, e, group=args.get("group", "day"))
    rep["start"], rep["end"] = s, e
    return rep


# ------------------------------------------------------------------ exports
def to_xlsx(rep):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    wb = Workbook()
    ws = wb.active
    ws.title = rep["title"][:31].replace("/", "-")
    ws.append(["Glowspot Cosmetics - " + rep["title"]])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([f"Period: {rep['start']} to {rep['end']}"])
    for label, val in rep["summary"]:
        ws.append([label, val])
    ws.append([])
    head = ws.max_row + 1
    ws.append(rep["columns"])
    for c in ws[head]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="5B2A57")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    for row in rep["rows"]:
        ws.append(row)
    for i in rep["money"]:
        for r in range(head + 1, ws.max_row + 1):
            ws.cell(row=r, column=i + 1).number_format = "#,##0"
    for i, col in enumerate(rep["columns"], start=1):
        width = max([len(str(col))] + [len(str(r[i - 1])) for r in rep["rows"][:200]]) + 2
        ws.column_dimensions[ws.cell(row=head, column=i).column_letter].width = min(max(width, 10), 40)
    ws.freeze_panes = ws.cell(row=head + 1, column=1)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def to_pdf(rep):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm,
                            topMargin=12 * mm, bottomMargin=12 * mm, title=rep["title"])
    st = getSampleStyleSheet()
    cell = ParagraphStyle("cell", parent=st["BodyText"], fontSize=7.5, leading=9)
    head = ParagraphStyle("head", parent=cell, textColor=colors.white, fontName="Helvetica-Bold")

    def safe(v):
        t = f"{v:,}" if isinstance(v, int) else str(v)
        return escape(t.encode("latin-1", "replace").decode("latin-1"))

    story = [Paragraph("Glowspot Cosmetics - " + safe(rep["title"]), st["Title"]),
             Paragraph(f"Period: {rep['start']} to {rep['end']}", st["Normal"])]
    for label, val in rep["summary"]:
        story.append(Paragraph(f"<b>{safe(label)}</b> {safe(val)}", st["Normal"]))
    story.append(Spacer(1, 6))
    data = [[Paragraph(safe(c), head) for c in rep["columns"]]]
    for r in rep["rows"]:
        data.append([Paragraph(safe(v), cell) for v in r])
    if len(data) == 1:
        story.append(Paragraph("No records for this period.", st["Normal"]))
    else:
        t = Table(data, repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#5B2A57")),
                               ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#bbbbbb")),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f1f6")]),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(t)
    doc.build(story)
    return buf.getvalue()
