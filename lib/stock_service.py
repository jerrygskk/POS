from collections.abc import Mapping

from lib import product_data
from lib.application import TransactionRunner
from lib.application_errors import NotFoundError, ValidationError
from lib.db import db_conn


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _validate_payload(action, payload):
    allowed = {"variant_id"} if action == "stock.detail" else {"variant_id", "qty", "note"}
    unknown = set(payload) - allowed
    if unknown:
        raise ValidationError(f"不支援的欄位：{sorted(unknown)[0]}")
    if not _is_int(payload.get("variant_id")):
        raise ValidationError("子產品識別碼格式不正確")
    if action == "stock.receive":
        if not _is_int(payload.get("qty")) or payload["qty"] <= 0:
            raise ValidationError("進貨數量必須為正整數")
        if payload.get("note") is not None and not isinstance(payload["note"], str):
            raise ValidationError("備註格式不正確")


class StockRepository:
    def __init__(self, connection):
        self.connection = connection

    def require_variant(self, variant_id):
        row = self.connection.execute("SELECT 1 FROM Variant WHERE variant_id=?", (variant_id,)).fetchone()
        if row is None:
            raise NotFoundError("找不到指定的子產品")

    def add_purchase(self, variant_id, qty, note):
        self.connection.execute(
            "INSERT INTO StockMovement(variant_id,qty,kind,note) VALUES(?,?,'purchase',?)",
            (variant_id, qty, note),
        )

    def movements(self, variant_id):
        return self.connection.execute(
            "SELECT * FROM StockMovement WHERE variant_id=? ORDER BY move_id DESC LIMIT 50",
            (variant_id,),
        ).fetchall()


class StockService:
    def __init__(self, repository):
        self.repo = repository

    def receive(self, variant_id, qty, note=None):
        self.repo.require_variant(variant_id)
        self.repo.add_purchase(variant_id, qty, note)
        return {"stock": product_data.stock_of(self.repo.connection, variant_id)}

    def detail(self, variant_id):
        self.repo.require_variant(variant_id)
        return {
            "stock": product_data.stock_of(self.repo.connection, variant_id),
            "movements": [dict(row) for row in self.repo.movements(variant_id)],
        }


class StockFacade:
    ACTIONS = {"stock.receive", "stock.detail"}

    def __init__(self, db_path):
        self.runner = TransactionRunner(db_path, connection_context=db_conn)

    def invoke(self, action, payload=None):
        payload = {} if payload is None else payload
        if action not in self.ACTIONS or not isinstance(payload, Mapping):
            raise ValidationError("不支援的庫存操作")
        _validate_payload(action, payload)

        def work(connection):
            service = StockService(StockRepository(connection))
            if action == "stock.receive":
                return service.receive(payload["variant_id"], payload["qty"], payload.get("note"))
            return service.detail(payload["variant_id"])

        return self.runner.run(work)
