from lib.application_errors import ValidationError
from lib.db import in_clause, next_sort, stock_map
from lib.product_rules import FIELD_TYPES


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


def set_variant_attributes(conn, vid, category_id, attributes):
    conn.execute("DELETE FROM VariantAttribute WHERE variant_id=?", (vid,))
    for name, value in (attributes or {}).items():
        if _empty(value): continue
        field = conn.execute("SELECT field_id,field_type FROM AttributeField WHERE name=? AND active=1 AND (category_id=? OR category_id IS NULL) ORDER BY (category_id IS NULL) LIMIT 1", (name, category_id)).fetchone()
        if field is None: raise ValidationError(f"規格欄「{name}」不存在")
        fid, kind = field["field_id"], field["field_type"]
        values = value if isinstance(value, (list, tuple)) else [value]
        values = list(dict.fromkeys(str(x).strip() for x in values if str(x).strip()))
        if kind == "text":
            conn.execute("INSERT INTO VariantAttribute(variant_id,field_id,text_value) VALUES(?,?,?)", (vid, fid, str(value)))
            continue
        if kind == "select" and len(values) != 1: raise ValidationError(f"規格欄「{name}」格式不正確")
        for val in values:
            row = conn.execute("SELECT option_id FROM AttributeOption WHERE field_id=? AND value=?", (fid, val)).fetchone()
            if row is None and kind == "tags":
                conn.execute("INSERT OR IGNORE INTO AttributeOption(field_id,value,sort) VALUES(?,?,?)", (fid, val, next_sort(conn, "AttributeOption", "field_id=?", (fid,))))
                row = conn.execute("SELECT option_id FROM AttributeOption WHERE field_id=? AND value=?", (fid, val)).fetchone()
            if row is None: raise ValidationError(f"規格欄「{name}」查無選項「{val}」")
            conn.execute("INSERT INTO VariantAttribute(variant_id,field_id,option_id) VALUES(?,?,?)", (vid, fid, row["option_id"]))


def attr_rows(conn, ids):
    if not ids: return []
    qs=in_clause(ids)
    return conn.execute(f"SELECT va.variant_id,f.name field_name,f.field_type,o.value option_value,va.text_value,(va.option_id IS NOT NULL AND va.option_id=f.default_option_id) is_default FROM VariantAttribute va JOIN AttributeField f ON va.field_id=f.field_id LEFT JOIN AttributeOption o ON va.option_id=o.option_id WHERE va.variant_id IN ({qs}) ORDER BY va.variant_id,f.sort,f.field_id,o.sort,o.option_id", ids).fetchall()


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
    for r in conn.execute(f"SELECT va.variant_id,f.field_type,f.sort fsort,f.field_id,o.sort osort,o.option_id,o.value oval FROM VariantAttribute va JOIN AttributeField f ON va.field_id=f.field_id LEFT JOIN AttributeOption o ON va.option_id=o.option_id WHERE va.variant_id IN ({qs}) ORDER BY va.variant_id,f.sort,f.field_id,o.sort,o.option_id",ids):
        k=keys[r["variant_id"]]
        if r["field_type"]=="tags":
            if r["oval"]=="抗AR":k[1]=1
            if r["osort"] is not None:k[3].append((r["fsort"] or 0,r["field_id"],r["osort"],r["option_id"]))
        else:
            if r["field_type"]=="multi":k[0]+=1
            if r["osort"] is not None:k[2].append((r["fsort"] or 0,r["field_id"],r["osort"],r["option_id"]))
    return {vid:(k[0],k[1],tuple(k[2]),tuple(k[3])) for vid,k in keys.items()}


def stock_of(conn, vid): return conn.execute("SELECT COALESCE(SUM(qty),0) s FROM StockMovement WHERE variant_id=?",(vid,)).fetchone()["s"]
def has_records(conn, ids):
    if not ids:return False
    qs=in_clause(ids)
    return any(conn.execute(f"SELECT 1 FROM {t} WHERE variant_id IN ({qs}) LIMIT 1",ids).fetchone() for t in ("SaleItem","StockMovement"))


def catalog(conn, include_inactive=False, category_id=None, brand_id=None, model_id=None):
    clauses=[];args=[]
    if not include_inactive:clauses.append("p.active=1")
    if category_id is not None:clauses.append("p.category_id=?");args.append(category_id)
    if brand_id is not None:clauses.append("p.brand_id=?");args.append(brand_id)
    where=" WHERE "+" AND ".join(clauses) if clauses else ""
    products=conn.execute("SELECT p.*,c.name category_name,b.name brand_name FROM Product p LEFT JOIN Category c ON p.category_id=c.category_id LEFT JOIN Brand b ON p.brand_id=b.brand_id"+where+" ORDER BY c.sort,p.category_id,b.sort,p.name,p.product_id",args).fetchall()
    pids=[p["product_id"] for p in products]; rows=[]
    if pids:
        qs=in_clause(pids);active="" if include_inactive else " AND active=1"
        rows=conn.execute(f"SELECT * FROM Variant WHERE product_id IN ({qs}){active} ORDER BY product_id,variant_id",pids).fetchall()
    if model_id is not None:
        allowed={r[0] for r in conn.execute("SELECT variant_id FROM VariantModel WHERE model_id=?",(model_id,))};rows=[r for r in rows if r["variant_id"] in allowed]
    ids=[r["variant_id"] for r in rows]; attrs=attrs_by_variant(conn,ids);disp=display_attrs(conn,ids);models=models_by_variant(conn,ids);sorts=variant_sort_keys(conn,ids);stocks=stock_map(conn,ids)
    bars={}
    for r in conn.execute("SELECT variant_id,barcode,source FROM Barcode ORDER BY variant_id,barcode"):bars.setdefault(r["variant_id"],[]).append({"barcode":r["barcode"],"source":r["source"]})
    by={}
    for r in rows:by.setdefault(r["product_id"],[]).append(r)
    out=[]
    for p in products:
        vs=[]
        for r in sorted(by.get(p["product_id"],[]),key=lambda x:(sorts[x["variant_id"]],x["variant_id"])):
            vid=r["variant_id"];vs.append({"variant_id":vid,"attributes":attrs.get(vid,{}),"attr_display":disp.get(vid,""),"price":r["price"],"effective_price":r["price"] if r["price"] is not None else p["default_price"],"stock":stocks.get(vid,0),"active":bool(r["active"]),"models":models.get(vid,[]),"barcodes":bars.get(vid,[])})
        if model_id is not None and not vs:continue
        out.append({"product_id":p["product_id"],"name":p["name"],"category_id":p["category_id"],"category_name":p["category_name"],"brand_id":p["brand_id"],"brand_name":p["brand_name"],"default_price":p["default_price"],"note":p["note"],"active":bool(p["active"]),"variants":vs})
    return out


def filter_catalog(products,q):
    if not q:return products
    q=q.lower();out=[]
    for p in products:
        if q in (p["name"] or "").lower():out.append(p);continue
        hits=[v for v in p["variants"] if any(q in str(x).lower() for x in v["attributes"].values())]
        if hits:p["variants"]=hits;out.append(p)
    return out
