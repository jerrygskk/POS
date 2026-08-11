"""共用商品規則：執行期欄位型別與自取條碼計數器。"""

from lib.application_errors import ValidationError


FIELD_TYPES = {"select", "text", "multi", "tags"}


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def allow_keys(payload, allowed, message="不支援的欄位：{field}"):
    unknown = set(payload) - set(allowed)
    if unknown:
        raise ValidationError(message.format(field=sorted(unknown)[0]))


def check_field_type(field_type):
    if field_type not in FIELD_TYPES:
        raise ValidationError("欄位類型不合法")


# ---- 店內自取碼 ----
# 格式：TL + 6 位序號 + 1 位檢查碼（共 9 字元），例：TL0000018。
# 檢查碼採 GS1／EAN 標準 mod 10，與廠商條碼同一套算法；TL 為固定前綴不參與運算。
STORE_BARCODE_PREFIX = "TL"
STORE_BARCODE_SERIAL_DIGITS = 6
STORE_BARCODE_MAX_SERIAL = 10 ** STORE_BARCODE_SERIAL_DIGITS - 1
_STORE_BARCODE_LENGTH = len(STORE_BARCODE_PREFIX) + STORE_BARCODE_SERIAL_DIGITS + 1


def mod10_check_digit(digits):
    """GS1／EAN 檢查碼：由右至左權重 3、1 交替加總，補足為 10 的倍數。"""
    total = sum(int(ch) * (3 if i % 2 == 0 else 1)
                for i, ch in enumerate(reversed(digits)))
    return (10 - total % 10) % 10


def format_store_barcode(serial):
    """序號（1 起）→ 自取碼字串；超出容量丟 ValidationError。"""
    if not is_int(serial) or not 1 <= serial <= STORE_BARCODE_MAX_SERIAL:
        raise ValidationError("自取碼號碼已用罄")
    body = f"{serial:0{STORE_BARCODE_SERIAL_DIGITS}d}"
    return f"{STORE_BARCODE_PREFIX}{body}{mod10_check_digit(body)}"


def parse_store_barcode(code):
    """自取碼字串 → 序號；格式或檢查碼不符回 None（大小寫、前後空白容錯）。"""
    if not isinstance(code, str):
        return None
    text = code.strip().upper()
    if len(text) != _STORE_BARCODE_LENGTH or not text.startswith(STORE_BARCODE_PREFIX):
        return None
    body = text[len(STORE_BARCODE_PREFIX):]
    if not body.isdigit():
        return None
    if mod10_check_digit(body[:-1]) != int(body[-1]):
        return None
    return int(body[:-1])


def has_store_barcode_prefix(code):
    """是否以自取碼保留字頭開頭（不論後續格式是否正確）。"""
    return isinstance(code, str) and code.strip().upper().startswith(STORE_BARCODE_PREFIX)


def next_store_barcode(conn):
    """以同一連線取用自取碼，更新計數器；交易由呼叫端決定是否提交。"""
    row = conn.execute(
        "SELECT value FROM Setting WHERE key='next_store_barcode'"
    ).fetchone()
    serial = int(row["value"]) if row else 1
    code = format_store_barcode(serial)
    conn.execute(
        "INSERT OR REPLACE INTO Setting(key,value) VALUES('next_store_barcode',?)",
        (str(serial + 1),))
    return code
