#!/usr/bin/env python3
"""No production data; exercise forward-only billing and fresh/upgrade contracts."""
import pathlib
import sqlite3
import sys
import unittest

sys.path.insert(0, str(pathlib.Path("src").resolve()))
from modules.PeerMeterLedger import checkpoint, factor, metered, reset_checkpoint

ROOT = pathlib.Path("src")
def read(path):
    return (ROOT / path).read_text(encoding="utf-8")

class ForwardMetering(unittest.TestCase):
    def test_multiple_rate_changes_freeze_old_traffic(self):
        # 10 actual GB at factor 1 remains billed as 10 for all future time.
        first = checkpoint(7, 3, 1, 2)
        self.assertEqual(metered(7, 3, 2, first)["total"], 10)
        self.assertEqual(metered(11, 4, 2, first)["total"], 20)  # +5*2
        second = checkpoint(11, 4, 2, 3, first)
        self.assertEqual(metered(11, 4, 3, second)["total"], 20)
        self.assertEqual(metered(13, 6, 3, second)["total"], 32)  # +4*3
        self.assertEqual(metered(13, 6, 0.5, second)["total"], 32)
        third = checkpoint(13, 6, 3, 0.5, second)
        self.assertEqual(metered(15, 8, 0.5, third)["total"], 34)

    def test_upload_and_download_are_separately_preserved(self):
        before = checkpoint(10, 20, 2, 3)
        after = metered(12, 24, 3, before)
        self.assertEqual(after["receive"], 26)  # 10*2 + 2*3
        self.assertEqual(after["sent"], 52)      # 20*2 + 4*3
        self.assertEqual(after["total"], 78)

    def test_total_and_partial_resets(self):
        previous = checkpoint(8, 5, 1, 2)
        r = reset_checkpoint(12, 6, 2, previous, "receive")
        self.assertEqual(metered(0, 6, 2, r)["receive"], 0)
        self.assertEqual(metered(0, 6, 2, r)["sent"], 7)
        self.assertEqual(metered(2, 7, 2, r)["total"], 13)
        total = reset_checkpoint(2, 7, 2, r, "total")
        self.assertEqual(metered(0, 0, 2, total)["total"], 0)
        self.assertEqual(metered(1, 1, 2, total)["total"], 4)

    def test_migration_without_snapshot_and_restore(self):
        self.assertEqual(metered(12, 3, 1.5)["total"], 22.5)
        saved = checkpoint(12, 3, 1.5, 2)
        # Simulate SQLite backup/restore: no RAM-only browser preferences.
        with sqlite3.connect(":memory:") as db:
            db.execute("create table snapshots (data text)")
            import json
            db.execute("insert into snapshots values (?)", (json.dumps(saved),))
            restored = json.loads(db.execute("select data from snapshots").fetchone()[0])
        self.assertEqual(metered(12, 3, 2, restored)["total"], 22.5)
        self.assertEqual(metered(14, 4, 2, restored)["total"], 28.5)

    def test_factor_rejected_out_of_bounds(self):
        for invalid in (0, -1, 10.1, "nan", "Infinity", None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                factor(invalid)

    def test_backend_billing_contract(self):
        jobs = read("modules/PeerJobs.py")
        peer = read("modules/Peer.py")
        dashboard = read("dashboard.py")
        config = read("modules/DashboardConfig.py")
        self.assertIn("PeerTrafficMeterState", jobs)
        self.assertIn("self._save_meter(", jobs)
        self.assertIn("self.metered_usage_for(", jobs)
        self.assertIn("self._rebase_weight(", jobs)
        self.assertIn("reset_meter(", peer)
        self.assertIn("return self.configuration.AllPeerJobs.metered_usage_for(", peer)
        self.assertIn('if "traffic_factor" not in data:', dashboard)
        self.assertIn("duration_days, quota_gb, traffic_factor = parse_creation_limits(data)", dashboard)
        self.assertIn("AllPeerJobs.apply_global_factor(", dashboard)
        self.assertIn('"peer_traffic_factor": "1.0"', config)
        self.assertIn('"peer_global_DNS": "1.1.1.1,8.8.8.8,9.9.9.9"', config)
        self.assertIn('"peer_MTU": "1280"', config)
        self.assertIn('"peer_keep_alive": "21"', config)
        self.assertIn('"remote_endpoint": "" if first_install else GetRemoteEndpoint()', config)
        self.assertIn("if not exist:", config)  # never overwrite persisted upgrade values

    def test_ui_setup_schedule_and_backups(self):
        setup = read("static/app/src/views/setup.vue")
        job = read("static/app/src/components/configurationComponents/peerJobs.vue")
        settings = read("static/app/src/components/settingsComponent/peerTrafficFactorDefault.vue")
        manager = read("static/app/src/components/configurationComponents/peerListComponents/peerBulkManager.vue")
        backup = pathlib.Path("deploy/wgdashbackup.sh").read_text()
        for path in ("peerAddModal.vue", "peerCreate.vue"):
            form = read("static/app/src/components/configurationComponents/"+path)
            self.assertNotIn("traffic_factor: 1", form)
            self.assertIn("server default", form.lower())
        self.assertIn("WireGuard tunnel connection address", setup)
        self.assertIn('key:"remote_endpoint"', setup)
        self.assertIn('fetchPost("/api/Welcome_Finish"', setup)
        self.assertIn("this.$route?.params?.id", job)
        self.assertNotIn("Configuration: this.selectedPeer.configuration.Name", job)
        self.assertIn("localJobs", job)
        self.assertIn('key:"peer_traffic_factor"', settings)
        self.assertIn("previous billed consumption remains unchanged", settings.lower())
        self.assertIn("previously billed traffic is", manager.lower())
        self.assertIn('tar -C "$DATA" --exclude=\'./db\' -cf - .', backup)
        self.assertIn("online_sqlite", backup)
        self.assertIn("tar -C \"$stage\" -cf - data etc", backup)

if __name__ == "__main__":
    unittest.main()
