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
print("PASS: continuous piecewise metering, multiple factor changes, preserved history, reset, transaction rollback and DB restore")
