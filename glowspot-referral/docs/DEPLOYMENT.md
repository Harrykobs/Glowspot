# Deployment and database operations

Glowspot uses PostgreSQL. Production and local deployment must provide a PostgreSQL connection string through `DATABASE_URL`; do not configure `DATABASE_PATH` or SQLite storage.

## Required environment variables

- `APP_ENV=production`
- `SECRET_KEY`: a long, random Flask session-signing secret
- `DATABASE_URL`: Supabase PostgreSQL connection string (use the provider-recommended connection mode for the hosting environment)
- `TRUST_PROXY=1` when hosted behind a trusted HTTPS reverse proxy
- `INITIAL_ADMIN_USERNAME`: optional first-manager username (defaults to `admin`)
- `INITIAL_ADMIN_NAME`: optional first-manager display name
- `INITIAL_ADMIN_PASSWORD`: required when bootstrapping an empty production database

Set secrets only in the hosting provider's secret/environment-variable settings. Never commit them or paste them into issue trackers. The application retains its own database-backed username/password authentication; Supabase Auth is not used.

## Render

The repository includes `render.yaml`. Create a Render Blueprint from the repository and configure `DATABASE_URL` and `INITIAL_ADMIN_PASSWORD` in the service environment. Render's generated `SECRET_KEY` is used for Flask sessions. The application applies the PostgreSQL schema during startup. The first manager is created only when the users table is empty and a bootstrap password is configured. Remove or rotate the bootstrap password after successful first-manager creation.

Production demo seeding is disabled. Do not enable synthetic demo data in a live database.

## Docker

Build and run with a PostgreSQL URL supplied securely at runtime:

```sh
docker build -t glowspot .
docker run --rm -p 8000:8000 \
  -e APP_ENV=production \
  -e SECRET_KEY="$SECRET_KEY" \
  -e DATABASE_URL="$DATABASE_URL" \
  -e INITIAL_ADMIN_PASSWORD="$INITIAL_ADMIN_PASSWORD" \
  glowspot
```

The image does not contain or mount a SQLite database. Ensure secrets are injected by a secure deployment mechanism rather than saved in the image.

## PostgreSQL backup

The scripts require PostgreSQL client tools (`pg_dump` and `pg_restore`) and `DATABASE_URL`.

```sh
python scripts/backup.py --out backups --keep 30
```

The backup is a PostgreSQL custom-format archive. Store it encrypted, restrict access, and copy it off the application host. Backups contain personal customer information.

Supabase also provides provider-managed backup and recovery options depending on the selected plan. Confirm the retention and point-in-time recovery capabilities of your actual Supabase project; do not assume they are enabled.

## PostgreSQL restore

Restoring can replace database objects. First take a fresh backup and verify that `DATABASE_URL` points to the intended recovery database. Then run:

```sh
python scripts/restore.py backups/glowspot-YYYYMMDD-HHMMSS.dump --confirm
```

The script deliberately requires `--confirm`. Prefer restoring into a separate recovery database first, validate the data and application, and only then plan production recovery. Never test restore against the live production database.

## Moving hosts or recovering service

1. Provision PostgreSQL and apply the migration schema.
2. Configure the environment variables above on the new host.
3. Restore a verified PostgreSQL archive into the intended database if migrating existing data.
4. Start the application and verify manager/staff login, customers/referrals, sales, payments, commissions, payouts, reports and exports.
5. Restart the application and confirm that records persist.

## Before real use

Use a dedicated test database for integration tests. Verify the full test suite and the migration schema against PostgreSQL before production deployment. Do not treat a successful build or deployment as evidence that the migration is complete.
