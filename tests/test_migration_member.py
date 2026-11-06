"""schema v22:會員名單(Member/MemberPhone)與銷售單 member_id。"""
import sqlite3
from pathlib import Path

from base import ConnTestCase
from lib import db_schema
from lib.db import get_conn, init_db

MEMBER_DDL_START = "CREATE TABLE IF NOT EXISTS Member("
MEMBER_DDL_END = "CREATE INDEX IF NOT EXISTS idx_memberphone_phone ON MemberPhone(phone);\n"
SALE_COLUMN = "  member_id INTEGER REFERENCES Member(member_id)  -- 會員;一般客人與舊單為 NULL\n"
EMPLOYEE_NEW = "employee INTEGER CHECK(employee BETWEEN 1 AND 6),  -- 結帳員工代號"
EMPLOYEE_OLD = "employee INTEGER CHECK(employee BETWEEN 1 AND 6)  -- 結帳員工代號"


def _v21_schema():
    schema = db_schema.SCHEMA
    start, end = schema.index(MEMBER_DDL_START), schema.index(MEMBER_DDL_END) + len(MEMBER_DDL_END)
    schema = schema[:start] + schema[end:]
    assert SALE_COLUMN in schema and EMPLOYEE_NEW in schema
    return schema.replace(SALE_COLUMN, "").replace(EMPLOYEE_NEW, EMPLOYEE_OLD)


class MemberMigrationTests(ConnTestCase):
    def test_upgrade_adds_member_tables_and_null_member_id(self):
        self.conn.close()
        Path(self.db).unlink()
        conn = get_conn(self.db)
        conn.executescript(_v21_schema())
        conn.execute("INSERT INTO Setting(key,value) VALUES('schema_version',?)", (str(db_schema.BASE_VERSION + 20),))
        conn.execute("INSERT INTO Sale(sale_id,payment,total,paid) VALUES(5,'現金',70,70)")
        conn.commit(); conn.close()

        init_db(self.db)
        self.conn = get_conn(self.db)
        self.assertEqual(self.conn.execute("SELECT value FROM Setting WHERE key='schema_version'").fetchone()[0],
                         str(db_schema.SCHEMA_VERSION))
        self.assertIsNone(self.conn.execute("SELECT member_id FROM Sale WHERE sale_id=5").fetchone()[0])
        names = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"Member", "MemberPhone"} <= names)

    def test_phone_check_rejects_bad_format(self):
        mid = self.conn.execute("INSERT INTO Member(name) VALUES('王小明')").lastrowid
        for bad in ("0912-00011", "0812000111", "091200011"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.conn.execute("INSERT INTO MemberPhone(member_id,phone) VALUES(?,?)", (mid, bad))

    def test_same_phone_two_members_allowed(self):
        a = self.conn.execute("INSERT INTO Member(name) VALUES('王小明')").lastrowid
        b = self.conn.execute("INSERT INTO Member(name) VALUES('王太太')").lastrowid
        for mid in (a, b):
            self.conn.execute("INSERT INTO MemberPhone(member_id,phone) VALUES(?,'0912000111')", (mid,))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM MemberPhone WHERE phone='0912000111'").fetchone()[0], 2)
