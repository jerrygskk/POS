"""規格值的跨表一致性:資料庫擋不了的規則由 product_data.check_variant_attributes 集中把關。"""
import unittest

from base import FacadeTestCase
from lib import product_data
from lib.application_errors import ValidationError
from lib.db import get_conn


class AttributeConsistencyTest(FacadeTestCase):
    def setUp(self):
        super().setUp()
        self.glass = self.create_category("鋼化玻璃")
        self.case = self.create_category("手機殼")
        self.layout = self.create_field("版型", self.glass)
        self.create_options(self.layout, ("滿版", "9分滿"))
        self.extras = self.create_field("配件", self.glass, "multi")
        self.create_options(self.extras, ("貼膜神器", "擦拭布"))
        self.color = self.create_field("顏色", self.case)
        self.create_options(self.color, ("黑",))
        created = self.invoke("products.create", {"name": "膜", "category_id": self.glass, "variants": [
            {"attributes": {"版型": "滿版", "配件": ["貼膜神器", "擦拭布"]}, "barcodes": []}]})
        self.pid, self.vid = created["product_id"], created["variant_ids"][0]

    def _option(self, field_id, value):
        return next(o["option_id"] for o in self.invoke("options.list", {"field_id": field_id})
                    if o["value"] == value)

    def _check_after(self, sql, args):
        conn = get_conn(self.db)
        try:
            conn.execute(sql, args)
            product_data.check_variant_attributes(conn, [self.vid])
        finally:
            conn.rollback()
            conn.close()

    def test_multi_values_are_allowed(self):
        conn = get_conn(self.db)
        try:
            product_data.check_variant_attributes(conn, [self.vid])
        finally:
            conn.close()

    def test_field_of_other_category_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "不屬於此商品的種類"):
            self._check_after("INSERT INTO VariantAttribute(variant_id,field_id,option_id) VALUES(?,?,?)",
                              (self.vid, self.color, self._option(self.color, "黑")))

    def test_select_with_two_values_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "只能填一個值"):
            self._check_after("INSERT INTO VariantAttribute(variant_id,field_id,option_id) VALUES(?,?,?)",
                              (self.vid, self.layout, self._option(self.layout, "9分滿")))

    def test_text_value_on_option_field_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "與欄位型態不符"):
            self._check_after("INSERT INTO VariantAttribute(variant_id,field_id,text_value) VALUES(?,?,?)",
                              (self.vid, self.extras, "手寫"))

    def test_product_with_attributes_cannot_change_category(self):
        self.assert_application_error("conflict", "products.update",
                                      {"id": self.pid, "fields": {"category_id": self.case}})
        product = next(p for p in self.invoke("catalog.list", {"include_inactive": True})
                       if p["product_id"] == self.pid)
        self.assertEqual(product["category_id"], self.glass)

    def test_product_without_attributes_can_change_category(self):
        pid = self.invoke("products.create", {"name": "空白", "category_id": self.glass, "variants": [
            {"attributes": {}, "barcodes": []}]})["product_id"]
        self.invoke("products.update", {"id": pid, "fields": {"category_id": self.case}})


if __name__ == "__main__":
    unittest.main()
