import sqlite3

from lib import product_data
from lib.application import BaseFacade, BaseRepository
from lib.application_errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from lib.product_rules import allow_keys as _allow, is_int as _is_int, require_employee

ACTIVE_SESSION_MESSAGE = "尚有未完成的盤點單，請先完成或等待管理者核准"

def _validate(action, payload):
    allowed = {"stocktake.create":{"employee","note"}, "stocktake.list":set(),
        "stocktake.detail":{"session_id"}, "stocktake.scan":{"session_id","variant_id","qty"},
        "stocktake.set_counted":{"session_id","variant_id","counted_qty"},
        "stocktake.close":{"session_id"}, "stocktake.withdraw":{"session_id"},
        "stocktake.approve":{"session_id"}, "stocktake.pending_count":set()}[action]
    _allow(payload, allowed)
    for key in ("session_id", "variant_id"):
        if key in allowed and not _is_int(payload.get(key)): raise ValidationError(f"{key} 必須是整數")
    if action == "stocktake.scan" and (not _is_int(payload.get("qty")) or payload["qty"] <= 0):
        raise ValidationError("數量必須是正整數")
    if action == "stocktake.set_counted" and (not _is_int(payload.get("counted_qty")) or payload["counted_qty"] < 0):
        raise ValidationError("實盤數量必須是非負整數")
    if action == "stocktake.create":
        require_employee(payload.get("employee"))
        if payload.get("note") is not None and not isinstance(payload["note"], str): raise ValidationError("note 必須是字串")

class StocktakeRepository(BaseRepository):
    def session(self, sid): return self.one("SELECT * FROM StocktakeSession WHERE session_id=?", (sid,))
    def require_open(self, sid):
        row = self.session(sid)
        if row is None: raise NotFoundError("找不到盤點作業")
        if row["status"] != "open": raise ConflictError("盤點作業已結案")
        return row
    def require_variant(self, vid):
        if self.one("SELECT 1 FROM Variant WHERE variant_id=?", (vid,)) is None:
            raise NotFoundError("找不到子產品")
    def close_open(self, sid):
        return self.execute("UPDATE StocktakeSession SET status='pending',submitted_at=datetime('now','localtime') WHERE session_id=? AND status='open'", (sid,)).rowcount
    def require_recounted(self, sid):
        count = self.one("SELECT COUNT(*) AS n FROM StocktakeItem WHERE session_id=? AND needs_recount=1", (sid,))["n"]
        if count:
            raise ConflictError(f"有 {count} 項需重新清點，請確認數量後再結案")
    def add_adjustment(self, variant_id, qty, sid):
        self.execute("INSERT INTO StockMovement(variant_id,qty,kind,ref_id,note) VALUES(?,?,'adjust',?,'盤點調整')", (variant_id, qty, sid))

class StocktakeService:
    def __init__(self, repository, session=None):
        self.repo = repository
        self.session = session
    def create(self, employee, note=None):
        if self.repo.one("SELECT 1 FROM StocktakeSession WHERE status IN ('open','pending') LIMIT 1"):
            raise ConflictError(ACTIVE_SESSION_MESSAGE)
        try:
            cur = self.repo.execute("INSERT INTO StocktakeSession(employee,note) VALUES(?,?)", (employee,note))
        except sqlite3.IntegrityError as exc:
            if "idx_stocktake_one_active" in str(exc):
                raise ConflictError(ACTIVE_SESSION_MESSAGE) from exc
            raise
        return {"session_id":cur.lastrowid}
    def list(self): return [dict(r) for r in self.repo.all("SELECT * FROM StocktakeSession ORDER BY session_id DESC LIMIT 50")]
    def scan(self, sid, vid, qty):
        self.repo.require_open(sid)
        self.repo.require_variant(vid)
        row=self.repo.one("SELECT * FROM StocktakeItem WHERE session_id=? AND variant_id=?", (sid,vid))
        if row:
            counted=row["counted_qty"]+qty; system=row["system_qty"]
            self.repo.execute("UPDATE StocktakeItem SET counted_qty=?,needs_recount=0 WHERE id=?", (counted,row["id"]))
        else:
            system=product_data.stock_of(self.repo.connection,vid); counted=qty
            self.repo.execute("INSERT INTO StocktakeItem(session_id,variant_id,system_qty,counted_qty) VALUES(?,?,?,?)", (sid,vid,system,counted))
        return {"system_qty":system,"counted_qty":counted}
    def set_counted(self,sid,vid,counted):
        row = self.repo.session(sid)
        if row is None:
            raise NotFoundError("找不到盤點作業")
        if row["status"] == "pending":
            if self.session is None:
                raise ForbiddenError()
            self.session.require_admin()
        elif row["status"] != "open":
            raise ConflictError("盤點作業已結案")
        if self.repo.execute("UPDATE StocktakeItem SET counted_qty=?,needs_recount=0 WHERE session_id=? AND variant_id=?",(counted,sid,vid)).rowcount==0: raise NotFoundError("找不到盤點品項")
        return {"ok":True}
    def detail(self,sid):
        session=self.repo.session(sid)
        if session is None: raise NotFoundError("找不到盤點作業")
        rows=self.repo.all("SELECT si.*,p.name FROM StocktakeItem si JOIN Variant v ON si.variant_id=v.variant_id JOIN Product p ON v.product_id=p.product_id WHERE si.session_id=?",(sid,))
        vids=[r["variant_id"] for r in rows]; attrs=product_data.attrs_by_variant(self.repo.connection,vids); disp=product_data.display_attrs(self.repo.connection,vids)
        items=[{"variant_id":r["variant_id"],"name":r["name"],"attributes":attrs.get(r["variant_id"],{}),"attr_display":disp.get(r["variant_id"],""),"system_qty":r["system_qty"],"counted_qty":r["counted_qty"],"diff":r["counted_qty"]-r["system_qty"],"needs_recount":r["needs_recount"]} for r in rows]
        return {**dict(session),"items":items}
    def close(self,sid):
        self.repo.require_open(sid)
        self.repo.require_recounted(sid)
        if self.repo.close_open(sid)==0: self.repo.require_open(sid)
        return {"ok":True}
    def withdraw(self, sid):
        if self.repo.execute("UPDATE StocktakeSession SET status='open',submitted_at=NULL WHERE session_id=? AND status='pending'", (sid,)).rowcount == 0:
            raise ConflictError("盤點單不是待核准狀態")
        for row in self.repo.all("SELECT * FROM StocktakeItem WHERE session_id=?", (sid,)):
            system = product_data.stock_of(self.repo.connection, row["variant_id"])
            counted = row["counted_qty"] + system - row["system_qty"]
            self.repo.execute("UPDATE StocktakeItem SET system_qty=?,counted_qty=?,needs_recount=? WHERE id=?",
                              (system, max(0, counted), int(counted < 0), row["id"]))
        return {"ok": True}
    def approve(self, sid):
        self.repo.require_recounted(sid)
        if self.repo.execute("UPDATE StocktakeSession SET status='closed',approved_at=datetime('now','localtime'),ended_at=datetime('now','localtime') WHERE session_id=? AND status='pending'", (sid,)).rowcount == 0:
            raise ConflictError("盤點單不是待核准狀態")
        for row in self.repo.all("SELECT variant_id,counted_qty-system_qty AS diff FROM StocktakeItem WHERE session_id=?",(sid,)):
            if row["diff"]: self.repo.add_adjustment(row["variant_id"],row["diff"],sid)
        return {"ok":True}
    def pending_count(self):
        return {"count": self.repo.one("SELECT COUNT(*) AS n FROM StocktakeSession WHERE status='pending'")["n"]}

class StocktakeFacade(BaseFacade):
    ACTIONS={"stocktake.create","stocktake.list","stocktake.detail","stocktake.scan","stocktake.set_counted","stocktake.close","stocktake.withdraw","stocktake.approve","stocktake.pending_count"}
    ERROR_MESSAGE="不支援的盤點操作"
    def __init__(self, db_path, session=None):
        super().__init__(db_path)
        self.session = session
    def _prepare_payload(self,action,payload):
        if action in {"stocktake.approve", "stocktake.pending_count"}:
            if self.session is None:
                raise ForbiddenError()
            self.session.require_admin()
        _validate(action,payload)
        return payload
    def _dispatch(self,action,payload,conn):
        # 狀態、快照與異動都在同一交易，避免核准與修改數量同時執行。
        if action not in {"stocktake.list", "stocktake.detail", "stocktake.pending_count"}:
            conn.execute("BEGIN IMMEDIATE")
        s=StocktakeService(StocktakeRepository(conn), session=self.session)
        if action=="stocktake.create": return s.create(payload["employee"],payload.get("note"))
        if action=="stocktake.list": return s.list()
        if action=="stocktake.detail": return s.detail(payload["session_id"])
        if action=="stocktake.scan": return s.scan(payload["session_id"],payload["variant_id"],payload["qty"])
        if action=="stocktake.set_counted": return s.set_counted(payload["session_id"],payload["variant_id"],payload["counted_qty"])
        if action=="stocktake.close": return s.close(payload["session_id"])
        if action=="stocktake.withdraw": return s.withdraw(payload["session_id"])
        if action=="stocktake.approve":
            self.session.require_admin()
            return s.approve(payload["session_id"])
        return s.pending_count()
