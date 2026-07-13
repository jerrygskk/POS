import json, io, csv
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from lib.db import db_conn
from api.products import attrs_by_variant, display_attrs

router = APIRouter(prefix="/api")

class ItemIn(BaseModel):
    variant_id: int
    qty: int = Field(gt=0)
    unit_price: int = Field(ge=0)
    discount: int = Field(default=0, ge=0)

class SaleIn(BaseModel):
    payment: str
    order_discount: int = Field(default=0, ge=0)
    paid: int = Field(ge=0)
    items: list[ItemIn] = Field(min_length=1)

@router.get("/payments")
def payments(request: Request):
    with db_conn(request.app.state.db_path) as conn:
        row = conn.execute("SELECT value FROM Setting WHERE key='payments'").fetchone()
        return json.loads(row["value"])

@router.post("/sales")
def checkout(body: SaleIn, request: Request):
    total = sum(i.qty * i.unit_price - i.discount for i in body.items) - body.order_discount
    if total < 0:
        raise HTTPException(422, "折扣後金額不可為負")
    with db_conn(request.app.state.db_path) as conn:
        # 停用 guard:任一變體或其款停用 → 擋掉,不寫入任何資料
        for i in body.items:
            row = conn.execute(
                "SELECT (v.active AND p.active) AS ok FROM Variant v "
                "JOIN Product p ON v.product_id=p.product_id WHERE v.variant_id=?",
                (i.variant_id,)).fetchone()
            if row is None or not row["ok"]:
                raise HTTPException(422, "商品已停用,無法銷售")
        cur = conn.execute(
            "INSERT INTO Sale(payment,order_discount,total,paid,change) VALUES(?,?,?,?,?)",
            (body.payment, body.order_discount, total, body.paid, body.paid - total))
        sale_id = cur.lastrowid
        for i in body.items:
            conn.execute(
                "INSERT INTO SaleItem(sale_id,variant_id,qty,unit_price,discount) "
                "VALUES(?,?,?,?,?)",
                (sale_id, i.variant_id, i.qty, i.unit_price, i.discount))
            conn.execute(
                "INSERT INTO StockMovement(variant_id,qty,kind,ref_id) "
                "VALUES(?,?,'sale',?)", (i.variant_id, -i.qty, sale_id))
        conn.commit()   # 全部一次 commit=同一 transaction
        return {"sale_id": sale_id, "total": total, "change": body.paid - total}

def _query_sales(conn, date_from, date_to, payment):
    sql = ("SELECT s.*, i.variant_id, i.qty, i.unit_price, i.discount, p.name "
           "FROM Sale s JOIN SaleItem i ON s.sale_id=i.sale_id "
           "JOIN Variant v ON i.variant_id=v.variant_id "
           "JOIN Product p ON v.product_id=p.product_id WHERE 1=1")
    args = []
    if date_from: sql += " AND date(s.ts)>=?"; args.append(date_from)
    if date_to:   sql += " AND date(s.ts)<=?"; args.append(date_to)
    if payment:   sql += " AND s.payment=?";   args.append(payment)
    return conn.execute(sql + " ORDER BY s.sale_id DESC", args).fetchall()

@router.get("/sales")
def list_sales(request: Request, date_from: str = "", date_to: str = "", payment: str = ""):
    with db_conn(request.app.state.db_path) as conn:
        rows = _query_sales(conn, date_from, date_to, payment)
        vids = [r["variant_id"] for r in rows]
        attrs = attrs_by_variant(conn, vids)
        disp = display_attrs(conn, vids)
        out = {}
        for r in rows:
            s = out.setdefault(r["sale_id"], {
                "sale_id": r["sale_id"], "ts": r["ts"], "payment": r["payment"],
                "order_discount": r["order_discount"], "total": r["total"], "items": []})
            s["items"].append({"variant_id": r["variant_id"], "name": r["name"],
                "attributes": attrs.get(r["variant_id"], {}),
                "attr_display": disp.get(r["variant_id"], ""), "qty": r["qty"],
                "unit_price": r["unit_price"], "discount": r["discount"]})
        return list(out.values())

@router.get("/sales/summary")
def summary(request: Request, date_from: str = "", date_to: str = "",
            payment: str = "", date: str = ""):
    with db_conn(request.app.state.db_path) as conn:
        # date 為舊參數(單日),保留相容;優先用 date_from/date_to 區間
        if date and not date_from and not date_to:
            date_from = date_to = date
        sql = "SELECT payment, COUNT(*) c, SUM(total) t FROM Sale WHERE 1=1"
        args = []
        if date_from: sql += " AND date(ts)>=?"; args.append(date_from)
        if date_to:   sql += " AND date(ts)<=?"; args.append(date_to)
        if payment:   sql += " AND payment=?";   args.append(payment)
        rows = conn.execute(sql + " GROUP BY payment", args).fetchall()
        return {"count": sum(r["c"] for r in rows),
                "total": sum(r["t"] or 0 for r in rows),
                "by_payment": {r["payment"]: r["t"] for r in rows}}

@router.get("/sales/export")
def export_csv(request: Request, date_from: str = "", date_to: str = ""):
    with db_conn(request.app.state.db_path) as conn:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["交易編號","時間","付款方式","商品","屬性","數量","成交單價","單品折扣","整單折抵","應收"])
        rows = _query_sales(conn, date_from, date_to, "")
        disp = display_attrs(conn, [r["variant_id"] for r in rows])
        for r in rows:
            attr_str = disp.get(r["variant_id"], "")
            w.writerow([r["sale_id"], r["ts"], r["payment"], r["name"],
                        attr_str, r["qty"], r["unit_price"], r["discount"],
                        r["order_discount"], r["total"]])
        data = "﻿" + buf.getvalue()   # utf-8-sig 給 Excel
        return StreamingResponse(iter([data]), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=sales.csv"})
