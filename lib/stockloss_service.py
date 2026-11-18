"""商品報損:只扣庫存,不進立帳、現金與營收。"""
from lib import product_data, stock_ledger
from lib.application import BaseFacade, BaseRepository
from lib.application_errors import ConflictError, NotFoundError, ValidationError
from lib.product_rules import allow_keys, date_range, date_sql, is_int

REASON_MAX = 60


def _validate(action, payload):
    if action == "stockloss.history":
        allow_keys(payload, {"date_from", "date_to"})
        date_range(payload)
        return
    allow_keys(payload, {"variant_id", "qty", "reason"})
    if not is_int(payload.get("variant_id")):
        raise ValidationError("variant_id 必須是整數")
    if not is_int(payload.get("qty")) or payload["qty"] <= 0:
        raise ValidationError("報損數量必須是正整數")
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("請填寫報損原因")
    if len(reason.strip()) > REASON_MAX:
        raise ValidationError(f"報損原因最多 {REASON_MAX} 字")


class StockLossService:
    def __init__(self, connection):
        self.repo = BaseRepository(connection)

    def create(self, variant_id, qty, reason):
        conn = self.repo.connection
        row = self.repo.one("SELECT p.name FROM Variant v JOIN Product p ON p.product_id=v.product_id "
                            "WHERE v.variant_id=?", (variant_id,))
        if row is None:
            raise NotFoundError("找不到子產品")
        # 盤點單的實盤量可能已排除這件損壞品,此時再報損會重複扣庫存
        if self.repo.one("SELECT 1 FROM StocktakeItem i JOIN StocktakeSession s ON s.session_id=i.session_id "
                         "WHERE i.variant_id=? AND s.status IN ('open','pending') LIMIT 1", (variant_id,)):
            raise ConflictError("此商品正在盤點中，請於盤點完成後再報損")
        stock = product_data.stock_of(conn, variant_id)
        if qty > stock:
            raise ConflictError(f"報損數量超過目前庫存（剩 {stock} 件）")
        spec = product_data.display_attrs(conn, [variant_id]).get(variant_id, "")
        loss_id = self.repo.execute(
            "INSERT INTO StockLoss(variant_id,qty,reason,name,spec) VALUES(?,?,?,?,?)",
            (variant_id, qty, reason.strip(), row["name"], spec)).lastrowid
        stock_ledger.post(conn, "loss", loss_id)
        return {"loss_id": loss_id, "stock": stock - qty}

    def history(self, filters):
        suffix, args = date_sql(filters, "ts")
        return [dict(r) for r in self.repo.all(
            "SELECT loss_id,variant_id,qty,reason,name,spec,ts FROM StockLoss WHERE 1=1" + suffix +
            " ORDER BY loss_id DESC", args)]


class StockLossFacade(BaseFacade):
    ACTIONS = {"stockloss.create", "stockloss.history"}
    ERROR_MESSAGE = "不支援的報損操作"

    def _prepare_payload(self, action, payload):
        _validate(action, payload)
        return payload

    def _dispatch(self, action, payload, connection):
        service = StockLossService(connection)
        if action == "stockloss.history":
            return service.history(date_range(payload))
        # 庫存檢查與扣帳同一交易,避免同時結帳時扣成負數
        connection.execute("BEGIN IMMEDIATE")
        return service.create(payload["variant_id"], payload["qty"], payload["reason"])
