"""Opt-in, bounded API diagnostics for external WireDash clients.

Logs detailed redacted API request/response bodies and account policy changes.
Private keys, credentials and oversized/binary payloads are never recorded.
"""
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time
import uuid

from flask import g, request, session
from .ApiBodyCapture import capture_query, capture_request, capture_response

LOG_DIR = Path("/data/api-audit")
LOG_FILE = LOG_DIR / "requests.jsonl"
LOCK_FILE = LOG_DIR / ".lock"
MAX_BYTES = 8 * 1024 * 1024
KEEP_FILES = 4
MAX_BODY = 32768
MAX_PEERS = 10
TIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?$")
NUMBER_PATTERN = re.compile(r"^-?\d+(?:\.\d+)?$")
INTERESTING = (
    "/api/allowAccessPeers", "/api/restrictPeers/", "/api/resetPeerData/",
    "/api/updatePeerSettings/", "/api/addPeers/", "/api/deletePeers/",
    "/api/savePeerScheduleJob", "/api/deletePeerScheduleJob",
)


def clean(value, length=120):
    """Keep one printable line, never arbitrary nested content."""
    if not isinstance(value, str):
        return None
    return "".join(c if c.isprintable() else " " for c in value)[:length]


def peer_ref(value):
    if not isinstance(value, str) or not value:
        return None
    return "peer:" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def safe_rule_value(field, value):
    """Only expose known numeric quota and date fields."""
    if value is None:
        return None
    if field == "quota_total_data":
        try:
            data = json.loads(value) if isinstance(value, str) else value
            if not isinstance(data, dict):
                return "<invalid quota shape>"
            parsed = {}
            for key in ("limit_gb", "weight"):
                item = str(data.get(key, ""))
                parsed[key] = item[:30] if NUMBER_PATTERN.fullmatch(item) else "<invalid>"
            return parsed
        except (ValueError, TypeError):
            return "<invalid quota JSON>"
    if field == "date":
        s = str(value)
        if TIME_PATTERN.fullmatch(s):
            return s[:25]
        if s.lower() in ("", "none", "null", "0", "unlimited", "never"):
            return s[:20]
        return "<invalid date value>"
    if field in ("total_data", "total_receive", "total_sent"):
        s = str(value)
        return s[:30] if NUMBER_PATTERN.fullmatch(s) else "<invalid numeric value>"
    return "<redacted unrecognized rule>"


def safe_job(job):
    if not isinstance(job, dict):
        return {"type": type(job).__name__}
    field = job.get("Field")
    if not isinstance(field, str):
        field = None
    return {
        "job_id": peer_ref(job.get("JobID")),
        "peer": peer_ref(job.get("Peer")),
        "configuration": clean(job.get("Configuration"), 80),
        "field": clean(field, 40),
        "operator": clean(job.get("Operator"), 25),
        "value": safe_rule_value(field, job.get("Value")),
        "action": clean(job.get("Action"), 40),
        "creation_date": clean(job.get("CreationDate"), 40),
        "expire_date": clean(job.get("ExpireDate"), 40),
    }


def safe_payload(body):
    if not isinstance(body, dict):
        return {"body_type": type(body).__name__}
    result = {"field_names": sorted([clean(k, 64) for k in body if isinstance(k, str)])[:50]}
    if "Job" in body:
        result["job"] = safe_job(body["Job"])
    for key in ("id", "peer", "public_key"):
        if key in body:
            result[key] = peer_ref(body[key])
    if "peers" in body:
        peers = body["peers"]
        result["peers"] = (
            {"type": "list", "count": len(peers),
             "refs": [peer_ref(p) for p in peers[:MAX_PEERS]]}
            if isinstance(peers, list) else {"type": type(peers).__name__}
        )
    for key in ("configurationName", "configName", "configuration",
                "ConfigurationName", "Configuration", "config_name"):
        if key in body:
            result[key] = clean(body[key], 80)
    for key in ("duration_days", "quota_gb", "traffic_factor", "type",
                "bulkAddAmount", "bulkAdd", "duration", "days", "volume",
                "expiry", "expire_date", "quota", "data_limit", "limit",
                "expiration_date"):
        if key in body:
            value = body[key]
            if type(value) in (int, float, bool) and abs(value) <= 1000000:
                result[key] = value
            elif key == "type" and isinstance(value, str) and len(value) <= 40:
                result[key] = clean(value, 40)
            elif isinstance(value, str) and NUMBER_PATTERN.fullmatch(value):
                result[key] = value[:30]
            else:
                result[key] = "<redacted>"
    return result


def candidate_peers(body):
    if not isinstance(body, dict):
        return []
    job = body.get("Job")
    if isinstance(job, dict) and isinstance(job.get("Peer"), str):
        return [job["Peer"]][:MAX_PEERS]
    result = []
    for key in ("peers",):
        value = body.get(key)
        if isinstance(value, list):
            result.extend(v for v in value if isinstance(v, str) and len(v) <= 300)
    for key in ("id", "peer", "public_key"):
        if isinstance(body.get(key), str):
            result.append(body[key])
    return list(dict.fromkeys(result))[:MAX_PEERS]


def candidate_configs(path, body, view_args, configurations):
    # If the legacy bot omits the configuration name, search only existing
    # configurations for the requested public key; never mutate anything.
    for key in ("configName", "configurationName", "Configuration"):
        value = (view_args or {}).get(key)
        if isinstance(value, str) and value in configurations:
            return [value]
    if isinstance(body, dict):
        job = body.get("Job")
        if isinstance(job, dict):
            value = job.get("Configuration")
            if isinstance(value, str) and value in configurations:
                return [value]
        for key in ("configurationName", "configName", "configuration",
                    "ConfigurationName", "Configuration", "config_name"):
            value = body.get(key)
            if isinstance(value, str) and value in configurations:
                return [value]
    return list(configurations.keys())[:12]


def snapshot(peers, config_names, configurations, jobs):
    if not peers:
        return []
    from sqlalchemy import and_, select
    results = []
    now = dt.datetime.now()
    try:
        with jobs.engine.connect() as jobs_conn:
            for config_name in config_names:
                config = configurations.get(config_name)
                if config is None:
                    continue
                with config.engine.connect() as conn:
                    selected = {}
                    for table, state in (
                        (config.peersTable, "active"),
                        (config.peersRestrictedTable, "restricted"),
                    ):
                        for row in conn.execute(
                            select(table).where(table.c.id.in_(peers))
                        ).mappings():
                            selected[row["id"]] = (state, row)
                    for pid, (state, row) in selected.items():
                        existing_jobs = jobs_conn.execute(
                            select(jobs.peerJobTable).where(and_(
                                jobs.peerJobTable.c.Configuration == config_name,
                                jobs.peerJobTable.c.Peer == pid,
                            )).limit(30)
                        ).mappings().all()
                        policy = []
                        for item in existing_jobs:
                            expires = item.get("ExpireDate")
                            active = expires is None or (isinstance(expires, dt.datetime) and expires > now)
                            if not active:
                                continue
                            field = item.get("Field")
                            policy.append({
                                "field": clean(field, 40),
                                "operator": clean(item.get("Operator"), 20),
                                "value": safe_rule_value(field, item.get("Value")),
                                "action": clean(item.get("Action"), 25),
                            })
                        policy.sort(key=lambda x: (x["field"] or "", x["action"] or ""))
                        received = (float(row.get("total_receive") or 0) +
                                    float(row.get("cumu_receive") or 0))
                        sent = (float(row.get("total_sent") or 0) +
                                float(row.get("cumu_sent") or 0))
                        results.append({
                            "configuration": clean(config_name, 80),
                            "peer": peer_ref(pid),
                            "name": clean(row.get("name"), 100),
                            "state": state,
                            "usage_gb": round(received + sent, 4),
                            "active_limits": policy[:15],
                        })
    except Exception:
        return [{"diagnostics": "snapshot_unavailable"}]
    return sorted(results, key=lambda x: (x.get("configuration", ""), x.get("peer", "")))[:MAX_PEERS]


def append_event(event):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
        lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if LOG_FILE.exists() and LOG_FILE.stat().st_size + len(line.encode()) > MAX_BYTES:
                for number in range(KEEP_FILES, 1, -1):
                    older = Path(str(LOG_FILE) + "." + str(number - 1))
                    if older.is_file() and not older.is_symlink():
                        older.replace(str(LOG_FILE) + "." + str(number))
                LOG_FILE.replace(str(LOG_FILE) + ".1")
            fd = os.open(LOG_FILE, os.O_CREAT | os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
    except OSError:
        # Diagnostics must never break the public API.
        return


def register_api_audit(app, app_prefix, configurations, jobs):
    if os.environ.get("WGD_API_AUDIT", "0") != "1":
        return False
    api_prefix = (app_prefix or "").rstrip("/") + "/api/"

    @app.before_request
    def audit_before():
        if not request.path.startswith(api_prefix) or request.path.endswith("/api/handshake"):
            return
        if request.method == "OPTIONS":
            return
        started = time.monotonic()
        path = request.path
        is_mutation = request.method in ("POST", "PUT", "PATCH", "DELETE")
        safe_body = None
        body = None
        if is_mutation and not any(s in path for s in
                                   ("/api/authenticate", "/api/newDashboardAPIKey")):
            safe_body, body = capture_request(request)
        elif is_mutation:
            safe_body = {"capture": "sensitive_endpoint_omitted",
                         "body_bytes": request.content_length}
        key = request.headers.get("wg-dashboard-apikey")
        event = {
            "utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "request_id": uuid.uuid4().hex[:12],
            "method": request.method,
            "path": path,
            "query_fields": sorted(request.args.keys())[:30],
            "query": capture_query(request.args),
            "content_type": clean(request.mimetype, 100),
            "content_length": request.content_length,
            "headers": {
                "content_type": clean(request.headers.get("Content-Type"), 120),
                "origin": clean(request.headers.get("Origin"), 150),
                "x_forwarded_for": clean(request.headers.get("X-Forwarded-For"), 160),
                "x_request_id": clean(request.headers.get("X-Request-ID"), 120),
            },
            "remote_ip": clean(request.remote_addr, 80),
            "auth_type": "api_key" if key else ("session" if session.get("username") else "none"),
            "api_key_ref": hashlib.sha256(key.encode()).hexdigest()[:12] if key else None,
            "user_agent": clean(request.user_agent.string, 100),
            "payload": safe_body,
        }
        peers = candidate_peers(body) if is_mutation else []
        if is_mutation and peers and any(marker in path for marker in INTERESTING):
            configs = candidate_configs(path, body, request.view_args, configurations)
            event["before"] = snapshot(peers, configs, configurations, jobs)
            g._audit_target = (peers, configs)
        g._audit_event = event
        g._audit_started = started

    @app.after_request
    def audit_after(response):
        event = getattr(g, "_audit_event", None)
        if event is None:
            return response
        event["http_status"] = response.status_code
        event["duration_ms"] = round((time.monotonic() - g._audit_started) * 1000, 2)
        event["response"] = capture_response(
            response, request.method in ("POST", "PUT", "PATCH", "DELETE"), event["path"]
        )
        data = event["response"].get("body")
        if isinstance(data, dict):
            event["api_status"] = data.get("status") if type(data.get("status")) is bool else None
        target = getattr(g, "_audit_target", None)
        if target:
            event["after"] = snapshot(target[0], target[1], configurations, jobs)
        append_event(event)
        return response

    return True
