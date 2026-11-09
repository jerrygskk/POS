import sqlite3
from collections.abc import Mapping

from lib.application import BaseFacade, BaseRepository
from lib.application_errors import ConflictError, NotFoundError, ValidationError
from lib.db import in_clause, next_sort
from lib import product_data, product_rules
from lib.product_rules import FIELD_TYPES


_ACTION_RULES = {
    **{f"{kind}.{op}": ({}, {}) for kind in ("categories", "brands", "phone_brands", "device_types") for op in ("list", "create", "update", "delete", "sort")},
    "brands.set_categories": ({"id": int, "category_ids": "int_list"}, {}),
    "models.list": ({}, {"all": (bool, int), "phone_brand_id": (int, type(None))}),
    "models.create": ({"phone_brand_id": int, "device_type_id": int, "name": str}, {"alias": (str, type(None)), "sort": (int, type(None))}),
    "models.update": ({"id": int, "fields": Mapping}, {}),
    "models.delete": ({"id": int}, {}),
    "models.sort": ({"ids": "int_list"}, {}),
    "fields.list": ({}, {"category_id": (int, type(None))}),
    "fields.create": ({"name": str, "category_id": int}, {"field_type": str}),
    "fields.update": ({"id": int, "fields": Mapping}, {}),
    "options.list": ({"field_id": int}, {"all": (bool, int), "model_ids": "int_list"}),
    "options.create": ({"field_id": int, "value": str}, {"reactivate": bool}),
    "options.update": ({"id": int, "fields": Mapping}, {}),
    "options.delete": ({"id": int}, {}),
    "options.models": ({"id": int}, {}),
    "options.set_models": ({"id": int, "model_ids": "int_list"}, {}),
    "options.cleanup": ({}, {"field_id": (int, type(None))}),
    "categories.fields": ({"id": int}, {}),
    "categories.set_field": ({"category_id": int, "field_id": int, "fields": Mapping}, {}),
    "categories.delete_field": ({"category_id": int, "field_id": int}, {}),
}

_UPDATE_FIELD_RULES = {
    "categories.update": {"name": str, "sort": int, "active": (bool, int), "model_mode": str},
    "brands.update": {"name": str, "sort": int, "active": (bool, int)},
    "phone_brands.update": {"name": str, "sort": int, "active": (bool, int)},
    "device_types.update": {"name": str, "sort": int, "active": (bool, int)},
    "models.update": {
        "phone_brand_id": int,
        "device_type_id": int,
        "name": str,
        "alias": (str, type(None)),
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
    "categories.set_field": {
        "sort": int,
        "required": (bool, int),
        "default_option_id": (int, type(None)),
        "active": (bool, int),
    },
}
for _kind in ("categories", "brands", "phone_brands", "device_types"):
    _ACTION_RULES[f"{_kind}.create"] = ({"name": str}, {"sort": (int, type(None))})
    _ACTION_RULES[f"{_kind}.update"] = ({"id": int, "fields": Mapping}, {})
    _ACTION_RULES[f"{_kind}.delete"] = ({"id": int}, {})
    _ACTION_RULES[f"{_kind}.sort"] = ({"ids": "int_list"}, {})
    _ACTION_RULES[f"{_kind}.list"] = ({}, {"all": (bool, int), "category_id": (int, type(None))})
# 廠牌清單另支援 with_products:只回在該範圍真的有商品的廠牌(商品資料庫篩選列用)
_ACTION_RULES["brands.list"] = ({}, {"all": (bool, int), "category_id": (int, type(None)),
                                     "with_products": (bool, int)})
# 種類另支援 model_mode(適用型號模式)讀寫
_ACTION_RULES["categories.create"] = ({"name": str}, {"sort": (int, type(None)), "model_mode": (str, type(None))})

MODEL_MODES = ("required", "hidden")


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


class SettingsRepository(BaseRepository):
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
        "phone_brands": ("PhoneBrand", "phone_brand_id", "phone_brand_id", "查無此機型品牌"),
        "device_types": ("DeviceType", "device_type_id", "device_type_id", "查無此裝置類型"),
    }
    NO_ACTIVE = {"brands"}  # 廠牌無 active,不參與營運狀態

    def __init__(self, repository):
        self.repo = repository

    def simple_list(self, kind, all=False):
        table, id_col, _, _ = self.SIMPLE[kind]
        where = "" if all or kind in self.NO_ACTIVE else " WHERE active=1"
        return self.repo.rows(f"SELECT * FROM {table}{where} ORDER BY sort,{id_col}")

    def simple_create(self, kind, payload):
        table, _, result_key, _ = self.SIMPLE[kind]
        sort = payload.get("sort")
        if sort is None:
            sort = next_sort(self.repo.connection, table)
        if kind == "categories":
            mode = payload.get("model_mode") or "hidden"
            if mode not in MODEL_MODES:
                raise ValidationError("適用型號模式不正確")
            cur = self.repo.execute("INSERT INTO Category(name,sort,model_mode) VALUES(?,?,?)",
                                    (payload["name"], sort, mode))
        else:
            cur = self.repo.execute(f"INSERT INTO {table}(name,sort) VALUES(?,?)", (payload["name"], sort))
        return {result_key: cur.lastrowid}

    def simple_update(self, kind, item_id, fields):
        table, id_col, _, message = self.SIMPLE[kind]
        self.repo.require(table, id_col, item_id, message)
        allowed = {"name", "sort"} if kind in self.NO_ACTIVE else {"name", "sort", "active"}
        if kind == "categories":
            allowed = allowed | {"model_mode"}
            if fields.get("model_mode") is not None and fields["model_mode"] not in MODEL_MODES:
                raise ValidationError("適用型號模式不正確")
        fields = {k: v for k, v in fields.items() if k in allowed}
        if fields:
            sets = ",".join(f"{key}=?" for key in fields)
            self.repo.execute(f"UPDATE {table} SET {sets} WHERE {id_col}=?", (*fields.values(), item_id))
        return {"ok": True}

    def simple_delete(self, kind, item_id):
        table, id_col, _, message = self.SIMPLE[kind]
        self.repo.require(table, id_col, item_id, message)
        refs = {
            "categories": [("Product", "category_id", "仍有商品屬於此種類，無法刪除，請改用停用")],
            "brands": [("Product", "brand_id", "仍有商品屬於此廠牌，無法刪除，請改用停用")],
            "phone_brands": [("PhoneModel", "phone_brand_id", "仍有型號屬於此機型品牌，無法刪除，請改用停用")],
            "device_types": [("PhoneModel", "device_type_id", "仍有型號屬於此裝置類型，無法刪除，請改用停用")],
        }[kind]
        for ref_table, ref_col, ref_message in refs:
            if self.repo.one(f"SELECT 1 FROM {ref_table} WHERE {ref_col}=? LIMIT 1", (item_id,)):
                raise ConflictError(ref_message)
        if kind == "categories":
            # 規格欄隸屬種類;種類已無商品,欄位、選項一起清(預設選項先放掉,否則複合外鍵擋刪選項)
            fields = "SELECT field_id FROM AttributeField WHERE category_id=?"
            self.repo.execute("DELETE FROM BrandCategory WHERE category_id=?", (item_id,))
            self.repo.execute("UPDATE AttributeField SET default_option_id=NULL WHERE category_id=?", (item_id,))
            self.repo.execute("DELETE FROM OptionModel WHERE option_id IN "
                              f"(SELECT option_id FROM AttributeOption WHERE field_id IN ({fields}))", (item_id,))
            self.repo.execute(f"DELETE FROM AttributeOption WHERE field_id IN ({fields})", (item_id,))
            self.repo.execute(f"DELETE FROM VariantIssue WHERE field_id IN ({fields})", (item_id,))
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

    def list_brands(self, all=False, category_id=None, with_products=False):
        # with_products:只回真的有商品的廠牌。商品資料庫的篩選列用——掛了種類卻一件
        # 商品都沒有的廠牌選了只會查無資料,列出來只是讓店員多按一次。
        if with_products:
            sql = ("SELECT DISTINCT b.* FROM Brand b JOIN Product p ON p.brand_id=b.brand_id"
                   + (" WHERE p.category_id=?" if category_id is not None else "")
                   + " ORDER BY b.sort,b.brand_id")
            return self.repo.rows(sql, (category_id,) if category_id is not None else ())
        if category_id is None:
            return self.simple_list("brands", all)
        return self.repo.rows("SELECT b.* FROM Brand b JOIN BrandCategory bc ON b.brand_id=bc.brand_id WHERE bc.category_id=? ORDER BY b.sort,b.brand_id", (category_id,))

    def set_brand_categories(self, item_id, ids):
        self.repo.require("Brand", "brand_id", item_id, "查無此廠牌")
        self.repo.replace_links("BrandCategory", "brand_id", item_id, "category_id", ids, "種類不存在")
        return {"ok": True}

    def list_models(self, all=False, phone_brand_id=None):
        sql = ("SELECT m.model_id,m.phone_brand_id,m.device_type_id,m.name,m.alias,m.sort,m.active,"
               "pb.name AS brand_name,dt.name AS device_type_name FROM PhoneModel m "
               "JOIN PhoneBrand pb ON m.phone_brand_id=pb.phone_brand_id "
               "JOIN DeviceType dt ON m.device_type_id=dt.device_type_id")
        clauses, args = [], []
        if not all: clauses += ["m.active=1", "pb.active=1", "dt.active=1"]
        if phone_brand_id is not None: clauses.append("m.phone_brand_id=?"); args.append(phone_brand_id)
        if clauses: sql += " WHERE " + " AND ".join(clauses)
        # 排序值相同(匯入建的品牌都是 0)時以建立先後為第二順序,同品牌型號才不會被拆成好幾段
        return self.repo.rows(sql + " ORDER BY pb.sort,pb.phone_brand_id,dt.sort,dt.device_type_id,m.sort,m.model_id", args)

    def _check_model_refs(self, fields):
        if "phone_brand_id" in fields and not self.repo.one("SELECT 1 FROM PhoneBrand WHERE phone_brand_id=?", (fields["phone_brand_id"],)):
            raise ValidationError("機型品牌不存在")
        if "device_type_id" in fields and not self.repo.one("SELECT 1 FROM DeviceType WHERE device_type_id=?", (fields["device_type_id"],)):
            raise ValidationError("裝置類型不存在")

    def _write_model(self, sql, args):
        # 同品牌、同裝置類型不可同名;不同類型可同名(手機與平板可能都叫 Pro)
        try:
            return self.repo.execute(sql, args)
        except sqlite3.IntegrityError as exc:
            raise ConflictError("同品牌、同裝置類型已有此型號") from exc

    def create_model(self, p):
        self._check_model_refs(p)
        sort = p.get("sort") if p.get("sort") is not None else next_sort(self.repo.connection, "PhoneModel")
        cur = self._write_model("INSERT INTO PhoneModel(phone_brand_id,device_type_id,name,alias,sort) VALUES(?,?,?,?,?)",
                                (p["phone_brand_id"], p["device_type_id"], p["name"], p.get("alias"), sort))
        return {"model_id": cur.lastrowid}

    def update_model(self, item_id, fields):
        self.repo.require("PhoneModel", "model_id", item_id, "查無此型號")
        fields = {k:v for k,v in fields.items() if k in {"phone_brand_id","device_type_id","name","alias","sort","active"}}
        self._check_model_refs(fields)
        if fields:
            self._write_model("UPDATE PhoneModel SET " + ",".join(f"{k}=?" for k in fields) + " WHERE model_id=?", (*fields.values(), item_id))
        return {"ok": True}

    def delete_model(self, item_id):
        self.repo.require("PhoneModel", "model_id", item_id, "查無此型號")
        for table, msg in (("VariantModel", "仍有商品掛此型號，無法刪除，請改用停用"), ("OptionModel", "仍有選項限定此型號，無法刪除，請改用停用")):
            if self.repo.one(f"SELECT 1 FROM {table} WHERE model_id=?", (item_id,)): raise ConflictError(msg)
        self.repo.execute("DELETE FROM PhoneModel WHERE model_id=?", (item_id,)); return {"ok": True}

    def _require_category_field(self, category_id, field_id):
        self.repo.require("Category", "category_id", category_id, "查無此種類")
        self.repo.require("AttributeField", "field_id", field_id, "查無此規格欄")
        if not self.repo.one("SELECT 1 FROM AttributeField WHERE field_id=? AND category_id=?",
                             (field_id, category_id)):
            raise NotFoundError("此種類未使用此規格欄")

    def _check_default(self, field_id, option_id):
        if option_id is None:
            return
        row = self.repo.one("SELECT o.field_id, f.field_type FROM AttributeOption o "
                            "JOIN AttributeField f ON f.field_id=o.field_id WHERE o.option_id=?", (option_id,))
        if row is None or row[0] != field_id:
            raise ValidationError("預設選項不屬於此規格欄")
        if row[1] != "select":
            raise ValidationError("只有單選規格可設定預設選項")

    def list_fields(self, category_id=None):
        # 指定種類:回該種類全部規格欄(含停用),供設定頁就地編輯
        if category_id is not None:
            rows = self.repo.rows(
                "SELECT field_id,category_id,name,field_type,active,sort,required,default_option_id "
                "FROM AttributeField WHERE category_id=? ORDER BY sort,field_id", (category_id,))
            # 附上該種類已有多少子產品填過此規格:刪除確認視窗要把影響講明白
            used = {r["field_id"]: r["c"] for r in self.repo.execute(
                "SELECT va.field_id, COUNT(DISTINCT va.variant_id) c "
                "FROM VariantAttribute va JOIN Variant v ON v.variant_id=va.variant_id "
                "JOIN Product p ON p.product_id=v.product_id "
                "WHERE p.category_id=? GROUP BY va.field_id", (category_id,))}
            for r in rows:
                r["cat_usage_count"] = used.get(r["field_id"], 0)
            return rows
        return self.repo.rows(
            "SELECT field_id,category_id,name,field_type,active FROM AttributeField "
            "WHERE active=1 ORDER BY category_id,sort,field_id")

    def create_field(self, p):
        field_type = p.get("field_type", "select")
        if field_type not in FIELD_TYPES: raise ValidationError("不支援的規格欄類型")
        self.repo.require("Category", "category_id", p["category_id"], "查無此種類")
        product_data.check_field_name(self.repo.connection, p["category_id"], p["name"])
        sort = next_sort(self.repo.connection, "AttributeField", "category_id=?", (p["category_id"],))
        cur = self.repo.execute(
            "INSERT INTO AttributeField(category_id,name,field_type,sort) VALUES(?,?,?,?)",
            (p["category_id"], p["name"], field_type, sort))
        return {"field_id": cur.lastrowid}

    def update_field(self, item_id, fields):
        row = self.repo.one("SELECT category_id,field_type FROM AttributeField WHERE field_id=?", (item_id,))
        if row is None:
            raise NotFoundError("查無此規格欄")
        fields = {k: v for k, v in fields.items()
                  if k in {"name", "sort", "active", "field_type", "default_option_id"}}
        if "name" in fields:
            product_data.check_field_name(self.repo.connection, row["category_id"], fields["name"],
                                          exclude_field_id=item_id)
        if "field_type" in fields:
            if fields["field_type"] not in FIELD_TYPES: raise ValidationError("不支援的規格欄類型")
            # 欄位已被使用鎖型態
            if (fields["field_type"] != row["field_type"]
                    and self.repo.one("SELECT 1 FROM VariantAttribute WHERE field_id=? LIMIT 1", (item_id,))):
                raise ValidationError("欄位已被使用，不可變更型態")
            if fields["field_type"] != "select":
                fields["default_option_id"] = None  # 預設選項只限單選
        if fields.get("default_option_id") is not None:
            if fields.get("field_type", row["field_type"]) != "select":
                raise ValidationError("只有單選規格可設定預設選項")
            self._check_default(item_id, fields["default_option_id"])
        if fields:
            self.repo.execute("UPDATE AttributeField SET " + ",".join(f"{k}=?" for k in fields) +
                              " WHERE field_id=?", (*fields.values(), item_id))
        return {"ok": True}

    def list_options(self, field_id, all=False, model_ids=None):
        sql = "SELECT o.*,COUNT(DISTINCT va.variant_id) usage_count FROM AttributeOption o LEFT JOIN VariantAttribute va ON va.option_id=o.option_id WHERE o.field_id=?"; args=[field_id]
        if not all: sql += " AND o.active=1"
        if model_ids:
            qs=in_clause(model_ids); sql += f" AND (NOT EXISTS(SELECT 1 FROM OptionModel om WHERE om.option_id=o.option_id) OR EXISTS(SELECT 1 FROM OptionModel om WHERE om.option_id=o.option_id AND om.model_id IN ({qs})))"; args += model_ids
        opts=self.repo.rows(sql+" GROUP BY o.option_id ORDER BY usage_count DESC,o.sort,o.option_id",args)
        for o in opts: o["model_ids"]=[r[0] for r in self.repo.execute("SELECT model_id FROM OptionModel WHERE option_id=? ORDER BY model_id",(o["option_id"],))]
        # 玻璃貼材質等固定次序的值排最前(product_rules.PINNED_OPTION_VALUES)
        return product_rules.sort_pinned_options(opts)

    def create_option(self,p):
        self.repo.require("AttributeField","field_id",p["field_id"],"查無此規格欄")
        sort=next_sort(self.repo.connection,"AttributeOption","field_id=?",(p["field_id"],)); self.repo.execute("INSERT OR IGNORE INTO AttributeOption(field_id,value,sort) VALUES(?,?,?)",(p["field_id"],p["value"],sort))
        if p.get("reactivate"): self.repo.execute("UPDATE AttributeOption SET active=1 WHERE field_id=? AND value=?",(p["field_id"],p["value"]))
        return {"ok":True}

    def update_option(self,item_id,fields):
        self.repo.require("AttributeOption","option_id",item_id,"查無此選項")
        fields={k:v for k,v in fields.items() if k in {"value","sort","active"} and v is not None}
        # 停用選項若為規格欄預設值,同交易清空
        if fields.get("active")==0:
            self.repo.execute("UPDATE AttributeField SET default_option_id=NULL WHERE default_option_id=?",(item_id,))
        try:
            if fields:self.repo.execute("UPDATE AttributeOption SET "+",".join(f"{k}=?" for k in fields)+" WHERE option_id=?",(*fields.values(),item_id))
        except sqlite3.IntegrityError as exc: raise ConflictError("此選項值已存在") from exc
        return {"ok":True}

    def delete_option(self,item_id):
        self.repo.require("AttributeOption","option_id",item_id,"查無此選項"); count=self.repo.one("SELECT COUNT(DISTINCT variant_id) FROM VariantAttribute WHERE option_id=?",(item_id,))[0]
        self.repo.execute("UPDATE AttributeField SET default_option_id=NULL WHERE default_option_id=?",(item_id,)); self.repo.execute("DELETE FROM OptionModel WHERE option_id=?",(item_id,))
        self.repo.execute("UPDATE AttributeOption SET active=0 WHERE option_id=?" if count else "DELETE FROM AttributeOption WHERE option_id=?",(item_id,)); return {"ok":True,"deleted":not bool(count)}

    def cleanup_options(self, field_id=None):
        """手動清理未使用選項:使用數為 0 且非任何規格欄 default_option_id 者硬刪。
        field_id 指定則限該欄,否則全域。回傳實際刪除筆數。"""
        from lib import product_data
        if field_id is not None:
            self.repo.require("AttributeField", "field_id", field_id, "查無此規格欄")
            ids = [r["option_id"] for r in self.repo.execute(
                "SELECT option_id FROM AttributeOption WHERE field_id=?", (field_id,))]
        else:
            ids = [r["option_id"] for r in self.repo.execute(
                "SELECT option_id FROM AttributeOption")]
        deleted = product_data.cleanup_unused_options(self.repo.connection, ids)
        return {"ok": True, "deleted": len(deleted)}

    def option_models(self,item_id):
        self.repo.require("AttributeOption","option_id",item_id,"查無此選項"); return {"model_ids":[r[0] for r in self.repo.execute("SELECT model_id FROM OptionModel WHERE option_id=? ORDER BY model_id",(item_id,))]}

    def set_option_models(self,item_id,ids):
        self.repo.require("AttributeOption","option_id",item_id,"查無此選項"); self.repo.replace_links("OptionModel","option_id",item_id,"model_id",ids,"型號不存在"); return {"ok":True}

    def _category_has_variant(self, category_id):
        return self.repo.one(
            "SELECT 1 FROM Variant v JOIN Product p ON v.product_id=p.product_id "
            "WHERE p.category_id=? LIMIT 1", (category_id,)) is not None

    def category_fields(self,cid):
        self.repo.require("Category","category_id",cid,"查無此種類")
        # 建檔用:本種類啟用中的規格欄與啟用選項
        rows=self.repo.rows(
            "SELECT field_id,name,field_type,sort,required,default_option_id "
            "FROM AttributeField WHERE category_id=? AND active=1 ORDER BY sort,field_id",(cid,))
        out=[]
        for f in rows:
            opts=[] if f["field_type"]=="text" else self.repo.rows(
                "SELECT o.option_id,o.value,o.sort FROM AttributeOption o "
                "LEFT JOIN VariantAttribute va ON va.option_id=o.option_id "
                "WHERE o.field_id=? AND o.active=1 GROUP BY o.option_id "
                "ORDER BY COUNT(DISTINCT va.variant_id) DESC,o.sort,o.option_id",(f["field_id"],)); dv=self.repo.one("SELECT value FROM AttributeOption WHERE option_id=?",(f["default_option_id"],)) if f["default_option_id"] is not None else None
            out.append({"field_id":f["field_id"],"name":f["name"],"field_type":f["field_type"],"required":f["required"],"default_option_id":f["default_option_id"],"default_value":dv[0] if dv else None,"options":opts})
        return out

    def set_field(self, category_id, field_id, fields):
        """本種類規格欄的排序／必填／預設帶入／啟用。"""
        self._require_category_field(category_id, field_id)
        cur = dict(self.repo.one(
            "SELECT sort,required,default_option_id,active FROM AttributeField WHERE field_id=?", (field_id,)))
        new = dict(cur)
        for k in ("sort", "required", "active"):
            if k in fields and fields[k] is not None:
                new[k] = int(fields[k])
        # 預設帶入值允許送 None 清除(其餘欄位 None＝不動)
        if "default_option_id" in fields:
            new["default_option_id"] = fields["default_option_id"]
            self._check_default(field_id, new["default_option_id"])
        self.repo.execute(
            "UPDATE AttributeField SET sort=?,required=?,default_option_id=?,active=? WHERE field_id=?",
            (new["sort"], new["required"], new["default_option_id"], new["active"], field_id))
        if int(new["required"]) != int(cur["required"]):
            self._sync_required_issues(category_id, field_id, int(new["required"]))
        return {"ok": True}

    def _sync_required_issues(self, category_id, field_id, required):
        """必填切換只往前生效:既有子產品不停用、不強制補值,改列入待補清單。

        改成必填→該種類目前缺此欄值的子產品補一筆 missing_required;
        改回選填→把此欄的 missing_required 清掉。缺值定義同 VariantIssue:
        沒有該欄的值,或文字欄只填空白。"""
        if not required:
            self.repo.execute(
                "DELETE FROM VariantIssue WHERE issue_type='missing_required' AND field_id=? "
                "AND variant_id IN (SELECT v.variant_id FROM Variant v "
                "JOIN Product p ON v.product_id=p.product_id WHERE p.category_id=?)",
                (field_id, category_id))
            return
        self.repo.execute(
            "INSERT INTO VariantIssue(variant_id,issue_type,field_id) "
            "SELECT v.variant_id,'missing_required',? FROM Variant v "
            "JOIN Product p ON v.product_id=p.product_id "
            "WHERE p.category_id=? "
            "AND NOT EXISTS (SELECT 1 FROM VariantAttribute va WHERE va.variant_id=v.variant_id "
            "  AND va.field_id=? AND (va.option_id IS NOT NULL OR TRIM(COALESCE(va.text_value,''))<>'')) "
            "AND NOT EXISTS (SELECT 1 FROM VariantIssue vi WHERE vi.variant_id=v.variant_id "
            "  AND vi.issue_type='missing_required' AND vi.field_id=?)",
            (field_id, category_id, field_id, field_id))

    def delete_field(self, category_id, field_id):
        """刪除本種類的規格欄:清掉填過的值、待補問題、選項與欄位本身。

        規格欄隸屬單一種類,不跨種類共用,所以沒有「只解除掛勾、保留欄位」的情況。"""
        self._require_category_field(category_id, field_id)
        removed = self.repo.one(
            "SELECT COUNT(DISTINCT variant_id) c FROM VariantAttribute WHERE field_id=?", (field_id,))[0]
        self.repo.execute("DELETE FROM VariantAttribute WHERE field_id=?", (field_id,))
        self.repo.execute("DELETE FROM VariantIssue WHERE field_id=?", (field_id,))
        self.repo.execute("UPDATE AttributeField SET default_option_id=NULL WHERE field_id=?", (field_id,))
        self.repo.execute(
            "DELETE FROM OptionModel WHERE option_id IN "
            "(SELECT option_id FROM AttributeOption WHERE field_id=?)", (field_id,))
        self.repo.execute("DELETE FROM AttributeOption WHERE field_id=?", (field_id,))
        self.repo.execute("DELETE FROM AttributeField WHERE field_id=?", (field_id,))
        return {"ok": True, "removed_variant_count": removed, "field_deleted": True}


class SettingsFacade(BaseFacade):
    ACTIONS = set(_ACTION_RULES)
    ERROR_MESSAGE = "不支援的設定操作"
    def _validate_payload_type(self, payload):
        if not isinstance(payload, Mapping):
            raise ValidationError("設定操作資料格式不正確")
    def _prepare_payload(self, action, payload):
        _validate_action(action, payload)
        return payload
    def _dispatch(self, action, payload, conn):
        # 欄名查重(正規化比對)與寫入須在同一寫入交易,否則兩個操作交錯會各自通過檢查
        if action in {"fields.create", "fields.update"}:
            conn.execute("BEGIN IMMEDIATE")
        s=SettingsService(SettingsRepository(conn)); parts=action.split("."); kind=parts[0]; op=parts[1] if len(parts)==2 else ""
        simple={"categories":"categories","brands":"brands","phone_brands":"phone_brands","device_types":"device_types"}
        if kind in simple and op in {"list","create","update","delete","sort"}:
            if kind=="brands" and op=="list": return s.list_brands(bool(payload.get("all")),payload.get("category_id"),bool(payload.get("with_products")))
            if op=="list": return s.simple_list(simple[kind],bool(payload.get("all")))
            if op=="create": return s.simple_create(simple[kind],payload)
            if op=="update": return s.simple_update(simple[kind],payload["id"],payload.get("fields",{}))
            if op=="delete": return s.simple_delete(simple[kind],payload["id"])
            return s.resort(simple[kind],payload["ids"])
        handlers={
                "brands.set_categories":lambda:s.set_brand_categories(payload["id"],payload.get("category_ids",[])),"models.list":lambda:s.list_models(bool(payload.get("all")),payload.get("phone_brand_id")),"models.create":lambda:s.create_model(payload),"models.update":lambda:s.update_model(payload["id"],payload.get("fields",{})),"models.delete":lambda:s.delete_model(payload["id"]),"models.sort":lambda:s.resort("models",payload["ids"]),"fields.list":lambda:s.list_fields(payload.get("category_id")),"fields.create":lambda:s.create_field(payload),"fields.update":lambda:s.update_field(payload["id"],payload.get("fields",{})),"options.list":lambda:s.list_options(payload["field_id"],bool(payload.get("all")),payload.get("model_ids",[])),"options.create":lambda:s.create_option(payload),"options.update":lambda:s.update_option(payload["id"],payload.get("fields",{})),"options.delete":lambda:s.delete_option(payload["id"]),"options.cleanup":lambda:s.cleanup_options(payload.get("field_id")),"options.models":lambda:s.option_models(payload["id"]),"options.set_models":lambda:s.set_option_models(payload["id"],payload.get("model_ids",[])),"categories.fields":lambda:s.category_fields(payload["id"]),"categories.set_field":lambda:s.set_field(payload["category_id"],payload["field_id"],payload.get("fields",{})),"categories.delete_field":lambda:s.delete_field(payload["category_id"],payload["field_id"]),}
        if action not in handlers: raise ValidationError("不支援的設定操作")
        return handlers[action]()
