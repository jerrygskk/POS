import sqlite3
import tempfile
import unittest
import json
from contextlib import closing
from pathlib import Path

from lib.application import ApplicationFacade, Repository, TransactionRunner
from lib.application_errors import (
    ApplicationError,
    ConflictError,
    DatabaseError,
    InternalError,
    NotFoundError,
    ValidationError,
)
from lib.desktop_bridge import DesktopBridge


class RecordingLogger:
    def __init__(self):
        self.calls = []

    def exception(self, message):
        self.calls.append(message)


class FailingLogger:
    def exception(self, message):
        raise RuntimeError("記錄器秘密")


class ApplicationErrorTests(unittest.TestCase):
    def test_error_types_have_stable_codes_and_formal_messages(self):
        cases = [
            (ValidationError, "validation_error", "輸入資料不正確"),
            (NotFoundError, "not_found", "找不到指定資料"),
            (ConflictError, "conflict", "資料狀態衝突"),
            (DatabaseError, "database_error", "資料庫操作失敗"),
            (InternalError, "internal_error", "系統發生未預期錯誤"),
        ]

        for error_type, code, message in cases:
            with self.subTest(error_type=error_type.__name__):
                error = error_type()
                self.assertIsInstance(error, ApplicationError)
                self.assertEqual(code, error.code)
                self.assertEqual(message, error.message)
                self.assertIsNone(error.details)

    def test_error_accepts_message_and_details(self):
        error = ValidationError("請填寫名稱", {"field": "name"})

        self.assertEqual("請填寫名稱", str(error))
        self.assertEqual({"field": "name"}, error.details)


class TransactionLayerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "transaction.db"
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("CREATE TABLE Item(value TEXT NOT NULL)")
            conn.commit()
        self.runner = TransactionRunner(self.db_path)
        self.facade = ApplicationFacade(self.runner)

    def tearDown(self):
        self.tmp.cleanup()

    def test_facade_commits_repository_changes_after_success(self):
        def operation(repository):
            repository.execute("INSERT INTO Item(value) VALUES(?)", ("完成",))
            return repository.fetch_one("SELECT value FROM Item")["value"]

        result = self.facade.execute(operation)

        with closing(sqlite3.connect(self.db_path)) as conn:
            saved = conn.execute("SELECT value FROM Item").fetchone()[0]
        self.assertEqual("完成", result)
        self.assertEqual("完成", saved)

    def test_facade_rolls_back_and_preserves_original_exception(self):
        original = ConflictError("測試衝突")

        def operation(repository):
            repository.execute("INSERT INTO Item(value) VALUES(?)", ("不應保留",))
            raise original

        with self.assertRaises(ConflictError) as caught:
            self.facade.execute(operation)

        self.assertIs(original, caught.exception)
        with closing(sqlite3.connect(self.db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM Item").fetchone()[0]
        self.assertEqual(0, count)

    def test_repository_uses_injected_connection_without_committing(self):
        connection = sqlite3.connect(self.db_path)
        repository = Repository(connection)
        try:
            repository.execute("INSERT INTO Item(value) VALUES(?)", ("待回滾",))
            connection.rollback()
        finally:
            connection.close()

        with closing(sqlite3.connect(self.db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM Item").fetchone()[0]
        self.assertEqual(0, count)

    def test_rollback_failure_does_not_replace_original_business_error(self):
        def operation(repository):
            repository.connection.close()
            raise ValueError("原始業務錯誤")

        with self.assertRaisesRegex(ValueError, "原始業務錯誤"):
            self.facade.execute(operation)

    def test_rollback_logger_failure_does_not_replace_original_business_error(self):
        runner = TransactionRunner(self.db_path, logger=FailingLogger())
        facade = ApplicationFacade(runner)

        def operation(repository):
            repository.connection.close()
            raise ValueError("原始業務錯誤")

        with self.assertRaisesRegex(ValueError, "原始業務錯誤"):
            facade.execute(operation)

    def test_sqlite_database_error_maps_to_safe_application_error_with_cause(self):
        def operation(repository):
            repository.execute("SELECT * FROM MissingTable")

        with self.assertRaises(DatabaseError) as caught:
            self.facade.execute(operation)

        self.assertEqual("資料庫操作失敗", str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, sqlite3.DatabaseError)

    def test_business_value_error_is_not_mapped_to_database_error(self):
        with self.assertRaisesRegex(ValueError, "業務資料錯誤"):
            self.facade.execute(
                lambda repository: (_ for _ in ()).throw(ValueError("業務資料錯誤"))
            )


class DesktopBridgeTests(unittest.TestCase):
    def setUp(self):
        self.logger = RecordingLogger()
        self.bridge = DesktopBridge(logger=self.logger)

    def test_success_envelope(self):
        result = self.bridge._respond(lambda: {"id": 7})

        self.assertEqual({"ok": True, "data": {"id": 7}}, result)
        self.assertEqual([], self.logger.calls)

    def test_known_error_envelope_includes_optional_details(self):
        def fail():
            raise ValidationError("名稱不可空白", {"field": "name"})

        result = self.bridge._respond(fail)

        self.assertEqual(
            {
                "ok": False,
                "error": {
                    "code": "validation_error",
                    "message": "名稱不可空白",
                    "details": {"field": "name"},
                },
            },
            result,
        )
        self.assertEqual([], self.logger.calls)

    def test_known_error_without_details_omits_details_key(self):
        result = self.bridge._respond(lambda: (_ for _ in ()).throw(NotFoundError()))

        self.assertEqual(
            {
                "ok": False,
                "error": {"code": "not_found", "message": "找不到指定資料"},
            },
            result,
        )

    def test_unknown_error_is_logged_without_leaking_exception(self):
        result = self.bridge._respond(
            lambda: (_ for _ in ()).throw(RuntimeError("敏感技術內容"))
        )

        self.assertEqual(
            {
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": "系統發生未預期錯誤",
                },
            },
            result,
        )
        self.assertEqual(["DesktopBridge 執行失敗"], self.logger.calls)
        self.assertNotIn("敏感技術內容", repr(result))

    def test_logger_failure_does_not_replace_internal_error_envelope(self):
        bridge = DesktopBridge(logger=FailingLogger())

        result = bridge._respond(
            lambda: (_ for _ in ()).throw(RuntimeError("原始秘密"))
        )

        self.assertEqual(
            {
                "ok": False,
                "error": {
                    "code": "internal_error",
                    "message": "系統發生未預期錯誤",
                },
            },
            result,
        )
        self.assertNotIn("秘密", repr(result))

    def test_details_are_json_safe_without_leaking_unsafe_values(self):
        class SecretValue:
            def __repr__(self):
                return "repr秘密"

            def __str__(self):
                return "str秘密"

        details = {
            "valid": [None, True, 3, 1.5, "文字"],
            "exception": RuntimeError("例外秘密"),
            "object": SecretValue(),
            7: "非字串鍵秘密",
        }

        result = self.bridge._respond(
            lambda: (_ for _ in ()).throw(ValidationError(details=details))
        )
        serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)

        self.assertEqual([None, True, 3, 1.5, "文字"], result["error"]["details"]["valid"])
        self.assertEqual("[無法序列化]", result["error"]["details"]["exception"])
        self.assertEqual("[無法序列化]", result["error"]["details"]["object"])
        self.assertNotIn(7, result["error"]["details"])
        self.assertNotIn("秘密", serialized)

    def test_deep_details_are_truncated_and_remain_json_safe(self):
        details = "最深層"
        for _ in range(1100):
            details = [details]

        result = self.bridge._respond(
            lambda: (_ for _ in ()).throw(ValidationError(details=details))
        )
        serialized = json.dumps(result, ensure_ascii=False, allow_nan=False)

        self.assertFalse(result["ok"])
        self.assertIn("[無法序列化]", serialized)

    def test_database_error_uses_database_error_envelope_without_sql_message(self):
        error = DatabaseError()
        error.__cause__ = sqlite3.OperationalError("資料表秘密")

        result = self.bridge._respond(lambda: (_ for _ in ()).throw(error))

        self.assertEqual(
            {
                "ok": False,
                "error": {
                    "code": "database_error",
                    "message": "資料庫操作失敗",
                },
            },
            result,
        )
        self.assertNotIn("資料表秘密", repr(result))


if __name__ == "__main__":
    unittest.main()
