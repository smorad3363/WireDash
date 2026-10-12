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
        mid = item.get("message_id")
        if mid is not None and (type(mid) is not int or mid <= 0):
            return False
        total += item["bytes"]
    for field in ("header_message_id", "complete_message_id"):
        mid = record.get(field)
        if mid is not None and (type(mid) is not int or mid <= 0):
            return False
    chat_id = record.get("chat_id")
    if chat_id is not None and (type(chat_id) is not int or chat_id == 0):
        return False
    username = record.get("chat_username")
    if username is not None and (not isinstance(username, str) or
                                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", username)):
        return False
    return total == record.get("bytes") and total <= importer.LIMIT_BYTES


def backup_link(record):
    """Return a link to a recorded header or part, when Telegram provides one."""
    mid = record.get("header_message_id") or next(
        (f.get("message_id") for f in record["files"] if f.get("message_id")), None)
    if type(mid) is not int or mid <= 0:
        return ""
    username = record.get("chat_username")
    if username:
        return f"https://t.me/{username}/{mid}"
    chat_id = record.get("chat_id")
    if type(chat_id) is int and str(chat_id).startswith("-100"):
        suffix = str(chat_id)[4:]
        if suffix.isdigit() and int(suffix) > 0:
            return f"https://t.me/c/{suffix}/{mid}"
    return ""


def resolve_link(link):
    """Match a Telegram link ONLY to this host's saved bot-sent messages.

    The Bot API has no arbitrary history lookup. A link is usable without a
    user login only if the original message ID and chat identity were saved.
    """
    kind, target, message_id = telegram.parse_link(link)
    if not CATALOG.is_dir():
        return None
    for path in CATALOG.glob("wgdashboard-*.tar.gz.json"):
        if path.is_symlink():
            continue
        try:
            data = load(path.name[:-5])
        except (OSError, ValueError, TypeError):
            continue
        ids = [data.get("header_message_id"), data.get("complete_message_id")]
        ids.extend(item.get("message_id") for item in data["files"])
        if message_id not in ids:
            continue
        chat_id = data.get("chat_id")
        username = data.get("chat_username")
        if (kind == "username" and isinstance(username, str) and
                username.casefold() == target.casefold()):
            return data["name"]
        if kind == "channel" and type(chat_id) is int:
            if str(chat_id) == "-100" + str(target):
                return data["name"]
        if kind == "user" and type(chat_id) is int and chat_id == target:
            return data["name"]
    return None


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
                "parts": len(data["files"]), "created": int(data.get("created", 0)),
                "link": backup_link(data),
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
    """Send all parts and catalog their file IDs with resumable checkpoints.

    A Telegram album can fail after some documents were accepted. Previously
    NO file IDs were written until the last part completed; consequently the
    bot library could be empty despite messages appearing in Telegram. Save
    root-only checkpoints after EVERY successful batch and resume unfinished
    sends without resending acknowledged parts.
    """
    if not NAME.fullmatch(name):
        raise ValueError("Unsupported backup name")
    folder = Path(folder)
    parts = sorted(folder.glob(name + ".part-*"))
    if not parts:
        parts = [importer.ARCHIVES / name]
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
    fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()
    pending_path = CATALOG / (name + ".partial.json")
    pending = None
    if pending_path.exists():
        if pending_path.is_symlink():
            raise ValueError("Unsafe Telegram upload checkpoint")
        try:
            pending = json.loads(pending_path.read_text())
        except (OSError, ValueError) as exc:
            raise ValueError("Unreadable Telegram upload checkpoint") from exc
        if (not isinstance(pending, dict) or pending.get("name") != name or
                pending.get("sha256") != checksum or pending.get("bytes") != total_size or
                pending.get("bot") != fingerprint or pending.get("chat") != chat):
            raise ValueError("Telegram upload checkpoint does not match this archive/chat")
    if pending is None:
        intro = _bot_call(token, "sendMessage", {
            "chat_id": chat,
            "text": ("📦 WireDash FULL BACKUP\n" + name +
                     "\nSize: %.1f MiB | Parts: %s\nSHA256: %s\n"
                     "All parts below belong to this backup." %
                     (total_size / 1048576, len(parts), checksum))
        })
        header_id = intro.get("message_id") if isinstance(intro, dict) else None
        if type(header_id) is not int or header_id <= 0:
            raise ValueError("Telegram did not return header message ID")
        intro_chat = intro.get("chat") if isinstance(intro.get("chat"), dict) else {}
        chat_id = intro_chat.get("id")
        if type(chat_id) is not int:
            chat_id = int(chat) if chat.lstrip("-").isdigit() else None
        chat_username = intro_chat.get("username", "")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", str(chat_username)):
            candidate = chat[1:] if chat.startswith("@") else ""
            chat_username = candidate if re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_]{4,31}", candidate) else None
        pending = {"name": name, "sha256": checksum, "bytes": total_size,
                   "bot": fingerprint, "chat": chat, "created": int(time.time()),
                   "header_message_id": header_id, "files": []}
        if chat_id is not None:
            pending["chat_id"] = chat_id
        if chat_username:
            pending["chat_username"] = chat_username
        _atomic_json(pending_path, pending)
    header_id = pending["header_message_id"]
    accepted = {}
    for item in pending.get("files", []):
        if not isinstance(item, dict) or item.get("name") in accepted:
            raise ValueError("Telegram upload checkpoint has duplicate/invalid entries")
        accepted[item.get("name")] = item
    expected = {p.name: p.stat().st_size for p in parts}
    if any(n not in expected or obj.get("bytes") != expected[n] or
           not FILE_ID.fullmatch(str(obj.get("file_id", "")))
           for n, obj in accepted.items()):
        raise ValueError("Invalid Telegram upload checkpoint contents")

    def accept(part, msg):
        doc = msg.get("document") if isinstance(msg, dict) else None
        if (not isinstance(doc, dict) or
                not FILE_ID.fullmatch(str(doc.get("file_id", ""))) or
                doc.get("file_size") != part.stat().st_size):
            raise ValueError("Telegram returned a mismatched document")
        record = {"name": part.name, "bytes": part.stat().st_size,
                  "file_id": doc["file_id"]}
        mid = msg.get("message_id")
        if type(mid) is int and mid > 0:
            record["message_id"] = mid
        accepted[part.name] = record

    def checkpoint():
        pending["files"] = [accepted[p.name] for p in parts if p.name in accepted]
        _atomic_json(pending_path, pending)

    def send_one(part, index):
        caption = "WGDashboard %s | PART %s/%s | SHA256 %s" % (
            name, index + 1, len(parts), checksum)
        return _bot_call(token, "sendDocument", {
            "chat_id": chat, "caption": caption,
            "reply_parameters": json.dumps({"message_id": header_id}),
            "document": "@" + str(part) + ";filename=" + part.name
        })

    for start in range(0, len(parts), 10):
        batch = [(i, p) for i, p in enumerate(parts[start:start + 10], start)
                 if p.name not in accepted]
        if not batch:
            continue
        if len(batch) >= 2:
            media = [
                {"type": "document", "media": "attach://file" + str(i),
                 "caption": "WGDashboard %s | PART %s/%s | SHA256 %s" %
                            (name, index + 1, len(parts), checksum)}
                for i, (index, _part) in enumerate(batch)
            ]
            try:
                messages = _bot_call(token, "sendMediaGroup", {
                    "chat_id": chat, "media": json.dumps(media),
                    "reply_parameters": json.dumps({"message_id": header_id})
                }, attachments=[part for _index, part in batch])
                if not isinstance(messages, list) or len(messages) != len(batch):
                    raise ValueError("Telegram returned an incomplete album")
                for (_index, part), msg in zip(batch, messages):
                    accept(part, msg)
                checkpoint()
                continue
            except ValueError:
                # Some Telegram endpoints reject large albums even though
                # individual 18 MB documents work. Fall back to individual
                # sends; preserve any accepted/verified IDs before retry.
                checkpoint()
        for index, part in batch:
            if part.name in accepted:
                continue
            accept(part, send_one(part, index))
            checkpoint()
    if len(accepted) != len(parts):
        raise ValueError("Incomplete Telegram backup: part IDs not all recorded")
    manifest = {k: v for k, v in pending.items()
                if k not in ("bot", "chat")}
    if not _valid_manifest(manifest):
        raise ValueError("Incomplete Telegram backup catalog; upload not recorded")
    _atomic_json(catalog_path(name), manifest)
    pending_path.unlink(missing_ok=True)
    try:
        done = _bot_call(token, "sendMessage", {
            "chat_id": chat, "text": "✅ Backup complete: %s\n%s parts | %.1f MiB\nSHA256: %s" %
            (name, len(parts), total_size / 1048576, checksum),
            "reply_parameters": json.dumps({"message_id": header_id})
        })
        mid = done.get("message_id") if isinstance(done, dict) else None
        if type(mid) is int and mid > 0:
            manifest["complete_message_id"] = mid
            _atomic_json(catalog_path(name), manifest)
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
