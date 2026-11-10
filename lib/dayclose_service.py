"""每日點帳:管理者輸入錢櫃現金總額,扣預備金後與該帳日現金銷售比對,點完鎖帳。

帳日以 00:00 為界;當天已點帳後才結的單算隔天的帳(由結帳的 INSERT 決定)。
點帳後該帳日的銷售單由資料庫 trigger 鎖住,所以現金銷售與預備金存當下的快照。
"""
from collections.abc import Mapping

from lib.application import BaseFacade
from lib.application_errors import ConflictError, ValidationError
from lib.product_rules import date_range, is_int, require_employee
from lib.sales_service import CASH_PAYMENT, SalesRepository

REASON_MAX = 60
AMOUNT_MAX = 99_999_999
UNCLOSED_SHOWN = 7  # 「尚未點帳」提示最多列出最近幾個帳日


def today(conn):
    return conn.execute("SELECT date('now','localtime')").fetchone()[0]


def is_closed(conn, business_date):
    return conn.execute("SELECT 1 FROM DayClose WHERE business_date=?", (business_date,)).fetchone() is not None


def current_business_date(conn):
    """現在結帳會記到哪個帳日:今天,今天已點帳則明天。"""
    return conn.execute(
        "SELECT CASE WHEN EXISTS(SELECT 1 FROM DayClose WHERE business_date=date('now','localtime')) "
        "THEN date('now','localtime','+1 day') ELSE date('now','localtime') END").fetchone()[0]


def cash_float(conn):
    row = conn.execute("SELECT value FROM Setting WHERE key='cash_float'").fetchone()
    try:
        return max(int(row[0]), 0) if row else 0
    except (TypeError, ValueError):
        return 0


def _amount(value, label):
    if not is_int(value) or value < 0 or value > AMOUNT_MAX:
        raise ValidationError(f"{label}須為 0 以上的整數")
    return value


def _business_date(payload):
    value = payload.get("business_date")
    if not isinstance(value, str) or not value:
        raise ValidationError("請選擇帳日")
    return date_range({"date_from": value})["date_from"]


def _validate(action, payload):
    allowed = {"dayclose.current": set(), "dayclose.preview": {"business_date"},
               "dayclose.close": {"business_date", "counted_total", "reason", "employee", "expected"},
               "dayclose.list": {"date_from", "date_to"}, "dayclose.set_float": {"amount"}}[action]
    if set(payload) - allowed:
        raise ValidationError("點帳資料格式不正確")
    if action == "dayclose.preview":
        return {"business_date": _business_date(payload)}
    if action == "dayclose.list":
        values = date_range(payload)
        if values["date_from"] and values["date_to"] and values["date_from"] > values["date_to"]:
            raise ValidationError("起日不可晚於迄日")
        return values
    if action == "dayclose.set_float":
        return {"amount": _amount(payload.get("amount"), "預備金")}
    if action == "dayclose.close":
        require_employee(payload.get("employee"))
        reason = payload.get("reason")
        if not isinstance(reason, str):
            raise ValidationError("短溢原因格式不正確")
        reason = reason.strip()
        if len(reason) > REASON_MAX:
            raise ValidationError(f"短溢原因最多 {REASON_MAX} 字")
        expected = payload.get("expected")
        if not isinstance(expected, Mapping) or set(expected) != {"float_amount", "cash_sales"}:
            raise ValidationError("點帳資料格式不正確")
        return {"business_date": _business_date(payload),
                "counted_total": _amount(payload.get("counted_total"), "錢櫃現金總額"),
                "reason": reason, "employee": payload["employee"],
                "expected": {"float_amount": _amount(expected["float_amount"], "預備金"),
                             "cash_sales": _amount(expected["cash_sales"], "現金銷售")}}
    return {}


class DayCloseService:
    def __init__(self, connection):
        self.conn = connection

    def _record(self, business_date):
        row = self.conn.execute("SELECT * FROM DayClose WHERE business_date=?", (business_date,)).fetchone()
        return dict(row) if row else None

    def _cash_sales(self, business_date):
        return self.conn.execute(
            "SELECT COALESCE(SUM(total),0) FROM Sale WHERE business_date=? AND payment=? AND voided_at IS NULL",
            (business_date, CASH_PAYMENT)).fetchone()[0]

    def current(self):
        return {"business_date": current_business_date(self.conn), "today": today(self.conn)}

    def preview(self, business_date):
        # 同一次預覽的數字要出自同一個時間點:先開讀取交易
        self.conn.execute("BEGIN")
        record = self._record(business_date)
        rows = self.conn.execute(
            "SELECT payment, COUNT(*) c, COALESCE(SUM(total),0) t FROM Sale "
            "WHERE business_date=? AND voided_at IS NULL GROUP BY payment", (business_date,)).fetchall()
        found = {row["payment"]: (row["c"], row["t"]) for row in rows}
        payments = SalesRepository(self.conn).payments()
        names = payments + [p for p in found if p not in payments]
        void = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(total),0) FROM Sale WHERE business_date=? AND voided_at IS NOT NULL",
            (business_date,)).fetchone()
        day = today(self.conn)
        unclosed = [row[0] for row in self.conn.execute(
            "SELECT DISTINCT business_date FROM Sale s WHERE business_date<? "
            "AND NOT EXISTS(SELECT 1 FROM DayClose d WHERE d.business_date=s.business_date) "
            "ORDER BY business_date DESC", (day,))]
        return {"business_date": business_date, "today": day, "closed": record is not None, "record": record,
                # 已點帳的帳日顯示當時的快照,不拿現在的設定值
                "float_amount": record["float_amount"] if record else cash_float(self.conn),
                "float_setting": cash_float(self.conn),
                "cash_sales": record["cash_sales"] if record else self._cash_sales(business_date),
                "by_payment": [{"payment": p, "count": found.get(p, (0, 0))[0], "total": found.get(p, (0, 0))[1]}
                               for p in names],
                "void_count": void[0], "void_total": void[1],
                "unclosed": sorted(unclosed[:UNCLOSED_SHOWN]), "unclosed_count": len(unclosed)}

    def close(self, body):
        self.conn.execute("BEGIN IMMEDIATE")
        business_date = body["business_date"]
        if business_date > today(self.conn):
            raise ValidationError("不可點未來的帳日")
        if is_closed(self.conn, business_date):
            raise ConflictError("此帳日已點帳")
        float_amount, cash_sales = cash_float(self.conn), self._cash_sales(business_date)
        if body["expected"] != {"float_amount": float_amount, "cash_sales": cash_sales}:
            raise ConflictError("金額已變動，請重新確認")
        diff = body["counted_total"] - float_amount - cash_sales
        if diff and not body["reason"]:
            raise ValidationError("有短溢時請填寫原因")
        self.conn.execute(
            "INSERT INTO DayClose(business_date,employee,float_amount,counted_total,cash_sales,diff,reason) "
            "VALUES(?,?,?,?,?,?,?)",
            (business_date, body["employee"], float_amount, body["counted_total"], cash_sales, diff,
             body["reason"] or None))
        return self._record(business_date)

    def list(self, filters):
        sql, args = "SELECT * FROM DayClose WHERE 1=1", []
        for key, operator in (("date_from", ">="), ("date_to", "<=")):
            if filters[key]:
                sql += f" AND business_date{operator}?"
                args.append(filters[key])
        return [dict(row) for row in self.conn.execute(sql + " ORDER BY business_date DESC", args)]

    def set_float(self, amount):
        self.conn.execute("INSERT OR REPLACE INTO Setting(key,value) VALUES('cash_float',?)", (str(amount),))
        return {"amount": amount}


class DayCloseFacade(BaseFacade):
    ACTIONS = {"dayclose.current", "dayclose.preview", "dayclose.close", "dayclose.list", "dayclose.set_float"}

    ERROR_MESSAGE = "點帳操作不正確"

    def _prepare_payload(self, action, payload):
        return _validate(action, payload)

    def _dispatch(self, action, payload, connection):
        service = DayCloseService(connection)
        if action == "dayclose.current": return service.current()
        if action == "dayclose.preview": return service.preview(payload["business_date"])
        if action == "dayclose.close": return service.close(payload)
        if action == "dayclose.list": return service.list(payload)
        return service.set_float(payload["amount"])
