#!/usr/bin/env python3
"""Make a consistent backup of the database (safe while the app is running).

Usage:  python scripts/backup.py [--db PATH] [--out DIR] [--keep 30]
Schedule it daily with cron, e.g.:  0 2 * * * cd /path/to/app && python scripts/backup.py
Copy the backups OFF the server as well (another disk, Google Drive, etc).
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime

p = argparse.ArgumentParser()
p.add_argument("--db", default=os.environ.get("DATABASE_PATH", "instance/glowspot.db"))
p.add_argument("--out", default="backups")
p.add_argument("--keep", type=int, default=30, help="number of recent backups to keep")
a = p.parse_args()
if not os.path.exists(a.db):
    sys.exit(f"Database not found: {a.db}")
os.makedirs(a.out, exist_ok=True)
dest = os.path.join(a.out, "glowspot-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".db")
src, dst = sqlite3.connect(a.db), sqlite3.connect(dest)
src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
src.close(); dst.close()
if ok != "ok":
    sys.exit(f"Backup failed integrity check: {ok}")
os.chmod(dest, 0o600)
old = sorted(f for f in os.listdir(a.out) if f.startswith("glowspot-") and f.endswith(".db"))[:-a.keep]
for f in old:
    os.remove(os.path.join(a.out, f))
print("Backup written:", dest)
