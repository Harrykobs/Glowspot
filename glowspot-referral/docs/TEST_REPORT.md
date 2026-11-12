# Test report

Environment: Python 3.12, Flask 3.1.3, SQLite, headless Chromium (Playwright) for screenshots. Run: `python -m unittest discover -s tests -v`.
**Result: 40 tests run, 40 passed, 0 failed** (final run). One real bug was found by the tests during development (returning-customer report used query parameters in the wrong order) and fixed.

## What the automated tests cover
- Phone formats, duplicates, self/unknown/circular referrals.
- First skin analysis TZS 2,000 reward (only when fully paid; once only; database-level duplicate guard; not for a returning customer's own visits).
- Returning price: 15,000 standard, 10,000 only with 2 qualifying referrals; partial/unpaid referrals do not count.
- Package commission 8% per instalment, after discount, on money actually collected; rounding; none without referrer; overpayment refused.
- Reversal of payments, voiding sales, clawback adjustment after payout, re-earning after correction.
- Approvals, payouts (no double payout), settings changes with history applying to future sales only.
- Roles (staff blocked from 9 manager pages, manager allowed), CSRF, forced password change, account lockout, disabled-account effect, staff date limits and search limits.
- Reports (all 7 with expected figures), Excel and PDF exports for every report, backup download.
- Security checks: XSS escaping, SQL-injection strings in search, open-redirect on login, security headers.

## Checked by hand in a running server
Health check, login with demo data, dashboard banner and totals, customer account figures; mobile (390 px) and desktop (1280 px) screenshots show no sideways page scrolling; sale form JavaScript updates the amount due.

## Speed (3,000 customers, 2,000 sales, test client on this sandbox)
Pages 5-75 ms; Excel export of 2,000 rows 0.5 s; PDF export of 2,000 rows 3.9 s (acceptable, slowest item).

## Not tested / outstanding
- **Not deployed:** no public URL exists. This sandbox has no internet, and hosting needs your account. Nothing about gunicorn, Render, Docker or HTTPS was run here.
- Only one phone-size and one desktop size in one browser (Chromium); not tested on real phones, Safari or Firefox.
- Not tested: PostgreSQL (the app uses SQLite), many simultaneous users, a professional penetration test, opening exports in Microsoft Excel (files were opened with openpyxl and checked as valid PDF/ZIP).
- Requirement gaps for you to decide: price for returning customers without 2 referrals (standard price assumed); whether a manager must "confirm" each payment (payments count as confirmed when staff record them); SMS/WhatsApp and mobile-money integrations are not included.
- Interface is English only.
