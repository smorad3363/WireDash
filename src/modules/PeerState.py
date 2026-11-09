"""Serialize peer transitions and keep address leases independent of wg showconf.

WireGuard omits AllowedIPs after another peer steals the same route. That is
not a request to erase the customer's assigned address in the database.
"""
from contextlib import ExitStack
from functools import wraps
import ipaddress
import threading

_locks = {}
_guard = threading.Lock()


def configuration_lock(configuration):
    # Shared across replacement configuration objects in the same worker.
    key = (getattr(configuration, "Protocol", "wg"), configuration.Name)
    with _guard:
        return _locks.setdefault(key, threading.RLock())


def locked_peer_state(function):
    @wraps(function)
    def call(self, *args, **kwargs):
        configuration = getattr(self, "configuration", self)
        with configuration_lock(configuration):
            return function(self, *args, **kwargs)
    return call


def locked_configurations(function):
    @wraps(function)
    def call(self, *args, **kwargs):
        with ExitStack() as stack:
            for name, configuration in sorted(self.WireguardConfigurations.items()):
                stack.enter_context(configuration_lock(configuration))
            return function(self, *args, **kwargs)
    return call


def locked_configuration_route(configurations):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            name = kwargs.get("configName")
            configuration = configurations.get(name)
            if configuration is None:
                return function(*args, **kwargs)
            with configuration_lock(configuration):
                return function(*args, **kwargs)
        return call
    return decorate


def address_networks(value):
    try:
        if not isinstance(value, str) or not value.strip():
            return ()
        return tuple(ipaddress.ip_network(part.strip(), strict=False)
                     for part in value.split(","))
    except ValueError:
        return ()


def addresses_overlap(left, right):
    return any(a.version == b.version and a.overlaps(b)
               for a in address_networks(left) for b in address_networks(right))


def preferred_peer_record(active, restricted):
    """A keyless placeholder must never replace a real restricted customer.

    Preserve the larger lifetime total in each direction; these two records
    represent the same key, so adding both would double bill its history.
    """
    if active is None:
        return dict(restricted)
    if restricted is None:
        return dict(active)

    def score(row):
        return (bool(row.get("private_key")), bool((row.get("name") or "").strip()),
                bool(address_networks(row.get("allowed_ip"))))

    chosen = dict(restricted if score(restricted) > score(active) else active)
    for direction in ("receive", "sent"):
        if "total_" + direction not in chosen:
            continue
        total = max(float(row.get("total_" + direction) or 0) +
                    float(row.get("cumu_" + direction) or 0)
                    for row in (active, restricted))
        chosen["cumu_" + direction] = max(
            0, total - float(chosen.get("total_" + direction) or 0))
    if "total_receive" in chosen:
        chosen["total_data"] = sum(float(chosen.get("total_" + d) or 0)
                                   for d in ("receive", "sent"))
        chosen["cumu_data"] = sum(float(chosen.get("cumu_" + d) or 0)
                                  for d in ("receive", "sent"))
    return chosen


class PeerConfigurationError(ValueError):
    """The stored record cannot produce a usable client configuration."""
