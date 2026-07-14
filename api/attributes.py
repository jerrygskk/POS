import sqlite3
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel
from lib.db import db_conn, in_clause, next_sort
from lib.dbutil import require_exists, update_by_id, replace_links
from lib.product_rules import check_field_type

router = APIRouter(prefix="/api")

class FieldPatch(BaseModel):
    name: str | None = None
    sort: int | None = None
    active: int | None = None
    field_type: str | None = None
    default_option_id: int | None = None

class FieldNew(BaseModel):
    name: str
    category_id: int | None = None   # NULL=共用欄
    field_type: str = "select"       # select / text / multi / tags
    default_option_id: int | None = None

class OptionNew(BaseModel):
    field_id: int
    value: str
    reactivate: bool = False

class OptionPatch(BaseModel):
    value: str | None = None
    sort: int | None = None
    active: int | None = None

class OptionModelList(BaseModel):
    model_ids: list[int] = []

def _check_field_type(field_type):
    check_field_type(field_type)

def _check_default_option(conn, field_id, option_id):
    if option_id is None:
        return
    row = conn.execute(
        "SELECT field_id FROM AttributeOption WHERE option_id=?",
        (option_id,)).fetchone()
    if row is None or row["field_id"] != field_id:
        raise HTTPException(422, "預設選項不屬於此規格欄")

def _option_models(conn, option_ids):
    """回傳 {option_id: [model_id, ...]}(選項限定型號)。"""
    out = {}
    if not option_ids:
        return out
    qs = in_clause(option_ids)
    for r in conn.execute(
            f"SELECT option_id, model_id FROM OptionModel "
            f"WHERE option_id IN ({qs}) ORDER BY model_id", option_ids):
        out.setdefault(r["option_id"], []).append(r["model_id"])
    return out

@router.get("/fields")
def list_fields(request: Request, category_id: int | None = None,
                common: int = 0):
    with db_conn(request.app.state.db_path) as conn:
        sql = "SELECT * FROM AttributeField WHERE active=1"
        args = []
        if common:
            sql += " AND category_id IS NULL"
        elif category_id is not None:
            sql += " AND category_id=?"
            args.append(category_id)
        sql += " ORDER BY sort, field_id"
        return [dict(r) for r in conn.execute(sql, args)]

@router.post("/fields")
def add_field(body: FieldNew, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        _check_field_type(body.field_type)
        if body.default_option_id is not None:
            raise HTTPException(422, "新建規格欄不可設定預設選項")
        if body.category_id is not None:
            require_exists(conn, "Category", "category_id", body.category_id,
                           "查無此種類")
        sort = next_sort(conn, "AttributeField")
        cur = conn.execute(
            "INSERT INTO AttributeField(name, category_id, field_type, "
            "default_option_id, sort) VALUES(?, ?, ?, ?, ?)",
            (body.name, body.category_id, body.field_type,
             body.default_option_id, sort))
        conn.commit()
        return {"field_id": cur.lastrowid}

@router.put("/fields/{field_id}")
def patch_field(field_id: int, body: FieldPatch, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        # default_option_id 可明確設為 None(清除預設),故以 exclude_unset 判斷
        fields = body.model_dump(exclude_unset=True)
        if not fields:
            return {"ok": True}
        if "field_type" in fields:
            _check_field_type(fields["field_type"])
        if "default_option_id" in fields:
            _check_default_option(conn, field_id, fields["default_option_id"])
        update_by_id(conn, "AttributeField", "field_id", field_id, fields)
        conn.commit()
        return {"ok": True}

@router.get("/options")
def list_options(field_id: int, request: Request, all: int = 0,
                 model_ids: list[int] = Query(default=[])):
    with db_conn(request.app.state.db_path) as conn:
        # all=1:維護頁需看到停用者;預設只回啟用(建檔下拉用)
        sql = ("SELECT AttributeOption.*, "
               "COUNT(DISTINCT va.variant_id) AS usage_count "
               "FROM AttributeOption "
               "LEFT JOIN VariantAttribute va "
               "ON va.option_id=AttributeOption.option_id "
               "WHERE AttributeOption.field_id=?")
        args = [field_id]
        if not all:
            sql += " AND AttributeOption.active=1"
        # model_ids 過濾(建檔下拉):回「未綁任何型號的 ∪ 綁定含任一給定型號的」
        if model_ids:
            qs = in_clause(model_ids)
            sql += (" AND (NOT EXISTS(SELECT 1 FROM OptionModel om "
                    "WHERE om.option_id=AttributeOption.option_id) "
                    "OR EXISTS(SELECT 1 FROM OptionModel om "
                    "WHERE om.option_id=AttributeOption.option_id "
                    f"AND om.model_id IN ({qs})))")
            args += model_ids
        sql += " GROUP BY AttributeOption.option_id ORDER BY sort, option_id"
        opts = [dict(r) for r in conn.execute(sql, args)]
        # 附上每個選項的限定型號清單(維護頁顯示用)
        mm = _option_models(conn, [o["option_id"] for o in opts])
        for o in opts:
            o["model_ids"] = mm.get(o["option_id"], [])
        return opts

@router.post("/options")
def add_option(body: OptionNew, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        require_exists(conn, "AttributeField", "field_id", body.field_id,
                       "查無此規格欄")
        # UNIQUE(field_id,value):手打自增入庫入口,重複送出冪等成功
        sort = next_sort(conn, "AttributeOption", "field_id=?", (body.field_id,))
        conn.execute(
            "INSERT OR IGNORE INTO AttributeOption(field_id, value, sort) "
            "VALUES(?, ?, ?)", (body.field_id, body.value, sort))
        if body.reactivate:
            conn.execute(
                "UPDATE AttributeOption SET active=1 WHERE field_id=? AND value=?",
                (body.field_id, body.value))
        conn.commit()
        return {"ok": True}

@router.patch("/options/{option_id}")
def patch_option(option_id: int, body: OptionPatch, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        require_exists(conn, "AttributeOption", "option_id", option_id, "查無此選項")
        for col in ("value", "sort", "active"):
            v = getattr(body, col)
            if v is None:
                continue
            try:
                conn.execute(
                    f"UPDATE AttributeOption SET {col}=? WHERE option_id=?",
                    (v, option_id))
            except sqlite3.IntegrityError:
                # UNIQUE(field_id,value):同欄已有相同選項值
                raise HTTPException(409, "此選項值已存在")
        conn.commit()
        return {"ok": True}

@router.delete("/options/{option_id}")
def delete_option(option_id: int, request: Request):
    with db_conn(request.app.state.db_path) as conn:
        require_exists(conn, "AttributeOption", "option_id", option_id, "查無此選項")
        usage_count = conn.execute(
            "SELECT COUNT(DISTINCT variant_id) FROM VariantAttribute WHERE option_id=?",
            (option_id,)).fetchone()[0]
        conn.execute(
            "UPDATE AttributeField SET default_option_id=NULL WHERE default_option_id=?",
            (option_id,))
        conn.execute("DELETE FROM OptionModel WHERE option_id=?", (option_id,))
        if usage_count:
            conn.execute("UPDATE AttributeOption SET active=0 WHERE option_id=?",
                         (option_id,))
        else:
            conn.execute("DELETE FROM AttributeOption WHERE option_id=?", (option_id,))
        conn.commit()
        return {"ok": True, "deleted": not bool(usage_count)}

@router.get("/options/{option_id}/models")
def get_option_models(option_id: int, request: Request):
    """讀取選項的限定型號 model_id 清單(空=通用,不限型號)。"""
    with db_conn(request.app.state.db_path) as conn:
        require_exists(conn, "AttributeOption", "option_id", option_id, "查無此選項")
        return {"model_ids": _option_models(conn, [option_id]).get(option_id, [])}

@router.put("/options/{option_id}/models")
def set_option_models(option_id: int, body: OptionModelList, request: Request):
    """全量替換選項的限定型號。空清單=改回通用。只影響建檔下拉,不回溯既有變體。"""
    with db_conn(request.app.state.db_path) as conn:
        require_exists(conn, "AttributeOption", "option_id", option_id, "查無此選項")
        replace_links(conn, "OptionModel", "option_id", option_id,
                      "model_id", body.model_ids, fk_error_msg="型號不存在")
        conn.commit()
        return {"ok": True}
