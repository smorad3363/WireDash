"""Persistent UI creation order, independent of peer moves and traffic resets."""
from datetime import datetime, timezone
import sqlalchemy as sa
from .PeerCardSummary import utc_naive




class PeerTimeline:
    def __init__(self, engine, configuration):
        self.engine, self.configuration = engine, configuration
        self.table = sa.Table(
            "PeerLifecycle", sa.MetaData(),
            sa.Column("RecordID", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("Configuration", sa.String(255), nullable=False),
            sa.Column("Peer", sa.String(255), nullable=False),
            sa.Column("CreatedAt", sa.DateTime),
            sa.Column("Basis", sa.String(32), nullable=False),
            sa.UniqueConstraint("Configuration", "Peer", name="uq_peer_lifecycle"))
        self.table.create(engine, checkfirst=True)

    def record_created(self, conn, keys):
        """Commit creation evidence in the SAME transaction as provisioning."""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for key in keys:
            where = sa.and_(self.table.c.Configuration == self.configuration,
                            self.table.c.Peer == key)
            changed = conn.execute(self.table.update().where(where).values(
                CreatedAt=now, Basis="created"))
            if not changed.rowcount:
                conn.execute(self.table.insert().values(
                    Configuration=self.configuration, Peer=key,
                    CreatedAt=now, Basis="created"))

    def forget(self, conn, key):
        conn.execute(self.table.delete().where(sa.and_(
            self.table.c.Configuration == self.configuration,
            self.table.c.Peer == key)))

    def snapshot(self, peers, jobs=(), shares=None):
        with self.engine.connect() as conn:
            rows = conn.execute(self.table.select().where(
                self.table.c.Configuration == self.configuration)).mappings().all()
        existing = {row["Peer"]: dict(row) for row in rows}
        missing = {peer.id for peer in peers} - set(existing)
        if missing:
            evidence = {}
            for job in jobs:
                if job.Peer in missing:
                    value = utc_naive(job.CreationDate, local=True)
                    if value and (job.Peer not in evidence or value < evidence[job.Peer]):
                        evidence[job.Peer] = value
            if shares is not None:
                with self.engine.connect() as conn:
                    rows = conn.execute(sa.select(
                        shares.c.Peer, sa.func.min(shares.c.SharedDate).label("first"))
                        .where(shares.c.Configuration == self.configuration)
                        .group_by(shares.c.Peer)).mappings()
                    for row in rows:
                        value = utc_naive(row["first"])
                        if row["Peer"] in missing and value and (
                                row["Peer"] not in evidence or value < evidence[row["Peer"]]):
                            evidence[row["Peer"]] = value
            # Unknown creation dates stay unknown. Never label upgrade time as
            # the birthday of every historic customer.
            with self.engine.begin() as conn:
                for key in sorted(missing):
                    conn.execute(self.table.insert().values(
                        Configuration=self.configuration, Peer=key,
                        CreatedAt=evidence.get(key),
                        Basis="first_record" if key in evidence else "unknown"))
            return self.snapshot(peers)
        return {key: {
            "created_at": row["CreatedAt"].isoformat(timespec="seconds") + "Z"
                if row["CreatedAt"] else None,
            "creation_basis": row["Basis"], "creation_order": row["RecordID"],
        } for key, row in existing.items()}
