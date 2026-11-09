#!/usr/bin/env python3
"""Regression: stale active/restricted duplicates must not break renewals."""
import pathlib
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import sqlalchemy as sa
from modules.WireguardConfiguration import WireguardConfiguration

with tempfile.TemporaryDirectory() as tmp:
    engine = sa.create_engine("sqlite:///" + str(pathlib.Path(tmp) / "peers.sqlite"))
    metadata = sa.MetaData()
    columns = lambda: [
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("name", sa.String(255)),
        sa.Column("allowed_ip", sa.String(255)),
        sa.Column("preshared_key", sa.String(255)),
        sa.Column("status", sa.String(255)),
    ]
    active = sa.Table("peers", metadata, *columns())
    restricted = sa.Table("restricted", metadata, *columns())
    metadata.create_all(engine)
    with engine.begin() as db:
        db.execute(active.insert(), [
            {"id": "old_duplicate", "name": "fresh-active", "allowed_ip": "10.0.0.2/32", "preshared_key": "", "status": "running"},
            {"id": "reset_peer", "name": "fresh-reset", "allowed_ip": "10.0.0.3/32", "preshared_key": "", "status": "running"},
        ])
        db.execute(restricted.insert(), [
            {"id": "old_duplicate", "name": "stale-restricted", "allowed_ip": "10.0.0.2/32", "preshared_key": "", "status": "stopped"},
            {"id": "reset_peer", "name": "stale-before-reset", "allowed_ip": "10.0.0.3/32", "preshared_key": "", "status": "stopped"},
        ])
    cfg = object.__new__(WireguardConfiguration)
    cfg.Name = "wg0"
    cfg.Protocol = "wg"
    cfg.Address = "10.0.0.1/24"
    cfg.engine = engine
    cfg.peersTable = active
    cfg.peersRestrictedTable = restricted
    cfg.Peers = [SimpleNamespace(id="old_duplicate"), SimpleNamespace(id="reset_peer")]
    cfg.getStatus = lambda: True
    cfg.getPeers = lambda: None
    cfg.getRestrictedPeers = lambda: None
    cfg._WireguardConfiguration__wgSave = lambda: (True, None)

    with patch("modules.WireguardConfiguration.CheckPeerKey", return_value=True), patch("modules.WireguardConfiguration.subprocess.check_output") as command:
        success, message = cfg.allowAccessPeers(["old_duplicate"])
        assert success, message
        command.assert_called_once()

    with engine.connect() as db:
        retained = db.execute(sa.select(active).where(active.c.id == "old_duplicate")).mappings().one()
        assert retained["name"] == "fresh-active"
        assert db.execute(sa.select(restricted).where(restricted.c.id == "old_duplicate")).first() is None

    with patch("modules.WireguardConfiguration.CheckPeerKey", return_value=True), patch("modules.WireguardConfiguration.subprocess.check_output") as command:
        success, message = cfg.restrictPeers(["reset_peer"])
        assert success, message
        command.assert_called_once()

    with engine.connect() as db:
        row = db.execute(sa.select(restricted).where(restricted.c.id == "reset_peer")).mappings().one()
        assert row["name"] == "fresh-reset"
        assert db.execute(sa.select(active).where(active.c.id == "reset_peer")).first() is None

print("PASS: duplicate renew is idempotent and reset replaces stale restricted records")
