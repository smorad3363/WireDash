"""Use short-lived SQLite connections to avoid orphaned WAL file handles."""
from sqlalchemy import create_engine as _sqlalchemy_create_engine
from sqlalchemy.pool import NullPool

def create_engine(url, **kwargs):
    if str(url).startswith("sqlite:///"):
        kwargs.setdefault("poolclass", NullPool)
        connect_args = dict(kwargs.pop("connect_args", {}) or {})
        connect_args.setdefault("timeout", 30.0)
        kwargs["connect_args"] = connect_args
    return _sqlalchemy_create_engine(url, **kwargs)
