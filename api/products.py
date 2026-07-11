from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from lib.db import get_conn

router = APIRouter(prefix="/api")

class BarcodeIn(BaseModel):
    barcode: str | None = None
    source: str = "store"

class VariantIn(BaseModel):
    attributes: dict = {}
    price: int | None = None
    model_ids: list[int] = []
    barcodes: list[BarcodeIn] = []

class ProductIn(BaseModel):
    name: str
    category_id: int
    brand_id: int | None = None
    default_price: int | None = None
    note: str | None = None
    variants: list[VariantIn] = []

class ProductPatch(BaseModel):
    name: str | None = None
    category_id: int | None = None
    brand_id: int | None = None
    default_price: int | None = None
    note: str | None = None
    active: int | None = None

class VariantPatch(BaseModel):
    attributes: dict | None = None
    price: int | None = None
    active: int | None = None

class NewVariantIn(BaseModel):
    attributes: dict = {}
    price: int | None = None
    model_ids: list[int] = []
    barcodes: list[BarcodeIn] = []

class ModelIdList(BaseModel):
    model_ids: list[int] = []

def _check_category(conn, category_id):
    r = conn.execute("SELECT active FROM Category WHERE category_id=?",
                     (category_id,)).fetchone()
    if r is None:
        raise HTTPException(422, "種類不存在")
    if not r["active"]:
        raise HTTPException(422, "種類已停用,無法建檔")

def _check_brand(conn, brand_id):
    if brand_id is None:
        return
    r = conn.execute("SELECT active FROM Brand WHERE brand_id=?",
                     (brand_id,)).fetchone()
    if r is None:
        raise HTTPException(422, "廠牌不存在")
    if not r["active"]:
        raise HTTPException(422, "廠牌已停用,無法建檔")

def _set_variant_models(conn, variant_id, model_ids):
    conn.execute("DELETE FROM VariantModel WHERE variant_id=?", (variant_id,))
    for mid in dict.fromkeys(model_ids):
        conn.execute(
            "INSERT OR IGNORE INTO VariantModel(variant_id, model_id) VALUES(?,?)",
            (variant_id, mid))

def _models_by_variant(conn, variant_ids):
    """回傳 {variant_id: [型號顯示名, ...]}(有別名顯示別名,否則全名)"""
    out = {}
    if not variant_ids:
        return out
    qs = ",".join("?" * len(variant_ids))
    for r in conn.execute(
            f"SELECT vm.variant_id, COALESCE(NULLIF(m.alias,''), m.name) AS name "
            f"FROM VariantModel vm "
            f"JOIN PhoneModel m ON vm.model_id=m.model_id "
            f"WHERE vm.variant_id IN ({qs}) ORDER BY m.sort, m.model_id",
            variant_ids):
        out.setdefault(r["variant_id"], []).append(r["name"])
    return out

# ---- 規格值(VariantAttribute)讀寫 ----
# API 對外仍以 attributes:{欄名:值} dict 形狀進出;內部轉為關聯列。

def _product_category(conn, pid):
    r = conn.execute("SELECT category_id FROM Product WHERE product_id=?",
                     (pid,)).fetchone()
    return r["category_id"] if r else None

def _variant_category(conn, variant_id):
    r = conn.execute(
        "SELECT p.category_id FROM Variant v "
        "JOIN Product p ON v.product_id=p.product_id WHERE v.variant_id=?",
        (variant_id,)).fetchone()
    return r["category_id"] if r else None

def _resolve_field(conn, category_id, name):
    """依欄名 + 商品種類找 AttributeField;專屬欄優先於共用欄(category_id NULL)。"""
    return conn.execute(
        "SELECT field_id, field_type FROM AttributeField "
        "WHERE name=? AND active=1 AND (category_id=? OR category_id IS NULL) "
        "ORDER BY (category_id IS NULL) LIMIT 1", (name, category_id)).fetchone()

def _is_empty(value):
    """None/空字串/空清單/僅空白字串 視為空值。"""
    if value is None or value == "":
        return True
    if isinstance(value, (list, tuple)):
        return not any(str(v).strip() for v in value)
    return str(value).strip() == ""

def _as_list(value):
    """multi/tags 欄值標準化為去空白後的字串清單(容忍單一字串)。"""
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = [value]
    return [str(v).strip() for v in items if str(v).strip()]

def _find_option(conn, field_id, value):
    r = conn.execute(
        "SELECT option_id FROM AttributeOption WHERE field_id=? AND value=?",
        (field_id, value)).fetchone()
    return r["option_id"] if r else None

def _create_option(conn, field_id, value):
    """tags 欄未見過的詞條自動建選項(冪等)。"""
    conn.execute(
        "INSERT OR IGNORE INTO AttributeOption(field_id, value, sort) "
        "VALUES(?,?,(SELECT COALESCE(MAX(sort),0)+1 FROM AttributeOption "
        "WHERE field_id=?))", (field_id, value, field_id))
    return _find_option(conn, field_id, value)

def set_variant_attributes(conn, variant_id, category_id, attributes):
    """依 {欄名:值} 覆寫 VariantAttribute。
    - select 欄:值為字串 → 存單筆 option_id(查無回 422)。
    - text 欄:值為字串 → 存單筆 text_value。
    - multi 欄:值為清單 → 每值存一筆 option_id(值須為既有選項,否則 422)。
    - tags 欄:值為清單 → 每值存一筆 option_id(未見過的詞條自動建選項)。
    空值(None/''/空清單)略過不寫;欄名查無(且值非空)回 422。"""
    conn.execute("DELETE FROM VariantAttribute WHERE variant_id=?", (variant_id,))
    for name, value in (attributes or {}).items():
        if _is_empty(value):
            continue
        f = _resolve_field(conn, category_id, name)
        if f is None:
            raise HTTPException(422, f"規格欄「{name}」不存在")
        fid, ftype = f["field_id"], f["field_type"]
        if ftype in ("multi", "tags"):
            for v in dict.fromkeys(_as_list(value)):     # 同欄去重、保序
                oid = _find_option(conn, fid, v)
                if oid is None:
                    if ftype == "tags":
                        oid = _create_option(conn, fid, v)
                    else:
                        raise HTTPException(
                            422, f"規格欄「{name}」查無選項「{v}」")
                conn.execute(
                    "INSERT INTO VariantAttribute(variant_id, field_id, option_id) "
                    "VALUES(?,?,?)", (variant_id, fid, oid))
        elif ftype == "select":
            oid = _find_option(conn, fid, str(value))
            if oid is None:
                raise HTTPException(422, f"規格欄「{name}」查無選項「{value}」")
            conn.execute(
                "INSERT INTO VariantAttribute(variant_id, field_id, option_id) "
                "VALUES(?,?,?)", (variant_id, fid, oid))
        else:   # text
            conn.execute(
                "INSERT INTO VariantAttribute(variant_id, field_id, text_value) "
                "VALUES(?,?,?)", (variant_id, fid, str(value)))

# 顯示各欄連接符:multi 以「+」、tags 以「, 」連;各欄間以「｜」分隔(spec §2)。
_MULTI_JOIN = "+"
_TAGS_JOIN = ", "
_FIELD_SEP = "｜"

def _attr_rows(conn, variant_ids):
    """撈變體規格關聯列(依欄 sort、選項 sort 排序),供 dict/顯示字串共用。"""
    if not variant_ids:
        return []
    qs = ",".join("?" * len(variant_ids))
    return conn.execute(
        f"SELECT va.variant_id, f.name AS field_name, f.field_type, "
        f"o.value AS option_value, va.text_value, "
        f"(va.option_id IS NOT NULL AND va.option_id = f.default_option_id) AS is_default "
        f"FROM VariantAttribute va "
        f"JOIN AttributeField f ON va.field_id=f.field_id "
        f"LEFT JOIN AttributeOption o ON va.option_id=o.option_id "
        f"WHERE va.variant_id IN ({qs}) "
        f"ORDER BY va.variant_id, f.sort, f.field_id, o.sort, o.option_id",
        variant_ids).fetchall()

def attrs_by_variant(conn, variant_ids):
    """批次組回 {variant_id: {欄名: 值}};一次撈齊避免 N+1。
    select 回 option 值、text 回 text_value(皆為字串);
    multi/tags 回值清單([值,...]);依欄 sort、選項 sort 排序。"""
    out = {}
    for r in _attr_rows(conn, variant_ids):
        d = out.setdefault(r["variant_id"], {})
        if r["field_type"] in ("multi", "tags"):
            d.setdefault(r["field_name"], []).append(r["option_value"])
        else:
            d[r["field_name"]] = (r["option_value"] if r["option_value"] is not None
                                  else r["text_value"])
    return out

def display_attrs(conn, variant_ids):
    """批次組回 {variant_id: 顯示字串},遵守 spec §2 順位(欄 sort)。
    multi 以「+」、tags 以「, 」連,各欄以「｜」分隔。"""
    # {vid: [(field_name, field_type, [值,...] 或 值)]} 保序
    acc = {}
    for r in _attr_rows(conn, variant_ids):
        fields = acc.setdefault(r["variant_id"], [])
        ftype = r["field_type"]
        if ftype in ("multi", "tags"):
            if fields and fields[-1][0] == r["field_name"]:
                fields[-1][2].append(r["option_value"])
            else:
                fields.append((r["field_name"], ftype, [r["option_value"]]))
        else:
            if r["is_default"]:
                continue    # 值=該欄預設選項(如版型=滿版)不顯示,非預設才顯示
            val = r["option_value"] if r["option_value"] is not None else r["text_value"]
            fields.append((r["field_name"], ftype, val))
    out = {}
    for vid, fields in acc.items():
        parts = []
        for _name, ftype, val in fields:
            if ftype == "multi":
                parts.append(_MULTI_JOIN.join(val))
            elif ftype == "tags":
                parts.append(_TAGS_JOIN.join(val))
            else:
                parts.append(str(val))
        out[vid] = _FIELD_SEP.join(parts)
    return out

def variant_sort_keys(conn, variant_ids):
    """批次算變體排序鍵 {variant_id: key}。供資料庫頁變體列排序,依材質組合分節:
    ① 材質數(multi 值個數)少者在前:單一材質各自成節,複合材質(霧面+防窺)排全部單材質之後
    ② 抗AR 特例(維護者指定):帶「抗AR」詞條者整塊移到同材質數的素身/一般詞條之後,
       塊內主材質排序照舊(亮,霧,藍,窺,亮|AR,窺|AR)
    ③ 依欄 sort、選項 sort(材質序);其他詞條照舊跟著自己的材質、依詞條序
    無屬性=最前。"""
    keys = {vid: [0, 0, [], []] for vid in variant_ids}  # [材質數,抗AR,材質序,詞條序]
    if not variant_ids:
        return {}
    qs = ",".join("?" * len(variant_ids))
    for r in conn.execute(
            f"SELECT va.variant_id, f.field_type, f.sort AS fsort, "
            f"f.field_id, o.sort AS osort, o.option_id, o.value AS oval "
            f"FROM VariantAttribute va "
            f"JOIN AttributeField f ON va.field_id=f.field_id "
            f"LEFT JOIN AttributeOption o ON va.option_id=o.option_id "
            f"WHERE va.variant_id IN ({qs}) "
            f"ORDER BY va.variant_id, f.sort, f.field_id, o.sort, o.option_id",
            variant_ids):
        k = keys[r["variant_id"]]
        if r["field_type"] == "tags":
            if r["oval"] == "抗AR":
                k[1] = 1
            if r["osort"] is not None:
                k[3].append((r["fsort"] or 0, r["field_id"],
                             r["osort"], r["option_id"]))
            continue
        if r["field_type"] == "multi":
            k[0] += 1
        if r["osort"] is not None:
            k[2].append((r["fsort"] or 0, r["field_id"],
                         r["osort"], r["option_id"]))
    return {vid: (k[0], k[1], tuple(k[2]), tuple(k[3]))
            for vid, k in keys.items()}

def attrs_of(conn, variant_id):
    """單一變體規格 dict(便捷包裝)。"""
    return attrs_by_variant(conn, [variant_id]).get(variant_id, {})

def display_of(conn, variant_id):
    """單一變體顯示字串(便捷包裝)。"""
    return display_attrs(conn, [variant_id]).get(variant_id, "")

def _has_records(conn, variant_ids):
    if not variant_ids:
        return False
    qs = ",".join("?" * len(variant_ids))
    r = conn.execute(
        f"SELECT 1 FROM SaleItem WHERE variant_id IN ({qs}) LIMIT 1",
        variant_ids).fetchone()
    if r:
        return True
    r = conn.execute(
        f"SELECT 1 FROM StockMovement WHERE variant_id IN ({qs}) LIMIT 1",
        variant_ids).fetchone()
    return bool(r)

def _reject_manual_tl(barcode):
    """TL 開頭為自取碼保留字頭,禁止手動輸入(只能由系統取號或匯入工具寫入),
    避免與流水號撞號。"""
    if barcode and barcode.upper().startswith("TL"):
        raise HTTPException(422, "TL 開頭為系統保留，如有需求請按自取條碼")

def next_store_barcode(conn):
    """自取碼取號:TL+流水號。號碼只存 Setting.next_store_barcode(取用後+1),
    單調遞增、刪除條碼不回收號碼;匯入工具寫入既有 TL 碼後會更新此值。"""
    row = conn.execute("SELECT value FROM Setting WHERE key='next_store_barcode'").fetchone()
    n = int(row["value"]) if row else 100000001
    conn.execute("INSERT OR REPLACE INTO Setting(key,value) VALUES('next_store_barcode',?)",
                 (str(n + 1),))
    return f"TL{n}"

def stock_of(conn, variant_id):
    r = conn.execute("SELECT COALESCE(SUM(qty),0) s FROM StockMovement WHERE variant_id=?",
                     (variant_id,)).fetchone()
    return r["s"]

@router.post("/products")
def create_product(body: ProductIn, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        _check_category(conn, body.category_id)
        _check_brand(conn, body.brand_id)
        cur = conn.execute(
            "INSERT INTO Product(name,category_id,brand_id,default_price,note) "
            "VALUES(?,?,?,?,?)",
            (body.name, body.category_id, body.brand_id,
             body.default_price, body.note))
        pid = cur.lastrowid
        vids = []
        for v in body.variants:
            cur = conn.execute(
                "INSERT INTO Variant(product_id,price) VALUES(?,?)", (pid, v.price))
            vid = cur.lastrowid
            vids.append(vid)
            set_variant_attributes(conn, vid, body.category_id, v.attributes)
            _set_variant_models(conn, vid, v.model_ids)
            for b in v.barcodes:
                _reject_manual_tl(b.barcode)
                code = b.barcode or next_store_barcode(conn)
                conn.execute("INSERT INTO Barcode(barcode,variant_id,source) VALUES(?,?,?)",
                             (code, vid, b.source))
        conn.commit()
        return {"product_id": pid, "variant_ids": vids}
    finally:
        conn.close()

@router.get("/barcode/{code}")
def scan(code: str, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        row = conn.execute(
            "SELECT v.variant_id, v.product_id, "
            "COALESCE(v.price, p.default_price) AS price, p.name, "
            "(p.active AND v.active) AS active "
            "FROM Barcode b JOIN Variant v ON b.variant_id=v.variant_id "
            "JOIN Product p ON v.product_id=p.product_id WHERE b.barcode=?",
            (code,)).fetchone()
        if not row:
            raise HTTPException(404, "查無此條碼")
        return {"variant_id": row["variant_id"], "product_id": row["product_id"],
                "name": row["name"], "attributes": attrs_of(conn, row["variant_id"]),
                "attr_display": display_of(conn, row["variant_id"]),
                "price": row["price"], "stock": stock_of(conn, row["variant_id"]),
                "active": bool(row["active"])}
    finally:
        conn.close()

@router.post("/variants/{variant_id}/barcodes")
def add_barcode(variant_id: int, body: BarcodeIn, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        _reject_manual_tl(body.barcode)
        code = body.barcode or next_store_barcode(conn)
        conn.execute("INSERT INTO Barcode(barcode,variant_id,source) VALUES(?,?,?)",
                     (code, variant_id, body.source))
        conn.commit()
        return {"barcode": code}
    finally:
        conn.close()

@router.get("/products")
def search(request: Request, q: str = "", category_id: int | None = None,
           brand_id: int | None = None, model_id: int | None = None):
    conn = get_conn(request.app.state.db_path)
    try:
        # 先以 SQL 撈符合種類/廠牌/型號的啟用變體,再於 Python 端以 q 比對
        # 款名或組回的規格值(正規化後屬性不再是欄,不能直接 LIKE)
        sql = (
            "SELECT v.variant_id, p.name, "
            "COALESCE(v.price,p.default_price) AS price, "
            "c.name AS category_name, b.name AS brand_name "
            "FROM Variant v JOIN Product p ON v.product_id=p.product_id "
            "LEFT JOIN Category c ON p.category_id=c.category_id "
            "LEFT JOIN Brand b ON p.brand_id=b.brand_id "
            "WHERE p.active=1 AND v.active=1")
        args = []
        if category_id is not None:
            sql += " AND p.category_id=?"; args.append(category_id)
        if brand_id is not None:
            sql += " AND p.brand_id=?"; args.append(brand_id)
        if model_id is not None:
            sql += (" AND v.variant_id IN "
                    "(SELECT variant_id FROM VariantModel WHERE model_id=?)")
            args.append(model_id)
        sql += " ORDER BY v.variant_id"
        rows = conn.execute(sql, args).fetchall()
        attrs = attrs_by_variant(conn, [r["variant_id"] for r in rows])
        if q:
            like = q.lower()
            rows = [r for r in rows
                    if like in (r["name"] or "").lower()
                    or any(like in str(val).lower()
                           for val in attrs.get(r["variant_id"], {}).values())]
        rows = rows[:100]
        models = _models_by_variant(conn, [r["variant_id"] for r in rows])
        disp = display_attrs(conn, [r["variant_id"] for r in rows])
        return [{"variant_id": r["variant_id"], "name": r["name"],
                 "attributes": attrs.get(r["variant_id"], {}),
                 "attr_display": disp.get(r["variant_id"], ""), "price": r["price"],
                 "category_name": r["category_name"], "brand_name": r["brand_name"],
                 "models": models.get(r["variant_id"], []),
                 "stock": stock_of(conn, r["variant_id"])} for r in rows]
    finally:
        conn.close()

@router.get("/catalog")
def catalog(request: Request, q: str = "", include_inactive: bool = False,
            category_id: int | None = None, brand_id: int | None = None,
            model_id: int | None = None):
    conn = get_conn(request.app.state.db_path)
    try:
        # 條碼:先撈全部,依 variant_id 分組
        bc = {}
        for b in conn.execute(
                "SELECT variant_id, barcode, source FROM Barcode "
                "ORDER BY variant_id, barcode"):
            bc.setdefault(b["variant_id"], []).append(
                {"barcode": b["barcode"], "source": b["source"]})

        p_clauses, p_args = [], []
        if not include_inactive:
            p_clauses.append("p.active=1")
        if category_id is not None:
            p_clauses.append("p.category_id=?"); p_args.append(category_id)
        if brand_id is not None:
            p_clauses.append("p.brand_id=?"); p_args.append(brand_id)
        p_where = (" WHERE " + " AND ".join(p_clauses)) if p_clauses else ""
        prods = conn.execute(
            "SELECT p.product_id, p.name, p.category_id, p.brand_id, "
            "p.default_price, p.note, p.active, "
            "c.name AS category_name, b.name AS brand_name "
            "FROM Product p "
            "LEFT JOIN Category c ON p.category_id=c.category_id "
            "LEFT JOIN Brand b ON p.brand_id=b.brand_id"
            + p_where + " ORDER BY p.product_id", p_args).fetchall()

        # 依 model_id 篩選的變體白名單(None=不篩)
        model_vids = None
        if model_id is not None:
            model_vids = {r["variant_id"] for r in conn.execute(
                "SELECT variant_id FROM VariantModel WHERE model_id=?", (model_id,))}

        # 一次撈齊所有款的變體 + 規格 + 型號,避免逐款/逐變體 N+1
        prod_ids = [p["product_id"] for p in prods]
        vrows = []
        if prod_ids:
            v_active = "" if include_inactive else " AND active=1"
            qs = ",".join("?" * len(prod_ids))
            vrows = conn.execute(
                f"SELECT variant_id, product_id, price, active FROM Variant "
                f"WHERE product_id IN ({qs})" + v_active +
                " ORDER BY product_id, variant_id", prod_ids).fetchall()
        all_vids = [r["variant_id"] for r in vrows]
        attrs_map = attrs_by_variant(conn, all_vids)
        disp_map = display_attrs(conn, all_vids)
        models_map = _models_by_variant(conn, all_vids)
        sort_keys = variant_sort_keys(conn, all_vids)
        vrows_by_pid = {}
        for v in vrows:
            vrows_by_pid.setdefault(v["product_id"], []).append(v)
        # 變體列排序:單一材質在前(依選項 sort)、combo 在後,同鍵依建檔序
        for vs in vrows_by_pid.values():
            vs.sort(key=lambda v: (sort_keys[v["variant_id"]], v["variant_id"]))

        out = []
        for p in prods:
            variants = []
            for v in vrows_by_pid.get(p["product_id"], []):
                if model_vids is not None and v["variant_id"] not in model_vids:
                    continue
                eff = v["price"] if v["price"] is not None else p["default_price"]
                variants.append({
                    "variant_id": v["variant_id"],
                    "attributes": attrs_map.get(v["variant_id"], {}),
                    "attr_display": disp_map.get(v["variant_id"], ""),
                    "price": v["price"], "effective_price": eff,
                    "stock": stock_of(conn, v["variant_id"]),
                    "active": bool(v["active"]),
                    "models": models_map.get(v["variant_id"], []),
                    "barcodes": bc.get(v["variant_id"], [])})
            if model_vids is not None and not variants:
                continue
            out.append({
                "product_id": p["product_id"], "name": p["name"],
                "category_id": p["category_id"], "category_name": p["category_name"],
                "brand_id": p["brand_id"], "brand_name": p["brand_name"],
                "default_price": p["default_price"],
                "note": p["note"], "active": bool(p["active"]),
                "variants": variants})

        if q:
            like = q.lower()
            filtered = []
            for p in out:
                if like in (p["name"] or "").lower():
                    filtered.append(p)
                    continue
                hit = [v for v in p["variants"]
                       if any(like in str(val).lower()
                              for val in v["attributes"].values())]
                if hit:
                    p["variants"] = hit
                    filtered.append(p)
            out = filtered
        return out
    finally:
        conn.close()

@router.put("/products/{pid}")
def update_product(pid: int, body: ProductPatch, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        fields = body.model_dump(exclude_unset=True)
        if not fields:
            return {"ok": True}
        if fields.get("category_id") is not None:
            _check_category(conn, fields["category_id"])
        if fields.get("brand_id") is not None:
            _check_brand(conn, fields["brand_id"])
        cols = ", ".join(f"{k}=?" for k in fields)
        args = list(fields.values()) + [pid]
        cur = conn.execute(f"UPDATE Product SET {cols} WHERE product_id=?", args)
        if cur.rowcount == 0:
            raise HTTPException(404, "查無此商品")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.put("/variants/{vid}")
def update_variant(vid: int, body: VariantPatch, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        fields = body.model_dump(exclude_unset=True)
        has_attrs = "attributes" in fields
        attributes = fields.pop("attributes", None)
        # 先確認變體存在(取其種類供規格欄解析)
        cat = _variant_category(conn, vid)
        if cat is None and not conn.execute(
                "SELECT 1 FROM Variant WHERE variant_id=?", (vid,)).fetchone():
            raise HTTPException(404, "查無此變體")
        if fields:
            cols = ", ".join(f"{k}=?" for k in fields)
            args = list(fields.values()) + [vid]
            conn.execute(f"UPDATE Variant SET {cols} WHERE variant_id=?", args)
        if has_attrs:
            set_variant_attributes(conn, vid, cat, attributes)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.put("/variants/{vid}/models")
def set_variant_models(vid: int, body: ModelIdList, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        if not conn.execute("SELECT 1 FROM Variant WHERE variant_id=?",
                           (vid,)).fetchone():
            raise HTTPException(404, "查無此變體")
        _set_variant_models(conn, vid, body.model_ids)
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.post("/products/{pid}/variants")
def add_variant(pid: int, body: NewVariantIn, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        cat = _product_category(conn, pid)
        if cat is None and not conn.execute(
                "SELECT 1 FROM Product WHERE product_id=?", (pid,)).fetchone():
            raise HTTPException(404, "查無此商品")
        cur = conn.execute(
            "INSERT INTO Variant(product_id,price) VALUES(?,?)", (pid, body.price))
        vid = cur.lastrowid
        set_variant_attributes(conn, vid, cat, body.attributes)
        _set_variant_models(conn, vid, body.model_ids)
        codes = []
        for b in body.barcodes:
            _reject_manual_tl(b.barcode)
            code = b.barcode or next_store_barcode(conn)
            conn.execute("INSERT INTO Barcode(barcode,variant_id,source) VALUES(?,?,?)",
                         (code, vid, b.source))
            codes.append(code)
        conn.commit()
        return {"variant_id": vid, "barcodes": codes}
    finally:
        conn.close()

@router.delete("/barcodes/{code}")
def delete_barcode(code: str, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        cur = conn.execute("DELETE FROM Barcode WHERE barcode=?", (code,))
        if cur.rowcount == 0:
            raise HTTPException(404, "查無此條碼")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.delete("/variants/{vid}")
def delete_variant(vid: int, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        if not conn.execute("SELECT 1 FROM Variant WHERE variant_id=?", (vid,)).fetchone():
            raise HTTPException(404, "查無此變體")
        if _has_records(conn, [vid]):
            raise HTTPException(409, "該變體已有交易或庫存紀錄,無法刪除,請改用停用")
        conn.execute("DELETE FROM VariantAttribute WHERE variant_id=?", (vid,))
        conn.execute("DELETE FROM VariantModel WHERE variant_id=?", (vid,))
        conn.execute("DELETE FROM Barcode WHERE variant_id=?", (vid,))
        conn.execute("DELETE FROM Variant WHERE variant_id=?", (vid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.delete("/products/{pid}")
def delete_product(pid: int, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        if not conn.execute("SELECT 1 FROM Product WHERE product_id=?", (pid,)).fetchone():
            raise HTTPException(404, "查無此商品")
        vids = [r["variant_id"] for r in conn.execute(
            "SELECT variant_id FROM Variant WHERE product_id=?", (pid,))]
        if _has_records(conn, vids):
            raise HTTPException(409, "該商品已有交易或庫存紀錄,無法刪除,請改用停用")
        if vids:
            qs = ",".join("?" * len(vids))
            conn.execute(f"DELETE FROM VariantAttribute WHERE variant_id IN ({qs})", vids)
            conn.execute(f"DELETE FROM VariantModel WHERE variant_id IN ({qs})", vids)
            conn.execute(f"DELETE FROM Barcode WHERE variant_id IN ({qs})", vids)
            conn.execute(f"DELETE FROM Variant WHERE variant_id IN ({qs})", vids)
        conn.execute("DELETE FROM Product WHERE product_id=?", (pid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
