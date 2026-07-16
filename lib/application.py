import sqlite3

from lib.db import db_conn
from lib.application_errors import DatabaseError


class Repository:
    """使用外部注入的連線執行 SQL；交易提交由 Service 邊界負責。"""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, parameters=()):
        return self.connection.execute(sql, parameters)

    def fetch_one(self, sql, parameters=()):
        return self.execute(sql, parameters).fetchone()


class TransactionRunner:
    """以單一資料庫連線執行工作，成功提交、失敗回滾。"""

    def __init__(self, db_path, connection_context=db_conn, logger=None):
        self.db_path = db_path
        self.connection_context = connection_context
        self.logger = logger

    def run(self, work):
        with self.connection_context(self.db_path) as connection:
            try:
                result = work(connection)
                connection.commit()
                return result
            except sqlite3.DatabaseError as exc:
                self._try_rollback(connection)
                raise DatabaseError() from exc
            except Exception:
                self._try_rollback(connection)
                raise

    def _try_rollback(self, connection):
        try:
            connection.rollback()
        except Exception:
            if self.logger is None:
                return
            try:
                self.logger.exception("交易回滾失敗")
            except Exception:
                pass


class ApplicationFacade:
    """提供畫面層可共用的交易式應用操作入口。"""

    def __init__(self, transaction_runner):
        self.transaction_runner = transaction_runner

    def execute(self, operation):
        return self.transaction_runner.run(
            lambda connection: operation(Repository(connection))
        )
