#!/usr/bin/env python3
"""Bound LOCAL automatic WireDash snapshots, including failed Telegram uploads.

Keep at most seven delivered daily noon archives and one recent undelivered
archive for retries. Constrain total automatic backup bytes to 12 GiB. Never
touch imports, migration/restore safety archives, or Telegram messages.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import re
import shutil
import sys

AUTO = re.compile(r"^wgdashboard-(\d{8})-(\d{6})\.tar\.gz$")
MAX_DAILY = 7
MAX_UNSENT = 1
MAX_LOCAL_BYTES = 12 * 1024 ** 3
MIN_FREE_BYTES = 12 * 1024 ** 3
MIN_FREE_FRACTION = 0.15


def backup_zone():
    env = Path("/opt/wgdashboard/.env")
    try:
        for line in env.read_text().splitlines():
            if line.startswith("WGD_TZ="):
                return ZoneInfo(line.split("=", 1)[1].strip().strip('"'))
    except (OSError, ValueError, KeyError):
        pass
    return datetime.now().astimezone().tzinfo


def retention_plan(folder):
    root = Path(folder)
    if not root.is_dir() or root.is_symlink():
        return []
    zone = backup_zone()
    sent, unsent = [], []
    for path in root.iterdir():
        match = AUTO.fullmatch(path.name)
        if not match or path.is_symlink() or not path.is_file():
            continue
        stamp = datetime.strptime(match[1] + match[2], "%Y%m%d%H%M%S")
        local = stamp.replace(tzinfo=timezone.utc).astimezone(zone)
        if Path(str(path) + ".sent").is_file():
            sent.append((path, local))
        else:
            unsent.append((path, local))
    sent.sort(key=lambda record: record[1], reverse=True)
    unsent.sort(key=lambda record: record[1], reverse=True)
    daily = {}
    for path, local in sent:
        if local.hour == 12:
            key = local.date()
            rank = (local.minute, local.second)
            if key not in daily or rank < daily[key][0]:
                daily[key] = (rank, path)
    newest_daily = [v[1] for _, v in sorted(daily.items(), reverse=True)[:MAX_DAILY]]
    keep = set(newest_daily)
    if sent:
        keep.add(sent[0][0])
    keep.update(path for path, _ in unsent[:MAX_UNSENT])
    # If free space is already tight, keep only the latest delivered and
    # pending archives, without affecting unrelated/user-imported files.
    try:
        disk = shutil.disk_usage(root)
        low_space = disk.free < max(MIN_FREE_BYTES, int(disk.total * MIN_FREE_FRACTION))
    except OSError:
        low_space = False
    if low_space:
        keep = {sent[0][0]} if sent else set()
        keep.update(path for path, _ in unsent[:MAX_UNSENT])
    used = sum(p.stat().st_size for p in keep)
    if used > MAX_LOCAL_BYTES:
        protected = set()
        if sent:
            protected.add(sent[0][0])
        if unsent:
            protected.add(unsent[0][0])
        removable = sorted(keep - protected, key=lambda p: p.name)
        for path in removable:
            if used <= MAX_LOCAL_BYTES:
                break
            keep.remove(path)
            used -= path.stat().st_size
    return sorted((p for p, _ in sent + unsent if p not in keep), key=lambda p: p.name)


def prune(folder):
    removed = 0
    for archive in retention_plan(folder):
        if not AUTO.fullmatch(archive.name) or archive.is_symlink() or not archive.is_file():
            continue
        for path in (archive, Path(str(archive) + ".sha256"), Path(str(archive) + ".sent")):
            if path.is_file() and not path.is_symlink():
                path.unlink()
        removed += 1
    return removed


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] != "/var/backups/wgdashboard":
        raise SystemExit("Usage: retention.py /var/backups/wgdashboard")
    print("Removed expired automatic local backups:", prune(sys.argv[1]))
