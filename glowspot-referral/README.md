# Glowspot Cosmetics Referral System

A web application for recording customers, skin analysis and package sales, payments, and referral rewards/commissions. Built with Python (Flask) and PostgreSQL (Supabase). Mobile-first, role-based (Staff / Manager).

## Database configuration

The application connects to PostgreSQL through `DATABASE_URL`. For local development, set this to a PostgreSQL connection string before running Flask. Do not commit connection strings or passwords.

For Render, configure these environment variables in the service dashboard:
- `DATABASE_URL` — Supabase PostgreSQL connection string
- `SECRET_KEY` — long random secret
- `INITIAL_ADMIN_USERNAME` — first manager username (defaults to `admin`)
- `INITIAL_ADMIN_NAME` — first manager's display name (defaults to `Shop Manager`)
- `INITIAL_ADMIN_PASSWORD` — first manager password; required when the database has no users

On first startup, the app applies `migrations/001_postgres_schema.sql` and creates the initial manager when the users table is empty. The manager is required to change the password at first login. Keep the initial password in Render's environment settings only; never send it in chat or commit it to Git.

## Run it locally

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
export DATABASE_URL="postgresql://..."  # Windows PowerShell: $env:DATABASE_URL="postgresql://..."
export SECRET_KEY="replace-with-a-long-random-secret"
python -m flask --app glowspot init-db
python -m flask --app glowspot create-manager
python -m flask --app glowspot run
```

Optional demo data (synthetic, clearly labelled): `python -m flask --app glowspot seed-demo`. Use on test databases only. Production Render configuration does not seed demo data.

## Backups

The production database is remote PostgreSQL hosted by Supabase. Create and download backups using the Supabase dashboard's backup/export facilities or PostgreSQL tooling such as `pg_dump`. Protect backup files because they contain customer phone numbers, referral relationships, sales and payment records. Test restoration before relying on a backup.

## Tests

Run `python -m unittest discover -s tests -v`. PostgreSQL integration tests require a dedicated test database configured through `DATABASE_URL`; never point tests at the live production database.

## Documentation

- docs/ARCHITECTURE.md - design, data model, business rules, routes
- docs/DEPLOYMENT.md - hosting, domain, migration, backups
- docs/ADMIN_GUIDE.md - manager tasks
- docs/USER_GUIDE.md - daily staff use
- docs/TEST_REPORT.md - what was tested, what was not

No secrets are stored in the code.
