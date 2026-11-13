#!/usr/bin/env python3
"""Create a PostgreSQL custom-format backup using pg_dump.

Requires DATABASE_URL and PostgreSQL client tools (pg_dump, pg_restore).
Example: python scripts/backup.py --out backups --keep 30
Keep backup files encrypted and off the application server.
"""
import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--out", default="backups")
parser.add_argument("--keep", type=int, default=30)
args = parser.parse_args()

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    sys.exit("DATABASE_URL is required; configure your Supabase PostgreSQL connection string.")
if args.keep < 1:
    sys.exit("--keep must be at least 1.")
pg_dump = shutil.which("pg_dump")
pg_restore = shutil.which("pg_restore")
if not pg_dump or not pg_restore:
    sys.exit("PostgreSQL client tools pg_dump and pg_restore must be installed.")

out_dir = Path(args.out)
out_dir.mkdir(parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
destination = out_dir / f"glowspot-{stamp}.dump"
try:
    with destination.open("wb") as backup_file:
        subprocess.run(
            [pg_dump, "--format=custom", "--no-owner", "--no-acl", "--dbname", database_url],
            stdout=backup_file, check=True,
        )
    with destination.open("rb") as backup_file:
        subprocess.run([pg_restore, "--list"], stdin=backup_file, stdout=subprocess.DEVNULL, check=True)
except (OSError, subprocess.CalledProcessError) as exc:
    destination.unlink(missing_ok=True)
    sys.exit(f"PostgreSQL backup failed: {exc}")

os.chmod(destination, 0o600)
old_backups = sorted(out_dir.glob("glowspot-*.dump"), key=lambda path: path.stat().st_mtime, reverse=True)
for old in old_backups[args.keep:]:
    old.unlink()
print(f"Verified PostgreSQL backup written: {destination}")
