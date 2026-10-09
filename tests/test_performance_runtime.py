#!/usr/bin/env python3
"""Exercise live-rate snapshots and cached Peer updates with installed dependencies."""
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from modules.Peer import Peer
from modules.WireguardConfiguration import WireguardConfiguration


class RateSamplingTest(unittest.TestCase):
    def test_nonblocking_elapsed_sampling(self):
        config = object.__new__(WireguardConfiguration)
        config.Name = "wg0"
        frames = [
            {"wg0": SimpleNamespace(bytes_recv=10_000_000, bytes_sent=20_000_000)},
            {"wg0": SimpleNamespace(bytes_recv=12_097_152, bytes_sent=23_145_728)},
            {"wg0": SimpleNamespace(bytes_recv=1, bytes_sent=1)},
            {},
        ]
        with patch("modules.WireguardConfiguration.psutil.net_io_counters",
                   side_effect=frames), patch("modules.WireguardConfiguration.time.monotonic",
                                              side_effect=[100.0, 102.0, 103.0]):
            self.assertEqual(config.getRealtimeTrafficUsage(), {"sent": 0, "recv": 0})
            second = config.getRealtimeTrafficUsage()
            self.assertEqual(second, {"recv": 1.0, "sent": 1.5})
            self.assertEqual(config.getRealtimeTrafficUsage(), {"sent": 0, "recv": 0})
            self.assertEqual(config.getRealtimeTrafficUsage(), {"sent": 0, "recv": 0})

    def test_reused_peer_preserves_jobs_and_drops_expired_links(self):
        peer = object.__new__(Peer)
        peer.name = "Before"
        peer.total_receive = 0
        peer.total_sent = 0
        peer.jobs = ["original job"]
        future = SimpleNamespace(ExpireDate=datetime.now() + timedelta(days=1))
        old = SimpleNamespace(ExpireDate=datetime.now() - timedelta(days=1))
        peer.ShareLink = [old, future]
        peer.refreshFromRow({"name": "After", "total_receive": 5, "total_sent": 10})
        self.assertEqual(peer.name, "After")
        self.assertEqual(peer.total_receive, 5)
        self.assertEqual(peer.total_sent, 10)
        self.assertEqual(peer.jobs, ["original job"])
        self.assertEqual(peer.ShareLink, [future])


if __name__ == "__main__":
    unittest.main()
