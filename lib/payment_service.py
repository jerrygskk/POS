"""付款方式設定:新增、改名、排序、停用／啟用、改抽成比例、刪除沒用過的(限管理者,由 SalesFacade 分派)。

編號不可變、不重用:舊銷售單與立帳快照靠編號認付款方式,所以改名只改這張表,用過的不能刪。
改抽成比例只影響之後立帳的帳日;已立帳的讀自己的快照。
"""
from collections.abc import Mapping

from lib.application_errors import NotFoundError, ValidationError
from lib.normalize import normalize_key
from lib.product_rules import is_int

NAME_MAX = 10          # 報表每個付款方式一欄,名稱太長欄位放不下
FEE_RATE_MAX = 10000   # 抽成比例以「百分比×100」的整數進出:2.2%＝220,最多兩位小數、不會有小數誤差


def _fee_rate(value):
    if not is_int(value) or not 0 <= value <= FEE_RATE_MAX:
        raise ValidationError("抽成比例須為 0～100，最多兩位小數")
    return value


def _name(value):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("請輸入付款方式名稱")
    if len(value.strip()) > NAME_MAX:
        raise ValidationError(f"付款方式名稱最多 {NAME_MAX} 字")
    return value.strip()


def _check_unique(conn, name, except_id=None):
    # 全半形、大小寫、空白不同也算同名;停用的也算,否則報表會出現兩欄看起來一樣的名稱
    key = normalize_key(name)
    if any(normalize_key(row["name"]) == key and row["payment_id"] != except_id
           for row in conn.execute("SELECT payment_id, name FROM PaymentMethod")):
        raise ValidationError("已有相同名稱的付款方式")


def used_ids(conn):
    """有銷售單或立帳快照的付款方式編號:這些不能刪,只能停用。"""
    return {row[0] for row in conn.execute(
        "SELECT payment_id FROM Sale UNION SELECT payment_id FROM DayClosePayment")}


def prepare(action, payload):
    allowed = {"payments.create": {"name", "fee_rate"}, "payments.update": {"payment_id", "name", "active", "fee_rate"},
               "payments.sort": {"ids"}, "payments.delete": {"payment_id"}}[action]
    if not isinstance(payload, Mapping) or set(payload) - allowed:
        raise ValidationError("付款方式資料格式不正確")
    if action == "payments.create":
        return {"name": _name(payload.get("name")), "fee_rate": _fee_rate(payload.get("fee_rate"))}
    if action in ("payments.update", "payments.delete"):
        if not is_int(payload.get("payment_id")):
            raise ValidationError("付款方式資料格式不正確")
        out = {"payment_id": payload["payment_id"]}
        if action == "payments.delete":
            return out
        if "name" in payload:
            out["name"] = _name(payload["name"])
        if "active" in payload:
            if not isinstance(payload["active"], bool):
                raise ValidationError("付款方式資料格式不正確")
            out["active"] = payload["active"]
        if "fee_rate" in payload:
            out["fee_rate"] = _fee_rate(payload["fee_rate"])
        if len(out) == 1:
            raise ValidationError("付款方式資料格式不正確")
        return out
    ids = payload.get("ids")
    if not isinstance(ids, list) or not all(is_int(i) for i in ids) or len(set(ids)) != len(ids):
        raise ValidationError("付款方式資料格式不正確")
    return {"ids": ids}


def create(conn, body):
    conn.execute("BEGIN IMMEDIATE")   # 查重與寫入同一交易,兩個操作交錯才不會各自通過
    _check_unique(conn, body["name"])
    payment_id = conn.execute(
        "INSERT INTO PaymentMethod(name,sort,fee_rate) VALUES(?,(SELECT COALESCE(MAX(sort),0)+1 FROM PaymentMethod),?)",
        (body["name"], body["fee_rate"])).lastrowid
    return {"payment_id": payment_id}


def update(conn, body):
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute("SELECT is_cash FROM PaymentMethod WHERE payment_id=?", (body["payment_id"],)).fetchone()
    if row is None:
        raise NotFoundError("找不到付款方式")
    # 現金恆啟用、不抽成(資料庫 CHECK 也擋);所以啟用中的付款方式至少有現金一個
    if row["is_cash"] and (body.get("active") is False or body.get("fee_rate")):
        raise ValidationError("現金不可停用，也不可設定抽成")
    if "name" in body:
        # 只改這張表:舊銷售單、更正紀錄、立帳快照上留的是當時的名稱,不跟著改
        _check_unique(conn, body["name"], body["payment_id"])
        conn.execute("UPDATE PaymentMethod SET name=? WHERE payment_id=?", (body["name"], body["payment_id"]))
    for column in ("active", "fee_rate"):
        if column in body:
            conn.execute(f"UPDATE PaymentMethod SET {column}=? WHERE payment_id=?", (int(body[column]), body["payment_id"]))
    return {"ok": True}


def delete(conn, body):
    conn.execute("BEGIN IMMEDIATE")   # 檢查與刪除同一交易:中間不會插進一筆剛結帳的銷售單
    row = conn.execute("SELECT is_cash FROM PaymentMethod WHERE payment_id=?", (body["payment_id"],)).fetchone()
    if row is None:
        raise NotFoundError("找不到付款方式")
    if row["is_cash"]:
        raise ValidationError("現金不可刪除")
    if body["payment_id"] in used_ids(conn):
        raise ValidationError("這個付款方式已有交易紀錄，只能停用")
    conn.execute("DELETE FROM PaymentMethod WHERE payment_id=?", (body["payment_id"],))
    return {"ok": True}


def sort(conn, body):
    conn.execute("BEGIN IMMEDIATE")
    # 要送全部的編號:只送一部分會讓沒送的留著舊號碼,順序錯亂
    if sorted(body["ids"]) != [row[0] for row in conn.execute("SELECT payment_id FROM PaymentMethod ORDER BY payment_id")]:
        raise ValidationError("付款方式清單已變動，請重新整理")
    for position, payment_id in enumerate(body["ids"], 1):
        conn.execute("UPDATE PaymentMethod SET sort=? WHERE payment_id=?", (position, payment_id))
    return {"ok": True}
