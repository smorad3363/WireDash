"""One serialized reconciliation per configuration, no new polling threads."""
import json
import subprocess
import time
from .PeerTrafficControl import TrafficControl
from .PeerState import locked_peer_state


class PeerSpeedController:
    def __init__(self, configuration):
        self.configuration = configuration
        self.store = configuration.SpeedStore
        self.tc = TrafficControl(configuration.Name, self.store.kernel_state(), self.store.save_kernel)
        self.status = 'inactive'
        self.last_error = None
        self.retry_at = 0
        self.failures = 0
        self.ifindex = None

    @locked_peer_state
    def tick(self):
        c = self.configuration
        if time.monotonic() < self.retry_at:
            return
        try:
            self._recover_resets()
            self._project_expiry()
            peers=[(p,False) for p in c.getPeersList()]+[(p,True) for p in c.getRestrictedPeersList()]
            desired=self.store.sample_peers(peers)
            # A stopped interface must stay stopped. No systemctl/wg-quick here.
            if not c.getStatus():
                self.status='interface_stopped'
                return
            link=json.loads(self.tc.command('ip','-j','link','show','dev',c.Name))[0]
            if self.ifindex != link['ifindex']:
                self.tc.invalidate()
                self.ifindex=link['ifindex']
            self.tc.reconcile(desired)
            if not any(any(v['caps'].values()) for v in desired.values()) and self.tc.ownership:
                self.tc.cleanup()
            self.status='applied' if any(any(v['caps'].values()) for v in desired.values()) else 'inactive'
            self.last_error=None
            self.failures=0
        except Exception:
            # Errors can contain peer identifiers or shell output; expose a safe
            # diagnostic and log only a transition, not every ten-second sample.
            self.tc.batch=None
            self.failures+=1
            self.status='error'
            self.last_error='Speed rules pending; inspect controller or kernel capabilities'
            self.retry_at=time.monotonic()+min(300, 5*2**min(self.failures,6))
            if self.failures==1:
                from flask import current_app
                current_app.logger.exception('Speed policy reconciliation deferred')

    def _project_expiry(self):
        jobs=self.configuration.AllPeerJobs
        table=getattr(jobs,'speedOutbox',None)
        if table is None:
            return
        with jobs.engine.connect() as conn:
            rows=conn.execute(table.select().where(table.c.Configuration==self.configuration.Name)).mappings().all()
        for row in rows:
            value=json.loads(row['Payload'])
            self.store.import_expiry(row['ID'],row['Peer'],value['generation'],value['at'],value['event'])
            with jobs.engine.begin() as conn:
                conn.execute(table.delete().where(table.c.ID==row['ID']))

    def _recover_resets(self):
        c=self.configuration
        with self.store.engine.connect() as conn:
            rows=conn.execute(self.store.peers.select().where(
                self.store.peers.c.Configuration==c.Name)).mappings().all()
        for row in rows:
            value=json.loads(row['Payload'])
            pending=value.get('pending_reset')
            if not pending:
                continue
            if not pending['ledger_done']:
                if pending.get('raw_receive') is None:
                    continue
                c.AllPeerJobs.reset_metering(c.Name,row['Peer'],pending['raw_receive'],pending['raw_sent'],pending['mode'])
                self.store.ledger_completed(row['Peer'])
            # Never enable/reinstall a peer during recovery. Only recognize
            # restoration that actually completed before the crash.
            found, peer=c.searchPeer(row['Peer'])
            if found and c.getStatus():
                keys=set(self.tc.command(c.Protocol,'show',c.Name,'peers').split())
                if row['Peer'] in keys:
                    self.store.reset_completed(row['Peer'],True)

    @locked_peer_state
    def remove_peer(self,key):
        # Remove rules BEFORE the IP can be leased to a new identity.
        self.tc.remove(key)
