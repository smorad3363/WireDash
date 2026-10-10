#!/usr/bin/env python3
"""Offline rehearsal: legacy root discovery, multipart uploads, SHA generation."""
from io import BytesIO
import importlib.util
import os
from pathlib import Path
import sqlite3
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path("deploy").resolve()))
import wgdashbackup_import as imp

class ImportBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "root"
        self.out = Path(self.tmp.name) / "archives"
        self.uploads = Path(self.tmp.name) / "uploads"
        for d in (self.root, self.out, self.uploads):
            d.mkdir()
        self.patchers = [
            patch.object(imp, "ROOT", self.root),
            patch.object(imp, "ARCHIVES", self.out),
            patch.object(imp, "UPLOADS", self.uploads),
        ]
        for p in self.patchers:
            p.start()
            self.addCleanup(p.stop)

    def archive(self, path):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "settings.db"
            with sqlite3.connect(db) as conn:
                conn.execute("create table example (id int)")
                conn.execute("insert into example values(1)")
            content = {
                "data/wg-dashboard.ini": b"[Database]\ntype=sqlite\n",
                "compose.yaml": b"services:\n  wgdashboard:\n    image: example\n",
            }
            with tarfile.open(path, "w:gz") as tar:
                for name, body in content.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(body)
                    tar.addfile(info, BytesIO(body))
                tar.add(db, arcname="data/db/settings.db")

    def verify_mock(self, command, **kwargs):
        self.assertEqual(command[:2], [imp.BACKUP_BIN, "--verify"])
        with tarfile.open(command[2], "r:gz") as archive:
            self.assertIn("data/db/settings.db", archive.getnames())
        return True

    def test_root_pre_migrate_import_without_supplied_sha(self):
        original = self.root / "pre-migrate-20261009-230907.tar.gz"
        self.archive(original)
        discovered = imp.visible_root()
        self.assertEqual(len(discovered), 1)
        self.assertEqual(discovered[0]["name"], original.name)
        self.assertTrue(discovered[0]["complete"])
        result = imp.import_backup(self.root, original.name, self.verify_mock)
        promoted = self.out / result["name"]
        self.assertTrue(promoted.exists())
        self.assertTrue(Path(str(promoted) + ".imported").exists())
        self.assertEqual(len(Path(str(promoted) + ".sha256").read_text().split()[0]), 64)
        self.assertEqual(promoted.read_bytes(), original.read_bytes())
        self.assertTrue(original.exists(), "/root source must not be removed")

    def test_consecutive_telegram_parts_assemble_without_sha(self):
        archive = self.root / "wgdashboard-20261009-120000.tar.gz"
        self.archive(archive)
        raw = archive.read_bytes()
        archive.unlink()
        (self.root / (archive.name + ".part-0000")).write_bytes(raw[:len(raw)//2])
        (self.root / (archive.name + ".part-0001")).write_bytes(raw[len(raw)//2:])
        groups = imp.visible_root()
        self.assertEqual(groups[0]["files"], 2)
        self.assertTrue(groups[0]["complete"])
        result = imp.import_backup(self.root, archive.name, self.verify_mock)
        self.assertEqual(result["parts"], 2)
        self.assertEqual((self.out / result["name"]).read_bytes(), raw)

    def test_missing_part_or_symlink_rejected(self):
        (self.root / "old.tar.gz.part-0001").write_bytes(b"x")
        items = imp.visible_root()
        self.assertFalse(items[0]["complete"])
        with self.assertRaises(ValueError):
            imp.import_backup(self.root, "old.tar.gz", self.verify_mock)
        (self.root / "wgdashboard-20261010-111111.tar.gz").symlink_to(
            self.root / "old.tar.gz.part-0001")
        self.assertEqual(len(imp.visible_root()), 1)
        with self.assertRaises(ValueError):
            imp.import_backup(self.root, "../../etc/passwd", self.verify_mock)

    def test_upload_multiple_backup_sets_and_cleanup(self):
        upload_id = "a"*32
        stage = self.uploads / upload_id
        stage.mkdir()
        self.archive(stage / "snapshot-one.tar.gz")
        self.archive(stage / "snapshot-two.tar.gz")
        results = imp.import_upload(upload_id, self.verify_mock)
        self.assertEqual(len(results), 2)
        self.assertFalse(stage.exists())
        self.assertEqual(len(list(self.out.glob("*.tar.gz"))), 2)

    def test_reject_tar_symlinks_and_unsafe_layout(self):
        dangerous = self.root / "danger.tar.gz"
        with tarfile.open(dangerous, "w:gz") as archive:
            link = tarfile.TarInfo("data/passwords")
            link.type = tarfile.SYMTYPE
            link.linkname = "/etc/passwd"
            archive.addfile(link)
        with self.assertRaisesRegex(ValueError, "Unsafe"):
            imp.import_backup(self.root, dangerous.name, self.verify_mock)
        self.assertEqual(len(list(self.out.glob("*.tar.gz"))), 0)

    def test_multi_upload_is_atomic_if_second_archive_is_invalid(self):
        stage = self.uploads / ("c"*32)
        stage.mkdir()
        valid = stage / "one.tar.gz"
        corrupt = stage / "two.tar.gz"
        self.archive(valid)
        corrupt.write_bytes(b"invalid gzip archive")
        # The importer orders by modification time, so first process the
        # valid file and then force failure on the corrupt file.
        os.utime(valid, (1000000200, 1000000200))
        os.utime(corrupt, (1000000100, 1000000100))
        with self.assertRaises(ValueError):
            imp.import_upload("c"*32, self.verify_mock)
        self.assertFalse(stage.exists())
        self.assertEqual(len(list(self.out.glob("*.tar.gz"))), 0)
        self.assertEqual(len(list(self.out.glob("*.sha256"))), 0)

    def test_upload_rejects_extra_files(self):
        stage = self.uploads / ("b"*32)
        stage.mkdir()
        self.archive(stage / "snapshot.tar.gz")
        (stage / "secret.exe").write_text("unrelated")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            imp.import_upload("b"*32, self.verify_mock)
        self.assertFalse(stage.exists())

    def test_contract_paths_and_admin_only_route(self):
        import ast
        content = Path("src/dashboard.py").read_text()
        self.assertIn("def API_UI_SystemBackupImport():", content)
        self.assertIn("if not _backup_ui_authorized():", content)
        self.assertIn('max_bytes = 900 * 1024 * 1024', content)
        self.assertIn('request.files.getlist("files")', content)
        self.assertIn('fields["name"] = data.get("name")', content)
        self.assertIn('operation == "import_upload"', Path("deploy/wgdashbackup-panel-agent.py").read_text())
        compose = Path("deploy/compose.yaml").read_text()
        self.assertIn("/var/lib/wgdashbackup-import:/var/lib/wgdashbackup-import:rw", compose)
        self.assertNotIn(":/root:", compose)
        self.assertNotIn("/var/run/docker.sock", compose)
        sh = Path("deploy/wgdashbackup.sh").read_text()
        self.assertIn('"$pending.imported"', sh)
        for path in ("deploy/wgdashbackup-panel-agent.py", "deploy/wgdashbackup_import.py"):
            ast.parse(Path(path).read_text())

if __name__ == "__main__":
    unittest.main()
