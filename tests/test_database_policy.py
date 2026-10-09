"""Stdlib regression checks; no production database or container needed.

Run from the repository root: python3 tests/test_database_policy.py
"""
import importlib.util
import os
import sqlite3
import sys
import tempfile
import types
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

stub = types.ModuleType('sqlalchemy_utils')
stub.database_exists = lambda url: Path(url.split('?')[0].removeprefix('sqlite:///')).exists()
def create_database(url):
    sqlite3.connect(url.split('?')[0].removeprefix('sqlite:///')).close()
stub.create_database = create_database
sys.modules['sqlalchemy_utils'] = stub
spec = importlib.util.spec_from_file_location('connection_under_test', Path(__file__).resolve().parents[1] / 'src/modules/DatabaseConnection.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as temp:
    old = os.getcwd()
    os.chdir(temp)
    try:
        Path('wg-dashboard.ini').write_text('[Database]\ntype=sqlite\n')
        for name in ('wgdashboard', 'wgdashboard_job', 'wgdashboard_log'):
            url = module.ConnectionString(name)
            assert parse_qs(urlsplit(url).query)['timeout'] == ['10']
            with sqlite3.connect(f'db/{name}.db') as c:
                assert c.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
            assert url == module.ConnectionString(name)
        print('PASS: WAL initialized for 3 databases; existing databases preserved; timeout on URLs')
    finally:
        os.chdir(old)
