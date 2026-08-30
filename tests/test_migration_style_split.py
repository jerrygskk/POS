# -*- coding: utf-8 -*-
"""v14:共用「款式」欄依種類拆開的遷移驗證。

款式原本與顏色同為全域欄,但各種類的款式詞彙互不相干(手機殼的磁吸支架
vs AppleWatch玻璃的 3D全玻璃),共用會讓建檔候選混進別種類的值。
"""
import os
import tempfile
import unittest

import base  # noqa: F401  確保 sys.path 有專案根
from lib import db_schema
from lib.db import get_conn, init_db


class TestSplitSharedStyleField(unittest.TestCase):
    def _shared_style_db(self, conn):
        """在已升級的 DB 上重建「共用款式欄」的舊狀態,回傳關鍵 id。"""
        cats = {}
        for name in ("手機殼", "AppleWatch玻璃"):
            cats[name] = conn.execute(
                "INSERT INTO Category(name) VALUES(?)", (name,)).lastrowid
        fid = conn.execute(
            "INSERT INTO AttributeField(name,field_type) VALUES('款式','select')"
        ).lastrowid
        opts = {}
        for sort, value in enumerate(
                ["磁吸支架", "3D全玻璃", "共用款", "款式A"], start=1):
            opts[value] = conn.execute(
                "INSERT INTO AttributeOption(field_id,value,sort) VALUES(?,?,?)",
                (fid, value, sort)).lastrowid
        model_id = conn.execute(
            "INSERT INTO PhoneBrand(name) VALUES('iPhone')").lastrowid
        model_id = conn.execute(
            "INSERT INTO PhoneModel(phone_brand_id,name) VALUES(?, 'iPhone 17')",
            (model_id,)).lastrowid
        conn.execute("INSERT INTO OptionModel(option_id,model_id) VALUES(?,?)",
                     (opts["磁吸支架"], model_id))
        for cat_name, cid in cats.items():
            conn.execute(
                "INSERT INTO CategoryField(category_id,field_id,sort,required,active,"
                "default_option_id) VALUES(?,?,1,0,1,?)",
                (cid, fid, opts["共用款"]))
            pid = conn.execute(
                "INSERT INTO Product(name,category_id) VALUES(?,?)",
                (cat_name + "商品", cid)).lastrowid
            own = "磁吸支架" if cat_name == "手機殼" else "3D全玻璃"
            for value in (own, "共用款"):
                vid = conn.execute(
                    "INSERT INTO Variant(product_id) VALUES(?)", (pid,)).lastrowid
                conn.execute(
                    "INSERT INTO VariantAttribute(variant_id,field_id,option_id) "
                    "VALUES(?,?,?)", (vid, fid, opts[value]))
        return cats, fid, opts, model_id

    def _run(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            init_db(db_path)
            conn = get_conn(db_path)
            try:
                cats, old_fid, opts, model_id = self._shared_style_db(conn)
                db_schema._mig_split_style_field(conn)
                conn.commit()
                result = {}
                result["fields"] = [dict(r) for r in conn.execute(
                    "SELECT f.field_id, ca.name cat FROM AttributeField f "
                    "JOIN CategoryField cf ON cf.field_id=f.field_id "
                    "JOIN Category ca ON ca.category_id=cf.category_id "
                    "WHERE f.name='款式' ORDER BY ca.name")]
                result["values"] = [tuple(r) for r in conn.execute(
                    "SELECT ca.name, o.value FROM VariantAttribute va "
                    "JOIN AttributeOption o ON o.option_id=va.option_id "
                    "JOIN Variant v ON v.variant_id=va.variant_id "
                    "JOIN Product p ON p.product_id=v.product_id "
                    "JOIN Category ca ON ca.category_id=p.category_id "
                    "ORDER BY ca.name, o.value")]
                result["old_field"] = conn.execute(
                    "SELECT COUNT(*) FROM AttributeField WHERE field_id=?",
                    (old_fid,)).fetchone()[0]
                result["unused"] = conn.execute(
                    "SELECT COUNT(*) FROM AttributeOption WHERE value='款式A'"
                ).fetchone()[0]
                result["mismatch"] = conn.execute(
                    "SELECT COUNT(*) FROM VariantAttribute va "
                    "JOIN AttributeOption o ON o.option_id=va.option_id "
                    "WHERE o.field_id<>va.field_id").fetchone()[0]
                result["limited"] = [tuple(r) for r in conn.execute(
                    "SELECT o.value, om.model_id FROM OptionModel om "
                    "JOIN AttributeOption o ON o.option_id=om.option_id")]
                result["defaults"] = [tuple(r) for r in conn.execute(
                    "SELECT ca.name, o.value FROM CategoryField cf "
                    "JOIN Category ca ON ca.category_id=cf.category_id "
                    "JOIN AttributeOption o ON o.option_id=cf.default_option_id "
                    "ORDER BY ca.name")]
                return result
            finally:
                conn.close()

    def test_each_category_gets_own_field_and_only_its_own_values(self):
        r = self._run()
        # 兩個種類各拿到一份自己的款式欄(field_id 不同)
        self.assertEqual([f["cat"] for f in r["fields"]],
                         ["AppleWatch玻璃", "手機殼"])
        self.assertNotEqual(r["fields"][0]["field_id"], r["fields"][1]["field_id"])
        # 值只留該種類真的用過的;兩邊都用過的「共用款」各留一份
        self.assertEqual(r["values"], [
            ("AppleWatch玻璃", "3D全玻璃"), ("AppleWatch玻璃", "共用款"),
            ("手機殼", "共用款"), ("手機殼", "磁吸支架"),
        ])
        self.assertEqual(r["mismatch"], 0)
        # 原全域欄與沒人用過的選項一併清掉
        self.assertEqual(r["old_field"], 0)
        self.assertEqual(r["unused"], 0)
        # 限定型號與模板預設值跟著搬到新選項
        self.assertEqual(r["limited"], [("磁吸支架", 1)])
        self.assertEqual(r["defaults"],
                         [("AppleWatch玻璃", "共用款"), ("手機殼", "共用款")])

    def test_field_bound_to_one_category_is_left_alone(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            init_db(db_path)
            conn = get_conn(db_path)
            try:
                cid = conn.execute(
                    "INSERT INTO Category(name) VALUES('手機殼')").lastrowid
                fid = conn.execute(
                    "INSERT INTO AttributeField(name,field_type) "
                    "VALUES('款式','select')").lastrowid
                conn.execute(
                    "INSERT INTO CategoryField(category_id,field_id) VALUES(?,?)",
                    (cid, fid))
                db_schema._mig_split_style_field(conn)
                kept = conn.execute(
                    "SELECT COUNT(*) FROM AttributeField WHERE field_id=?",
                    (fid,)).fetchone()[0]
            finally:
                conn.close()
        self.assertEqual(kept, 1)


class TestSplitAllSharedFields(unittest.TestCase):
    """v15:規格欄一律不跨種類共用,顏色等共用欄一併依種類拆開。"""

    def test_shared_color_and_text_field_split_per_category(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test.db")
            init_db(db_path)
            conn = get_conn(db_path)
            try:
                cats = {}
                for name in ("手機殼", "充電線"):
                    cats[name] = conn.execute(
                        "INSERT INTO Category(name) VALUES(?)", (name,)).lastrowid
                color_fid = conn.execute(
                    "INSERT INTO AttributeField(name,field_type) "
                    "VALUES('顏色','select')").lastrowid
                desc_fid = conn.execute(
                    "INSERT INTO AttributeField(name,field_type) "
                    "VALUES('商品描述','text')").lastrowid
                opts = {}
                for sort, value in enumerate(["天峰藍", "黑色", "沒人用"], start=1):
                    opts[value] = conn.execute(
                        "INSERT INTO AttributeOption(field_id,value,sort) VALUES(?,?,?)",
                        (color_fid, value, sort)).lastrowid
                for cat_name, cid in cats.items():
                    for fid in (color_fid, desc_fid):
                        conn.execute(
                            "INSERT INTO CategoryField(category_id,field_id) VALUES(?,?)",
                            (cid, fid))
                    pid = conn.execute(
                        "INSERT INTO Product(name,category_id) VALUES(?,?)",
                        (cat_name, cid)).lastrowid
                    vid = conn.execute(
                        "INSERT INTO Variant(product_id) VALUES(?)", (pid,)).lastrowid
                    value = "天峰藍" if cat_name == "手機殼" else "黑色"
                    conn.execute(
                        "INSERT INTO VariantAttribute(variant_id,field_id,option_id) "
                        "VALUES(?,?,?)", (vid, color_fid, opts[value]))
                    conn.execute(
                        "INSERT INTO VariantAttribute(variant_id,field_id,text_value) "
                        "VALUES(?,?,?)", (vid, desc_fid, cat_name + "描述"))
                db_schema._mig_split_shared_fields(conn)
                conn.commit()
                pairs = [tuple(r) for r in conn.execute(
                    "SELECT ca.name, f.name, o.value FROM AttributeOption o "
                    "JOIN AttributeField f ON f.field_id=o.field_id "
                    "JOIN CategoryField cf ON cf.field_id=f.field_id "
                    "JOIN Category ca ON ca.category_id=cf.category_id "
                    "ORDER BY ca.name, o.value")]
                texts = [tuple(r) for r in conn.execute(
                    "SELECT ca.name, va.text_value FROM VariantAttribute va "
                    "JOIN AttributeField f ON f.field_id=va.field_id "
                    "JOIN CategoryField cf ON cf.field_id=f.field_id "
                    "JOIN Category ca ON ca.category_id=cf.category_id "
                    "WHERE va.text_value IS NOT NULL ORDER BY ca.name")]
                shared = conn.execute(
                    "SELECT COUNT(*) FROM AttributeField f WHERE "
                    "(SELECT COUNT(*) FROM CategoryField cf "
                    " WHERE cf.field_id=f.field_id) > 1").fetchone()[0]
                mismatch = conn.execute(
                    "SELECT COUNT(*) FROM VariantAttribute va "
                    "JOIN AttributeOption o ON o.option_id=va.option_id "
                    "WHERE o.field_id<>va.field_id").fetchone()[0]
            finally:
                conn.close()

        # 顏色各種類只留自己用過的值;沒人用過的丟掉
        self.assertEqual(pairs, [("充電線", "顏色", "黑色"),
                                 ("手機殼", "顏色", "天峰藍")])
        # text 欄沒有選項,值直接改指該種類那一份
        self.assertEqual(texts, [("充電線", "充電線描述"), ("手機殼", "手機殼描述")])
        self.assertEqual(shared, 0)
        self.assertEqual(mismatch, 0)


if __name__ == "__main__":
    unittest.main()
