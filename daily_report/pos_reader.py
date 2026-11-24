"""以唯讀方式從 POS 資料庫取日報表要用的數字。"""
import sqlite3
from pathlib import Path

EMPLOYEES = 6


class PosReadError(Exception):
    pass


def read_month(db_path, year, month):
    """回傳 {日期字串: {"closed", "revenue", "employees"}}，只含已立帳的日子。

    整個月在同一個讀取交易內取完、立刻關閉：多次查詢才會是同一時間點的資料，
    也不會長時間佔住檔案讓 POS 存檔等待逾時。
    """
    path = Path(db_path)
    if not path.is_file():
        raise PosReadError(f"找不到 POS 資料庫：{path}")
    prefix = f"{year:04d}-{month:02d}-"
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=3)
    except sqlite3.Error as exc:
        raise PosReadError(f"無法開啟 POS 資料庫：{exc}") from None
    try:
        conn.execute("BEGIN")
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"DayClose", "DayClosePayment", "Sale"} <= tables:
            raise PosReadError("選取的檔案不是 POS 資料庫")
        days = {}
        for date, refund in conn.execute(
                "SELECT business_date, cash_refund FROM DayClose WHERE business_date LIKE ?", (prefix + "%",)):
            days[date] = {"closed": True, "revenue": -refund,
                          "employees": [0] * EMPLOYEES}
        for date, total in conn.execute(
                "SELECT business_date, SUM(total) FROM DayClosePayment WHERE business_date LIKE ? "
                "GROUP BY business_date", (prefix + "%",)):
            if date in days:
                days[date]["revenue"] += total
        for date, employee, total in conn.execute(
                "SELECT business_date, employee, SUM(total) FROM Sale "
                "WHERE business_date LIKE ? AND voided_at IS NULL AND employee IS NOT NULL "
                "GROUP BY business_date, employee",
                (prefix + "%",)):
            if date in days:
                days[date]["employees"][employee - 1] = total
        conn.commit()
        return days
    except sqlite3.Error as exc:
        raise PosReadError(f"讀取 POS 資料庫失敗：{exc}") from None
    finally:
        conn.close()
