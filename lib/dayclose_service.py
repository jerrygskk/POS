"""每日立帳:管理者輸入錢櫃現金總額,扣預備金後與該帳日現金銷售比對,立帳後鎖帳。

帳日以 00:00 為界;當天已立帳後才結的單算隔天的帳(由結帳的 INSERT 決定)。
立帳後該帳日的銷售單由資料庫 trigger 鎖住,所以現金銷售與預備金存當下的快照。
"""
from collections.abc import Mapping

from lib.application import BaseFacade
from lib.application_errors import ConflictError, ValidationError
from lib.product_rules import date_range, is_int, require_employee
from lib.sales_service import SalesRepository

REASON_MAX = 60
AMOUNT_MAX = 99_999_999
UNCLOSED_SHOWN = 7  # 「尚未立帳」提示最多列出最近幾個帳日


def today(conn):
    return conn.execute("SELECT date('now','localtime')").fetchone()[0]


def is_closed(conn, business_date):
    return conn.execute("SELECT 1 FROM DayClose WHERE business_date=?", (business_date,)).fetchone() is not None


def current_business_date(conn):
    """現在結帳會記到哪個帳日:今天,今天已立帳則明天。"""
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
        raise ValidationError("立帳資料格式不正確")
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
        if not isinstance(expected, Mapping) or set(expected) != {"float_amount", "cash_sales", "cash_refund"}:
            raise ValidationError("立帳資料格式不正確")
        return {"business_date": _business_date(payload),
                "counted_total": _amount(payload.get("counted_total"), "錢櫃現金（含預備金）"),
                "reason": reason, "employee": payload["employee"],
                "expected": {"float_amount": _amount(expected["float_amount"], "預備金"),
                             "cash_sales": _amount(expected["cash_sales"], "現金銷售"),
                             "cash_refund": _amount(expected["cash_refund"], "現金退款")}}
    return {}


class DayCloseService:
    def __init__(self, connection):
        self.conn = connection

    def _record(self, business_date):
        row = self.conn.execute("SELECT * FROM DayClose WHERE business_date=?", (business_date,)).fetchone()
        return dict(row) if row else None

    def _by_payment(self, business_date):
        """該帳日各付款方式的筆數與金額(不含作廢),依設定順序;從銷售單即時加總。

        啟用中的沒交易也列 0;停用的只在那天有交易時才列(停用前收的錢要對得上帳)。
        """
        found = {row["payment_id"]: (row["c"], row["t"]) for row in self.conn.execute(
            "SELECT payment_id, COUNT(*) c, COALESCE(SUM(total),0) t FROM Sale "
            "WHERE business_date=? AND voided_at IS NULL GROUP BY payment_id", (business_date,))}
        return [{**p, "count": found.get(p["payment_id"], (0, 0))[0], "total": found.get(p["payment_id"], (0, 0))[1]}
                for p in SalesRepository(self.conn).payments() if p["active"] or p["payment_id"] in found]

    @staticmethod
    def _cash_of(by_payment):
        return sum(b["total"] for b in by_payment if b["is_cash"])

    def current(self):
        return {"business_date": current_business_date(self.conn), "today": today(self.conn)}

    def _refunds(self, business_date):
        return self.conn.execute("SELECT COUNT(*),COALESCE(SUM(refund_amount),0) FROM ReturnSession WHERE business_date=? AND kind='normal' AND status='closed'", (business_date,)).fetchone()

    def _pending_returns(self):
        return self.conn.execute("SELECT COUNT(*) FROM ReturnSession WHERE kind='normal' AND status='pending'").fetchone()[0]

    def preview(self, business_date):
        # 同一次預覽的數字要出自同一個時間點:先開讀取交易
        self.conn.execute("BEGIN")
        record = self._record(business_date)
        by_payment = self._by_payment(business_date)
        void = self.conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(total),0) FROM Sale WHERE business_date=? AND voided_at IS NOT NULL",
            (business_date,)).fetchone()
        day = today(self.conn)
        refunds = self._refunds(business_date)
        unclosed = [row[0] for row in self.conn.execute(
            "SELECT business_date FROM (SELECT business_date FROM Sale UNION SELECT business_date FROM ReturnSession WHERE business_date IS NOT NULL) s WHERE business_date<? "
            "AND NOT EXISTS(SELECT 1 FROM DayClose d WHERE d.business_date=s.business_date) "
            "ORDER BY business_date DESC", (day,))]
        return {"business_date": business_date, "today": day, "closed": record is not None, "record": record,
                # 已立帳的帳日顯示當時的快照,不拿現在的設定值
                "float_amount": record["float_amount"] if record else cash_float(self.conn),
                "float_setting": cash_float(self.conn),
                "cash_sales": record["cash_sales"] if record else self._cash_of(by_payment),
                "cash_refund": record["cash_refund"] if record else refunds[1],
                "refund_count": refunds[0], "pending_returns": self._pending_returns(),
                "by_payment": [{"payment_id": b["payment_id"], "payment": b["name"], "count": b["count"],
                                "total": b["total"]} for b in by_payment],
                "void_count": void[0], "void_total": void[1],
                "unclosed": sorted(unclosed[:UNCLOSED_SHOWN]), "unclosed_count": len(unclosed)}

    def close(self, body):
        self.conn.execute("BEGIN IMMEDIATE")
        business_date = body["business_date"]
        if business_date > today(self.conn):
            raise ValidationError("不可替未來的帳日立帳")
        if is_closed(self.conn, business_date):
            raise ConflictError("此帳日已立帳")
        pending = self._pending_returns()
        if pending: raise ConflictError(f"尚有 {pending} 張退貨待核准，請先核准或作廢")
        # 現金銷售與快照出自同一份加總:兩邊不可能對不上
        by_payment = self._by_payment(business_date)
        float_amount, cash_sales = cash_float(self.conn), self._cash_of(by_payment)
        cash_refund = self._refunds(business_date)[1]
        if body["expected"] != {"float_amount": float_amount, "cash_sales": cash_sales, "cash_refund": cash_refund}:
            raise ConflictError("金額已變動，請重新確認")
        diff = body["counted_total"] - float_amount - cash_sales + cash_refund
        if diff and not body["reason"]:
            raise ValidationError("有短溢時請填寫原因")
        # 快照先寫、主檔最後寫:主檔一寫入,資料庫就不再讓該帳日加明細。只存當天有交易的
        self.conn.executemany(
            "INSERT INTO DayClosePayment(business_date,payment_id,name,count,total,fee_rate) VALUES(?,?,?,?,?,?)",
            [(business_date, b["payment_id"], b["name"], b["count"], b["total"], b["fee_rate"])
             for b in by_payment if b["count"]])
        self.conn.execute(
            "INSERT INTO DayClose(business_date,employee,float_amount,counted_total,cash_sales,cash_refund,diff,reason) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (business_date, body["employee"], float_amount, body["counted_total"], cash_sales, cash_refund,diff,
             body["reason"] or None))
        return self._record(business_date)

    def list(self, filters):
        where, args = "", []
        for key, operator in (("date_from", ">="), ("date_to", "<=")):
            if filters[key]:
                where += f" AND business_date{operator}?"
                args.append(filters[key])
        self.conn.execute("BEGIN")   # 立帳紀錄與各付款方式金額出自同一個時間點
        records = [dict(row) for row in self.conn.execute(
            f"SELECT * FROM DayClose WHERE 1=1{where} ORDER BY business_date DESC", args)]
        # 金額與抽成比例只讀立帳當下的快照:不回頭加總銷售單,也不讀現在的比例(改比例不可動到舊報表)
        found = {(row["business_date"], row["payment_id"]): (row["total"], row["fee_rate"]) for row in self.conn.execute(
            f"SELECT business_date, payment_id, total, fee_rate FROM DayClosePayment WHERE 1=1{where}", args)}
        # 每一列的欄位與順序相同:啟用中的付款方式,加上停用但這段期間有交易的(用過的刪不掉,快照裡的編號一定找得到)。
        # 欄名用現在的名稱,那天沒交易的補 0。fee_rate 是百分比×100,抽成由畫面逐日相乘加總後算一次
        seen = {payment_id for _, payment_id in found}
        payments = [p for p in SalesRepository(self.conn).payments() if p["active"] or p["payment_id"] in seen]
        for record in records:
            record["by_payment"] = [
                {"payment_id": p["payment_id"], "payment": p["name"],
                 "total": found.get((record["business_date"], p["payment_id"]), (0, 0))[0],
                 "fee_rate": found.get((record["business_date"], p["payment_id"]), (0, 0))[1]} for p in payments]
            record["sales_total"] = sum(b["total"] for b in record["by_payment"])
        return records

    def set_float(self, amount):
        self.conn.execute("INSERT OR REPLACE INTO Setting(key,value) VALUES('cash_float',?)", (str(amount),))
        return {"amount": amount}


class DayCloseFacade(BaseFacade):
    ACTIONS = {"dayclose.current", "dayclose.preview", "dayclose.close", "dayclose.list", "dayclose.set_float"}

    ERROR_MESSAGE = "立帳操作不正確"

    def _prepare_payload(self, action, payload):
        return _validate(action, payload)

    def _dispatch(self, action, payload, connection):
        service = DayCloseService(connection)
        if action == "dayclose.current": return service.current()
        if action == "dayclose.preview": return service.preview(payload["business_date"])
        if action == "dayclose.close": return service.close(payload)
        if action == "dayclose.list": return service.list(payload)
        return service.set_float(payload["amount"])
