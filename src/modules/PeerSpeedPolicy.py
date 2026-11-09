"""Pure, clock-injected speed-period accounting; never mutates billing usage.

All traffic values use the panel's GiB-labelled-GB convention. Rates are Mbps.
The caller serializes transitions with the existing configuration lock.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import math


DEFAULT_POLICY = {
    "enabled": False, "period_days": 30,
    "download_gb": 30.0, "upload_gb": 30.0,
    "download_mbps": None, "upload_mbps": None,
}


def _number(value, field, minimum, maximum):
    if isinstance(value, bool):
        raise ValueError(f"Invalid {field}")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"Invalid {field}") from None
    if not number.is_finite() or not minimum <= number <= maximum:
        raise ValueError(f"Invalid {field}")
    return float(number)


def validate_policy(payload):
    if not isinstance(payload, dict) or set(payload) != set(DEFAULT_POLICY):
        raise ValueError("A complete speed policy is required")
    if type(payload["enabled"]) is not bool:
        raise ValueError("enabled must be boolean")
    out = {"enabled": payload["enabled"]}
    days = _number(payload["period_days"], "period_days", 1, 366)
    if days != int(days):
        raise ValueError("period_days must be an integer")
    out["period_days"] = int(days)
    for direction in ("download", "upload"):
        key = direction + "_gb"
        out[key] = _number(payload[key], key, Decimal("0.000001"), 1000000)
        key = direction + "_mbps"
        # None explicitly denotes unlimited. Zero never means block.
        out[key] = None if payload[key] is None else _number(
            payload[key], key, Decimal("0.001"), 100000)
    if out["enabled"] and all(out[d + "_mbps"] is None for d in ("download", "upload")):
        raise ValueError("Configure at least one reduced speed before enabling")
    return out


def effective_policy(defaults, override):
    mode = override.get("mode", "inherit")
    if mode not in ("inherit", "custom", "exempt"):
        raise ValueError("Invalid policy mode")
    result = validate_policy(override["policy"] if mode == "custom" else defaults)
    if mode == "exempt":
        result["enabled"] = False
    return result


def _counter(value):
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("Invalid traffic counter")
    return number


def initial_state(now, received, sent, days=30, created=None):
    """Legacy traffic establishes a baseline, never a fabricated renewal.

    A genuine creation anchor may be preserved, but pre-observation usage is
    explicitly incomplete. Unknown creation dates remain unknown to the UI.
    """
    now = float(now)
    anchor = float(created) if created is not None else now
    if anchor > now:
        raise ValueError("Creation anchor is in the future")
    length = int(days) * 86400
    start = anchor + int((now - anchor) // length) * length
    return {"anchor": anchor, "start": start, "end": start + length,
            "days": int(days), "download": 0.0, "upload": 0.0,
            "last_receive": _counter(received), "last_sent": _counter(sent),
            "sampled_at": now, "incomplete": created is None or created < now,
            "reset_generation": 0}


def sample(previous, now, received, sent, next_days):
    """Fold cumulative metered deltas once, with fixed, durable boundaries.

    At a crossed boundary, allocate the last interval proportionally by elapsed
    time. This is an estimate, explicitly flagged. A long observation gap (>30s)
    is also incomplete. Repeated samples and backward wall-clock movement never
    grant allowance or subtract observed usage. A duration edit takes effect at
    the next boundary; it never restarts the current period.
    """
    state = deepcopy(previous)
    now = max(float(now), state["sampled_at"])
    received, sent = _counter(received), _counter(sent)
    deltas = {"upload": max(0, received - state["last_receive"]),
              "download": max(0, sent - state["last_sent"])}
    interval = now - state["sampled_at"]
    if interval > 30 or received < state["last_receive"] or sent < state["last_sent"]:
        state["incomplete"] = True
    if now >= state["end"]:
        boundary = state["end"]
        length = int(next_days) * 86400
        state["start"] = boundary + int((now - boundary) // length) * length
        state["end"] = state["start"] + length
        state["days"] = int(next_days)
        fraction = min(1, (now - state["start"]) / interval) if interval else 0
        state["download"] = state["upload"] = 0.0
        state["incomplete"] = bool(interval and any(deltas.values()))
        deltas = {d: value * fraction for d, value in deltas.items()}
    for direction, value in deltas.items():
        state[direction] += value
    state.update(last_receive=max(received, state["last_receive"]),
                 last_sent=max(sent, state["last_sent"]), sampled_at=now)
    return state


def reset(previous, now, mode, days):
    if mode not in ("total", "receive", "sent"):
        raise ValueError("Invalid reset mode")
    if mode == "total":
        state = initial_state(now, 0, 0, days, created=now)
        state["reset_generation"] = previous["reset_generation"] + 1
        return state
    state = deepcopy(previous)
    direction, counter = (("upload", "last_receive") if mode == "receive"
                          else ("download", "last_sent"))
    state[direction] = state[counter] = 0.0
    state["reset_generation"] += 1
    return state


def desired_caps(policy, state, restricted=False):
    return {d: policy[d + "_mbps"] if (
        policy["enabled"] and not restricted and
        state[d] >= policy[d + "_gb"]) else None
        for d in ("download", "upload")}
