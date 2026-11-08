from lib import product_data
from lib.application import BaseFacade, BaseRepository
from lib.application_errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from lib.product_rules import allow_keys as _allow, date_range, date_sql, is_int as _is_int, require_employee

DISPOSITIONS = ("restock", "discard", "reject", "repair")
HISTORY_KINDS = ("normal", "repair")
REASON_MAX = 30
# 返修單:任一品項返修整張就是返修單,全部品項都是返修,不混其他處理方式;一般退貨不含返修
REPAIR_SQL = "EXISTS (SELECT 1 FROM ReturnItem x WHERE x.return_id=ReturnSession.return_id AND x.disposition='repair')"

def _optional_text(payload, key):
    if payload.get(key) is not None and not isinstance(payload[key], str):
        raise ValidationError(f"{key} 必須是字串")

def _validate(action, payload):
    allowed = {"returns.create":{"employee"}, "returns.list":set(),
        "returns.history":{"date_from","date_to","kind"}, "returns.detail":{"return_id"},
        "returns.repair_receive":{"return_id","employee","items"},
        "returns.close_item":{"return_id","variant_id","reason"}, "returns.reopen_item":{"return_id","variant_id"},
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
    if action == "returns.repair_receive":
        items = payload.get("items")
        if not isinstance(items, list) or not items: raise ValidationError("items 必須是非空清單")
        for it in items:
            if not isinstance(it, dict) or set(it) != {"variant_id", "qty"}: raise ValidationError("items 格式不正確")
            if not _is_int(it["variant_id"]): raise ValidationError("variant_id 必須是整數")
            if not _is_int(it["qty"]) or it["qty"] <= 0: raise ValidationError("數量必須是正整數")
        if len({it["variant_id"] for it in items}) != len(items): raise ValidationError("品項重複")
    if action == "returns.close_item":
        if not isinstance(payload.get("reason"), str) or not payload["reason"].strip():
            raise ValidationError("請輸入結案理由")
    if action in {"returns.create", "returns.cancel", "returns.repair_receive"}:
        require_employee(payload.get("employee"))
    if action == "returns.set_sale":
        if "sale_id" not in payload: raise ValidationError("sale_id 為必填")
        if payload["sale_id"] is not None and not _is_int(payload["sale_id"]): raise ValidationError("sale_id 必須是整數")
    if action == "returns.set_reason":
        if "reason" not in payload: raise ValidationError("reason 為必填")
        _optional_text(payload, "reason")
        # 退貨原因在返新進貨與紀錄查詢都要一行顯示完,故限 30 字
        if len((payload["reason"] or "").strip()) > REASON_MAX: raise ValidationError(f"退貨原因最多 {REASON_MAX} 字")
    if action == "returns.set_disposition" and payload.get("disposition") not in DISPOSITIONS:
        raise ValidationError("處理方式不正確")
    if action == "returns.history":
        if payload.get("kind", "normal") not in HISTORY_KINDS: raise ValidationError("退貨種類不正確")
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
    def repaired(self, rid):
        return {r["variant_id"]: r["n"] for r in self.all(
            "SELECT variant_id, SUM(qty) AS n FROM StockMovement WHERE kind='repair' AND ref_id=? GROUP BY variant_id", (rid,))}
    def label_codes(self, vids):
        if not vids: return {}
        marks = ",".join("?" * len(vids))
        return {r["variant_id"]: r["code"] for r in self.all(
            "SELECT variant_id,COALESCE(MIN(CASE WHEN source='store' THEN barcode END),"
            f"MIN(CASE WHEN source='factory' THEN barcode END)) code FROM Barcode WHERE variant_id IN ({marks}) GROUP BY variant_id", vids)}
    def repair_item(self, rid, vid):
        row = self.one("SELECT * FROM ReturnItem WHERE return_id=? AND variant_id=? AND disposition='repair'", (rid, vid))
        if row is None: raise NotFoundError("找不到返修品項")
        return row
    # 每個返修品項都返新或結案完,整張單才算完成;撤回結案時退回返修中
    def refresh_completion(self, rid):
        done = self.repaired(rid)
        left = [r for r in self.all("SELECT variant_id,qty,closed_qty FROM ReturnItem WHERE return_id=? AND disposition='repair'", (rid,))
                if r["qty"] - r["closed_qty"] - done.get(r["variant_id"], 0) > 0]
        if left:
            self.execute("UPDATE ReturnSession SET status='repairing',completed_at=NULL WHERE return_id=?", (rid,))
        else:
            self.execute("UPDATE ReturnSession SET status='closed',completed_at=datetime('now','localtime') "
                         "WHERE return_id=? AND status='repairing'", (rid,))
        return not left
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
    # 紀錄查詢分兩區:一般退貨列作廢以外的所有狀態;返修退貨的返修中一律列出(不論日期),完成的依完成日期篩
    def history(self, filters, kind="normal"):
        if kind == "repair":
            suffix, args = date_sql(filters, "completed_at")
            rows = self.repo.all(f"SELECT return_id FROM ReturnSession WHERE {REPAIR_SQL} AND (status='repairing' OR "
                                 "(status='closed'" + suffix + ")) ORDER BY return_id DESC", args)
        else:
            suffix, args = date_sql(filters, "COALESCE(approved_at,submitted_at,started_at)")
            rows = self.repo.all(f"SELECT return_id FROM ReturnSession WHERE status<>'cancelled' AND NOT {REPAIR_SQL}" + suffix +
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
        repaired = self.repo.repaired(rid)
        codes = self.repo.label_codes(vids)
        items = [{"variant_id": r["variant_id"], "name": r["name"], "attributes": attrs.get(r["variant_id"], {}),
                  "attr_display": disp.get(r["variant_id"], ""), "models": models.get(r["variant_id"], []), "qty": r["qty"], "sale_id": r["sale_id"], "reason": r["reason"],
                  "disposition": r["disposition"], "closed_qty": r["closed_qty"], "closed_reason": r["closed_reason"],
                  "closed_at": r["closed_at"], "repaired_qty": repaired.get(r["variant_id"], 0),
                  "label_barcode": codes.get(r["variant_id"])} for r in rows]
        return {**dict(session), "is_repair": any(r["disposition"] == "repair" for r in rows), "items": items}
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
        # 返修是整張單的模式:改成返修時全部品項一起改返修;從返修改回別的處理方式時全部品項一起改回
        self.repo.require_pending(rid)
        old = self.repo.one("SELECT disposition FROM ReturnItem WHERE return_id=? AND variant_id=?", (rid, vid))
        if old is None: raise NotFoundError("找不到退貨品項")
        if disposition == "repair" or old["disposition"] == "repair":
            self.repo.execute("UPDATE ReturnItem SET disposition=? WHERE return_id=?", (disposition, rid))
        self.repo.update_item("UPDATE ReturnItem SET disposition=? WHERE return_id=? AND variant_id=?", (disposition, rid, vid))
        return {"ok": True}
    def approve(self, rid):
        # 只有「加回庫存」的品項寫入庫存流水;不入庫與不同意只留在退貨單上
        session = self.repo.require_pending(rid)
        kinds = {r["disposition"] for r in self.repo.all("SELECT disposition FROM ReturnItem WHERE return_id=?", (rid,))}
        if "repair" in kinds:
            if kinds != {"repair"}: raise ConflictError("返修單的品項只能全部選返修不入庫")
            # 返修品項等返新進貨或結案才完成,核准當下不動庫存
            self.repo.execute("UPDATE ReturnSession SET status='repairing',approved_at=datetime('now','localtime') WHERE return_id=?", (rid,))
            return {"ok": True}
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
    def repair_receive(self, rid, employee, items):
        self.repo.require(rid, "repairing", "退貨單不是返修中")
        done = self.repo.repaired(rid)
        for it in items:
            row = self.repo.repair_item(rid, it["variant_id"])
            left = row["qty"] - row["closed_qty"] - done.get(it["variant_id"], 0)
            if it["qty"] > left: raise ConflictError(f"返新數量超過未處理件數（剩 {left} 件）")
        for it in items:
            self.repo.execute("INSERT INTO StockMovement(variant_id,qty,kind,ref_id,note,employee) VALUES(?,?,'repair',?,'返新進貨',?)",
                              (it["variant_id"], it["qty"], rid, employee))
        return {"done": self.repo.refresh_completion(rid)}
    def close_item(self, rid, vid, reason):
        self.repo.require(rid, "repairing", "退貨單不是返修中")
        row = self.repo.repair_item(rid, vid)
        left = row["qty"] - row["closed_qty"] - self.repo.repaired(rid).get(vid, 0)
        if left <= 0: raise ConflictError("此品項已處理完畢")
        self.repo.execute("UPDATE ReturnItem SET closed_qty=closed_qty+?,closed_reason=?,closed_at=datetime('now','localtime') "
                          "WHERE return_id=? AND variant_id=?", (left, reason.strip(), rid, vid))
        return {"done": self.repo.refresh_completion(rid)}
    def reopen_item(self, rid, vid):
        session = self.repo.session(rid)
        if session is None: raise NotFoundError("找不到退貨單")
        if session["status"] not in ("repairing", "closed"): raise ConflictError("退貨單不是返修單")
        if self.repo.repair_item(rid, vid)["closed_qty"] == 0: raise ConflictError("此品項沒有結案")
        self.repo.execute("UPDATE ReturnItem SET closed_qty=0,closed_reason=NULL,closed_at=NULL WHERE return_id=? AND variant_id=?", (rid, vid))
        self.repo.refresh_completion(rid)
        return {"ok": True}
    def pending_count(self):
        return {"count": self.repo.one("SELECT COUNT(*) AS n FROM ReturnSession WHERE status='pending'")["n"]}

READ_ACTIONS = {"returns.list", "returns.history", "returns.detail", "returns.pending_count"}
ADMIN_ONLY = {"returns.set_disposition", "returns.approve", "returns.pending_count", "returns.close_item", "returns.reopen_item"}

class ReturnsFacade(BaseFacade):
    ACTIONS = {"returns.create", "returns.list", "returns.history", "returns.detail",
               "returns.scan", "returns.set_qty", "returns.set_sale", "returns.set_reason", "returns.remove_item",
               "returns.close", "returns.withdraw", "returns.set_disposition", "returns.approve",
               "returns.cancel", "returns.pending_count", "returns.repair_receive", "returns.close_item", "returns.reopen_item"}
    ERROR_MESSAGE = "不支援的退貨操作"
    def __init__(self, db_path, session=None):
        super().__init__(db_path)
        self.session = session
    def _is_admin(self): return self.session is not None and self.session.is_admin()
    def _prepare_payload(self, action, payload):
        if action in ADMIN_ONLY and not self._is_admin():
            raise ForbiddenError()
        _validate(action, payload)
        if action == "returns.history":
            payload = {**payload, "kind": payload.get("kind", "normal")}
        return payload
    def _dispatch(self, action, payload, conn):
        # 狀態與庫存異動在同一交易，避免核准與修改同時執行。
        if action not in READ_ACTIONS:
            conn.execute("BEGIN IMMEDIATE")
        s = ReturnsService(ReturnsRepository(conn))
        p = payload
        if action == "returns.create": return s.create(p["employee"])
        if action == "returns.list": return s.list()
        if action == "returns.history":
            return s.history(date_range(p), p["kind"])
        if action == "returns.detail": return s.detail(p["return_id"])
        if action == "returns.scan": return s.scan(p["return_id"], p["variant_id"], p["qty"])
        if action == "returns.set_qty": return s.set_qty(p["return_id"], p["variant_id"], p["qty"])
        if action == "returns.set_sale": return s.set_sale(p["return_id"], p["variant_id"], p["sale_id"])
        if action == "returns.set_reason": return s.set_reason(p["return_id"], p["variant_id"], p["reason"])
        if action == "returns.remove_item": return s.remove_item(p["return_id"], p["variant_id"])
        if action == "returns.close": return s.close(p["return_id"])
        if action == "returns.withdraw": return s.withdraw(p["return_id"])
        if action == "returns.cancel": return s.cancel(p["return_id"], p["employee"], self._is_admin())
        if action == "returns.repair_receive": return s.repair_receive(p["return_id"], p["employee"], p["items"])
        # 管理者動作在交易內再確認一次身分(確認視窗期間可能已閒置登出)
        if not self._is_admin(): raise ForbiddenError()
        if action == "returns.set_disposition": return s.set_disposition(p["return_id"], p["variant_id"], p["disposition"])
        if action == "returns.approve": return s.approve(p["return_id"])
        if action == "returns.close_item": return s.close_item(p["return_id"], p["variant_id"], p["reason"])
        if action == "returns.reopen_item": return s.reopen_item(p["return_id"], p["variant_id"])
        return s.pending_count()
