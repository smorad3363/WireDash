"""Full-backup import from /root or a dedicated multi-file browser upload directory.

Untrusted archives are never restored directly. They are reassembled in a
private host staging directory, bounded, verified by wireback (TAR structure
and SQLite quick_check), hashed and promoted into the managed backup directory.

Do not expose /root or /var/backups to the web container.
"""
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import re
import shutil
import stat
import tarfile
import tempfile
import uuid

ROOT = Path("/root")
UPLOADS = Path("/var/lib/wgdashbackup-import")
ARCHIVES = Path("/var/backups/wgdashboard")
BACKUP_BIN = "/usr/local/bin/wgdashbackup"
# Legacy wireback archives are often sent as .part-0000 through Telegram.
FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.tar\.gz(?:\.part-([0-9]{3,4}))?$")
UPLOAD_ID = re.compile(r"^[a-f0-9]{32}$")
LIMIT_BYTES = 900 * 1024 * 1024
# Full backups can contain multi-GiB SQLite DBs even when their compressed
# Telegram parts are only a few hundred MiB. Bound the SUM of extracted data
# instead of incorrectly rejecting any individual file above 512 MiB.
LIMIT_EXTRACT_BYTES = 12 * 1024 * 1024 * 1024
MIN_FREE_DISK_RESERVE = 1024 * 1024 * 1024
MAX_FILES = 32
MAX_TAR_MEMBERS = 25000


def safe_file(path):
    """Only regular, non-symlink files, including uploaded parts."""
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and info.st_size > 0


def scan(directory):
    """Groups archives and consecutive 3/4-digit Telegram parts by base name."""
    if not directory.is_dir() or directory.is_symlink():
        return []
    groups = {}
    for path in directory.iterdir():
        match = FILENAME.fullmatch(path.name)
        if not match or not safe_file(path):
            continue
        stem = path.name.split(".part-")[0] if match.group(1) else path.name
        group = groups.setdefault(stem, {"single": None, "parts": [], "width": 0})
        if match.group(1):
            group["parts"].append((int(match.group(1)), path))
            group["width"] = len(match.group(1))
        else:
            group["single"] = path
    results = []
    for name, group in groups.items():
        if group["single"]:
            paths = [group["single"]]
            complete = True
        else:
            ordered = sorted(group["parts"], key=lambda item: item[0])
            widths = {len(p.name.rsplit(".part-", 1)[1]) for _, p in ordered}
            complete = (
                len(widths) == 1 and ordered and len(ordered) <= MAX_FILES
                and [x for x, _ in ordered] == list(range(len(ordered)))
            )
            paths = [p for _, p in ordered]
        size = sum(p.lstat().st_size for p in paths)
        if len(paths) > MAX_FILES or size > LIMIT_BYTES or size <= 0:
            complete = False
        results.append({
            "name": name, "files": len(paths), "bytes": size,
            "modified": int(max(p.lstat().st_mtime for p in paths)),
            "complete": bool(complete),
            "_paths": paths,
        })
    return sorted(results, key=lambda x: (-x["modified"], x["name"]))[:100]


def visible_root():
    return [
        {k: v for k, v in group.items() if k != "_paths"}
        for group in scan(ROOT)
    ]


def _copy_parts(paths, destination):
    total = 0
    digest = hashlib.sha256()
    with destination.open("xb") as out:
        for path in paths:
            # O_NOFOLLOW prevents an attacker replacing a staged file with a
            # symlink before this root-owned service opens it.
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError("Upload is not a regular file")
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > LIMIT_BYTES:
                        raise ValueError("Backup exceeds the 900 MiB import limit")
                    digest.update(chunk)
                    out.write(chunk)
    if not total:
        raise ValueError("Empty backup")
    return digest.hexdigest()


def _precheck_tar(path):
    """Bound decompression before invoking the existing legacy verifier."""
    import posixpath
    bytes_total = 0
    with tarfile.open(path, "r:gz") as archive:
        for index, member in enumerate(archive):
            if index >= MAX_TAR_MEMBERS:
                raise ValueError("Backup has too many archive entries")
            name = member.name
            normalized = posixpath.normpath(name)
            if (not name or name.startswith("/") or
                any(part == ".." for part in name.split("/")) or
                normalized in ("..",) or normalized.startswith("../") or
                not (member.isfile() or member.isdir())):
                raise ValueError("Unsafe backup archive entry")
            if not (normalized in (
                ".", "data", "etc", "etc/wireguard", "etc/amnezia",
                "etc/amnezia/amneziawg", "compose.yaml", ".env", "manifest.txt",
            ) or normalized.startswith((
                "data/", "etc/wireguard/", "etc/amnezia/amneziawg/"
            ))):
                raise ValueError("Unsupported full-backup archive layout")
            # Do not remove the extraction bound: this prevents importing a
            # compressed file that could exhaust host disk during verification.
            # Counting directory sizes is harmless; normal TAR dirs have size 0.
            bytes_total += member.size
            if bytes_total > LIMIT_EXTRACT_BYTES:
                raise ValueError("Backup exceeds the 12 GiB extracted-data safety limit")
    return bytes_total


def import_backup(directory, name, execute):
    """Return archive metadata for one successfully imported full backup."""
    if not isinstance(name, str) or not FILENAME.fullmatch(name) or ".part-" in name:
        raise ValueError("Select an archive name, not an individual part")
    found = next((g for g in scan(directory) if g["name"] == name), None)
    if not found:
        raise ValueError("Backup files are missing")
    if not found["complete"]:
        raise ValueError("Multipart backup is incomplete or exceeds the size limit")
    ARCHIVES.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".import-check-", dir=ARCHIVES) as tmp:
        candidate = Path(tmp) / "candidate.tar.gz"
        digest = _copy_parts(found["_paths"], candidate)
        try:
            extracted_size = _precheck_tar(candidate)
        except (tarfile.TarError, EOFError, OSError) as exc:
            raise ValueError("Backup archive is corrupt or unsupported") from exc
        # wireback --verify extracts a second copy under ARCHIVES. Preflight
        # disk space before launching TAR extraction; never guess from the
        # much smaller compressed multipart size.
        free_bytes = shutil.disk_usage(ARCHIVES).free
        reserve_bytes = max(MIN_FREE_DISK_RESERVE, extracted_size // 10)
        if free_bytes < extracted_size + reserve_bytes:
            raise ValueError(
                "Not enough free disk space to verify this backup: "
                "requires about %d MiB free, available %d MiB. "
                "Free disk space and retry; no backup was restored."
                % ((extracted_size + reserve_bytes + 1048575) // 1048576,
                   free_bytes // 1048576))
        if not execute([BACKUP_BIN, "--verify", str(candidate)], timeout=600):
            raise ValueError("TAR/SQLite integrity verification failed; not imported")
        label = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        destination = ARCHIVES / (
            "wgdashboard-imported-" + label + "-" + uuid.uuid4().hex[:8] + ".tar.gz"
        )
        # Candidate is immutable in a root-only temporary directory, and the
        # digest is computed on the exact bytes copied into the archive.
        os.replace(candidate, destination)
        destination.chmod(0o600)
        sidecar = Path(str(destination) + ".sha256")
        sidecar.write_text(digest + "  " + destination.name + "\n")
        sidecar.chmod(0o600)
        marker = Path(str(destination) + ".imported")
        marker.touch(mode=0o600)
        return {"name": destination.name, "bytes": destination.stat().st_size,
                "sha256": digest, "parts": found["files"]}


def uploaded_directory(upload_id):
    if not isinstance(upload_id, str) or not UPLOAD_ID.fullmatch(upload_id):
        raise ValueError("Invalid upload session")
    folder = UPLOADS / upload_id
    if not folder.is_dir() or folder.is_symlink():
        raise ValueError("Uploaded files not available")
    return folder


def import_upload(upload_id, execute):
    directory = uploaded_directory(upload_id)
    try:
        groups = scan(directory)
        if not groups or any(not g["complete"] for g in groups):
            raise ValueError("Select complete archives or consecutive Telegram parts")
        # Reject unrelated extra files, silently dropping none of the upload.
        files = [p for p in directory.iterdir() if p.is_file()]
        if len(files) != sum(g["files"] for g in groups):
            raise ValueError("Unsupported filename included in upload")
        if len(files) > MAX_FILES or sum(p.stat().st_size for p in files) > LIMIT_BYTES:
            raise ValueError("Upload exceeds allowed limits")
        imported = []
        try:
            for group in groups:
                imported.append(import_backup(directory, group["name"], execute))
        except Exception:
            # Multi-upload is all-or-nothing. Remove copies promoted earlier in
            # this batch if a later archive fails validation.
            for item in imported:
                target = ARCHIVES / item["name"]
                for path in (target, Path(str(target) + ".sha256"),
                             Path(str(target) + ".imported")):
                    path.unlink(missing_ok=True)
            raise
        return imported
    finally:
        # These bytes are sensitive. Remove the upload staging directory even
        # when validation fails; the original local /root files are untouched.
        shutil.rmtree(directory, ignore_errors=True)
