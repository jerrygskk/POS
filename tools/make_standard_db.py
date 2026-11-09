# -*- coding: utf-8 -*-
"""
產生標準資料庫（母版）—— 手機配件店 POS 系統
======================================================================
以唯讀方式讀取 v24 資料庫，只把「商品與設定」轉成最新格式，寫到一個全新檔案；
營業紀錄（銷售、進出貨、盤點、退貨）一律不帶，所以庫存全部為 0。

v24 的規格欄經 CategoryField 掛種類；新格式規格欄直接隸屬種類，轉換時把
CategoryField 的種類、排序、必填、預設選項併入 AttributeField，啟用取兩者皆啟用。

用法（從專案根目錄執行）：
    python tools/make_standard_db.py --source docs/standard_db/pos_standard.db --output docs/standard_db/pos_standard_v27_new.db

來源只以唯讀連線開啟；輸出檔已存在就停止，不覆寫任何既有檔案。
任何檢查不過就中止、刪掉未完成的輸出檔，來源檔內容不變。
"""
import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import db_schema, db_seed, product_data  # noqa: E402
from lib.application_errors import ValidationError  # noqa: E402
from lib.db import init_db  # noqa: E402
from lib.normalize import normalize_key  # noqa: E402

SOURCE_VERSION = 24  # 本工具唯一接受的來源版本

# 每張資料表都必須歸類；新增資料表時沒歸類，工具會直接停下，避免交付的資料庫悄悄少掉一張表。
COPY_TABLES = [  # 欄位與來源相同，原樣複製（保留主鍵）
    "Category", "AttributeOption", "OptionModel",
    "Brand", "BrandCategory", "PhoneBrand", "DeviceType", "PhoneModel",
    "Product", "Variant", "VariantAttribute", "VariantModel", "Barcode",
    "VariantIssue", "Member", "MemberPhone",
]
CONVERT_TABLES = ["AttributeField"]  # 由來源 AttributeField＋CategoryField 轉換
SOURCE_ONLY_TABLES = ["CategoryField"]  # 只存在來源，已併入 AttributeField
SKIP_TABLES = ["Sale", "SaleItem", "StockMovement", "StocktakeSession", "StocktakeItem",
               "ReturnSession", "ReturnItem", "Receipt", "ReceiptItem"]
# Setting 逐鍵處理：schema_version 由本工具寫入最新版號，密碼依種子預設（不複製來源密碼）。
SETTING_COPY_KEYS = ["payments", "next_store_barcode"]
SETTING_SKIP_KEYS = ["schema_version", "admin_password_hash"]
INSERT_ORDER = ["Category", "AttributeField", "AttributeOption", "OptionModel", "Brand",
                "BrandCategory", "PhoneBrand", "DeviceType", "PhoneModel", "Product", "Variant",
                "VariantAttribute", "VariantModel", "Barcode", "VariantIssue", "Member", "MemberPhone"]
FIELD_COLUMNS = "field_id,category_id,name,field_type,sort,required,default_option_id,active"
CONVERTED_FIELDS_SQL = (
    "SELECT f.field_id, cf.category_id, f.name, f.field_type, cf.sort, cf.required, "
    "cf.default_option_id, (f.active AND cf.active) "
    "FROM src.AttributeField f JOIN src.CategoryField cf ON cf.field_id=f.field_id")


class ConversionError(RuntimeError):
    """轉換中止：問題清單寫在訊息裡，輸出檔已刪除。"""


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tables(conn, schema="main"):
    return {row[0] for row in conn.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def columns(conn, table, schema="main"):
    return [row[1] for row in conn.execute(f"PRAGMA {schema}.table_info([{table}])")]


def check_classification(conn):
    known_new = set(COPY_TABLES) | set(CONVERT_TABLES) | set(SKIP_TABLES) | {"Setting"}
    unknown = sorted(tables(conn) - known_new)
    if unknown:
        raise ConversionError(f"新格式有資料表尚未歸類（請在工具中決定怎麼處理）：{', '.join(unknown)}")
    known_src = known_new | set(SOURCE_ONLY_TABLES)
    unknown = sorted(tables(conn, "src") - known_src)
    if unknown:
        raise ConversionError(f"來源有資料表尚未歸類：{', '.join(unknown)}")
    missing = sorted((set(COPY_TABLES) | set(CONVERT_TABLES) | set(SOURCE_ONLY_TABLES)) - tables(conn, "src"))
    if missing:
        raise ConversionError(f"來源缺少資料表：{', '.join(missing)}")
    for table in COPY_TABLES:
        if columns(conn, table) != columns(conn, table, "src"):
            raise ConversionError(f"資料表 {table} 的欄位與來源不一致")
    keys = {row[0] for row in conn.execute("SELECT key FROM src.Setting")}
    unknown = sorted(keys - set(SETTING_COPY_KEYS) - set(SETTING_SKIP_KEYS))
    if unknown:
        raise ConversionError(f"來源有尚未歸類的設定鍵：{', '.join(unknown)}")


def find_problems(conn):
    """停止條件：轉換後無法成立的主檔狀況。只列出，不自行刪欄或合併值。"""
    problems = []
    for row in conn.execute(
            "SELECT f.field_id, f.name, COUNT(cf.category_id) n FROM src.AttributeField f "
            "LEFT JOIN src.CategoryField cf ON cf.field_id=f.field_id GROUP BY f.field_id HAVING n<>1"):
        problems.append(f"規格欄 {row[0]}「{row[1]}」掛了 {row[2]} 個種類（須恰好一個）")
    seen = {}
    for row in conn.execute(
            "SELECT cf.category_id, f.field_id, f.name FROM src.AttributeField f "
            "JOIN src.CategoryField cf ON cf.field_id=f.field_id ORDER BY f.field_id"):
        key = (row[0], normalize_key(row[2]))
        if key in seen:
            problems.append(f"種類 {row[0]} 的規格欄 {seen[key]} 與 {row[1]}「{row[2]}」同名")
        else:
            seen[key] = row[1]
    for row in conn.execute(
            "SELECT va.variant_id, f.name FROM src.VariantAttribute va "
            "JOIN src.Variant v ON v.variant_id=va.variant_id JOIN src.Product p ON p.product_id=v.product_id "
            "LEFT JOIN src.CategoryField cf ON cf.field_id=va.field_id AND cf.category_id=p.category_id "
            "JOIN src.AttributeField f ON f.field_id=va.field_id WHERE cf.field_id IS NULL"):
        problems.append(f"子產品 {row[0]} 的規格「{row[1]}」不屬於該商品的種類")
    return problems


def copy_sequences(conn, names):
    for name, seq in conn.execute(
            f"SELECT name, seq FROM src.sqlite_sequence WHERE name IN ({','.join('?' * len(names))})", names).fetchall():
        conn.execute("DELETE FROM main.sqlite_sequence WHERE name=?", (name,))
        conn.execute("INSERT INTO main.sqlite_sequence(name,seq) VALUES(?,?)", (name, seq))


def same_rows(conn, left, right):
    """兩個查詢的資料列（含重複筆數）完全相同。"""
    a = sorted(conn.execute(left).fetchall(), key=repr)
    b = sorted(conn.execute(right).fetchall(), key=repr)
    return a == b


def reconcile(conn):
    """實際轉出後逐項比對；回傳差異清單（空清單＝一致）。"""
    diffs = []
    for table in COPY_TABLES:
        cols = ",".join(f"[{c}]" for c in columns(conn, table))
        if not same_rows(conn, f"SELECT {cols} FROM main.[{table}]", f"SELECT {cols} FROM src.[{table}]"):
            diffs.append(f"{table} 的資料與來源不一致")
    if not same_rows(conn, f"SELECT {FIELD_COLUMNS} FROM main.AttributeField", CONVERTED_FIELDS_SQL):
        diffs.append("AttributeField 的轉換結果與來源（含排序／必填／預設／啟用）不一致")
    for table in SKIP_TABLES:
        if conn.execute(f"SELECT COUNT(*) FROM main.[{table}]").fetchone()[0]:
            diffs.append(f"營業紀錄 {table} 不應有資料")
    for key in SETTING_COPY_KEYS:
        a = conn.execute("SELECT value FROM main.Setting WHERE key=?", (key,)).fetchone()
        b = conn.execute("SELECT value FROM src.Setting WHERE key=?", (key,)).fetchone()
        if a != b:
            diffs.append(f"設定 {key} 與來源不一致")
    names = ",".join(f"'{n}'" for n in COPY_TABLES + CONVERT_TABLES)
    if not same_rows(conn, f"SELECT name,seq FROM main.sqlite_sequence WHERE name IN ({names})",
                     f"SELECT name,seq FROM src.sqlite_sequence WHERE name IN ({names})"):
        diffs.append("流水號（sqlite_sequence）與來源不一致")
    version = conn.execute("SELECT value FROM main.Setting WHERE key='schema_version'").fetchone()
    if version is None or int(version[0]) != db_schema.SCHEMA_VERSION:
        diffs.append("輸出檔版號不是最新版")
    return diffs


def _convert(conn):
    conn.execute("BEGIN")
    conn.execute("PRAGMA defer_foreign_keys=ON")  # 規格欄與預設選項互相引用：commit 前才整體檢查，不關外鍵
    for table in INSERT_ORDER:
        if table == "AttributeField":
            conn.execute(f"INSERT INTO main.AttributeField({FIELD_COLUMNS}) {CONVERTED_FIELDS_SQL}")
            continue
        cols = ",".join(f"[{c}]" for c in columns(conn, table))
        conn.execute(f"INSERT INTO main.[{table}]({cols}) SELECT {cols} FROM src.[{table}]")
    copy_sequences(conn, COPY_TABLES + CONVERT_TABLES)
    for key in SETTING_COPY_KEYS:
        conn.execute("INSERT OR REPLACE INTO main.Setting(key,value) "
                     "SELECT key,value FROM src.Setting WHERE key=?", (key,))
    conn.execute("INSERT OR REPLACE INTO main.Setting(key,value) VALUES('schema_version',?)",
                 (str(db_schema.SCHEMA_VERSION),))
    db_seed.seed(conn)  # 補預設密碼等種子（已複製的不重複）
    violations = conn.execute("PRAGMA main.foreign_key_check").fetchall()
    if violations:
        raise ConversionError(f"外鍵檢查失敗：{', '.join(sorted({row[0] for row in violations}))}")
    # 資料庫只擋單筆;單選只能一個值、欄型別與值型別對應等跨表規則用正式檢查再核一次
    variant_ids = [r[0] for r in conn.execute("SELECT variant_id FROM main.Variant")]
    try:
        product_data.check_variant_attributes(conn, variant_ids)
    except ValidationError as exc:
        raise ConversionError(f"規格值檢查失敗：{exc}") from exc
    conn.execute("COMMIT")


def build(source, output):
    """轉出新母版；回傳 (各表筆數, 設定)。失敗拋 ConversionError 並刪掉輸出檔。"""
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_file():
        raise ConversionError(f"找不到來源資料庫：{source}")
    if source == output:
        raise ConversionError("輸出檔不可與來源相同。")
    if output.exists():
        raise ConversionError(f"輸出檔已存在，不覆寫：{output}")
    before = file_hash(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(output.as_uri(), uri=True, isolation_level=None)  # URI 模式，ATTACH 才能用 mode=ro
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(db_schema.SCHEMA)
        conn.execute("ATTACH DATABASE ? AS src", (f"{source.as_uri()}?mode=ro",))
        version = conn.execute("SELECT value FROM src.Setting WHERE key='schema_version'").fetchone()
        if version is None or int(version[0]) != SOURCE_VERSION:
            raise ConversionError(
                f"來源版本 {version[0] if version else '未知'} 不是本工具支援的 v{SOURCE_VERSION}")
        check_classification(conn)
        problems = find_problems(conn)
        if problems:
            raise ConversionError("來源有無法轉換的資料：\n  " + "\n  ".join(problems))
        try:
            _convert(conn)
        except sqlite3.DatabaseError as exc:
            raise ConversionError(f"寫入新格式失敗：{exc}") from exc
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
        diffs = reconcile(conn)
        if diffs:
            raise ConversionError("對帳不一致：\n  " + "\n  ".join(diffs))
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM main.[{t}]").fetchone()[0]
                  for t in INSERT_ORDER}
        settings = dict(conn.execute("SELECT key,value FROM main.Setting"))
        conn.execute("DETACH DATABASE src")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ConversionError("資料庫完整性檢查失敗")
        conn.execute("VACUUM")
        conn.close()
        init_db(output, require_existing=True)  # 經正式流程再開一次，確認可被程式直接使用
        if file_hash(source) != before:  # 放在清理範圍內:來源被改就不留輸出檔
            raise ConversionError("來源檔內容在轉換期間被改變")
    except Exception:
        conn.close()
        for path in (output, output.with_name(output.name + "-journal")):
            path.unlink(missing_ok=True)  # 半成品不留
        raise
    settings.pop("admin_password_hash", None)
    return counts, settings


def main():
    parser = argparse.ArgumentParser(description="由 v24 資料庫轉出只含商品與設定的新格式母版")
    parser.add_argument("--source", type=Path, required=True, help="v24 來源資料庫（唯讀）")
    parser.add_argument("--output", type=Path, required=True, help="輸出位置（不可已存在）")
    args = parser.parse_args()
    try:
        counts, settings = build(args.source, args.output)
    except ConversionError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        sys.exit(1)
    print(f"已產生：{args.output.resolve()}")
    for table, count in counts.items():
        print(f"  {table:<18}{count:>6} 筆")
    print(f"  店內條碼下一號：{settings.get('next_store_barcode', '（無）')}")
    print(f"  格式版本：{settings['schema_version']}；營業紀錄皆為 0，庫存全部為 0；對帳一致，來源未變動。")


if __name__ == "__main__":
    main()
