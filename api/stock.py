from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from lib.db import get_conn
from api.products import stock_of

router = APIRouter(prefix="/api")

class ReceiveIn(BaseModel):
    variant_id: int
    qty: int = Field(gt=0)   # 進貨必為正
    note: str | None = None

@router.post("/stock/receive")
def receive(body: ReceiveIn, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        conn.execute(
            "INSERT INTO StockMovement(variant_id,qty,kind,note) VALUES(?,?,'purchase',?)",
            (body.variant_id, body.qty, body.note))
        conn.commit()
        return {"stock": stock_of(conn, body.variant_id)}
    finally:
        conn.close()

@router.get("/stock/{variant_id}")
def detail(variant_id: int, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        moves = [dict(r) for r in conn.execute(
            "SELECT * FROM StockMovement WHERE variant_id=? ORDER BY move_id DESC LIMIT 50",
            (variant_id,))]
        return {"stock": stock_of(conn, variant_id), "movements": moves}
    finally:
        conn.close()
