from lib import product_data
from lib.application import BaseFacade, BaseRepository
from lib.application_errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from lib.product_rules import allow_keys as _allow, date_range, date_sql, is_int as _is_int, require_employee

DISPOSITIONS = ("restock", "discard", "reject")

def _optional_text(payload, key):
    if payload.get(key) is not None and not isinstance(payload[key], str):
        raise ValidationError(f"{key} 必須是字串")

def _validate(action, payload):
    allowed = {"returns.create":{"employee"}, "returns.list":set(),
        "returns.history":{"date_from","date_to"}, "returns.detail":{"return_id"},
        "returns.scan":{"return_id","variant_id","qty"}, "returns.set_qty":{"return_id","variant_id","qty"},
        "returns.set_reason":{"return_id","variant_id","reason"},
        "returns.set_sale":{"return_id","variant_id","sale_id"},
        "returns.remove_item":{"return_id","variant_id"},
        "returns.close":{"return_id"}, "returns.withdraw":{"return_id"},
        "returns.set_disposition":{"return_id","variant_id","disposition"},
        "returns.approve":{"return_id"}, "returns.cancel":{"return_id","employee"},
        "returns.pending_count":set()}[action]
    _allow(payload, allowed)
    for key in ("return_id", "variant_id"):
        if key in allowed and not _is_int(payload.get(key)): raise ValidationError(f"{key} 必須是整數")
    if action in {"returns.scan", "returns.set_qty"} and (not _is_int(payload.get("qty")) or payload["qty"] <= 0):
        raise ValidationError("數量必須是正整數")
    if action in {"returns.create", "returns.cancel"}:
        require_employee(payload.get("employee"))
    if action == "returns.set_sale":
        if "sale_id" not in payload: raise ValidationError("sale_id 為必填")
        if payload["sale_id"] is not None and not _is_int(payload["sale_id"]): raise ValidationError("sale_id 必須是整數")
    if action == "returns.set_reason":
        if "reason" not in payload: raise ValidationError("reason 為必填")
        _optional_text(payload, "reason")
    if action == "returns.set_disposition" and payload.get("disposition") not in DISPOSITIONS:
        raise ValidationError("處理方式不正確")
    if action == "returns.history":
        date_range(payload)

def _clean(text):
    return (text or "").strip() or None

class ReturnsRepository(BaseRepository):
    def session(self, rid): return self.one("SELECT * FROM ReturnSession WHERE return_id=?", (rid,))
    def require(self, rid, status, message):
        row = self.session(rid)
        if row is None: raise NotFoundError("找不到退貨單")
        if row["status"] != status: raise ConflictError(message)
        return row
    def require_open(self, rid): return self.require(rid, "open", "退貨單不是進行中")
    def require_pending(self, rid): return self.require(rid, "pending", "退貨單不是待核准狀態")
    def require_sale(self, sale_id):
        if sale_id is not None and self.one("SELECT 1 FROM Sale WHERE sale_id=?", (sale_id,)) is None:
            raise NotFoundError("找不到銷售單編號")
    def require_variant(self, vid):
        if self.one("SELECT 1 FROM Variant WHERE variant_id=?", (vid,)) is None:
            raise NotFoundError("找不到子產品")
    def update_item(self, sql, args):
        if self.execute(sql, args).rowcount == 0: raise NotFoundError("找不到退貨品項")

class ReturnsService:
    def __init__(self, repository):
        self.repo = repository
    def create(self, employee):
        cur = self.repo.execute("INSERT INTO ReturnSession(employee) VALUES(?)", (employee,))
        return {"return_id": cur.lastrowid}
    # 退貨頁只列最近 10 筆;更早的到紀錄查詢頁看
    def list(self): return [dict(r) for r in self.repo.all("SELECT * FROM ReturnSession ORDER BY return_id DESC LIMIT 10")]
    def history(self, filters):
        suffix, args = date_sql(filters, "COALESCE(approved_at,cancelled_at)")
        rows = self.repo.all("SELECT return_id FROM ReturnSession WHERE status IN ('closed','cancelled')" + suffix +
                             " ORDER BY return_id DESC", args)
        return [self.detail(r["return_id"]) for r in rows]
    def detail(self, rid):
        session = self.repo.session(rid)
        if session is None: raise NotFoundError("找不到退貨單")
        rows = self.repo.all("SELECT ri.*,p.name FROM ReturnItem ri JOIN Variant v ON ri.variant_id=v.variant_id "
                             "JOIN Product p ON v.product_id=p.product_id WHERE ri.return_id=? ORDER BY ri.id", (rid,))
        vids = [r["variant_id"] for r in rows]
        attrs = product_data.attrs_by_variant(self.repo.connection, vids)
        disp = product_data.display_attrs(self.repo.connection, vids)
        models = product_data.display_models_by_variant(self.repo.connection, vids)
        items = [{"variant_id": r["variant_id"], "name": r["name"], "attributes": attrs.get(r["variant_id"], {}),
                  "attr_display": disp.get(r["variant_id"], ""), "models": models.get(r["variant_id"], []), "qty": r["qty"], "sale_id": r["sale_id"], "reason": r["reason"],
                  "disposition": r["disposition"]} for r in rows]
        return {**dict(session), "items": items}
    def scan(self, rid, vid, qty):
        self.repo.require_open(rid)
        self.repo.require_variant(vid)
        self.repo.execute("INSERT INTO ReturnItem(return_id,variant_id,qty) VALUES(?,?,?) "
                          "ON CONFLICT(return_id,variant_id) DO UPDATE SET qty=qty+excluded.qty", (rid, vid, qty))
        return {"qty": self.repo.one("SELECT qty FROM ReturnItem WHERE return_id=? AND variant_id=?", (rid, vid))["qty"]}
    def set_qty(self, rid, vid, qty):
        self.repo.require_open(rid)
        self.repo.update_item("UPDATE ReturnItem SET qty=? WHERE return_id=? AND variant_id=?", (qty, rid, vid))
        return {"ok": True}
    def set_sale(self, rid, vid, sale_id):
        self.repo.require_open(rid)
        self.repo.require_sale(sale_id)
        self.repo.update_item("UPDATE ReturnItem SET sale_id=? WHERE return_id=? AND variant_id=?", (sale_id, rid, vid))
        return {"ok": True}
    def set_reason(self, rid, vid, reason):
        self.repo.require_open(rid)
        self.repo.update_item("UPDATE ReturnItem SET reason=? WHERE return_id=? AND variant_id=?", (_clean(reason), rid, vid))
        return {"ok": True}
    def remove_item(self, rid, vid):
        self.repo.require_open(rid)
        self.repo.update_item("DELETE FROM ReturnItem WHERE return_id=? AND variant_id=?", (rid, vid))
        return {"ok": True}
    def close(self, rid):
        self.repo.require_open(rid)
        if self.repo.one("SELECT 1 FROM ReturnItem WHERE return_id=? LIMIT 1", (rid,)) is None:
            raise ConflictError("退貨單沒有品項，無法送出")
        self.repo.execute("UPDATE ReturnSession SET status='pending',submitted_at=datetime('now','localtime') WHERE return_id=?", (rid,))
        return {"ok": True}
    def withdraw(self, rid):
        self.repo.require_pending(rid)
        self.repo.execute("UPDATE ReturnSession SET status='open',submitted_at=NULL WHERE return_id=?", (rid,))
        return {"ok": True}
    def set_disposition(self, rid, vid, disposition):
        self.repo.require_pending(rid)
        self.repo.update_item("UPDATE ReturnItem SET disposition=? WHERE return_id=? AND variant_id=?", (disposition, rid, vid))
        return {"ok": True}
    def approve(self, rid):
        # 只有「加回庫存」的品項寫入庫存流水;不入庫與不同意只留在退貨單上
        session = self.repo.require_pending(rid)
        self.repo.execute("UPDATE ReturnSession SET status='closed',approved_at=datetime('now','localtime') WHERE return_id=?", (rid,))
        for row in self.repo.all("SELECT variant_id,qty FROM ReturnItem WHERE return_id=? AND disposition='restock'", (rid,)):
            self.repo.execute("INSERT INTO StockMovement(variant_id,qty,kind,ref_id,note,employee) VALUES(?,?,'return',?,'退貨加回',?)",
                              (row["variant_id"], row["qty"], rid, session["employee"]))
        return {"ok": True}
    def cancel(self, rid, employee, is_admin):
        session = self.repo.session(rid)
        if session is None: raise NotFoundError("找不到退貨單")
        if session["status"] == "pending" and not is_admin: raise ForbiddenError()
        if self.repo.execute("UPDATE ReturnSession SET status='cancelled',cancelled_at=datetime('now','localtime'),"
                             "cancelled_by=? WHERE return_id=? AND status IN ('open','pending')",
                             (employee, rid)).rowcount == 0:
            raise ConflictError("退貨單已結案或已作廢，無法作廢")
        return {"ok": True}
    def pending_count(self):
        return {"count": self.repo.one("SELECT COUNT(*) AS n FROM ReturnSession WHERE status='pending'")["n"]}

READ_ACTIONS = {"returns.list", "returns.history", "returns.detail", "returns.pending_count"}
ADMIN_ONLY = {"returns.set_disposition", "returns.approve", "returns.pending_count"}

class ReturnsFacade(BaseFacade):
    ACTIONS = {"returns.create", "returns.list", "returns.history", "returns.detail",
               "returns.scan", "returns.set_qty", "returns.set_sale", "returns.set_reason", "returns.remove_item",
               "returns.close", "returns.withdraw", "returns.set_disposition", "returns.approve",
               "returns.cancel", "returns.pending_count"}
    ERROR_MESSAGE = "不支援的退貨操作"
    def __init__(self, db_path, session=None):
        super().__init__(db_path)
        self.session = session
    def _is_admin(self): return self.session is not None and self.session.is_admin()
    def _prepare_payload(self, action, payload):
        if action in ADMIN_ONLY and not self._is_admin():
            raise ForbiddenError()
        _validate(action, payload)
        return payload
    def _dispatch(self, action, payload, conn):
        # 狀態與庫存異動在同一交易，避免核准與修改同時執行。
        if action not in READ_ACTIONS:
            conn.execute("BEGIN IMMEDIATE")
        s = ReturnsService(ReturnsRepository(conn))
        p = payload
        if action == "returns.create": return s.create(p["employee"])
        if action == "returns.list": return s.list()
        if action == "returns.history": return s.history(date_range(p))
        if action == "returns.detail": return s.detail(p["return_id"])
        if action == "returns.scan": return s.scan(p["return_id"], p["variant_id"], p["qty"])
        if action == "returns.set_qty": return s.set_qty(p["return_id"], p["variant_id"], p["qty"])
        if action == "returns.set_sale": return s.set_sale(p["return_id"], p["variant_id"], p["sale_id"])
        if action == "returns.set_reason": return s.set_reason(p["return_id"], p["variant_id"], p["reason"])
        if action == "returns.remove_item": return s.remove_item(p["return_id"], p["variant_id"])
        if action == "returns.close": return s.close(p["return_id"])
        if action == "returns.withdraw": return s.withdraw(p["return_id"])
        if action == "returns.cancel": return s.cancel(p["return_id"], p["employee"], self._is_admin())
        # 管理者動作在交易內再確認一次身分(確認視窗期間可能已閒置登出)
        if not self._is_admin(): raise ForbiddenError()
        if action == "returns.set_disposition": return s.set_disposition(p["return_id"], p["variant_id"], p["disposition"])
        if action == "returns.approve": return s.approve(p["return_id"])
        return s.pending_count()
