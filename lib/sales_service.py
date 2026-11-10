import csv
import io
import json
from datetime import date as calendar_date
from collections.abc import Mapping

from lib import correction_service, product_data, stock_ledger
from lib.application import BaseFacade, BaseRepository
from lib.application_errors import ConflictError, NotFoundError, ValidationError
from lib.db import stock_map
from lib.member_service import phone_pattern
from lib.product_rules import is_int as _is_int, require_employee


CASH_PAYMENT = "現金"  # 只有現金找零、計入點帳的應有現金
# 作廢原因:代碼→存進資料庫的顯示文字;「其他」須另填說明
VOID_REASONS = {"duplicate": "重複結帳", "item": "品項錯誤", "qty": "數量錯誤", "amount": "金額錯誤", "other": "其他"}
VOID_TEXT_MAX = 30


def _strict_mapping(payload, allowed):
    if not isinstance(payload, Mapping) or set(payload) - set(allowed):
        raise ValidationError("銷售資料格式不正確")


def _filters(payload, legacy_date=False):
    _strict_mapping(payload, {"date_from", "date_to", "payment", "member_phone", "date"} if legacy_date
                    else {"date_from", "date_to", "payment", "member_phone"})
    values = {key: payload.get(key, "") for key in ("date_from", "date_to", "payment", "member_phone")}
    if any(not isinstance(value, str) for value in values.values()):
        raise ValidationError("篩選條件格式不正確")
    date = payload.get("date", "") if legacy_date else ""
    if not isinstance(date, str):
        raise ValidationError("日期格式不正確")
    for value in (values["date_from"], values["date_to"], date):
        if not value:
            continue
        try:
            parsed = calendar_date.fromisoformat(value)
        except ValueError:
            raise ValidationError("日期格式不正確") from None
        if len(value) != 10 or parsed.isoformat() != value:
            raise ValidationError("日期格式不正確")
    # 會員電話:空白＝不篩;否則同收銀查詢,末三碼或完整 10 碼
    values["member_phone"] = phone_pattern(values["member_phone"]) if values["member_phone"].strip() else ""
    if values["date_from"] and values["date_to"] and values["date_from"] > values["date_to"]:
        raise ValidationError("起日不可晚於迄日")
    if date and not values["date_from"] and not values["date_to"]:
        values["date_from"] = values["date_to"] = date
    return values


def _checkout_payload(payload):
    _strict_mapping(payload, {"payment", "order_discount", "member_discount", "paid", "items", "employee", "member_id"})
    member_id = payload.get("member_id")
    if member_id is not None and not _is_int(member_id):
        raise ValidationError("會員資料格式不正確")
    require_employee(payload.get("employee"))
    if not isinstance(payload.get("payment"), str) or not payload["payment"]:
        raise ValidationError("付款方式不正確")
    for key in ("order_discount", "member_discount", "paid"):
        if not _is_int(payload.get(key, 0)) or payload.get(key, 0) < 0:
            raise ValidationError("金額格式不正確")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValidationError("銷售明細不可空白")
    clean = []
    for item in items:
        _strict_mapping(item, {"variant_id", "qty", "unit_price", "discount"})
        row = {"variant_id": item.get("variant_id"), "qty": item.get("qty"),
               "unit_price": item.get("unit_price"), "discount": item.get("discount", 0)}
        if not _is_int(row["variant_id"]) or not _is_int(row["qty"]) or row["qty"] <= 0:
            raise ValidationError("商品或數量格式不正確")
        if not _is_int(row["unit_price"]) or row["unit_price"] < 0:
            raise ValidationError("原價格式不正確")
        if not _is_int(row["discount"]) or row["discount"] < 0:
            raise ValidationError("折扣格式不正確")
        if row["discount"] > row["qty"] * row["unit_price"]:
            raise ValidationError("單項折扣不可超過該項金額")
        clean.append(row)
    return {"payment": payload["payment"], "order_discount": payload.get("order_discount", 0),
            "member_discount": payload.get("member_discount", 0),
            "paid": payload.get("paid", 0), "items": clean, "employee": payload["employee"], "member_id": member_id}


def _void_payload(payload):
    _strict_mapping(payload, {"sale_id", "reason_code", "reason_text", "expected_total"})
    if not _is_int(payload.get("sale_id")):
        raise ValidationError("銷售單編號格式不正確")
    if not _is_int(payload.get("expected_total")) or payload["expected_total"] < 0:
        raise ValidationError("金額格式不正確")
    code, text = payload.get("reason_code"), payload.get("reason_text")
    if not isinstance(code, str) or code not in VOID_REASONS:
        raise ValidationError("請選擇作廢原因")
    if not isinstance(text, str):
        raise ValidationError("作廢說明格式不正確")
    text = text.strip()
    if code == "other":
        if not text:
            raise ValidationError("原因選「其他」時請填寫說明")
        if len(text) > VOID_TEXT_MAX:
            raise ValidationError(f"作廢說明最多 {VOID_TEXT_MAX} 字")
        reason = f"其他：{text}"
    else:
        reason = VOID_REASONS[code]
    return {"sale_id": payload["sale_id"], "reason": reason, "expected_total": payload["expected_total"]}


def _payment_payload(payload):
    _strict_mapping(payload, {"sale_id", "payment", "expected_payment"})
    if not _is_int(payload.get("sale_id")):
        raise ValidationError("銷售單編號格式不正確")
    for key in ("payment", "expected_payment"):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValidationError("付款方式不正確")
    return {key: payload[key] for key in ("sale_id", "payment", "expected_payment")}


def _csv_text(value):
    """文字格開頭是 = + - @ 或 tab／換行時補單引號:Excel 開檔才不會當成公式執行。"""
    text = str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


class SalesRepository(BaseRepository):
    def payments(self):
        row = self.connection.execute("SELECT value FROM Setting WHERE key='payments'").fetchone()
        return json.loads(row["value"]) if row else []

    def variant_is_active(self, variant_id):
        # 種類、產品、款式都須啟用;缺必填值只是待補提醒,不擋銷售,
        # 待處理問題中只有款式重複會擋。
        return self.connection.execute(
            "SELECT (COALESCE(c.active,1) AND p.active AND v.active AND "
            "NOT EXISTS(SELECT 1 FROM VariantIssue vi WHERE vi.variant_id=v.variant_id "
            "AND vi.issue_type='duplicate_signature')) ok, "
            "v.price, p.name "
            "FROM Variant v "
            "JOIN Product p ON v.product_id=p.product_id "
            "LEFT JOIN Category c ON p.category_id=c.category_id WHERE v.variant_id=?",
            (variant_id,),).fetchone()

    def create_sale(self, payment, order_discount, member_discount, total, paid, employee, member_id):
        return self.connection.execute(
            # 帳日在同一句內決定(不先查再寫,免得查完還沒寫別人剛好點帳):今天已點帳就算明天的帳
            "INSERT INTO Sale(payment,order_discount,member_discount,total,paid,change,employee,member_id,business_date) "
            "VALUES(?,?,?,?,?,?,?,?,CASE WHEN EXISTS(SELECT 1 FROM DayClose WHERE business_date=date('now','localtime')) "
            "THEN date('now','localtime','+1 day') ELSE date('now','localtime') END)",
            (payment, order_discount, member_discount, total, paid, paid - total, employee, member_id)).lastrowid

    def member_is_active(self, member_id):
        return self.connection.execute("SELECT 1 FROM Member WHERE member_id=? AND active=1", (member_id,)).fetchone() is not None

    def add_item(self, sale_id, item):
        item_id = self.connection.execute(
            "INSERT INTO SaleItem(sale_id,variant_id,qty,unit_price,discount) VALUES(?,?,?,?,?)",
            (sale_id, item["variant_id"], item["qty"], item["unit_price"], item["discount"])).lastrowid
        stock_ledger.post(self.connection, "sale", item_id)

    @staticmethod
    def filter_sql(filters, alias="s"):
        prefix = f"{alias}." if alias else ""
        sql, args = "", []
        for key, operator in (("date_from", ">="), ("date_to", "<=")):
            if filters[key]:
                sql += f" AND {prefix}business_date{operator}?"
                args.append(filters[key])
        if filters["payment"]:
            sql += f" AND {prefix}payment=?"
            args.append(filters["payment"])
        if filters["member_phone"]:
            sql += f" AND EXISTS (SELECT 1 FROM MemberPhone mp WHERE mp.member_id={prefix or "Sale."}member_id AND mp.phone LIKE ?)"
            args.append(filters["member_phone"])
        return sql, args

    SALE_ROWS_SQL = ("SELECT s.*,i.variant_id,i.qty,i.unit_price,i.discount,p.name,m.name member_name,"
                     "(SELECT group_concat(phone,'、') FROM (SELECT phone FROM MemberPhone mp WHERE mp.member_id=s.member_id ORDER BY phone)) member_phones,"
                     "EXISTS(SELECT 1 FROM DayClose d WHERE d.business_date=s.business_date) day_closed FROM Sale s "
                     "JOIN SaleItem i ON s.sale_id=i.sale_id JOIN Variant v ON i.variant_id=v.variant_id "
                     "JOIN Product p ON v.product_id=p.product_id LEFT JOIN Member m ON m.member_id=s.member_id WHERE 1=1")

    def sale_rows(self, filters):
        suffix, args = self.filter_sql(filters)
        return self.connection.execute(self.SALE_ROWS_SQL + suffix + " ORDER BY s.sale_id DESC", args).fetchall()

    def recent_rows(self, limit):
        return self.connection.execute(
            self.SALE_ROWS_SQL + " AND s.sale_id IN (SELECT sale_id FROM Sale WHERE date(ts)=date('now','localtime') ORDER BY sale_id DESC LIMIT ?)"
            " ORDER BY s.sale_id DESC", (limit,)).fetchall()

    def summary_rows(self, filters):
        suffix, args = self.filter_sql(filters, "")
        return self.connection.execute(
            "SELECT payment,COUNT(*) c,SUM(total) t FROM Sale WHERE voided_at IS NULL" + suffix + " GROUP BY payment", args).fetchall()

    def void_summary(self, filters):
        suffix, args = self.filter_sql(filters, "")
        return self.connection.execute(
            "SELECT COUNT(*) c,COALESCE(SUM(total),0) t FROM Sale WHERE voided_at IS NOT NULL" + suffix, args).fetchone()


class SalesService:
    def __init__(self, repository):
        self.repo = repository

    def checkout(self, body):
        if body["payment"] not in self.repo.payments():
            raise ValidationError("付款方式未在設定中")
        if body["member_id"] is not None and not self.repo.member_is_active(body["member_id"]):
            raise ValidationError("會員不存在或已刪除，請重新查詢")
        # 讀庫存前先上鎖:否則兩筆同時結最後一件都會通過庫存檢查
        self.repo.connection.execute("BEGIN IMMEDIATE")
        required = {}
        variant_rows = {}
        for item in body["items"]:
            row = self.repo.variant_is_active(item["variant_id"])
            if row is None or not row["ok"]:
                raise ValidationError("商品已停用或不存在")
            if row["price"] is not None and item["unit_price"] != row["price"]:
                raise ValidationError("售價與系統不符，請重新掃描")
            variant_rows[item["variant_id"]] = row
            required[item["variant_id"]] = required.get(item["variant_id"], 0) + item["qty"]
        available = stock_map(self.repo.connection, required)
        for variant_id, qty in required.items():
            if qty > available.get(variant_id, 0):
                row = variant_rows[variant_id]
                raise ValidationError(
                    f"庫存不足：{row['name']}（剩 {available.get(variant_id, 0)} 件）"
                )
        total = sum(i["qty"] * i["unit_price"] - i["discount"] for i in body["items"]) - body["order_discount"] - body["member_discount"]
        if total < 0:
            raise ValidationError("折扣後總額不可為負數")
        if body["payment"] != CASH_PAYMENT:
            body["paid"] = total  # 非現金不找零,實收即應收
        elif body["paid"] < total:
            raise ValidationError("實收金額不足，請確認實收金額")
        sale_id = self.repo.create_sale(body["payment"], body["order_discount"], body["member_discount"], total, body["paid"],
                                       body["employee"], body["member_id"])
        for item in body["items"]:
            self.repo.add_item(sale_id, item)
        return {"sale_id": sale_id, "total": total, "change": body["paid"] - total}

    def void(self, body):
        conn = self.repo.connection
        conn.execute("BEGIN IMMEDIATE")
        sale = conn.execute(
            "SELECT s.total, s.voided_at, EXISTS(SELECT 1 FROM DayClose d WHERE d.business_date=s.business_date) closed "
            "FROM Sale s WHERE s.sale_id=?", (body["sale_id"],)).fetchone()
        if sale is None:
            raise NotFoundError("找不到銷售單")
        if sale["total"] != body["expected_total"]:
            raise ConflictError("銷售單內容已變更，請重新確認")
        if sale["voided_at"] is not None:
            raise ConflictError("此單已作廢")
        if sale["closed"]:
            raise ConflictError("此帳日已點帳，請改走退貨")
        if conn.execute("SELECT 1 FROM ReturnItem i JOIN ReturnSession s ON s.return_id=i.return_id "
                        "WHERE i.sale_id=? AND s.status<>'cancelled' LIMIT 1", (body["sale_id"],)).fetchone():
            raise ConflictError("此單已有退貨單，不可作廢")
        # 條件式更新:連點兩次時第二次改不到列,不會重複沖回
        changed = conn.execute(
            "UPDATE Sale SET voided_at=datetime('now','localtime'), void_reason=? "
            "WHERE sale_id=? AND voided_at IS NULL", (body["reason"], body["sale_id"])).rowcount
        if changed != 1:
            raise ConflictError("此單已作廢")
        for item in conn.execute("SELECT item_id, variant_id, qty FROM SaleItem WHERE sale_id=?", (body["sale_id"],)).fetchall():
            stock_ledger.post(conn, "void", item["item_id"])
            # 以盤點為準:進行中或待核准的盤點單若已盤到這個品項,系統數量同步加回、實盤數量不動,
            # 核准時差額(實盤−系統)才不會把作廢沖回的數量再算一次
            conn.execute("UPDATE StocktakeItem SET system_qty=system_qty+? WHERE variant_id=? AND session_id IN "
                         "(SELECT session_id FROM StocktakeSession WHERE status IN ('open','pending'))",
                         (item["qty"], item["variant_id"]))
        return {"ok": True}

    def set_payment(self, body):
        """更正付款方式:只改 payment 並留紀錄。實收與找零保留原值(已入帳鎖定,也不該改);
        應有現金一律以應收計算,所以不影響帳。"""
        conn = self.repo.connection
        conn.execute("BEGIN IMMEDIATE")
        sale = conn.execute(
            "SELECT s.payment, s.voided_at, EXISTS(SELECT 1 FROM DayClose d WHERE d.business_date=s.business_date) closed "
            "FROM Sale s WHERE s.sale_id=?", (body["sale_id"],)).fetchone()
        if sale is None:
            raise NotFoundError("找不到銷售單")
        if sale["payment"] != body["expected_payment"]:
            raise ConflictError("銷售單內容已變更，請重新確認")
        if sale["voided_at"] is not None:
            raise ConflictError("此單已作廢，不可更正付款方式")
        if sale["closed"]:
            raise ConflictError("此帳日已點帳，不可更正付款方式")
        if body["payment"] not in self.repo.payments():
            raise ValidationError("付款方式未在設定中")
        if body["payment"] == sale["payment"]:
            raise ValidationError("新付款方式與目前相同")
        conn.execute("UPDATE Sale SET payment=? WHERE sale_id=?", (body["payment"], body["sale_id"]))
        correction_service.record(conn, "sale", body["sale_id"], "payment", sale["payment"], body["payment"])
        return {"ok": True}

    def list_sales(self, filters, load_attrs=True):
        sales = self._group(self.repo.sale_rows(filters), load_attrs)
        return correction_service.attach(self.repo.connection, "sale", sales, "sale_id")

    # 收銀頁下方只列本日最近 5 筆;其餘到紀錄查詢頁看
    def recent(self):
        return self._group(self.repo.recent_rows(5))

    def _group(self, rows, load_attrs=True):
        vids = [row["variant_id"] for row in rows]
        attrs = product_data.attrs_by_variant(self.repo.connection, vids) if load_attrs else {}
        display = product_data.display_attrs(self.repo.connection, vids)
        out = {}
        for row in rows:
            sale = out.setdefault(row["sale_id"], {"sale_id": row["sale_id"], "ts": row["ts"],
                "payment": row["payment"], "order_discount": row["order_discount"], "member_discount": row["member_discount"],
                "total": row["total"], "employee": row["employee"],
                "business_date": row["business_date"], "day_closed": bool(row["day_closed"]),
                "voided_at": row["voided_at"], "void_reason": row["void_reason"],
                "member_id": row["member_id"], "member_name": row["member_name"],
                "member_phones": row["member_phones"], "items": []})
            sale["items"].append({"variant_id": row["variant_id"], "name": row["name"],
                "attributes": attrs.get(row["variant_id"], {}), "attr_display": display.get(row["variant_id"], ""),
                "qty": row["qty"], "unit_price": row["unit_price"], "discount": row["discount"]})
        return list(out.values())

    def summary(self, filters):
        # 筆數、總額、各付款方式只算未作廢;作廢另外回報
        rows = self.repo.summary_rows(filters)
        void = self.repo.void_summary(filters)
        return {"count": sum(row["c"] for row in rows), "total": sum(row["t"] or 0 for row in rows),
                "by_payment": self._by_payment(rows), "void_count": void["c"], "void_total": void["t"]}

    # 依設定清單順序固定列出每種付款方式(沒交易的列 0);清單外的舊付款方式排在後面
    def _by_payment(self, rows):
        found = {row["payment"]: (row["c"], row["t"] or 0) for row in rows}
        names = self.repo.payments() + [p for p in found if p not in self.repo.payments()]
        return [{"payment": p, "count": found.get(p, (0, 0))[0], "total": found.get(p, (0, 0))[1]} for p in names]

    def export(self, filters):
        rows = self.repo.sale_rows(filters)
        display = product_data.display_attrs(self.repo.connection, [row["variant_id"] for row in rows])
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["銷售編號", "時間", "員工", "會員", "付款方式", "商品", "規格", "數量", "原價", "單項折扣", "訂單折扣", "會員折扣", "總額",
                         "帳日", "狀態", "作廢原因"])
        for row in rows:
            writer.writerow([row["sale_id"], row["ts"], row["employee"], _csv_text(row["member_name"] or ""),
                _csv_text(row["payment"]), _csv_text(row["name"]), _csv_text(display.get(row["variant_id"], "")), row["qty"], row["unit_price"], row["discount"],
                row["order_discount"], row["member_discount"], row["total"],
                row["business_date"], "已作廢" if row["voided_at"] else "", _csv_text(row["void_reason"] or "")])
        return {"filename": "sales.csv", "content": "\ufeff" + buf.getvalue()}


class SalesFacade(BaseFacade):
    ACTIONS = {"payments.list", "sales.checkout", "sales.list", "sales.recent", "sales.summary", "sales.export", "sales.void", "sales.set_payment"}

    ERROR_MESSAGE = "銷售操作不正確"

    def _prepare_payload(self, action, payload):
        if action == "sales.checkout":
            return _checkout_payload(payload)
        if action == "sales.void":
            return _void_payload(payload)
        if action == "sales.set_payment":
            return _payment_payload(payload)
        if action in {"payments.list", "sales.recent"}:
            _strict_mapping(payload, set())
            return payload
        return _filters(payload, legacy_date=action == "sales.summary")

    def _dispatch(self, action, payload, connection):
        service = SalesService(SalesRepository(connection))
        if action == "payments.list": return service.repo.payments()
        if action == "sales.checkout": return service.checkout(payload)
        if action == "sales.void": return service.void(payload)
        if action == "sales.set_payment": return service.set_payment(payload)
        if action == "sales.list": return service.list_sales(payload)
        if action == "sales.recent": return service.recent()
        if action == "sales.summary": return service.summary(payload)
        return service.export(payload)
