import unittest, sqlite3
from base import ConnTestCase


class TestCatalogTables(ConnTestCase):
    # 新表都建齊
    def test_new_tables_exist(self):
        names = {r["name"] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ["Category", "Brand", "BrandCategory", "PhoneBrand",
                  "PhoneModel", "VariantModel", "AttributeField",
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

    # PhoneModel UNIQUE(phone_brand_id,device_type_id,name):同品牌同類型同名違反;跨品牌或跨類型同名可
    def test_phonemodel_unique(self):
        ip = self.conn.execute(
            "INSERT INTO PhoneBrand(name) VALUES('iPhone')").lastrowid
        sam = self.conn.execute(
            "INSERT INTO PhoneBrand(name) VALUES('SAMSUNG')").lastrowid
        self.conn.execute("INSERT INTO PhoneModel(phone_brand_id,device_type_id,name) VALUES(?,1,'15')", (ip,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO PhoneModel(phone_brand_id,device_type_id,name) VALUES(?,1,'15')", (ip,))
        # 不同品牌同名可共存
        self.conn.execute("INSERT INTO PhoneModel(phone_brand_id,device_type_id,name) VALUES(?,1,'15')", (sam,))
        # 同品牌不同裝置類型同名可共存
        tablet = self.conn.execute("INSERT INTO DeviceType(name) VALUES('平板')").lastrowid
        self.conn.execute("INSERT INTO PhoneModel(phone_brand_id,device_type_id,name) VALUES(?,?,'15')", (ip, tablet))

    # PhoneModel FK:指向不存在的手機品牌 → 違反
    def test_phonemodel_fk_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO PhoneModel(phone_brand_id,device_type_id,name) VALUES(999,1,'15')")

    # VariantModel 複合主鍵
    def test_variantmodel_pk(self):
        self.conn.execute("INSERT INTO Product(name) VALUES('殼')")
        self.conn.execute("INSERT INTO Variant(product_id) VALUES(1)")
        self.conn.execute("INSERT INTO PhoneBrand(name) VALUES('iPhone')")
        self.conn.execute("INSERT INTO PhoneModel(phone_brand_id,device_type_id,name) VALUES(1,1,'15')")
        self.conn.execute("INSERT INTO VariantModel(variant_id,model_id) VALUES(1,1)")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO VariantModel(variant_id,model_id) VALUES(1,1)")

    def _field(self, name, field_type="select", category="手機殼"):
        cid = self.conn.execute("INSERT OR IGNORE INTO Category(name) VALUES(?)", (category,)).lastrowid
        cid = self.conn.execute("SELECT category_id FROM Category WHERE name=?", (category,)).fetchone()[0]
        return self.conn.execute(
            "INSERT INTO AttributeField(category_id,name,field_type) VALUES(?,?,?)",
            (cid, name, field_type)).lastrowid

    # 規格欄隸屬種類:category_id 不可空、指向的種類須存在
    def test_attributefield_requires_category(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO AttributeField(name) VALUES('版型')")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO AttributeField(category_id,name) VALUES(999,'版型')")

    # 同種類同名禁止;不同種類同名合法(正規化同名由應用層再擋)
    def test_attributefield_unique_per_category(self):
        self._field("版型")
        with self.assertRaises(sqlite3.IntegrityError):
            self._field("版型", "text")
        self._field("版型", category="鋼化玻璃")

    # AttributeOption UNIQUE(field_id,value)
    def test_attributeoption_unique(self):
        fid = self._field("顏色")
        self.conn.execute(
            "INSERT INTO AttributeOption(field_id,value) VALUES(?,'黑')", (fid,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO AttributeOption(field_id,value) VALUES(?,'黑')", (fid,))

    # field_type CHECK 限制
    def test_field_type_check(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self._field("壞欄", "number")

    # 預設選項必須屬於本欄(複合外鍵),且只限 select
    def test_default_option_must_belong_to_field_and_be_select(self):
        fid = self._field("顏色")
        other = self._field("材質")
        oid = self.conn.execute("INSERT INTO AttributeOption(field_id,value) VALUES(?,'黑')", (fid,)).lastrowid
        other_oid = self.conn.execute("INSERT INTO AttributeOption(field_id,value) VALUES(?,'霧面')", (other,)).lastrowid
        self.conn.execute("UPDATE AttributeField SET default_option_id=? WHERE field_id=?", (oid, fid))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE AttributeField SET default_option_id=? WHERE field_id=?", (other_oid, fid))
        multi = self._field("配件", "multi")
        multi_oid = self.conn.execute("INSERT INTO AttributeOption(field_id,value) VALUES(?,'掛繩')", (multi,)).lastrowid
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE AttributeField SET default_option_id=? WHERE field_id=?", (multi_oid, multi))

    # 被當預設的選項不能直接刪(要先清預設)
    def test_default_option_cannot_be_deleted_while_referenced(self):
        fid = self._field("顏色")
        oid = self.conn.execute("INSERT INTO AttributeOption(field_id,value) VALUES(?,'黑')", (fid,)).lastrowid
        self.conn.execute("UPDATE AttributeField SET default_option_id=? WHERE field_id=?", (oid, fid))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM AttributeOption WHERE option_id=?", (oid,))

    # 規格值的選項必須屬於該欄(複合外鍵)
    def test_variantattribute_option_must_belong_to_field(self):
        fid, oid = self._make_variant_and_option()
        other = self._field("顏色")
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO VariantAttribute(variant_id,field_id,option_id) VALUES(1,?,?)", (other, oid))

    # 同款式同一文字欄只能一筆
    def test_variantattribute_text_unique(self):
        self._make_variant_and_option()
        text = self._field("備註", "text")
        self.conn.execute("INSERT INTO VariantAttribute(variant_id,field_id,text_value) VALUES(1,?,'a')", (text,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO VariantAttribute(variant_id,field_id,text_value) VALUES(1,?,'b')", (text,))

    def _make_variant_and_option(self):
        fid = self._field("規格")
        cid = self.conn.execute("SELECT category_id FROM AttributeField WHERE field_id=?", (fid,)).fetchone()[0]
        self.conn.execute("INSERT INTO Product(name,category_id) VALUES('膜',?)", (cid,))
        self.conn.execute("INSERT INTO Variant(product_id) VALUES(1)")
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
            "INSERT INTO PhoneModel(phone_brand_id,device_type_id,name) VALUES(1,1,'15')").lastrowid
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
