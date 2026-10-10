#!/usr/bin/env python3
"""Opt-in Telegram user session and host-only multipart wireback downloader.

Never automatically invokes Restore. Telegram's bot token alone cannot read
historical 45 MB parts. Session credentials stay root-only on the VPS.
"""
import asyncio
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit, parse_qs

import wgdashbackup_import as importer

CFG = Path("/etc/wgdashbackup")
CONFIG = CFG / "telegram-client.json"
SESSION = CFG / "telegram-user"
RUN = Path("/run/wgdashbackup-panel")
VENV = Path("/opt/wgdashbackup-telegram/venv/bin/python3")
JOB = re.compile(r"^[a-f0-9]{12}$")
CAPTION = re.compile(
    r"WGDashboard\s+(wgdashboard-[0-9]{8}-[0-9]{6}\.tar\.gz)\s*\|\s*"
    r"PART\s+([0-9]{1,2})/([0-9]{1,2})\s*\|\s*SHA256\s+([a-fA-F0-9]{64})"
)
PART = re.compile(r"^(wgdashboard-[0-9]{8}-[0-9]{6}\.tar\.gz)\.part-(\d{3,4})$")


def parse_link(link):
    """Parse Telegram IDs, never fetching the input as a URL."""
    if not isinstance(link, str) or len(link) > 350:
        raise ValueError("Paste a Telegram message link")
    u = urlsplit(link.strip())
    if u.scheme == "tg" and u.netloc == "openmessage":
        q = parse_qs(u.query)
        user = q.get("user_id", [""])[0]
        mid = q.get("message_id", [""])[0]
        if re.fullmatch(r"[1-9]\d{0,18}", user) and re.fullmatch(r"[1-9]\d{0,14}", mid):
            return ("user", int(user), int(mid))
    if (u.scheme != "https" or u.hostname not in ("t.me", "www.t.me", "telegram.me")
            or u.username or u.password or u.port or u.fragment or u.query):
        raise ValueError("Only Telegram message links are accepted")
    parts = u.path.strip("/").split("/")
    if (len(parts) == 3 and parts[0] == "c"
            and re.fullmatch(r"[1-9]\d{1,18}", parts[1])
            and re.fullmatch(r"[1-9]\d{0,14}", parts[2])):
        return ("channel", int(parts[1]), int(parts[2]))
    if (len(parts) == 2 and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", parts[0])
            and re.fullmatch(r"[1-9]\d{0,14}", parts[1])):
        return ("username", parts[0], int(parts[1]))
    raise ValueError("Use the message link for the FIRST backup part")


def configured():
    return CONFIG.is_file() and not CONFIG.is_symlink() and Path(str(SESSION) + ".session").is_file()


def current_job():
    try:
        data = json.loads((RUN / "telegram-download-status.json").read_text())
        if (isinstance(data, dict) and JOB.fullmatch(str(data.get("jobId", "")))
                and data.get("state") in ("queued", "running", "completed", "failed")):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return None


def write_job(jobid, state, phase, percent, **data):
    if not JOB.fullmatch(jobid) or state not in ("queued", "running", "completed", "failed"):
        raise ValueError("Bad Telegram download job")
    if type(percent) is not int or not 0 <= percent <= 100:
        raise ValueError("Invalid download percentage")
    old = current_job() or {}
    if old.get("jobId") not in (None, jobid) and state != "queued":
        raise ValueError("Another job owns this status")
    if state == "queued":
        old = {}
    obj = {
        "jobId": jobid, "state": state, "phase": phase, "percent": percent,
        "updatedAt": int(time.time()),
        "completedParts": data.get("completedParts", old.get("completedParts", 0)),
        "totalParts": data.get("totalParts", old.get("totalParts", 0)),
        "downloadedBytes": data.get("downloadedBytes", old.get("downloadedBytes", 0)),
        "totalBytes": data.get("totalBytes", old.get("totalBytes", 0)),
        "archive": data.get("archive", old.get("archive", "")),
        "error": data.get("error", ""),
    }
    RUN.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = RUN / ("." + jobid + ".tmp")
    with tmp.open("w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(obj, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, RUN / "telegram-download-status.json")
    return obj


def login():
    if not sys.stdin.isatty():
        raise ValueError("Interactive VPS terminal required")
    CFG.mkdir(mode=0o700, parents=True, exist_ok=True)
    print("Login with a Telegram USER account that has access to the backup messages.")
    print("This grants the server access to that Telegram session. Keep root secure.")
    api_id = input("api_id (my.telegram.org): ").strip()
    api_hash = getpass.getpass("api_hash (hidden): ").strip()
    phone = input("Telegram phone in +international format: ").strip()
    if not re.fullmatch(r"[1-9]\d{3,12}", api_id):
        raise ValueError("Invalid api_id")
    if not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash):
        raise ValueError("Invalid api_hash")
    if not re.fullmatch(r"\+[1-9]\d{6,14}", phone):
        raise ValueError("Invalid phone")
    from telethon.sync import TelegramClient
    client = TelegramClient(str(SESSION), int(api_id), api_hash)
    try:
        # Do not use a context manager here: it may invoke an interactive
        # Telethon default start() before our explicit secure callbacks.
        client.start(phone=lambda: phone,
                     code_callback=lambda: input("Telegram login code: ").strip(),
                     password=lambda: getpass.getpass("Telegram 2FA password: "))
        if not client.is_user_authorized() or client.is_bot():
            raise ValueError("Telegram USER authorization is required")
    finally:
        client.disconnect()
    tmp = CFG / ".telegram-client.tmp"
    tmp.write_text(json.dumps({"api_id": int(api_id), "api_hash": api_hash}))
    tmp.chmod(0o600)
    os.replace(tmp, CONFIG)
    Path(str(SESSION) + ".session").chmod(0o600)
    print("Telegram linked. You may now paste the first-part link in the panel.")


def select_parts(messages, first):
    caption = CAPTION.search(getattr(first, "raw_text", "") or "")
    file_name = getattr(getattr(first, "file", None), "name", None)
    if not caption or not file_name:
        raise ValueError("Selected message is not a wireback backup document")
    base, number, count, checksum = caption.groups()
    count, number = int(count), int(number)
    if number != 1 or count not in range(1, 33):
        raise ValueError("Paste the message link for PART 1 of the backup")
    first_expected = base if count == 1 else base + ".part-0000"
    if not (file_name == first_expected or
            (count > 1 and file_name == base + ".part-000")):
        raise ValueError("First part filename does not match its caption")
    selected = {}
    for msg in [first] + list(messages):
        name = getattr(getattr(msg, "file", None), "name", None)
        found = CAPTION.search(getattr(msg, "raw_text", "") or "")
        if not name or not found:
            continue
        part_base, part_n, total, sha = found.groups()
        index = int(part_n) - 1
        if part_base != base or int(total) != count or sha.lower() != checksum.lower():
            continue
        match = PART.fullmatch(name)
        if count == 1 and name == base and index == 0:
            pass
        elif not match or match.group(1) != base or int(match.group(2)) != index:
            continue
        if index in selected and selected[index].id != msg.id:
            raise ValueError("Duplicate Telegram part: " + str(index))
        selected[index] = msg
    if set(selected) != set(range(count)):
        missing = sorted(set(range(count)) - set(selected))
        raise ValueError("Missing Telegram parts: " + ",".join(str(n + 1) for n in missing))
    total_bytes = sum(int(getattr(m.file, "size", 0) or 0) for m in selected.values())
    if total_bytes < 1 or total_bytes > importer.LIMIT_BYTES:
        raise ValueError("Telegram backup exceeds the 900 MiB limit")
    return base, checksum.lower(), [selected[i] for i in range(count)], total_bytes


async def resolve_chat(client, kind, target):
    if kind == "username":
        return await client.get_input_entity(target)
    from telethon import utils
    async for dialog in client.iter_dialogs(limit=500):
        obj = dialog.entity
        if kind == "channel" and getattr(obj, "id", None) == target and \
                utils.get_peer_id(obj) == int("-100" + str(target)):
            return dialog.input_entity
        if kind == "user" and utils.get_peer_id(obj) == target:
            return dialog.input_entity
    raise ValueError("Telegram account cannot access this bot chat or channel")


async def download(jobid, link):
    from telethon import TelegramClient
    config = json.loads(CONFIG.read_text())
    stageid = jobid + os.urandom(10).hex()
    stage = importer.UPLOADS / stageid
    stage.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        kind, target, message_id = parse_link(link)
        async with TelegramClient(str(SESSION), int(config["api_id"]), config["api_hash"]) as client:
            if not await client.is_user_authorized():
                raise ValueError("Telegram session expired; run wireback --telegram-login again")
            entity = await resolve_chat(client, kind, target)
            first = await client.get_messages(entity, ids=message_id)
            if not first:
                raise ValueError("Telegram message not found in the connected account")
            nearby = []
            async for message in client.iter_messages(
                    entity, min_id=max(0, message_id - 1500),
                    max_id=message_id + 1500, limit=3000):
                nearby.append(message)
            base, checksum, parts, total = select_parts(nearby, first)
            downloaded = 0
            write_job(jobid, "running", "downloading", 1,
                      totalParts=len(parts), totalBytes=total)
            for i, message in enumerate(parts):
                filename = base if len(parts) == 1 else base + ".part-" + str(i).zfill(4)
                if not 0 < message.file.size <= 50 * 1024 * 1024:
                    raise ValueError("Invalid Telegram part size")
                destination = stage / filename
                tick = [0.]
                def progress(current, ignored_total):
                    now = time.monotonic()
                    if now - tick[0] >= 2:
                        tick[0] = now
                        write_job(jobid, "running", "downloading",
                                  min(85, int((downloaded + current) * 85 / total)),
                                  completedParts=i, totalParts=len(parts),
                                  downloadedBytes=downloaded + current, totalBytes=total)
                result = await client.download_media(message, file=str(destination),
                                                     progress_callback=progress)
                if result != str(destination) or not importer.safe_file(destination):
                    raise ValueError("Telegram part could not be saved")
                if destination.stat().st_size != message.file.size:
                    raise ValueError("Telegram part size mismatch")
                downloaded += message.file.size
                write_job(jobid, "running", "downloading",
                          min(85, int(downloaded * 85 / total)),
                          completedParts=i + 1, totalParts=len(parts),
                          downloadedBytes=downloaded, totalBytes=total)
        write_job(jobid, "running", "checking", 90)
        digest = hashlib.sha256()
        for file in sorted(stage.iterdir()):
            with file.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        if digest.hexdigest() != checksum:
            raise ValueError("Downloaded parts do not match caption SHA256")
        write_job(jobid, "running", "importing", 95)
        def run(command, timeout=600):
            return subprocess.run(command, check=False, timeout=timeout,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
        imported = importer.import_backup(stage, base, run)
        write_job(jobid, "completed", "completed", 100, archive=imported["name"],
                  completedParts=len(parts), totalParts=len(parts),
                  downloadedBytes=downloaded, totalBytes=total)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def run_download(jobid):
    if not JOB.fullmatch(jobid):
        raise ValueError("Invalid job ID")
    request = RUN / ("telegram-job-" + jobid + ".json")
    try:
        payload = json.loads(request.read_text())
        if payload.get("jobId") != jobid:
            raise ValueError("Telegram job mismatch")
        asyncio.run(download(jobid, payload["link"]))
    except Exception as exc:
        error = str(exc)[:180] if isinstance(exc, (ValueError, TimeoutError)) else \
            "Telegram download failed; inspect host journal"
        write_job(jobid, "failed", "failed", 0, error=error)
        raise SystemExit(1)
    finally:
        request.unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "login":
        login()
    elif len(sys.argv) == 3 and sys.argv[1] == "download":
        run_download(sys.argv[2])
    else:
        raise SystemExit("Usage: telegram.py login | download JOB_ID")
