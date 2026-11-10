"""更正紀錄:付款方式與操作員工的更正歷程(Correction 表,不可改刪)。

更正＝同一交易內「改單據欄位＋寫一筆紀錄」;單據上永遠是目前的值,歷程看這張表。
"""
from lib.application import BaseFacade
from lib.application_errors import ConflictError, NotFoundError, ValidationError
from lib.product_rules import is_int, require_employee

# 單據種類 → (資料表, 主鍵欄, Correction 的來源欄)
DOCS = {
    "sale": ("Sale", "sale_id", "sale_id"),
    "return": ("ReturnSession", "return_id", "return_id"),
    "receipt": ("Receipt", "receipt_id", "receipt_id"),
    "stocktake": ("StocktakeSession", "session_id", "stocktake_id"),
}


def record(conn, doc_type, doc_id, field, old_value, new_value):
    """寫一筆更正紀錄;值一律存成文字,舊值可為 None(舊銷售單沒有員工)。"""
    source = DOCS[doc_type][2]
    conn.execute(
        f"INSERT INTO Correction({source},field,old_value,new_value) VALUES(?,?,?,?)",
        (doc_id, field, None if old_value is None else str(old_value), str(new_value)))


def by_document(conn, doc_type, doc_ids):
    """各單據的更正歷程 {單據編號: [紀錄…]},依時間先後;沒有紀錄的單據不在結果內。"""
    ids = list(doc_ids)
    if not ids:
        return {}
    source = DOCS[doc_type][2]
    marks = ",".join("?" * len(ids))
    out = {}
    for row in conn.execute(
            f"SELECT {source} doc_id, ts, field, old_value, new_value FROM Correction "
            f"WHERE {source} IN ({marks}) ORDER BY correction_id", ids):
        out.setdefault(row["doc_id"], []).append(
            {"ts": row["ts"], "field": row["field"], "old_value": row["old_value"], "new_value": row["new_value"]})
    return out


def attach(conn, doc_type, rows, key):
    """替清單每一筆補上 corrections(沒有就是空清單)。"""
    found = by_document(conn, doc_type, [row[key] for row in rows])
    for row in rows:
        row["corrections"] = found.get(row[key], [])
    return rows


def _validate(payload):
    if set(payload) - {"doc_type", "doc_id", "employee", "expected_employee"}:
        raise ValidationError("更正資料格式不正確")
    if not isinstance(payload.get("doc_type"), str) or payload["doc_type"] not in DOCS:
        raise ValidationError("單據種類不正確")
    if not is_int(payload.get("doc_id")):
        raise ValidationError("單據編號格式不正確")
    require_employee(payload.get("employee"))
    if "expected_employee" not in payload:
        raise ValidationError("更正資料格式不正確")
    expected = payload["expected_employee"]
    if expected is not None and not is_int(expected):
        raise ValidationError("員工代號格式不正確")
    return {key: payload[key] for key in ("doc_type", "doc_id", "employee", "expected_employee")}


def set_employee(conn, body):
    """更正單據的操作員工:不限期限,不看單據狀態、是否點帳、是否作廢。"""
    conn.execute("BEGIN IMMEDIATE")
    table, key, _source = DOCS[body["doc_type"]]
    row = conn.execute(f"SELECT employee FROM {table} WHERE {key}=?", (body["doc_id"],)).fetchone()
    if row is None:
        raise NotFoundError("找不到單據")
    if row["employee"] != body["expected_employee"]:
        raise ConflictError("單據內容已變更，請重新確認")
    if row["employee"] == body["employee"]:
        raise ValidationError("新員工代號與目前相同")
    conn.execute(f"UPDATE {table} SET employee=? WHERE {key}=?", (body["employee"], body["doc_id"]))
    record(conn, body["doc_type"], body["doc_id"], "employee", row["employee"], body["employee"])
    return {"ok": True}


class CorrectionFacade(BaseFacade):
    ACTIONS = {"corrections.set_employee"}

    ERROR_MESSAGE = "更正操作不正確"

    def _prepare_payload(self, action, payload):
        return _validate(payload)

    def _dispatch(self, action, payload, connection):
        return set_employee(connection, payload)
