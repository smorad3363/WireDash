#!/usr/bin/env python3
"""Test actual restore status milestones and offline SQLite snapshot compaction."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path("deploy/wgdashbackup_progress.py")
SPEC = importlib.util.spec_from_file_location("wgdashbackup_progress", SOURCE)
progress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(progress)


class RestoreAndCompaction(unittest.TestCase):
    def test_milestone_file_is_atomic_and_validated(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.object(progress, "FILE", Path(root) / "restore-progress.json"):
                self.assertIsNone(progress.read_progress())
                first = progress.write_progress("a"*12, "queued", 0, "queued", "old.tar.gz")
                self.assertEqual(first["jobId"], "a"*12)
                second = progress.write_progress("a"*12, "running", 45, "stopping")
                self.assertEqual(second["archive"], "old.tar.gz")
                self.assertEqual(progress.read_progress()["percent"], 45)
                failed = progress.write_progress("a"*12, "failed", 0, "failed")
                self.assertEqual(failed["percent"], 45)
                with self.assertRaises(ValueError):
                    progress.write_progress("../../evil", "running", 70, "applying")
                with self.assertRaises(ValueError):
                    progress.write_progress("b"*12, "completed", 100, "completed")
                self.assertEqual(list(Path(root).glob(".*")), [], "No orphan temp file")
                final = progress.write_progress("b"*12, "queued", 0, "queued", "next.tar.gz")
                self.assertEqual(final["jobId"], "b"*12)

    def test_backup_only_sqlite_reclaims_free_pages_without_changing_live_db(self):
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            inp = base / "live"
            out = base / "backup"
            (inp / "db").mkdir(parents=True)
            out.mkdir()
            (inp / "wg-dashboard.ini").write_text("[Database]\ntype=sqlite\n")
            db = inp / "db" / "peers.db"
            conn = sqlite3.connect(str(db))
            try:
                conn.execute("PRAGMA journal_mode=DELETE")
                conn.execute("CREATE TABLE peers (id INTEGER PRIMARY KEY, data BLOB)")
                # ~32MB database with >20MB deletions.
                payload = b"x" * 3800
                conn.executemany("INSERT INTO peers VALUES (?,?)",
                                 ((i, payload) for i in range(8500)))
                conn.commit()
                conn.execute("DELETE FROM peers WHERE id < 6800")
                conn.commit()
                before_bytes = db.stat().st_size
                unused_pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
                self.assertGreater(unused_pages * 4096, 16 * 1024 * 1024)
            finally:
                conn.close()
            script = Path("deploy/wgdashbackup.sh").read_text()
            code = script.split("online_sqlite() {", 1)[1].split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
            proc = subprocess.run(
                [sys.executable, "-", str(inp), str(out)],
                input=code, text=True, capture_output=True, timeout=40)
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
            offline = out / "peers.db"
            self.assertTrue(offline.exists())
            self.assertEqual(db.stat().st_size, before_bytes, "Live DB file was modified")
            self.assertLess(offline.stat().st_size, before_bytes)
            with sqlite3.connect(str(offline)) as read:
                self.assertEqual(read.execute("SELECT count(*) FROM peers").fetchone()[0], 1700)
                self.assertEqual(read.execute("PRAGMA quick_check").fetchone()[0], "ok")
            self.assertIn("gzip -9", script)
            self.assertIn('tar -tzf "$archive"', script)

    def test_restore_shell_reports_real_phases_without_changing_bot_api(self):
        shell = Path("deploy/wgdashbackup.sh").read_text()
        for state in ("preparing", "verifying", "extracting", "stopping",
                      "snapshotting", "applying", "starting", "checking"):
            self.assertIn("restore_milestone " + state, shell)
        self.assertIn("restore_approved_cleanup", shell)
        self.assertIn("Managed restore: retained current Compose", shell)
        self.assertIn('if [[ "$mode" == approved ]]', shell)
        web = Path("src/static/app/src/components/settingsComponent/systemBackupSettings.vue").read_text()
        self.assertIn("Full restore progress", web)
        self.assertIn("setInterval(pollRestoreProgress", web)
        self.assertIn('cache: "no-store"', web)
        agent = Path("deploy/wgdashbackup-panel-agent.py").read_text()
        self.assertIn('"restoreProgress": read_progress()', agent)
        self.assertIn('"--restore-approved", str(path), digest, job_id', agent)
        self.assertIn('previous["state"] in ("queued", "running")', agent)
        installer = Path("install.sh").read_text()
        self.assertIn("wgdashbackup_progress.py", installer)
        self.assertIn('"/api/getWireguardConfigurationInfo"', Path("src/static/app/src/components/configurationComponents/peerList.vue").read_text())


if __name__ == "__main__":
    unittest.main()
