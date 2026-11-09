"""Use real SQLite transactions, restart reads and revision conflicts."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import tempfile
import unittest
import sqlalchemy as sa
from modules.PeerSpeedStore import PeerSpeedStore
from modules.PeerSpeedPolicy import DEFAULT_POLICY

class Store(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.engine=sa.create_engine('sqlite:///'+self.temp.name+'/state.db')
        self.store=PeerSpeedStore(self.engine,'wg-test')
    def ensure(self):
        with self.engine.begin() as conn:return self.store.ensure(conn,'peer',10,20)
    def test_idempotent_migration_and_defaults(self):
        PeerSpeedStore(self.engine,'wg-test')
        with self.engine.connect() as c:
            self.assertEqual(self.store.defaults(c),{'revision':0,'policy':DEFAULT_POLICY,'master_enabled':False})
    def test_counter_journal_rolls_back_with_counter_transaction(self):
        self.ensure()
        with self.assertRaises(RuntimeError):
            with self.engine.begin() as c:
                self.store.reset_committed(c,'peer','total',10,20)
                raise RuntimeError('simulated failure')
        self.assertIsNone(self.store.snapshot('peer')['pending_reset'])
        self.assertEqual(self.store.snapshot('peer')['history'],[])
    def test_pending_stage_survives_restart_without_false_renewal(self):
        self.ensure()
        with self.engine.begin() as c:self.store.reset_committed(c,'peer','total',10,20)
        self.store=PeerSpeedStore(self.engine,'wg-test')
        s=self.store.snapshot('peer')
        self.assertIsNotNone(s['pending_reset'])
        self.assertNotIn('renewal',[e['kind'] for e in s['history']])
        self.store.reset_completed('peer',False)
        self.assertIsNotNone(self.store.snapshot('peer')['pending_reset'])
    def test_full_reset_keeps_custom_rule(self):
        value=self.ensure()
        p={**DEFAULT_POLICY,'enabled':True,'download_mbps':8}
        self.store.save_peer('peer',{'mode':'custom','policy':p},value['revision'])
        with self.engine.begin() as c:self.store.reset_committed(c,'peer','total',10,20)
        self.store.reset_completed('peer',True)
        s=self.store.snapshot('peer')
        self.assertIsNone(s['pending_reset']);self.assertEqual(s['mode'],'custom')
        self.assertEqual(s['policy']['download_mbps'],8)
        self.assertEqual(s['state']['download'],0)
    def test_preserve_then_overwrite_and_stale_revision(self):
        v=self.ensure();self.store.save_peer('peer',{'mode':'exempt'},v['revision'])
        self.store.save_defaults(DEFAULT_POLICY,1,False)
        self.assertEqual(self.store.snapshot('peer')['mode'],'exempt')
        with self.assertRaises(ValueError):self.store.save_defaults(DEFAULT_POLICY,0,True)
        self.assertEqual(self.store.snapshot('peer')['mode'],'exempt')
        self.store.save_defaults(DEFAULT_POLICY,2,True)
        self.assertEqual(self.store.snapshot('peer')['mode'],'inherit')
    def test_deleted_recreated_key_never_inherits_old_history(self):
        self.ensure()
        with self.engine.begin() as c:self.store.created(c,['peer'])
        first=self.store.snapshot('peer')['generation']
        with self.engine.begin() as c:
            self.store.forget(c,'peer');self.store.created(c,['peer'])
        s=self.store.snapshot('peer')
        self.assertNotEqual(s['generation'],first)
        self.assertEqual([e['kind'] for e in s['history']],['created'])
    def test_peer_revision_rejects_stale_save(self):
        v=self.ensure();self.store.save_peer('peer',{'mode':'exempt'},v['revision'])
        with self.assertRaises(ValueError):self.store.save_peer('peer',{'mode':'inherit'},v['revision'])

if __name__=='__main__':unittest.main()
