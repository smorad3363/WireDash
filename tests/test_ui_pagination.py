#!/usr/bin/env python3
"""Preserve legacy bot API while exercising pagination, search, sorting, stats."""
import pathlib
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, str(pathlib.Path("src").resolve()))
from modules.ConfigurationReadModel import configuration_info_payload, ui_configuration_page


class FakePeer:
    def __init__(self, n):
        self.id = f"peer-{n:05d}"
        self.name = f"user-{n:05d}"
        self.allowed_ip = f"10.1.{n // 256}.{n % 256}/32"
        self.status = "running" if n % 2 else "stopped"
        self.jobs = []
        self.restricted = False
        self.upload = float(n % 7)
        self.download = float(n % 11)
    def metered_usage(self):
        return {"receive": self.upload, "sent": self.download, "total": self.upload + self.download}
    def toJson(self):
        return {"id": self.id, "name": self.name, "allowed_ip": self.allowed_ip,
                "status": self.status, "jobs": self.jobs,
                "configuration": "must-not-be-in-response",
                "metered_receive": self.upload, "metered_sent": self.download,
                "metered_data": self.upload + self.download}


class FakeConfig:
    def __init__(self, count=5000):
        self.peers = [FakePeer(i) for i in range(count)]
        self.restricted = []
        self.configurationInfo = SimpleNamespace(PeerGroups={})
    def getPeersList(self): return self.peers
    def getRestrictedPeersList(self): return self.restricted


class Contract(unittest.TestCase):
    def test_original_bot_endpoint_unchanged(self):
        cfg = FakeConfig()
        original = configuration_info_payload(cfg)
        self.assertEqual(set(original), {"configurationInfo", "configurationPeers", "configurationRestrictedPeers"})
        self.assertEqual(len(original["configurationPeers"]), 5000)
        self.assertEqual(len(original["configurationRestrictedPeers"]), 0)

    def test_pagination_search_and_sort(self):
        cfg = FakeConfig()
        first = ui_configuration_page(cfg, page=1, per_page=50)
        second = ui_configuration_page(cfg, page=2, per_page=50)
        self.assertEqual(first["totalPeers"], 5000)
        self.assertEqual(first["filteredPeers"], 5000)
        self.assertEqual(len(first["configurationPeers"]), 50)
        self.assertEqual(len(first["chartPeers"]), 30)
        self.assertEqual(first["summary"]["connectedPeers"], 2500)
        self.assertEqual(len(set(p["id"] for p in first["configurationPeers"]) &
                             set(p["id"] for p in second["configurationPeers"])), 0)
        self.assertTrue(all("configuration" not in p for p in first["configurationPeers"]))
        last = ui_configuration_page(cfg, page=100, per_page=50)
        self.assertEqual(last["page"], 100)
        self.assertEqual(len(last["configurationPeers"]), 50)
        search = ui_configuration_page(cfg, query="user-03421")
        self.assertEqual(search["filteredPeers"], 1)
        self.assertEqual(search["configurationPeers"][0]["id"], "peer-03421")
        self.assertEqual(search["summary"]["totalUsage"], first["summary"]["totalUsage"])
        self.assertAlmostEqual(first["summary"]["totalUsage"],
                               first["summary"]["totalReceive"] + first["summary"]["totalSent"])

    def test_restricted_and_tags(self):
        cfg = FakeConfig(5)
        cfg.restricted = [cfg.peers.pop()]
        cfg.configurationInfo.PeerGroups = {"hidden": SimpleNamespace(Peers=["peer-00002"])}
        result = ui_configuration_page(cfg, sort="restricted", hidden_tags={"hidden"})
        self.assertEqual(result["totalPeers"], 5)
        self.assertEqual(result["filteredPeers"], 4)
        self.assertTrue(result["configurationPeers"][0]["restricted"])
        self.assertEqual(result["summary"]["connectedPeers"], 2)
        only_tagged = ui_configuration_page(cfg, show_all_when_hidden=False)
        self.assertEqual(only_tagged["filteredPeers"], 1)


if __name__ == "__main__":
    unittest.main()
