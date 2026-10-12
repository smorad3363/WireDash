#!/usr/bin/env python3
"""Disposable SQLite integration of real PeerJobs checkpoint logic.

No WireGuard interfaces, network, panel sessions, or production files.
Intentionally run inside the PR Docker image with its Python dependencies.
"""
import datetime
import json
import tempfile
from pathlib import Path
import sqlalchemy as db

from modules.PeerJobs import PeerJobs
from modules.PeerJob import PeerJob

class Peer:
    def __init__(self, id):
        self.id = id
    def getJobs(self):
        pass

class Config:
    Name = "wg0"
    def __init__(self, engine, active, restricted, peers):
        self.engine = engine
        self.peersTable = active
        self.peersRestrictedTable = restricted
        self.peers = peers
    def getPeersList(self):
        return list(self.peers.values())
    def searchPeer(self, peer):
        return peer in self.peers, self.peers.get(peer)

class Logger:
    def log(self, *args, **kwargs):
        pass

def setup_engine(path):
    engine = db.create_engine("sqlite:///" + str(path))
    metadata = db.MetaData()
    active = db.Table("wg0", metadata,
        db.Column("id",db.String,primary_key=True),
        db.Column("total_receive",db.Float),
        db.Column("cumu_receive",db.Float),
        db.Column("total_sent",db.Float),
        db.Column("cumu_sent",db.Float))
    restricted = db.Table("wg0_restrict_access", metadata,
        db.Column("id",db.String,primary_key=True),
        db.Column("total_receive",db.Float),
        db.Column("cumu_receive",db.Float),
        db.Column("total_sent",db.Float),
        db.Column("cumu_sent",db.Float))
    metadata.create_all(engine)
    return engine, active, restricted

def jobs_instance(engine, config):
    """Reflect persisted job DB tables after simulating an application restart."""
    p = PeerJobs.__new__(PeerJobs)
    p.engine = engine
    p.metadata = db.MetaData()
    p.peerJobTable = db.Table("PeerJobs", p.metadata,
        db.Column("JobID",db.String(255),primary_key=True),
        db.Column("Configuration",db.String(255)),
        db.Column("Peer",db.String(255)),
        db.Column("Field",db.String(255)),
        db.Column("Operator",db.String(255)),
        db.Column("Value",db.String(255)),
        db.Column("CreationDate",db.DateTime),
        db.Column("ExpireDate",db.DateTime),
        db.Column("Action",db.String(255)))
    p.weightsTable = db.Table("PeerTrafficWeights",p.metadata,
        db.Column("Configuration",db.String(255),primary_key=True),
        db.Column("Peer",db.String(255),primary_key=True),
        db.Column("Weight",db.String(32)))
    p.meterTable = db.Table("PeerTrafficMeterState",p.metadata,
        db.Column("Configuration",db.String(255),primary_key=True),
        db.Column("Peer",db.String(255),primary_key=True),
        db.Column("RawReceive",db.String(64)),
        db.Column("RawSent",db.String(64)),
        db.Column("BilledReceive",db.String(64)),
        db.Column("BilledSent",db.String(64)),
        db.Column("Factor",db.String(32)))
    p.metadata.create_all(engine)
    p._weights, p._meters = {}, {}
    p._load_weights()
    p._load_meters()
    p.WireguardConfigurations = {"wg0":config}
    p.Jobs = []
    p._PeerJobs__getJobs()
    p.JobLogger = Logger()
    return p

with tempfile.TemporaryDirectory() as temp:
    data = Path(temp)
    peersEngine, active, restricted = setup_engine(data/"peers.db")
    with peersEngine.begin() as conn:
        conn.execute(active.insert(),[
            {"id":"p1","total_receive":7,"cumu_receive":0,
             "total_sent":3,"cumu_sent":0},
        ])
        conn.execute(restricted.insert(),[
            {"id":"p2","total_receive":4,"cumu_receive":0,
             "total_sent":0,"cumu_sent":0}
        ])
    conf = Config(peersEngine, active, restricted, {"p1":Peer("p1")})
    jobEngine = db.create_engine("sqlite:///" + str(data/"wgdashboard_job.db"))
    manager = jobs_instance(jobEngine, conf)
    with jobEngine.begin() as conn:
        conn.execute(manager.peerJobTable.insert().values(
            JobID="quota-j1",Configuration="wg0",Peer="p1",
            Field="quota_total_data",Operator="lgt",
            Value=json.dumps({"limit_gb":"100","weight":"1"}),
            CreationDate=datetime.datetime.now(),ExpireDate=None,Action="restrict"))
    manager._PeerJobs__getJobs()
    assert manager.metered_usage_for("wg0","p1",7,3)["total"] == 10

    saved = []
    def setting(value):
        saved.append(value)
        return True, ""
    success, msg = manager.apply_global_factor("2",setting)
    assert success, msg
    assert saved == ["2"]
    assert manager.metered_usage_for("wg0","p1",7,3)["total"] == 10
    assert manager.metered_usage_for("wg0","p2",4,0)["total"] == 4
    with peersEngine.begin() as conn:
        conn.execute(active.update().where(active.c.id=="p1").values(
            total_receive=10,total_sent=5))
        conn.execute(restricted.update().where(restricted.c.id=="p2").values(
            total_receive=6))
    assert manager.metered_usage_for("wg0","p1",10,5)["total"] == 20
    assert manager.metered_usage_for("wg0","p2",6,0)["total"] == 8
    with jobEngine.connect() as conn:
        row = conn.execute(manager.peerJobTable.select()).mappings().one()
        assert json.loads(row["Value"])["weight"] == "2"
        assert conn.execute(manager.meterTable.select()).fetchall()

    # Fresh process using persisted job DB, not the previous Python cache.
    rebooted = jobs_instance(jobEngine, conf)
    assert rebooted.metered_usage_for("wg0","p1",10,5)["total"] == 20

    updated = PeerJob("quota-j1","wg0","p1","quota_total_data","lgt",
        json.dumps({"limit_gb":"100","weight":"3"}),
        datetime.datetime.now(),None,"restrict")
    result, savedJob = rebooted.saveJob(updated)
    assert result, savedJob
    assert rebooted.metered_usage_for("wg0","p1",10,5)["total"] == 20
    with peersEngine.begin() as conn:
        conn.execute(active.update().where(active.c.id=="p1").values(total_receive=11,total_sent=6))
    assert rebooted.metered_usage_for("wg0","p1",11,6)["total"] == 26

    # Explicit creation factor even when quota is unlimited.
    with peersEngine.begin() as conn:
        conn.execute(active.insert().values(
            id="p3",total_receive=0,total_sent=0,cumu_receive=0,cumu_sent=0))
    conf.peers["p3"] = Peer("p3")
    ok, err = rebooted.provision_creation_limits("wg0",["p3"],0,0,2)
    assert ok, err
    assert rebooted.metered_usage_for("wg0","p3",1,1)["total"] == 4

    again = jobs_instance(jobEngine, conf)
    assert again.metered_usage_for("wg0","p1",11,6)["total"] == 26
    assert again.metered_usage_for("wg0","p3",1,1)["total"] == 4

print("PASS: SQLite global factor, restricted peer, persisted reboot, per-peer factor and unlimited creation")
