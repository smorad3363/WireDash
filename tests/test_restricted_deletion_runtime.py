#!/usr/bin/env python3
"""Disposable SQLite regression for bulk delete on restricted/allowed peers."""
import pathlib
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import sqlalchemy as sa
from modules.WireguardConfiguration import WireguardConfiguration

class Jobs:
    def __init__(self):
        self.removed=[];self.forgotten=[]
    def deleteJob(self,job): self.removed.append(job);return True,None
    def forget_weights(self,configuration,ids):
        self.forgotten.extend(ids)
class Links:
    def __init__(self):self.removed=[]
    def updateLinkExpireDate(self,link,t):self.removed.append(link)
class Hooks:
    def __init__(self):self.deleted=[]
    def RunWebHook(self,event,data):self.deleted.extend(data.get("peers",[]))
def peer(key):
    return SimpleNamespace(id=key,jobs=[SimpleNamespace(JobID=key+"-job")],
                           ShareLink=[SimpleNamespace(ShareID=key+"-link")])

with tempfile.TemporaryDirectory() as tmp:
    engine=sa.create_engine("sqlite:///"+str(pathlib.Path(tmp)/"peers.sqlite"))
    md=sa.MetaData()
    allowed=sa.Table("peers",md,sa.Column("id",sa.String(255),primary_key=True))
    restricted=sa.Table("restricted",md,sa.Column("id",sa.String(255),primary_key=True))
    md.create_all(engine)
    with engine.begin() as conn:
        conn.execute(allowed.insert().values(id="active"))
        conn.execute(restricted.insert().values(id="expired"))
    cfg=object.__new__(WireguardConfiguration)
    cfg.Name="wg0";cfg.Protocol="wg";cfg.engine=engine
    cfg.peersTable=allowed;cfg.peersRestrictedTable=restricted
    cfg.Peers=[peer("active")]
    cfg.getRestrictedPeersList=lambda:[peer("expired")]
    cfg.getPeers=lambda:None
    cfg.getRestrictedPeers=lambda:None
    cfg.getStatus=lambda:True
    cfg._WireguardConfiguration__wgSave=lambda:True
    cfg.DashboardWebHooks=Hooks()
    jobs=Jobs();links=Links()
    with patch("modules.WireguardConfiguration.subprocess.check_output") as cmd:
        success,msg=cfg.deletePeers(["expired"],jobs,links)
        assert success and "1 peer" in msg,(success,msg)
        cmd.assert_not_called()  # must not unrestrict or touch WireGuard
    with engine.connect() as conn:
        assert conn.execute(sa.select(restricted)).all()==[]
        assert conn.execute(sa.select(allowed)).all() != []
    assert jobs.forgotten==["expired"]
    assert len(jobs.removed)==1
    assert links.removed==["expired-link"]
    assert cfg.DashboardWebHooks.deleted==["expired"]
    with patch("modules.WireguardConfiguration.subprocess.check_output") as cmd:
        success,msg=cfg.deletePeers(["active","gone"],jobs,links)
        assert not success and "failed" in msg,(success,msg)
        cmd.assert_called_once()
    with engine.connect() as conn:
        assert conn.execute(sa.select(allowed)).all()==[]
    assert "active" in jobs.forgotten
print("PASS: delete restricted without unrestricting; allowed/missing mixed selections return normal errors, no HTTP 500")
