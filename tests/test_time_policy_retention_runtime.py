#!/usr/bin/env python3
"""Disposable SQLite regression for time policies on restricted peers."""
import pathlib
import tempfile
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch
import sqlalchemy as sa
from modules.PeerJobs import PeerJobs
from modules.PeerJob import PeerJob
from benchmark_synthetic_api import make_fake_config

with tempfile.TemporaryDirectory() as tmp:
    cfg = make_fake_config(3, tmp)
    cfg._WireguardConfiguration__wgSave = lambda: (True, None)
    configs = {cfg.Name: cfg}
    settings = SimpleNamespace(GetConfig=lambda *_: (True, 'sqlite'))
    with patch('modules.PeerJobs.ConnectionString', return_value='sqlite:///'+tmp+'/jobs.db'), patch('modules.PeerJobLogger.ConnectionString', return_value='sqlite:///'+tmp+'/logs.db'):
        jobs = PeerJobs(settings, configs, cfg.AllPeerShareLinks)
        cfg.AllPeerJobs = jobs
        key = cfg.Peers[0].id
        rule = PeerJob('retained-date', cfg.Name, key, 'date', 'lgt', '2099-01-01 00:00:00', '', '', 'restrict')
        assert jobs.saveJob(rule)[0]
        for _ in range(11):
            jobs.JobLogger.log(rule.JobID, False, 'Historic lookup failure')
        assert rule.JobID in {r['JobID'] for r in jobs.JobLogger.getFailingJobs()}
        # Startup loads jobs before the configuration registry is populated.
        restarted = PeerJobs(settings, {}, cfg.AllPeerShareLinks)
        assert restarted.searchJobById(rule.JobID)
        with jobs.engine.connect() as conn:
            assert conn.execute(sa.select(jobs.peerJobTable.c.ExpireDate).where(jobs.peerJobTable.c.JobID == rule.JobID)).scalar() is None
        before_logs = jobs.JobLogger.getLogs(cfg.Name)
        with patch('modules.WireguardConfiguration.subprocess.check_output', return_value=b''), patch('modules.WireguardConfiguration.CheckPeerKey', return_value=True):
            assert cfg.restrictPeers([key])[0]
            for _ in range(15):
                jobs.runJob()
            assert jobs.searchJobById(rule.JobID)
            assert len(jobs.JobLogger.getLogs(cfg.Name)) == len(before_logs)
            assert cfg.allowAccessPeers([key])[0]
            jobs.runJob()
            assert jobs.searchJobById(rule.JobID)
            # A real reached deadline still restricts and retires the job.
            rule.Value = (datetime.now() - timedelta(seconds=5)).strftime('%Y-%m-%d %H:%M:%S')
            assert jobs.saveJob(rule)[0]
            jobs.runJob()
            assert not cfg.searchPeer(key)[0]
            assert key in {p.id for p in cfg.getRestrictedPeersList()}
            assert not jobs.searchJobById(rule.JobID)
        with jobs.engine.connect() as conn:
            assert conn.execute(sa.select(jobs.peerJobTable.c.ExpireDate).where(jobs.peerJobTable.c.JobID == rule.JobID)).scalar() is not None
        # Existing non-date stale cleanup is retained.
        other = cfg.Peers[0]
        volume = PeerJob('stale-volume', cfg.Name, other.id, 'total_data', 'lgt', '1000', '', '', 'restrict')
        assert jobs.saveJob(volume)[0]
        for _ in range(11):
            jobs.JobLogger.log(volume.JobID, False, 'Historic volume failure')
        jobs.cleanJob()
        jobs._PeerJobs__getJobs()
        assert not jobs.searchJobById(volume.JobID)
        # Explicit deletion of a future date remains allowed.
        explicit = PeerJob('explicit-delete', cfg.Name, other.id, 'date', 'lgt', '2099-01-01 00:00:00', '', '', 'restrict')
        assert jobs.saveJob(explicit)[0]
        assert jobs.deleteJob(explicit)[0]
        assert not jobs.searchJobById(explicit.JobID)
print('PASS: failing time rules survive startup and restriction; real expiry and explicit deletion still work')
