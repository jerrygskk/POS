import unittest

from base import FacadeTestCase
from lib.application_errors import ValidationError
from lib.db import get_conn


class TestSortActions(FacadeTestCase):
    def _add_categories(self, names):
        return [self.invoke("categories.create", {"name": name})["category_id"]
                for name in names]

    def test_reorders_categories(self):
        ids = self._add_categories(["one", "two", "three"])
        self.assertEqual(
            self.invoke("categories.sort", {"ids": [ids[2], ids[0], ids[1]]}),
            {"ok": True},
        )
        got = [item["category_id"] for item in self.invoke("categories.list", {})]
        self.assertEqual(got, [ids[2], ids[0], ids[1]])

    def test_reordering_unknown_category_rejects_and_preserves_order(self):
        ids = self._add_categories(["one", "two"])
        with self.assertRaises(ValidationError) as raised:
            self.invoke("categories.sort", {"ids": [ids[1], 9999]})
        self.assertEqual(raised.exception.code, "validation_error")
        got = [item["category_id"] for item in self.invoke("categories.list", {})]
        self.assertEqual(got, ids)

    def test_reorders_models_within_brand_group(self):
        brand_one = self.create_phone_brand("iPhone")
        brand_two = self.create_phone_brand("Samsung")
        make_model = lambda brand_id, name: self.create_model(brand_id, name)
        first, second, third = (make_model(brand_one, name) for name in ("i15", "i16", "i17"))
        other_brand_model = make_model(brand_two, "S25")
        with get_conn(self.db) as conn:
            conn.execute("UPDATE PhoneModel SET sort=0")

        self.assertEqual(
            self.invoke("models.sort", {"ids": [third, first, second]}),
            {"ok": True},
        )
        got = [item["model_id"] for item in self.invoke("models.list", {})]
        self.assertEqual(got[:3], [third, first, second])
        self.assertIn(other_brand_model, got)

    def test_reorders_brands_and_phone_brands(self):
        first_brand = self.invoke("brands.create", {"name": "HODA"})["brand_id"]
        second_brand = self.invoke("brands.create", {"name": "hoda2"})["brand_id"]
        self.assertEqual(
            self.invoke("brands.sort", {"ids": [second_brand, first_brand]}),
            {"ok": True},
        )
        brands = [item["brand_id"] for item in self.invoke("brands.list", {})]
        self.assertEqual(brands, [second_brand, first_brand])

        first_phone_brand = self.create_phone_brand("iPhone")
        second_phone_brand = self.create_phone_brand("Redmi")
        self.assertEqual(
            self.invoke("phone_brands.sort", {"ids": [second_phone_brand, first_phone_brand]}),
            {"ok": True},
        )
        phone_brands = [item["phone_brand_id"] for item in self.invoke("phone_brands.list", {})]
        self.assertEqual(phone_brands, [second_phone_brand, first_phone_brand])


if __name__ == "__main__":
    unittest.main()
