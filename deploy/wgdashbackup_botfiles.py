#!/usr/bin/env python3
"""Host-only Bot API catalog. No Telegram user session required for NEW backups.

Uploads files as grouped Telegram document albums, saves the bot's returned
file_id list under root-only /etc/wgdashbackup/catalog and downloads NEW
<20 MB parts through the official Bot API into the existing verified importer.

Historic 45MB parts without a saved file_id catalog still need MTProto or
manual upload. This module deliberately NEVER performs a restore.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

import wgdashbackup_import as importer
import wgdashbackup_telegram as telegram

CFG = Path("/etc/wgdashbackup")
CATALOG = CFG / "catalog"
TELEGRAM_CFG = CFG / "telegram.conf"
NAME = re.compile(r"^wgdashboard-[0-9]{8}-[0-9]{6}\.tar\.gz$")
FILE_ID = re.compile(r"^[A-Za-z0-9_-]{8,300}$")
SHA = re.compile(r"^[a-f0-9]{64}$")
PART_BYTES = 18000000  # below Telegram cloud getFile 20 MB limit
MAX_PARTS = 64


def credentials():
    """Read root-generated shell assignments without ever executing shell."""
    if TELEGRAM_CFG.is_symlink() or not TELEGRAM_CFG.is_file():
        raise ValueError("Configure Telegram bot in WireDash settings first")
    data = {}
    for line in TELEGRAM_CFG.read_text().splitlines():
        if line.startswith(("TOKEN=", "CHAT=")):
            key, value = line.split("=", 1)
            parsed = shlex.split(value)
            if len(parsed) == 1:
                data[key] = parsed[0]
    token, chat = data.get("TOKEN", ""), data.get("CHAT", "")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{20,150}", token):
        raise ValueError("Telegram bot token not configured")
    if not re.fullmatch(r"(?:-?[0-9]{1,22}|@[A-Za-z0-9_]{5,32})", chat):
        raise ValueError("Telegram bot chat ID not configured")
    return token, chat


def catalog_path(name):
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ValueError("Invalid Telegram backup name")
    return CATALOG / (name + ".json")


def _atomic_json(path, value):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", prefix=".catalog-", dir=path.parent,
                                     delete=False) as handle:
        filename = handle.name
        os.fchmod(handle.fileno(), 0o600)
        json.dump(value, handle, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(filename, path)


def _valid_manifest(record):
    if not isinstance(record, dict):
        return False
    parts = record.get("files")
    if (not NAME.fullmatch(str(record.get("name", "")))
            or not SHA.fullmatch(str(record.get("sha256", "")))
            or not isinstance(parts, list) or not 1 <= len(parts) <= MAX_PARTS):
        return False
    total = 0
    for index, item in enumerate(parts):
        if not isinstance(item, dict):
            return False
        expected = (record["name"] if len(parts) == 1 else
                    record["name"] + ".part-" + str(index).zfill(4))
        if (item.get("name") != expected or
                not FILE_ID.fullmatch(str(item.get("file_id", ""))) or
                type(item.get("bytes")) is not int or
                not 0 < item["bytes"] <= PART_BYTES):
            return False
        total += item["bytes"]
    return total == record.get("bytes") and total <= importer.LIMIT_BYTES


def available():
    """Return display metadata only; never return file IDs or bot credentials."""
    if not CATALOG.is_dir():
        return []
    listed = []
    for path in CATALOG.glob("wgdashboard-*.tar.gz.json"):
        if path.is_symlink():
            continue
        try:
            data = json.loads(path.read_text())
            if not _valid_manifest(data):
                continue
            listed.append({
                "name": data["name"], "bytes": data["bytes"],
                "parts": len(data["files"]), "created": int(data.get("created", 0))
            })
        except (ValueError, OSError, TypeError):
            continue
    return sorted(listed, key=lambda d: d["created"], reverse=True)[:30]


def load(name):
    path = catalog_path(name)
    if path.is_symlink():
        raise ValueError("Unsafe Telegram backup manifest")
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        raise ValueError("Telegram backup catalog entry not found") from None
    if not _valid_manifest(data) or data.get("name") != name:
        raise ValueError("Telegram backup catalog entry is invalid")
    return data


def _bot_call(token, method, fields, attachments=()):
    """Never pass the token on a process command line or echo into logs."""
    with tempfile.NamedTemporaryFile(delete=False) as response:
        output = Path(response.name)
    try:
        command = [
            "curl", "-fsS", "--retry", "2", "--connect-timeout", "20",
            "--max-time", "600", "-o", str(output),
        ]
        for key, value in fields.items():
            command.extend(["-F", f"{key}={value}"])
        for index, path in enumerate(attachments):
            command.extend(["-F", f"file{index}=@{path};filename={path.name}"])
        command.extend(["--config", "-"])
        config = 'url = "https://api.telegram.org/bot%s/%s"\n' % (token, method)
        proc = subprocess.run(command, input=config, text=True,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=630, check=False)
        if proc.returncode:
            raise ValueError("Telegram bot upload failed; check network and bot permissions")
        try:
            data = json.loads(output.read_text())
        except (ValueError, OSError):
            raise ValueError("Telegram bot sent an invalid API response") from None
        if data.get("ok") is not True:
            raise ValueError("Telegram bot rejected the upload")
        return data.get("result")
    finally:
        output.unlink(missing_ok=True)


def send_archive(name, folder):
    if not NAME.fullmatch(name):
        raise ValueError("Unsupported backup name")
    folder = Path(folder)
    source = folder / name
    parts = sorted(folder.glob(name + ".part-*"))
    if not parts:
        # Small (<=18MB) snapshots are kept in the persistent output folder,
        # not inside the working/split-part directory.
        source = importer.ARCHIVES / name
        parts = [source]
    if not 1 <= len(parts) <= MAX_PARTS:
        raise ValueError("Telegram backup exceeds permitted part count")
    if not all(p.is_file() and 0 < p.stat().st_size <= PART_BYTES for p in parts):
        raise ValueError("Part exceeds 18MB Bot API download limit")
    if len(parts) > 1 and [p.name for p in parts] != [
        name + ".part-" + str(i).zfill(4) for i in range(len(parts))
    ]:
        raise ValueError("Backup Telegram parts are not consecutive")
    digest = hashlib.sha256()
    for part in parts:
        with part.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    checksum = digest.hexdigest()
    token, chat = credentials()
    total_size = sum(p.stat().st_size for p in parts)
    intro = _bot_call(token, "sendMessage", {
        "chat_id": chat,
        "text": ("📦 WireDash FULL BACKUP\n" + name +
                 "\nSize: %.1f MiB | Parts: %s\nSHA256: %s\n"
                 "All parts below belong to this backup." %
                 (total_size / 1048576, len(parts), checksum))
    })
    header_id = intro.get("message_id") if isinstance(intro, dict) else None
    if not isinstance(header_id, int):
        raise ValueError("Telegram did not return header message ID")
    files = []
    for start in range(0, len(parts), 10):
        batch = parts[start:start + 10]
        captions = [
            "WGDashboard %s | PART %s/%s | SHA256 %s" %
            (name, start + offset + 1, len(parts), checksum)
            for offset, _ in enumerate(batch)
        ]
        if len(batch) == 1:
            item = _bot_call(token, "sendDocument", {
                "chat_id": chat,
                "caption": captions[0],
                "reply_parameters": json.dumps({"message_id": header_id}),
                "document": "@"+str(batch[0])+";filename="+batch[0].name,
            })
            messages = [item]
        else:
            media = [
                {"type": "document", "media": "attach://file" + str(i),
                 "caption": captions[i]}
                for i in range(len(batch))
            ]
            messages = _bot_call(token, "sendMediaGroup", {
                "chat_id": chat, "media": json.dumps(media),
                "reply_parameters": json.dumps({"message_id": header_id}),
            }, attachments=batch)
        if not isinstance(messages, list) or len(messages) != len(batch):
            raise ValueError("Telegram response did not contain every backup document")
        for part, msg in zip(batch, messages):
            document = msg.get("document") if isinstance(msg, dict) else None
            if (not isinstance(document, dict)
                    or not FILE_ID.fullmatch(str(document.get("file_id", "")))
                    or document.get("file_size") != part.stat().st_size):
                raise ValueError("Telegram returned a mismatched document")
            files.append({
                "name": part.name,
                "bytes": part.stat().st_size,
                "file_id": document["file_id"],
            })
    manifest = {"name": name, "sha256": checksum, "bytes": total_size,
                "created": int(time.time()), "files": files}
    if not _valid_manifest(manifest):
        raise ValueError("Incomplete Telegram backup catalog; upload not recorded")
    _atomic_json(catalog_path(name), manifest)
    # Completion message is helpful but nonessential: the catalog already
    # records all Telegram-accepted documents.
    try:
        _bot_call(token, "sendMessage", {
            "chat_id": chat, "text": "✅ Backup complete: %s\n%s parts | %.1f MiB" %
            (name, len(parts), total_size / 1048576),
            "reply_parameters": json.dumps({"message_id": header_id})
        })
    except ValueError:
        pass
    return manifest


def _json_request(url, payload, timeout=40):
    req = Request(url, data=json.dumps(payload).encode(),
                  headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(req, timeout=timeout) as response:
            body = response.read(32768)
        data = json.loads(body)
    except (OSError, ValueError, URLError):
        raise ValueError("Telegram Bot API unavailable or returned invalid data") from None
    if data.get("ok") is not True:
        raise ValueError("Telegram could not locate backup part; try another snapshot")
    return data.get("result")


def _download(token, file_id, target, expected_bytes, update):
    result = _json_request("https://api.telegram.org/bot" + token + "/getFile",
                           {"file_id": file_id})
    if not isinstance(result, dict) or (
            result.get("file_size") is not None and
            result.get("file_size") != expected_bytes):
        raise ValueError("Telegram file size changed")
    filepath = result.get("file_path", "")
    if (not isinstance(filepath, str) or len(filepath) > 240
            or not re.fullmatch(r"[A-Za-z0-9_./-]+", filepath)
            or filepath.startswith("/") or ".." in filepath.split("/")):
        raise ValueError("Telegram returned an unsafe file path")
    url = "https://api.telegram.org/file/bot" + token + "/" + filepath
    count = 0
    try:
        with urlopen(url, timeout=180) as stream, target.open("xb") as out:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > expected_bytes:
                    raise ValueError("Telegram file exceeds catalog size")
                out.write(chunk)
                update(len(chunk))
    except OSError:
        raise ValueError("Telegram download failed") from None
    if count != expected_bytes:
        raise ValueError("Incomplete Telegram backup part")


def download_archive(jobid, name):
    manifest = load(name)
    token, chat = credentials()
    # Chat credential intentionally not displayed, and file IDs never
    # originate from browser input.
    stageid = jobid + os.urandom(10).hex()
    stage = importer.UPLOADS / stageid
    stage.mkdir(mode=0o700, parents=True, exist_ok=False)
    done = 0
    total = manifest["bytes"]
    try:
        telegram.write_job(jobid, "running", "downloading", 1,
                           totalParts=len(manifest["files"]), totalBytes=total)
        for index, item in enumerate(manifest["files"]):
            bytecount = [0]
            def update(bytes_read):
                bytecount[0] += bytes_read
                telegram.write_job(
                    jobid, "running", "downloading",
                    max(1, min(85, int((done + bytecount[0]) * 85 / total))),
                    completedParts=index, totalParts=len(manifest["files"]),
                    downloadedBytes=done + bytecount[0], totalBytes=total,
                )
            _download(token, item["file_id"], stage / item["name"],
                      item["bytes"], update)
            done += item["bytes"]
        telegram.write_job(jobid, "running", "checking", 90)
        digest = hashlib.sha256()
        for item in manifest["files"]:
            with (stage / item["name"]).open("rb") as stream:
                for block in iter(lambda: stream.read(1048576), b""):
                    digest.update(block)
        if digest.hexdigest() != manifest["sha256"]:
            raise ValueError("Downloaded Telegram backup SHA256 mismatch")
        telegram.write_job(jobid, "running", "importing", 95)
        def run(args, timeout=600):
            return subprocess.run(args, check=False, timeout=timeout,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        imported = importer.import_backup(stage, name, run)
        telegram.write_job(jobid, "completed", "completed", 100,
                           archive=imported["name"],
                           completedParts=len(manifest["files"]),
                           totalParts=len(manifest["files"]),
                           downloadedBytes=done, totalBytes=total)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def run_job(jobid):
    if not telegram.JOB.fullmatch(jobid):
        raise ValueError("Invalid bot download job")
    request = telegram.RUN / ("telegram-job-" + jobid + ".json")
    try:
        data = json.loads(request.read_text())
        if data.get("jobId") != jobid:
            raise ValueError("Telegram job identity mismatch")
        download_archive(jobid, data["name"])
    except Exception as exc:
        safe = str(exc)[:175] if isinstance(exc, ValueError) else \
            "Bot download failed. Inspect host service journal"
        telegram.write_job(jobid, "failed", "failed", 0, error=safe)
        raise SystemExit(1)
    finally:
        request.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "send":
        send_archive(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 3 and sys.argv[1] == "download":
        run_job(sys.argv[2])
    else:
        raise SystemExit("Usage: botfiles.py send BACKUPNAME PART_DIR | download JOB")
