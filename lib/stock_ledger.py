"""庫存入帳的唯一入口。

所有 StockMovement 都經 post() 寫入:數量一律由來源明細算出(不收呼叫端傳的數量,
返新除外),並在同一交易內檢查來源單據狀態與返新上限。資料庫的 CHECK／外鍵／唯一索引／
鎖定觸發條件擋單筆規則,這裡擋需要跨表加總或依流程狀態判斷的規則。
"""
from lib.application_errors import ConflictError, NotFoundError


def _one(conn, sql, args):
    row = conn.execute(sql, args).fetchone()
    if row is None:
        raise NotFoundError("找不到入帳來源明細")
    return row


def repaired_by_item(conn, item_ids):
    """各退貨品項已返新件數:加總指向它的 repair 異動,不另存累計欄位。"""
    ids = list(item_ids)
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {r[0]: r[1] for r in conn.execute(
        f"SELECT return_item_id, SUM(qty) FROM StockMovement WHERE kind='repair' "
        f"AND return_item_id IN ({marks}) GROUP BY return_item_id", ids)}


def _insert(conn, kind, variant_id, qty, source_col, source_id, note=None, employee=None):
    return conn.execute(
        f"INSERT INTO StockMovement(variant_id,qty,kind,{source_col},note,employee) VALUES(?,?,?,?,?,?)",
        (variant_id, qty, kind, source_id, note, employee)).lastrowid


def post(conn, kind, source_id, qty=None, employee=None):
    """寫一筆庫存異動;回傳 move_id,盤點差異為 0 時不寫並回 None。"""
    if kind == "purchase":
        row = _one(conn, "SELECT variant_id, qty FROM ReceiptItem WHERE receipt_item_id=?", (source_id,))
        return _insert(conn, kind, row["variant_id"], row["qty"], "receipt_item_id", source_id)
    if kind == "sale":
        row = _one(conn, "SELECT variant_id, qty FROM SaleItem WHERE item_id=?", (source_id,))
        return _insert(conn, kind, row["variant_id"], -row["qty"], "sale_item_id", source_id)
    if kind == "adjust":
        row = _one(conn, "SELECT i.variant_id, i.counted_qty - i.system_qty AS diff, s.status "
                         "FROM StocktakeItem i JOIN StocktakeSession s ON s.session_id=i.session_id WHERE i.id=?",
                   (source_id,))
        if row["status"] != "closed":
            raise ConflictError("盤點單尚未核准，不可調整庫存")
        if not row["diff"]:
            return None
        return _insert(conn, kind, row["variant_id"], row["diff"], "stocktake_item_id", source_id, "盤點調整")
    if kind == "return":
        row = _one(conn, "SELECT i.variant_id, i.qty, i.disposition, s.status, s.kind FROM ReturnItem i "
                         "JOIN ReturnSession s ON s.return_id=i.return_id WHERE i.id=?", (source_id,))
        if row["kind"] != "normal" or row["status"] != "closed" or row["disposition"] != "restock":
            raise ConflictError("退貨品項不是已核准的加回庫存")
        return _insert(conn, kind, row["variant_id"], row["qty"], "return_item_id", source_id, "退貨加回")
    if kind == "repair":
        row = _one(conn, "SELECT i.variant_id, i.qty, i.closed_qty, s.status, s.kind FROM ReturnItem i "
                         "JOIN ReturnSession s ON s.return_id=i.return_id WHERE i.id=?", (source_id,))
        if row["kind"] != "repair" or row["status"] != "repairing":
            raise ConflictError("退貨單不是返修中")
        left = row["qty"] - row["closed_qty"] - repaired_by_item(conn, [source_id]).get(source_id, 0)
        if not qty or qty <= 0 or qty > left:
            raise ConflictError(f"返新數量超過未處理件數（剩 {left} 件）")
        return _insert(conn, kind, row["variant_id"], qty, "return_item_id", source_id, "返新進貨", employee)
    raise ValueError(f"不支援的庫存異動類型：{kind}")
