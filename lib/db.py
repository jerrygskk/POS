import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from lib import db_schema
from lib.db_schema import SCHEMA
from lib import db_seed

def get_conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


@contextmanager
def db_conn(db_path):
    """開連線並保證關閉;未 commit 的交易於 close 時回滾(保留原 try/finally 語意)。"""
    conn = get_conn(db_path)
    try:
        yield conn
    finally:
        conn.close()


# ---- 共用查詢 helper（純資料層、零框架依賴）----

def in_clause(ids):
    """回傳 IN 子句佔位字串,如 [1,2,3] → "?,?,?"。"""
    return ",".join("?" * len(ids))


def next_sort(conn, table, where="", args=()):
    """回傳該表(可選 where 範圍內)MAX(sort)+1;空表回 1。"""
    sql = f"SELECT COALESCE(MAX(sort),0)+1 s FROM {table}"
    if where:
        sql += f" WHERE {where}"
    return conn.execute(sql, args).fetchone()["s"]


def stock_map(conn, variant_ids):
    """一次 GROUP BY 查回 {variant_id: 庫存};無異動的變體視為 0(呼叫端 .get(vid,0))。"""
    if not variant_ids:
        return {}
    qs = in_clause(variant_ids)
    rows = conn.execute(
        f"SELECT variant_id, COALESCE(SUM(qty),0) s FROM StockMovement "
        f"WHERE variant_id IN ({qs}) GROUP BY variant_id", list(variant_ids))
    return {r["variant_id"]: r["s"] for r in rows}

def _get_schema_version(conn):
    row = conn.execute(
        "SELECT value FROM Setting WHERE key='schema_version'").fetchone()
    if row is None:
        return db_schema.BASE_VERSION  # 舊版 DB 無版號=初版
    return int(row["value"])

def read_schema_version(db_path):
    """唯讀讀出既有資料庫的版號;沒有 Setting 表(全新空檔)回 None。

    語意同 _get_schema_version。網址用 as_uri() 產生:自己拼 file:{path} 時,
    路徑含 #、?、% 會被當成網址符號而開到別的檔案。
    """
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='Setting'").fetchone() is None:
            return None
        return _get_schema_version(conn)
    finally:
        conn.close()

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

def init_db(db_path, require_existing=False):
    if require_existing and not os.path.isfile(db_path):
        raise FileNotFoundError(f"找不到正式資料庫：{db_path}")
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
        db_seed.seed(conn, fresh=is_new)
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            tables = ", ".join(sorted({row[0] for row in violations}))
            raise RuntimeError(f"資料庫外鍵檢查失敗：{tables}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
