#!/usr/bin/env python3
"""Offline source-level probes. Never imports or starts the dashboard application.

Run against a checkout: python3 review_probes.py --repo /path/to/WireDash
These probes use real AST-extracted methods with fake records, a fake database
adapter, and stdlib JSON encoding. They are not Flask, SQLite, VPN, or bot
integration tests. No dependency installation or network access is required.
"""
import argparse
import ast
import datetime as dt
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, sentinel


def load_class(root, filename, name, namespace=None):
    tree = ast.parse((root / filename).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), node], type_ignores=[])
    ns = dict(namespace or {})
    exec(compile(ast.fix_missing_locations(module), str(root / filename), "exec"), ns)
    return ns[name]


def encode(value):
    def default(obj):
        if hasattr(obj, "toJson"):
            return obj.toJson()
        if isinstance(obj, dt.datetime):
            return obj.strftime("%Y-%m-%d %H:%M:%S")
        raise TypeError(type(obj).__name__)
    return json.loads(json.dumps(value, default=default))


class Config:
    def __init__(self, name="wg1"):
        self.Name = name
        self.Peers = []
        self.RestrictedPeers = []
        self.calls = 0
        self.configurationInfo = SimpleNamespace(PeerGroups={})

    def getPeersList(self):
        return self.Peers

    def getRestrictedPeersList(self):
        return self.RestrictedPeers

    def toJson(self):
        self.calls += 1
        return {"Name": self.Name, "Status": True, "Protocol": "wg",
                "Info": {"PeerGroups": {}}, "TotalPeers": len(self.Peers),
                "DataUsage": {"Total": 10.0, "Sent": 7.0, "Receive": 3.0}}


class DummyPeer:
    def __init__(self, configuration, number):
        self.configuration = configuration
        self.id = "FAKE-PEER-" + str(number)
        self.name = "Test " + str(number)
        self.allowed_ip = "10.1.0." + str(number % 254 + 1) + "/32"
        self.status = "stopped"
        self.jobs = []

    def metered_usage(self):
        return {"receive": 3.0, "sent": 7.0, "total": 10.0}

    def toJson(self):
        return {**vars(self), "metered_receive": 3.0, "metered_sent": 7.0, "metered_data": 10.0}


class RepairProbes(unittest.TestCase):
    def test_legacy_parent_is_full_object_for_active_and_restricted(self):
        cfg = Config("wg1")
        cfg.Peers = [DummyPeer(cfg, 1)]
        cfg.RestrictedPeers = [DummyPeer(cfg, 2)]
        payload = self.read_model.configuration_info_payload(cfg)
        wire = encode(payload)
        self.assertEqual(set(wire), {"configurationInfo", "configurationPeers", "configurationRestrictedPeers"})
        for group in ("configurationPeers", "configurationRestrictedPeers"):
            peer = wire[group][0]
            self.assertIn("configuration", peer)
            self.assertIsInstance(peer["configuration"], dict)
            self.assertEqual(peer["configuration"], wire["configurationInfo"])
            self.assertEqual(peer["configuration"]["Name"], "wg1")
            self.assertEqual(peer["metered_data"], 10.0)

    def test_snapshots_do_not_cross_configurations(self):
        for name in ("wg0", "wg1", "awg-test"):
            cfg = Config(name)
            cfg.Peers = [DummyPeer(cfg, 1)]
            result = encode(self.read_model.configuration_info_payload(cfg))
            self.assertEqual(result["configurationPeers"][0].get("configuration", {}).get("Name"), name)

    def test_serialization_computes_parent_once(self):
        cfg = Config()
        cfg.Peers = [DummyPeer(cfg, n) for n in range(1000)]
        encode(self.read_model.configuration_info_payload(cfg))
        self.assertEqual(cfg.calls, 1, "Parent must be computed once for the whole legacy response")

    def test_legacy_serializer_does_not_modify_peer_objects(self):
        cfg = Config()
        cfg.Peers = [DummyPeer(cfg, 1)]
        before = dict(vars(cfg.Peers[0]))
        encode(self.read_model.configuration_info_payload(cfg))
        self.assertEqual(vars(cfg.Peers[0]), before)
        self.assertIs(cfg.Peers[0].configuration, cfg)

    def test_ui_stays_paginated_and_omits_parent(self):
        cfg = Config()
        cfg.Peers = [DummyPeer(cfg, n) for n in range(1000)]
        result = encode(self.read_model.ui_configuration_page(cfg, page=2, per_page=50))
        self.assertEqual(result["totalPeers"], 1000)
        self.assertEqual(len(result["configurationPeers"]), 50)
        self.assertEqual(len(result["chartPeers"]), 30)
        self.assertTrue(all("configuration" not in peer for peer in result["configurationPeers"]))

    def test_null_configuration_still_returns_original_error(self):
        tree = ast.parse((self.root / "src/dashboard.py").read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "API_savePeerScheduleJob")
        fn.decorator_list = []
        ns = {
            "request": SimpleNamespace(json={"Job": {"Peer": "FAKE", "Configuration": None}}),
            "WireguardConfigurations": {"wg0": Config("wg0")},
            "ResponseObject": lambda status=True, message=None, data=None, status_code=200: {
                "status": status, "message": message, "data": data, "http": status_code},
        }
        mod = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), fn], type_ignores=[])
        exec(compile(ast.fix_missing_locations(mod), "<save handler>", "exec"), ns)
        result = ns[fn.name]()
        self.assertEqual(result, {"status": False, "message": "Configuration does not exist", "data": None, "http": 200})

    def make_jobs(self):
        manager = object.__new__(self.Jobs)
        manager.Jobs = []
        table = MagicMock()
        table.select.return_value.where.return_value = sentinel.select
        table.update.return_value.values.return_value.where.return_value = sentinel.expire
        manager.peerJobTable = table
        manager.JobLogger = MagicMock()
        manager.JobLogger.getFailingJobs.return_value = [{"JobID": "stale"}]
        manager.engine = MagicMock()
        conn = MagicMock()
        conn.dialect.name = "sqlite"
        manager.engine.begin.return_value.__enter__.return_value = conn
        manager.engine.connect.return_value.__enter__.return_value = conn
        state = {"expired": False}
        rows = []
        for jid, cfg in (("stale", "wg0"), ("keep", "wg1")):
            rows.append(dict(JobID=jid, Configuration=cfg, Peer="FAKE", Field="date", Operator="lgt", Value="2099-01-01 00:00:00", CreationDate=dt.datetime(2026, 1, 1), ExpireDate=None, Action="restrict"))
        def execute(stmt):
            if stmt is sentinel.expire:
                state["expired"] = True
            elif stmt is not sentinel.select:
                raise AssertionError("Unexpected database operation")
            result = MagicMock()
            result.mappings.return_value.fetchall.return_value = [r for r in rows if not (state["expired"] and r["JobID"] == "stale")]
            return result
        conn.execute.side_effect = execute
        # Matches initialization ordering: the first reload happens before
        # WireguardConfigurations is assigned by the current constructor.
        manager._PeerJobs__getJobs()
        return manager

    def test_reload_after_cleanup_refreshes_cached_active_and_restricted_jobs(self):
        for restricted in (False, True):
            with self.subTest(restricted=restricted):
                manager = self.make_jobs()
                cfg, other = Config("wg0"), Config("wg1")
                cached = SimpleNamespace(id="FAKE", jobs=manager.searchJob("wg0", "FAKE"))
                untouched = SimpleNamespace(id="FAKE", jobs=manager.searchJob("wg1", "FAKE"))
                (cfg.RestrictedPeers if restricted else cfg.Peers).append(cached)
                other.Peers.append(untouched)
                manager.WireguardConfigurations = {cfg.Name: cfg, other.Name: other}
                manager.cleanJob()
                manager._PeerJobs__getJobs()
                self.assertEqual(cached.jobs, [], "Cached peers still advertise a job removed by scheduler cleanup")
                self.assertEqual([j.JobID for j in untouched.jobs], ["keep"])
                self.assertEqual([j.JobID for j in manager.Jobs], ["keep"])

    def test_share_link_cache_keeps_null_expiry(self):
        now = dt.datetime.now()
        links = object.__new__(self.Links)
        forever = SimpleNamespace(Configuration="wg1", Peer="FAKE", ExpireDate=None)
        future = SimpleNamespace(Configuration="wg1", Peer="FAKE", ExpireDate=now + dt.timedelta(days=1))
        past = SimpleNamespace(Configuration="wg1", Peer="FAKE", ExpireDate=now - dt.timedelta(days=1))
        links.Links = [forever, future, past]
        self.assertEqual(links.getLink("wg1", "FAKE"), [forever, future])

    def test_peer_refresh_keeps_null_expiry_and_object_identity(self):
        peer = object.__new__(self.Peer)
        forever = SimpleNamespace(ExpireDate=None)
        past = SimpleNamespace(ExpireDate=dt.datetime.now() - dt.timedelta(days=1))
        peer.ShareLink = [forever, past]
        peer.jobs = []
        original = id(peer)
        peer.refreshFromRow({"name": "After", "total_receive": 5})
        self.assertEqual(id(peer), original)
        self.assertEqual(peer.ShareLink, [forever])
        self.assertEqual(peer.name, "After")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--expect-baseline", action="store_true", help="Require the exact known failing baseline; never use this flag as release validation")
    args = parser.parse_args()
    root = args.repo.resolve()
    RepairProbes.root = root
    spec = importlib.util.spec_from_file_location("review_read_model", root / "src/modules/ConfigurationReadModel.py")
    model = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model)
    RepairProbes.read_model = model
    Job = load_class(root, "src/modules/PeerJob.py", "PeerJob", {"datetime": dt.datetime})
    RepairProbes.Jobs = load_class(root, "src/modules/PeerJobs.py", "PeerJobs", {"datetime": dt.datetime, "PeerJob": Job})
    RepairProbes.Peer = load_class(root, "src/modules/Peer.py", "Peer", {"datetime": dt})
    RepairProbes.Links = load_class(root, "src/modules/PeerShareLinks.py", "PeerShareLinks", {"datetime": dt.datetime})
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RepairProbes))
    if args.expect_baseline:
        expected = {
            "test_legacy_parent_is_full_object_for_active_and_restricted",
            "test_snapshots_do_not_cross_configurations",
            "test_reload_after_cleanup_refreshes_cached_active_and_restricted_jobs",
            "test_share_link_cache_keeps_null_expiry",
            "test_peer_refresh_keeps_null_expiry_and_object_identity",
        }
        actual = {test.id().split(".")[-1].split(" ")[0] for test, _ in result.failures + result.errors}
        if result.testsRun == 9 and actual == expected:
            print("BASELINE CONFIRMED: 9 source-level probes; 5 named probes expose known defects.")
            print("This is intentionally NOT a passing release test run.")
            return 0
        print("BASELINE MISMATCH: source has drifted or diagnostic assumptions changed.")
        return 2
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
