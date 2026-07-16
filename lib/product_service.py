from collections.abc import Mapping

from lib.application import TransactionRunner
from lib.application_errors import ConflictError, NotFoundError, ValidationError
from lib.db import db_conn, in_clause, next_sort
from lib.normalize import normalize_display, normalize_key
from lib.product_rules import next_store_barcode
from lib import product_data


def _update_by_id(conn, table, id_col, item_id, fields):
    if fields:
        conn.execute(f"UPDATE {table} SET " + ",".join(f"{key}=?" for key in fields) +
                     f" WHERE {id_col}=?", (*fields.values(), item_id))


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _mapping(value, name):
    if not isinstance(value, Mapping):
        raise ValidationError(f"{name} 格式不正確")
    return value


def _int_list(value, name):
    if not isinstance(value, list) or any(not _is_int(item) for item in value):
        raise ValidationError(f"{name} 格式不正確")


def _allow(payload, allowed):
    unknown = set(payload) - set(allowed)
    if unknown:
        raise ValidationError(f"不支援的欄位：{sorted(unknown)[0]}")


def _validate_barcode(value):
    value = _mapping(value, "條碼")
    _allow(value, {"barcode", "source"})
    if value.get("barcode") is not None and not isinstance(value["barcode"], str):
        raise ValidationError("條碼格式不正確")
    if "source" in value and not isinstance(value["source"], str):
        raise ValidationError("條碼來源格式不正確")


def _validate_variant(value):
    value = _mapping(value, "子產品")
    _allow(value, {"attributes", "price", "active", "model_ids", "barcodes"})
    if "attributes" in value: _mapping(value["attributes"], "規格")
    if value.get("price") is not None and not _is_int(value["price"]): raise ValidationError("售價格式不正確")
    if value.get("active") is not None and not _is_int(value["active"]): raise ValidationError("啟用狀態格式不正確")
    if "model_ids" in value: _int_list(value["model_ids"], "型號")
    if "barcodes" in value:
        if not isinstance(value["barcodes"], list): raise ValidationError("條碼清單格式不正確")
        for item in value["barcodes"]: _validate_barcode(item)


def _validate_draft(value):
    value = _mapping(value, "子產品")
    _allow(value, {"draft_id", "attributes", "price", "active", "model_ids", "barcodes"})
    if value.get("draft_id") is not None and not isinstance(value["draft_id"], str):
        raise ValidationError("draft_id 格式不正確")
    if "attributes" in value: _mapping(value["attributes"], "規格")
    if value.get("price") is not None and not _is_int(value["price"]): raise ValidationError("售價格式不正確")
    if value.get("active") is not None and not _is_int(value["active"]): raise ValidationError("啟用狀態格式不正確")
    if "model_ids" in value: _int_list(value["model_ids"], "型號")
    if "barcodes" in value:
        if not isinstance(value["barcodes"], list): raise ValidationError("條碼清單格式不正確")
        for item in value["barcodes"]: _validate_barcode(item)


def _validate_action_payload(action, payload):
    id_actions={"products.update","products.delete","variants.update","variants.set_models","variants.update_details","variants.delete"}
    if action=="variants.batch_create":
        _allow(payload,{"product_id","drafts"})
        if not _is_int(payload.get("product_id")):raise ValidationError("商品識別碼格式不正確")
        if not isinstance(payload.get("drafts"),list) or not payload["drafts"]:raise ValidationError("尚未加入任何子產品")
        for item in payload["drafts"]:_validate_draft(item)
        return
    if action=="variants.field_usage":
        _allow(payload,{"category_id","field_id"})
        if not _is_int(payload.get("category_id")) or not _is_int(payload.get("field_id")):raise ValidationError("識別碼格式不正確")
        return
    if action=="products.create":
        _allow(payload,{"name","category_id","brand_id","brand_name","note","variants"})
        if not isinstance(payload.get("name"),str) or not _is_int(payload.get("category_id")):raise ValidationError("商品資料格式不正確")
        if payload.get("brand_id") is not None and not _is_int(payload["brand_id"]):raise ValidationError("廠牌格式不正確")
        if payload.get("brand_name") is not None and not isinstance(payload["brand_name"],str):raise ValidationError("廠牌名稱格式不正確")
        if payload.get("note") is not None and not isinstance(payload["note"],str):raise ValidationError("備註格式不正確")
        if not isinstance(payload.get("variants",[]),list):raise ValidationError("子產品清單格式不正確")
        for item in payload.get("variants",[]):_validate_variant(item)
    elif action in ("products.list","catalog.list"):
        allowed={"q","category_id","brand_id","model_id"}|({"include_inactive"} if action=="catalog.list" else set());_allow(payload,allowed)
        if not isinstance(payload.get("q",""),str):raise ValidationError("查詢字串格式不正確")
        for key in ("category_id","brand_id","model_id"):
            if payload.get(key) is not None and not _is_int(payload[key]):raise ValidationError("篩選條件格式不正確")
        if "include_inactive" in payload and not isinstance(payload["include_inactive"],bool):raise ValidationError("停用篩選格式不正確")
    elif action in id_actions:
        allowed={"id"}
        if action in ("products.update","variants.update","variants.update_details"):allowed.add("fields")
        if action in ("variants.set_models","variants.update_details"):allowed.add("model_ids")
        _allow(payload,allowed)
        if not _is_int(payload.get("id")):raise ValidationError("識別碼格式不正確")
        if "fields" in payload:
            fields=_mapping(payload["fields"],"更新欄位")
            if action=="products.update":
                _allow(fields,{"name","category_id","brand_id","brand_name","note","active"})
                if fields.get("name") is not None and not isinstance(fields["name"],str):raise ValidationError("商品名稱格式不正確")
                for key in ("category_id","brand_id","active"):
                    if fields.get(key) is not None and not _is_int(fields[key]):raise ValidationError(f"{key} 格式不正確")
                if fields.get("brand_name") is not None and not isinstance(fields["brand_name"],str):raise ValidationError("廠牌名稱格式不正確")
                if fields.get("note") is not None and not isinstance(fields["note"],str):raise ValidationError("備註格式不正確")
            else:_validate_variant(fields)
        if "model_ids" in payload:_int_list(payload["model_ids"],"型號")
    elif action=="variants.create":
        _allow(payload,{"product_id","fields"})
        if not _is_int(payload.get("product_id")):raise ValidationError("商品識別碼格式不正確")
        _validate_variant(payload.get("fields"))
    elif action=="barcodes.add":
        _allow(payload,{"variant_id","barcode","source"})
        if not _is_int(payload.get("variant_id")):raise ValidationError("子產品識別碼格式不正確")
        _validate_barcode({k:v for k,v in payload.items() if k!="variant_id"})
    else:
        _allow(payload,{"code"})
        if not isinstance(payload.get("code"),str):raise ValidationError("條碼格式不正確")


class ProductRepository:
    def __init__(self, connection):
        self.connection = connection

    def one(self, sql, args=()):
        return self.connection.execute(sql, args).fetchone()

    def all(self, sql, args=()):
        return self.connection.execute(sql, args).fetchall()

    def execute(self, sql, args=()):
        return self.connection.execute(sql, args)

    def require_active_category(self, category_id):
        row = self.one("SELECT active FROM Category WHERE category_id=?", (category_id,))
        if row is None or not row["active"]:
            raise ValidationError("商品種類不存在或已停用")

    def require_brand(self, brand_id):
        if brand_id is None:
            return
        if self.one("SELECT 1 FROM Brand WHERE brand_id=?", (brand_id,)) is None:
            raise ValidationError("廠牌不存在")

    def require_variant(self, variant_id):
        if self.one("SELECT 1 FROM Variant WHERE variant_id=?", (variant_id,)) is None:
            raise NotFoundError("找不到子產品")

    def require_product(self, product_id):
        if self.one("SELECT 1 FROM Product WHERE product_id=?", (product_id,)) is None:
            raise NotFoundError("找不到商品")


class ProductService:
    def __init__(self, repository):
        self.repo = repository

    def _resolve_brand(self, category_id, brand_id, brand_name):
        """廠牌解析:brand_name(inline)以 normalize_key 比對既有,同名沿用,否則建立;
        回傳最終 brand_id。呼叫端另建 BrandCategory 關聯。"""
        if brand_name is not None and brand_name.strip():
            key = normalize_key(brand_name)
            for r in self.repo.all("SELECT brand_id,name FROM Brand"):
                if normalize_key(r["name"]) == key:
                    return r["brand_id"]
            cur = self.repo.execute("INSERT INTO Brand(name,sort) VALUES(?,?)",
                                    (normalize_display(brand_name), next_sort(self.repo.connection, "Brand")))
            return cur.lastrowid
        self.repo.require_brand(brand_id)
        return brand_id

    def _same_category_name_exists(self, category_id, name, exclude_pid=None):
        key = normalize_key(name)
        sql = "SELECT product_id,name FROM Product WHERE category_id=?"
        args = [category_id]
        if exclude_pid is not None:
            sql += " AND product_id<>?"; args.append(exclude_pid)
        return any(normalize_key(r["name"]) == key for r in self.repo.all(sql, args))

    def create(self, payload):
        self.repo.require_active_category(payload["category_id"])
        brand_id = self._resolve_brand(payload["category_id"], payload.get("brand_id"),
                                       payload.get("brand_name"))
        if self._same_category_name_exists(payload["category_id"], payload["name"]):
            raise ConflictError("此種類已有同名大產品")
        cur = self.repo.execute(
            "INSERT INTO Product(name,category_id,brand_id,note) VALUES(?,?,?,?)",
            (payload["name"], payload["category_id"], brand_id, payload.get("note")))
        if brand_id is not None:
            self.repo.execute("INSERT OR IGNORE INTO BrandCategory(brand_id,category_id) VALUES(?,?)",
                              (brand_id, payload["category_id"]))
        variant_ids = []
        for variant in payload.get("variants", []):
            variant_ids.append(self._create_variant(cur.lastrowid, payload["category_id"], variant)[0])
        return {"product_id": cur.lastrowid, "variant_ids": variant_ids}

    def _create_variant(self, product_id, category_id, payload):
        cur = self.repo.execute("INSERT INTO Variant(product_id,price) VALUES(?,?)",
                                (product_id, payload.get("price")))
        vid = cur.lastrowid
        product_data.set_variant_attributes(self.repo.connection, vid, category_id, payload.get("attributes", {}))
        product_data.set_variant_models(self.repo.connection, vid, payload.get("model_ids", []))
        codes = []
        for barcode in payload.get("barcodes", []):
            codes.append(self._add_barcode(vid, barcode)["barcode"])
        return vid, codes

    def add_variant(self, product_id, payload):
        row = self.repo.one("SELECT category_id,active FROM Product WHERE product_id=?", (product_id,))
        if row is None:
            raise NotFoundError("找不到商品")
        # 子產品建立要求 Category 與 Product 皆 active(規格 §8.2)
        if not row["active"]:
            raise ValidationError("大產品已停用,不可新增子產品")
        self.repo.require_active_category(row["category_id"])
        vid, codes = self._create_variant(product_id, row["category_id"], payload)
        return {"variant_id": vid, "barcodes": codes}

    def _add_barcode(self, variant_id, payload):
        self.repo.require_variant(variant_id)
        code = payload.get("barcode")
        if code and code.strip().upper().startswith("TL"):
            raise ValidationError("TL 開頭條碼僅供系統自動產生")
        code = code or next_store_barcode(self.repo.connection)
        self.repo.execute("INSERT INTO Barcode(barcode,variant_id,source) VALUES(?,?,?)",
                          (code, variant_id, payload.get("source", "store")))
        return {"barcode": code}

    def add_barcode(self, payload):
        return self._add_barcode(payload["variant_id"], payload)

    def scan(self, code):
        row = self.repo.one(
            "SELECT v.variant_id,v.product_id,v.price price,p.name,"
            "(COALESCE(c.active,1) AND p.active AND v.active) active FROM Barcode b "
            "JOIN Variant v ON b.variant_id=v.variant_id JOIN Product p ON v.product_id=p.product_id "
            "LEFT JOIN Category c ON p.category_id=c.category_id "
            "WHERE b.barcode=?", (code,))
        if row is None:
            raise NotFoundError("找不到此條碼")
        vid = row["variant_id"]
        return {"variant_id": vid, "product_id": row["product_id"], "name": row["name"],
                "attributes": product_data.attrs_by_variant(self.repo.connection, [vid]).get(vid, {}),
                "attr_display": product_data.display_attrs(self.repo.connection, [vid]).get(vid, ""), "price": row["price"],
                "stock": product_data.stock_of(self.repo.connection, vid), "active": bool(row["active"])}

    def search(self, payload):
        sql = ("SELECT v.variant_id,p.name,v.price price,"
               "c.name category_name,b.name brand_name FROM Variant v "
               "JOIN Product p ON v.product_id=p.product_id LEFT JOIN Category c ON p.category_id=c.category_id "
               "LEFT JOIN Brand b ON p.brand_id=b.brand_id "
               "WHERE p.active=1 AND v.active=1 AND (c.active=1 OR c.category_id IS NULL)")
        args = []
        for key, column in (("category_id", "p.category_id"), ("brand_id", "p.brand_id")):
            if payload.get(key) is not None:
                sql += f" AND {column}=?"; args.append(payload[key])
        if payload.get("model_id") is not None:
            sql += " AND v.variant_id IN (SELECT variant_id FROM VariantModel WHERE model_id=?)"
            args.append(payload["model_id"])
        rows = self.repo.all(sql + " ORDER BY v.variant_id", args)
        attrs = product_data.attrs_by_variant(self.repo.connection, [r["variant_id"] for r in rows])
        query = payload.get("q", "").lower()
        if query:
            rows = [r for r in rows if query in (r["name"] or "").lower() or
                    any(query in str(v).lower() for v in attrs.get(r["variant_id"], {}).values())]
        rows = rows[:100]; vids = [r["variant_id"] for r in rows]
        models = product_data.models_by_variant(self.repo.connection, vids)
        display = product_data.display_attrs(self.repo.connection, vids)
        from lib.db import stock_map
        stocks = stock_map(self.repo.connection, vids)
        return [{"variant_id": r["variant_id"], "name": r["name"],
                 "attributes": attrs.get(r["variant_id"], {}), "attr_display": display.get(r["variant_id"], ""),
                 "price": r["price"], "category_name": r["category_name"], "brand_name": r["brand_name"],
                 "models": models.get(r["variant_id"], []), "stock": stocks.get(r["variant_id"], 0)} for r in rows]

    def catalog(self, payload):
        data = product_data.catalog(self.repo.connection, payload.get("include_inactive", False),
                                    payload.get("category_id"), payload.get("brand_id"), payload.get("model_id"))
        return product_data.filter_catalog(data, payload.get("q", ""))

    def update_product(self, pid, fields):
        self.repo.require_product(pid)
        row = self.repo.one("SELECT category_id FROM Product WHERE product_id=?", (pid,))
        fields = dict(fields)
        category_id = fields.get("category_id") if fields.get("category_id") is not None else row["category_id"]
        if fields.get("category_id") is not None: self.repo.require_active_category(fields["category_id"])
        # 廠牌 inline 新增(brand_name)或指定 brand_id
        if fields.pop("brand_name", None) is not None or "brand_id" in fields:
            brand_id = self._resolve_brand(category_id, fields.get("brand_id"), fields.get("brand_name"))
            fields["brand_id"] = brand_id
            if brand_id is not None and category_id is not None:
                self.repo.execute("INSERT OR IGNORE INTO BrandCategory(brand_id,category_id) VALUES(?,?)",
                                  (brand_id, category_id))
        fields.pop("brand_name", None)
        if fields.get("name") is not None and category_id is not None:
            if self._same_category_name_exists(category_id, fields["name"], exclude_pid=pid):
                raise ConflictError("此種類已有同名大產品")
        _update_by_id(self.repo.connection, "Product", "product_id", pid, fields)
        return {"ok": True}

    def update_variant(self, vid, fields, model_ids=None):
        self.repo.require_variant(vid)
        row = self.repo.one("SELECT p.category_id FROM Variant v JOIN Product p ON v.product_id=p.product_id WHERE v.variant_id=?", (vid,))
        fields = dict(fields); marker = "attributes" in fields; attrs = fields.pop("attributes", None)
        _update_by_id(self.repo.connection, "Variant", "variant_id", vid, fields)
        if marker: product_data.set_variant_attributes(self.repo.connection, vid, row["category_id"], attrs)
        if model_ids is not None: product_data.set_variant_models(self.repo.connection, vid, model_ids)
        return {"ok": True}

    def delete_barcode(self, code):
        if self.repo.execute("DELETE FROM Barcode WHERE barcode=?", (code,)).rowcount == 0:
            raise NotFoundError("找不到此條碼")
        return {"ok": True}

    def delete_variant(self, vid):
        self.repo.require_variant(vid)
        if product_data.has_records(self.repo.connection, [vid]): raise ConflictError("子產品已有交易紀錄，無法刪除")
        touched = {r["option_id"] for r in self.repo.all(
            "SELECT DISTINCT option_id FROM VariantAttribute WHERE variant_id=? AND option_id IS NOT NULL", (vid,))}
        for table in ("VariantAttribute", "VariantModel", "Barcode"):
            self.repo.execute(f"DELETE FROM {table} WHERE variant_id=?", (vid,))
        self.repo.execute("DELETE FROM Variant WHERE variant_id=?", (vid,))
        product_data.cleanup_unused_options(self.repo.connection, touched)
        return {"ok": True}

    def delete_product(self, pid):
        self.repo.require_product(pid)
        vids = [r["variant_id"] for r in self.repo.all("SELECT variant_id FROM Variant WHERE product_id=?", (pid,))]
        if product_data.has_records(self.repo.connection, vids): raise ConflictError("商品已有交易紀錄，無法刪除")
        touched = set()
        if vids:
            qs = in_clause(vids)
            touched = {r["option_id"] for r in self.repo.all(
                f"SELECT DISTINCT option_id FROM VariantAttribute WHERE variant_id IN ({qs}) AND option_id IS NOT NULL", vids)}
            for table in ("VariantAttribute", "VariantModel", "Barcode", "Variant"):
                self.repo.execute(f"DELETE FROM {table} WHERE variant_id IN ({qs})", vids)
        self.repo.execute("DELETE FROM Product WHERE product_id=?", (pid,))
        product_data.cleanup_unused_options(self.repo.connection, touched)
        return {"ok": True}


class ProductFacade:
    ACTIONS = {"products.create", "products.list", "catalog.list", "products.update", "products.delete",
               "variants.create", "variants.update", "variants.set_models", "variants.update_details", "variants.delete",
               "variants.batch_create", "variants.field_usage",
               "barcodes.scan", "barcodes.add", "barcodes.delete"}

    def __init__(self, db_path):
        self.runner = TransactionRunner(db_path, connection_context=db_conn)

    def invoke(self, action, payload=None):
        payload = {} if payload is None else payload
        if action not in self.ACTIONS or not isinstance(payload, Mapping): raise ValidationError("不支援的商品操作")
        _validate_action_payload(action, payload)

        def work(connection):
            s = ProductService(ProductRepository(connection))
            if action == "products.create": return s.create(payload)
            if action == "products.list": return s.search(payload)
            if action == "catalog.list": return s.catalog(payload)
            if action == "products.update": return s.update_product(payload["id"], payload.get("fields", {}))
            if action == "products.delete": return s.delete_product(payload["id"])
            if action == "variants.create": return s.add_variant(payload["product_id"], payload.get("fields", payload))
            if action == "variants.update": return s.update_variant(payload["id"], payload.get("fields", {}))
            if action == "variants.set_models": return s.update_variant(payload["id"], {}, payload.get("model_ids", []))
            if action == "variants.update_details": return s.update_variant(payload["id"], payload.get("fields", {}), payload.get("model_ids", []))
            if action == "variants.delete": return s.delete_variant(payload["id"])
            if action == "variants.batch_create":
                from lib.variant_batch_service import VariantBatchService
                return VariantBatchService(connection).batch_create(payload)
            if action == "variants.field_usage":
                return product_data.option_usage_in_category(connection, payload["field_id"], payload["category_id"])
            if action == "barcodes.scan": return s.scan(payload["code"])
            if action == "barcodes.delete": return s.delete_barcode(payload["code"])
            return s.add_barcode(payload)
        return self.runner.run(work)
