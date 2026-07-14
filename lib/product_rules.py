"""共用商品規則：執行期欄位型別與自取條碼計數器。"""

from fastapi import HTTPException


FIELD_TYPES = {"select", "text", "multi", "tags"}


def check_field_type(field_type):
    if field_type not in FIELD_TYPES:
        raise HTTPException(422, "欄位類型不合法")


def next_store_barcode(conn):
    """以同一連線取用自取碼，更新計數器；交易由呼叫端決定是否提交。"""
    row = conn.execute(
        "SELECT value FROM Setting WHERE key='next_store_barcode'"
    ).fetchone()
    number = int(row["value"]) if row else 100000001
    conn.execute(
        "INSERT OR REPLACE INTO Setting(key,value) VALUES('next_store_barcode',?)",
        (str(number + 1),))
    return f"TL{number}"
