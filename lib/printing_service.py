from collections.abc import Mapping

from lib.application_errors import ValidationError


class PrintingFacade:
    ACTIONS = {"printing.barcode"}

    def invoke(self, action, payload=None):
        payload = {} if payload is None else payload
        if action not in self.ACTIONS or not isinstance(payload, Mapping):
            raise ValidationError("不支援的列印操作")
        raise ValidationError("列印功能尚未支援。")
