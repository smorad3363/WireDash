#!/usr/bin/env python3
"""Conservative VPS disk retention for AUTOMATIC WireDash snapshots only.

Keep ONE fully delivered backup created during the local noon hour (12:00 to
12:59) per calendar day, newest 30 days maximum. Other successfully delivered
automatic snapshots are removed from the VPS along with their .sent/.sha256
sidecars. Nothing is deleted in Telegram (no Telegram API deletion calls).

Manual imports, pre-restore safety snapshots, unuploaded snapshots, pending
uploads, and arbitrary files are never targeted. The last local backup is
retained if no qualifying noon backup exists yet.
"""
from datetime import datetime
from pathlib import Path
import re
import sys

AUTO = re.compile(r"^wgdashboard-(\d{8})-(\d{6})\.tar\.gz$")
MAX_DAILY = 30


def retention_plan(folder):
    root = Path(folder)
    sent = []
    if not root.is_dir():
        return []
    for path in root.iterdir():
        match = AUTO.fullmatch(path.name)
        if not match or path.is_symlink() or not path.is_file():
            continue
        if not Path(str(path) + ".sent").is_file():
            # Never remove a backup that has NOT been confirmed delivered.
            continue
        dt = datetime.strptime(match[1] + match[2], "%Y%m%d%H%M%S")
        sent.append((path, dt))
    if not sent:
        return []
    # One noon snapshot per day. Prefer the CLOSEST available to 12:00.
    selected = {}
    for path, dt in sent:
        if dt.hour != 12:
            continue
        key = dt.date()
        rank = (dt.minute, dt.second, path.name)
        if key not in selected or rank < selected[key][0]:
            selected[key] = (rank, path)
    keep = {v[1] for _,v in sorted(selected.items(), reverse=True)[:MAX_DAILY]}
    # Never destroy the only successfully sent local backup when noon has not
    # arrived yet or its job failed: preserve one latest safety copy.
    if not keep:
        keep.add(max(sent, key=lambda x: x[1])[0])
    return sorted([path for path, _dt in sent if path not in keep])


def prune(folder):
    removed = 0
    for archive in retention_plan(folder):
        # Re-check both exact filename and sent marker before deleting.
        if not AUTO.fullmatch(archive.name) or not Path(str(archive) + ".sent").is_file():
            continue
        if archive.is_symlink() or not archive.is_file():
            continue
        for name in (archive, Path(str(archive) + ".sha256"),
                     Path(str(archive) + ".sent")):
            if name.is_file() and not name.is_symlink():
                name.unlink()
        removed += 1
    return removed


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "/var/backups/wgdashboard":
        raise SystemExit("Usage: retention.py /var/backups/wgdashboard")
    print("Removed delivered non-noon/old snapshots:", prune(sys.argv[1]))
