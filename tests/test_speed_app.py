"""Full Flask/model/kernel lifecycle in a disposable application container.

Requires isolated /data and /etc/wireguard mounts, never production volumes.
"""
import os,sys,subprocess,configparser,pathlib,json,uuid,sqlite3
assert os.environ.get('WIREDASH_DISPOSABLE_APP_TEST')=='1'
assert pathlib.Path('/data/.wiredash-disposable').read_text().strip()=='wiredash-speed-rehearsal', 'Refusing an unmarked data volume'
os.chdir('/opt/wgdashboard/src')
import bcrypt
cfg=configparser.ConfigParser(strict=False);cfg.read('/data/wg-dashboard.ini')
cfg.set('Account','username','speedtest');cfg.set('Account','password',bcrypt.hashpw(b'test-integration-only',bcrypt.gensalt()).decode())
cfg.set('Account','enable_totp','false')
with open('/data/wg-dashboard.ini','w') as f:cfg.write(f)
for name,target in [('wg-dashboard.ini','/data/wg-dashboard.ini'),('db','/data/db'),('wg-dashboard-oidc-providers.json','/data/wg-dashboard-oidc-providers.json')]:
    p=pathlib.Path(name)
    if p.is_symlink():p.unlink()
    elif p.exists():
        import shutil
        if p.is_dir():shutil.rmtree(p)
        else:p.unlink()
    p.symlink_to(target)
private=subprocess.check_output(['wg','genkey'],text=True).strip()
p=pathlib.Path('/etc/wireguard/wgtest0.conf');p.write_text('[Interface]\nPrivateKey = '+private+'\nAddress = 10.231.0.1/24\nListenPort = 51999\nSaveConfig = true\n');p.chmod(0o600)
sys.path.insert(0,'/opt/wgdashboard/src')
import dashboard as d
c=d.WireguardConfigurations['wgtest0']
assert c.getStatus()
with c.engine.connect() as conn:
    key=conn.execute(__import__('sqlalchemy').text('SELECT Key FROM DashboardAPIKeys WHERE ExpiredAt IS NULL LIMIT 1')).scalar()
assert key
bot=d.app.test_client();admin=d.app.test_client()
headers={'wg-dashboard-apikey':key}
def api(path,body=None):
    r=bot.get(path,headers=headers) if body is None else bot.post(path,json=body,headers=headers)
    data=r.get_json();assert r.status_code==200 and data['status'],(path,r.status_code,data.get('message'));return data['data']
def private_get(path):
    r=admin.get(path);data=r.get_json();assert r.status_code==200 and data['status'],(path,r.status_code,data.get('message'));return data
login=admin.post('/api/authenticate',json={'username':'speedtest','password':'test-integration-only'})
assert login.get_json()['status']
settings=private_get('/api/ui/speed/settings');assert settings['data']['master_enabled'] is False
csrf=settings['csrf'];ui_headers={'X-Speed-CSRF':csrf,'Origin':'http://localhost'}
peer=api('/api/addPeers/wgtest0',{'name':'__isolated_speed_'+uuid.uuid4().hex[:8],'duration_days':1,'quota_gb':50})[0]
pid=peer['id'];c.SpeedController.tick()
assert c.SpeedController.status=='inactive',c.SpeedController.last_error
snapshot=private_get('/api/ui/speed/peer/wgtest0?id='+__import__('urllib.parse').parse.quote(pid))['data']
policy={'enabled':True,'period_days':30,'download_gb':0.000001,'upload_gb':0.000001,'download_mbps':8,'upload_mbps':2}
r=admin.post('/api/ui/speed/peer/wgtest0',json={'id':pid,'mode':'custom','policy':policy,'revision':snapshot['revision']},headers=ui_headers);assert r.get_json()['status']
settings=private_get('/api/ui/speed/settings')['data']
r=admin.post('/api/ui/speed/settings',json={**settings,'overwrite':False,'master_enabled':True},headers=ui_headers);assert r.get_json()['status']
# Test fixture counters belong ONLY to this new disposable account.
with c.engine.begin() as conn:
    conn.execute(c.peersTable.update().where(c.peersTable.c.id==pid).values(cumu_receive=1,cumu_sent=1,cumu_data=2))
c.getPeers();c.SpeedController.tick();assert c.SpeedController.status=='applied'
assert pid in c.SpeedController.tc.applied
# Actual unchanged bot reset, successful kernel restore and cap removal.
api('/api/resetPeerData/wgtest0',{'id':pid,'type':'total'})
assert pid not in c.SpeedController.tc.applied
s=c.SpeedStore.snapshot(pid);assert s['last_reset'] and s['mode']=='custom' and s['pending_reset'] is None
assert s['state']['download']==0
# Actual expiry operation through the unchanged bot API.
jobs=[j for j in c.AllPeerJobs.Jobs if j.Peer==pid and j.Field=='date'];assert jobs
job=jobs[0];from datetime import datetime,timedelta
body={'JobID':job.JobID,'Configuration':'wgtest0','Peer':pid,'Field':'date','Operator':'lgt','Value':(datetime.now()+timedelta(days=35)).strftime('%Y-%m-%d %H:%M:%S'),'Action':'restrict'}
# Discover the exact scheduler request shape from its handler in tests, not a new protocol.
body.update(CreationDate=job.CreationDate.isoformat(),ExpireDate=None)
api('/api/savePeerScheduleJob',{'Job':body})
c.SpeedController.tick()
s=c.SpeedStore.snapshot(pid);assert s.get('last_renewal'),s['history']
# API-key-only and exchanged API-derived session cannot configure the feature.
assert bot.get('/api/ui/speed/settings',headers=headers).status_code==403
exchanged=bot.post('/api/authenticate',json={},headers=headers);assert exchanged.get_json()['status']
assert bot.get('/api/ui/speed/settings').status_code==403
api('/api/restrictPeers/wgtest0',{'peers':[pid]});c.SpeedController.tick()
assert pid not in c.SpeedController.tc.applied
api('/api/allowAccessPeers/wgtest0',{'peers':[pid]})
api('/api/deletePeers/wgtest0',{'peers':[pid]})
assert c.SpeedStore.snapshot(pid)['state'] is None
assert pid not in c.SpeedController.tc.applied
print('PASS actual application creation, protected settings, bot reset, expiry journal, renewal history, restrict/allow/delete and kernel cleanup',flush=True)
