# Glowspot Cosmetics Referral System

A web application for recording customers, skin analysis and package sales, payments, and referral rewards/commissions.
Built with Python (Flask) and SQLite. Mobile-first, role-based (Staff / Manager).

## Run it on your own computer
```
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m flask --app glowspot create-manager            # creates your first manager login
python -m flask --app glowspot run                       # open http://127.0.0.1:5000
```
Optional demo data (synthetic, clearly labelled): `python -m flask --app glowspot seed-demo`
Demo logins: `demo.manager` and `demo.staff`, password `Glowspot-Demo-2026` (or the `DEMO_PASSWORD` you set). Use on test sites only.

## Run the tests
`python -m unittest discover -s tests -v`   (40 tests; see docs/TEST_REPORT.md)

## Documentation
- docs/ARCHITECTURE.md - design, data model, business rules, routes
- docs/DEPLOYMENT.md - hosting, domain, migration, backups
- docs/ADMIN_GUIDE.md - manager tasks
- docs/USER_GUIDE.md - daily staff use
- docs/TEST_REPORT.md - what was tested, what was not

Configuration is by environment variables: see `.env.example`. No secrets are stored in the code.
