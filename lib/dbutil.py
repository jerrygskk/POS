"""HTTP 語意的資料層輔助:存在/參照檢查、動態更新、關聯全量替換。

與 lib.db(純連線/查詢,零框架依賴)分層:凡是會 raise HTTPException
(把資料狀態轉成 404/409/422)的 helper 集中在此,依賴 FastAPI。
"""
import sqlite3
from fastapi import HTTPException


def require_exists(conn, table, id_col, id_val, msg):
    """查無 → HTTPException(404, msg)。"""
    if not conn.execute(
            f"SELECT 1 FROM {table} WHERE {id_col}=?", (id_val,)).fetchone():
        raise HTTPException(404, msg)


def reject_if_referenced(conn, table, col, id_val, msg):
    """存在參照 → HTTPException(409, msg)。"""
    if conn.execute(
            f"SELECT 1 FROM {table} WHERE {col}=? LIMIT 1", (id_val,)).fetchone():
        raise HTTPException(409, msg)


def update_by_id(conn, table, id_col, id_val, fields, not_found_msg=None):
    """依 fields dict 組動態 UPDATE;not_found_msg 非空時,rowcount==0 → 404。
    fields 不可為空(呼叫端需先擋)。"""
    cols = ", ".join(f"{k}=?" for k in fields)
    cur = conn.execute(f"UPDATE {table} SET {cols} WHERE {id_col}=?",
                       list(fields.values()) + [id_val])
    if not_found_msg is not None and cur.rowcount == 0:
        raise HTTPException(404, not_found_msg)


def replace_links(conn, table, owner_col, owner_id, other_col, ids,
                  fk_error_msg=None):
    """全量替換關聯表:先刪 owner 的既有列,再逐 id INSERT OR IGNORE(去重保序)。
    fk_error_msg 非空時,FK 等 IntegrityError → HTTPException(422, fk_error_msg)。"""
    conn.execute(f"DELETE FROM {table} WHERE {owner_col}=?", (owner_id,))
    for v in dict.fromkeys(ids):
        try:
            conn.execute(
                f"INSERT OR IGNORE INTO {table}({owner_col},{other_col}) "
                f"VALUES(?,?)", (owner_id, v))
        except sqlite3.IntegrityError:
            if fk_error_msg is not None:
                raise HTTPException(422, fk_error_msg)
            raise
