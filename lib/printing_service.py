"""Application service for printing store-barcode labels."""
from lib import product_data
from lib.application import BaseFacade, BaseRepository
from lib.application_errors import NotFoundError, ValidationError
from lib.label_layout import render_label
from lib.label_printer import LabelPrinter
from lib.product_rules import allow_keys, is_int


def _validate(action, payload):
    allow_keys(payload, {"variant_id", "copies"})
    if not is_int(payload.get("variant_id")):
        raise ValidationError("variant_id 必須是整數")
    copies = payload.get("copies", 1)
    if not is_int(copies) or copies < 1:
        raise ValidationError("張數必須是正整數")
    payload.setdefault("copies", 1)


class PrintingRepository(BaseRepository):
    def label_data(self, variant_id):
        row = self.one("SELECT p.name,v.price FROM Variant v JOIN Product p ON p.product_id=v.product_id WHERE v.variant_id=?", (variant_id,))
        if row is None:
            raise NotFoundError("找不到子產品")
        store = self.one("SELECT barcode FROM Barcode WHERE variant_id=? AND source='store' ORDER BY barcode LIMIT 1", (variant_id,))
        if store is None:
            if self.one("SELECT 1 FROM Barcode WHERE variant_id=? LIMIT 1", (variant_id,)):
                raise ValidationError("原廠條碼商品不需列印標籤。")
            raise ValidationError("此子產品尚未建立店內條碼。")
        return {"name": row["name"], "price": row["price"], "barcode": store["barcode"]}


class PrintingService:
    def __init__(self, repository, printer_factory=LabelPrinter):
        self.repo = repository
        self.printer_factory = printer_factory

    def barcode(self, variant_id, copies):
        data = self.repo.label_data(variant_id)
        data["specification"] = product_data.display_attrs(self.repo.connection, [variant_id]).get(variant_id, "")
        price = "" if data["price"] is None else f"${data['price']}"
        image = render_label(data["name"], data["specification"], price, data["barcode"])
        self.printer_factory().print(image, copies)
        return {"ok": True}


class PrintingFacade(BaseFacade):
    ACTIONS = {"printing.barcode"}
    ERROR_MESSAGE = "不支援的列印操作"
    def _prepare_payload(self, action, payload):
        _validate(action, payload)
        return payload
    def _dispatch(self, action, payload, conn):
        return PrintingService(PrintingRepository(conn)).barcode(payload["variant_id"], payload["copies"])
