# Deployment and migration

The app is one Python web process plus one SQLite file. It needs **persistent disk storage** for that file. Hosts with no persistent disk (e.g. free tiers that wipe files) will lose data.

## Required environment variables
`APP_ENV=production`, `SECRET_KEY` (long random), `DATABASE_PATH` (on persistent storage), `TRUST_PROXY=1` behind HTTPS proxies, `INITIAL_ADMIN_PASSWORD` (first manager; must be changed at first login). Test site only: `SEED_DEMO=1`, `TEST_BANNER`.

## Option A: Render (repo includes render.yaml)
Push the code to your GitHub, create a Render "Blueprint" from it, type the INITIAL_ADMIN_PASSWORD when asked. Render's persistent disk needs a paid instance; check current pricing. Health check: `/healthz`.

## Option B: Any Linux server / VPS
```
pip install -r requirements.txt
APP_ENV=production SECRET_KEY=... DATABASE_PATH=/var/lib/glowspot/glowspot.db TRUST_PROXY=1 \
  gunicorn wsgi:app --workers 2 --bind 127.0.0.1:8000
```
Put Nginx or Caddy in front for HTTPS (Caddy gets certificates automatically). Run gunicorn under systemd.

## Option C: Docker
`docker build -t glowspot . && docker run -p 8000:8000 -v glowspot-data:/data -e SECRET_KEY=... -e INITIAL_ADMIN_PASSWORD=... glowspot`

## Own domain
Add the domain in your host's dashboard, then create the DNS record it shows (usually a CNAME for a subdomain such as app.yourdomain.com, or A records for the root). HTTPS certificates are issued by the host/Caddy.

## Moving to another host
1. Manager logs in, Manage > Backup > Download backup (or `python scripts/backup.py`).
2. Deploy the code on the new host with the environment variables above.
3. Stop the app, run `python scripts/restore.py BACKUPFILE`, start the app.
4. Check customer count and a few balances, then switch DNS.

## Backups
Run `python scripts/backup.py` daily from cron and copy files off the server. Restore with `scripts/restore.py` (stop the app first). Treat backups as confidential: they contain phone numbers.

## Before real use
Remove demo data (use a fresh database without SEED_DEMO), change all default passwords, set `TEST_BANNER` empty. Moving to PostgreSQL later would need the SQL layer adapted; it is plain SQL but SQLite-specific in a few places (date functions, partial indexes syntax is shared).
