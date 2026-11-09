import sys,tempfile,time,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import sqlalchemy as sa
from modules.PeerSpeedStore import PeerSpeedStore
with tempfile.TemporaryDirectory() as folder:
    engine=sa.create_engine('sqlite:///'+folder+'/scale.db',connect_args={'timeout':30})
    store=PeerSpeedStore(engine,'scale')
    peers=[(SimpleNamespace(id=f'peer-{i}',allowed_ip=f'10.230.{i//250}.{i%250+1}/32',
                           metered_usage=lambda:{'receive':10,'sent':20}),False) for i in range(5000)]
    t=time.monotonic();store.sample_peers(peers);first=time.monotonic()-t
    t=time.monotonic();result=store.sample_peers(peers);steady=time.monotonic()-t
    assert len(result)==5000
    t=time.monotonic()
    with ThreadPoolExecutor(max_workers=50) as pool:
        values=list(pool.map(lambda i:store.snapshot(f'peer-{i}'),range(50)))
    elapsed=time.monotonic()-t
    assert all(v['state']['download']==0 for v in values)
    assert steady<30 and elapsed<30
    print(json.dumps({'accounts':5000,'initial_seconds':round(first,3),'sample_seconds':round(steady,3),'concurrent_readers':50,'read_seconds':round(elapsed,3),'database_bytes':Path(folder+'/scale.db').stat().st_size,'status':'PASS'}))
