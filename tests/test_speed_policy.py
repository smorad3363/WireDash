"""Business invariants with an injected clock, no live customer mutations."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from modules.PeerSpeedPolicy import (DEFAULT_POLICY, validate_policy,
    effective_policy, initial_state, sample, reset, desired_caps)


class SpeedPolicy(unittest.TestCase):
    def setUp(self):
        self.policy = validate_policy({**DEFAULT_POLICY, "enabled": True,
                                     "download_mbps": 8, "upload_mbps": 2})

    def test_directions_are_independent(self):
        s = initial_state(0, 0, 0, created=0)
        s = sample(s, 10, 1, 31, 30)
        self.assertEqual(desired_caps(self.policy, s), {"download": 8, "upload": None})
        s = sample(s, 20, 30, 31, 30)
        self.assertEqual(desired_caps(self.policy, s), {"download": 8, "upload": 2})
        self.assertEqual(desired_caps(self.policy, s, True), {"download": None, "upload": None})

    def test_separate_thresholds_not_combined_pool(self):
        s = sample(initial_state(0, 0, 0, created=0), 10, 25, 25, 30)
        self.assertEqual(desired_caps(self.policy, s), {"download": None, "upload": None})

    def test_fixed_period_not_lifetime(self):
        s = initial_state(0, 0, 0, created=0)
        for month in range(6):
            s = sample(s, month * 30 * 86400 + 10, (month + 1) * 8, 0, 30)
            self.assertLess(s["upload"], 30)
        self.assertEqual(s["anchor"], 0)

    def test_restart_does_not_reset_usage(self):
        s = sample(initial_state(0, 0, 0, created=0), 10, 31, 31, 30)
        import json
        restarted = sample(json.loads(json.dumps(s)), 20, 31, 31, 30)
        self.assertEqual(restarted["download"], 31)
        self.assertEqual(restarted["start"], 0)

    def test_zero_to_zero_reset_is_explicit(self):
        s = initial_state(0, 0, 0, created=0)
        s = reset(s, 100, "total", 30)
        self.assertEqual(s["anchor"], 100)
        self.assertEqual(s["reset_generation"], 1)

    def test_partial_reset_preserves_other_direction_and_period(self):
        s = sample(initial_state(0, 0, 0, created=0), 10, 31, 32, 30)
        s = reset(s, 20, "receive", 30)
        self.assertEqual((s["upload"], s["download"], s["start"]), (0, 32, 0))
        s = sample(s, 30, 2, 33, 30)
        self.assertEqual((s["upload"], s["download"]), (2, 33))

    def test_duration_edit_waits_until_boundary(self):
        s = sample(initial_state(0, 0, 0, created=0), 10, 5, 5, 7)
        self.assertEqual(s["end"], 30 * 86400)
        s = sample(s, 30 * 86400, 5, 5, 7)
        self.assertEqual(s["end"], 37 * 86400)

    def test_legacy_migration_does_not_throttle_lifetime_traffic(self):
        s = initial_state(1000, 5000, 6000)
        self.assertTrue(s["incomplete"])
        self.assertEqual(s["download"], 0)
        s = sample(s, 1010, 5001, 6002, 30)
        self.assertEqual((s["upload"], s["download"]), (1, 2))

    def test_clock_rollback_and_counter_rollback_do_not_grant_allowance(self):
        s = sample(initial_state(0, 0, 0, created=0), 10, 31, 31, 30)
        s = sample(s, 5, 0, 0, 30)
        self.assertEqual(s["download"], 31)
        self.assertEqual(s["sampled_at"], 10)
        self.assertTrue(s["incomplete"])

    def test_exemption_does_not_mutate_defaults(self):
        p = effective_policy(self.policy, {"mode": "exempt"})
        self.assertFalse(p["enabled"])
        self.assertTrue(self.policy["enabled"])

    def test_bad_values_never_mean_block(self):
        for key, value in [("download_mbps", 0), ("upload_mbps", -2),
                           ("download_gb", "NaN"), ("enabled", "true"),
                           ("period_days", 1.5), ("upload_mbps", True)]:
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                validate_policy({**self.policy, key: value})
        with self.assertRaises(ValueError):
            validate_policy({**DEFAULT_POLICY, "enabled": True})


if __name__ == "__main__":
    unittest.main()
