#!/usr/bin/env python3
"""Static regression: first install, existing install and backup compatibility."""
from pathlib import Path
import ast
import re

dashboard = Path("src/modules/DashboardConfig.py").read_text()
tree = ast.parse(dashboard)
klass = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "DashboardConfig")
ctor = next(x for x in klass.body if isinstance(x, ast.FunctionDef) and x.name == "__init__")
defaults = next(x.value for x in ast.walk(ctor) if isinstance(x, ast.Assign)
                and any(isinstance(t, ast.Attribute) and t.attr == "__default"
                        for t in x.targets))
# AST is generated from a private attribute; the source defaults are tested
# without instantiating DashboardConfig or touching real databases.
def peer_items(node):
    for k,v in zip(node.keys,node.values):
        if isinstance(k,ast.Constant) and k.value=="Peers":
            return {key.value:val for key,val in zip(v.keys,v.values)}
    raise AssertionError("missing peer defaults")
peers=peer_items(defaults)
for k,v in {"peer_global_DNS":"1.1.1.1,8.8.8.8,9.9.9.9",
            "peer_endpoint_allowed_ip":"0.0.0.0/0",
            "peer_MTU":"1280", "peer_keep_alive":"21",
            "peer_traffic_factor":"1.0"}.items():
    assert ast.literal_eval(peers[k]) == v, (k,ast.dump(peers[k]))
assert 'if not exist:' in dashboard and 'self.SetConfig(section, key, value, True)' in dashboard
# A bare "1" is converted to bool True by GetConfig. Avoid that on save:
# bot-created peers without traffic_factor must still get a numeric default.
assert 'if "." not in new_factor:' in dashboard
assert 'new_factor += ".0"' in dashboard
entry=Path("docker/entrypoint.sh").read_text()
assert "if ! grep -Eiq" in entry
assert 'set_ini Peers remote_endpoint "${public_ip}"' in entry
assert '[[ -n "${public_ip}" ]]' in entry
assert "curl -s https://ifconfig.me" not in entry, "Cannot overwrite restored endpoint on restart"
assert 'set_ini Peers peer_global_dns "${global_dns}"' in entry
docker=Path("docker/Dockerfile").read_text()
assert 'global_dns="1.1.1.1,8.8.8.8,9.9.9.9"' in docker
setup=Path("src/static/app/src/views/setup.vue").read_text()
assert 'key:"remote_endpoint"' in setup
assert '"/api/Welcome_Finish"' in setup
assert setup.index('"/api/updateDashboardConfigurationItem"') < setup.index('"/api/Welcome_Finish"')
jobs=Path("src/static/app/src/components/configurationComponents/peerJobs.vue").read_text()
assert 'this.selectedPeer?.configuration?.Name || this.$route.params.id' in jobs
assert "this.selectedPeer.jobs.unshift" in jobs
backup=Path("deploy/wgdashbackup.sh").read_text()
assert 'tar -C "$DATA" --exclude=\'./db\'' in backup
assert 'online_sqlite "$stage/data/db"' in backup
assert '/opt/wgdashboard/.env' in backup
assert 'data etc compose.yaml manifest.txt' in backup
assert "PeerTrafficFactorHistory" in Path("src/modules/PeerTrafficLedger.py").read_text()
print("PASS: fresh-only defaults, endpoint wizard, no upgrade overwrite, jobs plus, whole-system INI and ledger backup")
