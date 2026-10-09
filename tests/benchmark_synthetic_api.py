#!/usr/bin/env python3
"""Offline, disposable 5000-peer API concurrency benchmark for WireDash.

Runs INSIDE a separate container with --network none. It does not import
dashboard.py, start a VPN interface, touch persistent volumes, or read host
keys. Each simulated client executes the production configuration read-model
and the real Peer/WireguardConfiguration serialization over loopback HTTP.

Typical:
  python benchmark_synthetic_api.py --peers 5000 --concurrency 50 --requests 150
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import math
import os
import pathlib
import sqlite3
import statistics
import tempfile
import threading
import time
from urllib.request import urlopen

import psutil
import sqlalchemy as sa
from flask import Flask, request
from flask.json.provider import DefaultJSONProvider
from sqlalchemy.pool import NullPool
from werkzeug.serving import make_server

from modules.ConfigurationReadModel import configuration_info_payload
from modules.Peer import Peer
from modules.WireguardConfiguration import WireguardConfiguration


class SyntheticJobs:
    def searchJob(self, configuration, peer):
        return []

    def weight_for(self, configuration, peer):
        # Mixed billing policies exercise the real Decimal metering path.
        # Never print per-peer policy details in logs.
        index = int(peer.rsplit("-", 1)[-1])
        return ("1", "1.5", "2")[index % 3]


class SyntheticLinks:
    def getLink(self, configuration, peer):
        return []


class SyntheticInfo:
    def model_dump(self):
        return {"PeerGroups": {}, "OverridePeerSettings": {}}


class Provider(DefaultJSONProvider):
    def default(self, obj):
        if callable(getattr(obj, "toJson", None)):
            return obj.toJson()
        if isinstance(obj, datetime):
            return obj.strftime("%Y-%m-%d %H:%M:%S")
        return super().default(obj)


def make_fake_config(count, temp_dir):
    """Create an actual temporary SQLite peers table with no real keys."""
    cfg = object.__new__(WireguardConfiguration)
    cfg.Name = "wg-synthetic"
    cfg.Protocol = "wg"
    cfg.Address = "10.250.0.1/16"
    cfg.PrivateKey = ""
    cfg.PublicKey = ""
    cfg.ListenPort = "51820"
    cfg.PreUp = cfg.PreDown = cfg.PostUp = cfg.PostDown = ""
    cfg.SaveConfig = True
    cfg.Table = "auto"
    cfg.Status = True
    cfg.configurationInfo = SyntheticInfo()
    cfg.getStatus = lambda: True
    cfg.AllPeerJobs = SyntheticJobs()
    cfg.AllPeerShareLinks = SyntheticLinks()
    cfg.Peers = []
    cfg.RestrictedPeers = []
    dbpath = pathlib.Path(temp_dir) / "synthetic.sqlite3"
    # A temporary SQLite file, never any WGDashboard DB or server config.
    cfg.engine = sa.create_engine("sqlite:///" + str(dbpath), poolclass=NullPool)
    columns = [
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("private_key", sa.String(255)), sa.Column("DNS", sa.Text),
        sa.Column("endpoint_allowed_ip", sa.Text), sa.Column("name", sa.Text),
        sa.Column("total_receive", sa.Float), sa.Column("total_sent", sa.Float),
        sa.Column("total_data", sa.Float), sa.Column("endpoint", sa.String(255)),
        sa.Column("status", sa.String(255)),
        sa.Column("latest_handshake", sa.String(255)),
        sa.Column("allowed_ip", sa.String(255)),
        sa.Column("cumu_receive", sa.Float), sa.Column("cumu_sent", sa.Float),
        sa.Column("cumu_data", sa.Float), sa.Column("mtu", sa.Integer),
        sa.Column("keepalive", sa.Integer), sa.Column("notes", sa.Text),
        sa.Column("remote_endpoint", sa.String(255)),
        sa.Column("preshared_key", sa.String(255)),
    ]
    meta = sa.MetaData()
    cfg.peersTable = sa.Table("fake_active_peers", meta, *columns)
    cfg.peersRestrictedTable = sa.Table(
        "fake_restricted_peers", meta,
        *[c.copy() for c in columns])
    meta.create_all(cfg.engine)
    rows = []
    for i in range(count):
        # Use clearly non-WireGuard fake IDs; these cannot authenticate.
        # Upload and download are deliberately asymmetric.
        upstream = round((i * 37 % 9500) / 100, 3)
        downstream = round((i * 53 % 12000) / 100, 3)
        rows.append({
            "id": "fake-peer-%06d" % i, "private_key": "",
            "DNS": "1.1.1.1", "endpoint_allowed_ip": "0.0.0.0/0",
            "name": "test-%06d" % i, "total_receive": upstream,
            "total_sent": downstream, "total_data": upstream + downstream,
            "endpoint": "N/A", "status": "stopped",
            "latest_handshake": "No Handshake",
            "allowed_ip": "10.250.%d.%d/32" % ((i // 256) % 256, i % 256),
            "cumu_receive": 0.0, "cumu_sent": 0.0, "cumu_data": 0.0,
            "mtu": 1280, "keepalive": 25, "notes": "",
            "remote_endpoint": "", "preshared_key": "",
        })
    with cfg.engine.begin() as conn:
        for start in range(0, count, 500):
            conn.execute(cfg.peersTable.insert(), rows[start:start + 500])
    cfg.configPath = str(pathlib.Path(temp_dir) / "fake.conf")
    pathlib.Path(cfg.configPath).write_text("[Interface]\n# Synthetic test only\n")
    cfg._WireguardConfiguration__configFileModifiedTime = os.path.getmtime(cfg.configPath)
    cfg.getPeers()  # actual production loader; no 'wg' command or real interface
    if len(cfg.Peers) != count:
        raise RuntimeError("Synthetic peer setup failed")
    return cfg


def make_app(cfg, count):
    app = Flask("wiredash_synthetic_api")
    app.json = Provider(app)

    @app.get("/api/getWireguardConfigurationInfo")
    def get_info():
        if request.args.get("configurationName") != cfg.Name:
            return {"status": False, "data": None, "message": "Not found"}, 404
        return {"status": True, "data": configuration_info_payload(cfg), "message": None}

    @app.get("/api/getWireguardConfigurations")
    def get_configurations():
        return {"status": True, "data": [cfg], "message": None}

    @app.get("/api/benchmark-health")
    def health():
        return {"status": True, "count": count}

    return app


def percentile(values, p):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * (p / 100)
    lower = math.floor(position)
    upper = math.ceil(position)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower), 2)


def summary(samples, count, concurrency, started, proc):
    completed = len(samples)
    ok = [s for s in samples if s["ok"]]
    durations = [s["latency_ms"] for s in ok]
    elapsed = max(0.0001, time.perf_counter() - started)
    result = {
        "peers": count, "concurrency": concurrency,
        "completed": completed, "success": len(ok),
        "errors": completed - len(ok),
        "elapsed_sec": round(elapsed, 3),
        "requests_per_sec": round(completed / elapsed, 2),
        "p50_ms": percentile(durations, 50),
        "p95_ms": percentile(durations, 95),
        "p99_ms": percentile(durations, 99),
        "max_ms": round(max(durations), 2) if durations else None,
        "avg_bytes_per_response": round(
            statistics.mean([s["response_bytes"] for s in ok])) if ok else 0,
        "rss_peak_mb": round(proc.memory_info().rss / 1048576, 2),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peers", type=int, default=5000)
    parser.add_argument("--concurrency", type=int, default=50)
    parser.add_argument("--requests", type=int, default=150)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--json-out", default="", help="optional summary JSON path")
    args = parser.parse_args()
    if not (1 <= args.peers <= 10000 and 1 <= args.concurrency <= 100
            and 1 <= args.requests <= 2000 and 1 <= args.timeout <= 120):
        parser.error("test ranges: peers 1..10000, concurrency 1..100, requests 1..2000, timeout 1..120")
    proc = psutil.Process()
    proc.cpu_percent(None)
    with tempfile.TemporaryDirectory(prefix="wiredash-synthetic-") as temp:
        t0 = time.perf_counter()
        cfg = make_fake_config(args.peers, temp)
        seed_s = time.perf_counter() - t0
        app = make_app(cfg, args.peers)
        # Bind loopback only, never the VPS public interface.
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        endpoint = "http://127.0.0.1:%s" % server.server_port
        samples = []
        guard = threading.Lock()
        stop = threading.Event()
        start_gate = threading.Event()
        def monitor():
            high_water = 0
            while not stop.wait(1.0):
                with guard:
                    n = len(samples)
                    fail = sum(not x["ok"] for x in samples)
                rss = proc.memory_info().rss / 1048576
                high_water = max(high_water, rss)
                print("[LIVE] completed=%d/%d errors=%d cpu_pct=%.1f rss_mb=%.1f" %
                      (n, args.requests, fail, proc.cpu_percent(None), rss), flush=True)
            return high_water

        def worker(i):
            start_gate.wait()
            kind = "list" if i % 10 else "configurations"
            url = endpoint + (
                "/api/getWireguardConfigurationInfo?configurationName=wg-synthetic"
                if kind == "list" else "/api/getWireguardConfigurations")
            begun = time.perf_counter()
            size = 0
            ok = False
            try:
                with urlopen(url, timeout=args.timeout) as res:
                    body = res.read()
                    size = len(body)
                    payload = json.loads(body)
                    ok = (res.status == 200 and payload.get("status") is True
                          and (len(payload["data"]["configurationPeers"]) == args.peers
                               if kind == "list" else len(payload["data"]) == 1))
            except Exception:
                # Avoid leaking a user's environment or URLs into benchmark logs.
                ok = False
            data = {
                "kind": kind, "ok": ok, "latency_ms": round(
                    (time.perf_counter() - begun) * 1000, 2),
                "response_bytes": size,
            }
            with guard:
                samples.append(data)

        print("[SETUP] fake_peers=%d concurrent_clients=%d requests=%d seed_seconds=%.3f" %
              (args.peers, args.concurrency, args.requests, seed_s), flush=True)
        started = time.perf_counter()
        watcher = threading.Thread(target=monitor, daemon=True)
        watcher.start()
        try:
            with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
                futures = [pool.submit(worker, i) for i in range(args.requests)]
                start_gate.set()
                for future in as_completed(futures):
                    future.result()
        finally:
            stop.set()
            watcher.join(timeout=2)
            server.shutdown()
            thread.join(timeout=2)
        result = summary(samples, args.peers, args.concurrency, started, proc)
        result["seed_seconds"] = round(seed_s, 3)
        result["endpoints"] = {
            kind: {
                "requests": sum(s["kind"] == kind for s in samples),
                "errors": sum(s["kind"] == kind and not s["ok"] for s in samples),
                "p95_ms": percentile(
                    [s["latency_ms"] for s in samples
                     if s["kind"] == kind and s["ok"]], 95)
            } for kind in ("list", "configurations")
        }
        print("[SUMMARY] " + json.dumps(result, separators=(",", ":"), sort_keys=True), flush=True)
        if args.json_out:
            pathlib.Path(args.json_out).write_text(json.dumps(result, indent=2) + "\n")
        if result["errors"]:
            return 1
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
