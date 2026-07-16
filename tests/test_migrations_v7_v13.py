"""v7–v13 append 式 migration 驗證。

建 v6 結構假資料 DB → init_db 自動升級 → 逐項驗證:
model_mode 依資料、required 常數表、欄位合併與引用改指、同名不同型態中止、
價格只搬有值、NULL 維持 NULL、Brand 無 active、BrandCategory 回填、VariantIssue 結構;
另驗證全新 DB 與升級後 DB 結構一致。
"""
import os
import sqlite3
import tempfile
import unittest

import base  # noqa: F401  確保 sys.path 有專案根
from lib import db_schema
from lib.db import get_conn, init_db


# v6 結構(本批遷移前)DDL:僅列遷移會讀/改或需帶資料的表;其餘缺表由
# init_db 的 executescript(SCHEMA) 以 IF NOT EXISTS 補齊。
_V6_DDL = """
CREATE TABLE Category(
  category_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE Brand(
  brand_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE BrandCategory(
  brand_id INTEGER NOT NULL REFERENCES Brand(brand_id),
  category_id INTEGER NOT NULL REFERENCES Category(category_id),
  PRIMARY KEY(brand_id, category_id)
);
CREATE TABLE PhoneBrand(
  phone_brand_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE PhoneModel(
  model_id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone_brand_id INTEGER NOT NULL REFERENCES PhoneBrand(phone_brand_id),
  name TEXT NOT NULL, alias TEXT, series TEXT,
  sort INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(phone_brand_id, name)
);
CREATE TABLE Product(
  product_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  category_id INTEGER REFERENCES Category(category_id),
  brand_id INTEGER REFERENCES Brand(brand_id),
  default_price INTEGER,
  note TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE Variant(
  variant_id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES Product(product_id),
  price INTEGER,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE AttributeField(
  field_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  category_id INTEGER REFERENCES Category(category_id),
  field_type TEXT NOT NULL DEFAULT 'select'
    CHECK(field_type IN ('select','text','multi','tags')),
  default_option_id INTEGER REFERENCES AttributeOption(option_id),
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(category_id, name)
);
CREATE TABLE AttributeOption(
  option_id INTEGER PRIMARY KEY AUTOINCREMENT,
  field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
  value TEXT NOT NULL,
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(field_id, value)
);
CREATE TABLE VariantAttribute(
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
  option_id INTEGER REFERENCES AttributeOption(option_id),
  text_value TEXT,
  CHECK((option_id IS NULL) <> (text_value IS NULL))
);
CREATE UNIQUE INDEX idx_va_unique ON VariantAttribute(variant_id, field_id, option_id);
CREATE TABLE OptionModel(
  option_id INTEGER NOT NULL REFERENCES AttributeOption(option_id),
  model_id INTEGER NOT NULL REFERENCES PhoneModel(model_id),
  PRIMARY KEY(option_id, model_id)
);
CREATE TABLE VariantModel(
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  model_id INTEGER NOT NULL REFERENCES PhoneModel(model_id),
  PRIMARY KEY(variant_id, model_id)
);
CREATE TABLE CategoryField(
  category_id INTEGER NOT NULL REFERENCES Category(category_id),
  field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
  PRIMARY KEY(category_id, field_id)
);
CREATE TABLE Setting(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _new_v6_db():
    db = os.path.join(tempfile.mkdtemp(), "v6.db")
    conn = sqlite3.connect(db)
    conn.executescript(_V6_DDL)
    conn.execute("INSERT INTO Setting(key,value) VALUES('schema_version','6')")
    conn.commit()
    conn.close()
    return db


def _table_cols(conn):
    """回傳 {table: [(col_name, type, notnull, pk), ...]},供結構比對。"""
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    out = {}
    for t in tables:
        out[t] = [(r["name"], r["type"], r["notnull"], r["pk"])
                  for r in conn.execute(f"PRAGMA table_info({t})")]
    return out


class TestMigrationV7toV13(unittest.TestCase):
    def setUp(self):
        db = _new_v6_db()
        conn = sqlite3.connect(db)
        conn.executescript("""
          INSERT INTO Category(category_id,name) VALUES
            (1,'鋼化玻璃'),(2,'手機殼'),(3,'充電線');
          INSERT INTO Brand(brand_id,name,active) VALUES
            (1,'A牌',1),(2,'B牌',0);            -- B牌 停用,升級後仍須存在
          INSERT INTO PhoneBrand(phone_brand_id,name) VALUES(1,'iPhone');
          INSERT INTO PhoneModel(model_id,phone_brand_id,name) VALUES(1,1,'15');

          -- AttributeField(舊:含 category_id/sort/default_option_id)
          INSERT INTO AttributeField(field_id,name,category_id,field_type,sort) VALUES
            (1,'材質',1,'select',5),
            (2,'版型',1,'select',6),
            (3,'接頭',3,'select',1),
            (4,'長度',3,'select',2),
            (5,'顏色',NULL,'select',0),      -- 共用欄(存活)
            (6,'顏色',2,'select',0),          -- 專用欄同名同型→v9 併入 field 5
            (7,'特性詞條',NULL,'tags',0);
          INSERT INTO AttributeOption(option_id,field_id,value,sort) VALUES
            (10,1,'玻璃',0),
            (11,5,'黑',0),
            (12,5,'白',0),
            (13,6,'黑',0);                    -- 與 opt 11 同值,v9 併入 11
          UPDATE AttributeField SET default_option_id=10 WHERE field_id=1;

          -- 既有 CategoryField 連結列(共用欄掛用)
          INSERT INTO CategoryField(category_id,field_id) VALUES
            (1,5),(1,7),(3,7),(2,5);          -- (2,5) 與 v8 補建的 (2,6)→v9→(2,5) 撞,測去重

          -- 大產品/子產品
          INSERT INTO Product(product_id,name,category_id,brand_id,default_price) VALUES
            (1,'A牌手機殼',2,1,NULL),
            (2,'HODA鋼化玻璃',1,2,590),
            (3,'A牌充電線',3,1,NULL);          -- default_price 為 NULL
          INSERT INTO Variant(variant_id,product_id,price) VALUES
            (1,1,NULL),                        -- 手機殼(掛型號→model_mode required)
            (2,2,NULL),                        -- price NULL + default 590 → 搬 590
            (3,2,200),                         -- price 有值 → 不動
            (4,3,NULL);                        -- price NULL + default NULL → 維持 NULL
          INSERT INTO VariantModel(variant_id,model_id) VALUES(1,1);
          -- 手機殼變體用 field6/opt13(顏色黑)→v9 應改指 field5/opt11
          INSERT INTO VariantAttribute(variant_id,field_id,option_id) VALUES(1,6,13);

          -- 既有 BrandCategory(測 v12 OR IGNORE 冪等)
          INSERT INTO BrandCategory(brand_id,category_id) VALUES(1,2);
        """)
        conn.commit()
        conn.close()
        self.db = db
        init_db(db)  # 觸發 v7–v13 升級
        self.conn = get_conn(db)

    def tearDown(self):
        self.conn.close()

    def _one(self, sql, args=()):
        return self.conn.execute(sql, args).fetchone()

    def test_version_bumped(self):
        v = int(self._one("SELECT value FROM Setting WHERE key='schema_version'")["value"])
        self.assertEqual(v, db_schema.SCHEMA_VERSION)
        self.assertEqual(v, 13)

    # v7 -----------------------------------------------------------------
    def test_model_mode_from_data(self):
        modes = {r["name"]: r["model_mode"] for r in self.conn.execute(
            "SELECT name, model_mode FROM Category")}
        self.assertEqual(modes["手機殼"], "required")   # 有 VariantModel
        self.assertEqual(modes["鋼化玻璃"], "hidden")   # 無 VariantModel
        self.assertEqual(modes["充電線"], "hidden")

    # v8 -----------------------------------------------------------------
    def test_categoryfield_templatized(self):
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(CategoryField)")}
        self.assertTrue({"sort", "required", "default_option_id", "active"} <= cols)

    def test_required_constant_table(self):
        def req(cid, fid):
            r = self._one(
                "SELECT required FROM CategoryField WHERE category_id=? AND field_id=?",
                (cid, fid))
            return None if r is None else r["required"]
        # 鋼化玻璃 材質=1、版型=1(常數表)
        self.assertEqual(req(1, 1), 1)
        self.assertEqual(req(1, 2), 1)
        # 充電線 接頭=1、長度=1
        self.assertEqual(req(3, 3), 1)
        self.assertEqual(req(3, 4), 1)
        # 特性詞條 一律 0
        self.assertEqual(req(1, 7), 0)
        self.assertEqual(req(3, 7), 0)
        # 未列到組合(鋼化玻璃 顏色)=0
        self.assertEqual(req(1, 5), 0)

    def test_dedicated_field_default_and_sort_migrated(self):
        # field1 材質:sort=5、default_option_id=10 應自 AttributeField 搬入 CategoryField
        r = self._one(
            "SELECT sort, default_option_id FROM CategoryField "
            "WHERE category_id=1 AND field_id=1")
        self.assertEqual(r["sort"], 5)
        self.assertEqual(r["default_option_id"], 10)

    # v9 -----------------------------------------------------------------
    def test_attributefield_globalized(self):
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(AttributeField)")}
        self.assertNotIn("category_id", cols)
        self.assertNotIn("default_option_id", cols)
        self.assertNotIn("sort", cols)

    def test_field_merge_same_name_type(self):
        # field6 併入 field5;顏色 select 僅存一欄
        self.assertIsNone(self._one("SELECT 1 FROM AttributeField WHERE field_id=6"))
        n = self._one(
            "SELECT COUNT(*) c FROM AttributeField WHERE name='顏色' AND field_type='select'"
        )["c"]
        self.assertEqual(n, 1)

    def test_option_merge_and_va_repoint(self):
        # opt13 併入 opt11
        self.assertIsNone(self._one("SELECT 1 FROM AttributeOption WHERE option_id=13"))
        # 手機殼變體規格值改指存活 field5 / opt11
        r = self._one("SELECT field_id, option_id FROM VariantAttribute WHERE variant_id=1")
        self.assertEqual((r["field_id"], r["option_id"]), (5, 11))

    def test_categoryfield_ref_repoint_and_dedup(self):
        # (2,6) 經 v9 改指 (2,5),與既有 (2,5) 去重 → 僅一列,且無 field_id=6
        rows = [r["field_id"] for r in self.conn.execute(
            "SELECT field_id FROM CategoryField WHERE category_id=2")]
        self.assertEqual(rows.count(6), 0)
        self.assertEqual(rows.count(5), 1)

    # v10 ----------------------------------------------------------------
    def test_product_default_price_removed(self):
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(Product)")}
        self.assertNotIn("default_price", cols)

    def test_price_moved_only_when_present(self):
        def price(vid):
            return self._one("SELECT price FROM Variant WHERE variant_id=?", (vid,))["price"]
        self.assertEqual(price(2), 590)   # NULL + default 590 → 搬入
        self.assertEqual(price(3), 200)   # 原有值 → 不動
        self.assertIsNone(price(4))       # 兩者皆 NULL → 維持 NULL
        self.assertIsNone(price(1))       # 手機殼變體無價、無 default → NULL

    # v11 ----------------------------------------------------------------
    def test_brand_active_removed_disabled_survives(self):
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(Brand)")}
        self.assertNotIn("active", cols)
        names = {r["name"] for r in self.conn.execute("SELECT name FROM Brand")}
        self.assertEqual(names, {"A牌", "B牌"})  # 停用 B牌 仍存在

    # v12 ----------------------------------------------------------------
    def test_brandcategory_backfill(self):
        pairs = {(r["brand_id"], r["category_id"]) for r in self.conn.execute(
            "SELECT brand_id, category_id FROM BrandCategory")}
        self.assertEqual(pairs, {(1, 2), (2, 1), (1, 3)})

    # v13 ----------------------------------------------------------------
    def test_variant_issue_structure(self):
        cols = {r["name"]: r for r in self.conn.execute("PRAGMA table_info(VariantIssue)")}
        self.assertEqual(
            set(cols),
            {"issue_id", "variant_id", "issue_type", "field_id",
             "source_value", "related_variant_id"})
        self.assertEqual(cols["variant_id"]["notnull"], 1)
        self.assertEqual(cols["issue_type"]["notnull"], 1)
        # variant_id 索引存在
        idx = {r["name"] for r in self.conn.execute("PRAGMA index_list(VariantIssue)")}
        self.assertIn("idx_variant_issue_variant", idx)
        # CHECK 生效:非法 issue_type 應被拒
        self.conn.execute("INSERT INTO Variant(product_id) VALUES(1)")
        vid = self.conn.execute("SELECT MAX(variant_id) m FROM Variant").fetchone()["m"]
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO VariantIssue(variant_id,issue_type) VALUES(?, 'bogus')",
                (vid,))


class TestMigrationConflictAborts(unittest.TestCase):
    """v9 前置檢查:兩個「有資料」的同名不同型欄 → 中止並列出衝突。"""

    def test_two_data_bearing_conflicts_raise(self):
        db = _new_v6_db()
        conn = sqlite3.connect(db)
        conn.executescript("""
          INSERT INTO Category(category_id,name) VALUES(1,'A'),(2,'B');
          INSERT INTO Product(product_id,name,category_id) VALUES(1,'p',2);
          INSERT INTO Variant(variant_id,product_id) VALUES(1,1);
          INSERT INTO AttributeField(field_id,name,category_id,field_type) VALUES
            (1,'尺寸',1,'select'),
            (2,'尺寸',2,'text');            -- 正規化同名、型態不同
          INSERT INTO AttributeOption(option_id,field_id,value) VALUES(1,1,'L');
          -- 兩欄皆有資料:field1 有選項、field2 有 VariantAttribute 文字值
          INSERT INTO VariantAttribute(variant_id,field_id,text_value) VALUES(1,2,'大');
        """)
        conn.commit()
        conn.close()
        with self.assertRaises(ValueError) as ctx:
            init_db(db)
        self.assertIn("尺寸", str(ctx.exception))


class TestMigrationConflictAutoResolve(unittest.TestCase):
    """v9 衝突自動解決:衝突組中零選項且零使用者直接刪除(連同 CategoryField 綁定),
    再繼續合併;不再中止。"""

    def test_zero_usage_conflict_field_dropped(self):
        db = _new_v6_db()
        conn = sqlite3.connect(db)
        conn.executescript("""
          INSERT INTO Category(category_id,name) VALUES(1,'A'),(2,'B');
          INSERT INTO Product(product_id,name,category_id) VALUES(1,'p',1);
          INSERT INTO Variant(variant_id,product_id) VALUES(1,1);
          -- 種子零使用「顏色」(text,無選項無使用)與各種類「顏色」select 衝突
          INSERT INTO AttributeField(field_id,name,category_id,field_type) VALUES
            (1,'顏色',NULL,'text'),         -- 零選項零使用 → 應被自動刪除
            (2,'顏色',1,'select'),          -- 有資料(選項+使用)
            (3,'顏色',2,'select');          -- 有資料(選項)
          INSERT INTO AttributeOption(option_id,field_id,value) VALUES
            (10,2,'黑'),(11,3,'白');
          INSERT INTO VariantAttribute(variant_id,field_id,option_id) VALUES(1,2,10);
          -- 種子欄有 CategoryField 綁定,應一併丟棄
          INSERT INTO CategoryField(category_id,field_id) VALUES(1,1),(1,2),(2,3);
        """)
        conn.commit()
        conn.close()
        init_db(db)  # 不應 raise
        c = get_conn(db)
        try:
            # 種子 text 顏色(field1)已刪除
            self.assertIsNone(c.execute(
                "SELECT 1 FROM AttributeField WHERE field_id=1").fetchone())
            # 六(此處二)個 select 顏色合併為一個全域欄
            rows = c.execute(
                "SELECT COUNT(*) n FROM AttributeField WHERE name='顏色' "
                "AND field_type='select'").fetchone()
            self.assertEqual(rows["n"], 1)
            survivor = c.execute(
                "SELECT field_id FROM AttributeField WHERE name='顏色'").fetchone()["field_id"]
            self.assertEqual(survivor, 2)  # 存活取最小 field_id
            # 使用中的規格值改指存活欄
            va = c.execute("SELECT field_id FROM VariantAttribute WHERE variant_id=1").fetchone()
            self.assertEqual(va["field_id"], 2)
            # 兩選項(黑/白)合併入存活欄
            opt = c.execute(
                "SELECT COUNT(*) n FROM AttributeOption WHERE field_id=2").fetchone()["n"]
            self.assertEqual(opt, 2)
            # 種子欄的 CategoryField 綁定(1,1)已丟棄;無指向已刪欄的列
            self.assertIsNone(c.execute(
                "SELECT 1 FROM CategoryField WHERE field_id=1").fetchone())
            # (2,3)→改指存活欄 2,與 (1,2) 併存
            cats = {r["category_id"] for r in c.execute(
                "SELECT category_id FROM CategoryField WHERE field_id=2")}
            self.assertEqual(cats, {1, 2})
        finally:
            c.close()


class TestUpgradedMatchesFresh(unittest.TestCase):
    """全新 v13 DB 與 v6 升級後 DB 結構一致(欄位集合、型態、notnull、pk)。"""

    def test_structure_identical(self):
        # 升級後 DB
        up = _new_v6_db()
        conn = sqlite3.connect(up)
        conn.executescript(
            "INSERT INTO Category(category_id,name) VALUES(1,'鋼化玻璃');"
            "INSERT INTO Brand(brand_id,name) VALUES(1,'A牌');"
            "INSERT INTO Product(product_id,name,category_id,brand_id) VALUES(1,'x',1,1);"
            "INSERT INTO Variant(variant_id,product_id) VALUES(1,1);")
        conn.commit()
        conn.close()
        init_db(up)

        # 全新 DB
        fresh = os.path.join(tempfile.mkdtemp(), "fresh.db")
        init_db(fresh)

        cu = get_conn(up)
        cf = get_conn(fresh)
        try:
            up_cols, fresh_cols = _table_cols(cu), _table_cols(cf)
            self.assertEqual(set(up_cols), set(fresh_cols),
                             "表集合不一致")
            for t in fresh_cols:
                self.assertEqual(up_cols[t], fresh_cols[t], f"{t} 欄位結構不一致")
        finally:
            cu.close()
            cf.close()


if __name__ == "__main__":
    unittest.main()
