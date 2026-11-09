"""Read-only card facts from existing quota rules and usage; never changes limits."""
from datetime import datetime, timezone
import math

from .PeerLimits import parse_quota_payload


def utc_naive(value, local=False):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None and not local:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(tzinfo=None)



def card_policy(peer, restricted, history=()):
    active = list(getattr(peer, "jobs", ()) or ())
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    dates = [j for j in active if j.Field == "date"]
    historical_time = False
    if not dates and restricted:
        dated = [j for j in history if j.Field == "date"]
        if dated:
            dates = [max(dated, key=lambda j: str(j.CreationDate or ""))]
            historical_time = True
    deadlines = [utc_naive(j.Value, local=True) for j in dates
                 if j.Action in ("restrict", "delete") and j.Operator == "lgt"]
    deadlines = [d for d in deadlines if d is not None]
    expiry = min(deadlines) if deadlines else None
    time_state = ("expired" if expiry <= now else "limited") if expiry else (
        "unknown" if restricted or dates else "unlimited")

    fields = {"quota_total_data", "total_data", "total_receive", "total_sent"}
    rules = [j for j in active if j.Field in fields]
    historical_quota = False
    if not rules and restricted:
        previous = [j for j in history if j.Field in fields]
        if previous:
            rules = [max(previous, key=lambda j: str(j.CreationDate or ""))]
            historical_quota = True
    quotas = []
    for job in rules:
        if job.Action != "restrict" or job.Operator != "lgt":
            continue
        try:
            if job.Field == "quota_total_data":
                limit, _ = parse_quota_payload(job.Value)
                limit = float(limit)
                used = float(peer.metered_usage()["total"])
                kind = "combined"
            else:
                limit = float(job.Value)
                used = float(getattr(peer, job.Field, 0) or 0) + float(
                    getattr(peer, job.Field.replace("total_", "cumu_"), 0) or 0)
                kind = {"total_data": "combined", "total_receive": "upload",
                        "total_sent": "download"}[job.Field]
            if math.isfinite(limit) and math.isfinite(used) and limit > 0 and used >= 0:
                quotas.append(dict(limit_gb=limit, used_gb=used, kind=kind,
                                   remaining_gb=max(0, limit - used),
                                   percent=min(100, used / limit * 100)))
        except (ValueError, TypeError, KeyError):
            continue
    quota = min(quotas, key=lambda q: q["remaining_gb"]) if quotas else None
    quota_state = ("expired" if quota["used_gb"] >= quota["limit_gb"] else "limited") if quota else (
        "unknown" if restricted or rules else "unlimited")
    return {
        "time": {"state": time_state, "historical": historical_time,
                 "expires_at": expiry.isoformat(timespec="seconds") + "Z" if expiry else None,
                 "remaining_seconds": (expiry - now).total_seconds() if expiry else None},
        "traffic": {"state": quota_state, "historical": historical_quota,
                    **(quota or {"limit_gb": None, "used_gb": None,
                                 "remaining_gb": None, "percent": None, "kind": "combined"})},
    }
