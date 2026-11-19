#!/usr/bin/env python3
"""Offline property, regression and mock end-to-end tests for UDP port sync."""
import importlib.util
import json
from pathlib import Path
import random
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

path = Path(__file__).with_name("wg-port-sync-dev.py")
if not path.exists():
    path = Path(__file__).resolve().parents[1] / "deploy" / "wg-port-sync.py"
spec = importlib.util.spec_from_file_location("wgd_sync", path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def endpoint(actual=51821, mapped=51820, public=51820, ip="172.18.0.2",
             bridge="br-9432899bc386"):
    return mod.discover({
        "Ports": {f"{mapped}/udp": [
            {"HostIp": "0.0.0.0", "HostPort": str(public)},
            {"HostIp": "::", "HostPort": str(public)}]},
        "Networks": {"wgd": {"IPAddress": ip}}},
        actual, [{"dev": bridge}])


class FakeIpt:
    def __init__(self):
        self.rules = set()
        self.history = []
        self.fail_filter = False

    def __call__(self, op, spec, check=False):
        key = tuple(spec)
        self.history.append((op, key))
        if op == "-C":
            return key in self.rules
        if op == "-I":
            if self.fail_filter and spec[0] == "DOCKER-USER":
                return False
            self.rules.add(key)
            return True
        if op == "-D":
            if key not in self.rules:
                return False
            self.rules.remove(key)
            return True
        raise AssertionError(op)


class TestSync(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fake = FakeIpt()
        self.p = mock.patch.object(mod, "ipt", side_effect=self.fake)
        self.p.start()
        self.addCleanup(self.p.stop)
        self.state = Path(self.temp.name) / "state.json"
        old_state = mod.STATE
        mod.STATE = self.state
        self.addCleanup(lambda: setattr(mod, "STATE", old_state))
        self.stored = []
        self.storepatch = mock.patch.object(mod, "save_state",
            side_effect=lambda data: self.stored.append(dict(data)))
        self.storepatch.start()
        self.addCleanup(self.storepatch.stop)

    def test_default_port_differential_unchanged(self):
        self.assertEqual(mod.reconcile(endpoint(51820)), "standard Docker mapping")
        self.assertFalse(self.fake.rules)
        self.assertFalse(self.stored)

    def test_mismatch_installs_two_scoped_rules(self):
        ep = endpoint()
        self.assertEqual(mod.reconcile(ep), "override active")
        self.assertEqual(self.fake.rules, set(map(tuple, mod.rule_pair(ep))))
        self.assertEqual(len(self.stored), 1)
        self.assertEqual(self.stored[0]["public"], 51820)
        self.assertEqual(self.stored[0]["listen"], 51821)

    def test_timer_idempotence_and_docker_recreation(self):
        ep = endpoint()
        mod.reconcile(ep)
        rule_count = len(self.fake.rules)
        mod.reconcile(ep, ep)
        self.assertEqual(len(self.fake.rules), rule_count)
        self.fake.rules.clear()  # Docker restarted and dropped custom rules
        mod.reconcile(ep, ep)
        self.assertEqual(len(self.fake.rules), rule_count)

    def test_port_change_cleans_only_owned_rules(self):
        old = endpoint()
        mod.reconcile(old)
        unrelated = tuple(["DOCKER-USER", "-s", "192.0.2.1", "-j", "RETURN"])
        self.fake.rules.add(unrelated)
        new = endpoint(actual=51822)
        mod.reconcile(new, old)
        self.assertEqual(self.fake.rules, {unrelated, *map(tuple, mod.rule_pair(new))})

    def test_container_ip_changes(self):
        old = endpoint()
        mod.reconcile(old)
        new = endpoint(ip="172.18.0.5", bridge="br-111111111111")
        mod.reconcile(new, old)
        self.assertEqual(self.fake.rules, set(map(tuple, mod.rule_pair(new))))

    def test_rollback_when_filter_insert_fails(self):
        self.fake.fail_filter = True
        with self.assertRaises(RuntimeError):
            mod.reconcile(endpoint())
        self.assertEqual(self.fake.rules, set())
        self.assertFalse(self.stored)

    def test_no_side_effects_dry_check(self):
        self.assertEqual(mod.reconcile(endpoint(), write=False), "override required")
        self.assertEqual(self.fake.rules, set())

    def test_bad_inputs_rejected(self):
        for bad in (0, -1, 65536, "5;rm -rf /", "", "00080", "abc", None, True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                mod.port(bad)
        with self.assertRaises(ValueError):
            mod.discover({"Ports": {"51820/udp": [
                {"HostPort": "51820", "HostIp": "0.0.0.0"}],
                "51830/udp": [{"HostPort": "51830", "HostIp": "0.0.0.0"}]},
                "Networks": {"n": {"IPAddress": "172.18.0.2"}}},
                "51821", [{"dev": "docker0"}])
        with self.assertRaises(ValueError):
            mod.discover({"Ports": {"51820/udp": [
                {"HostPort": "51820", "HostIp": "0.0.0.0"}]},
                "Networks": {"n": {"IPAddress": "172.18.0.2"}}},
                "51821", [{"dev": "br;hack"}])

    def test_property_fuzz_valid_ports_no_host_port_change(self):
        rng = random.Random(31921)
        for _ in range(2000):
            live = rng.randint(1, 65535)
            published = rng.randint(1, 65535)
            ep = endpoint(actual=live, public=published)
            nat, filt = mod.rule_pair(ep)
            self.assertIn(str(published), nat)
            self.assertEqual(nat[-1], f"172.18.0.2:{live}")
            self.assertIn(str(published), filt)
            self.assertIn(str(live), filt)

    def test_discovery_with_fake_docker_and_ip_route(self):
        ep = endpoint()
        net = {
            "Ports": {"51820/udp": [
                {"HostIp": "0.0.0.0", "HostPort": "51820"},
                {"HostIp": "::", "HostPort": "51820"}]},
            "Networks": {"bridge": {"IPAddress": "172.18.0.2"}},
        }
        def command(*args, **kwargs):
            if args[:3] == ("docker", "inspect", "-f"):
                if "{{.State.Running}}" in args: return SimpleNamespace(stdout="true\n")
                return SimpleNamespace(stdout=json.dumps(net))
            if args[:2] == ("docker", "exec"): return SimpleNamespace(stdout="51821\n")
            if args[:2] == ("ip", "-j"): return SimpleNamespace(stdout=json.dumps([{"dev": ep["bridge"]}]))
            raise AssertionError(args)
        with mock.patch.object(mod, "run", side_effect=command):
            self.assertEqual(mod.current(), ep)


if __name__ == "__main__":
    unittest.main(verbosity=2)