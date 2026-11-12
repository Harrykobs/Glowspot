#!/usr/bin/env python3
"""Restore the database from a backup file.  STOP THE APP FIRST.

Usage:  python scripts/restore.py BACKUP_FILE [--db PATH]
The current database is kept as <db>.before-restore-<time> in case you restored the wrong file.
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

p = argparse.ArgumentParser()
p.add_argument("backup")
p.add_argument("--db", default=os.environ.get("DATABASE_PATH", "instance/glowspot.db"))
a = p.parse_args()
try:
    c = sqlite3.connect(a.backup)
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.execute("SELECT COUNT(*) FROM customers"); c.execute("SELECT COUNT(*) FROM commissions")
    c.close()
except Exception as e:  # noqa: BLE001
    sys.exit(f"That file is not a valid Glowspot backup: {e}")
if os.path.exists(a.db):
    keep = a.db + ".before-restore-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(a.db, keep)
    print("Current database saved as", keep)
for ext in ("-wal", "-shm"):
    if os.path.exists(a.db + ext):
        os.remove(a.db + ext)
shutil.copy2(a.backup, a.db)
print("Restored", a.db, "from", a.backup, ". Start the app again.")
