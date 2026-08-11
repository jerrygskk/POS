from collections.abc import Mapping

from lib.application_errors import ValidationError
from lib.product_rules import is_int


class PrintingFacade:
    ACTIONS = {"printing.barcode"}

    def invoke(self, action, payload=None):
        payload = {} if payload is None else payload
        if action not in self.ACTIONS or not isinstance(payload, Mapping):
            raise ValidationError("不支援的列印操作")
        unknown = set(payload) - {"variant_id"}
        if unknown:
            raise ValidationError(f"不支援的欄位：{sorted(unknown)[0]}")
        if not is_int(payload.get("variant_id")):
            raise ValidationError("子產品識別碼格式不正確")
        raise ValidationError("列印功能尚未支援。")
