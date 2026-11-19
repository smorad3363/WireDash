#!/usr/bin/env python3
"""Safely reconcile Docker's published WireGuard UDP port after in-panel changes.

Host-only; uses Docker metadata and wg show (never reads private keys).
No container recreation and no changes to WireDash API or peer configuration.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

STATE = Path("/run/wiredash-wg-port-sync.json")
CONTAINER = "wgdashboard"
BRIDGE_NAME = re.compile(r"^[A-Za-z0-9_.:@-]{1,15}$")


def port(value):
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]{0,4}", str(value)):
        raise ValueError("invalid WireGuard UDP port")
    number = int(value)
    if number > 65535:
        raise ValueError("WireGuard UDP port outside 1..65535")
    return number


def discover(network, listen, route):
    """Build a validated, redacted endpoint description from Docker state."""
    published = network.get("Ports") or {}
    udp = set()
    for key, bindings in published.items():
        if not key.endswith("/udp") or not bindings:
            continue
        inner = port(key.split("/")[0])
        for binding in bindings:
            bindip = binding.get("HostIp") or "0.0.0.0"
            if ":" in bindip:  # Ignore parallel IPv6 publication; never guess IPv6 NAT.
                continue
            ipaddress.IPv4Address(bindip)
            if bindip.startswith("127."):
                continue  # Loopback uses OUTPUT, not PREROUTING.
            udp.add((port(binding.get("HostPort")), inner, bindip))
    if len(udp) != 1:
        raise ValueError("expected exactly one non-loopback IPv4 UDP publication")
    public, mapped, bindip = udp.pop()
    networks = network.get("Networks") or {}
    ips = {i.get("IPAddress") for i in networks.values() if i.get("IPAddress")}
    if len(ips) != 1:
        raise ValueError("expected one container IPv4 address")
    target_ip = next(iter(ips))
    ipaddress.IPv4Address(target_ip)
    if len(route) != 1 or not BRIDGE_NAME.fullmatch(route[0].get("dev", "")):
        raise ValueError("could not determine one safe Docker bridge interface")
    return dict(public=public, mapped=mapped, listen=port(listen),
                target_ip=target_ip, bridge=route[0]["dev"], bindip=bindip)


def rule_pair(ep):
    """Rules are restricted to this container and the published destination."""
    nat = ["-m", "addrtype", "--dst-type", "LOCAL"]
    if ep["bindip"] != "0.0.0.0":
        nat += ["-d", ep["bindip"]]
    nat += ["!", "-i", ep["bridge"], "-p", "udp", "--dport",
            str(ep["public"]), "-j", "DNAT", "--to-destination",
            f'{ep["target_ip"]}:{ep["listen"]}']
    forward = ["-o", ep["bridge"], "-d", ep["target_ip"], "-p", "udp",
               "--dport", str(ep["listen"]), "-m", "conntrack",
               "--ctorigdstport", str(ep["public"]), "-j", "ACCEPT"]
    return (["-t", "nat", "PREROUTING", *nat], ["DOCKER-USER", *forward])


def run(*args, check=True):
    p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       text=True, check=False, timeout=20)
    if check and p.returncode:
        raise RuntimeError(f"{args[0]} returned {p.returncode}: {p.stderr.strip()[:180]}")
    return p


def ipt(operation, spec, check=False):
    table = ["-t", "nat"] if spec[0] == "-t" else []
    chain_index = 2 if table else 0
    chain = spec[chain_index]
    args = spec[chain_index + 1:]
    return run("iptables", "-w", *table, operation, chain,
               *(["1"] if operation == "-I" else []), *args, check=check).returncode == 0


def ensure(spec):
    if not ipt("-C", spec):
        if not ipt("-I", spec):
            raise RuntimeError("failed to install scoped WireGuard UDP rule")
        return True
    return False


def discard(spec):
    if ipt("-C", spec):
        if not ipt("-D", spec):
            raise RuntimeError("failed to remove previous WireGuard UDP rule")


def load_state(path=STATE):
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError("refuse symlink state")
    data = json.loads(path.read_text())
    return discover({"Ports": {f'{data["mapped"]}/udp':
                           [{"HostIp": data["bindip"], "HostPort": str(data["public"])}]},
                     "Networks": {"known": {"IPAddress": data["target_ip"]}}},
                    data["listen"], [{"dev": data["bridge"]}])


def save_state(value, path=STATE):
    fd, temp = tempfile.mkstemp(prefix=".wg-port-sync-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(value, f, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def reconcile(ep, old=None, write=True):
    """Idempotent, non-destructive reconciliation with rollback on failure."""
    valid = discover({"Ports": {f'{ep["mapped"]}/udp':
                       [{"HostIp": ep["bindip"], "HostPort": str(ep["public"])}]},
                      "Networks": {"known": {"IPAddress": ep["target_ip"]}}},
                     ep["listen"], [{"dev": ep["bridge"]}])
    if old is not None:
        old = discover({"Ports": {f'{old["mapped"]}/udp':
                        [{"HostIp": old["bindip"], "HostPort": str(old["public"])}]},
                       "Networks": {"known": {"IPAddress": old["target_ip"]}}},
                      old["listen"], [{"dev": old["bridge"]}])
    changed = valid["listen"] != valid["mapped"]
    wanted = rule_pair(valid) if changed else None
    previous = rule_pair(old) if old and old["listen"] != old["mapped"] else None
    if not write:
        return "override required" if changed else "standard Docker mapping"
    created = []
    if wanted:
        try:
            for spec in wanted:
                if ensure(spec):
                    created.append(spec)
        except Exception:
            for spec in reversed(created):
                discard(spec)
            raise
    if previous and previous != wanted:
        for spec in reversed(previous):
            discard(spec)
    if wanted:
        save_state(valid)
    elif STATE.exists():
        STATE.unlink()
    return "override active" if changed else "standard Docker mapping"


def current():
    if run("docker", "inspect", "-f", "{{.State.Running}}", CONTAINER).stdout.strip() != "true":
        raise RuntimeError("WireDash container is not running")
    raw = run("docker", "inspect", "-f", "{{json .NetworkSettings}}", CONTAINER).stdout
    network = json.loads(raw)
    listen = run("docker", "exec", CONTAINER, "wg", "show", "wg0",
                 "listen-port").stdout.strip()
    ips = {i.get("IPAddress") for i in (network.get("Networks") or {}).values()
           if i.get("IPAddress")}
    if len(ips) != 1:
        raise RuntimeError("ambiguous container network")
    ip = next(iter(ips))
    route = json.loads(run("ip", "-j", "-4", "route", "get", ip).stdout)
    return discover(network, listen, route)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="read-only inspection")
    args = parser.parse_args()
    try:
        ep = current()
        old = load_state()
        result = reconcile(ep, old, write=not args.check)
        print(f"WireDash UDP {ep['public']} -> container:{ep['listen']}: {result}")
    except (ValueError, KeyError, RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"WireDash UDP sync: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()