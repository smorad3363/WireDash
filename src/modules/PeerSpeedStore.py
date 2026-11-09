"""Additive, portable persistence for private speed settings and factual history.

Transactions are supplied by model hooks where a billing write already exists.
A pending reset is intentionally visible, never guessed to be a paid renewal.
"""
import json
import threading
import time
import uuid
import sqlalchemy as sa
from .PeerSpeedPolicy import DEFAULT_POLICY, validate_policy, effective_policy, initial_state, sample, reset, desired_caps

_LOCK = threading.RLock()


def event_source():
    from flask import has_request_context, request, session
    if not has_request_context():
        return "scheduler"
    return "api" if request.headers.get("wg-dashboard-apikey") else "admin"


class PeerSpeedStore:
    def __init__(self, engine, configuration):
        self.engine, self.configuration = engine, configuration
        metadata = sa.MetaData()
        self.settings = sa.Table('SpeedDefaultsV1', metadata,
            sa.Column('ID', sa.Integer, primary_key=True),
            sa.Column('Revision', sa.Integer, nullable=False),
            sa.Column('Payload', sa.Text, nullable=False))
        self.peers = sa.Table('SpeedPeersV1', metadata,
            sa.Column('Configuration', sa.String(255), primary_key=True),
            sa.Column('Peer', sa.String(255), primary_key=True),
            sa.Column('Generation', sa.String(36), nullable=False),
            sa.Column('Revision', sa.Integer, nullable=False),
            sa.Column('Payload', sa.Text, nullable=False))
        self.events = sa.Table('SpeedEventsV1', metadata,
            sa.Column('ID', sa.String(36), primary_key=True),
            sa.Column('Configuration', sa.String(255), nullable=False, index=True),
            sa.Column('Peer', sa.String(255), nullable=False, index=True),
            sa.Column('Generation', sa.String(36), nullable=False),
            sa.Column('OccurredAt', sa.Float, nullable=False),
            sa.Column('Kind', sa.String(64), nullable=False),
            sa.Column('Payload', sa.Text, nullable=False))
        self.kernel = sa.Table('SpeedKernelV1', metadata,
            sa.Column('Configuration', sa.String(255), primary_key=True),
            sa.Column('Payload', sa.Text, nullable=False))
        metadata.create_all(engine)
        with _LOCK, engine.begin() as conn:
            if conn.execute(self.settings.select().where(self.settings.c.ID == 1)).first() is None:
                conn.execute(self.settings.insert().values(ID=1, Revision=0, Payload=json.dumps(DEFAULT_POLICY)))

    def where(self, key):
        return sa.and_(self.peers.c.Configuration == self.configuration, self.peers.c.Peer == key)

    def defaults(self, conn):
        row = conn.execute(self.settings.select().where(self.settings.c.ID == 1)).mappings().one()
        payload=json.loads(row['Payload'])
        master=payload.pop('_master_enabled',False)
        return {'revision': row['Revision'], 'policy': payload, 'master_enabled':master}

    def read(self, conn, key):
        row = conn.execute(self.peers.select().where(self.where(key))).mappings().first()
        if row is None:
            return None
        return dict(json.loads(row['Payload']), generation=row['Generation'], revision=row['Revision'])

    def write(self, conn, key, value):
        payload = {k:v for k,v in value.items() if k not in ('generation', 'revision')}
        values = dict(Generation=value['generation'], Revision=value['revision'], Payload=json.dumps(payload, allow_nan=False))
        result = conn.execute(self.peers.update().where(self.where(key)).values(**values))
        if not result.rowcount:
            conn.execute(self.peers.insert().values(Configuration=self.configuration, Peer=key, **values))

    def ensure(self, conn, key, received=0, sent=0, created=None):
        value = self.read(conn, key)
        if value is None:
            value = {'generation':str(uuid.uuid4()), 'revision':0, 'mode':'inherit',
                     'state':initial_state(time.time(), received, sent, days=self.defaults(conn)['policy']['period_days'], created=created),
                     'pending_reset':None, 'apply_status':'inactive'}
            self.write(conn, key, value)
        return value

    def record(self, conn, key, value, kind, payload):
        identifier = str(uuid.uuid4())
        conn.execute(self.events.insert().values(ID=identifier,
            Configuration=self.configuration, Peer=key, Generation=value['generation'],
            OccurredAt=time.time(), Kind=kind, Payload=json.dumps(payload, allow_nan=False)))
        return identifier

    def created(self, conn, keys):
        for key in keys:
            conn.execute(self.peers.delete().where(self.where(key)))
            value = self.ensure(conn, key, created=time.time())
            self.record(conn, key, value, 'created', {'source':event_source()})

    def forget(self, conn, key):
        value = self.read(conn, key)
        if value:
            self.record(conn, key, value, 'deleted', {'source':event_source()})
        conn.execute(self.peers.delete().where(self.where(key)))

    def reset_committed(self, conn, key, mode, received, sent, raw_receive=None, raw_sent=None):
        value = self.ensure(conn, key, received, sent)
        policy = effective_policy(self.defaults(conn)['policy'], value)
        value['state'] = sample(value['state'], time.time(), received, sent, policy['period_days'])
        operation = self.record(conn, key, value, 'usage_reset_'+mode,
                                {'source':event_source(), 'stage':'counter_committed'})
        value['pending_reset'] = {'id':operation, 'mode':mode, 'at':time.time(), 'source':event_source(),
                                  'raw_receive':raw_receive, 'raw_sent':raw_sent, 'ledger_done':False}
        self.write(conn, key, value)
        return operation

    def reset_completed(self, key, successful):
        with _LOCK, self.engine.begin() as conn:
            value = self.read(conn, key)
            if not value or not value['pending_reset']:
                return
            pending = value['pending_reset']
            self.record(conn, key, value, 'reset_completed' if successful else 'reset_incomplete',
                        dict(pending))
            if successful:
                policy = effective_policy(self.defaults(conn)['policy'], value)
                value['state'] = reset(value['state'], pending['at'], pending['mode'], policy['period_days'])
                if pending['mode'] == 'total':
                    value['last_reset'] = pending['at']
                value['pending_reset'] = None
                value['revision'] += 1
                self._correlate(conn, key, value)
            self.write(conn, key, value)

    def observe_expiry(self, key, before, after, job_id):
        if before == after:
            return
        with _LOCK, self.engine.begin() as conn:
            value = self.ensure(conn, key)
            self.record(conn, key, value, 'expiry_changed',
                        {'before':before, 'after':after, 'job_id':job_id, 'source':event_source()})

    def snapshot(self, key, limit=50):
        with _LOCK, self.engine.connect() as conn:
            value = self.read(conn, key)
            if value is None:
                return {'mode':'inherit', 'history':[], 'state':None, 'apply_status':'awaiting_sample'}
            rows = conn.execute(self.events.select().where(sa.and_(
                self.events.c.Configuration == self.configuration, self.events.c.Peer == key,
                self.events.c.Generation == value['generation']))
                .order_by(self.events.c.OccurredAt.desc()).limit(limit)).mappings().all()
            return dict(value, policy=effective_policy(self.defaults(conn)['policy'], value),
                        history=[{'at':r['OccurredAt'], 'kind':r['Kind'], **json.loads(r['Payload'])} for r in rows])

    def save_defaults(self, payload, revision, overwrite, master_enabled=None):
        policy = validate_policy(payload)
        if type(revision) is not int or revision < 0:
            raise ValueError('Invalid revision')
        if type(overwrite) is not bool:
            raise ValueError('Explicit preserve/overwrite choice required')
        with _LOCK, self.engine.begin() as conn:
            previous = self.defaults(conn)
            master = previous['master_enabled'] if master_enabled is None else master_enabled
            if type(master) is not bool:
                raise ValueError('Master control must be boolean')
            result = conn.execute(self.settings.update().where(sa.and_(
                self.settings.c.ID == 1, self.settings.c.Revision == revision)).values(
                Revision=revision+1, Payload=json.dumps(dict(policy, _master_enabled=master))))
            if result.rowcount != 1:
                raise ValueError('Settings changed; reload before applying')
            self.record(conn, '__global__', {'generation':'global'}, 'global_policy_changed', {
                'source':event_source(), 'before':previous, 'after':dict(policy,master_enabled=master),
                'overwrite':overwrite, 'counts':self.counts(conn)})
            if overwrite:
                rows = conn.execute(self.peers.select()).mappings().all()
                for row in rows:
                    value = json.loads(row['Payload'])
                    value['mode'] = 'inherit'
                    value.pop('policy', None)
                    conn.execute(self.peers.update().where(sa.and_(
                        self.peers.c.Configuration == row['Configuration'], self.peers.c.Peer == row['Peer']))
                        .values(Revision=row['Revision']+1, Payload=json.dumps(value)))

    def save_peer(self, key, payload, revision):
        if payload.get('mode') not in ('inherit', 'custom', 'exempt'):
            raise ValueError('Invalid mode')
        policy = validate_policy(payload['policy']) if payload['mode'] == 'custom' else None
        with _LOCK, self.engine.begin() as conn:
            value = self.read(conn, key)
            if value is None or value['revision'] != revision:
                raise ValueError('Account changed; reload before applying')
            before={'mode':value['mode'],'policy':value.get('policy')}
            value.update(mode=payload['mode'], revision=revision+1)
            if policy:
                value['policy'] = policy
            else:
                value.pop('policy', None)
            self.record(conn, key, value, 'policy_changed', {'mode':value['mode'], 'source':event_source(), 'before':before, 'policy':policy})
            conn.execute(self.settings.update().where(self.settings.c.ID == 1).values(Revision=self.settings.c.Revision+1))
            self.write(conn, key, value)

    def sample_peers(self, peers):
        """Caller holds configuration lock; one transaction, no subprocesses."""
        desired = {}
        with _LOCK, self.engine.begin() as conn:
            settings = self.defaults(conn)
            defaults = settings['policy']
            rows = conn.execute(self.peers.select().where(self.peers.c.Configuration == self.configuration)).mappings().all()
            existing = {r['Peer']:dict(json.loads(r['Payload']), generation=r['Generation'], revision=r['Revision']) for r in rows}
            for peer, restricted in peers:
                usage = peer.metered_usage()
                value = existing.get(peer.id) or self.ensure(conn, peer.id, usage['receive'], usage['sent'])
                policy = effective_policy(defaults, value)
                if not value['pending_reset']:
                    value['state'] = sample(value['state'], time.time(), usage['receive'], usage['sent'], policy['period_days'])
                desired[peer.id] = {'caps':desired_caps(policy, value['state'], restricted or bool(value['pending_reset']) or not settings['master_enabled']),
                                    'address':peer.allowed_ip, 'generation':value['generation'], 'restricted':restricted}
                self.write(conn, peer.id, value)
        return desired

    def kernel_state(self):
        with self.engine.connect() as conn:
            row = conn.execute(self.kernel.select().where(self.kernel.c.Configuration == self.configuration)).mappings().first()
            return json.loads(row['Payload']) if row else {}

    def save_kernel(self, payload):
        with _LOCK, self.engine.begin() as conn:
            values = {'Payload':json.dumps(payload)}
            changed = conn.execute(self.kernel.update().where(self.kernel.c.Configuration == self.configuration).values(**values))
            if not changed.rowcount:
                conn.execute(self.kernel.insert().values(Configuration=self.configuration, **values))

    def ledger_completed(self, key):
        with _LOCK, self.engine.begin() as conn:
            value = self.read(conn, key)
            if value and value['pending_reset']:
                value['pending_reset']['ledger_done'] = True
                self.write(conn, key, value)

    def import_expiry(self, identifier, key, generation, occurred, payload):
        with _LOCK, self.engine.begin() as conn:
            if conn.execute(self.events.select().where(self.events.c.ID == identifier)).first():
                return
            value = self.read(conn, key)
            if value is None or value['generation'] != generation:
                return
            conn.execute(self.events.insert().values(ID=identifier,
                Configuration=self.configuration, Peer=key, Generation=generation,
                OccurredAt=occurred, Kind='expiry_changed', Payload=json.dumps(payload)))
            self._correlate(conn,key,value)
            self.write(conn,key,value)

    def summaries(self, keys):
        with self.engine.connect() as conn:
            rows = conn.execute(self.peers.select().where(sa.and_(
                self.peers.c.Configuration == self.configuration,
                self.peers.c.Peer.in_(list(keys))))).mappings().all()
            return {r['Peer']:json.loads(r['Payload']) for r in rows}

    def renamed(self, conn, new):
        conn.execute(self.kernel.delete().where(self.kernel.c.Configuration==self.configuration))
        for table in (self.peers, self.events):
            conn.execute(table.update().where(table.c.Configuration == self.configuration).values(Configuration=new))

    def counts(self, conn):
        rows=conn.execute(self.peers.select()).mappings().all()
        modes=[json.loads(r['Payload']).get('mode','inherit') for r in rows]
        return {mode:modes.count(mode) for mode in ('inherit','custom','exempt')}

    def _correlate(self,conn,key,value):
        rows=conn.execute(self.events.select().where(sa.and_(
            self.events.c.Configuration==self.configuration,self.events.c.Peer==key,
            self.events.c.Generation==value['generation'],self.events.c.OccurredAt>=time.time()-1200,
            self.events.c.Kind.in_(['reset_completed','expiry_changed'])))).mappings().all()
        resets=[r for r in rows if r['Kind']=='reset_completed']
        expiries=[r for r in rows if r['Kind']=='expiry_changed']
        for r in resets:
            reset_fact=json.loads(r['Payload'])
            if reset_fact.get('mode')!='total' or reset_fact.get('source')!='api':
                continue
            for e in expiries:
                fact=json.loads(e['Payload'])
                if (fact.get('source')=='api' and fact.get('before') and
                    fact['after']>fact['before'] and abs(e['OccurredAt']-r['OccurredAt'])<=600):
                    renewal_id=str(uuid.uuid5(uuid.NAMESPACE_URL,r['ID']+e['ID']))
                    if conn.execute(self.events.select().where(self.events.c.ID==renewal_id)).first():
                        continue
                    at=max(r['OccurredAt'],e['OccurredAt'])
                    value['last_renewal']=max(value.get('last_renewal',0),at)
                    conn.execute(self.events.insert().values(ID=renewal_id,
                        Configuration=self.configuration,Peer=key,Generation=value['generation'],
                        OccurredAt=at,Kind='renewal_observed',Payload=json.dumps({
                            'source':'api','before':fact['before'],'after':fact['after'],
                            'evidence':'full_reset_and_expiry_extension','payment_verified':False})))

    def export_configuration(self):
        with self.engine.connect() as conn:
            return {'version':1, 'configuration':self.configuration,
                'peers':[dict(r) for r in conn.execute(self.peers.select().where(self.peers.c.Configuration==self.configuration)).mappings()],
                'events':[dict(r) for r in conn.execute(self.events.select().where(self.events.c.Configuration==self.configuration)).mappings()]}

    def restore_configuration(self, document):
        if document.get('version')!=1 or document.get('configuration')!=self.configuration:
            raise ValueError('Speed history backup identity mismatch')
        with _LOCK, self.engine.begin() as conn:
            for table,label in ((self.peers,'peers'),(self.events,'events')):
                rows=document.get(label,[])
                if any(r.get('Configuration')!=self.configuration for r in rows):
                    raise ValueError('Invalid configuration history row')
                conn.execute(table.delete().where(table.c.Configuration==self.configuration))
                if rows:
                    conn.execute(table.insert(),rows)
