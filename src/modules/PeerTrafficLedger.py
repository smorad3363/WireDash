"""Durable piecewise traffic billing for unchanged WireDash peer APIs.

A factor change freezes the previously billed totals at the exact raw-counter
boundary and applies the new multiplier ONLY to future traffic. State and
event history live in the existing wgdashboard_job SQLite database, which is
included in verified whole-system backups. Raw WireGuard counters are NEVER
rewritten by factor changes. This module has no HTTP handlers.
"""
from datetime import datetime
from decimal import Decimal
import sqlalchemy as db
from .PeerLimits import metered_usage


def raw_traffic(row):
    """Combined counter fields already used by the original billing code."""
    def value(k):
        v = row[k] if isinstance(row, dict) or hasattr(row, "keys") else getattr(row, k)
        return max(0.0, float(v or 0))
    return (value("total_receive") + value("cumu_receive"),
            value("total_sent") + value("cumu_sent"))


def _valid_weight(factor):
    value = Decimal(str(factor))
    if not value.is_finite() or not Decimal("0.1") <= value <= Decimal("10"):
        raise ValueError("Traffic factor must be from 0.1 to 10")
    return str(value)


class PeerTrafficLedger:
    def __init__(self, engine, metadata):
        self.engine = engine
        self.epochs = db.Table("PeerTrafficEpochs", metadata,
            db.Column("Configuration", db.String(255), primary_key=True),
            db.Column("Peer", db.String(255), primary_key=True),
            db.Column("AnchorReceive", db.Float, nullable=False),
            db.Column("AnchorSent", db.Float, nullable=False),
            db.Column("BilledReceive", db.Float, nullable=False),
            db.Column("BilledSent", db.Float, nullable=False),
            db.Column("Factor", db.String(32), nullable=False),
            db.Column("ChangedAt", db.DateTime, nullable=False))
        self.history = db.Table("PeerTrafficFactorHistory", metadata,
            db.Column("ID", db.Integer, primary_key=True, autoincrement=True),
            db.Column("Configuration", db.String(255), nullable=False),
            db.Column("Peer", db.String(255), nullable=False),
            db.Column("AnchorReceive", db.Float, nullable=False),
            db.Column("AnchorSent", db.Float, nullable=False),
            db.Column("BilledReceive", db.Float, nullable=False),
            db.Column("BilledSent", db.Float, nullable=False),
            db.Column("Factor", db.String(32), nullable=False),
            db.Column("ChangedAt", db.DateTime, nullable=False),
            db.Column("Reason", db.String(24), nullable=False))
        metadata.create_all(engine)
        with engine.connect() as conn:
            self._cache = {
                (r["Configuration"], r["Peer"]): dict(r)
                for r in conn.execute(self.epochs.select()).mappings()
            }

    def totals(self, configuration, peer, received, sent, fallback="1"):
        received = max(0.0, float(received or 0))
        sent = max(0.0, float(sent or 0))
        previous = self._cache.get((configuration, peer))
        if previous is None:
            return metered_usage(received, sent, fallback)
        factor = float(previous["Factor"])
        # A manual reset is handled by reset() below. A counter rollover should
        # never subtract already billed usage or turn it negative.
        receive_delta = (received - previous["AnchorReceive"]
                         if received >= previous["AnchorReceive"] else received)
        sent_delta = (sent - previous["AnchorSent"]
                      if sent >= previous["AnchorSent"] else sent)
        recv = previous["BilledReceive"] + receive_delta * factor
        send = previous["BilledSent"] + sent_delta * factor
        return {"receive": recv, "sent": send, "total": recv + send}

    def change_many(self, transitions, weights, reason="global", update_callback=None):
        """Commit factor changes and their event history in ONE job-DB txn.

        transitions: (configuration, peer, receive, send, NEW factor).
        Updates become visible in memory only after the DB transaction commits.
        """
        staged = {}
        staged_weights = {}
        now = datetime.now()
        with self.engine.begin() as conn:
            for configuration, peer, received, sent, factor in transitions:
                factor = _valid_weight(factor)
                key = (configuration, peer)
                old = weights.get(key, "1")
                old_epoch = self._cache.get(key)
                if str(old) == factor and old_epoch is None:
                    continue
                if old_epoch is not None and str(old_epoch["Factor"]) == factor:
                    continue
                billed = self.totals(configuration, peer, received, sent, old)
                record = {
                    "Configuration": configuration, "Peer": peer,
                    "AnchorReceive": float(received), "AnchorSent": float(sent),
                    "BilledReceive": billed["receive"], "BilledSent": billed["sent"],
                    "Factor": factor, "ChangedAt": now
                }
                updated = conn.execute(self.epochs.update().where(db.and_(
                    self.epochs.c.Configuration == configuration,
                    self.epochs.c.Peer == peer)).values(**record))
                if updated.rowcount == 0:
                    conn.execute(self.epochs.insert().values(**record))
                conn.execute(self.history.insert().values(**record, Reason=reason))
                staged[key] = record
                staged_weights[key] = factor
            if update_callback is not None:
                update_callback(conn)
        self._cache.update(staged)
        weights.update(staged_weights)

    def stage_factor(self, conn, configuration, peer, received, sent,
                     old_weight, new_weight, reason="job"):
        """Stage a single job edit in the caller's existing SQLite transaction.

        Caller must update _cache ONLY after the parent transaction commits,
        otherwise quota jobs and billing epochs could diverge on DB failure.
        """
        factor = _valid_weight(new_weight)
        key = (configuration, peer)
        previous = self._cache.get(key)
        if str(old_weight) == factor or (previous and str(previous["Factor"]) == factor):
            return None
        billed = self.totals(configuration, peer, received, sent, old_weight)
        record = {
            "Configuration": configuration, "Peer": peer,
            "AnchorReceive": float(received), "AnchorSent": float(sent),
            "BilledReceive": billed["receive"], "BilledSent": billed["sent"],
            "Factor": factor, "ChangedAt": datetime.now()
        }
        updated = conn.execute(self.epochs.update().where(db.and_(
            self.epochs.c.Configuration == configuration,
            self.epochs.c.Peer == peer)).values(**record))
        if updated.rowcount == 0:
            conn.execute(self.epochs.insert().values(**record))
        conn.execute(self.history.insert().values(**record, Reason=reason))
        return record

    def committed(self, record):
        if record:
            self._cache[(record["Configuration"], record["Peer"])] = record

    def reset(self, configuration, peer, received, sent, old_weight, mode):
        """An explicitly reset counter starts a NEW usage accounting epoch."""
        current = self.totals(configuration, peer, received, sent, old_weight)
        recv = current["receive"] if mode == "sent" else 0.0
        send = current["sent"] if mode == "receive" else 0.0
        with self.engine.begin() as conn:
            key = (configuration, peer)
            previous = self._cache.get(key)
            record = {
                "Configuration": configuration, "Peer": peer,
                "AnchorReceive": 0.0 if mode in ("total", "receive") else received,
                "AnchorSent": 0.0 if mode in ("total", "sent") else sent,
                "BilledReceive": recv, "BilledSent": send,
                "Factor": _valid_weight(old_weight), "ChangedAt": datetime.now()
            }
            update = conn.execute(self.epochs.update().where(db.and_(
                self.epochs.c.Configuration == configuration,
                self.epochs.c.Peer == peer)).values(**record))
            if update.rowcount == 0:
                conn.execute(self.epochs.insert().values(**record))
            conn.execute(self.history.insert().values(**record, Reason="reset"))
        self._cache[(configuration, peer)] = record

    def rename(self, old, new):
        with self.engine.begin() as conn:
            for table in (self.epochs, self.history):
                conn.execute(table.update().where(
                    table.c.Configuration == old).values(Configuration=new))
        self._cache = {(new if config == old else config, peer): item
                       | {"Configuration": new if config == old else config}
                       for (config, peer), item in self._cache.items()}

    def forget(self, configuration, peers):
        with self.engine.begin() as conn:
            conn.execute(self.epochs.delete().where(db.and_(
                self.epochs.c.Configuration == configuration,
                self.epochs.c.Peer.in_(peers))))
            # Event history remains for audit and backups, not for active billing.
        for peer in peers:
            self._cache.pop((configuration, peer), None)
