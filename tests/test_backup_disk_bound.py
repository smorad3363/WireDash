import pathlib
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import timezone

sys.path.insert(0, str(pathlib.Path('deploy').resolve()))
import wgdashbackup_retention as retention


class BackupDiskBound(unittest.TestCase):
    def test_multiple_unsent_keep_only_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = []
            for minute in range(8):
                file = root / f'wgdashboard-20261102-120{minute}00.tar.gz'
                file.write_bytes(b'archive')
                paths.append(file)
            imported = root / 'wgdashboard-imported-20261102-000000-abcd.tar.gz'
            imported.write_bytes(b'kept')
            with patch.object(retention, 'backup_zone', return_value=timezone.utc):
                self.assertEqual(retention.prune(root), 7)
            self.assertEqual([p for p in paths if p.exists()], paths[-1:])
            self.assertTrue(imported.exists())


if __name__ == '__main__':
    unittest.main()
