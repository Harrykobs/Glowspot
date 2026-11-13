#!/usr/bin/env python3
"""Restore a PostgreSQL custom-format backup.

Requires DATABASE_URL and PostgreSQL client tools. This operation can replace
objects in the target database. It refuses to run without --confirm.
Take and verify a fresh backup before restoring.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("backup", help="pg_dump custom-format .dump file")
parser.add_argument("--confirm", action="store_true", help="confirm that the target database may be modified")
args = parser.parse_args()

database_url = os.environ.get("DATABASE_URL")
if not database_url:
    sys.exit("DATABASE_URL is required; configure the intended Supabase PostgreSQL database.")
if not args.confirm:
    sys.exit("Restore not run. Review the target database and repeat with --confirm.")
backup = Path(args.backup)
if not backup.is_file():
    sys.exit(f"Backup file not found: {backup}")
pg_restore = shutil.which("pg_restore")
if not pg_restore:
    sys.exit("PostgreSQL client tool pg_restore must be installed.")

try:
    with backup.open("rb") as backup_file:
        subprocess.run([pg_restore, "--list"], stdin=backup_file, stdout=subprocess.DEVNULL, check=True)
    subprocess.run([
        pg_restore, "--exit-on-error", "--clean", "--if-exists", "--no-owner", "--no-acl",
        "--dbname", database_url, str(backup),
    ], check=True)
except (OSError, subprocess.CalledProcessError) as exc:
    sys.exit(f"PostgreSQL restore failed; inspect the database before retrying: {exc}")

print(f"Restore completed from {backup}. Verify application workflows before reopening service.")
