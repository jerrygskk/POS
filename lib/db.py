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

class DatabaseTooNewError(RuntimeError):
    """資料庫版號比程式認得的新：舊程式寫入可能破壞新版資料，必須在寫入前停下。"""


class DatabaseTooOldError(RuntimeError):
    """資料庫版號低於 MIN_SUPPORTED_VERSION(含讀不到版號)：程式已不保留那麼舊的升級步驟。"""


UNKNOWN_VERSION = 0  # 有資料表卻讀不到版號:視為低於最低支援版本


def _get_schema_version(conn):
    """讀版號;沒有 Setting 表或沒有版號列一律回 UNKNOWN_VERSION(不再當成初版補寫)。"""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='Setting'").fetchone() is None:
        return UNKNOWN_VERSION
    row = conn.execute(
        "SELECT value FROM Setting WHERE key='schema_version'").fetchone()
    if row is None:
        return UNKNOWN_VERSION
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return UNKNOWN_VERSION  # 版號不是數字:同讀不到版號,走舊庫拒絕流程


def read_schema_version(db_path):
    """唯讀讀出既有資料庫的版號;檔案不存在或沒有任何資料表(全新空檔)回 None。

    網址用 as_uri() 產生:自己拼 file:{path} 時,路徑含 #、?、% 會被當成網址符號而開到別的檔案。
    """
    if not os.path.isfile(db_path):
        return None
    conn = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone() is None:
            return None
        return _get_schema_version(conn)
    finally:
        conn.close()


def check_supported_version(version):
    """版號超出程式能處理的範圍就拒絕;呼叫端須在任何寫入(含升級前備份)之前呼叫。"""
    if version is None:
        return
    if version > db_schema.SCHEMA_VERSION:
        raise DatabaseTooNewError(
            f"資料庫版本 {version} 比程式支援的 {db_schema.SCHEMA_VERSION} 新")
    if version < db_schema.MIN_SUPPORTED_VERSION:
        shown = "未知" if version == UNKNOWN_VERSION else str(version)
        raise DatabaseTooOldError(
            f"資料庫版本 {shown} 過舊，程式最低支援 {db_schema.MIN_SUPPORTED_VERSION}，請改用新版標準資料庫")


def _set_schema_version(conn, version):
    conn.execute(
        "INSERT OR REPLACE INTO Setting(key,value) VALUES('schema_version',?)",
        (str(version),))


def _run_migrations(conn, current):
    for target, fn in db_schema.MIGRATIONS:
        if current < target:
            fn(conn)
            current = target
    _set_schema_version(conn, current)


def init_db(db_path, require_existing=False):
    if require_existing and not os.path.isfile(db_path):
        raise FileNotFoundError(f"找不到正式資料庫：{db_path}")
    # 先以唯讀連線判斷版號:過舊／過新在開寫入連線之前就拒絕,檔案一個位元組都不動
    version = read_schema_version(db_path)
    check_supported_version(version)
    is_new = version is None
    conn = get_conn(db_path)
    try:
        if is_new or version == db_schema.SCHEMA_VERSION:
            conn.executescript(SCHEMA)  # 建缺少的表(IF NOT EXISTS)
            if is_new:
                _set_schema_version(conn, db_schema.SCHEMA_VERSION)
        else:
            # 升級:全程同一交易,任何一步失敗整個回復。不可先 executescript(SCHEMA):
            # 新結構的索引、trigger 對舊表會失敗,而且 executescript 會自行提交、收不回來。
            conn.execute("BEGIN IMMEDIATE")
            version = _get_schema_version(conn)  # 上鎖後重讀:等鎖期間可能已被別的程序升級
            check_supported_version(version)
            if version < db_schema.SCHEMA_VERSION:
                _run_migrations(conn, version)
            for statement in db_schema.statements():
                conn.execute(statement)
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
