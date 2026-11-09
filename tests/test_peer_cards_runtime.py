#!/usr/bin/env python3
"""Persistent creation order and read-only policy cards on disposable SQLite."""
from datetime import datetime, timedelta
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from modules.PeerTimeline import PeerTimeline
from modules.PeerCardSummary import card_policy
from modules.ConfigurationReadModel import ui_configuration_page, configuration_info_payload
from benchmark_synthetic_api import make_fake_config


class Cards(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cfg = make_fake_config(5, self.temp.name)
        self.timeline = PeerTimeline(self.cfg.engine, self.cfg.Name)
        self.cfg.PeerTimeline = self.timeline
        self.history = []
        self.cfg.AllPeerJobs.getAllJobs = lambda *_: self.history
        self.newest = self.cfg.Peers[2]

    def test_newest_before_legacy_independent_of_name_ip_and_access(self):
        for p in self.cfg.Peers:
            self.history.append(SimpleNamespace(Peer=p.id, CreationDate=datetime(2020,1,1), Field="date", Value="2099-01-01 00:00:00", Action="restrict", Operator="lgt"))
        with self.cfg.engine.begin() as conn:
            self.timeline.record_created(conn, [self.newest.id])
        result=ui_configuration_page(self.cfg, sort='created_desc')
        self.assertEqual(result['configurationPeers'][0]['id'],self.newest.id)
        self.assertEqual(result['configurationPeers'][0]['creation_basis'],'created')
        original=configuration_info_payload(self.cfg)
        self.assertNotIn('created_at',original['configurationPeers'][0])
        # Restricted or renewed accounts retain the original creation evidence.
        self.cfg.Peers.remove(self.newest)
        self.cfg.RestrictedPeers=[self.newest]
        self.cfg.getRestrictedPeersList=lambda:self.cfg.RestrictedPeers
        self.history.append(SimpleNamespace(Peer=self.cfg.Peers[0].id,CreationDate=datetime.now(), Field="date", Value="2099-01-01 00:00:00", Action="restrict", Operator="lgt"))
        result=ui_configuration_page(self.cfg, sort='created_desc')
        self.assertEqual(result['configurationPeers'][0]['id'],self.newest.id)
        self.assertTrue(result['configurationPeers'][0]['restricted'])

    def test_unknown_old_dates_never_use_upgrade_time(self):
        result=ui_configuration_page(self.cfg, sort='created_desc')
        self.assertTrue(all(p['created_at'] is None for p in result['configurationPeers']))
        with self.cfg.engine.begin() as conn:self.timeline.record_created(conn,[self.newest.id])
        first=ui_configuration_page(self.cfg, sort='created_desc')['configurationPeers'][0]
        self.assertEqual(first['id'],self.newest.id)
        # A restarted configuration reads the same durable creation time.
        self.cfg.PeerTimeline=PeerTimeline(self.cfg.engine,self.cfg.Name)
        again=ui_configuration_page(self.cfg, sort='created_desc')['configurationPeers'][0]
        self.assertEqual(first['created_at'],again['created_at'])

    def test_creation_event_rolls_back_with_failed_provision(self):
        try:
            with self.cfg.engine.begin() as conn:
                self.timeline.record_created(conn,[self.newest.id])
                raise RuntimeError('simulated kernel failure')
        except RuntimeError:pass
        with self.cfg.engine.connect() as conn:
            self.assertEqual(conn.execute(self.timeline.table.select()).all(),[])

    def test_legacy_total_data_quota_shows_actual_remaining(self):
        p=self.newest
        p.total_data=8;p.cumu_data=2
        p.jobs=[SimpleNamespace(Field='total_data',Value='20',Action='restrict',Operator='lgt')]
        result=card_policy(p,False)
        self.assertEqual(result['traffic']['used_gb'],10)
        self.assertEqual(result['traffic']['remaining_gb'],10)
        self.assertEqual(result['traffic']['percent'],50)

    def test_weighted_quota_matches_metered_usage(self):
        p=self.newest
        p.jobs=[SimpleNamespace(Field='quota_total_data',Value='{"limit_gb":"100","weight":"2"}',Action='restrict',Operator='lgt')]
        result=card_policy(p,False)
        self.assertEqual(result['traffic']['remaining_gb'],100-p.metered_usage()['total'])

    def test_restricted_unknown_is_not_unlimited(self):
        p=self.newest;p.jobs=[]
        unknown=card_policy(p,True)
        self.assertEqual(unknown['traffic']['state'],'unknown')
        self.assertEqual(unknown['time']['state'],'unknown')
        expired=SimpleNamespace(Field='date',Value='2020-01-01 00:00:00',CreationDate=datetime(2019,12,1),Action='restrict',Operator='lgt')
        result=card_policy(p,True,[expired])
        self.assertEqual(result['time']['state'],'expired')
        self.assertTrue(result['time']['historical'])
        self.assertEqual(p.jobs,[])  # Read-only; no jobs, access or counters altered.

if __name__=='__main__':unittest.main()
