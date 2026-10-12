#!/usr/bin/env python3
"""Live SQLite unit tests; executed only inside disposable PR Docker image."""
import pathlib
import tempfile
import sqlalchemy as db
from modules.PeerTrafficLedger import PeerTrafficLedger
from modules.PeerLimits import quota_reached, quota_payload


def approx(a, b):
    assert abs(a - b) < 1e-8, (a, b)

with tempfile.TemporaryDirectory() as tmp:
    engine = db.create_engine("sqlite:///" + str(pathlib.Path(tmp)/"meter.db"))
    ledger = PeerTrafficLedger(engine, db.MetaData())
    w = {("wg0","alice"): "1"}
    approx(ledger.totals("wg0", "alice", 10, 0, "1")["total"], 10)
    ledger.change_many([("wg0","alice",10,0,"2")], w)
    approx(ledger.totals("wg0", "alice", 15, 0, w[("wg0","alice")])["total"], 20)
    assert not quota_reached(15, 0, quota_payload(25, 2), metered_total=20)
    assert quota_reached(18, 0, quota_payload(25, 2), metered_total=26)
    ledger.change_many([("wg0","alice",15,0,"0.5")], w)
    approx(ledger.totals("wg0", "alice", 19, 0, w[("wg0","alice")])["total"], 22)
    reload = PeerTrafficLedger(engine, db.MetaData())
    approx(reload.totals("wg0", "alice", 19, 0, w[("wg0","alice")])["total"], 22)
    with engine.connect() as conn:
        events = conn.execute(db.text("SELECT Factor FROM PeerTrafficFactorHistory ORDER BY ID")).scalars().all()
        assert events == ["2","0.5"], events
    try:
        ledger.change_many([("wg0","alice",19,0,"3")], w,
                           update_callback=lambda _: (_ for _ in ()).throw(ValueError("abort")))
    except ValueError:
        pass
    else:
        raise AssertionError("atomic rollback must propagate error")
    assert w[("wg0","alice")] == "0.5"
    approx(ledger.totals("wg0","alice",19,0,w[("wg0","alice")])["total"],22)
    ledger.reset("wg0","alice",19,0,"0.5","total")
    approx(ledger.totals("wg0","alice",2,0,"0.5")["total"],1)
    ledger.rename("wg0","wg1")
    approx(ledger.totals("wg1","alice",2,0,"0.5")["total"],1)
    ledger.forget("wg1",["alice"])
    assert ("wg1","alice") not in ledger._cache
    # Both tables and historical audit travel in SQLite backup snapshots.
    with engine.connect() as conn:
        assert conn.execute(db.text("SELECT count(*) FROM PeerTrafficFactorHistory")).scalar() == 3
# Integration: the ACTUAL PeerJobs global transition must preserve accrued
# usage of both active and restricted peers and update the SAME existing
# quota jobs (no HTTP method, path or request/response schema changes).
from modules.PeerJobs import PeerJobs
from modules.PeerLimits import parse_quota_payload
from types import SimpleNamespace

with tempfile.TemporaryDirectory() as tmp:
    jobs_engine = db.create_engine("sqlite:///" + str(pathlib.Path(tmp)/"jobs.sqlite"))
    meta = db.MetaData()
    wt = db.Table("PeerTrafficWeights", meta,
        db.Column("Configuration",db.String(255),primary_key=True),
        db.Column("Peer",db.String(255),primary_key=True),
        db.Column("Weight",db.String(32),nullable=False))
    jt = db.Table("PeerJobs", meta,
        db.Column("JobID",db.String(255),primary_key=True),
        db.Column("Configuration",db.String(255)),
        db.Column("Peer",db.String(255)),
        db.Column("Field",db.String(255)),
        db.Column("Operator",db.String(255)),
        db.Column("Value",db.String(255)),
        db.Column("CreationDate",db.DateTime),
        db.Column("ExpireDate",db.DateTime),
        db.Column("Action",db.String(255)))
    meta.create_all(jobs_engine)
    with jobs_engine.begin() as conn:
        conn.execute(jt.insert().values(JobID="quota-a",Configuration="wg0",
            Peer="alice",Field="quota_total_data",Operator="lgt",
            Value=quota_payload(100,1),CreationDate=__import__("datetime").datetime.now(),
            ExpireDate=None,Action="restrict"))
    peers_engine = db.create_engine("sqlite:///" + str(pathlib.Path(tmp)/"peers.sqlite"))
    pmeta = db.MetaData()
    def peer_table(name):
        return db.Table(name,pmeta,
          db.Column("id",db.String(255),primary_key=True),
          *[db.Column(k,db.Float) for k in (
            "total_receive","total_sent","cumu_receive","cumu_sent")])
    allowed=peer_table("allowed");restricted=peer_table("restricted")
    pmeta.create_all(peers_engine)
    with peers_engine.begin() as conn:
        conn.execute(allowed.insert().values(
          id="alice",total_receive=10,total_sent=0,cumu_receive=0,cumu_sent=0))
        conn.execute(restricted.insert().values(
          id="bob",total_receive=0,total_sent=5,cumu_receive=0,cumu_sent=0))
    fake=SimpleNamespace(engine=peers_engine,peersTable=allowed,
        peersRestrictedTable=restricted,getStatus=lambda:False,
        getPeersList=lambda:[],getRestrictedPeersList=lambda:[])
    pj=object.__new__(PeerJobs)
    pj.engine=jobs_engine;pj.peerJobTable=jt;pj.weightsTable=wt
    pj.ledger=PeerTrafficLedger(jobs_engine,meta)
    pj._weights={};pj.Jobs=[]
    pj.WireguardConfigurations={"wg0":fake}
    pj._PeerJobs__getJobs()
    pj.set_global_factor("2")
    assert pj.weight_for("wg0","alice")=="2"
    assert pj.weight_for("wg0","bob")=="2"
    approx(pj.metered_for("wg0","alice",15,0)["total"],20)
    approx(pj.metered_for("wg0","bob",0,6)["total"],7)
    with jobs_engine.connect() as conn:
        quota=conn.execute(db.text("SELECT Value FROM PeerJobs WHERE JobID='quota-a'")).scalar()
    limit,weight=parse_quota_payload(quota)
    assert float(limit)==100 and float(weight)==2
    pj.set_global_factor("3")
    approx(pj.metered_for("wg0","alice",15,0)["total"],20)
    approx(pj.metered_for("wg0","alice",17,0)["total"],26)
    reloaded=PeerTrafficLedger(jobs_engine,db.MetaData())
    approx(reloaded.totals("wg0","alice",17,0,"3")["total"],26)
print("PASS: continuous piecewise metering, multiple factor changes, preserved history, reset, transaction rollback and DB restore")
