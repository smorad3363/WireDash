#!/usr/bin/env python3
"""Rehearse the actual wireback verifier on disposable full and multipart archives.

CI uses a disposable GitHub runner, no running VPN or real credentials.
"""
from io import BytesIO
import os
from pathlib import Path
import sqlite3
import subprocess
import tarfile
import tempfile


def archive(destination):
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        db = data / "backup.db"
        with sqlite3.connect(db) as conn:
            conn.execute("create table peers (id text)")
            conn.execute("insert into peers values ('fake-only')")
        with tarfile.open(destination, "w:gz") as tar:
            for name, content in (
                ("./data/wg-dashboard.ini", b"[Database]\ntype = sqlite\n"),
                ("./compose.yaml", b"services:\n  wgdashboard:\n    image: test-only\n"),
            ):
                info = tarfile.TarInfo(name)
                info.size = len(content)
                tar.addfile(info, BytesIO(content))
            tar.add(db, arcname="./data/db/fake.db")


def verify(path, should_pass):
    proc = subprocess.run([
        "sudo", "bash", "deploy/wgdashbackup.sh", "--verify", str(path)
    ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, timeout=70)
    if (proc.returncode == 0) != should_pass:
        raise AssertionError(
            "Expected verify=" + str(should_pass) + ", got rc=" +
            str(proc.returncode) + "\n" + proc.stdout[-1500:])


def main():
    with tempfile.TemporaryDirectory() as root:
        location = Path(root)
        backup = location / "pre-migrate-20261010-010101.tar.gz"
        archive(backup)
        verify(backup, True)  # no SHA256 supplied
        content = backup.read_bytes()
        backup.unlink()
        first = location / (backup.name + ".part-0000")
        second = location / (backup.name + ".part-0001")
        first.write_bytes(content[:len(content)//2])
        second.write_bytes(content[len(content)//2:])
        verify(first, True)  # join 4-digit parts automatically, no SHA
        second.unlink()
        verify(first, False)  # incomplete gzip backup rejected
        bad = location / "untrusted.tar.gz"
        with tarfile.open(bad, "w:gz") as tar:
            link = tarfile.TarInfo("./data/evil")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/shadow"
            tar.addfile(link)
        verify(bad, False)  # no symlink extraction
    print("PASS: real wireback accepts complete legacy and multipart archives without SHA, rejects invalid data")


if __name__ == "__main__":
    main()
