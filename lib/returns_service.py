from lib import correction_service, product_data, stock_ledger
from lib.application import BaseFacade, BaseRepository
from lib.application_errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from lib.product_rules import allow_keys as _allow, date_range, date_sql, is_int as _is_int, require_employee

DISPOSITIONS = ("restock", "discard", "reject")
RETURN_KINDS = ("normal", "repair")
HISTORY_KINDS = RETURN_KINDS
REASON_MAX = 30
# 返修是整張單的模式(ReturnSession.kind),返修單的品項沒有個別處理方式
REPAIR_SQL = "kind='repair'"

def _optional_text(payload, key):
    if payload.get(key) is not None and not isinstance(payload[key], str):
        raise ValidationError(f"{key} 必須是字串")

def _validate(action, payload):
    allowed = {"returns.create":{"employee"}, "returns.list":set(),
        "returns.history":{"date_from","date_to","kind"}, "returns.detail":{"return_id"},
        "returns.repair_receive":{"return_id","employee","items"},
        "returns.close_item":{"return_id","item_id","reason"}, "returns.reopen_item":{"return_id","item_id"},
        "returns.scan":{"return_id","variant_id","qty"}, "returns.set_qty":{"return_id","item_id","qty"},
        "returns.set_reason":{"return_id","item_id","reason"},
        "returns.set_sale":{"return_id","item_id","sale_id"},
        "returns.remove_item":{"return_id","item_id"},
        "returns.close":{"return_id"}, "returns.withdraw":{"return_id"},
        "returns.set_disposition":{"return_id","item_id","disposition"},
        "returns.set_kind":{"return_id","kind"},
        "returns.approve":{"return_id","kind","items"}, "returns.cancel":{"return_id","employee"},
        "returns.pending_count":set()}[action]
    _allow(payload, allowed)
    for key in ("return_id", "variant_id", "item_id"):
        if key in allowed and not _is_int(payload.get(key)): raise ValidationError(f"{key} 必須是整數")
    if action in {"returns.scan", "returns.set_qty"} and (not _is_int(payload.get("qty")) or payload["qty"] <= 0):
        raise ValidationError("數量必須是正整數")
    if action == "returns.repair_receive":
        items = payload.get("items")
        if not isinstance(items, list) or not items: raise ValidationError("items 必須是非空清單")
        for it in items:
            if not isinstance(it, dict) or set(it) != {"item_id", "qty"}: raise ValidationError("items 格式不正確")
            if not _is_int(it["item_id"]): raise ValidationError("item_id 必須是整數")
            if not _is_int(it["qty"]) or it["qty"] <= 0: raise ValidationError("數量必須是正整數")
        if len({it["item_id"] for it in items}) != len(items): raise ValidationError("品項重複")
    if action == "returns.close_item":
        if not isinstance(payload.get("reason"), str) or not payload["reason"].strip():
            raise ValidationError("請輸入結案理由")
        if len(payload["reason"].strip()) > REASON_MAX: raise ValidationError(f"結案理由最多 {REASON_MAX} 字")
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
    if action == "returns.approve":
        # 前端把確認框上顯示的種類與各品項處理方式一起送來,後端核對一致才入帳
        if payload.get("kind") not in RETURN_KINDS: raise ValidationError("退貨種類不正確")
        items = payload.get("items")
        if not isinstance(items, list): raise ValidationError("items 必須是清單")
        for it in items:
            if not isinstance(it, dict) or set(it) != {"item_id", "qty", "disposition"}: raise ValidationError("items 格式不正確")
            if not _is_int(it["item_id"]): raise ValidationError("item_id 必須是整數")
            if not _is_int(it["qty"]) or it["qty"] <= 0: raise ValidationError("數量必須是正整數")
            if it["disposition"] is not None and it["disposition"] not in DISPOSITIONS: raise ValidationError("處理方式不正確")
        if len({it["item_id"] for it in items}) != len(items): raise ValidationError("品項重複")
    if action == "returns.set_kind" and payload.get("kind") not in RETURN_KINDS:
        raise ValidationError("退貨種類不正確")
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
        """{退貨品項 id: 已返新件數}"""
        ids = [r["id"] for r in self.all("SELECT id FROM ReturnItem WHERE return_id=?", (rid,))]
        return stock_ledger.repaired_by_item(self.connection, ids)
    def label_codes(self, vids):
        if not vids: return {}
        marks = ",".join("?" * len(vids))
        return {r["variant_id"]: r["code"] for r in self.all(
            "SELECT variant_id,COALESCE(MIN(CASE WHEN source='store' THEN barcode END),"
            f"MIN(CASE WHEN source='factory' THEN barcode END)) code FROM Barcode WHERE variant_id IN ({marks}) GROUP BY variant_id", vids)}
    def repair_item(self, rid, item_id):
        row = self.one("SELECT i.* FROM ReturnItem i JOIN ReturnSession s ON s.return_id=i.return_id "
                       "WHERE i.return_id=? AND i.id=? AND s.kind='repair'", (rid, item_id))
        if row is None: raise NotFoundError("找不到返修品項")
        return row
    # 每個返修品項都返新或結案完,整張單才算完成;撤回結案時退回返修中
    def refresh_completion(self, rid):
        done = self.repaired(rid)
        left = [r for r in self.all("SELECT id,qty,closed_qty FROM ReturnItem WHERE return_id=?", (rid,))
                if r["qty"] - r["closed_qty"] - done.get(r["id"], 0) > 0]
        if left:
            self.execute("UPDATE ReturnSession SET status='repairing',completed_at=NULL WHERE return_id=?", (rid,))
        else:
            self.execute("UPDATE ReturnSession SET status='closed',completed_at=datetime('now','localtime') "
                         "WHERE return_id=? AND status='repairing'", (rid,))
        return not left
    def require_sale(self, sale_id):
        if sale_id is None:
            return
        row = self.one("SELECT voided_at FROM Sale WHERE sale_id=?", (sale_id,))
        if row is None:
            raise NotFoundError("找不到銷售單編號")
        if row["voided_at"] is not None:
            raise ConflictError("此銷售單已作廢，不可填為原銷售單")
    def require_variant(self, vid):
        if self.one("SELECT 1 FROM Variant WHERE variant_id=?", (vid,)) is None:
            raise NotFoundError("找不到子產品")
    def update_item(self, sql, args):
        if self.execute(sql, args).rowcount == 0: raise NotFoundError("找不到退貨品項")
    def check_kind(self, rid):
        """單據種類與品項處理方式一致:返修單的品項全為 NULL,一般單全有處理方式。
        跨兩張表,不寫 trigger,所有會改到兩邊的寫入在同一交易內呼叫。"""
        bad = self.one("SELECT 1 FROM ReturnItem i JOIN ReturnSession s ON s.return_id=i.return_id "
                       "WHERE i.return_id=? AND (s.kind='repair') <> (i.disposition IS NULL) LIMIT 1", (rid,))
        if bad: raise ConflictError("退貨品項的處理方式與退貨單種類不一致")

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
            # 返修單核准前(進行中/待核准)仍算一般退貨,核准後才移到返修區
            suffix, args = date_sql(filters, "COALESCE(approved_at,submitted_at,started_at)")
            rows = self.repo.all(f"SELECT return_id FROM ReturnSession WHERE status<>'cancelled' AND NOT "
                                 f"(status IN ('repairing','closed') AND {REPAIR_SQL})" + suffix + " ORDER BY return_id DESC", args)
        return self._details([r["return_id"] for r in rows])
    def detail(self, rid):
        if self.repo.session(rid) is None: raise NotFoundError("找不到退貨單")
        return self._details([rid])[0]
    # 多張單一次撈品項、屬性、返新數、標籤碼再分組,避免逐張查詢
    def _details(self, rids):
        if not rids: return []
        marks = ",".join("?" * len(rids))
        sessions = {r["return_id"]: r for r in self.repo.all(f"SELECT * FROM ReturnSession WHERE return_id IN ({marks})", rids)}
        all_rows = self.repo.all("SELECT ri.*,p.name FROM ReturnItem ri JOIN Variant v ON ri.variant_id=v.variant_id "
                                 f"JOIN Product p ON v.product_id=p.product_id WHERE ri.return_id IN ({marks}) ORDER BY ri.id", rids)
        vids = list(dict.fromkeys(r["variant_id"] for r in all_rows))
        attrs = product_data.attrs_by_variant(self.repo.connection, vids)
        disp = product_data.display_attrs(self.repo.connection, vids)
        models = product_data.display_models_by_variant(self.repo.connection, vids)
        codes = self.repo.label_codes(vids)
        done = stock_ledger.repaired_by_item(self.repo.connection, [r["id"] for r in all_rows])
        grouped = {rid: [] for rid in rids}
        for r in all_rows: grouped[r["return_id"]].append(r)
        out = [self._shape(sessions[rid], grouped[rid], attrs, disp, models, done, codes) for rid in rids]
        return correction_service.attach(self.repo.connection, "return", out, "return_id")
    @staticmethod
    def _shape(session, rows, attrs, disp, models, repaired, codes):
        items = [{"id": r["id"], "variant_id": r["variant_id"], "name": r["name"], "attributes": attrs.get(r["variant_id"], {}),
                  "attr_display": disp.get(r["variant_id"], ""), "models": models.get(r["variant_id"], []), "qty": r["qty"], "sale_id": r["sale_id"], "reason": r["reason"],
                  "disposition": r["disposition"], "closed_qty": r["closed_qty"], "closed_reason": r["closed_reason"],
                  "closed_at": r["closed_at"], "repaired_qty": repaired.get(r["id"], 0),
                  "label_barcode": codes.get(r["variant_id"])} for r in rows]
        return {**dict(session), "items": items}
    def scan(self, rid, vid, qty):
        self.repo.require_open(rid)
        self.repo.require_variant(vid)
        # 新品項依單據種類:返修單(抽回後再掃)沒有個別處理方式,一般單預設加回庫存
        session = self.repo.session(rid)
        disp = None if session["kind"] == "repair" else "restock"
        self.repo.execute("INSERT INTO ReturnItem(return_id,variant_id,qty,disposition) VALUES(?,?,?,?) "
                          "ON CONFLICT(return_id,variant_id) DO UPDATE SET qty=qty+excluded.qty", (rid, vid, qty, disp))
        self.repo.check_kind(rid)
        return {"qty": self.repo.one("SELECT qty FROM ReturnItem WHERE return_id=? AND variant_id=?", (rid, vid))["qty"]}
    def set_qty(self, rid, item_id, qty):
        self.repo.require_open(rid)
        self.repo.update_item("UPDATE ReturnItem SET qty=? WHERE return_id=? AND id=?", (qty, rid, item_id))
        return {"ok": True}
    def set_sale(self, rid, item_id, sale_id):
        self.repo.require_open(rid)
        self.repo.require_sale(sale_id)
        self.repo.update_item("UPDATE ReturnItem SET sale_id=? WHERE return_id=? AND id=?", (sale_id, rid, item_id))
        return {"ok": True}
    def set_reason(self, rid, item_id, reason):
        self.repo.require_open(rid)
        self.repo.update_item("UPDATE ReturnItem SET reason=? WHERE return_id=? AND id=?", (_clean(reason), rid, item_id))
        return {"ok": True}
    def remove_item(self, rid, item_id):
        self.repo.require_open(rid)
        self.repo.update_item("DELETE FROM ReturnItem WHERE return_id=? AND id=?", (rid, item_id))
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
    def set_disposition(self, rid, item_id, disposition):
        # 只改一般退貨的單一品項;整張單切換返修走 set_kind
        session = self.repo.require_pending(rid)
        if session["kind"] != "normal": raise ConflictError("返修單的品項不逐項選處理方式")
        self.repo.update_item("UPDATE ReturnItem SET disposition=? WHERE return_id=? AND id=?", (disposition, rid, item_id))
        self.repo.check_kind(rid)
        return {"ok": True}
    def set_kind(self, rid, kind):
        # 整張單切換:切返修時品項處理方式全部清空,切回一般時全部設回加回庫存;
        # 種類沒變就不動,重送不得蓋掉已選的不入庫／不同意
        session = self.repo.require_pending(rid)
        if session["kind"] == kind: return {"ok": True}
        self.repo.execute("UPDATE ReturnSession SET kind=? WHERE return_id=?", (kind, rid))
        self.repo.execute("UPDATE ReturnItem SET disposition=? WHERE return_id=?",
                          (None if kind == "repair" else "restock", rid))
        self.repo.check_kind(rid)
        return {"ok": True}
    def approve(self, rid, kind, items):
        # 只有「加回庫存」的品項寫入庫存流水;不入庫與不同意只留在退貨單上
        session = self.repo.require_pending(rid)
        self.repo.check_kind(rid)
        # 確認框顯示的內容(種類、品項、數量、處理方式)必須等於資料庫現況;
        # 切換尚未讀回、或抽回改數量後重送,都拒絕,避免照未確認的內容入帳
        saved = {r["id"]: (r["qty"], r["disposition"]) for r in self.repo.all("SELECT id,qty,disposition FROM ReturnItem WHERE return_id=?", (rid,))}
        if session["kind"] != kind or saved != {it["item_id"]: (it["qty"], it["disposition"]) for it in items}:
            raise ConflictError("退貨單內容已變更，請重新確認後再核准")
        if session["kind"] == "repair":
            # 返修品項等返新進貨或結案才完成,核准當下不動庫存
            self.repo.execute("UPDATE ReturnSession SET status='repairing',approved_at=datetime('now','localtime') WHERE return_id=?", (rid,))
            return {"ok": True}
        self.repo.execute("UPDATE ReturnSession SET status='closed',approved_at=datetime('now','localtime') WHERE return_id=?", (rid,))
        for row in self.repo.all("SELECT id FROM ReturnItem WHERE return_id=? AND disposition='restock' ORDER BY id", (rid,)):
            stock_ledger.post(self.repo.connection, "return", row["id"])
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
        for it in items:
            self.repo.repair_item(rid, it["item_id"])  # 須是本單的返修品項
            stock_ledger.post(self.repo.connection, "repair", it["item_id"], it["qty"], employee)
        return {"done": self.repo.refresh_completion(rid)}
    def close_item(self, rid, item_id, reason):
        self.repo.require(rid, "repairing", "退貨單不是返修中")
        row = self.repo.repair_item(rid, item_id)
        left = row["qty"] - row["closed_qty"] - self.repo.repaired(rid).get(item_id, 0)
        if left <= 0: raise ConflictError("此品項已處理完畢")
        self.repo.execute("UPDATE ReturnItem SET closed_qty=closed_qty+?,closed_reason=?,closed_at=datetime('now','localtime') "
                          "WHERE return_id=? AND id=?", (left, reason.strip(), rid, item_id))
        return {"done": self.repo.refresh_completion(rid)}
    def reopen_item(self, rid, item_id):
        session = self.repo.session(rid)
        if session is None: raise NotFoundError("找不到退貨單")
        if session["status"] not in ("repairing", "closed"): raise ConflictError("退貨單不是返修單")
        if self.repo.repair_item(rid, item_id)["closed_qty"] == 0: raise ConflictError("此品項沒有結案")
        self.repo.execute("UPDATE ReturnItem SET closed_qty=0,closed_reason=NULL,closed_at=NULL WHERE return_id=? AND id=?", (rid, item_id))
        self.repo.refresh_completion(rid)
        return {"ok": True}
    def pending_count(self):
        return {"count": self.repo.one("SELECT COUNT(*) AS n FROM ReturnSession WHERE status='pending'")["n"]}

READ_ACTIONS = {"returns.list", "returns.history", "returns.detail", "returns.pending_count"}
ADMIN_ONLY = {"returns.set_disposition", "returns.set_kind", "returns.approve", "returns.pending_count", "returns.close_item", "returns.reopen_item"}

class ReturnsFacade(BaseFacade):
    ACTIONS = {"returns.create", "returns.list", "returns.history", "returns.detail",
               "returns.scan", "returns.set_qty", "returns.set_sale", "returns.set_reason", "returns.remove_item",
               "returns.close", "returns.withdraw", "returns.set_disposition", "returns.set_kind", "returns.approve",
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
        if action == "returns.set_qty": return s.set_qty(p["return_id"], p["item_id"], p["qty"])
        if action == "returns.set_sale": return s.set_sale(p["return_id"], p["item_id"], p["sale_id"])
        if action == "returns.set_reason": return s.set_reason(p["return_id"], p["item_id"], p["reason"])
        if action == "returns.remove_item": return s.remove_item(p["return_id"], p["item_id"])
        if action == "returns.close": return s.close(p["return_id"])
        if action == "returns.withdraw": return s.withdraw(p["return_id"])
        if action == "returns.cancel": return s.cancel(p["return_id"], p["employee"], self._is_admin())
        if action == "returns.repair_receive": return s.repair_receive(p["return_id"], p["employee"], p["items"])
        # 管理者動作在交易內再確認一次身分(確認視窗期間可能已閒置登出)
        if not self._is_admin(): raise ForbiddenError()
        if action == "returns.set_disposition": return s.set_disposition(p["return_id"], p["item_id"], p["disposition"])
        if action == "returns.set_kind": return s.set_kind(p["return_id"], p["kind"])
        if action == "returns.approve": return s.approve(p["return_id"], p["kind"], p["items"])
        if action == "returns.close_item": return s.close_item(p["return_id"], p["item_id"], p["reason"])
        if action == "returns.reopen_item": return s.reopen_item(p["return_id"], p["item_id"])
        return s.pending_count()
