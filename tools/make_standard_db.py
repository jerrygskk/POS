# -*- coding: utf-8 -*-
"""
產生標準資料庫 —— 手機配件店 POS 系統
======================================================================
以最新格式建一個全新資料庫，從來源只複製「商品與設定」，營業紀錄（銷售、
進出貨、盤點）一律不帶，所以庫存全部為 0。

用法（從專案根目錄執行）：
    python tools/make_standard_db.py                       # 來源＝母版
    python tools/make_standard_db.py --source pos.db --output docs/standard_db/pos_standard.db

母版 docs/standard_db/pos_standard.db 不入庫，只存在本機；遺失時以第二行重新產生。
要把 pos.db 清乾淨：關掉 POS → 執行第一行 → 自行把產出的 pos_standard.db 換成 pos.db。
工具不覆蓋任何既有檔案。
"""
import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import db_schema  # noqa: E402
from lib.db import init_db  # noqa: E402

DEFAULT_SOURCE = ROOT / "docs" / "standard_db" / "pos_standard.db"
DEFAULT_OUTPUT = ROOT / "pos_standard.db"

# 每張資料表都必須歸在其中一份清單；新增資料表時沒歸類，工具會直接停下，
# 避免交付的資料庫悄悄少掉一張表。
COPY_TABLES = [
    "Category", "AttributeField", "CategoryField", "AttributeOption", "OptionModel",
    "Brand", "BrandCategory", "PhoneBrand", "PhoneModel",
    "Product", "Variant", "VariantAttribute", "VariantModel", "Barcode",
    "VariantIssue",
]
SKIP_TABLES = ["Sale", "SaleItem", "StockMovement", "StocktakeSession", "StocktakeItem"]
# Setting 逐鍵處理：schema_version 由建庫流程寫入，其餘鍵照抄。
SETTING_COPY_KEYS = ["payments", "next_store_barcode"]
SETTING_SKIP_KEYS = ["schema_version"]


def fail(message):
    print(f"錯誤：{message}", file=sys.stderr)
    sys.exit(1)


def tables(conn, schema="main"):
    return {row[0] for row in conn.execute(
        f"SELECT name FROM {schema}.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def columns(conn, table, schema="main"):
    return [row[1] for row in conn.execute(f"PRAGMA {schema}.table_info([{table}])")]


def build(source, output):
    source, output = source.resolve(), output.resolve()
    if not source.is_file():
        fail(f"找不到來源資料庫：{source}")
    if source == output:
        fail("輸出檔不可與來源相同。")
    if output.exists():
        fail(f"輸出檔已存在，請先移走：{output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    init_db(output)  # 以最新格式建空資料庫（含付款方式種子與 schema_version）
    conn = sqlite3.connect(output.as_uri(), uri=True)  # 開 URI 模式，ATTACH 才能用 mode=ro
    try:
        conn.execute("PRAGMA foreign_keys=OFF")  # 複製順序不受外鍵限制，完成後再整體檢查
        conn.execute("ATTACH DATABASE ? AS src", (f"{source.as_uri()}?mode=ro",))

        known = set(COPY_TABLES) | set(SKIP_TABLES) | {"Setting"}
        unknown = sorted(tables(conn) - known)
        if unknown:
            raise RuntimeError(f"有資料表尚未歸類（請在工具中決定是否複製）：{', '.join(unknown)}")

        version = conn.execute("SELECT value FROM src.Setting WHERE key='schema_version'").fetchone()
        if version is None or int(version[0]) != db_schema.SCHEMA_VERSION:
            raise RuntimeError(
                f"來源格式版本 {version[0] if version else '未知'} 不是最新版 {db_schema.SCHEMA_VERSION}，"
                "請先用目前版本的 POS 開啟來源一次讓它升級。")
        missing = sorted(set(COPY_TABLES) - tables(conn, "src"))
        if missing:
            raise RuntimeError(f"來源缺少資料表：{', '.join(missing)}")

        source_keys = {row[0] for row in conn.execute("SELECT key FROM src.Setting")}
        unknown_keys = sorted(source_keys - set(SETTING_COPY_KEYS) - set(SETTING_SKIP_KEYS))
        if unknown_keys:
            raise RuntimeError(f"來源有尚未歸類的設定鍵：{', '.join(unknown_keys)}")

        counts = {}
        for table in COPY_TABLES:
            cols = columns(conn, table)
            if cols != columns(conn, table, "src"):
                raise RuntimeError(f"資料表 {table} 的欄位與來源不一致")
            col_sql = ",".join(f"[{c}]" for c in cols)
            conn.execute(f"DELETE FROM main.[{table}]")
            conn.execute(f"INSERT INTO main.[{table}]({col_sql}) SELECT {col_sql} FROM src.[{table}]")
            copied = conn.execute(f"SELECT COUNT(*) FROM main.[{table}]").fetchone()[0]
            expected = conn.execute(f"SELECT COUNT(*) FROM src.[{table}]").fetchone()[0]
            if copied != expected:
                raise RuntimeError(f"資料表 {table} 筆數不符：來源 {expected}、複製 {copied}")
            counts[table] = copied
        for key in SETTING_COPY_KEYS:
            conn.execute("INSERT OR REPLACE INTO main.Setting(key,value) "
                         "SELECT key,value FROM src.Setting WHERE key=?", (key,))
        for table in SKIP_TABLES:
            if conn.execute(f"SELECT COUNT(*) FROM main.[{table}]").fetchone()[0]:
                raise RuntimeError(f"營業紀錄 {table} 不應有資料")
        conn.commit()
        conn.execute("DETACH DATABASE src")

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"外鍵檢查失敗：{', '.join(sorted({row[0] for row in violations}))}")
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("資料庫完整性檢查失敗")
        conn.execute("VACUUM")
    except Exception as exc:
        conn.close()
        output.unlink(missing_ok=True)  # 半成品不留
        fail(str(exc))
    settings = dict(conn.execute("SELECT key,value FROM Setting"))
    conn.close()
    # 經正式流程再開一次，確認可被程式直接使用
    init_db(output, require_existing=True)
    return counts, settings


def main():
    parser = argparse.ArgumentParser(description="產生只含商品與設定的標準資料庫")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="來源資料庫（預設：母版）")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="輸出位置（不可已存在）")
    args = parser.parse_args()
    counts, settings = build(args.source, args.output)
    print(f"已產生：{args.output.resolve()}")
    for table, count in counts.items():
        print(f"  {table:<18}{count:>6} 筆")
    print(f"  店內條碼下一號：{settings.get('next_store_barcode', '（無）')}")
    print(f"  格式版本：{settings['schema_version']}；營業紀錄皆為 0，庫存全部為 0。")


if __name__ == "__main__":
    main()
