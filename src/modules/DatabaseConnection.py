"""Database URL creation and persistent SQLite startup policy."""

import configparser
import os
import sqlite3

from sqlalchemy_utils import create_database, database_exists


# SQLAlchemy's SQLite dialect recognizes the `timeout` URL query parameter.
# This is per-connection; PRAGMA busy_timeout is NOT persistent across connections.
SQLITE_BUSY_TIMEOUT_SECONDS = 10


def ConnectionString(database) -> str:
    parser = configparser.ConfigParser(strict=False)
    with open('wg-dashboard.ini', 'r', encoding='utf-8') as configuration:
        parser.read_file(configuration)

    sqlite_path = 'db'
    os.makedirs(sqlite_path, exist_ok=True)

    engine_type = parser.get('Database', 'type', fallback='sqlite').lower()
    sqlite_file = None
    if engine_type == 'postgresql':
        cn = (f'postgresql+psycopg://{parser.get("Database", "username")}:'
              f'{parser.get("Database", "password")}@'
              f'{parser.get("Database", "host")}/{database}')
    elif engine_type == 'mysql':
        cn = (f'mysql+pymysql://{parser.get("Database", "username")}:'
              f'{parser.get("Database", "password")}@'
              f'{parser.get("Database", "host")}/{database}')
    else:
        sqlite_file = os.path.join(sqlite_path, f'{database}.db')
        cn = f'sqlite:///{sqlite_file}?timeout={SQLITE_BUSY_TIMEOUT_SECONDS}'

    # Keep existing SQLAlchemy-Utils database provisioning for all backends.
    if not database_exists(cn):
        create_database(cn)

    if sqlite_file is not None:
        # WAL is a persistent database setting, not a connection setting. Apply it
        # before application engines begin to use this file; fail closed if the
        # filesystem cannot support it instead of silently falling back to DELETE.
        with sqlite3.connect(sqlite_file, timeout=SQLITE_BUSY_TIMEOUT_SECONDS) as conn:
            mode = conn.execute('PRAGMA journal_mode=WAL').fetchone()[0]
            if mode.lower() != 'wal':
                raise RuntimeError(f'SQLite WAL could not be enabled for {database}')

    return cn