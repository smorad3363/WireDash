"""Forward-only weighted billing for WireGuard peer counters.

A checkpoint freezes all previously billed traffic under its old factor.
New transfer bytes are billed at the new factor; total raw counters are NEVER
retroactively multiplied by a replacement factor. Checkpoints live in the
existing wgdashboard_job database and are included in regular online backups.
"""
from decimal import Decimal, InvalidOperation

def amount(value):
    try:
        n = Decimal(str(value or 0))
    except (ValueError, InvalidOperation, TypeError):
        raise ValueError("Invalid traffic counter") from None
    if not n.is_finite() or n < 0:
        raise ValueError("Invalid traffic counter")
    return n

def factor(value):
    n = amount(value)
    if not Decimal("0.1") <= n <= Decimal("10"):
        raise ValueError("Traffic factor must be between 0.1 and 10")
    return n

def metered(raw_receive, raw_sent, weight, snapshot=None):
    """Returns billed receive/sent; snapshot is a saved checkpoint or None.

    Snapshot fields: raw_receive/raw_sent, billed_receive/billed_sent, factor.
    No snapshot means migration from the old all-history times weight logic.
    Unexpected raw-counter regressions are treated conservatively as resets;
    supported UI resets explicitly update checkpoints first.
    """
    received, sent = amount(raw_receive), amount(raw_sent)
    current = factor(weight)
    if snapshot is None:
        return {"receive": float(received * current),
                "sent": float(sent * current),
                "total": float((received + sent) * current)}
    old_receive = amount(snapshot["raw_receive"])
    old_sent = amount(snapshot["raw_sent"])
    prior_receive = amount(snapshot["billed_receive"])
    prior_sent = amount(snapshot["billed_sent"])
    current = factor(snapshot["factor"])
    # A reset may have happened outside the app (e.g. a manual SQL restore).
    # Do not subtract counters or charge a negative delta.
    billed_receive = (prior_receive + (received - old_receive)*current
                      if received >= old_receive else received*current)
    billed_sent = (prior_sent + (sent - old_sent)*current
                   if sent >= old_sent else sent*current)
    return {"receive": float(billed_receive), "sent": float(billed_sent),
            "total": float(billed_receive + billed_sent)}

def checkpoint(raw_receive, raw_sent, old_factor, new_factor, snapshot=None):
    """Atomically freeze old billing at current counters before factor changes."""
    billed = metered(raw_receive, raw_sent, old_factor, snapshot)
    return {
        "raw_receive": str(amount(raw_receive)),
        "raw_sent": str(amount(raw_sent)),
        "billed_receive": str(amount(billed["receive"])),
        "billed_sent": str(amount(billed["sent"])),
        "factor": str(factor(new_factor))
    }

def reset_checkpoint(raw_receive, raw_sent, weight, snapshot, mode):
    """Reset one/both billing directions after the corresponding raw DB reset."""
    if mode not in ("receive", "sent", "total"):
        raise ValueError("Invalid reset mode")
    current = metered(raw_receive, raw_sent, weight, snapshot)
    return {
        "raw_receive": "0" if mode in ("receive","total") else str(amount(raw_receive)),
        "raw_sent": "0" if mode in ("sent","total") else str(amount(raw_sent)),
        "billed_receive": "0" if mode in ("receive","total") else str(amount(current["receive"])),
        "billed_sent": "0" if mode in ("sent","total") else str(amount(current["sent"])),
        "factor": str(factor(weight))
    }
