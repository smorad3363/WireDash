#!/usr/bin/env python3
"""No-VPS files harmed: timed retention, rollback safety and sent markers."""
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo
sys.path.insert(0, str(pathlib.Path("deploy").resolve()))
import wgdashbackup_retention as retention

class RetentionPolicy(unittest.TestCase):
    def test_one_local_noon_per_day_and_cap_30(self):
        with tempfile.TemporaryDirectory() as root:
            folder = pathlib.Path(root)
            with patch.object(retention,"backup_zone",return_value=ZoneInfo("Europe/Istanbul")):
                dates = []
                for ago in range(36):
                    day = datetime(2026,10,12,tzinfo=ZoneInfo("Europe/Istanbul"))-timedelta(days=ago)
                    for hour in (10,12,12,13,17):
                        minute = 0 if hour!=12 else len(dates)%2*30
                        local=day.replace(hour=hour,minute=minute,second=0)
                        utc=local.astimezone(timezone.utc)
                        name="wgdashboard-"+utc.strftime("%Y%m%d-%H%M%S")+".tar.gz"
                        path=folder/name
                        path.write_bytes(b"fake-test-only")
                        pathlib.Path(str(path)+".sent").touch()
                        pathlib.Path(str(path)+".sha256").write_text("fake")
                        dates.append(path)
                # Imports, safety snapshots, and unsent pending archives are
                # explicitly excluded from destructive disk retention.
                imported=folder/"wgdashboard-imported-20261012-122222-deadbeef.tar.gz"
                imported.write_bytes(b"must-not-delete")
                pres=folder/"pre-restore-20261012-123456.tar.gz"
                pres.write_bytes(b"must-not-delete")
                unsent=folder/"wgdashboard-20261012-221500.tar.gz"
                unsent.write_bytes(b"not-delivered")
                removed=retention.prune(folder)
                self.assertGreater(removed, 50)
                surviving=[p for p in folder.glob("wgdashboard-*.tar.gz")
                           if retention.AUTO.fullmatch(p.name) and
                              pathlib.Path(str(p)+".sent").exists()]
                self.assertEqual(len(surviving),30)
                for p in surviving:
                    stamp=p.name.removeprefix("wgdashboard-").removesuffix(".tar.gz")
                    d=datetime.strptime(stamp,"%Y%m%d-%H%M%S").replace(
                        tzinfo=timezone.utc).astimezone(ZoneInfo("Europe/Istanbul"))
                    self.assertEqual(d.hour,12)
                    self.assertEqual(d.minute,0)
                for p in (imported,pres,unsent):
                    self.assertTrue(p.exists())
                # No message/file deletion was attempted against Telegram.
                self.assertNotIn("deleteMessage", pathlib.Path(
                    "deploy/wgdashbackup_botfiles.py").read_text())
    def test_safety_fallback_if_no_noon_yet(self):
        with tempfile.TemporaryDirectory() as root:
            folder=pathlib.Path(root)
            with patch.object(retention,"backup_zone",return_value=timezone.utc):
                for h in (8,10):
                    p=folder/f"wgdashboard-20261012-{h:02d}0000.tar.gz"
                    p.write_bytes(b"fake")
                    pathlib.Path(str(p)+".sent").touch()
                retention.prune(folder)
                self.assertTrue((folder/"wgdashboard-20261012-100000.tar.gz").exists())
                self.assertFalse((folder/"wgdashboard-20261012-080000.tar.gz").exists())

if __name__=="__main__": unittest.main()
