"""Run ONLY inside a disposable privileged container, never on a customer WG.

Two real WireGuard peers in an isolated namespace, actual TCP/UDP packets.
No external link or customer key is involved. Controller adapter is unchanged.
"""
import sys,subprocess,json,time,socket,threading,tempfile,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from modules.PeerTrafficControl import TrafficControl

def run(*args,input=None):
    return subprocess.check_output(list(args),input=input,text=True,stderr=subprocess.STDOUT)
def ns(*args):return run('ip','netns','exec','speedclient',*args)

def serve(listener):
    while True:
        c,_=listener.accept()
        def handle(c):
            with c:
                mode=c.recv(1);start=time.monotonic();count=0
                if mode==b'D':
                    payload=b'x'*16384
                    try:
                        for _ in range(256):c.sendall(payload)
                    except OSError:pass
                else:
                    while True:
                        block=c.recv(65536)
                        if not block:break
                        count+=len(block)
                    c.sendall(json.dumps({'bytes':count,'seconds':time.monotonic()-start}).encode())
        threading.Thread(target=handle,args=(c,),daemon=True).start()

CLIENT=r'''
import socket,time,json,sys
mode=sys.argv[1];s=socket.create_connection(('10.230.0.1',5055),timeout=30)
s.sendall(mode.encode());start=time.monotonic();count=0
if mode=='D':
 while True:
  block=s.recv(65536)
  if not block:break
  count+=len(block)
 result={'bytes':count,'seconds':time.monotonic()-start}
else:
 for _ in range(256):s.sendall(b'x'*16384)
 s.shutdown(socket.SHUT_WR)
 result=json.loads(s.recv(4096))
print(json.dumps(dict(result,mbps=result['bytes']*8/result['seconds']/1e6)))
'''

def measure(mode):
    r=json.loads(ns(sys.executable,'-c',CLIENT,mode));print(mode,json.dumps(r),flush=True);return r['mbps']

UDP_CLIENT = r"""
import socket,time,json,sys
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);s.settimeout(1.2)
mode=sys.argv[1];target=('10.230.0.1',5056)
if mode=='D':
 s.sendto(b'D',target);count=0;first=last=None
 while True:
  try:b,_=s.recvfrom(4096)
  except socket.timeout:break
  now=time.monotonic();first=first or now;last=now;count+=len(b)
 print(json.dumps({'mbps':count*8/(last-first)/1e6,'bytes':count}))
else:
 start=time.monotonic();n=0
 while time.monotonic()-start<3:
  s.sendto(b'U'+b'x'*1199,target);n+=1
  time.sleep(max(0,start+n*1200*8/8e6-time.monotonic()))
 time.sleep(1);s.sendto(b'?',target);b,_=s.recvfrom(4096);print(b.decode())
"""

def udp_server(sock):
    counts={}
    while True:
        data,address=sock.recvfrom(4096);now=time.monotonic()
        if data==b'D':
            def send(address):
                start=time.monotonic();n=0
                while time.monotonic()-start<3:
                    sock.sendto(b'x'*1200,address);n+=1
                    time.sleep(max(0,start+n*1200*8/20e6-time.monotonic()))
            threading.Thread(target=send,args=(address,),daemon=True).start()
        elif data==b'?':
            v=counts.pop(address)
            sock.sendto(json.dumps({'mbps':v[0]*8/(v[2]-v[1])/1e6,'bytes':v[0]}).encode(),address)
        else:
            v=counts.setdefault(address,[0,now,now]);v[0]+=len(data);v[2]=now

def measure_udp(mode):
    result=json.loads(ns(sys.executable,'-c',UDP_CLIENT,mode));print('UDP '+mode,json.dumps(result),flush=True);return result['mbps']

if __name__=='__main__':
    assert os.environ.get('WIREDASH_DISPOSABLE_KERNEL_TEST')=='1','Disposable container only'
    run('ip','netns','add','speedclient')
    run('ip','link','add','underlay','type','veth','peer','name','clienteth')
    run('ip','link','set','clienteth','netns','speedclient')
    run('ip','addr','add','192.0.2.1/30','dev','underlay');run('ip','link','set','underlay','up')
    ns('ip','addr','add','192.0.2.2/30','dev','clienteth');ns('ip','link','set','clienteth','up');ns('ip','link','set','lo','up')
    server_private=run('wg','genkey').strip();client_private=run('wg','genkey').strip()
    server_public=run('wg','pubkey',input=server_private).strip();client_public=run('wg','pubkey',input=client_private).strip()
    run('ip','link','add','wgtest0','type','wireguard');ns('ip','link','add','wgclient','type','wireguard')
    with tempfile.TemporaryDirectory(dir='/etc/wireguard') as d:
        sk=Path(d)/'server';ck=Path(d)/'client';sk.write_text(server_private+'\n');ck.write_text(client_private+'\n');sk.chmod(0o600);ck.chmod(0o600)
        run('wg','set','wgtest0','private-key',str(sk),'listen-port','51899','peer',client_public,'allowed-ips','10.230.0.2/32')
        ns('wg','set','wgclient','private-key',str(ck),'peer',server_public,'allowed-ips','10.230.0.1/32','endpoint','192.0.2.1:51899','persistent-keepalive','1')
    run('ip','addr','add','10.230.0.1/24','dev','wgtest0');run('ip','link','set','wgtest0','mtu','1380','up')
    ns('ip','addr','add','10.230.0.2/32','dev','wgclient');ns('ip','link','set','wgclient','mtu','1380','up');ns('ip','route','add','10.230.0.1/32','dev','wgclient')
    # Separate authenticated, uncapped control peer, while the capped peer sends.
    run('ip','netns','add','speedcontrol')
    run('ip','link','add','controlunderlay','type','veth','peer','name','controleth')
    run('ip','link','set','controleth','netns','speedcontrol')
    run('ip','addr','add','192.0.2.5/30','dev','controlunderlay');run('ip','link','set','controlunderlay','up')
    def control(*args):return run('ip','netns','exec','speedcontrol',*args)
    control('ip','addr','add','192.0.2.6/30','dev','controleth');control('ip','link','set','controleth','up')
    control_private=run('wg','genkey').strip();control_public=run('wg','pubkey',input=control_private).strip()
    control('ip','link','add','wgcontrol','type','wireguard')
    with tempfile.TemporaryDirectory(dir='/etc/wireguard') as folder:
        keyfile=Path(folder)/'key';keyfile.write_text(control_private+'\n');keyfile.chmod(0o600)
        control('wg','set','wgcontrol','private-key',str(keyfile),'peer',server_public,'allowed-ips','10.230.0.1/32','endpoint','192.0.2.5:51899')
    run('wg','set','wgtest0','peer',control_public,'allowed-ips','10.230.0.3/32')
    control('ip','addr','add','10.230.0.3/32','dev','wgcontrol');control('ip','link','set','wgcontrol','mtu','1380','up')
    control('ip','route','add','10.230.0.1/32','dev','wgcontrol')
    ns('ping','-c','2','-W','2','10.230.0.1')
    listener=socket.socket();listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);listener.bind(('10.230.0.1',5055));listener.listen()
    threading.Thread(target=serve,args=(listener,),daemon=True).start()
    udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.bind(('10.230.0.1',5056))
    threading.Thread(target=udp_server,args=(udp,),daemon=True).start()
    saved={}
    def save(v):saved.clear();saved.update(json.loads(json.dumps(v)))
    desired={'test':{'caps':{'download':8,'upload':2},'address':'10.230.0.2/32','generation':'test-only'}}
    adapter=TrafficControl('wgtest0',{},save);adapter.reconcile(desired)
    load=subprocess.Popen(['ip','netns','exec','speedclient',sys.executable,'-c',CLIENT,'D'],stdout=subprocess.PIPE,text=True)
    control_rate=json.loads(control(sys.executable,'-c',CLIENT,'D'))['mbps']
    assert control_rate>12,control_rate
    load.communicate(timeout=15);assert load.returncode==0
    print('Concurrent uncapped control peer Mbps',round(control_rate,2),flush=True)
    down=measure('D');up=measure('U')
    assert 6.0<down<9.5,down
    assert 1.5<up<2.5,up
    assert 6<measure_udp('D')<9.5
    assert 1.4<measure_udp('U')<2.6
    # Process restart must reconcile actual rules and keep the cap.
    adapter=TrafficControl('wgtest0',json.loads(json.dumps(saved)),save);adapter.reconcile(desired)
    assert 6<measure('D')<9.5
    adapter.reconcile({})
    assert measure('D')>12
    assert measure('U')>4
    # Direction independence: only download class must not cap upload.
    desired['test']['caps']['upload']=None;adapter.reconcile(desired)
    assert measure('U')>4
    assert 6<measure('D')<9.5
    # A failed filter installation is retried after durable intent/restart.
    adapter.reconcile({})
    original=adapter.run;injected=[False]
    def fail(args):
        if args[:2]==['tc','-batch'] and not injected[0]:
            injected[0]=True;raise RuntimeError('injected filter crash')
        return original(args)
    adapter.run=fail
    try:adapter.reconcile(desired)
    except RuntimeError:pass
    else:raise AssertionError('Failure injection did not run')
    adapter=TrafficControl('wgtest0',json.loads(json.dumps(saved)),save);adapter.reconcile(desired)
    assert 6<measure('D')<9.5
    adapter.reconcile({})
    assert not adapter.applied
    print('PASS encrypted TCP/UDP directions, cap removal, independent upload, restart and interrupted install recovery',flush=True)
