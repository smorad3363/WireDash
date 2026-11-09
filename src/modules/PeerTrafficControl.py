"""Owned TC rules on decrypted WireGuard traffic; no host-wide shaping.

Only individual host routes are accepted. Unmatched traffic uses HTB's direct
path without an artificial whole-interface rate ceiling. Never adopt a foreign
root or ingress qdisc, even when it has our preferred handle.
"""
import hashlib
import ipaddress
import json
import re
import subprocess


class TrafficControl:
    def __init__(self, interface, ownership, save_ownership, run=None):
        if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,15}', interface):
            raise ValueError('Invalid interface')
        self.interface = interface
        self.ifb = 'wd' + hashlib.sha256(interface.encode()).hexdigest()[:10]
        self.ownership = ownership
        self.save_ownership = save_ownership
        self.run = run or self._run
        self.applied = ownership.pop('applied', {})
        for old in self.applied.values():
            old['rules'] = [(d,i,[ipaddress.ip_network(a) for a in addresses]) for d,i,addresses in old['rules']]

    @staticmethod
    def _run(args):
        return subprocess.check_output(args, stderr=subprocess.STDOUT, timeout=8, text=True)

    def command(self, *args):
        return self.run(list(args))

    def persist(self):
        data = dict(self.ownership)
        data['applied'] = {key:dict(desired=value['desired'], rules=[
            (d,i,[str(a) for a in addresses]) for d,i,addresses in value['rules']])
            for key,value in self.applied.items()}
        self.save_ownership(data)

    def delete(self, *args):
        try:
            self.command(*args)
        except subprocess.CalledProcessError as error:
            output = str(error.output)
            if not any(message in output for message in ('No such file', 'Cannot find', 'Invalid handle')):
                raise

    def qdiscs(self, dev):
        return json.loads(self.command('tc', '-j', 'qdisc', 'show', 'dev', dev))

    def ensure_root(self, dev):
        roots = [q for q in self.qdiscs(dev) if q.get('root')]
        meaningful = [q for q in roots if q['kind'] != 'noqueue']
        if meaningful:
            if not (self.ownership.get(dev) and len(meaningful) == 1 and
                    meaningful[0]['kind'] == 'htb' and meaningful[0]['handle'] == '7a00:'):
                raise RuntimeError('Existing unowned queue; speed enforcement deferred')
            return
        self.command('tc', 'qdisc', 'add', 'dev', dev, 'root', 'handle', '7a00:',
                     'htb', 'direct_qlen', '64')
        self.ownership[dev] = True
        self.persist()

    def ensure_upload(self):
        links = json.loads(self.command('ip', '-j', 'link', 'show'))
        existing = next((v for v in links if v['ifname'] == self.ifb), None)
        if existing and not self.ownership.get('ifb_created'):
            raise RuntimeError('Unowned IFB name conflict')
        if not existing:
            self.command('ip', 'link', 'add', self.ifb, 'type', 'ifb')
            self.ownership['ifb_created'] = True
            self.persist()
        self.command('ip', 'link', 'set', self.ifb, 'up')
        self.ensure_root(self.ifb)
        ingress = [q for q in self.qdiscs(self.interface) if q['kind'] in ('ingress', 'clsact')]
        if ingress and not self.ownership.get('ingress'):
            raise RuntimeError('Existing unowned ingress queue')
        if not ingress:
            self.command('tc', 'qdisc', 'add', 'dev', self.interface, 'handle', 'ffff:', 'ingress')
            self.ownership['ingress'] = True
            self.persist()

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
        # Validate every identity/address before changing any queue.
        claimed = {}
        for key, value in desired.items():
            for net in self.addresses(value['address']):
                if net in claimed and claimed[net] != key:
                    raise ValueError('Overlapping peer addresses; no speed rules changed')
                claimed[net] = key
        for key in list(self.applied):
            if key not in desired or self.applied[key]['desired'] != desired[key]:
                self.remove(key)
        used = {identifier for old in self.applied.values() for _, identifier, _ in old['rules']}
        for key, value in desired.items():
            if key in self.applied or not any(value['caps'].values()):
                continue
            addresses = self.addresses(value['address'])
            if len(addresses) > 8:
                raise ValueError('Too many peer addresses')
            rules = []
            # Allocate small stable blocks; resolve collisions before installation.
            identifier = 16 + (int(hashlib.sha256(key.encode()).hexdigest()[:6], 16) % 3500) * 16
            while identifier in used:
                identifier = 16 if identifier >= 60000 else identifier + 16
            used.add(identifier)
            self.applied[key] = {'desired':value, 'rules':rules}
            for direction, rate in value['caps'].items():
                if rate is None:
                    continue
                if direction == 'upload':
                    self.ensure_upload()
                dev = self.interface if direction == 'download' else self.ifb
                self.ensure_root(dev)
                classid = f'7a00:{identifier:x}'
                self.command('tc', 'class', 'replace', 'dev', dev, 'parent', '7a00:', 'classid', classid,
                             'htb', 'rate', f'{rate}mbit', 'ceil', f'{rate}mbit', 'burst', '16k', 'cburst', '16k')
                self.command('tc', 'qdisc', 'replace', 'dev', dev, 'parent', classid, 'fq_codel',
                             'limit', '128', 'flows', '128')
                rules.append((direction, identifier, addresses))
                self.persist()
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

        self.persist()
