#!/usr/bin/env python3
"""Persist a WireGuard interface MTU without altering keys, peers, or routes.

Runs in the container entrypoint, before WGDashboard can start wg0. The
configuration resides in the /etc/wireguard Docker volume, not the image.
"""
import os
from pathlib import Path
import re
import stat
import sys
import tempfile

WG0 = Path("/etc/wireguard/wg0.conf")
MTU_FIELD = re.compile(r"^\s*MTU\s*=", re.IGNORECASE)
SECTION = re.compile(r"^\s*\[([^]]+)\]\s*(?:[#;].*)?$")


def persist_mtu(path: Path, value: str) -> bool:
    if not re.fullmatch(r"[0-9]{4}", value) or not 1280 <= int(value) <= 9000:
        raise ValueError("WireGuard MTU must be an integer from 1280 to 9000")
    # Never follow a symlink, hard-linked config, directory, or unexpected node.
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Unsafe wg0 configuration path")
    original = path.read_bytes()
    contents = original.decode("utf-8")
    lines = contents.splitlines(keepends=True)
    section_starts = [
        i for i, line in enumerate(lines)
        if (match := SECTION.match(line)) and match.group(1).strip().lower() == "interface"
    ]
    if len(section_starts) != 1:
        raise ValueError("Expected exactly one [Interface] in wg0.conf")
    start = section_starts[0]
    end = next((i for i in range(start + 1, len(lines)) if SECTION.match(lines[i])),
               len(lines))
    indices = [i for i in range(start + 1, end) if MTU_FIELD.match(lines[i])]
    newline = "\r\n" if "\r\n" in contents else "\n"
    desired = "MTU = " + value + newline
    if indices:
        lines[indices[0]] = desired
        for i in reversed(indices[1:]):
            del lines[i]
    else:
        # If the header is at EOF without a newline, add a separator first.
        if not lines[start].endswith(("\n", "\r")):
            lines[start] += newline
        lines.insert(start + 1, desired)
    updated = "".join(lines).encode("utf-8")
    if updated == original:
        return False

    fd, temporary = tempfile.mkstemp(prefix=".wg0-mtu-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            os.fchmod(out.fileno(), stat.S_IMODE(info.st_mode))
            os.fchown(out.fileno(), info.st_uid, info.st_gid)
            out.write(updated)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: ensure-wg-mtu.py MTU")
    try:
        changed = persist_mtu(WG0, sys.argv[1])
    except (OSError, UnicodeError, ValueError) as exc:
        raise SystemExit("Unable to persist WireGuard MTU: " + str(exc)) from None
    print("WireGuard wg0 MTU is configured:", sys.argv[1], "(updated)" if changed else "(unchanged)")
