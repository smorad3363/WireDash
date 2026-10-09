#!/usr/bin/env python3
"""Regression tests for per-peer quotas and creation form contracts."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path("src").resolve()))
from modules.PeerLimits import parse_creation_limits, quota_payload, quota_reached


class QuotaTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(parse_creation_limits({})[0], 0)
        self.assertEqual(str(parse_creation_limits({})[1]), "0")

    def test_sum_received_sent_and_weight(self):
        _, gb, weight = parse_creation_limits({"duration_days": 30, "quota_gb": 50, "traffic_factor": 2})
        policy = quota_payload(gb, weight)
        self.assertFalse(quota_reached(10, 14.99, policy))
        self.assertTrue(quota_reached(10, 15, policy))
        self.assertTrue(quota_reached(25, 0, policy))
        self.assertFalse(quota_reached(0, 20, policy))

    def test_unweighted_and_fractional(self):
        self.assertTrue(quota_reached(4, 6, quota_payload(10, 1)))
        self.assertFalse(quota_reached(4, 6, quota_payload(10, 0.5)))
        self.assertTrue(quota_reached(5, 5, quota_payload(10, 1.5)))

    def test_validate_bad_inputs(self):
        for data in [
            {"duration_days": -1}, {"duration_days": "1.5"}, {"duration_days": 9999},
            {"quota_gb": "nan"}, {"quota_gb": -2}, {"quota_gb": True},
            {"traffic_factor": 2}, {"quota_gb": 20, "traffic_factor": 0},
            {"quota_gb": 20, "traffic_factor": "Infinity"},
        ]:
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_creation_limits(data)

    def test_ui_and_logs_contract(self):
        root = pathlib.Path("src/static/app/src/components/configurationComponents")
        for p in (root/"peerAddModal.vue", root/"peerCreate.vue"):
            content = p.read_text()
            for key in ("duration_days", "quota_gb", "traffic_factor"):
                self.assertIn(key, content)
        jobs = pathlib.Path("src/modules/PeerJobs.py").read_text()
        self.assertIn("quota_reached(", jobs)
        self.assertIn("Traffic quota rule created", jobs)
        self.assertIn("Traffic quota rule updated", jobs)
        self.assertNotIn("f\"{Job.Value}", jobs)
        graph = (root/"peerListComponents/peerDataUsageCharts.vue").read_text()
        self.assertIn("cumu_receive + x.total_receive", graph)
        self.assertIn("cumu_sent + x.total_sent", graph)
        self.assertIn("GetLocale('Data Received')", graph)
        self.assertIn("GetLocale('Data Sent')", graph)


if __name__ == "__main__":
    unittest.main()
