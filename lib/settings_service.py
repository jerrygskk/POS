import sqlite3
from collections.abc import Mapping

from lib.application import TransactionRunner
from lib.application_errors import ConflictError, NotFoundError, ValidationError
from lib.db import db_conn, in_clause, next_sort
from lib.product_rules import FIELD_TYPES


_ACTION_RULES = {
    **{f"{kind}.{op}": ({}, {}) for kind in ("categories", "brands", "phone_brands") for op in ("list", "create", "update", "delete", "sort")},
    "brands.set_categories": ({"id": int, "category_ids": "int_list"}, {}),
    "models.list": ({}, {"all": (bool, int), "phone_brand_id": (int, type(None))}),
    "models.create": ({"phone_brand_id": int, "name": str}, {"alias": (str, type(None)), "series": (str, type(None)), "sort": (int, type(None))}),
    "models.update": ({"id": int, "fields": Mapping}, {}),
    "models.delete": ({"id": int}, {}),
    "models.sort": ({"ids": "int_list"}, {}),
    "fields.list": ({}, {"category_id": (int, type(None)), "common": (bool, int)}),
    "fields.create": ({"name": str}, {"category_id": (int, type(None)), "field_type": str, "default_option_id": (int, type(None))}),
    "fields.update": ({"id": int, "fields": Mapping}, {}),
    "options.list": ({"field_id": int}, {"all": (bool, int), "model_ids": "int_list"}),
    "options.create": ({"field_id": int, "value": str}, {"reactivate": bool}),
    "options.update": ({"id": int, "fields": Mapping}, {}),
    "options.delete": ({"id": int}, {}),
    "options.models": ({"id": int}, {}),
    "options.set_models": ({"id": int, "model_ids": "int_list"}, {}),
    "categories.fields": ({"id": int}, {}),
    "categories.set_common_fields": ({"id": int, "field_ids": "int_list"}, {}),
}

_UPDATE_FIELD_RULES = {
    "categories.update": {"name": str, "sort": int, "active": (bool, int)},
    "brands.update": {"name": str, "sort": int, "active": (bool, int)},
    "phone_brands.update": {"name": str, "sort": int, "active": (bool, int)},
    "models.update": {
        "phone_brand_id": int,
        "name": str,
        "alias": (str, type(None)),
        "series": (str, type(None)),
        "sort": int,
        "active": (bool, int),
    },
    "fields.update": {
        "name": str,
        "sort": int,
        "active": (bool, int),
        "field_type": str,
        "default_option_id": (int, type(None)),
    },
    "options.update": {"value": str, "sort": int, "active": (bool, int)},
}
for _kind in ("categories", "brands", "phone_brands"):
    _ACTION_RULES[f"{_kind}.create"] = ({"name": str}, {"sort": (int, type(None))})
    _ACTION_RULES[f"{_kind}.update"] = ({"id": int, "fields": Mapping}, {})
    _ACTION_RULES[f"{_kind}.delete"] = ({"id": int}, {})
    _ACTION_RULES[f"{_kind}.sort"] = ({"ids": "int_list"}, {})
    _ACTION_RULES[f"{_kind}.list"] = ({}, {"all": (bool, int), "category_id": (int, type(None))})


def _valid_type(value, expected):
    if expected == "int_list":
        return isinstance(value, list) and all(
            isinstance(item, int) and not isinstance(item, bool) for item in value
        )
    if isinstance(value, bool):
        if expected is bool:
            return True
        return isinstance(expected, tuple) and bool in expected
    if expected is int:
        return isinstance(value, int)
    return isinstance(value, expected)


def _validate_action(action, payload):
    if not isinstance(action, str) or action not in _ACTION_RULES:
        raise ValidationError("不支援的設定操作")
    if not isinstance(payload, Mapping):
        raise ValidationError("設定操作資料格式不正確")
    required, optional = _ACTION_RULES[action]
    for key, expected in required.items():
        if key not in payload or not _valid_type(payload[key], expected):
            raise ValidationError(f"欄位 {key} 格式不正確")
    for key, value in payload.items():
        if key not in required and key not in optional:
            raise ValidationError(f"不支援欄位：{key}")
        expected = required.get(key, optional.get(key))
        if not _valid_type(value, expected):
            raise ValidationError(f"欄位 {key} 格式不正確")
    if action in _UPDATE_FIELD_RULES:
        fields = payload["fields"]
        rules = _UPDATE_FIELD_RULES[action]
        for key, value in fields.items():
            if key not in rules or not _valid_type(value, rules.get(key)):
                raise ValidationError(f"欄位 fields.{key} 格式不正確")


class SettingsRepository:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, args=()):
        return self.connection.execute(sql, args)

    def rows(self, sql, args=()):
        return [dict(row) for row in self.execute(sql, args)]

    def one(self, sql, args=()):
        return self.execute(sql, args).fetchone()

    def require(self, table, column, value, message):
        if not self.one(f"SELECT 1 FROM {table} WHERE {column}=?", (value,)):
            raise NotFoundError(message)

    def replace_links(self, table, owner_col, owner_id, value_col, values, message):
        self.execute(f"DELETE FROM {table} WHERE {owner_col}=?", (owner_id,))
        try:
            self.connection.executemany(
                f"INSERT INTO {table}({owner_col},{value_col}) VALUES(?,?)",
                [(owner_id, value) for value in values],
            )
        except sqlite3.IntegrityError as exc:
            raise ValidationError(message) from exc


class SettingsService:
    SIMPLE = {
        "categories": ("Category", "category_id", "category_id", "查無此種類"),
        "brands": ("Brand", "brand_id", "brand_id", "查無此廠牌"),
        "phone_brands": ("PhoneBrand", "phone_brand_id", "phone_brand_id", "查無此手機品牌"),
    }

    def __init__(self, repository):
        self.repo = repository

    def simple_list(self, kind, all=False):
        table, id_col, _, _ = self.SIMPLE[kind]
        where = "" if all else " WHERE active=1"
        return self.repo.rows(f"SELECT * FROM {table}{where} ORDER BY sort,{id_col}")

    def simple_create(self, kind, payload):
        table, _, result_key, _ = self.SIMPLE[kind]
        sort = payload.get("sort")
        if sort is None:
            sort = next_sort(self.repo.connection, table)
        cur = self.repo.execute(f"INSERT INTO {table}(name,sort) VALUES(?,?)", (payload["name"], sort))
        return {result_key: cur.lastrowid}

    def simple_update(self, kind, item_id, fields):
        table, id_col, _, message = self.SIMPLE[kind]
        self.repo.require(table, id_col, item_id, message)
        fields = {k: v for k, v in fields.items() if k in {"name", "sort", "active"}}
        if fields:
            sets = ",".join(f"{key}=?" for key in fields)
            self.repo.execute(f"UPDATE {table} SET {sets} WHERE {id_col}=?", (*fields.values(), item_id))
        return {"ok": True}

    def simple_delete(self, kind, item_id):
        table, id_col, _, message = self.SIMPLE[kind]
        self.repo.require(table, id_col, item_id, message)
        refs = {
            "categories": [("Product", "category_id", "仍有商品屬於此種類,無法刪除,請改用停用")],
            "brands": [("Product", "brand_id", "仍有商品屬於此廠牌,無法刪除,請改用停用")],
            "phone_brands": [("PhoneModel", "phone_brand_id", "仍有型號屬於此手機品牌,無法刪除,請改用停用")],
        }[kind]
        for ref_table, ref_col, ref_message in refs:
            if self.repo.one(f"SELECT 1 FROM {ref_table} WHERE {ref_col}=? LIMIT 1", (item_id,)):
                raise ConflictError(ref_message)
        if kind == "categories":
            self.repo.execute("DELETE FROM CategoryField WHERE category_id=?", (item_id,))
            self.repo.execute("DELETE FROM BrandCategory WHERE category_id=?", (item_id,))
            self.repo.execute("UPDATE AttributeField SET default_option_id=NULL WHERE category_id=?", (item_id,))
            self.repo.execute("DELETE FROM AttributeOption WHERE field_id IN (SELECT field_id FROM AttributeField WHERE category_id=?)", (item_id,))
            self.repo.execute("DELETE FROM AttributeField WHERE category_id=?", (item_id,))
        elif kind == "brands":
            self.repo.execute("DELETE FROM BrandCategory WHERE brand_id=?", (item_id,))
        self.repo.execute(f"DELETE FROM {table} WHERE {id_col}=?", (item_id,))
        return {"ok": True}

    def resort(self, kind, ids):
        table, id_col, _, _ = self.SIMPLE.get(kind, ("PhoneModel", "model_id", None, None))
        for item_id in ids:
            if not self.repo.one(f"SELECT 1 FROM {table} WHERE {id_col}=?", (item_id,)):
                raise ValidationError(f"查無 id={item_id}")
        for sort, item_id in enumerate(ids, 1):
            self.repo.execute(f"UPDATE {table} SET sort=? WHERE {id_col}=?", (sort, item_id))
        return {"ok": True}

    def list_brands(self, all=False, category_id=None):
        if category_id is None:
            return self.simple_list("brands", all)
        return self.repo.rows("SELECT b.* FROM Brand b JOIN BrandCategory bc ON b.brand_id=bc.brand_id WHERE bc.category_id=? AND b.active=1 ORDER BY b.sort,b.brand_id", (category_id,))

    def set_brand_categories(self, item_id, ids):
        self.repo.require("Brand", "brand_id", item_id, "查無此廠牌")
        self.repo.replace_links("BrandCategory", "brand_id", item_id, "category_id", ids, "種類不存在")
        return {"ok": True}

    def list_models(self, all=False, phone_brand_id=None):
        sql = "SELECT m.model_id,m.phone_brand_id,m.name,m.alias,m.series,m.sort,m.active,pb.name AS brand_name FROM PhoneModel m JOIN PhoneBrand pb ON m.phone_brand_id=pb.phone_brand_id"
        clauses, args = [], []
        if not all: clauses += ["m.active=1", "pb.active=1"]
        if phone_brand_id is not None: clauses.append("m.phone_brand_id=?"); args.append(phone_brand_id)
        if clauses: sql += " WHERE " + " AND ".join(clauses)
        return self.repo.rows(sql + " ORDER BY pb.sort,m.sort,m.model_id", args)

    def create_model(self, p):
        if not self.repo.one("SELECT 1 FROM PhoneBrand WHERE phone_brand_id=?", (p["phone_brand_id"],)):
            raise ValidationError("手機品牌不存在")
        sort = p.get("sort") if p.get("sort") is not None else next_sort(self.repo.connection, "PhoneModel")
        cur = self.repo.execute("INSERT INTO PhoneModel(phone_brand_id,name,alias,series,sort) VALUES(?,?,?,?,?)", (p["phone_brand_id"], p["name"], p.get("alias"), (p.get("series") or "").strip() or None, sort))
        return {"model_id": cur.lastrowid}

    def update_model(self, item_id, fields):
        self.repo.require("PhoneModel", "model_id", item_id, "查無此型號")
        fields = {k:v for k,v in fields.items() if k in {"phone_brand_id","name","alias","series","sort","active"}}
        if "series" in fields: fields["series"] = (fields["series"] or "").strip() or None
        if fields:
            self.repo.execute("UPDATE PhoneModel SET " + ",".join(f"{k}=?" for k in fields) + " WHERE model_id=?", (*fields.values(), item_id))
        return {"ok": True}

    def delete_model(self, item_id):
        self.repo.require("PhoneModel", "model_id", item_id, "查無此型號")
        for table, msg in (("VariantModel", "仍有商品掛此型號,無法刪除,請改用停用"), ("OptionModel", "仍有選項限定此型號,無法刪除,請改用停用")):
            if self.repo.one(f"SELECT 1 FROM {table} WHERE model_id=?", (item_id,)): raise ConflictError(msg)
        self.repo.execute("DELETE FROM PhoneModel WHERE model_id=?", (item_id,)); return {"ok": True}

    def list_fields(self, category_id=None, common=False):
        sql, args = "SELECT * FROM AttributeField WHERE active=1", []
        if common: sql += " AND category_id IS NULL"
        elif category_id is not None: sql += " AND category_id=?"; args.append(category_id)
        return self.repo.rows(sql + " ORDER BY sort,field_id", args)

    def create_field(self, p):
        if p.get("field_type", "select") not in FIELD_TYPES: raise ValidationError("不支援的規格欄類型")
        if p.get("default_option_id") is not None: raise ValidationError("新建規格欄不可設定預設選項")
        if p.get("category_id") is not None: self.repo.require("Category", "category_id", p["category_id"], "查無此種類")
        cur = self.repo.execute("INSERT INTO AttributeField(name,category_id,field_type,sort) VALUES(?,?,?,?)", (p["name"], p.get("category_id"), p.get("field_type", "select"), next_sort(self.repo.connection, "AttributeField")))
        return {"field_id": cur.lastrowid}

    def update_field(self, item_id, fields):
        self.repo.require("AttributeField", "field_id", item_id, "查無此規格欄")
        fields = {k:v for k,v in fields.items() if k in {"name","sort","active","field_type","default_option_id"}}
        if "field_type" in fields and fields["field_type"] not in FIELD_TYPES: raise ValidationError("不支援的規格欄類型")
        if "default_option_id" in fields and fields["default_option_id"] is not None:
            row = self.repo.one("SELECT field_id FROM AttributeOption WHERE option_id=?", (fields["default_option_id"],))
            if row is None or row[0] != item_id: raise ValidationError("預設選項不屬於此規格欄")
        if fields: self.repo.execute("UPDATE AttributeField SET " + ",".join(f"{k}=?" for k in fields) + " WHERE field_id=?", (*fields.values(), item_id))
        return {"ok": True}

    def list_options(self, field_id, all=False, model_ids=None):
        sql = "SELECT o.*,COUNT(DISTINCT va.variant_id) usage_count FROM AttributeOption o LEFT JOIN VariantAttribute va ON va.option_id=o.option_id WHERE o.field_id=?"; args=[field_id]
        if not all: sql += " AND o.active=1"
        if model_ids:
            qs=in_clause(model_ids); sql += f" AND (NOT EXISTS(SELECT 1 FROM OptionModel om WHERE om.option_id=o.option_id) OR EXISTS(SELECT 1 FROM OptionModel om WHERE om.option_id=o.option_id AND om.model_id IN ({qs})))"; args += model_ids
        opts=self.repo.rows(sql+" GROUP BY o.option_id ORDER BY o.sort,o.option_id",args)
        for o in opts: o["model_ids"]=[r[0] for r in self.repo.execute("SELECT model_id FROM OptionModel WHERE option_id=? ORDER BY model_id",(o["option_id"],))]
        return opts

    def create_option(self,p):
        self.repo.require("AttributeField","field_id",p["field_id"],"查無此規格欄")
        sort=next_sort(self.repo.connection,"AttributeOption","field_id=?",(p["field_id"],)); self.repo.execute("INSERT OR IGNORE INTO AttributeOption(field_id,value,sort) VALUES(?,?,?)",(p["field_id"],p["value"],sort))
        if p.get("reactivate"): self.repo.execute("UPDATE AttributeOption SET active=1 WHERE field_id=? AND value=?",(p["field_id"],p["value"]))
        return {"ok":True}

    def update_option(self,item_id,fields):
        self.repo.require("AttributeOption","option_id",item_id,"查無此選項")
        fields={k:v for k,v in fields.items() if k in {"value","sort","active"} and v is not None}
        try:
            if fields:self.repo.execute("UPDATE AttributeOption SET "+",".join(f"{k}=?" for k in fields)+" WHERE option_id=?",(*fields.values(),item_id))
        except sqlite3.IntegrityError as exc: raise ConflictError("此選項值已存在") from exc
        return {"ok":True}

    def delete_option(self,item_id):
        self.repo.require("AttributeOption","option_id",item_id,"查無此選項"); count=self.repo.one("SELECT COUNT(DISTINCT variant_id) FROM VariantAttribute WHERE option_id=?",(item_id,))[0]
        self.repo.execute("UPDATE AttributeField SET default_option_id=NULL WHERE default_option_id=?",(item_id,)); self.repo.execute("DELETE FROM OptionModel WHERE option_id=?",(item_id,))
        self.repo.execute("UPDATE AttributeOption SET active=0 WHERE option_id=?" if count else "DELETE FROM AttributeOption WHERE option_id=?",(item_id,)); return {"ok":True,"deleted":not bool(count)}

    def option_models(self,item_id):
        self.repo.require("AttributeOption","option_id",item_id,"查無此選項"); return {"model_ids":[r[0] for r in self.repo.execute("SELECT model_id FROM OptionModel WHERE option_id=? ORDER BY model_id",(item_id,))]}

    def set_option_models(self,item_id,ids):
        self.repo.require("AttributeOption","option_id",item_id,"查無此選項"); self.repo.replace_links("OptionModel","option_id",item_id,"model_id",ids,"型號不存在"); return {"ok":True}

    def category_fields(self,cid):
        self.repo.require("Category","category_id",cid,"查無此種類")
        rows=self.repo.rows("SELECT field_id,name,field_type,default_option_id,sort,category_id FROM AttributeField WHERE active=1 AND (category_id=? OR field_id IN (SELECT field_id FROM CategoryField WHERE category_id=?)) ORDER BY (category_id IS NULL),sort,field_id",(cid,cid)); out=[]
        for f in rows:
            opts=[] if f["field_type"]=="text" else self.repo.rows("SELECT option_id,value,sort FROM AttributeOption WHERE field_id=? AND active=1 ORDER BY sort,option_id",(f["field_id"],)); dv=self.repo.one("SELECT value FROM AttributeOption WHERE option_id=?",(f["default_option_id"],)) if f["default_option_id"] is not None else None
            out.append({"field_id":f["field_id"],"name":f["name"],"field_type":f["field_type"],"default_option_id":f["default_option_id"],"default_value":dv[0] if dv else None,"shared":f["category_id"] is None,"options":opts})
        return out

    def set_category_common_fields(self, cid, ids):
        self.repo.require("Category", "category_id", cid, "查無此種類")
        self.repo.replace_links(
            "CategoryField", "category_id", cid, "field_id", ids, "規格欄不存在"
        )
        return {"ok": True}


class SettingsFacade:
    def __init__(self, db_path): self.runner=TransactionRunner(db_path, connection_context=db_conn)
    def invoke(self, action, payload=None):
        payload = {} if payload is None else payload
        _validate_action(action, payload)
        def work(conn):
            s=SettingsService(SettingsRepository(conn)); parts=action.split("."); kind=parts[0]; op=parts[1] if len(parts)==2 else ""
            simple={"categories":"categories","brands":"brands","phone_brands":"phone_brands"}
            if kind in simple and op in {"list","create","update","delete","sort"}:
                if kind=="brands" and op=="list": return s.list_brands(bool(payload.get("all")),payload.get("category_id"))
                if op=="list": return s.simple_list(simple[kind],bool(payload.get("all")))
                if op=="create": return s.simple_create(simple[kind],payload)
                if op=="update": return s.simple_update(simple[kind],payload["id"],payload.get("fields",{}))
                if op=="delete": return s.simple_delete(simple[kind],payload["id"])
                return s.resort(simple[kind],payload["ids"])
            handlers={
                "brands.set_categories":lambda:s.set_brand_categories(payload["id"],payload.get("category_ids",[])),"models.list":lambda:s.list_models(bool(payload.get("all")),payload.get("phone_brand_id")),"models.create":lambda:s.create_model(payload),"models.update":lambda:s.update_model(payload["id"],payload.get("fields",{})),"models.delete":lambda:s.delete_model(payload["id"]),"models.sort":lambda:s.resort("models",payload["ids"]),"fields.list":lambda:s.list_fields(payload.get("category_id"),bool(payload.get("common"))),"fields.create":lambda:s.create_field(payload),"fields.update":lambda:s.update_field(payload["id"],payload.get("fields",{})),"options.list":lambda:s.list_options(payload["field_id"],bool(payload.get("all")),payload.get("model_ids",[])),"options.create":lambda:s.create_option(payload),"options.update":lambda:s.update_option(payload["id"],payload.get("fields",{})),"options.delete":lambda:s.delete_option(payload["id"]),"options.models":lambda:s.option_models(payload["id"]),"options.set_models":lambda:s.set_option_models(payload["id"],payload.get("model_ids",[])),"categories.fields":lambda:s.category_fields(payload["id"]),"categories.set_common_fields":lambda:s.set_category_common_fields(payload["id"],payload.get("field_ids",[])),}
            if action not in handlers: raise ValidationError("不支援的設定操作")
            return handlers[action]()
        return self.runner.run(work)
