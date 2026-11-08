#!/usr/bin/env python3
"""Prevent regressions to long-lived SQLite WAL descriptors."""
import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1]
MODULES = ROOT / "src/modules"
helper = MODULES / "SafeDatabaseEngine.py"
assert helper.is_file()

calls = []
fake_sa = types.ModuleType("sqlalchemy")
fake_pool = types.ModuleType("sqlalchemy.pool")
class NullPool:
    pass
def fake_create(url, **kwargs):
    calls.append((str(url), kwargs))
    return kwargs
fake_sa.create_engine = fake_create
fake_pool.NullPool = NullPool
sys.modules["sqlalchemy"] = fake_sa
sys.modules["sqlalchemy.pool"] = fake_pool
spec = importlib.util.spec_from_file_location("SafeDatabaseEngine", helper)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

sqlite = module.create_engine("sqlite:////tmp/wiredash-test.db")
assert sqlite["poolclass"] is NullPool
assert sqlite["connect_args"]["timeout"] == 30.0
assert module.create_engine("postgresql://local/test") == {}
assert module.create_engine("mysql://local/test") == {}
assert module.create_engine("sqlite:///test.db", connect_args={"timeout": 12})["connect_args"]["timeout"] == 12

patched = []
for path in MODULES.glob("*.py"):
    if path == helper:
        continue
    text = path.read_text()
    assert "db.create_engine(ConnectionString(" not in text, path.name
    assert "sqlalchemy.create_engine(ConnectionString(" not in text, path.name
    if "safe_create_engine(ConnectionString(" in text:
        assert "from .SafeDatabaseEngine import create_engine as safe_create_engine" in text
        patched.append(path.name)
assert len(patched) == 11, patched

watchdog = (ROOT / "deploy/wgd-watchdog.sh").read_text()
assert "stale_wal()" in watchdog
assert "*/db/*.db-wal" in watchdog
assert "kill -HUP" in watchdog
assert "if cap_allows; then" in watchdog
print("PASS: SQLite engines avoid persistent WAL handles; watchdog detects orphans")
