from collections.abc import Mapping

from lib import product_data
from lib.db import stock_map
from lib.application import BaseFacade, BaseRepository
from lib.application_errors import NotFoundError, ValidationError
from lib.product_rules import date_range, date_sql, is_int as _is_int, require_employee


def _validate_payload(action, payload):
    allowed = {"stock.detail": {"variant_id"}, "stock.receipts": {"date_from", "date_to"}}.get(
        action, {"items", "employee"})
    unknown = set(payload) - allowed
    if unknown:
        raise ValidationError(f"不支援的欄位：{sorted(unknown)[0]}")
    if action == "stock.receive":
        require_employee(payload.get("employee"))
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise ValidationError("進貨明細不可空白")
        for item in items:
            if not isinstance(item, Mapping):
                raise ValidationError("進貨明細格式不正確")
            unknown = set(item) - {"variant_id", "qty"}
            if unknown:
                raise ValidationError(f"不支援的欄位：{sorted(unknown)[0]}")
            if not _is_int(item.get("variant_id")):
                raise ValidationError("子產品識別碼格式不正確")
            if not _is_int(item.get("qty")) or item["qty"] <= 0:
                raise ValidationError("進貨數量必須為正整數")
    elif action == "stock.receipts":
        date_range(payload)
    elif not _is_int(payload.get("variant_id")):
        raise ValidationError("子產品識別碼格式不正確")


class StockRepository(BaseRepository):
    def require_variant(self, variant_id):
        row = self.connection.execute("SELECT 1 FROM Variant WHERE variant_id=?", (variant_id,)).fetchone()
        if row is None:
            raise NotFoundError("找不到指定的子產品")

    def add_purchase(self, variant_id, qty, employee, batch=None):
        # 同一次確認進貨共用進貨單號:記在 ref_id,取該批第一筆的 move_id
        move_id = self.connection.execute(
            "INSERT INTO StockMovement(variant_id,qty,kind,employee) VALUES(?,?,'purchase',?)",
            (variant_id, qty, employee),
        ).lastrowid
        batch = batch or move_id
        self.connection.execute("UPDATE StockMovement SET ref_id=? WHERE move_id=?", (batch, move_id))
        return batch

    def purchase_rows(self, filters):
        suffix, args = date_sql(filters, "m.ts")
        return self.connection.execute(
            "SELECT m.move_id,m.variant_id,m.qty,m.ref_id,m.employee,m.ts,p.name FROM StockMovement m "
            "JOIN Variant v ON m.variant_id=v.variant_id JOIN Product p ON v.product_id=p.product_id "
            "WHERE m.kind='purchase'" + suffix + " ORDER BY m.move_id DESC", args).fetchall()

    def movements(self, variant_id):
        return self.connection.execute(
            "SELECT * FROM StockMovement WHERE variant_id=? ORDER BY move_id DESC LIMIT 50",
            (variant_id,),
        ).fetchall()


class StockService:
    def __init__(self, repository):
        self.repo = repository

    def receive(self, items, employee):
        batch = None
        for item in items:
            self.repo.require_variant(item["variant_id"])
            batch = self.repo.add_purchase(item["variant_id"], item["qty"], employee, batch)
        return {"items": [{"variant_id": item["variant_id"],
                           "stock": product_data.stock_of(self.repo.connection, item["variant_id"])}
                          for item in items]}

    def receipts(self, filters):
        rows = self.repo.purchase_rows(filters)
        vids = [row["variant_id"] for row in rows]
        attrs = product_data.attrs_by_variant(self.repo.connection, vids)
        display = product_data.display_attrs(self.repo.connection, vids)
        stocks = stock_map(self.repo.connection, vids)
        out = {}
        for row in rows:
            # 舊進貨沒有單號:同一秒、同一員工視為同一批
            key = row["ref_id"] if row["ref_id"] is not None else (row["ts"], row["employee"])
            receipt = out.setdefault(key, {"receipt_id": row["ref_id"], "ts": row["ts"],
                                           "employee": row["employee"], "items": []})
            receipt["items"].insert(0, {"variant_id": row["variant_id"], "name": row["name"],
                "attributes": attrs.get(row["variant_id"], {}), "attr_display": display.get(row["variant_id"], ""),
                "qty": row["qty"], "stock": stocks.get(row["variant_id"], 0)})
        return list(out.values())

    def detail(self, variant_id):
        self.repo.require_variant(variant_id)
        return {
            "stock": product_data.stock_of(self.repo.connection, variant_id),
            "movements": [dict(row) for row in self.repo.movements(variant_id)],
        }


class StockFacade(BaseFacade):
    ACTIONS = {"stock.receive", "stock.detail", "stock.receipts"}

    ERROR_MESSAGE = "不支援的庫存操作"

    def _prepare_payload(self, action, payload):
        _validate_payload(action, payload)
        return payload

    def _dispatch(self, action, payload, connection):
        service = StockService(StockRepository(connection))
        if action == "stock.receive":
            return service.receive(payload["items"], payload["employee"])
        if action == "stock.receipts":
            return service.receipts(date_range(payload))
        return service.detail(payload["variant_id"])
