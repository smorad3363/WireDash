"""Durable-through-container-restarts progress for the host restore job.

The file is written atomically by the root-only host agent and restore worker,
never by the Docker panel. Percentages denote verified milestones, NOT an
estimate of bytes copied or a guaranteed remaining duration.
"""
import json
import os
from pathlib import Path
import re
import tempfile
import time

FILE = Path("/run/wgdashbackup-panel/restore-progress.json")
JOB = re.compile(r"^[a-f0-9]{12}$")
STATES = frozenset(("queued", "running", "completed", "failed"))
PHASES = frozenset((
    "queued", "preparing", "verifying", "extracting",
    "stopping", "snapshotting", "applying", "starting",
    "checking", "completed", "failed",
))


def read_progress():
    try:
        data = json.loads(FILE.read_text())
    except (OSError, ValueError, TypeError):
        return None
    if (not isinstance(data, dict) or
            not JOB.fullmatch(str(data.get("jobId", ""))) or
            data.get("state") not in STATES or
            data.get("phase") not in PHASES or
            type(data.get("percent")) is not int or
            not 0 <= data["percent"] <= 100):
        return None
    return data


def write_progress(job_id, state, percent, phase, archive=None):
    if (not isinstance(job_id, str) or not JOB.fullmatch(job_id)
            or state not in STATES or phase not in PHASES
            or type(percent) is not int or not 0 <= percent <= 100):
        raise ValueError("Invalid restore progress update")
    if archive is not None and (not isinstance(archive, str) or
                                len(archive) > 200 or "/" in archive):
        raise ValueError("Invalid restore archive")
    previous = read_progress()
    if state == "failed" and percent == 0 and previous and previous["jobId"] == job_id:
        percent = previous["percent"]
    if previous and previous["jobId"] != job_id and state != "queued":
        raise ValueError("Restore progress belongs to a different job")
    data = {
        "jobId": job_id,
        "state": state,
        "percent": percent,
        "phase": phase,
        "updatedAt": int(time.time()),
        "archive": archive if archive is not None else (
            previous.get("archive", "") if previous else ""),
    }
    FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, filename = tempfile.mkstemp(prefix=".restore-progress-", dir=FILE.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(data, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(filename, FILE)
    finally:
        if os.path.exists(filename):
            os.unlink(filename)
    return data


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 5:
        raise SystemExit("Usage: progress.py JOB STATE PERCENT PHASE")
    write_progress(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4])
