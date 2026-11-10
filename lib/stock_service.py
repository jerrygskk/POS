from collections.abc import Mapping

from lib import correction_service, product_data, stock_ledger
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

    def create_receipt(self, employee):
        return self.connection.execute("INSERT INTO Receipt(employee) VALUES(?)", (employee,)).lastrowid

    def add_receipt_item(self, receipt_id, variant_id, qty):
        return self.connection.execute(
            "INSERT INTO ReceiptItem(receipt_id,variant_id,qty) VALUES(?,?,?)",
            (receipt_id, variant_id, qty)).lastrowid

    def receipt_rows(self, filters):
        suffix, args = date_sql(filters, "r.ts")
        return self.connection.execute(
            "SELECT r.receipt_id,r.ts,r.employee,i.variant_id,i.qty,p.name FROM Receipt r "
            "JOIN ReceiptItem i ON i.receipt_id=r.receipt_id "
            "JOIN Variant v ON i.variant_id=v.variant_id JOIN Product p ON v.product_id=p.product_id "
            "WHERE 1=1" + suffix + " ORDER BY r.receipt_id DESC, i.receipt_item_id", args).fetchall()

    def movements(self, variant_id):
        # 來源以具名欄位回傳;操作員工:返新記在異動本身,其餘取各自單據
        return self.connection.execute(
            "SELECT m.move_id,m.variant_id,m.qty,m.kind,m.note,m.ts,"
            "m.receipt_item_id,m.sale_item_id,m.stocktake_item_id,m.return_item_id,"
            "ri.receipt_id,si.sale_id,sti.session_id AS stocktake_id,rti.return_id,"
            "COALESCE(m.employee,r.employee,CASE WHEN m.kind='void' THEN NULL ELSE s.employee END,"
            "ss.employee,rs.employee) AS employee "
            "FROM StockMovement m "
            "LEFT JOIN ReceiptItem ri ON ri.receipt_item_id=m.receipt_item_id "
            "LEFT JOIN Receipt r ON r.receipt_id=ri.receipt_id "
            "LEFT JOIN SaleItem si ON si.item_id=m.sale_item_id LEFT JOIN Sale s ON s.sale_id=si.sale_id "
            "LEFT JOIN StocktakeItem sti ON sti.id=m.stocktake_item_id "
            "LEFT JOIN StocktakeSession ss ON ss.session_id=sti.session_id "
            "LEFT JOIN ReturnItem rti ON rti.id=m.return_item_id "
            "LEFT JOIN ReturnSession rs ON rs.return_id=rti.return_id "
            "WHERE m.variant_id=? ORDER BY m.move_id DESC LIMIT 50",
            (variant_id,),
        ).fetchall()


class StockService:
    def __init__(self, repository):
        self.repo = repository

    def receive(self, items, employee):
        # 一次確認進貨＝一張進貨單;同款式多列各自成一筆明細,數量不合併也不略過
        conn = self.repo.connection
        receipt_id = self.repo.create_receipt(employee)
        for item in items:
            self.repo.require_variant(item["variant_id"])
            item_id = self.repo.add_receipt_item(receipt_id, item["variant_id"], item["qty"])
            stock_ledger.post(conn, "purchase", item_id)
        return {"receipt_id": receipt_id,
                "items": [{"variant_id": item["variant_id"],
                           "stock": product_data.stock_of(conn, item["variant_id"])}
                          for item in items]}

    def receipts(self, filters):
        rows = self.repo.receipt_rows(filters)
        vids = [row["variant_id"] for row in rows]
        attrs = product_data.attrs_by_variant(self.repo.connection, vids)
        display = product_data.display_attrs(self.repo.connection, vids)
        stocks = stock_map(self.repo.connection, vids)
        out = {}
        for row in rows:
            receipt = out.setdefault(row["receipt_id"], {"receipt_id": row["receipt_id"], "ts": row["ts"],
                                                         "employee": row["employee"], "items": []})
            receipt["items"].append({"variant_id": row["variant_id"], "name": row["name"],
                "attributes": attrs.get(row["variant_id"], {}), "attr_display": display.get(row["variant_id"], ""),
                "qty": row["qty"], "stock": stocks.get(row["variant_id"], 0)})
        return correction_service.attach(self.repo.connection, "receipt", list(out.values()), "receipt_id")

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
