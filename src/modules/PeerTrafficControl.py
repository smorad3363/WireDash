"""Owned TC rules on decrypted WireGuard traffic; no host-wide shaping.

Only individual host routes are accepted. Unmatched traffic uses HTB's direct
path without an artificial whole-interface rate ceiling. Never adopt a foreign
root or ingress qdisc, even when it has our preferred handle.
"""
from copy import deepcopy
import hashlib
import ipaddress
import json
import re
import subprocess
import tempfile
import shlex


class TrafficControl:
    def __init__(self, interface, ownership, save_ownership, run=None):
        if not re.fullmatch(r'[a-zA-Z0-9_=+.-]{1,15}', interface):
            raise ValueError('Invalid interface')
        self.interface = interface
        self.ifb = 'wd' + hashlib.sha256(interface.encode()).hexdigest()[:10]
        self.ownership = ownership
        self.save_ownership = save_ownership
        self.run = run or self._run
        self.applied = ownership.pop('applied', {})
        self.verified = False
        self.batch = None
        self.roots_checked = set()
        self.upload_checked = False
        for old in self.applied.values():
            old['rules'] = [(d,i,[ipaddress.ip_network(a) for a in addresses]) for d,i,addresses in old['rules']]

    @staticmethod
    def _run(args):
        return subprocess.check_output(args, stderr=subprocess.STDOUT, timeout=8, text=True)

    def command(self, *args):
        if (self.batch is not None and args[0]=='tc' and len(args)>2 and
            args[2]=='replace' and (args[1] in ('class','filter') or
                                    args[1]=='qdisc' and 'parent' in args)):
            self.batch.append(list(args[1:]))
            return ''
        return self.run(list(args))

    def persist(self, force=False):
        if self.batch is not None and not force:
            return
        data = dict(self.ownership)
        data['applied'] = {key:dict(desired=value['desired'], complete=value.get('complete',False), rules=[
            (d,i,[str(a) for a in addresses]) for d,i,addresses in value['rules']])
            for key,value in self.applied.items()}
        self.save_ownership(data)

    def delete(self, *args):
        try:
            self.command(*args)
        except subprocess.CalledProcessError as error:
            output = str(error.output)
            if not any(message in output for message in ('No such file', 'Cannot find', 'Invalid handle', "Parent Qdisc doesn't exist", 'Cannot delete qdisc with handle of zero')):
                raise

    def qdiscs(self, dev):
        return json.loads(self.command('tc', '-j', 'qdisc', 'show', 'dev', dev))

    def ensure_root(self, dev):
        if dev in self.roots_checked:
            return
        roots = [q for q in self.qdiscs(dev) if q.get('root')]
        meaningful = [q for q in roots if q['kind'] != 'noqueue']
        if dev == self.ifb and self.ownership.get('ifb_created'):
            meaningful = [q for q in meaningful if q.get('handle') != '0:']
        if meaningful:
            if not (self.ownership.get(dev) and len(meaningful) == 1 and
                    meaningful[0]['kind'] == 'htb' and meaningful[0]['handle'] == '7a00:'):
                raise RuntimeError('Existing unowned queue; speed enforcement deferred')
            self.roots_checked.add(dev)
            return
        self.ownership[dev] = True
        self.persist(force=True)
        self.command('tc', 'qdisc', 'replace' if dev == self.ifb else 'add', 'dev', dev, 'root', 'handle', '7a00:',
                     'htb', 'direct_qlen', '64')
        self.ownership[dev] = True
        self.persist(force=True)
        self.roots_checked.add(dev)

    def ensure_upload(self):
        if self.upload_checked:
            return
        links = json.loads(self.command('ip', '-j', 'link', 'show'))
        existing = next((v for v in links if v['ifname'] == self.ifb), None)
        if existing and not self.ownership.get('ifb_created'):
            raise RuntimeError('Unowned IFB name conflict')
        if not existing:
            self.ownership['ifb_created'] = True
            self.persist(force=True)
            self.command('ip', 'link', 'add', self.ifb, 'type', 'ifb')
            self.ownership['ifb_created'] = True
            self.persist(force=True)
        self.command('ip', 'link', 'set', self.ifb, 'up')
        self.ensure_root(self.ifb)
        ingress = [q for q in self.qdiscs(self.interface) if q['kind'] in ('ingress', 'clsact')]
        if ingress and not self.ownership.get('ingress'):
            raise RuntimeError('Existing unowned ingress queue')
        if not ingress:
            self.ownership['ingress'] = True
            self.persist(force=True)
            self.command('tc', 'qdisc', 'add', 'dev', self.interface, 'handle', 'ffff:', 'ingress')
            self.ownership['ingress'] = True
            self.persist(force=True)

        self.upload_checked = True

    @staticmethod
    def addresses(value):
        networks = [ipaddress.ip_network(v.strip(), strict=False) for v in value.split(',')]
        if not networks or any(n.prefixlen != n.max_prefixlen for n in networks):
            raise ValueError('Speed enforcement requires unambiguous host addresses')
        return networks

    def remove(self, key):
        old = self.applied.get(key)
        if not old:
            return
        old['complete'] = False
        self.persist()
        for direction, identifier, addresses in old['rules']:
            dev = self.interface if direction == 'download' else self.ifb
            for index, address in enumerate(addresses):
                protocol = 'ip' if address.version == 4 else 'ipv6'
                pref = str(identifier + index)
                self.delete('tc', 'filter', 'del', 'dev', dev, 'parent', '7a00:',
                             'protocol', protocol, 'pref', pref)
                if direction == 'upload':
                    self.delete('tc', 'filter', 'del', 'dev', self.interface, 'parent', 'ffff:',
                                 'protocol', protocol, 'pref', pref)
            self.delete('tc', 'class', 'del', 'dev', dev, 'classid', f'7a00:{identifier:x}')
        del self.applied[key]
        self.persist()

    def reconcile(self, desired):
        self.roots_checked = set()
        self.upload_checked = False
        # Validate every identity/address before changing any queue.
        claimed = {}
        for key, value in desired.items():
            if value.get('restricted'):
                continue
            try:
                networks=self.addresses(value['address'])
            except ValueError:
                if any(value['caps'].values()):
                    raise
                continue
            for net in networks:
                if net in claimed and claimed[net] != key:
                    raise ValueError('Overlapping peer addresses; no speed rules changed')
                claimed[net] = key
        if not self.verified:
            # Read actual state after process/interface restart. Durable intent
            # alone must never be mistaken for fully installed kernel filters.
            for old in self.applied.values():
                old['complete'] = False
            self.verified = True
        for key in list(self.applied):
            if key not in desired or self.applied[key]['desired'] != desired[key]:
                self.remove(key)
        used = {identifier for old in self.applied.values() for _, identifier, _ in old['rules']}
        self.batch = []
        pending_keys = []
        for key, value in desired.items():
            if not any(value['caps'].values()):
                continue
            if key in self.applied and self.applied[key].get('complete'):
                continue
            if key in self.applied:
                self.remove(key)
            addresses = self.addresses(value['address'])
            if len(addresses) > 8:
                raise ValueError('Too many peer addresses')
            rules = []
            # Allocate small stable blocks; resolve collisions before installation.
            identifier = 8 + (int(hashlib.sha256(key.encode()).hexdigest()[:6], 16) % 7000) * 8
            if len(used) >= 8190:
                raise RuntimeError('Speed class capacity exceeded')
            while identifier in used:
                identifier = 8 if identifier >= 65520 else identifier + 8
            used.add(identifier)
            self.applied[key] = {'desired':deepcopy(value), 'rules':rules, 'complete':False}
            for direction, rate in value['caps'].items():
                if rate is None:
                    continue
                if direction == 'upload':
                    self.ensure_upload()
                dev = self.interface if direction == 'download' else self.ifb
                self.ensure_root(dev)
                classid = f'7a00:{identifier:x}'
                rules.append((direction, identifier, addresses))
                self.persist()
                self.command('tc', 'class', 'replace', 'dev', dev, 'parent', '7a00:', 'classid', classid,
                             'htb', 'rate', f'{rate}mbit', 'ceil', f'{rate}mbit', 'burst', '16k', 'cburst', '16k')
                self.command('tc', 'qdisc', 'replace', 'dev', dev, 'parent', classid, 'fq_codel',
                             'limit', '128', 'flows', '128')
                for index, address in enumerate(addresses):
                    protocol = 'ip' if address.version == 4 else 'ipv6'
                    field = 'dst_ip' if direction == 'download' else 'src_ip'
                    pref = str(identifier + index)
                    self.command('tc', 'filter', 'replace', 'dev', dev, 'parent', '7a00:',
                                 'protocol', protocol, 'pref', pref, 'flower', field, str(address), 'classid', classid)
                    if direction == 'upload':
                        self.command('tc', 'filter', 'replace', 'dev', self.interface, 'parent', 'ffff:',
                                     'protocol', protocol, 'pref', pref, 'flower', 'src_ip', str(address),
                                     'action', 'mirred', 'egress', 'redirect', 'dev', self.ifb)

            pending_keys.append(key)
        commands = self.batch
        self.batch = None
        self.persist(force=True)
        if commands:
            with tempfile.NamedTemporaryFile(mode='w', prefix='wiredash-tc-', suffix='.batch') as handle:
                handle.write('\n'.join(shlex.join(args) for args in commands)+'\n')
                handle.flush()
                self.command('tc','-batch',handle.name)
        for key in pending_keys:
            self.applied[key]['complete'] = True
        if pending_keys:
            self.persist()

    def invalidate(self):
        self.verified = False

    def cleanup(self):
        """Revert empty owned infrastructure, never a foreign filter/tree."""
        self.batch = None
        self.reconcile({})
        links=json.loads(self.command('ip','-j','link','show'))
        if not any(link['ifname']==self.interface for link in links):
            self.ownership.pop('ingress',None)
            self.ownership.pop(self.interface,None)
        if self.ownership.get('ingress'):
            filters=json.loads(self.command('tc','-j','filter','show','dev',self.interface,'parent','ffff:'))
            if not filters:
                ingress=[q for q in self.qdiscs(self.interface) if q['kind']=='ingress' and q['handle']=='ffff:']
                if ingress:self.delete('tc','qdisc','del','dev',self.interface,'ingress')
                self.ownership.pop('ingress',None)
        for dev in (self.interface,self.ifb):
            if not self.ownership.get(dev):continue
            links=json.loads(self.command('ip','-j','link','show'))
            if not any(link['ifname']==dev for link in links):
                self.ownership.pop(dev,None);continue
            roots=[q for q in self.qdiscs(dev) if q.get('root')]
            if not any(q['kind']=='htb' and q['handle']=='7a00:' for q in roots):
                self.ownership.pop(dev,None)
                continue
            classes=json.loads(self.command('tc','-j','class','show','dev',dev))
            filters=json.loads(self.command('tc','-j','filter','show','dev',dev,'parent','7a00:'))
            if not classes and not filters:
                self.delete('tc','qdisc','del','dev',dev,'root','handle','7a00:')
                self.ownership.pop(dev,None)
        if self.ownership.get('ifb_created') and not self.ownership.get(self.ifb):
            self.delete('ip','link','del',self.ifb)
            self.ownership.pop('ifb_created',None)
        self.persist()
