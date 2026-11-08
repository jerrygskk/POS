"""schema v23:付款方式「刷卡」改為「轉帳」。"""
import json

from base import ConnTestCase
from lib import db_schema
from lib.db import get_conn, init_db


class PaymentTransferMigrationTests(ConnTestCase):
    def test_upgrade_renames_card_to_transfer(self):
        self.conn.execute("UPDATE Setting SET value=? WHERE key='payments'",
                          (json.dumps(["現金", "刷卡", "行動支付"], ensure_ascii=False),))
        self.conn.execute("INSERT INTO Sale(sale_id,payment,total,paid) VALUES(5,'刷卡',70,70)")
        self.conn.execute("INSERT INTO Sale(sale_id,payment,total,paid) VALUES(6,'現金',30,50)")
        self.conn.execute("UPDATE Setting SET value=? WHERE key='schema_version'", (str(db_schema.BASE_VERSION + 21),))
        self.conn.commit(); self.conn.close()

        init_db(self.db)
        self.conn = get_conn(self.db)
        payments = json.loads(self.conn.execute("SELECT value FROM Setting WHERE key='payments'").fetchone()[0])
        self.assertEqual(payments, ["現金", "轉帳", "行動支付"])
        rows = dict(self.conn.execute("SELECT sale_id,payment FROM Sale").fetchall())
        self.assertEqual(rows, {5: "轉帳", 6: "現金"})

    def test_fresh_db_seeds_transfer(self):
        payments = json.loads(self.conn.execute("SELECT value FROM Setting WHERE key='payments'").fetchone()[0])
        self.assertEqual(payments, ["現金", "轉帳", "行動支付"])
