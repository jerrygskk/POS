from collections.abc import Mapping

from lib import product_data
from lib.application import TransactionRunner
from lib.application_errors import ConflictError, NotFoundError, ValidationError
from lib.db import db_conn

def _is_int(value): return isinstance(value, int) and not isinstance(value, bool)

def _allow(payload, allowed):
    unknown = set(payload) - set(allowed)
    if unknown: raise ValidationError(f"不支援的欄位：{sorted(unknown)[0]}")

def _validate(action, payload):
    allowed = {"stocktake.create":{"operator","note"}, "stocktake.list":set(),
        "stocktake.detail":{"session_id"}, "stocktake.scan":{"session_id","variant_id","qty"},
        "stocktake.set_counted":{"session_id","variant_id","counted_qty"},
        "stocktake.close":{"session_id"}}[action]
    _allow(payload, allowed)
    for key in ("session_id", "variant_id"):
        if key in allowed and not _is_int(payload.get(key)): raise ValidationError(f"{key} 必須是整數")
    if action == "stocktake.scan" and (not _is_int(payload.get("qty")) or payload["qty"] <= 0):
        raise ValidationError("數量必須是正整數")
    if action == "stocktake.set_counted" and (not _is_int(payload.get("counted_qty")) or payload["counted_qty"] < 0):
        raise ValidationError("實盤數量必須是非負整數")
    if action == "stocktake.create":
        for key in ("operator", "note"):
            if payload.get(key) is not None and not isinstance(payload[key], str): raise ValidationError(f"{key} 必須是字串")

class StocktakeRepository:
    def __init__(self, connection): self.connection = connection
    def execute(self, sql, args=()): return self.connection.execute(sql, args)
    def one(self, sql, args=()): return self.execute(sql, args).fetchone()
    def all(self, sql, args=()): return self.execute(sql, args).fetchall()
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
        return self.execute("UPDATE StocktakeSession SET status='closed',ended_at=datetime('now','localtime') WHERE session_id=? AND status='open'", (sid,)).rowcount
    def add_adjustment(self, variant_id, qty, sid):
        self.execute("INSERT INTO StockMovement(variant_id,qty,kind,ref_id,note) VALUES(?,?,'adjust',?,'盤點調整')", (variant_id, qty, sid))

class StocktakeService:
    def __init__(self, repository): self.repo = repository
    def create(self, operator=None, note=None):
        cur=self.repo.execute("INSERT INTO StocktakeSession(operator,note) VALUES(?,?)", (operator,note)); return {"session_id":cur.lastrowid}
    def list(self): return [dict(r) for r in self.repo.all("SELECT * FROM StocktakeSession ORDER BY session_id DESC LIMIT 50")]
    def scan(self, sid, vid, qty):
        self.repo.require_open(sid)
        self.repo.require_variant(vid)
        row=self.repo.one("SELECT * FROM StocktakeItem WHERE session_id=? AND variant_id=?", (sid,vid))
        if row:
            counted=row["counted_qty"]+qty; system=row["system_qty"]
            self.repo.execute("UPDATE StocktakeItem SET counted_qty=? WHERE id=?", (counted,row["id"]))
        else:
            system=product_data.stock_of(self.repo.connection,vid); counted=qty
            self.repo.execute("INSERT INTO StocktakeItem(session_id,variant_id,system_qty,counted_qty) VALUES(?,?,?,?)", (sid,vid,system,counted))
        return {"system_qty":system,"counted_qty":counted}
    def set_counted(self,sid,vid,counted):
        self.repo.require_open(sid)
        if self.repo.execute("UPDATE StocktakeItem SET counted_qty=? WHERE session_id=? AND variant_id=?",(counted,sid,vid)).rowcount==0: raise NotFoundError("找不到盤點品項")
        return {"ok":True}
    def detail(self,sid):
        session=self.repo.session(sid)
        if session is None: raise NotFoundError("找不到盤點作業")
        rows=self.repo.all("SELECT si.*,p.name FROM StocktakeItem si JOIN Variant v ON si.variant_id=v.variant_id JOIN Product p ON v.product_id=p.product_id WHERE si.session_id=?",(sid,))
        vids=[r["variant_id"] for r in rows]; attrs=product_data.attrs_by_variant(self.repo.connection,vids); disp=product_data.display_attrs(self.repo.connection,vids)
        items=[{"variant_id":r["variant_id"],"name":r["name"],"attributes":attrs.get(r["variant_id"],{}),"attr_display":disp.get(r["variant_id"],""),"system_qty":r["system_qty"],"counted_qty":r["counted_qty"],"diff":r["counted_qty"]-r["system_qty"]} for r in rows]
        return {**dict(session),"items":items}
    def close(self,sid):
        if self.repo.close_open(sid)==0: self.repo.require_open(sid)
        for row in self.repo.all("SELECT variant_id,counted_qty-system_qty AS diff FROM StocktakeItem WHERE session_id=?",(sid,)):
            if row["diff"]: self.repo.add_adjustment(row["variant_id"],row["diff"],sid)
        return {"ok":True}

class StocktakeFacade:
    ACTIONS={"stocktake.create","stocktake.list","stocktake.detail","stocktake.scan","stocktake.set_counted","stocktake.close"}
    def __init__(self,db_path): self.runner=TransactionRunner(db_path,connection_context=db_conn)
    def invoke(self,action,payload=None):
        payload={} if payload is None else payload
        if action not in self.ACTIONS or not isinstance(payload,Mapping): raise ValidationError("不支援的盤點操作")
        _validate(action,payload)
        def work(conn):
            s=StocktakeService(StocktakeRepository(conn))
            if action=="stocktake.create": return s.create(payload.get("operator"),payload.get("note"))
            if action=="stocktake.list": return s.list()
            if action=="stocktake.detail": return s.detail(payload["session_id"])
            if action=="stocktake.scan": return s.scan(payload["session_id"],payload["variant_id"],payload["qty"])
            if action=="stocktake.set_counted": return s.set_counted(payload["session_id"],payload["variant_id"],payload["counted_qty"])
            return s.close(payload["session_id"])
        return self.runner.run(work)
