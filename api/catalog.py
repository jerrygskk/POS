from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from lib.db import db_conn, next_sort
from lib.dbutil import (require_exists, reject_if_referenced, update_by_id,
                        replace_links)
from lib.product_rules import FIELD_TYPES

router = APIRouter(prefix="/api")

# ---- Pydantic ----

class CategoryNew(BaseModel):
    name: str
    sort: int | None = None

class CategoryPatch(BaseModel):
    name: str | None = None
    sort: int | None = None
    active: int | None = None

class BrandNew(BaseModel):
    name: str
    sort: int | None = None

class BrandPatch(BaseModel):
    name: str | None = None
    sort: int | None = None
    active: int | None = None

class PhoneBrandNew(BaseModel):
    name: str
    sort: int | None = None

class PhoneBrandPatch(BaseModel):
    name: str | None = None
    sort: int | None = None
    active: int | None = None

class ModelNew(BaseModel):
    phone_brand_id: int
    name: str
    alias: str | None = None
    series: str | None = None
    sort: int | None = None

class ModelPatch(BaseModel):
    phone_brand_id: int | None = None
    name: str | None = None
    alias: str | None = None
    series: str | None = None
    sort: int | None = None
    active: int | None = None

class SortIds(BaseModel):
    ids: list[int]

class IdList(BaseModel):
    category_ids: list[int] = []

class FieldIdList(BaseModel):
    field_ids: list[int] = []


def _resort(conn, table, id_col, ids):
    """依 ids 順序重寫 sort=1..N;任一 id 查無 → 422,整筆不寫。"""
    for i in ids:
        if not conn.execute(f"SELECT 1 FROM {table} WHERE {id_col}=?", (i,)).fetchone():
            raise HTTPException(422, f"查無 id={i}")
    for n, i in enumerate(ids, start=1):
        conn.execute(f"UPDATE {table} SET sort=? WHERE {id_col}=?", (n, i))
    conn.commit()


# ---- 排序(四張維護清單共用) ----

_SORT_TABLES = {
    "categories": ("Category", "category_id"),
    "brands": ("Brand", "brand_id"),
    "phone-brands": ("PhoneBrand", "phone_brand_id"),
    "models": ("PhoneModel", "model_id"),
}

def _make_sort_route(kind):
    table, id_col = _SORT_TABLES[kind]
    @router.put(f"/{kind}/sort")
    def sort_items(body: SortIds, request: Request):
        with db_conn(request.app.state.db_path) as conn:
            _resort(conn, table, id_col, body.ids)
            return {"ok": True}

for _kind in _SORT_TABLES:
    _make_sort_route(_kind)


# ---- 設定驅動的 CRUD(種類 / 廠牌 / 手機品牌 同構) ----
# 保留原有 URL 端點與回傳形狀不變;list/add/patch/delete 由設定產生。

def _make_crud(cfg):
    table, id_col, nf = cfg["table"], cfg["id_col"], cfg["not_found"]
    path, id_key = cfg["path"], cfg["id_key"]
    NewModel, PatchModel = cfg["new"], cfg["patch"]
    refs, cleanup = cfg["refs"], cfg["cleanup"]

    if cfg.get("gen_list", True):
        @router.get(f"/{path}")
        def _list(request: Request, all: int = 0):
            with db_conn(request.app.state.db_path) as conn:
                where = "" if all else " WHERE active=1"
                return [dict(r) for r in conn.execute(
                    f"SELECT * FROM {table}{where} ORDER BY sort, {id_col}")]

    @router.post(f"/{path}")
    def _add(body: NewModel, request: Request):
        with db_conn(request.app.state.db_path) as conn:
            sort = body.sort if body.sort is not None else next_sort(conn, table)
            cur = conn.execute(f"INSERT INTO {table}(name, sort) VALUES(?,?)",
                               (body.name, sort))
            conn.commit()
            return {id_key: cur.lastrowid}

    @router.patch(f"/{path}/{{item_id}}")
    def _patch(item_id: int, body: PatchModel, request: Request):
        with db_conn(request.app.state.db_path) as conn:
            fields = body.model_dump(exclude_unset=True)
            if not fields:
                return {"ok": True}
            update_by_id(conn, table, id_col, item_id, fields, nf)
            conn.commit()
            return {"ok": True}

    @router.delete(f"/{path}/{{item_id}}")
    def _delete(item_id: int, request: Request):
        with db_conn(request.app.state.db_path) as conn:
            require_exists(conn, table, id_col, item_id, nf)
            for rtable, rcol, rmsg in refs:
                reject_if_referenced(conn, rtable, rcol, item_id, rmsg)
            for sql in cleanup:
                conn.execute(sql, (item_id,))
            conn.execute(f"DELETE FROM {table} WHERE {id_col}=?", (item_id,))
            conn.commit()
            return {"ok": True}

_CRUD_CFGS = [
    {   # 種類 Category
        "path": "categories", "table": "Category", "id_col": "category_id",
        "id_key": "category_id", "not_found": "查無此種類",
        "new": CategoryNew, "patch": CategoryPatch, "gen_list": True,
        "refs": [("Product", "category_id",
                  "仍有商品屬於此種類,無法刪除,請改用停用")],
        # 清掉關聯:共用欄勾選、廠牌掛勾、該種類專屬欄及其選項;
        # 先解除欄位對選項的預設參照,避免刪選項時觸發 FK 循環參照
        "cleanup": [
            "DELETE FROM CategoryField WHERE category_id=?",
            "DELETE FROM BrandCategory WHERE category_id=?",
            "UPDATE AttributeField SET default_option_id=NULL WHERE category_id=?",
            "DELETE FROM AttributeOption WHERE field_id IN "
            "(SELECT field_id FROM AttributeField WHERE category_id=?)",
            "DELETE FROM AttributeField WHERE category_id=?",
        ],
    },
    {   # 廠牌 Brand(list 另有 category_id 篩選,手寫於下方)
        "path": "brands", "table": "Brand", "id_col": "brand_id",
        "id_key": "brand_id", "not_found": "查無此廠牌",
        "new": BrandNew, "patch": BrandPatch, "gen_list": False,
        "refs": [("Product", "brand_id",
                  "仍有商品屬於此廠牌,無法刪除,請改用停用")],
        "cleanup": ["DELETE FROM BrandCategory WHERE brand_id=?"],
    },
    {   # 手機品牌 PhoneBrand
        "path": "phone-brands", "table": "PhoneBrand", "id_col": "phone_brand_id",
        "id_key": "phone_brand_id", "not_found": "查無此手機品牌",
        "new": PhoneBrandNew, "patch": PhoneBrandPatch, "gen_list": True,
        "refs": [("PhoneModel", "phone_brand_id",
                  "仍有型號屬於此手機品牌,無法刪除,請改用停用")],
        "cleanup": [],
    },
]

for _cfg in _CRUD_CFGS:
    _make_crud(_cfg)


# ---- 廠牌 Brand:list(含建檔下拉 category_id 篩選)與掛種類 ----

@router.get("/brands")
def list_brands(request: Request, all: int = 0, category_id: int | None = None):
    with db_conn(request.app.state.db_path) as conn:
        if category_id is not None:
            # 建檔下拉:只回掛該種類且 active 的廠牌
            rows = conn.execute(
                "SELECT b.* FROM Brand b "
                "JOIN BrandCategory bc ON b.brand_id=bc.brand_id "
                "WHERE bc.category_id=? AND b.active=1 "
                "ORDER BY b.sort, b.brand_id", (category_id,))
            return [dict(r) for r in rows]
        where = "" if all else " WHERE active=1"
        return [dict(r) for r in conn.execute(
            "SELECT * FROM Brand" + where + " ORDER BY sort, brand_id")]

@router.put("/brands/{bid}/categories")
def set_brand_categories(bid: int, body: IdList, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        require_exists(conn, "Brand", "brand_id", bid, "查無此廠牌")
        replace_links(conn, "BrandCategory", "brand_id", bid,
                      "category_id", body.category_ids)
        conn.commit()
        return {"ok": True}


# ---- 手機型號 PhoneModel(多 phone_brand_id 驗證與 series 正規化,手寫) ----

@router.get("/models")
def list_models(request: Request, all: int = 0, phone_brand_id: int | None = None):
    with db_conn(request.app.state.db_path) as conn:
        # 回傳同時帶品牌名稱方便前端;建檔下拉(all=0)排除停用品牌之型號
        sql = ("SELECT m.model_id, m.phone_brand_id, m.name, m.alias, m.series, "
               "m.sort, m.active, pb.name AS brand_name "
               "FROM PhoneModel m JOIN PhoneBrand pb "
               "ON m.phone_brand_id=pb.phone_brand_id")
        clauses, args = [], []
        if not all:
            clauses.append("m.active=1")
            clauses.append("pb.active=1")
        if phone_brand_id is not None:
            clauses.append("m.phone_brand_id=?")
            args.append(phone_brand_id)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY pb.sort, m.sort, m.model_id"
        return [dict(r) for r in conn.execute(sql, args)]

@router.post("/models")
def add_model(body: ModelNew, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        if not conn.execute("SELECT 1 FROM PhoneBrand WHERE phone_brand_id=?",
                           (body.phone_brand_id,)).fetchone():
            raise HTTPException(422, "手機品牌不存在")
        sort = body.sort if body.sort is not None else next_sort(conn, "PhoneModel")
        series = (body.series or "").strip() or None
        cur = conn.execute(
            "INSERT INTO PhoneModel(phone_brand_id, name, alias, series, sort) "
            "VALUES(?,?,?,?,?)",
            (body.phone_brand_id, body.name, body.alias, series, sort))
        conn.commit()
        return {"model_id": cur.lastrowid}

@router.patch("/models/{mid}")
def patch_model(mid: int, body: ModelPatch, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        fields = body.model_dump(exclude_unset=True)
        if not fields:
            return {"ok": True}
        if "series" in fields:  # 空字串存 NULL
            s = (fields["series"] or "").strip()
            fields["series"] = s or None
        update_by_id(conn, "PhoneModel", "model_id", mid, fields, "查無此型號")
        conn.commit()
        return {"ok": True}

@router.delete("/models/{mid}")
def delete_model(mid: int, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        require_exists(conn, "PhoneModel", "model_id", mid, "查無此型號")
        reject_if_referenced(conn, "VariantModel", "model_id", mid,
                             "仍有商品掛此型號,無法刪除,請改用停用")
        reject_if_referenced(conn, "OptionModel", "model_id", mid,
                             "仍有選項限定此型號,無法刪除,請改用停用")
        conn.execute("DELETE FROM PhoneModel WHERE model_id=?", (mid,))
        conn.commit()
        return {"ok": True}


# ---- 種類規格欄(專屬+共用)+ 共用欄勾選 ----

@router.get("/categories/{cid}/fields")
def category_fields(cid: int, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        require_exists(conn, "Category", "category_id", cid, "查無此種類")
        # 專屬欄(category_id=cid)+ 已勾選啟用的共用欄(CategoryField)
        rows = conn.execute(
            "SELECT field_id, name, field_type, default_option_id, sort, category_id "
            "FROM AttributeField "
            "WHERE active=1 AND (category_id=? OR field_id IN "
            "(SELECT field_id FROM CategoryField WHERE category_id=?)) "
            "ORDER BY (category_id IS NULL), sort, field_id", (cid, cid)).fetchall()
        out = []
        for f in rows:
            opts = []
            # select/multi/tags 皆帶選項供建檔勾選/下拉/詞條建議
            if f["field_type"] in FIELD_TYPES - {"text"}:
                opts = [dict(o) for o in conn.execute(
                    "SELECT option_id, value, sort FROM AttributeOption "
                    "WHERE field_id=? AND active=1 ORDER BY sort, option_id",
                    (f["field_id"],))]
            # 預設選項值(select 建檔自動帶入)
            default_value = None
            if f["default_option_id"] is not None:
                dv = conn.execute(
                    "SELECT value FROM AttributeOption WHERE option_id=?",
                    (f["default_option_id"],)).fetchone()
                default_value = dv["value"] if dv else None
            out.append({
                "field_id": f["field_id"], "name": f["name"],
                "field_type": f["field_type"],
                "default_option_id": f["default_option_id"],
                "default_value": default_value,
                "shared": f["category_id"] is None,
                "options": opts})
        return out

@router.put("/categories/{cid}/fields-common")
def set_category_common_fields(cid: int, body: FieldIdList, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        require_exists(conn, "Category", "category_id", cid, "查無此種類")
        replace_links(conn, "CategoryField", "category_id", cid,
                      "field_id", body.field_ids,
                      fk_error_msg="規格欄不存在")
        conn.commit()
        return {"ok": True}
