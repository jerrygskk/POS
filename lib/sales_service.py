import csv
import io
import json
from datetime import date as calendar_date
from collections.abc import Mapping

from lib import product_data
from lib.application import BaseFacade, BaseRepository
from lib.application_errors import ValidationError
from lib.db import stock_map
from lib.product_rules import is_int as _is_int


def _strict_mapping(payload, allowed):
    if not isinstance(payload, Mapping) or set(payload) - set(allowed):
        raise ValidationError("銷售資料格式不正確")


def _filters(payload, legacy_date=False):
    _strict_mapping(payload, {"date_from", "date_to", "payment", "date"} if legacy_date
                    else {"date_from", "date_to", "payment"})
    values = {key: payload.get(key, "") for key in ("date_from", "date_to", "payment")}
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
    if date and not values["date_from"] and not values["date_to"]:
        values["date_from"] = values["date_to"] = date
    return values


def _checkout_payload(payload):
    _strict_mapping(payload, {"payment", "order_discount", "paid", "items"})
    if not isinstance(payload.get("payment"), str) or not payload["payment"]:
        raise ValidationError("付款方式不正確")
    for key in ("order_discount", "paid"):
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
            raise ValidationError("單價格式不正確")
        if not _is_int(row["discount"]) or row["discount"] < 0:
            raise ValidationError("折扣格式不正確")
        if row["discount"] > row["qty"] * row["unit_price"]:
            raise ValidationError("單項折扣不可超過該項金額")
        clean.append(row)
    return {"payment": payload["payment"], "order_discount": payload.get("order_discount", 0),
            "paid": payload.get("paid", 0), "items": clean}


class SalesRepository(BaseRepository):
    def payments(self):
        row = self.connection.execute("SELECT value FROM Setting WHERE key='payments'").fetchone()
        return json.loads(row["value"]) if row else []

    def variant_is_active(self, variant_id):
        # 有效啟用(規格 §8.2):Category.active AND Product.active AND Variant.active
        # AND 無未解決 VariantIssue
        return self.connection.execute(
            "SELECT (COALESCE(c.active,1) AND p.active AND v.active AND "
            "NOT EXISTS(SELECT 1 FROM VariantIssue vi WHERE vi.variant_id=v.variant_id)) ok, "
            "v.price, p.name "
            "FROM Variant v "
            "JOIN Product p ON v.product_id=p.product_id "
            "LEFT JOIN Category c ON p.category_id=c.category_id WHERE v.variant_id=?",
            (variant_id,),).fetchone()

    def create_sale(self, payment, order_discount, total, paid):
        return self.connection.execute(
            "INSERT INTO Sale(payment,order_discount,total,paid,change) VALUES(?,?,?,?,?)",
            (payment, order_discount, total, paid, paid - total)).lastrowid

    def add_item(self, sale_id, item):
        self.connection.execute(
            "INSERT INTO SaleItem(sale_id,variant_id,qty,unit_price,discount) VALUES(?,?,?,?,?)",
            (sale_id, item["variant_id"], item["qty"], item["unit_price"], item["discount"]))
        self.connection.execute(
            "INSERT INTO StockMovement(variant_id,qty,kind,ref_id) VALUES(?,?,'sale',?)",
            (item["variant_id"], -item["qty"], sale_id))

    @staticmethod
    def filter_sql(filters, alias="s"):
        prefix = f"{alias}." if alias else ""
        sql, args = "", []
        for key, operator in (("date_from", ">="), ("date_to", "<=")):
            if filters[key]:
                sql += f" AND date({prefix}ts){operator}?"
                args.append(filters[key])
        if filters["payment"]:
            sql += f" AND {prefix}payment=?"
            args.append(filters["payment"])
        return sql, args

    def sale_rows(self, filters):
        sql = ("SELECT s.*,i.variant_id,i.qty,i.unit_price,i.discount,p.name FROM Sale s "
               "JOIN SaleItem i ON s.sale_id=i.sale_id JOIN Variant v ON i.variant_id=v.variant_id "
               "JOIN Product p ON v.product_id=p.product_id WHERE 1=1")
        suffix, args = self.filter_sql(filters)
        return self.connection.execute(sql + suffix + " ORDER BY s.sale_id DESC", args).fetchall()

    def summary_rows(self, filters):
        suffix, args = self.filter_sql(filters, "")
        return self.connection.execute(
            "SELECT payment,COUNT(*) c,SUM(total) t FROM Sale WHERE 1=1" + suffix + " GROUP BY payment", args).fetchall()


class SalesService:
    def __init__(self, repository):
        self.repo = repository

    def checkout(self, body):
        if body["payment"] not in self.repo.payments():
            raise ValidationError("付款方式未在設定中")
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
        total = sum(i["qty"] * i["unit_price"] - i["discount"] for i in body["items"]) - body["order_discount"]
        if total < 0:
            raise ValidationError("折扣後總額不可為負數")
        sale_id = self.repo.create_sale(body["payment"], body["order_discount"], total, body["paid"])
        for item in body["items"]:
            self.repo.add_item(sale_id, item)
        return {"sale_id": sale_id, "total": total, "change": body["paid"] - total}

    def list_sales(self, filters, load_attrs=True):
        rows = self.repo.sale_rows(filters)
        vids = [row["variant_id"] for row in rows]
        attrs = product_data.attrs_by_variant(self.repo.connection, vids) if load_attrs else {}
        display = product_data.display_attrs(self.repo.connection, vids)
        out = {}
        for row in rows:
            sale = out.setdefault(row["sale_id"], {"sale_id": row["sale_id"], "ts": row["ts"],
                "payment": row["payment"], "order_discount": row["order_discount"],
                "total": row["total"], "items": []})
            sale["items"].append({"variant_id": row["variant_id"], "name": row["name"],
                "attributes": attrs.get(row["variant_id"], {}), "attr_display": display.get(row["variant_id"], ""),
                "qty": row["qty"], "unit_price": row["unit_price"], "discount": row["discount"]})
        return list(out.values())

    def summary(self, filters):
        rows = self.repo.summary_rows(filters)
        return {"count": sum(row["c"] for row in rows), "total": sum(row["t"] or 0 for row in rows),
                "by_payment": {row["payment"]: row["t"] for row in rows}}

    def export(self, filters):
        rows = self.repo.sale_rows(filters)
        display = product_data.display_attrs(self.repo.connection, [row["variant_id"] for row in rows])
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["銷售編號", "時間", "付款方式", "商品", "規格", "數量", "單價", "單項折扣", "訂單折扣", "總額"])
        for row in rows:
            writer.writerow([row["sale_id"], row["ts"], row["payment"], row["name"],
                display.get(row["variant_id"], ""), row["qty"], row["unit_price"], row["discount"],
                row["order_discount"], row["total"]])
        return {"filename": "sales.csv", "content": "\ufeff" + buf.getvalue()}


class SalesFacade(BaseFacade):
    ACTIONS = {"payments.list", "sales.checkout", "sales.list", "sales.summary", "sales.export"}

    ERROR_MESSAGE = "銷售操作不正確"

    def _prepare_payload(self, action, payload):
        if action == "sales.checkout":
            return _checkout_payload(payload)
        if action == "payments.list":
            _strict_mapping(payload, set())
            return payload
        return _filters(payload, legacy_date=action == "sales.summary")

    def _dispatch(self, action, payload, connection):
        service = SalesService(SalesRepository(connection))
        if action == "payments.list": return service.repo.payments()
        if action == "sales.checkout": return service.checkout(payload)
        if action == "sales.list": return service.list_sales(payload)
        if action == "sales.summary": return service.summary(payload)
        return service.export(payload)
