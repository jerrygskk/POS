from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from lib.db import db_conn
from api.products import stock_of, attrs_by_variant, display_attrs

router = APIRouter(prefix="/api")

class SessionIn(BaseModel):
    operator: str | None = None
    note: str | None = None

class ScanIn(BaseModel):
    variant_id: int
    qty: int = 1

class SetIn(BaseModel):
    counted_qty: int

@router.post("/stocktake")
def open_session(body: SessionIn, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        cur = conn.execute("INSERT INTO StocktakeSession(operator,note) VALUES(?,?)",
                           (body.operator, body.note))
        conn.commit()
        return {"session_id": cur.lastrowid}

@router.get("/stocktake")
def list_sessions(request: Request):
    with db_conn(request.app.state.db_path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM StocktakeSession ORDER BY session_id DESC LIMIT 50")]

def _require_open(conn, sid):
    row = conn.execute("SELECT status FROM StocktakeSession WHERE session_id=?",
                       (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "查無此盤點單")
    if row["status"] != "open":
        raise HTTPException(409, "盤點單已結案")

@router.post("/stocktake/{sid}/scan")
def scan(sid: int, body: ScanIn, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        _require_open(conn, sid)
        row = conn.execute(
            "SELECT * FROM StocktakeItem WHERE session_id=? AND variant_id=?",
            (sid, body.variant_id)).fetchone()
        if row:
            counted = row["counted_qty"] + body.qty
            conn.execute("UPDATE StocktakeItem SET counted_qty=? WHERE id=?",
                         (counted, row["id"]))
            system = row["system_qty"]
        else:
            system = stock_of(conn, body.variant_id)  # 開盤快照:首掃當下
            counted = body.qty
            conn.execute(
                "INSERT INTO StocktakeItem(session_id,variant_id,system_qty,counted_qty) "
                "VALUES(?,?,?,?)", (sid, body.variant_id, system, counted))
        conn.commit()
        return {"system_qty": system, "counted_qty": counted}

@router.put("/stocktake/{sid}/items/{variant_id}")
def set_counted(sid: int, variant_id: int, body: SetIn, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        _require_open(conn, sid)
        cur = conn.execute(
            "UPDATE StocktakeItem SET counted_qty=? WHERE session_id=? AND variant_id=?",
            (body.counted_qty, sid, variant_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "此變體尚未進入本次盤點,請先掃描")
        conn.commit()
        return {"ok": True}

@router.get("/stocktake/{sid}")
def detail(sid: int, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        rows = conn.execute(
            "SELECT si.*, p.name FROM StocktakeItem si "
            "JOIN Variant v ON si.variant_id=v.variant_id "
            "JOIN Product p ON v.product_id=p.product_id "
            "WHERE si.session_id=?", (sid,)).fetchall()
        vids = [r["variant_id"] for r in rows]
        attrs = attrs_by_variant(conn, vids)
        disp = display_attrs(conn, vids)
        items = []
        for r in rows:
            items.append({"variant_id": r["variant_id"], "name": r["name"],
                "attributes": attrs.get(r["variant_id"], {}),
                "attr_display": disp.get(r["variant_id"], ""),
                "system_qty": r["system_qty"], "counted_qty": r["counted_qty"],
                "diff": r["counted_qty"] - r["system_qty"]})
        sess = conn.execute("SELECT * FROM StocktakeSession WHERE session_id=?",
                            (sid,)).fetchone()
        if not sess:
            raise HTTPException(404, "查無此盤點單")
        return {**dict(sess), "items": items}

@router.post("/stocktake/{sid}/close")
def close(sid: int, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        _require_open(conn, sid)
        for r in conn.execute(
                "SELECT variant_id, counted_qty - system_qty AS diff "
                "FROM StocktakeItem WHERE session_id=?", (sid,)):
            if r["diff"] != 0:
                conn.execute(
                    "INSERT INTO StockMovement(variant_id,qty,kind,ref_id,note) "
                    "VALUES(?,?,'adjust',?,'盤點調整')", (r["variant_id"], r["diff"], sid))
        conn.execute("UPDATE StocktakeSession SET status='closed', "
                     "ended_at=datetime('now','localtime') WHERE session_id=?", (sid,))
        conn.commit()
        return {"ok": True}
