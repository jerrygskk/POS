import sqlite3
from collections.abc import Mapping

from lib.db import db_conn
from lib.application_errors import DatabaseError, ValidationError


class BaseRepository:
    """使用外部注入的連線執行 SQL；交易提交由 Service 邊界負責。"""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, parameters=()):
        return self.connection.execute(sql, parameters)

    def one(self, sql, parameters=()):
        return self.execute(sql, parameters).fetchone()

    def all(self, sql, parameters=()):
        return self.execute(sql, parameters).fetchall()


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


class BaseFacade:
    """統一驗證應用操作邊界，並在單一交易內分派領域操作。"""

    ACTIONS = set()
    ERROR_MESSAGE = "不支援的操作"

    def __init__(self, db_path):
        self.runner = TransactionRunner(db_path, connection_context=db_conn)

    def invoke(self, action, payload=None):
        payload = {} if payload is None else payload
        if not isinstance(action, str) or action not in self.ACTIONS:
            raise ValidationError(self.ERROR_MESSAGE)
        self._validate_payload_type(payload)
        payload = self._prepare_payload(action, payload)
        return self.runner.run(
            lambda connection: self._dispatch(action, payload, connection)
        )

    def _prepare_payload(self, action, payload):
        return payload

    def _validate_payload_type(self, payload):
        if not isinstance(payload, Mapping):
            raise ValidationError(self.ERROR_MESSAGE)
