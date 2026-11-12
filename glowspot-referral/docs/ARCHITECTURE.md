# Architecture

**Stack:** Flask 3 (Python 3.12), server-rendered HTML (Jinja), a little plain JavaScript, SQLite (WAL mode), Werkzeug password hashing, openpyxl (Excel) and ReportLab (PDF). No external services or build step, so it moves easily between hosts.

**Layout:** `glowspot/services.py` holds every business rule; `views.py` holds routes only; `reports.py` builds reports and exports; `db.py` holds the schema. The rules can be tested without the website.

## Data model (see db.py)
users, customers (phone = referral code, referrer_id), referral_changes, settings + settings_history, packages, sales (price/discount/due snapshot), payments (confirmed/reversed), commissions (ledger), payouts, audit. Views `sale_totals` and `qualifying_sales` define "paid" and "qualifying paid visit" in one place. A partial unique index stops a second first-analysis reward for the same customer, and another stops two package commissions for one payment.

## Business rules as built
1. Phone numbers are normalised to +255XXXXXXXXX (06/07 mobiles). Duplicates are rejected in any format.
2. Self-referral, unknown referrer and circular referral are rejected. Only a manager can set/change a referral link after registration, with a reason; every change is stored.
3. First skin analysis = TZS 15,000 (setting). Later skin analyses cost the standard price unless the customer has 2+ qualifying referrals, then TZS 10,000. (The requirements did not state a price for returning customers without the referrals; the standard price is assumed.)
4. Qualifying paid visit: a package with at least one confirmed payment, or a skin analysis paid in full.
5. Reward TZS 2,000: created once per referred customer when their first skin analysis is paid in full. Not created for returning visits.
6. Package commission: 8% of each confirmed payment amount (after discount), rounded half-up to whole shillings, per instalment. Skin analyses cannot be discounted.
7. Commission starts Pending; a manager approves it; a payout marks it Paid. Settings changes affect future transactions only.
8. Nothing is deleted. A manager reverses a payment or voids a sale with a reason; Pending/Approved commission becomes Reversed; already-Paid commission gets a negative adjustment that is netted in the next payout.
9. Staff may date entries up to 7 days back, never the future.

## Roles
Staff: search by phone (4+ digits), register customers, record sales/payments, view an account and the commissions their transaction created. Manager: everything, plus customer lists, referral changes, reversals/voids, approvals, payouts, prices/rates, packages, staff accounts, reports/exports, audit log, backups.

## Security
Hashed passwords (scrypt), signed HttpOnly SameSite cookies (Secure in production), CSRF token on every POST, parameterised SQL, auto-escaped templates, Content-Security-Policy with no inline scripts, HSTS in production, lock-out after 5 failed logins (10 minutes), forced password change for new/reset accounts, immediate effect when an account is disabled, full audit log.

## Routes (no public API; all pages need login)
`/login /logout /account/password /healthz` - `/ /customers /customers/new /customers/<id> /customers/<id>/sales/new /customers/<id>/referrer /customers/<id>/payout /api/referrer` - `/sales/<id> /sales/<id>/payments /sales/<id>/void /payments/<id>/reverse` - `/commissions /commissions/approve /payouts` - manager only: `/staff /settings /packages /reports /reports/<key>[/export/xlsx|pdf] /audit /backup`.
