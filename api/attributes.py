import sqlite3
from fastapi import APIRouter, Request, HTTPException, Query
from pydantic import BaseModel
from lib.db import get_conn

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

class OptionPatch(BaseModel):
    value: str | None = None
    sort: int | None = None
    active: int | None = None

class OptionModelList(BaseModel):
    model_ids: list[int] = []

def _option_models(conn, option_ids):
    """回傳 {option_id: [model_id, ...]}(選項限定型號)。"""
    out = {}
    if not option_ids:
        return out
    qs = ",".join("?" * len(option_ids))
    for r in conn.execute(
            f"SELECT option_id, model_id FROM OptionModel "
            f"WHERE option_id IN ({qs}) ORDER BY model_id", option_ids):
        out.setdefault(r["option_id"], []).append(r["model_id"])
    return out

@router.get("/fields")
def list_fields(request: Request, category_id: int | None = None,
                common: int = 0):
    conn = get_conn(request.app.state.db_path)
    try:
        sql = "SELECT * FROM AttributeField WHERE active=1"
        args = []
        if common:
            sql += " AND category_id IS NULL"
        elif category_id is not None:
            sql += " AND category_id=?"
            args.append(category_id)
        sql += " ORDER BY sort, field_id"
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()

@router.post("/fields")
def add_field(body: FieldNew, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        cur = conn.execute(
            "INSERT INTO AttributeField(name, category_id, field_type, "
            "default_option_id, sort) "
            "VALUES(?, ?, ?, ?, (SELECT COALESCE(MAX(sort),0)+1 FROM AttributeField))",
            (body.name, body.category_id, body.field_type, body.default_option_id))
        conn.commit()
        return {"field_id": cur.lastrowid}
    finally:
        conn.close()

@router.put("/fields/{field_id}")
def patch_field(field_id: int, body: FieldPatch, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        # default_option_id 可明確設為 None(清除預設),故以 exclude_unset 判斷
        fields = body.model_dump(exclude_unset=True)
        for col, v in fields.items():
            conn.execute(f"UPDATE AttributeField SET {col}=? WHERE field_id=?",
                         (v, field_id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.get("/options")
def list_options(field_id: int, request: Request, all: int = 0,
                 model_ids: list[int] = Query(default=[])):
    conn = get_conn(request.app.state.db_path)
    try:
        # all=1:維護頁需看到停用者;預設只回啟用(建檔下拉用)
        sql = "SELECT * FROM AttributeOption WHERE field_id=?"
        args = [field_id]
        if not all:
            sql += " AND active=1"
        # model_ids 過濾(建檔下拉):回「未綁任何型號的 ∪ 綁定含任一給定型號的」
        if model_ids:
            qs = ",".join("?" * len(model_ids))
            sql += (" AND (NOT EXISTS(SELECT 1 FROM OptionModel om "
                    "WHERE om.option_id=AttributeOption.option_id) "
                    "OR EXISTS(SELECT 1 FROM OptionModel om "
                    "WHERE om.option_id=AttributeOption.option_id "
                    f"AND om.model_id IN ({qs})))")
            args += model_ids
        sql += " ORDER BY sort, option_id"
        opts = [dict(r) for r in conn.execute(sql, args)]
        # 附上每個選項的限定型號清單(維護頁顯示用)
        mm = _option_models(conn, [o["option_id"] for o in opts])
        for o in opts:
            o["model_ids"] = mm.get(o["option_id"], [])
        return opts
    finally:
        conn.close()

@router.post("/options")
def add_option(body: OptionNew, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        # UNIQUE(field_id,value):手打自增入庫入口,重複送出冪等成功
        conn.execute(
            "INSERT OR IGNORE INTO AttributeOption(field_id, value, sort) "
            "VALUES(?, ?, (SELECT COALESCE(MAX(sort),0)+1 FROM AttributeOption "
            "WHERE field_id=?))",
            (body.field_id, body.value, body.field_id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.patch("/options/{option_id}")
def patch_option(option_id: int, body: OptionPatch, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        if not conn.execute("SELECT 1 FROM AttributeOption WHERE option_id=?",
                            (option_id,)).fetchone():
            raise HTTPException(404, "查無此選項")
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
    finally:
        conn.close()

@router.delete("/options/{option_id}")
def delete_option(option_id: int, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        if not conn.execute("SELECT 1 FROM AttributeOption WHERE option_id=?",
                            (option_id,)).fetchone():
            raise HTTPException(404, "查無此選項")
        # 正規化後變體以 VariantAttribute.option_id 參照選項;有參照硬刪回 409
        if conn.execute("SELECT 1 FROM VariantAttribute WHERE option_id=? LIMIT 1",
                        (option_id,)).fetchone():
            raise HTTPException(409, "此選項已被商品使用,無法刪除,請改用停用")
        conn.execute("DELETE FROM OptionModel WHERE option_id=?", (option_id,))
        conn.execute("DELETE FROM AttributeOption WHERE option_id=?", (option_id,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.get("/options/{option_id}/models")
def get_option_models(option_id: int, request: Request):
    """讀取選項的限定型號 model_id 清單(空=通用,不限型號)。"""
    conn = get_conn(request.app.state.db_path)
    try:
        if not conn.execute("SELECT 1 FROM AttributeOption WHERE option_id=?",
                            (option_id,)).fetchone():
            raise HTTPException(404, "查無此選項")
        return {"model_ids": _option_models(conn, [option_id]).get(option_id, [])}
    finally:
        conn.close()

@router.put("/options/{option_id}/models")
def set_option_models(option_id: int, body: OptionModelList, request: Request):
    """全量替換選項的限定型號。空清單=改回通用。只影響建檔下拉,不回溯既有變體。"""
    conn = get_conn(request.app.state.db_path)
    try:
        if not conn.execute("SELECT 1 FROM AttributeOption WHERE option_id=?",
                            (option_id,)).fetchone():
            raise HTTPException(404, "查無此選項")
        conn.execute("DELETE FROM OptionModel WHERE option_id=?", (option_id,))
        for mid in dict.fromkeys(body.model_ids):
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO OptionModel(option_id, model_id) VALUES(?,?)",
                    (option_id, mid))
            except sqlite3.IntegrityError:
                # FK:型號不存在
                raise HTTPException(422, "型號不存在")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
