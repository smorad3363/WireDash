"""Validation and metering for peer time/combined-traffic policies.

Quota math uses the persisted raw upstream+downstream GB totals. The weight
changes only the quota decision; raw counters and charts are never rewritten.
"""
import json
from decimal import Decimal, InvalidOperation

MAX_QUOTA_GB = Decimal("1000000")
MAX_WEIGHT = Decimal("10")
MIN_WEIGHT = Decimal("0.1")


def _decimal(value, field):
    if isinstance(value, bool) or value is None:
        raise ValueError("Invalid " + field)
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("Invalid " + field) from None
    if not number.is_finite():
        raise ValueError("Invalid " + field)
    return number


def parse_creation_limits(data):
    """Return (days, quota_gb, weight), with zero meaning no limit."""
    days_raw = _decimal(data.get("duration_days", 0), "duration")
    if days_raw != days_raw.to_integral_value() or not 0 <= days_raw <= 3650:
        raise ValueError("Duration must be an integer from 0 to 3650 days")
    quota = _decimal(data.get("quota_gb", 0), "traffic quota")
    weight = _decimal(data.get("traffic_factor", 1), "traffic weight")
    if not 0 <= quota <= MAX_QUOTA_GB:
        raise ValueError("Quota must be between 0 and 1000000 GB")
    if not MIN_WEIGHT <= weight <= MAX_WEIGHT:
        raise ValueError("Traffic weight must be between 0.1 and 10")
    if quota == 0 and weight != 1:
        raise ValueError("A traffic quota is required when adjusting metering")
    return int(days_raw), quota, weight


def quota_payload(quota, weight):
    return json.dumps({"limit_gb": str(quota), "weight": str(weight)}, separators=(",", ":"))


def parse_quota_payload(value):
    try:
        data = json.loads(value)
        if not isinstance(data, dict):
            raise ValueError("Invalid quota policy")
        quota = _decimal(data.get("limit_gb"), "quota")
        weight = _decimal(data.get("weight"), "traffic weight")
    except (ValueError, TypeError):
        raise ValueError("Invalid quota policy") from None
    if not 0 < quota <= MAX_QUOTA_GB or not MIN_WEIGHT <= weight <= MAX_WEIGHT:
        raise ValueError("Invalid quota policy")
    return quota, weight


def quota_reached(received_gb, sent_gb, value):
    quota, weight = parse_quota_payload(value)
    receive = _decimal(received_gb, "received usage")
    send = _decimal(sent_gb, "sent usage")
    return (receive + send) * weight >= quota
