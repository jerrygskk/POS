from lib.application_errors import ValidationError
from lib.db import in_clause, next_sort, stock_map
from lib.normalize import normalize_key
from lib.product_rules import FIELD_TYPES

FEATURE_FIELD_KEY = normalize_key("特性詞條")  # 固定欄位:不需綁定即可使用

# 有效啟用(規格 §8.2):Category.active AND Product.active AND Variant.active
#   AND 沒有未解決的 VariantIssue。以下常數以 c/p/v 別名表示三表;
#   VARIANT_NO_ISSUE 需子查詢的 v 別名即為 Variant。
VARIANT_NO_ISSUE = "NOT EXISTS (SELECT 1 FROM VariantIssue vi WHERE vi.variant_id=v.variant_id)"
EFFECTIVE_ACTIVE = "c.active=1 AND p.active=1 AND v.active=1 AND " + VARIANT_NO_ISSUE


def variant_signature(conn, variant_id, feature_id):
    """正式規格（排除特性詞條）與適用型號的持久化簽章。"""
    signature = set()
    for row in conn.execute(
            "SELECT field_id, option_id, text_value FROM VariantAttribute "
            "WHERE variant_id=?", (variant_id,)):
        if feature_id is not None and row["field_id"] == feature_id:
            continue
        if row["option_id"] is not None:
            signature.add((row["field_id"], "o", row["option_id"]))
        elif (row["text_value"] or "").strip():
            signature.add((row["field_id"], "t", row["text_value"]))
    for row in conn.execute(
            "SELECT model_id FROM VariantModel WHERE variant_id=?", (variant_id,)):
        signature.add(("m", row["model_id"]))
    return frozenset(signature)


def set_variant_models(conn, variant_id, model_ids):
    try:
        conn.execute("DELETE FROM VariantModel WHERE variant_id=?", (variant_id,))
        for model_id in dict.fromkeys(model_ids):
            conn.execute("INSERT OR IGNORE INTO VariantModel(variant_id,model_id) VALUES(?,?)",
                         (variant_id, model_id))
    except Exception as exc:
        raise ValidationError("型號不存在") from exc


def models_by_variant(conn, ids):
    out = {}
    if not ids: return out
    qs = in_clause(ids)
    for r in conn.execute(f"SELECT vm.variant_id,COALESCE(NULLIF(m.alias,''),m.name) name FROM VariantModel vm JOIN PhoneModel m ON vm.model_id=m.model_id WHERE vm.variant_id IN ({qs}) ORDER BY m.sort,m.model_id", ids):
        out.setdefault(r["variant_id"], []).append(r["name"])
    return out


def _empty(v):
    if v is None or v == "": return True
    if isinstance(v, (list, tuple)): return not any(str(x).strip() for x in v)
    return not str(v).strip()


def _resolve_field(conn, name, category_id):
    """依欄名解析欄位:須為該種類已綁定且啟用的模板欄位;特性詞條為全域固定例外。"""
    field = conn.execute(
        "SELECT f.field_id,f.field_type FROM AttributeField f "
        "JOIN CategoryField cf ON cf.field_id=f.field_id "
        "WHERE f.name=? AND f.active=1 AND cf.category_id=? AND cf.active=1 LIMIT 1",
        (name, category_id)).fetchone()
    if field is not None:
        return field
    if normalize_key(name) == FEATURE_FIELD_KEY:
        return conn.execute(
            "SELECT field_id,field_type FROM AttributeField WHERE name=? AND active=1 "
            "ORDER BY field_id LIMIT 1", (name,)).fetchone()
    return None


def cleanup_unused_options(conn, option_ids):
    """硬刪使用數已歸零且非任何種類模板預設值的選項(連同 OptionModel)。

    傳入候選 option_id 集合(通常為剛因子產品修改/刪除而被移除引用者);
    逐一檢查:仍被任何 VariantAttribute 引用者跳過;被任何
    CategoryField.default_option_id 引用者跳過;其餘硬刪。回傳實際刪除的 id 清單。"""
    deleted = []
    for oid in {o for o in (option_ids or ()) if o is not None}:
        if conn.execute("SELECT 1 FROM VariantAttribute WHERE option_id=? LIMIT 1",
                        (oid,)).fetchone():
            continue
        if conn.execute("SELECT 1 FROM CategoryField WHERE default_option_id=? LIMIT 1",
                        (oid,)).fetchone():
            continue
        conn.execute("DELETE FROM OptionModel WHERE option_id=?", (oid,))
        conn.execute("DELETE FROM AttributeOption WHERE option_id=?", (oid,))
        deleted.append(oid)
    return deleted


def set_variant_attributes(conn, vid, category_id, attributes):
    """寫入子產品規格值。規格 §12.3:讀取修改前現值逐值比較——
    原已存在的停用選項可保留或移除,不得新增停用選項值(新建亦不得直接指定停用值)。
    停用/未綁定欄位的既有值原樣保留,不由本次覆寫刪除。"""
    # 修改前現值:逐欄 option_id 集合(供停用值差異驗證);另收集全部舊引用選項,
    # 供覆寫後清理使用數歸零的孤兒選項。
    prev = {}
    prev_option_ids = set()
    for r in conn.execute(
            "SELECT field_id, option_id FROM VariantAttribute "
            "WHERE variant_id=? AND option_id IS NOT NULL", (vid,)):
        prev.setdefault(r["field_id"], set()).add(r["option_id"])
        prev_option_ids.add(r["option_id"])
    # 本次可覆寫的欄位=該種類啟用中的模板欄位(+特性詞條);其餘欄位既有值保留
    editable = {r[0] for r in conn.execute(
        "SELECT f.field_id FROM AttributeField f JOIN CategoryField cf ON cf.field_id=f.field_id "
        "WHERE cf.category_id=? AND cf.active=1 AND f.active=1", (category_id,))}
    if editable:
        qs = in_clause(list(editable))
        conn.execute(f"DELETE FROM VariantAttribute WHERE variant_id=? AND field_id IN ({qs})",
                     (vid, *editable))
    for name, value in (attributes or {}).items():
        if _empty(value): continue
        field = _resolve_field(conn, name, category_id)
        if field is None: raise ValidationError(f"規格欄「{name}」不存在或未套用於此種類")
        fid, kind = field["field_id"], field["field_type"]
        if fid not in editable:
            # 特性詞條等未在 editable 名單者,先清本欄既有值再寫入
            conn.execute("DELETE FROM VariantAttribute WHERE variant_id=? AND field_id=?", (vid, fid))
        values = value if isinstance(value, (list, tuple)) else [value]
        values = list(dict.fromkeys(str(x).strip() for x in values if str(x).strip()))
        if kind == "text":
            conn.execute("INSERT INTO VariantAttribute(variant_id,field_id,text_value) VALUES(?,?,?)", (vid, fid, str(value)))
            continue
        if kind == "select" and len(values) != 1: raise ValidationError(f"規格欄「{name}」格式不正確")
        original = prev.get(fid, set())
        for val in values:
            row = conn.execute("SELECT option_id,active FROM AttributeOption WHERE field_id=? AND value=?", (fid, val)).fetchone()
            if row is None and kind == "tags":
                conn.execute("INSERT OR IGNORE INTO AttributeOption(field_id,value,sort) VALUES(?,?,?)", (fid, val, next_sort(conn, "AttributeOption", "field_id=?", (fid,))))
                row = conn.execute("SELECT option_id,active FROM AttributeOption WHERE field_id=? AND value=?", (fid, val)).fetchone()
            if row is None: raise ValidationError(f"規格欄「{name}」查無選項「{val}」")
            oid = row["option_id"]
            # §12.3:停用選項僅允許沿用既有值,不得新增
            if not row["active"] and oid not in original:
                raise ValidationError(f"規格欄「{name}」的選項「{val}」已停用,不可指定")
            conn.execute("INSERT INTO VariantAttribute(variant_id,field_id,option_id) VALUES(?,?,?)", (vid, fid, oid))
    # 覆寫後清理:舊引用中已無任何子產品使用者硬刪(default 引用除外)
    cleanup_unused_options(conn, prev_option_ids)


def attr_rows(conn, ids):
    if not ids: return []
    qs=in_clause(ids)
    return conn.execute(f"SELECT va.variant_id,f.name field_name,f.field_type,o.value option_value,va.text_value,(va.option_id IS NOT NULL AND va.option_id=cf.default_option_id) is_default FROM VariantAttribute va JOIN AttributeField f ON va.field_id=f.field_id JOIN Variant v ON va.variant_id=v.variant_id JOIN Product p ON v.product_id=p.product_id LEFT JOIN CategoryField cf ON cf.field_id=va.field_id AND cf.category_id=p.category_id LEFT JOIN AttributeOption o ON va.option_id=o.option_id WHERE va.variant_id IN ({qs}) ORDER BY va.variant_id,cf.sort,f.field_id,o.sort,o.option_id", ids).fetchall()


def attrs_by_variant(conn, ids):
    out={}
    for r in attr_rows(conn,ids):
        d=out.setdefault(r["variant_id"],{})
        if r["field_type"] in ("multi","tags"): d.setdefault(r["field_name"],[]).append(r["option_value"])
        else: d[r["field_name"]]=r["option_value"] if r["option_value"] is not None else r["text_value"]
    return out


def display_attrs(conn, ids):
    acc={}
    for r in attr_rows(conn,ids):
        fields=acc.setdefault(r["variant_id"],[]); kind=r["field_type"]
        if kind in ("multi","tags"):
            if fields and fields[-1][0]==r["field_name"]: fields[-1][2].append(r["option_value"])
            else: fields.append([r["field_name"],kind,[r["option_value"]]])
        elif not r["is_default"]: fields.append([r["field_name"],kind,r["option_value"] if r["option_value"] is not None else r["text_value"]])
    return {vid:"｜".join(("+" if k=="multi" else ", ").join(v) if k in ("multi","tags") else str(v) for _,k,v in fields) for vid,fields in acc.items()}


def variant_sort_keys(conn, ids):
    keys={vid:[0,0,[],[]] for vid in ids}
    if not ids:return {}
    qs=in_clause(ids)
    for r in conn.execute(f"SELECT va.variant_id,f.field_type,cf.sort fsort,f.field_id,o.sort osort,o.option_id,o.value oval FROM VariantAttribute va JOIN AttributeField f ON va.field_id=f.field_id JOIN Variant v ON va.variant_id=v.variant_id JOIN Product p ON v.product_id=p.product_id LEFT JOIN CategoryField cf ON cf.field_id=va.field_id AND cf.category_id=p.category_id LEFT JOIN AttributeOption o ON va.option_id=o.option_id WHERE va.variant_id IN ({qs}) ORDER BY va.variant_id,cf.sort,f.field_id,o.sort,o.option_id",ids):
        k=keys[r["variant_id"]]
        if r["field_type"]=="tags":
            if r["oval"]=="抗AR":k[1]=1
            if r["osort"] is not None:k[3].append((r["fsort"] or 0,r["field_id"],r["osort"],r["option_id"]))
        else:
            if r["field_type"]=="multi":k[0]+=1
            if r["osort"] is not None:k[2].append((r["fsort"] or 0,r["field_id"],r["osort"],r["option_id"]))
    return {vid:(k[0],k[1],tuple(k[2]),tuple(k[3])) for vid,k in keys.items()}


def option_usage_in_category(conn, field_id, category_id):
    """回傳指定欄位在指定種類內各選項的使用次數排序清單。
    可重用:特性詞條選取器與後續規格候選選單皆走「該種類用過的值優先＋搜尋全部」。
    使用次數=該種類內以此選項為值的子產品數。
    排序:使用次數多→少、既有 sort、選項值、option_id(穩定)。含停用選項(帶 active)。"""
    rows = conn.execute(
        "SELECT o.option_id, o.value, o.active, o.sort, "
        "COUNT(DISTINCT va.variant_id) usage_count "
        "FROM AttributeOption o "
        "LEFT JOIN VariantAttribute va ON va.option_id=o.option_id "
        "AND va.variant_id IN (SELECT v.variant_id FROM Variant v "
        "JOIN Product p ON v.product_id=p.product_id WHERE p.category_id=?) "
        "WHERE o.field_id=? "
        "GROUP BY o.option_id ORDER BY usage_count DESC, o.sort, o.value, o.option_id",
        (category_id, field_id)).fetchall()
    # 各選項限定型號(特別色):供候選清單依適用型號過濾;未綁型號者恆通用
    om = {}
    for r in conn.execute(
            "SELECT om.option_id, om.model_id FROM OptionModel om "
            "JOIN AttributeOption o ON o.option_id=om.option_id "
            "WHERE o.field_id=? ORDER BY om.model_id", (field_id,)):
        om.setdefault(r["option_id"], []).append(r["model_id"])
    return [{"option_id": r["option_id"], "value": r["value"],
             "active": bool(r["active"]), "usage_count": r["usage_count"],
             "model_ids": om.get(r["option_id"], [])} for r in rows]


def stock_of(conn, vid): return conn.execute("SELECT COALESCE(SUM(qty),0) s FROM StockMovement WHERE variant_id=?",(vid,)).fetchone()["s"]
def has_records(conn, ids):
    if not ids:return False
    qs=in_clause(ids)
    return any(conn.execute(f"SELECT 1 FROM {t} WHERE variant_id IN ({qs}) LIMIT 1",ids).fetchone() for t in ("SaleItem","StockMovement"))


def variant_issues(conn, ids):
    """回傳 {variant_id: [issue_dict,...]}。issue_dict 含 issue_type、field_id、
    field_name(缺必填欄名)、source_value、related_variant_id 與 related_label
    (對照子產品的「大產品名 規格 條碼」摘要),供前端一次列出全部問題。"""
    out = {}
    if not ids:
        return out
    qs = in_clause(ids)
    rows = conn.execute(
        f"SELECT vi.issue_id, vi.variant_id, vi.issue_type, vi.field_id, "
        f"vi.source_value, vi.related_variant_id, f.name field_name "
        f"FROM VariantIssue vi LEFT JOIN AttributeField f ON vi.field_id=f.field_id "
        f"WHERE vi.variant_id IN ({qs}) ORDER BY vi.variant_id, vi.issue_id", ids).fetchall()
    related_ids = [r["related_variant_id"] for r in rows if r["related_variant_id"] is not None]
    labels = _variant_labels(conn, related_ids)
    for r in rows:
        out.setdefault(r["variant_id"], []).append({
            "issue_type": r["issue_type"], "field_id": r["field_id"],
            "field_name": r["field_name"], "source_value": r["source_value"],
            "related_variant_id": r["related_variant_id"],
            "related_label": labels.get(r["related_variant_id"], "")})
    return out


def _variant_labels(conn, ids):
    """對照子產品摘要:{variant_id: '大產品名｜規格｜條碼'}。"""
    out = {}
    ids = [i for i in dict.fromkeys(ids) if i is not None]
    if not ids:
        return out
    qs = in_clause(ids)
    names = {r["variant_id"]: r["name"] for r in conn.execute(
        f"SELECT v.variant_id, p.name FROM Variant v JOIN Product p "
        f"ON v.product_id=p.product_id WHERE v.variant_id IN ({qs})", ids)}
    disp = display_attrs(conn, ids)
    bars = {}
    for r in conn.execute(
            f"SELECT variant_id, barcode FROM Barcode WHERE variant_id IN ({qs}) "
            f"ORDER BY variant_id, barcode", ids):
        bars.setdefault(r["variant_id"], []).append(r["barcode"])
    for vid in ids:
        parts = [names.get(vid, "")]
        if disp.get(vid):
            parts.append(disp[vid])
        if bars.get(vid):
            parts.append("、".join(bars[vid]))
        out[vid] = "｜".join(p for p in parts if p)
    return out


def catalog(conn, include_inactive=False, category_id=None, brand_id=None, model_id=None,
            pending=False):
    clauses=[];args=[]
    if not include_inactive:clauses.append("p.active=1 AND (c.active=1 OR c.category_id IS NULL)")
    if category_id is not None:clauses.append("p.category_id=?");args.append(category_id)
    if brand_id is not None:clauses.append("p.brand_id=?");args.append(brand_id)
    where=" WHERE "+" AND ".join(clauses) if clauses else ""
    products=conn.execute("SELECT p.*,c.name category_name,b.name brand_name FROM Product p LEFT JOIN Category c ON p.category_id=c.category_id LEFT JOIN Brand b ON p.brand_id=b.brand_id"+where+" ORDER BY c.sort,p.category_id,b.sort,p.name,p.product_id",args).fetchall()
    pids=[p["product_id"] for p in products]; rows=[]
    # 待處理篩選需納入停用中的問題子產品(問題筆恆停用)
    if pids:
        qs=in_clause(pids);active="" if (include_inactive or pending) else " AND active=1"
        rows=conn.execute(f"SELECT * FROM Variant WHERE product_id IN ({qs}){active} ORDER BY product_id,variant_id",pids).fetchall()
    if model_id is not None:
        allowed={r[0] for r in conn.execute("SELECT variant_id FROM VariantModel WHERE model_id=?",(model_id,))};rows=[r for r in rows if r["variant_id"] in allowed]
    ids=[r["variant_id"] for r in rows]; attrs=attrs_by_variant(conn,ids);disp=display_attrs(conn,ids);models=models_by_variant(conn,ids);sorts=variant_sort_keys(conn,ids);stocks=stock_map(conn,ids);issues=variant_issues(conn,ids)
    if pending:
        rows=[r for r in rows if issues.get(r["variant_id"])]
    bars={}
    for r in conn.execute("SELECT variant_id,barcode,source FROM Barcode ORDER BY variant_id,barcode"):bars.setdefault(r["variant_id"],[]).append({"barcode":r["barcode"],"source":r["source"]})
    by={}
    for r in rows:by.setdefault(r["product_id"],[]).append(r)
    out=[]
    for p in products:
        vs=[]
        for r in sorted(by.get(p["product_id"],[]),key=lambda x:(sorts[x["variant_id"]],x["variant_id"])):
            vid=r["variant_id"];vs.append({"variant_id":vid,"attributes":attrs.get(vid,{}),"attr_display":disp.get(vid,""),"price":r["price"],"effective_price":r["price"],"stock":stocks.get(vid,0),"active":bool(r["active"]),"models":models.get(vid,[]),"barcodes":bars.get(vid,[]),"issues":issues.get(vid,[])})
        if (model_id is not None or pending) and not vs:continue
        out.append({"product_id":p["product_id"],"name":p["name"],"category_id":p["category_id"],"category_name":p["category_name"],"brand_id":p["brand_id"],"brand_name":p["brand_name"],"note":p["note"],"active":bool(p["active"]),"variants":vs})
    return out


def filter_catalog(products,q):
    if not q:return products
    q=q.lower();out=[]
    for p in products:
        if q in (p["name"] or "").lower():out.append(p);continue
        hits=[v for v in p["variants"] if any(q in str(x).lower() for x in v["attributes"].values())]
        if hits:p["variants"]=hits;out.append(p)
    return out
