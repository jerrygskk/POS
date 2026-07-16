import unittest, sqlite3
from base import ConnTestCase


class TestCatalogTables(ConnTestCase):
    # 新表都建齊
    def test_new_tables_exist(self):
        names = {r["name"] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ["Category", "Brand", "BrandCategory", "PhoneBrand",
                  "PhoneModel", "VariantModel", "CategoryField",
                  "VariantAttribute", "OptionModel"]:
            self.assertIn(t, names)

    # BrandCategory 複合主鍵:同組合重複 → 違反
    def test_brandcategory_composite_pk(self):
        self.conn.execute("INSERT INTO Brand(name) VALUES('HODA')")
        self.conn.execute("INSERT INTO Category(name) VALUES('鋼化玻璃')")
        self.conn.execute("INSERT INTO BrandCategory(brand_id,category_id) VALUES(1,1)")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO BrandCategory(brand_id,category_id) VALUES(1,1)")

    # BrandCategory FK:指向不存在的廠牌 → 違反
    def test_brandcategory_fk_enforced(self):
        self.conn.execute("INSERT INTO Category(name) VALUES('手機殼')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO BrandCategory(brand_id,category_id) VALUES(999,1)")

    # PhoneBrand UNIQUE(name):同名重複違反
    def test_phonebrand_unique(self):
        self.conn.execute("INSERT INTO PhoneBrand(name) VALUES('iPhone')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO PhoneBrand(name) VALUES('iPhone')")

    # PhoneModel UNIQUE(phone_brand_id,name):同品牌同名重複違反;跨品牌同名可
    def test_phonemodel_unique(self):
        ip = self.conn.execute(
            "INSERT INTO PhoneBrand(name) VALUES('iPhone')").lastrowid
        sam = self.conn.execute(
            "INSERT INTO PhoneBrand(name) VALUES('SAMSUNG')").lastrowid
        self.conn.execute("INSERT INTO PhoneModel(phone_brand_id,name) VALUES(?,'15')", (ip,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO PhoneModel(phone_brand_id,name) VALUES(?,'15')", (ip,))
        # 不同品牌同名可共存
        self.conn.execute("INSERT INTO PhoneModel(phone_brand_id,name) VALUES(?,'15')", (sam,))

    # PhoneModel FK:指向不存在的手機品牌 → 違反
    def test_phonemodel_fk_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO PhoneModel(phone_brand_id,name) VALUES(999,'15')")

    # VariantModel 複合主鍵
    def test_variantmodel_pk(self):
        self.conn.execute("INSERT INTO Product(name) VALUES('殼')")
        self.conn.execute("INSERT INTO Variant(product_id) VALUES(1)")
        self.conn.execute("INSERT INTO PhoneBrand(name) VALUES('iPhone')")
        self.conn.execute("INSERT INTO PhoneModel(phone_brand_id,name) VALUES(1,'15')")
        self.conn.execute("INSERT INTO VariantModel(variant_id,model_id) VALUES(1,1)")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO VariantModel(variant_id,model_id) VALUES(1,1)")

    # CategoryField 複合主鍵
    def test_categoryfield_pk(self):
        self.conn.execute("INSERT INTO Category(name) VALUES('手機殼')")
        self.conn.execute(
            "INSERT INTO AttributeField(name,field_type) VALUES('顏色','select')")
        fid = self.conn.execute(
            "SELECT field_id FROM AttributeField WHERE name='顏色'").fetchone()["field_id"]
        self.conn.execute(
            "INSERT INTO CategoryField(category_id,field_id) VALUES(1,?)", (fid,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO CategoryField(category_id,field_id) VALUES(1,?)", (fid,))

    # AttributeField 全域化:DDL 層無 UNIQUE(name),正規化同名去重交應用層
    def test_attributefield_unique(self):
        self.conn.execute("INSERT INTO AttributeField(name) VALUES('版型')")
        self.conn.execute("INSERT INTO AttributeField(name) VALUES('版型')")
        n = self.conn.execute(
            "SELECT COUNT(*) c FROM AttributeField WHERE name='版型'").fetchone()["c"]
        self.assertEqual(n, 2)

    # AttributeOption UNIQUE(field_id,value)
    def test_attributeoption_unique(self):
        self.conn.execute(
            "INSERT INTO AttributeField(name) VALUES('顏色')")
        fid = self.conn.execute(
            "SELECT field_id FROM AttributeField WHERE name='顏色'").fetchone()["field_id"]
        self.conn.execute(
            "INSERT INTO AttributeOption(field_id,value) VALUES(?,'黑')", (fid,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO AttributeOption(field_id,value) VALUES(?,'黑')", (fid,))

    # field_type CHECK 限制
    def test_field_type_check(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO AttributeField(name,field_type) VALUES('壞欄','number')")

    def _make_variant_and_option(self):
        self.conn.execute("INSERT INTO Product(name) VALUES('膜')")
        self.conn.execute("INSERT INTO Variant(product_id) VALUES(1)")
        self.conn.execute(
            "INSERT INTO AttributeField(name,field_type) VALUES('規格','select')")
        fid = self.conn.execute(
            "SELECT field_id FROM AttributeField WHERE name='規格'").fetchone()["field_id"]
        oid = self.conn.execute(
            "INSERT INTO AttributeOption(field_id,value) VALUES(?,'亮面')",
            (fid,)).lastrowid
        return fid, oid

    # VariantAttribute CHECK:option_id 與 text_value 恰一非 NULL
    def test_variantattribute_check_xor(self):
        fid, oid = self._make_variant_and_option()
        # 兩者皆 NULL → 違反
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO VariantAttribute(variant_id,field_id) VALUES(1,?)", (fid,))
        # 兩者皆非 NULL → 違反
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO VariantAttribute(variant_id,field_id,option_id,text_value) "
                "VALUES(1,?,?,'x')", (fid, oid))
        # 只有 option_id → 可
        self.conn.execute(
            "INSERT INTO VariantAttribute(variant_id,field_id,option_id) VALUES(1,?,?)",
            (fid, oid))

    # VariantAttribute PK(variant_id,field_id):同變體同欄重複違反
    def test_variantattribute_pk(self):
        fid, oid = self._make_variant_and_option()
        self.conn.execute(
            "INSERT INTO VariantAttribute(variant_id,field_id,option_id) VALUES(1,?,?)",
            (fid, oid))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO VariantAttribute(variant_id,field_id,option_id) VALUES(1,?,?)",
                (fid, oid))

    # OptionModel 複合主鍵 + FK
    def test_optionmodel_pk_and_fk(self):
        fid, oid = self._make_variant_and_option()
        self.conn.execute("INSERT INTO PhoneBrand(name) VALUES('iPhone')")
        mid = self.conn.execute(
            "INSERT INTO PhoneModel(phone_brand_id,name) VALUES(1,'15')").lastrowid
        self.conn.execute(
            "INSERT INTO OptionModel(option_id,model_id) VALUES(?,?)", (oid, mid))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO OptionModel(option_id,model_id) VALUES(?,?)", (oid, mid))
        # FK:型號不存在 → 違反
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO OptionModel(option_id,model_id) VALUES(?,999)", (oid,))


if __name__ == "__main__":
    unittest.main()
