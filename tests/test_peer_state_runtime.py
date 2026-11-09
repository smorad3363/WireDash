#!/usr/bin/env python3
"""Reproduce missing Address, stale config imports and concurrent bot renewals.

Uses disposable SQLite and real production methods with an in-memory kernel.
No live interface, customer record, API key or volume is used.
"""
import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import unittest

from flask import Flask, request
from modules.PeerState import (locked_configuration_route, PeerConfigurationError,
                               preferred_peer_record, configuration_lock)
from modules.WireguardConfiguration import WireguardConfiguration
from benchmark_synthetic_api import make_fake_config, Provider


class PeerStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_fake_config(3, self.tmp.name)
        self.cfg.DashboardConfig = SimpleNamespace(GetConfig=lambda *args: (True, ''))
        self.cfg.DashboardWebHooks = MagicMock()
        self.cfg._WireguardConfiguration__wgSave = lambda: (True, None)
        self.key = self.cfg.Peers[2].id
        with self.cfg.engine.begin() as c:
            c.execute(self.cfg.peersTable.update().values(private_key='test-only-key'))
        self.cfg.getPeers()

    def rows(self, table):
        with self.cfg.engine.connect() as c:
            return [dict(r) for r in c.execute(table.select()).mappings()]

    def write_config(self, text):
        Path(self.cfg.configPath).write_text('[Interface]\n' + text)
        self.cfg._WireguardConfiguration__configFileModifiedTime = None
        self.cfg.getPeers()

    def test_kernel_missing_route_cannot_erase_assigned_address(self):
        original = self.cfg.searchPeer(self.key)[1]
        address, name, key = original.allowed_ip, original.name, original.private_key
        self.write_config('[Peer]\nPublicKey = ' + self.key + '\n')
        peer = self.cfg.searchPeer(self.key)[1]
        self.assertEqual((peer.allowed_ip, peer.name, peer.private_key), (address, name, key))
        self.assertEqual(self.rows(self.cfg.peersTable)[2]['allowed_ip'], address)

    def test_empty_config_preserves_customer_records(self):
        self.write_config('# interface temporarily has no peers\n')
        self.assertEqual(len(self.cfg.Peers), 3)

    def test_stale_file_cannot_resurrect_restricted_peer(self):
        peer = dict(self.rows(self.cfg.peersTable)[2])
        with patch('modules.WireguardConfiguration.subprocess.check_output', return_value=b''):
            self.assertTrue(self.cfg.restrictPeers([self.key])[0])
        self.write_config('[Peer]\nPublicKey = ' + self.key + '\nAllowedIPs = ' + peer['allowed_ip'] + '\n')
        self.assertFalse(self.cfg.searchPeer(self.key)[0])
        self.assertEqual(len(self.rows(self.cfg.peersRestrictedTable)), 1)

    def test_duplicate_ip_rejected_before_database_or_kernel_change(self):
        first = self.cfg.Peers[2]
        peer = dict(self.rows(self.cfg.peersTable)[2], id='other-test-key')
        before = self.rows(self.cfg.peersTable)
        with patch('modules.WireguardConfiguration.subprocess.check_output') as command:
            ok, data, error = self.cfg.addPeers([peer])
            self.assertFalse(ok)
            self.assertIn('already assigned', error)
            command.assert_not_called()
        self.assertEqual(before, self.rows(self.cfg.peersTable))
        ok, error = self.cfg.validatePeerAddresses([
            {'id': 'third-test-key', 'allowed_ip': first.allowed_ip.split('/')[0]}])
        self.assertFalse(ok)  # 10.x.y.z and 10.x.y.z/32 are the same lease.

    def test_allow_placeholder_recovers_name_key_usage_and_kernel_route(self):
        record = self.rows(self.cfg.peersTable)[2]
        with self.cfg.engine.begin() as c:
            c.execute(self.cfg.peersRestrictedTable.insert().values(record))
            c.execute(self.cfg.peersTable.update().where(self.cfg.peersTable.c.id == self.key).values(
                name='', private_key='', total_receive=0, total_sent=0, total_data=0))
        with patch('modules.WireguardConfiguration.CheckPeerKey', return_value=True), patch.object(self.cfg, '_set_peer_route') as install:
            self.assertTrue(self.cfg.allowAccessPeers([self.key])[0])
            install.assert_called_once()
        restored = self.cfg.searchPeer(self.key)[1]
        self.assertEqual((restored.name, restored.private_key), (record['name'], record['private_key']))
        self.assertGreaterEqual(restored.total_data + restored.cumu_data, record['total_data'])
        self.assertEqual(self.rows(self.cfg.peersRestrictedTable), [])
        with patch('modules.WireguardConfiguration.CheckPeerKey', return_value=True), patch.object(self.cfg, '_set_peer_route') as install:
            self.assertTrue(self.cfg.allowAccessPeers([self.key])[0])
            install.assert_called_once()  # retry also restores a missing kernel peer

    def test_invalid_lease_fails_before_state_change(self):
        with self.cfg.engine.begin() as c:
            c.execute(self.cfg.peersTable.update().where(self.cfg.peersTable.c.id == self.key).values(allowed_ip='N/A'))
        with patch('modules.WireguardConfiguration.CheckPeerKey', return_value=True), patch.object(self.cfg, '_set_peer_route') as install:
            self.assertFalse(self.cfg.allowAccessPeers([self.key])[0])
            install.assert_not_called()
        self.assertEqual(len(self.rows(self.cfg.peersTable)), 3)
        self.assertEqual(self.rows(self.cfg.peersRestrictedTable), [])
        self.cfg.getPeers()
        with self.assertRaises(PeerConfigurationError):
            self.cfg.searchPeer(self.key)[1].downloadPeer()

    def test_partial_creation_rolls_back(self):
        row = dict(self.rows(self.cfg.peersTable)[2], id='new-test-key', allowed_ip='10.250.20.1/32')
        for field in ['total_receive','total_sent','total_data','cumu_receive','cumu_sent','cumu_data','endpoint','status','latest_handshake','remote_endpoint']:
            row.pop(field)
        app = Flask('rollback')
        with app.app_context(), patch('modules.WireguardConfiguration.CheckPeerKey', return_value=True), patch.object(self.cfg, '_set_peer_route', side_effect=RuntimeError('test failure')), patch('modules.WireguardConfiguration.subprocess.check_output', return_value=b''):
            self.assertFalse(self.cfg.addPeers([row])[0])
        self.assertEqual(len(self.rows(self.cfg.peersTable)), 3)

    def test_poller_waits_for_transition(self):
        entered, release, finished = Event(), Event(), Event()
        def transition():
            with configuration_lock(self.cfg):
                entered.set()
                self.assertTrue(release.wait(3))
        def poll():
            self.cfg.getPeers()
            finished.set()
        with ThreadPoolExecutor(2) as pool:
            first = pool.submit(transition)
            self.assertTrue(entered.wait(2))
            second = pool.submit(poll)
            self.assertFalse(finished.wait(0.1))
            release.set()
            first.result()
            second.result()
        self.assertTrue(finished.is_set())

    def test_repeated_real_reset_keeps_identity_and_clears_usage(self):
        peer = self.cfg.searchPeer(self.key)[1]
        expected = (peer.name, peer.private_key, peer.allowed_ip)
        self.cfg.AllPeerJobs.reset_metering = MagicMock()
        with patch.object(self.cfg, 'getPeersTransfer'), patch('modules.WireguardConfiguration.CheckPeerKey', return_value=True), patch('modules.WireguardConfiguration.subprocess.check_output', return_value=b''):
            for _ in range(3):
                ok, message = self.cfg.resetPeerData(self.key, 'total')
                self.assertTrue(ok, message)
                peer = self.cfg.searchPeer(self.key)[1]
                self.assertEqual((peer.name, peer.private_key, peer.allowed_ip), expected)
                self.assertEqual(peer.total_data + peer.cumu_data, 0)
                self.assertEqual(self.rows(self.cfg.peersRestrictedTable), [])
            self.assertEqual(len(self.rows(self.cfg.peersTable)), 3)

    def test_concurrent_bot_create_allocates_different_addresses(self):
        import time
        from itertools import count
        sequence = count()
        @locked_configuration_route({self.cfg.Name: self.cfg})
        def create(configName):
            address = next(iter(next(iter(self.cfg.getAvailableIP(-1)[1].values()))))
            time.sleep(0.03)  # exposes the old allocation/commit race
            row = dict(self.rows(self.cfg.peersTable)[2],
                       id='concurrent-test-' + str(next(sequence)), allowed_ip=address)
            for field in ['total_receive','total_sent','total_data','cumu_receive','cumu_sent','cumu_data','endpoint','status','latest_handshake','remote_endpoint']:
                row.pop(field)
            success, peers, message = self.cfg.addPeers([row])
            self.assertTrue(success, message)
            return address
        with patch('modules.WireguardConfiguration.CheckPeerKey', return_value=True), patch.object(self.cfg, '_set_peer_route'), ThreadPoolExecutor(2) as pool:
            futures = [pool.submit(create, configName=self.cfg.Name) for _ in range(2)]
            addresses = [f.result() for f in futures]
        self.assertEqual(len(set(addresses)), 2)

    def test_live_reset_flask_handler_propagates_restore_failure(self):
        app = Flask('real-reset')
        def response(status=True, message=None, data=None, status_code=200):
            return app.make_response((dict(status=status,message=message,data=data),status_code))
        source_path=Path('src/dashboard.py')
        if not source_path.exists():source_path=Path('dashboard.py')
        node=next(n for n in ast.parse(source_path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='API_resetPeerData')
        ns=dict(app=app,APP_PREFIX='',request=request,ResponseObject=response,WireguardConfigurations={self.cfg.Name:self.cfg},locked_configuration_route=locked_configuration_route)
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'<real bot reset>','exec'),ns)
        with patch.object(self.cfg,'resetPeerData',return_value=(False,'WireGuard restore failed')):
            r=app.test_client().post('/api/resetPeerData/'+self.cfg.Name,json={'id':self.key,'type':'total'})
        self.assertEqual(r.status_code,200)
        self.assertFalse(r.get_json()['status'])
        self.assertEqual(r.get_json()['message'],'WireGuard restore failed')


if __name__ == '__main__':
    unittest.main()
