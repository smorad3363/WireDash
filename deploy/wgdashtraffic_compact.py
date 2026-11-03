#!/usr/bin/env python3
"""Bound historic WireDash graph samples without changing peer usage totals.

The peer and traffic-ledger tables remain authoritative. Keep first/last chart
samples per hour for 1-7 day history, per day thereafter, and every original
sample from the most recent 24 hours. Runs only from the host under a lock.
"""
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

HOURLY_DAYS = 7
FULL_HOURS = 24
BATCH_ROWIDS = 20000
TABLE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*_transfer$")


def source_database():
    raw = subprocess.check_output(["docker", "inspect", "wgdashboard"], text=True)
    containers = json.loads(raw)
    mounts = containers[0].get("Mounts", [])
    source = next((m.get("Source") for m in mounts
                   if m.get("Destination") == "/data"), None)
    if not source:
        raise RuntimeError("WireDash /data mount not found")
    path = Path(source).resolve() / "db" / "wgdashboard.db"
    if not path.is_file() or path.is_symlink():
        raise RuntimeError("WireDash SQLite database not found")
    return path


def compact_table(conn, name, now, batch_size=BATCH_ROWIDS):
    if not TABLE.fullmatch(name):
        raise ValueError("Unsafe transfer table")
    table = '"' + name + '"'
    hour_cutoff = (now - dt.timedelta(hours=FULL_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    day_cutoff = (now - dt.timedelta(days=HOURLY_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    # Read in insertion order, retaining chronological boundaries for charts.
    # No active/current usage is ever calculated from or changed by this table.
    boundaries = {}
    scanned = 0
    max_old_rowid = 0
    for rowid, peer, stamp in conn.execute(
        f"SELECT rowid, id, time FROM {table} WHERE time < ? ORDER BY rowid",
        (hour_cutoff,),
    ):
        if not isinstance(stamp, str) or len(stamp) < 13:
            continue
        bucket = stamp[:10] if stamp < day_cutoff else stamp[:13]
        key = (peer, bucket)
        if key in boundaries:
            boundaries[key][1] = rowid
        else:
            boundaries[key] = [rowid, rowid]
        scanned += 1
        if rowid > max_old_rowid:
            max_old_rowid = rowid
    if not scanned:
        return 0, 0, 0
    conn.execute("DROP TABLE IF EXISTS temp.wiredash_keep_samples")
    conn.execute("CREATE TEMP TABLE wiredash_keep_samples (rid INTEGER PRIMARY KEY)")
    rows = set()
    for first, last in boundaries.values():
        rows.add(first)
        rows.add(last)
    conn.executemany("INSERT INTO temp.wiredash_keep_samples(rid) VALUES (?)",
                     ((rid,) for rid in sorted(rows)))
    deleted = 0
    for start in range(1, max_old_rowid + 1, batch_size):
        end = min(start + batch_size - 1, max_old_rowid)
        cursor = conn.execute(
            f"DELETE FROM {table} WHERE rowid BETWEEN ? AND ? AND time < ? "
            "AND rowid NOT IN (SELECT rid FROM temp.wiredash_keep_samples)",
            (start, end, hour_cutoff))
        deleted += max(cursor.rowcount, 0)
        conn.commit()
        time.sleep(0.015)
    conn.execute("DROP TABLE temp.wiredash_keep_samples")
    conn.commit()
    return scanned, len(rows), deleted


def run(dbpath=None, now=None):
    path = dbpath or source_database()
    now = now or dt.datetime.now()
    db = sqlite3.connect(str(path), timeout=30, isolation_level="DEFERRED")
    try:
        db.execute("PRAGMA busy_timeout=30000")
        mode = db.execute("PRAGMA journal_mode").fetchone()[0]
        if mode.lower() != "wal":
            raise RuntimeError("Refuse live compaction without WAL mode")
        tables = [r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'") if TABLE.fullmatch(r[0])]
        for table in tables:
            scanned, kept, removed = compact_table(db, table, now)
            print(f"{table}: old={scanned} keep={kept} removed={removed}", flush=True)
        # Avoid a live VACUUM: deleted pages are reused in the active SQLite DB.
        # The backup job VACUUMs its offline snapshot to reduce archive size.
        db.execute("PRAGMA wal_checkpoint(PASSIVE)")
    finally:
        db.close()


if __name__ == "__main__":
    if os.geteuid():
        raise SystemExit("Run as root")
    fd = os.open("/run/wgdashtraffic-compact.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("Compaction already running")
    run()
