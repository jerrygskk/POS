from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from lib.db import get_conn

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


def _next_sort(conn, table):
    r = conn.execute(f"SELECT COALESCE(MAX(sort),0)+1 s FROM {table}").fetchone()
    return r["s"]


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
        conn = get_conn(request.app.state.db_path)
        try:
            _resort(conn, table, id_col, body.ids)
            return {"ok": True}
        finally:
            conn.close()

for _kind in _SORT_TABLES:
    _make_sort_route(_kind)


# ---- 種類 Category ----

@router.get("/categories")
def list_categories(request: Request, all: int = 0):
    conn = get_conn(request.app.state.db_path)
    try:
        where = "" if all else " WHERE active=1"
        return [dict(r) for r in conn.execute(
            "SELECT * FROM Category" + where + " ORDER BY sort, category_id")]
    finally:
        conn.close()

@router.post("/categories")
def add_category(body: CategoryNew, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        sort = body.sort if body.sort is not None else _next_sort(conn, "Category")
        cur = conn.execute("INSERT INTO Category(name, sort) VALUES(?,?)",
                           (body.name, sort))
        conn.commit()
        return {"category_id": cur.lastrowid}
    finally:
        conn.close()

@router.patch("/categories/{cid}")
def patch_category(cid: int, body: CategoryPatch, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        fields = body.model_dump(exclude_unset=True)
        if not fields:
            return {"ok": True}
        cols = ", ".join(f"{k}=?" for k in fields)
        cur = conn.execute(f"UPDATE Category SET {cols} WHERE category_id=?",
                          list(fields.values()) + [cid])
        if cur.rowcount == 0:
            raise HTTPException(404, "查無此種類")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.delete("/categories/{cid}")
def delete_category(cid: int, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        if not conn.execute("SELECT 1 FROM Category WHERE category_id=?",
                            (cid,)).fetchone():
            raise HTTPException(404, "查無此種類")
        if conn.execute("SELECT 1 FROM Product WHERE category_id=? LIMIT 1",
                       (cid,)).fetchone():
            raise HTTPException(409, "仍有商品屬於此種類,無法刪除,請改用停用")
        # 清掉關聯:共用欄勾選、廠牌掛勾、該種類專屬欄及其選項
        conn.execute("DELETE FROM CategoryField WHERE category_id=?", (cid,))
        conn.execute("DELETE FROM BrandCategory WHERE category_id=?", (cid,))
        # 先解除欄位對選項的預設參照,避免刪選項時觸發 FK 循環參照
        conn.execute(
            "UPDATE AttributeField SET default_option_id=NULL WHERE category_id=?",
            (cid,))
        conn.execute(
            "DELETE FROM AttributeOption WHERE field_id IN "
            "(SELECT field_id FROM AttributeField WHERE category_id=?)", (cid,))
        conn.execute("DELETE FROM AttributeField WHERE category_id=?", (cid,))
        conn.execute("DELETE FROM Category WHERE category_id=?", (cid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---- 廠牌 Brand ----

@router.get("/brands")
def list_brands(request: Request, all: int = 0, category_id: int | None = None):
    conn = get_conn(request.app.state.db_path)
    try:
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
    finally:
        conn.close()

@router.post("/brands")
def add_brand(body: BrandNew, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        sort = body.sort if body.sort is not None else _next_sort(conn, "Brand")
        cur = conn.execute("INSERT INTO Brand(name, sort) VALUES(?,?)",
                          (body.name, sort))
        conn.commit()
        return {"brand_id": cur.lastrowid}
    finally:
        conn.close()

@router.patch("/brands/{bid}")
def patch_brand(bid: int, body: BrandPatch, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        fields = body.model_dump(exclude_unset=True)
        if not fields:
            return {"ok": True}
        cols = ", ".join(f"{k}=?" for k in fields)
        cur = conn.execute(f"UPDATE Brand SET {cols} WHERE brand_id=?",
                          list(fields.values()) + [bid])
        if cur.rowcount == 0:
            raise HTTPException(404, "查無此廠牌")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.delete("/brands/{bid}")
def delete_brand(bid: int, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        if not conn.execute("SELECT 1 FROM Brand WHERE brand_id=?",
                           (bid,)).fetchone():
            raise HTTPException(404, "查無此廠牌")
        if conn.execute("SELECT 1 FROM Product WHERE brand_id=? LIMIT 1",
                       (bid,)).fetchone():
            raise HTTPException(409, "仍有商品屬於此廠牌,無法刪除,請改用停用")
        conn.execute("DELETE FROM BrandCategory WHERE brand_id=?", (bid,))
        conn.execute("DELETE FROM Brand WHERE brand_id=?", (bid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.put("/brands/{bid}/categories")
def set_brand_categories(bid: int, body: IdList, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        if not conn.execute("SELECT 1 FROM Brand WHERE brand_id=?",
                           (bid,)).fetchone():
            raise HTTPException(404, "查無此廠牌")
        conn.execute("DELETE FROM BrandCategory WHERE brand_id=?", (bid,))
        for cid in dict.fromkeys(body.category_ids):
            conn.execute(
                "INSERT OR IGNORE INTO BrandCategory(brand_id, category_id) "
                "VALUES(?,?)", (bid, cid))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---- 手機品牌 PhoneBrand ----

@router.get("/phone-brands")
def list_phone_brands(request: Request, all: int = 0):
    conn = get_conn(request.app.state.db_path)
    try:
        where = "" if all else " WHERE active=1"
        return [dict(r) for r in conn.execute(
            "SELECT * FROM PhoneBrand" + where +
            " ORDER BY sort, phone_brand_id")]
    finally:
        conn.close()

@router.post("/phone-brands")
def add_phone_brand(body: PhoneBrandNew, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        sort = body.sort if body.sort is not None else _next_sort(conn, "PhoneBrand")
        cur = conn.execute("INSERT INTO PhoneBrand(name, sort) VALUES(?,?)",
                           (body.name, sort))
        conn.commit()
        return {"phone_brand_id": cur.lastrowid}
    finally:
        conn.close()

@router.patch("/phone-brands/{pbid}")
def patch_phone_brand(pbid: int, body: PhoneBrandPatch, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        fields = body.model_dump(exclude_unset=True)
        if not fields:
            return {"ok": True}
        cols = ", ".join(f"{k}=?" for k in fields)
        cur = conn.execute(f"UPDATE PhoneBrand SET {cols} WHERE phone_brand_id=?",
                          list(fields.values()) + [pbid])
        if cur.rowcount == 0:
            raise HTTPException(404, "查無此手機品牌")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.delete("/phone-brands/{pbid}")
def delete_phone_brand(pbid: int, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        if not conn.execute("SELECT 1 FROM PhoneBrand WHERE phone_brand_id=?",
                           (pbid,)).fetchone():
            raise HTTPException(404, "查無此手機品牌")
        if conn.execute("SELECT 1 FROM PhoneModel WHERE phone_brand_id=? LIMIT 1",
                       (pbid,)).fetchone():
            raise HTTPException(409, "仍有型號屬於此手機品牌,無法刪除,請改用停用")
        conn.execute("DELETE FROM PhoneBrand WHERE phone_brand_id=?", (pbid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---- 手機型號 PhoneModel ----

@router.get("/models")
def list_models(request: Request, all: int = 0, phone_brand_id: int | None = None):
    conn = get_conn(request.app.state.db_path)
    try:
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
    finally:
        conn.close()

@router.post("/models")
def add_model(body: ModelNew, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        if not conn.execute("SELECT 1 FROM PhoneBrand WHERE phone_brand_id=?",
                           (body.phone_brand_id,)).fetchone():
            raise HTTPException(422, "手機品牌不存在")
        sort = body.sort if body.sort is not None else _next_sort(conn, "PhoneModel")
        series = (body.series or "").strip() or None
        cur = conn.execute(
            "INSERT INTO PhoneModel(phone_brand_id, name, alias, series, sort) "
            "VALUES(?,?,?,?,?)",
            (body.phone_brand_id, body.name, body.alias, series, sort))
        conn.commit()
        return {"model_id": cur.lastrowid}
    finally:
        conn.close()

@router.patch("/models/{mid}")
def patch_model(mid: int, body: ModelPatch, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        fields = body.model_dump(exclude_unset=True)
        if not fields:
            return {"ok": True}
        if "series" in fields:  # 空字串存 NULL
            s = (fields["series"] or "").strip()
            fields["series"] = s or None
        cols = ", ".join(f"{k}=?" for k in fields)
        cur = conn.execute(f"UPDATE PhoneModel SET {cols} WHERE model_id=?",
                          list(fields.values()) + [mid])
        if cur.rowcount == 0:
            raise HTTPException(404, "查無此型號")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.delete("/models/{mid}")
def delete_model(mid: int, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        if not conn.execute("SELECT 1 FROM PhoneModel WHERE model_id=?",
                           (mid,)).fetchone():
            raise HTTPException(404, "查無此型號")
        if conn.execute("SELECT 1 FROM VariantModel WHERE model_id=? LIMIT 1",
                       (mid,)).fetchone():
            raise HTTPException(409, "仍有商品掛此型號,無法刪除,請改用停用")
        if conn.execute("SELECT 1 FROM OptionModel WHERE model_id=? LIMIT 1",
                       (mid,)).fetchone():
            raise HTTPException(409, "仍有選項限定此型號,無法刪除,請改用停用")
        conn.execute("DELETE FROM PhoneModel WHERE model_id=?", (mid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---- 種類規格欄(專屬+共用)+ 共用欄勾選 ----

@router.get("/categories/{cid}/fields")
def category_fields(cid: int, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        if not conn.execute("SELECT 1 FROM Category WHERE category_id=?",
                           (cid,)).fetchone():
            raise HTTPException(404, "查無此種類")
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
            if f["field_type"] in ("select", "multi", "tags"):
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
    finally:
        conn.close()

@router.put("/categories/{cid}/fields-common")
def set_category_common_fields(cid: int, body: FieldIdList, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        if not conn.execute("SELECT 1 FROM Category WHERE category_id=?",
                           (cid,)).fetchone():
            raise HTTPException(404, "查無此種類")
        conn.execute("DELETE FROM CategoryField WHERE category_id=?", (cid,))
        for fid in dict.fromkeys(body.field_ids):
            conn.execute(
                "INSERT OR IGNORE INTO CategoryField(category_id, field_id) "
                "VALUES(?,?)", (cid, fid))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
