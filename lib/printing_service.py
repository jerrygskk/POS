"""Application service for checking, previewing, and printing barcode labels."""
import base64
import threading
from collections.abc import Mapping
from io import BytesIO

from lib import product_data
from lib.application import BaseFacade, BaseRepository
from lib.application_errors import NotFoundError, ValidationError
from lib.label_layout import render_label
from lib.label_printer import LabelPrinter
from lib.product_rules import allow_keys, is_int


_PRINT_LOCK = threading.Lock()
_BUSY = "標籤機正在列印中，請稍候。"
_NOT_OWNED = "此條碼已不屬於此子產品，請移除後重新加入。"


def _validate(action, payload):
    if action == "printing.check":
        allow_keys(payload, {"items"})
        items = payload.get("items")
        if not isinstance(items, list) or not items:
            raise ValidationError("items 必須是非空的清單")
        for item in items:
            if not isinstance(item, Mapping):
                raise ValidationError("items 的每一項必須是物件")
            allow_keys(item, {"variant_id", "barcode"})
            if not is_int(item.get("variant_id")):
                raise ValidationError("variant_id 必須是整數")
            if not isinstance(item.get("barcode"), str) or not item["barcode"].strip():
                raise ValidationError("條碼必須是非空白字串")
        return
    allowed = {"variant_id", "barcode"}
    if action == "printing.barcode":
        allowed.add("copies")
    allow_keys(payload, allowed)
    if not is_int(payload.get("variant_id")):
        raise ValidationError("variant_id 必須是整數")
    if not isinstance(payload.get("barcode"), str) or not payload["barcode"].strip():
        raise ValidationError("條碼必須是非空白字串")
    if action == "printing.barcode":
        copies = payload.get("copies", 1)
        if not is_int(copies) or copies < 1:
            raise ValidationError("張數必須是正整數")
        payload.setdefault("copies", 1)


class PrintingRepository(BaseRepository):
    def label_content(self, variant_id, barcode):
        """Return content and ownership; a missing variant has no content."""
        row = self.one("SELECT p.name,v.price FROM Variant v JOIN Product p ON p.product_id=v.product_id WHERE v.variant_id=?", (variant_id,))
        if row is None:
            return None, False
        owner = self.one("SELECT variant_id FROM Barcode WHERE barcode=?", (barcode,))
        content = {
            "name": row["name"],
            "specification": product_data.display_attrs(self.connection, [variant_id]).get(variant_id, ""),
            "price": "" if row["price"] is None else f"${row['price']}",
            "models": product_data.models_by_variant(self.connection, [variant_id]).get(variant_id, []),
        }
        return content, owner is not None and owner["variant_id"] == variant_id


class PrintingService:
    def __init__(self, repository, printer_factory=LabelPrinter):
        self.repo = repository
        self.printer_factory = printer_factory

    def _render(self, variant_id, barcode):
        content, owned = self.repo.label_content(variant_id, barcode)
        if content is None:
            raise NotFoundError("找不到子產品")
        if not owned:
            raise ValidationError(_NOT_OWNED)
        return render_label(content["name"], content["specification"], content["price"],
                            barcode, models=content["models"])

    def barcode(self, variant_id, barcode, copies):
        if not _PRINT_LOCK.acquire(blocking=False):
            raise ValidationError(_BUSY)
        try:
            image = self._render(variant_id, barcode)
            self.printer_factory().print(image, copies)
        finally:
            _PRINT_LOCK.release()
        return {"ok": True}

    def preview(self, variant_id, barcode):
        image = self._render(variant_id, barcode)
        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return {"image": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")}

    def check(self, items):
        result = []
        for item in items:
            content, owned = self.repo.label_content(item["variant_id"], item["barcode"])
            valid = bool(content is not None and owned)
            result.append({"variant_id": item["variant_id"], "barcode": item["barcode"],
                           "valid": valid, "label": content if valid else None})
        return {"items": result}


class PrintingFacade(BaseFacade):
    ACTIONS = {"printing.barcode", "printing.preview", "printing.check"}
    ERROR_MESSAGE = "不支援的列印操作"
    def _prepare_payload(self, action, payload):
        _validate(action, payload)
        return payload
    def _dispatch(self, action, payload, conn):
        service = PrintingService(PrintingRepository(conn))
        if action == "printing.barcode":
            return service.barcode(payload["variant_id"], payload["barcode"], payload["copies"])
        if action == "printing.preview":
            return service.preview(payload["variant_id"], payload["barcode"])
        return service.check(payload["items"])
