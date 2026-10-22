"""共用的管理者身分、密碼雜湊及唯一動作權限表。"""

import hashlib
import hmac
import secrets
import threading
import time

from lib.application_errors import ForbiddenError, ValidationError

PASSWORD_ITERATIONS = 200_000
PASSIVE_ACTIONS = frozenset({"auth.status", "auth.logout", "stocktake.pending_count"})
ADMIN_ACTIONS = frozenset({
    "categories.create", "categories.update", "categories.delete", "categories.sort",
    "brands.create", "brands.update", "brands.delete", "brands.sort", "brands.set_categories",
    "phone_brands.create", "phone_brands.update", "phone_brands.delete", "phone_brands.sort",
    "models.create", "models.update", "models.delete", "models.sort",
    "fields.create", "fields.update",
    "options.create", "options.update", "options.delete", "options.set_models", "options.cleanup",
    "categories.set_common_fields", "categories.set_field", "categories.delete_field",
    "products.create", "products.update", "products.delete",
    "variants.create", "variants.update", "variants.set_models", "variants.update_details",
    "variants.update_editor", "variants.delete", "variants.batch_create", "variants.activate", "variants.issues",
    "sales.export", "sales.export_save", "stocktake.approve", "stocktake.pending_count",
    "desktop.child_window.open",
})


def hash_password(password, salt=None, iterations=PASSWORD_ITERATIONS):
    salt = secrets.token_bytes(16) if salt is None else salt
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password, stored):
    if not isinstance(password, str) or not isinstance(stored, str):
        return False
    try:
        algorithm, count, salt_hex, digest_hex = stored.split("$")
        iterations = int(count)
        salt, expected = bytes.fromhex(salt_hex), bytes.fromhex(digest_hex)
        if algorithm != "pbkdf2_sha256" or iterations <= 0 or len(salt) != 16 or len(expected) != 32:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, OverflowError, UnicodeError):
        return False


class AdminSession:
    IDLE_SECONDS = 600

    def __init__(self, db_path, clock=time.monotonic):
        self.db_path = db_path
        self._clock = clock
        self._admin = False
        self._last_activity = None
        self._lock = threading.RLock()

    def _password_hash(self):
        # 延後匯入，讓 db_seed 可直接使用本模組的純雜湊函式。
        from lib.db import db_conn
        with db_conn(self.db_path) as conn:
            row = conn.execute("SELECT value FROM Setting WHERE key='admin_password_hash'").fetchone()
            return row["value"] if row else None

    def is_admin(self):
        with self._lock:
            if self._admin and self._clock() - self._last_activity > self.IDLE_SECONDS:
                self.logout()
            return self._admin

    def login(self, password):
        with self._lock:
            if not verify_password(password, self._password_hash()):
                return False
            self._admin = True
            self._last_activity = self._clock()
            return True

    def logout(self):
        with self._lock:
            self._admin = False
            self._last_activity = None

    def touch(self):
        with self._lock:
            if self.is_admin():
                self._last_activity = self._clock()

    def require_admin(self):
        if not self.is_admin():
            raise ForbiddenError()

    def change_password(self, old, new):
        from lib.db import db_conn
        with self._lock:
            self.require_admin()
            if not isinstance(old, str) or not isinstance(new, str):
                raise ValidationError("密碼必須是字串")
            if not verify_password(old, self._password_hash()):
                raise ValidationError("舊密碼錯誤")
            new = new.strip()
            if len(new) < 4:
                raise ValidationError("新密碼至少 4 個字")
            if new == old:
                raise ValidationError("新密碼不可與舊密碼相同")
            with db_conn(self.db_path) as conn:
                conn.execute("UPDATE Setting SET value=? WHERE key='admin_password_hash'", (hash_password(new),))
                conn.commit()
            self.logout()
