#!/usr/bin/env python3
"""Host-only WireDash backup control socket. Never exposed on TCP.

Minimal fixed-command interface to existing wgdashbackup/systemd services.
The application container mounts ONLY this Unix socket directory read-only;
never mount the Docker socket or the host root filesystem into the panel.
"""
import json
import os
from pathlib import Path
import re
import socketserver
import stat
import subprocess
import uuid
import hashlib
import wgdashbackup_import as importer
from wgdashbackup_progress import read_progress, write_progress

SOCKET_DIR = Path("/run/wgdashbackup-panel")
SOCKET = SOCKET_DIR / "control.sock"
ARCHIVES = Path("/var/backups/wgdashboard")
CFG = Path("/etc/wgdashbackup")
BACKUP_BIN = "/usr/local/bin/wgdashbackup"
VALID_NAME = re.compile(r"^wgdashboard-(?:imported-)?[0-9]{8}-[0-9]{6}(?:-[a-f0-9]{8})?\.tar\.gz$")
SHA_RE = re.compile(r"^[a-f0-9]{64}$")
MAX_REQUEST_BYTES = 4096


def execute(args, input_data=None, timeout=40):
    return subprocess.run(
        args, input=input_data, text=True, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, timeout=timeout, check=False
    ).returncode == 0


def is_enabled(unit):
    return execute(["systemctl", "is-enabled", "--quiet", unit], timeout=10)


def read_number(filename):
    try:
        return int((CFG / filename).read_text().strip())
    except (OSError, ValueError):
        return None


def archive_path(name):
    if not isinstance(name, str) or not VALID_NAME.fullmatch(name):
        raise ValueError("Invalid backup filename")
    path = ARCHIVES / name
    if not path.is_file() or path.is_symlink():
        raise ValueError("Backup archive is not available")
    return path


def list_archives():
    if not ARCHIVES.is_dir():
        return []
    entries = []
    for path in ARCHIVES.iterdir():
        if not VALID_NAME.fullmatch(path.name):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        info = path.stat()
        expected = ""
        sidecar = Path(str(path) + ".sha256")
        if sidecar.is_file() and not sidecar.is_symlink():
            try:
                candidate = sidecar.read_text().split()[0].lower()
                if SHA_RE.fullmatch(candidate):
                    expected = candidate
            except (OSError, IndexError):
                pass
        entries.append({
            "name": path.name, "bytes": info.st_size,
            "modified": int(info.st_mtime),
            "sha256": expected, "telegramSent": Path(str(path) + ".sent").is_file(),
            "imported": Path(str(path) + ".imported").is_file()
        })
    return sorted(entries, key=lambda item: item["modified"], reverse=True)[:30]


def status():
    try:
        interval = read_number("interval-minutes") or 30
        return {
            "available": True,
            "configured": (CFG / "telegram.conf").is_file() and
                          (CFG / "telegram.conf").stat().st_size > 0,
            "enabled": is_enabled("wgdashbackup.timer"),
            "intervalMinutes": interval,
            "lastSuccessEpoch": read_number("last-success.epoch"),
            "lastFailureEpoch": read_number("last-failure.epoch"),
            "lastAttemptEpoch": read_number("last-attempt.epoch"),
            "archives": list_archives(),
            "discoveredBackups": importer.visible_root(),
            "restoreProgress": read_progress(),
        }
    except OSError:
        raise ValueError("Unable to read backup status") from None


def call(operation, payload):
    if operation == "status":
        return status()
    if operation == "set_interval":
        minutes = payload.get("minutes")
        if type(minutes) is not int or not 5 <= minutes <= 1440:
            raise ValueError("Interval must be 5 to 1440 minutes")
        if not execute([BACKUP_BIN, "--set-interval", str(minutes)], timeout=30):
            raise ValueError("Unable to update schedule")
        return status()
    if operation in ("enable", "disable"):
        if not execute([BACKUP_BIN, "--" + operation], timeout=30):
            raise ValueError("Unable to change backup schedule")
        return status()
    if operation == "set_telegram":
        token, chat = payload.get("token"), payload.get("chat")
        if not isinstance(token, str) or not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,150}", token):
            raise ValueError("Invalid Telegram token format")
        if not isinstance(chat, str) or not re.fullmatch(r"(?:-?[0-9]{1,22}|@[A-Za-z0-9_]{5,32})", chat):
            raise ValueError("Invalid Telegram Chat ID")
        if not execute([BACKUP_BIN, "--configure-stdin"], token + "\n" + chat + "\n", timeout=75):
            raise ValueError("Telegram validation failed; existing settings were preserved")
        return status()
    if operation == "import_root":
        result = importer.import_backup(importer.ROOT, payload.get("name"), execute)
        return {"imported": [result], "status": status()}
    if operation == "import_upload":
        imported = importer.import_upload(payload.get("uploadId"), execute)
        return {"imported": imported, "status": status()}
    if operation == "backup_now":
        if not status()["configured"]:
            raise ValueError("Configure Telegram before requesting a backup")
        if not execute(["systemctl", "start", "--no-block", "wgdashbackup.service"], timeout=15):
            raise ValueError("Unable to queue backup")
        return {"queued": True}
    if operation in ("verify", "restore"):
        name = payload.get("name")
        path = archive_path(name)
        digest = next((item["sha256"] for item in list_archives() if item["name"] == name), "")
        # When an older backup has no sidecar, verify the TAR/SQLite first,
        # then generate its local SHA256 automatically. Do not equate this
        # locally computed digest with original sender authenticity.
        command = [BACKUP_BIN, "--verify", str(path)] + ([digest] if digest else [])
        if not execute(command, timeout=240):
            raise ValueError("Backup failed archive/SQLite integrity verification")
        if not digest:
            hasher = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    hasher.update(block)
            digest = hasher.hexdigest()
            sidecar = Path(str(path) + ".sha256")
            sidecar.write_text(digest + "  " + path.name + "\n")
            sidecar.chmod(0o600)
        if operation == "verify":
            return {"verified": True, "name": name}
        if payload.get("confirmation") != "RESTORE " + name:
            raise ValueError("Restore confirmation does not match the selected file")
        # The detached systemd job survives stopping the panel container. The
        # root-only status file remains available as soon as it reconnects.
        previous = read_progress()
        if (previous and previous["state"] in ("queued", "running")):
            # Old, abandoned jobs are not silently replaced: manual inspection
            # is safer than allowing competing destructive restores.
            raise ValueError("A restore is already queued or in progress; inspect host restore status")
        job_id = uuid.uuid4().hex[:12]
        unit = "wiredash-panel-restore-" + job_id
        write_progress(job_id, "queued", 0, "queued", archive=name)
        if not execute([
            "systemd-run", "--collect", "--no-block", "--unit=" + unit,
            BACKUP_BIN, "--restore-approved", str(path), digest, job_id,
        ], timeout=20):
            write_progress(job_id, "failed", 0, "failed")
            raise ValueError("Unable to queue restore job")
        return {"queued": True, "unit": unit, "progress": read_progress()}
    raise ValueError("Unknown backup operation")


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        # The host-side verifier may need several minutes for a multi-GiB
        # SQLite backup. This is an authenticated administrator-only action.
        self.connection.settimeout(900)
        try:
            raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
            if not raw or len(raw) > MAX_REQUEST_BYTES:
                raise ValueError("Invalid request length")
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("Invalid request")
            result = {"ok": True, "data": call(payload.get("operation"), payload)}
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            result = {"ok": False, "message": str(exc)[:250]}
        except (subprocess.TimeoutExpired, subprocess.SubprocessError):
            result = {"ok": False, "message": "Backup host operation timed out or failed"}
        except Exception:
            result = {"ok": False, "message": "Host backup helper failed"}
        self.wfile.write((json.dumps(result, separators=(",", ":")) + "\n").encode("utf-8"))


class Server(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = False


def serve():
    SOCKET_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    SOCKET_DIR.chmod(0o700)
    try:
        if SOCKET.exists():
            if not stat.S_ISSOCK(SOCKET.lstat().st_mode):
                raise RuntimeError("Backup control path is not a socket")
            SOCKET.unlink()
    except FileNotFoundError:
        pass
    with Server(str(SOCKET), Handler) as server:
        SOCKET.chmod(0o600)
        server.serve_forever(poll_interval=0.3)


if __name__ == "__main__":
    serve()
