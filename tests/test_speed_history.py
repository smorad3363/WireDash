import sys,uuid,time,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import sqlalchemy as sa
from flask import Flask
from modules.PeerSpeedStore import PeerSpeedStore

class History(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.engine=sa.create_engine('sqlite:///'+self.temp.name+'/db')
        self.store=PeerSpeedStore(self.engine,'wg-test')
        self.app=Flask('history')
        with self.engine.begin() as conn:self.value=self.store.ensure(conn,'peer',0,0)
    def do_reset(self,source='api',success=True):
        headers={'wg-dashboard-apikey':'test-only'} if source=='api' else {}
        with self.app.test_request_context('/api/resetPeerData/wg-test',headers=headers):
            with self.engine.begin() as c:self.store.reset_committed(c,'peer','total',0,0,0,0)
            self.store.ledger_completed('peer');self.store.reset_completed('peer',success)
    def expiry(self,before='2026-12-01T00:00:00Z',after='2027-01-01T00:00:00Z',identifier=None):
        identifier=identifier or str(uuid.uuid4())
        self.store.import_expiry(identifier,'peer',self.value['generation'],time.time(),
            {'source':'api','before':before,'after':after,'job_id':'test-only'})
        return identifier
    def test_both_operation_orders(self):
        self.do_reset();self.expiry()
        s=self.store.snapshot('peer');self.assertTrue(s['last_renewal'])
        events=[e for e in s['history'] if e['kind']=='renewal_observed']
        self.assertEqual(len(events),1);self.assertFalse(events[0]['payment_verified'])
    def test_reverse_order_and_idempotent_projection(self):
        identifier=self.expiry();self.expiry(identifier=identifier);self.do_reset()
        self.assertEqual(len([e for e in self.store.snapshot('peer')['history'] if e['kind']=='renewal_observed']),1)
    def test_manual_reset_not_renewal(self):
        self.do_reset(source='admin');self.expiry()
        self.assertNotIn('last_renewal',self.store.snapshot('peer'))
    def test_failed_restore_not_renewal(self):
        self.do_reset(success=False);self.expiry()
        self.assertNotIn('last_renewal',self.store.snapshot('peer'))
    def test_initial_entitlement_not_renewal(self):
        self.do_reset();self.expiry(before=None)
        self.assertNotIn('last_renewal',self.store.snapshot('peer'))
    def test_backwards_expiry_not_renewal(self):
        self.do_reset();self.expiry(after='2026-11-01T00:00:00Z')
        self.assertNotIn('last_renewal',self.store.snapshot('peer'))
    def test_backup_roundtrip_and_generation(self):
        self.do_reset();document=self.store.export_configuration()
        with self.engine.begin() as c:self.store.forget(c,'peer')
        self.store.restore_configuration(document)
        self.assertEqual(self.store.snapshot('peer')['generation'],self.value['generation'])
        self.assertTrue(self.store.snapshot('peer')['last_reset'])
        with self.assertRaises(ValueError):self.store.restore_configuration(dict(document,configuration='other'))

if __name__=='__main__':unittest.main()
