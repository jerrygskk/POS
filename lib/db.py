import sqlite3
from lib import db_schema
from lib.db_schema import SCHEMA
from lib import db_seed

def get_conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def _get_schema_version(conn):
    row = conn.execute(
        "SELECT value FROM Setting WHERE key='schema_version'").fetchone()
    if row is None:
        return db_schema.BASE_VERSION  # 舊版 DB 無版號=初版
    return int(row["value"])

def _set_schema_version(conn, version):
    conn.execute(
        "INSERT OR REPLACE INTO Setting(key,value) VALUES('schema_version',?)",
        (str(version),))

def _run_migrations(conn):
    current = _get_schema_version(conn)
    for target, fn in db_schema.MIGRATIONS:
        if current < target:
            fn(conn)
            current = target
    _set_schema_version(conn, current)  # 補寫舊版 DB 缺的版號

def init_db(db_path):
    conn = get_conn(db_path)
    is_new = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='Setting'"
    ).fetchone() is None
    try:
        conn.executescript(SCHEMA)  # 建缺少的表(IF NOT EXISTS)
        if is_new:
            _set_schema_version(conn, db_schema.SCHEMA_VERSION)
        else:
            _run_migrations(conn)
        db_seed.seed(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
