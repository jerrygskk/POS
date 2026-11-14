import unittest
from base import FacadeTestCase
from lib.application_errors import ApplicationError


class TestFixG1Sales(FacadeTestCase):
    def setUp(self):
        super().setUp()
        cid = self.create_category("膜類")
        product = self.invoke("products.create", {"name": "膜", "category_id": cid,
            "variants": [{"attributes": {}, "price": 500, "barcodes": [{"barcode": "B1", "source": "store"}]}]})
        self.vid = product["variant_ids"][0]
        self.invoke("stock.receive", {"employee": 1, "items": [{"variant_id": self.vid, "qty": 10}]})

    def _sale(self, **kw):
        body = {"payment": "現金", "paid": 500, "employee": 1,
                "items": [{"variant_id": self.vid, "qty": 1, "unit_price": 500}]}
        body.update(kw)
        return self.invoke("sales.checkout", body)

    def _error(self, **kw):
        with self.assertRaises(ApplicationError) as raised:
            self._sale(**kw)
        return str(raised.exception)

    def test_cash_paid_less_than_total_rejected(self):
        self.assertIn("實收金額不足", self._error(paid=499))
        self.assertEqual(self.invoke("sales.recent"), [])
        self.assertEqual(self._sale(paid=500)["change"], 0)

    def test_non_cash_ignores_paid(self):
        self.assertEqual(self._sale(payment="LINE Pay", paid=0)["change"], 0)

    def test_member_discount_allowed_without_member(self):
        self.assertEqual(self._sale(member_discount=10, paid=490)["total"], 490)

    def test_date_from_after_date_to_rejected(self):
        for action in ("sales.list", "sales.summary", "sales.export"):
            with self.subTest(action=action):
                with self.assertRaises(ApplicationError) as raised:
                    self.invoke(action, {"date_from": "2026-02-02", "date_to": "2026-02-01"})
                self.assertIn("起日不可晚於迄日", str(raised.exception))
        self.invoke("sales.list", {"date_from": "2026-02-01", "date_to": "2026-02-01"})

    def test_member_search_treats_percent_and_underscore_literally(self):
        self.invoke("members.create", {"name": "王小明", "phones": ["0912000111"]})
        self.invoke("members.create", {"name": "A_B%", "phones": ["0912000222"]})
        names = lambda q: [m["name"] for m in self.invoke("members.list", {"query": q})]
        self.assertEqual(names("%"), ["A_B%"])
        self.assertEqual(names("_"), ["A_B%"])
        self.assertEqual(names("小"), ["王小明"])


if __name__ == "__main__":
    unittest.main()
