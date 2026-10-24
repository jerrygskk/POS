"""v19 型號加裝置類型:資料與關聯保留、連同 v18 一次升級、失敗完整回滾。"""
import re
import sqlite3
from pathlib import Path
from unittest.mock import patch

from base import ConnTestCase
from lib import db_schema
from lib.db import get_conn, init_db
from test_migration_stocktake_approval import OLD_ITEMS, OLD_SESSIONS


OLD_PHONE_MODEL = """CREATE TABLE PhoneModel(
  model_id INTEGER PRIMARY KEY AUTOINCREMENT,
  phone_brand_id INTEGER NOT NULL REFERENCES PhoneBrand(phone_brand_id),
  name TEXT NOT NULL,
  alias TEXT,
  series TEXT,
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(phone_brand_id, name)
);"""


class DeviceTypeMigrationTests(ConnTestCase):
    def make_old_database(self, version=18):
        self.conn.close()
        Path(self.db).unlink()
        conn = get_conn(self.db)
        schema = re.sub(r"CREATE TABLE IF NOT EXISTS DeviceType\(.*?\n\);", "", db_schema.SCHEMA, flags=re.S)
        schema = re.sub(r"CREATE TABLE IF NOT EXISTS PhoneModel\(.*?\n\);", OLD_PHONE_MODEL, schema, flags=re.S)
        if version < 18:
            schema = re.sub(r"CREATE TABLE IF NOT EXISTS StocktakeSession\(.*?\n\);", OLD_SESSIONS, schema, flags=re.S)
            schema = re.sub(r"CREATE TABLE IF NOT EXISTS StocktakeItem\(.*?\n\);", OLD_ITEMS, schema, flags=re.S)
            schema = re.sub(r"CREATE UNIQUE INDEX IF NOT EXISTS idx_stocktake_one_active.*?;", "", schema, flags=re.S)
        conn.executescript(schema)
        conn.execute("INSERT INTO Setting(key,value) VALUES('schema_version',?)", (str(version),))
        conn.execute("INSERT INTO Category(category_id,name) VALUES(1,'migration category')")
        conn.execute("INSERT INTO Product(product_id,name,category_id) VALUES(1,'migration product',1)")
        conn.execute("INSERT INTO Variant(variant_id,product_id) VALUES(1,1)")
        conn.execute("INSERT INTO PhoneBrand(phone_brand_id,name) VALUES(1,'Apple')")
        conn.execute("INSERT INTO PhoneModel(model_id,phone_brand_id,name,alias,series,sort) VALUES(5,1,'iPhone 17','17','17 系列',1)")
        conn.execute("INSERT INTO PhoneModel(model_id,phone_brand_id,name,sort) VALUES(9,1,'iPhone 16',2)")
        conn.execute("DELETE FROM PhoneModel WHERE model_id=9")  # 流水號停在 9,新型號須大於它
        conn.execute("INSERT INTO PhoneModel(model_id,phone_brand_id,name,sort) VALUES(7,1,'iPhone 15',3)")
        conn.execute("INSERT INTO AttributeField(field_id,name) VALUES(1,'顏色')")
        conn.execute("INSERT INTO AttributeOption(option_id,field_id,value) VALUES(1,1,'沙漠色')")
        conn.execute("INSERT INTO VariantModel(variant_id,model_id) VALUES(1,5)")
        conn.execute("INSERT INTO VariantModel(variant_id,model_id) VALUES(1,7)")
        conn.execute("INSERT INTO OptionModel(option_id,model_id) VALUES(1,5)")
        if version < 18:
            conn.execute("INSERT INTO StocktakeSession(session_id,status) VALUES(3,'closed')")
            conn.execute("INSERT INTO StocktakeItem(id,session_id,variant_id,system_qty) VALUES(4,3,1,2)")
        conn.commit()
        self.conn = conn

    def snapshot(self):
        return {table: (self.conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone(),
                        [tuple(row) for row in self.conn.execute(f"SELECT * FROM {table} ORDER BY 1,2")])
                for table in ("PhoneModel", "VariantModel", "OptionModel", "Setting")}

    def assert_upgraded(self):
        cols = [row[1] for row in self.conn.execute("PRAGMA table_info(PhoneModel)")]
        self.assertIn("device_type_id", cols)
        self.assertNotIn("series", cols)
        phone = self.conn.execute("SELECT device_type_id FROM DeviceType WHERE name='手機'").fetchone()[0]
        self.assertEqual([tuple(r) for r in self.conn.execute(
            "SELECT model_id,name,alias,device_type_id FROM PhoneModel ORDER BY model_id")],
            [(5, "iPhone 17", "17", phone), (7, "iPhone 15", None, phone)])
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT variant_id,model_id FROM VariantModel ORDER BY 2")], [(1, 5), (1, 7)])
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT option_id,model_id FROM OptionModel")], [(1, 5)])
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(int(self.conn.execute("SELECT value FROM Setting WHERE key='schema_version'").fetchone()[0]),
                         db_schema.SCHEMA_VERSION)
        new_id = self.conn.execute("INSERT INTO PhoneModel(phone_brand_id,device_type_id,name) VALUES(1,?,'iPhone 18')", (phone,)).lastrowid
        self.assertGreater(new_id, 9)
        # 子表的外鍵約束重建後仍在
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO VariantModel(variant_id,model_id) VALUES(1,999)")
        self.conn.rollback()
        self.assertEqual(self.conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '%\\_mig' ESCAPE '\\'").fetchall(), [])

    def test_v18_upgrade_keeps_models_links_and_next_id(self):
        self.make_old_database(18)
        init_db(self.db)
        self.assert_upgraded()

    def test_v17_upgrades_through_v18_and_v19_in_one_run(self):
        self.make_old_database(17)
        init_db(self.db)
        self.assert_upgraded()
        self.assertEqual(self.conn.execute("SELECT needs_recount FROM StocktakeItem WHERE id=4").fetchone()[0], 0)

    def test_same_name_allowed_across_types_after_upgrade(self):
        self.make_old_database(18)
        init_db(self.db)
        tablet = self.conn.execute("INSERT INTO DeviceType(name) VALUES('平板')").lastrowid
        self.conn.execute("INSERT INTO PhoneModel(phone_brand_id,device_type_id,name) VALUES(1,?,'iPhone 17')", (tablet,))
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("INSERT INTO PhoneModel(phone_brand_id,device_type_id,name) VALUES(1,?,'iPhone 17')", (tablet,))
        self.conn.rollback()

    def _assert_failure_rolls_back(self, failure_point):
        self.make_old_database(18)
        before = self.snapshot()
        real_get_conn = get_conn
        hit = []

        class FailingConnection:
            def __init__(self, conn):
                self.conn = conn

            def __getattr__(self, name):
                return getattr(self.conn, name)

            def execute(self, sql, parameters=()):
                result = self.conn.execute(sql, parameters)
                normalized = " ".join(sql.upper().split())
                if failure_point(normalized):
                    hit.append(normalized)
                    raise RuntimeError("injected migration failure")
                return result

        with patch("lib.db.get_conn", side_effect=lambda path: FailingConnection(real_get_conn(path))):
            with self.assertRaisesRegex(RuntimeError, "injected migration failure"):
                init_db(self.db)
        self.assertEqual(len(hit), 1, "必須真的命中指定的失敗點")
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.conn.execute("SELECT name FROM sqlite_master WHERE name LIKE '%\\_mig' ESCAPE '\\'").fetchall(), [])

    def test_failure_after_children_dropped_restores_v18(self):
        self._assert_failure_rolls_back(lambda sql: sql == "DROP TABLE OPTIONMODEL")

    def test_failure_after_parent_rename_restores_v18(self):
        self._assert_failure_rolls_back(lambda sql: sql == "ALTER TABLE PHONEMODEL_MIG RENAME TO PHONEMODEL")
