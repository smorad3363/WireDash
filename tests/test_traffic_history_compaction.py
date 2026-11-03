#!/usr/bin/env python3
"""Use disposable SQLite fixtures; never modify production traffic."""
import datetime as dt
import pathlib
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path('deploy').resolve()))
import wgdashtraffic_compact as compactor


class TrafficHistoryCompaction(unittest.TestCase):
    def test_preserves_usage_and_first_last_chart_points(self):
        now = dt.datetime(2026, 11, 3, 12, 0, 0)
        with tempfile.TemporaryDirectory() as root:
            db = sqlite3.connect(str(pathlib.Path(root) / 'test.db'))
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE wg0 (id TEXT PRIMARY KEY, total_receive FLOAT, total_sent FLOAT)')
            db.execute('CREATE TABLE wg0_transfer (id TEXT, total_receive FLOAT, total_sent FLOAT, time TEXT)')
            db.execute('INSERT INTO wg0 VALUES ("alice", 10, 20)')
            samples = []
            for day in range(12):
                for hour in range(0, 24, 3):
                    for minute in (0, 10, 20, 30, 40, 50):
                        for peer in ('alice', 'bob'):
                            stamp = now - dt.timedelta(days=day, hours=hour, minutes=minute)
                            samples.append((peer, float(day + hour), 5.0,
                                            stamp.strftime('%Y-%m-%d %H:%M:%S')))
            # Insert as chronological rows, as the live writer does.
            db.executemany('INSERT INTO wg0_transfer VALUES (?,?,?,?)',
                           sorted(samples, key=lambda x: x[3]))
            db.commit()
            before = db.execute('SELECT count(*) FROM wg0_transfer').fetchone()[0]
            old_daily = db.execute(
                "SELECT min(time), max(time) FROM wg0_transfer WHERE time LIKE '2026-10-25%'"
            ).fetchone()
            scanned, kept, removed = compactor.compact_table(db, 'wg0_transfer', now, batch_size=200)
            after = db.execute('SELECT count(*) FROM wg0_transfer').fetchone()[0]
            self.assertGreater(removed, 0)
            self.assertEqual(before - after, removed)
            self.assertEqual(db.execute('SELECT total_receive,total_sent FROM wg0').fetchone(), (10,20))
            self.assertEqual(db.execute(
                "SELECT min(time), max(time) FROM wg0_transfer WHERE time LIKE '2026-10-25%'"
            ).fetchone(), old_daily)
            self.assertGreater(db.execute(
                "SELECT count(*) FROM wg0_transfer WHERE time >= '2026-11-02 12:00:00'"
            ).fetchone()[0], 0)
            second = compactor.compact_table(db, 'wg0_transfer', now, batch_size=200)
            self.assertEqual(second[2], 0)
            db.close()


if __name__ == '__main__':
    unittest.main()
