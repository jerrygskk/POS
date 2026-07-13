import unittest
from lib.db import get_conn
from base import ApiTestCase


class TestSortApi(ApiTestCase):
    def _add_cats(self, names):
        return [self.c.post("/api/categories", json={"name": n}).json()["category_id"]
                for n in names]

    def test_resort_categories(self):
        ids = self._add_cats(["甲", "乙", "丙"])
        r = self.c.put("/api/categories/sort", json={"ids": [ids[2], ids[0], ids[1]]})
        self.assertEqual(r.status_code, 200)
        got = [x["category_id"] for x in self.c.get("/api/categories").json()]
        self.assertEqual(got, [ids[2], ids[0], ids[1]])

    def test_resort_unknown_id_422_and_unchanged(self):
        ids = self._add_cats(["甲", "乙"])
        r = self.c.put("/api/categories/sort", json={"ids": [ids[1], 9999]})
        self.assertEqual(r.status_code, 422)
        got = [x["category_id"] for x in self.c.get("/api/categories").json()]
        self.assertEqual(got, ids)

    def test_resort_models_within_brand_group(self):
        pb1 = self.c.post("/api/phone-brands", json={"name": "iPhone"}).json()["phone_brand_id"]
        pb2 = self.c.post("/api/phone-brands", json={"name": "Samsung"}).json()["phone_brand_id"]
        m = lambda pb, n: self.c.post("/api/models",
            json={"phone_brand_id": pb, "name": n}).json()["model_id"]
        a, b, c = m(pb1, "i15"), m(pb1, "i16"), m(pb1, "i17")
        s = m(pb2, "S25")
        # 重現原 bug:sort 全部同值時上下移無效 → 重寫後應照指定順序
        conn = get_conn(self.db)
        conn.execute("UPDATE PhoneModel SET sort=0")
        conn.commit(); conn.close()
        r = self.c.put("/api/models/sort", json={"ids": [c, a, b]})
        self.assertEqual(r.status_code, 200)
        got = [x["model_id"] for x in self.c.get("/api/models").json()]
        self.assertEqual(got[:3], [c, a, b])   # pb1 群組內照新順序
        self.assertIn(s, got)                  # 他品牌不受影響

    def test_resort_brands_and_phone_brands(self):
        b1 = self.c.post("/api/brands", json={"name": "HODA"}).json()["brand_id"]
        b2 = self.c.post("/api/brands", json={"name": "hoda2"}).json()["brand_id"]
        self.assertEqual(
            self.c.put("/api/brands/sort", json={"ids": [b2, b1]}).status_code, 200)
        got = [x["brand_id"] for x in self.c.get("/api/brands").json()]
        self.assertEqual(got, [b2, b1])
        p1 = self.c.post("/api/phone-brands", json={"name": "iPhone"}).json()["phone_brand_id"]
        p2 = self.c.post("/api/phone-brands", json={"name": "紅米"}).json()["phone_brand_id"]
        self.assertEqual(
            self.c.put("/api/phone-brands/sort", json={"ids": [p2, p1]}).status_code, 200)
        got = [x["phone_brand_id"] for x in self.c.get("/api/phone-brands").json()]
        self.assertEqual(got, [p2, p1])


if __name__ == "__main__":
    unittest.main()
