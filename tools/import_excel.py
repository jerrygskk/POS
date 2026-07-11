"""匯入工具:docs/產品清單.xlsm「商品資料庫」工作表 → pos.db(新 schema v3)。

獨立、可重跑的轉檔工具(不入常駐程式)。以條碼判重:已存在的條碼整列跳過,
故可重複執行而不重覆建檔。

用法:
    python tools/import_excel.py [--category 鋼化玻璃] [--db data/pos.db]
                                 [--excel docs/產品清單.xlsm]

新 schema 對應:
- 商品種類  → Category
- 廠牌      → Brand(正規化去種類/子類尾綴)+ BrandCategory(廠牌×種類)
- 手機品牌  → PhoneBrand
- 手機型號  → PhoneModel(拆解共用字串,一變體掛多型號 VariantModel)
- 規格      → 該種類專屬 select 欄「規格」→ AttributeOption(自動補建)→ VariantAttribute
- 分類1     → 該種類專屬 select 欄「分類1」(僅該種類有值時建)→ 同上
- 分類2     → 該種類專屬 select 欄「分類2」(僅該種類有值時建)→ 同上
- 商品描述  → 既有共用 text 欄「商品描述」(CategoryField 勾選啟用)→ VariantAttribute(text)
- 備註      → Product.note
- 商品編碼  → Barcode(TL 開頭=自取條碼 source='store';其餘=原廠碼 source='factory';判重可重跑)
- 價格      → Excel 無 → NULL,日後維護頁補

⚠️ Excel 內含真實人名欄(登陸人/最後進貨人),本工具一律不讀入、不列印這些欄。
   庫存欄暫緩不匯。
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib.db import get_conn, init_db

# ---- Excel 欄名(人名/庫存/日期欄一律不列於此,不讀入)----
COL_CODE = "商品編碼"
COL_CATEGORY = "商品種類"
COL_BRAND = "廠牌"
COL_SPEC = "規格"
COL_DESC = "商品描述"
COL_CAT1 = "分類1"
COL_CAT2 = "分類2"
COL_PHONE_BRAND = "手機品牌"
COL_PHONE_MODEL = "手機型號"
COL_NOTE = "備註"

# 該種類專屬 select 欄:欄名沿用 Excel 欄名,只在該種類實際有值時建
CATEGORY_SELECT_COLS = [COL_SPEC, COL_CAT1, COL_CAT2]
# 既有共用 text 欄(種子已建):以 CategoryField 勾選啟用
SHARED_DESC_FIELD = "商品描述"

WANTED_COLS = [COL_CODE, COL_CATEGORY, COL_BRAND, COL_SPEC, COL_DESC,
               COL_CAT1, COL_CAT2, COL_PHONE_BRAND, COL_PHONE_MODEL, COL_NOTE]


# ================= 純函式(可單測、不碰 DB)=================

def clean(value):
    """儲存格值 → 去空白字串;空字串/None/'nan' 一律回 None。"""
    if value is None:
        return None
    s = str(value).strip()
    if s == "" or s.lower() == "nan":
        return None
    return s


# ---- 廠牌正規化 ----
# 中英並列品牌全名保留原樣;尾綴為種類/子類/產品線者去除。
BRAND_ALIASES = {
    "Dr.TOUGH硬博士": "Dr.TOUGH硬博士",
    "RS犀牛盾": "RS犀牛盾",
    "Solide索立得": "Solide索立得",
    "Baseus倍思": "Baseus倍思",
    "HODA": "HODA",
    "imos": "imos",
    "ETON": "ETON",
    "imos鏡頭貼": "imos",
    "imos手機殼": "imos",
    "UNIQTOUGH鏡頭貼": "UNIQTOUGH",
    "UNIQ手機殼": "UNIQ",
    "SK手機殼": "SK",
    "DAPAD手機殼": "DAPAD",
    "DAPAD四角": "DAPAD",
    "Garmma手機殼": "Garmma",
    "XMART皮套": "XMART",
    "Mageasy手機殼": "Mageasy",
    "DEVILCASE手機殼": "DEVILCASE",
    "AI空壓殼": "AI空壓殼",
    "COZY五倍強化": "COZY",
    "COZY微晶盾": "COZY",
}

# 敘述而非廠牌,無法還原純廠牌,保留原字串並記警告
NON_BRANDS = {"多卡槽牛皮皮套"}

# 規則兜底時可去除的尾綴(種類/子類/產品線)
_BRAND_SUFFIXES = [
    "手機殼", "鏡頭貼", "皮套", "充電線", "插座", "行動電源", "藍芽耳機",
    "空壓殼", "五倍強化", "微晶盾", "四角",
]


def normalize_brand(brand_str, category):
    """複合廠牌字串 → 純廠牌名(字串)。拆不出者回傳原字串。

    先查顯式對照表,再以規則兜底(去掉種類/子類尾綴)。
    """
    if brand_str is None:
        return None
    s = str(brand_str).strip()
    if not s:
        return None
    if s in NON_BRANDS:
        return s
    if s in BRAND_ALIASES:
        return BRAND_ALIASES[s]
    out = s
    if category and out.endswith(str(category)) and len(out) > len(str(category)):
        out = out[: -len(str(category))]
    for suf in _BRAND_SUFFIXES:
        if out.endswith(suf) and len(out) > len(suf):
            out = out[: -len(suf)]
            break
    out = out.strip()
    return out or s


def is_resolvable_brand(brand_str):
    """該廠牌字串是否可解析為純廠牌(否則匯入時列警告)。"""
    if brand_str is None:
        return True
    return str(brand_str).strip() not in NON_BRANDS


# ---- 手機型號拆解 ----
_MODEL_SUFFIXES = [
    ("promax", " Pro Max"),
    ("pro", " Pro"),
    ("plus", " Plus"),
    ("air", " Air"),
    ("mini", " mini"),
    ("max", " Max"),
]


def _canon_model(token, brand_prefix="iPhone"):
    """單一型號片段 → 標準型號名;無法解析回傳 None。"""
    t = re.sub(r"\(.*?\)", "", token)          # 去尺寸括號 (6.1)
    for junk in ("共用款", "共用", "一般"):
        t = t.replace(junk, "")
    t = t.strip().strip("/").strip()
    if not t:
        return None
    m = re.match(r"^\s*(iphone|ipone)\s*", t, re.I)   # 去(含錯字)前綴
    if m:
        t = t[m.end():].strip()
    low = t.lower().replace(" ", "")
    if not low:
        return None
    mm = re.match(r"^(se\d|xsmax|xr|xs|\d{1,2})(.*)$", low)
    if not mm:
        return None
    base, rest = mm.group(1), mm.group(2)
    if base in ("xr", "xs"):
        base_disp = base.upper()
    elif base == "xsmax":
        base_disp = "XS Max"
    elif base.startswith("se"):
        base_disp = base.upper()
    else:
        base_disp = base
    suffix = ""
    while rest:
        for key, disp in _MODEL_SUFFIXES:
            if rest.startswith(key):
                suffix += disp
                rest = rest[len(key):]
                break
        else:
            return None                        # 不認得的尾綴
    return f"{brand_prefix} {base_disp}{suffix}"


def split_models(model_str, brand_prefix="iPhone"):
    """共用型號字串 → 標準型號名列表。

    回傳 (型號列表, 拆不動的片段列表)。以「/」拆,補全省略的品牌前綴,
    去尺寸/共用後綴,同型號收斂為一筆。任何片段拆不動則保留原片段並列入警告。
    """
    if model_str is None:
        return [], []
    s = str(model_str).strip()
    if not s:
        return [], []
    models = []
    warnings = []
    for seg in s.split("/"):
        canon = _canon_model(seg, brand_prefix)
        if canon is None:
            frag = seg.strip()
            if frag:
                warnings.append(frag)
        elif canon not in models:
            models.append(canon)
    if not models and s not in warnings:
        warnings.append(s)                     # 整串拆不動 → 保留原字串
    return models, warnings


# ---- 列解析 / 分組鍵 ----

def parse_row(raw):
    """{Excel欄名: 原值} → 結構化 record(不碰 DB);無商品編碼回 None。

    record 欄位:
      barcode, category, brand_raw, brand, brand_resolvable,
      phone_brand, models(list), model_warnings(list),
      select_attrs({欄名:值}), desc, note
    """
    barcode = clean(raw.get(COL_CODE))
    if barcode is None:
        return None
    category = clean(raw.get(COL_CATEGORY))
    brand_raw = clean(raw.get(COL_BRAND))
    brand = normalize_brand(brand_raw, category)
    phone_brand = clean(raw.get(COL_PHONE_BRAND))
    model_str = clean(raw.get(COL_PHONE_MODEL))
    if model_str and phone_brand:
        models, model_warnings = split_models(model_str, phone_brand)
    else:
        models, model_warnings = [], []
    select_attrs = {}
    for col in CATEGORY_SELECT_COLS:
        v = clean(raw.get(col))
        if v is not None:
            select_attrs[col] = v
    return {
        "barcode": barcode,
        "category": category,
        "brand_raw": brand_raw,
        "brand": brand,
        "brand_resolvable": is_resolvable_brand(brand_raw),
        "phone_brand": phone_brand,
        "models": models,
        "model_warnings": model_warnings,
        "select_attrs": select_attrs,
        "desc": clean(raw.get(COL_DESC)),
        "note": clean(raw.get(COL_NOTE)),
    }


def product_key(rec):
    """款分組鍵:同 種類+廠牌 歸為一款(參考舊版分組邏輯)。"""
    return (rec["category"], rec["brand"])


def product_name(rec):
    """款名:沿用舊版命名邏輯「廠牌 種類」;無廠牌則僅種類。"""
    if rec["brand"]:
        return f"{rec['brand']} {rec['category']}"
    return rec["category"] or "未分類"


# ================= 鋼化玻璃規格模型(spec §2、§3 內建對照表)=================

GLASS_CATEGORY = "鋼化玻璃"
GLASS_SPEC_FIELD = "規格"          # multi:亮面/霧面/藍光/防窺
GLASS_TAGS_FIELD = "特性詞條"      # tags:自動長
GLASS_LAYOUT_FIELD = "版型"        # select:滿版/9分滿,預設滿版(原分類1改名)
GLASS_BASE_WORDS = ["亮面", "霧面", "藍光", "防窺"]
GLASS_LAYOUT_OPTIONS = ["滿版", "9分滿"]
GLASS_LAYOUT_DEFAULT = "滿版"

# 規格值 → (勾選基礎詞, 詞條)。spec §3 二十四值內建對照表。
GLASS_SPEC_MAP = {
    "亮面": (["亮面"], []),
    "亮面滿版": (["亮面"], []),
    "霧面": (["霧面"], []),
    "霧面滿版": (["霧面"], []),
    "藍光": (["藍光"], []),
    "藍光滿版": (["藍光"], []),
    "防窺": (["防窺"], []),
    "防窺滿版": (["防窺"], []),
    "霧面藍光": (["霧面", "藍光"], []),
    "霧面防窺": (["霧面", "防窺"], []),
    "抗AR亮面": (["亮面"], ["抗AR"]),
    "抗AR霧面": (["霧面"], ["抗AR"]),
    "抗AR藍光": (["藍光"], ["抗AR"]),
    "抗AR防窺": (["防窺"], ["抗AR"]),
    "360防窺": (["防窺"], ["360度"]),
    "霧面防窺(360度)": (["霧面", "防窺"], ["360度"]),
    "五倍防窺": (["防窺"], ["五倍強化"]),
    "電競霧面": (["霧面"], ["電競"]),
    "亮面藍寶石": (["亮面"], ["藍寶石"]),
    "9M藍寶石": (["亮面"], ["9M藍寶石"]),          # 完整一詞,不拆;補預設亮面
    "SGS認證無色偏藍光": (["藍光"], ["SGS認證", "無色偏"]),
    "霧面藍光(SGS認證)": (["霧面", "藍光"], ["SGS認證"]),
    "低藍光防窺": (["藍光", "防窺"], ["低藍光"]),
    "藍寶石低藍光": (["藍光"], ["藍寶石", "低藍光"]),
}


def _glass_fallback(s):
    """未列於對照表的規格值:以通則兜底偵測基礎詞(低藍光算藍光)。"""
    bases, tags = [], []
    tmp = s
    if "低藍光" in tmp:
        bases.append("藍光")
        tags.append("低藍光")
        tmp = tmp.replace("低藍光", "")
    for w in GLASS_BASE_WORDS:
        if w in tmp and w not in bases:
            bases.append(w)
    return bases, tags


def glass_spec(value):
    """鋼化玻璃 規格值 → (基礎詞 list, 詞條 list),遵守 spec §3。

    先查內建對照表,未列者以通則兜底。通則:四基礎詞皆未出現 → 補亮面;
    基礎詞依標準順位(亮面/霧面/藍光/防窺)排序。
    """
    if value is None:
        bases, tags = [], []
    else:
        s = str(value).strip()
        if s in GLASS_SPEC_MAP:
            b, t = GLASS_SPEC_MAP[s]
            bases, tags = list(b), list(t)
        else:
            bases, tags = _glass_fallback(s)
    if not bases:                       # 四基礎皆未現 → 補亮面
        bases = ["亮面"]
    bases = [b for b in GLASS_BASE_WORDS if b in bases]   # 依標準順位
    return bases, tags


def glass_layout(cat1_value):
    """分類1 → (版型值, 額外詞條 list)。
    滿版/空白→滿版;滿版(白)→滿版+詞條白;滿版(黑)→滿版+詞條黑。"""
    s = str(cat1_value).strip() if cat1_value is not None else ""
    if s in ("", "滿版"):
        return "滿版", []
    if s == "滿版(白)":
        return "滿版", ["白"]
    if s == "滿版(黑)":
        return "滿版", ["黑"]
    return s, []                        # 未預期值:原樣當版型


def glass_attrs(spec_value, cat1_value):
    """整列鋼化玻璃規格 → {規格:[基礎詞...], 特性詞條:[詞條...], 版型:值}。
    版型的白/黑併入特性詞條(去重、保序)。"""
    bases, tags = glass_spec(spec_value)
    layout, extra = glass_layout(cat1_value)
    for t in extra:
        if t not in tags:
            tags.append(t)
    return {GLASS_SPEC_FIELD: bases, GLASS_TAGS_FIELD: tags,
            GLASS_LAYOUT_FIELD: layout}


# ================= Excel 讀取 =================

def load_rows(xlsm_path, category=None):
    """讀「商品資料庫」工作表 → parse_row 後的 record 清單。

    只保留本工具要用的欄(人名/庫存/日期欄不進 dict)。category 給定時只回該種類。
    .xlsm 以 read_only + data_only 讀取。
    """
    import openpyxl
    wb = openpyxl.load_workbook(xlsm_path, read_only=True, data_only=True)
    ws = wb["商品資料庫"]
    it = ws.iter_rows(values_only=True)
    headers = [str(h) if h is not None else "" for h in next(it)]
    idx = {h: i for i, h in enumerate(headers)}
    records = []
    for raw in it:
        if not any(v is not None for v in raw):
            continue
        cell = {}
        for col in WANTED_COLS:
            i = idx.get(col)
            cell[col] = raw[i] if (i is not None and i < len(raw)) else None
        rec = parse_row(cell)
        if rec is None:
            continue
        if category is not None and rec["category"] != category:
            continue
        records.append(rec)
    wb.close()
    return records


# ================= DB find-or-create 輔助 =================

def _get_or_create_category(conn, name):
    row = conn.execute("SELECT category_id FROM Category WHERE name=?",
                       (name,)).fetchone()
    if row:
        return row["category_id"]
    return conn.execute("INSERT INTO Category(name) VALUES(?)", (name,)).lastrowid


def _get_or_create_brand(conn, name):
    row = conn.execute("SELECT brand_id FROM Brand WHERE name=?", (name,)).fetchone()
    if row:
        return row["brand_id"]
    return conn.execute("INSERT INTO Brand(name) VALUES(?)", (name,)).lastrowid


def _link_brand_category(conn, brand_id, category_id):
    conn.execute("INSERT OR IGNORE INTO BrandCategory(brand_id,category_id) "
                 "VALUES(?,?)", (brand_id, category_id))


def _get_or_create_field(conn, name, category_id, field_type="select"):
    if category_id is None:
        row = conn.execute("SELECT field_id FROM AttributeField "
                           "WHERE name=? AND category_id IS NULL",
                           (name,)).fetchone()
    else:
        row = conn.execute("SELECT field_id FROM AttributeField "
                           "WHERE name=? AND category_id=?",
                           (name, category_id)).fetchone()
    if row:
        return row["field_id"]
    return conn.execute(
        "INSERT INTO AttributeField(name,category_id,field_type,sort) "
        "VALUES(?,?,?,(SELECT COALESCE(MAX(sort),0)+1 FROM AttributeField))",
        (name, category_id, field_type)).lastrowid


def _get_or_create_option(conn, field_id, value):
    row = conn.execute(
        "SELECT option_id FROM AttributeOption WHERE field_id=? AND value=?",
        (field_id, value)).fetchone()
    if row:
        return row["option_id"]
    return conn.execute(
        "INSERT INTO AttributeOption(field_id,value,sort) "
        "VALUES(?,?,(SELECT COALESCE(MAX(sort),0)+1 FROM AttributeOption "
        "WHERE field_id=?))", (field_id, value, field_id)).lastrowid


def _link_category_field(conn, category_id, field_id):
    conn.execute("INSERT OR IGNORE INTO CategoryField(category_id,field_id) "
                 "VALUES(?,?)", (category_id, field_id))


def _get_or_create_phone_brand(conn, name):
    row = conn.execute("SELECT phone_brand_id FROM PhoneBrand WHERE name=?",
                       (name,)).fetchone()
    if row:
        return row["phone_brand_id"]
    return conn.execute("INSERT INTO PhoneBrand(name) VALUES(?)",
                        (name,)).lastrowid


def _get_or_create_model(conn, phone_brand_id, name):
    row = conn.execute(
        "SELECT model_id FROM PhoneModel WHERE phone_brand_id=? AND name=?",
        (phone_brand_id, name)).fetchone()
    if row:
        return row["model_id"]
    return conn.execute("INSERT INTO PhoneModel(phone_brand_id,name) VALUES(?,?)",
                        (phone_brand_id, name)).lastrowid


def _shared_field_id(conn, name):
    row = conn.execute("SELECT field_id FROM AttributeField "
                       "WHERE name=? AND category_id IS NULL", (name,)).fetchone()
    return row["field_id"] if row else None


def _ensure_glass_fields(conn, cid):
    """建立鋼化玻璃三欄並回傳 (規格_fid, 特性詞條_fid, 版型_fid)。
    欄 sort:規格 < 特性詞條 < 版型(顯示順位 基礎→詞條→版型)。
    規格四基礎選項依順位建立;版型 滿版/9分滿,預設滿版。"""
    spec_fid = _get_or_create_field(conn, GLASS_SPEC_FIELD, cid, "multi")
    tags_fid = _get_or_create_field(conn, GLASS_TAGS_FIELD, cid, "tags")
    layout_fid = _get_or_create_field(conn, GLASS_LAYOUT_FIELD, cid, "select")
    for i, fid in enumerate((spec_fid, tags_fid, layout_fid)):
        conn.execute("UPDATE AttributeField SET sort=? WHERE field_id=?",
                     (100 + i, fid))
    for w in GLASS_BASE_WORDS:                     # 依順位建立 → 選項 sort 遞增
        _get_or_create_option(conn, spec_fid, w)
    for v in GLASS_LAYOUT_OPTIONS:
        _get_or_create_option(conn, layout_fid, v)
    default_oid = _get_or_create_option(conn, layout_fid, GLASS_LAYOUT_DEFAULT)
    conn.execute("UPDATE AttributeField SET default_option_id=? WHERE field_id=?",
                 (default_oid, layout_fid))
    return spec_fid, tags_fid, layout_fid


# ================= 匯入主流程 =================

def run_import(conn, records):
    """回傳 (stats dict, warnings list)。可重跑:條碼判重,已存在整列跳過。"""
    warnings = []
    desc_fid = _shared_field_id(conn, SHARED_DESC_FIELD)

    cat_ids = {}          # 種類名 → category_id
    brand_ids = {}        # 純廠牌名 → brand_id
    pbrand_ids = {}       # 手機品牌名 → phone_brand_id
    select_fids = {}      # (category_id, 欄名) → field_id(非鋼化玻璃種類用)
    glass_fids = {}       # category_id → (規格,特性詞條,版型) field_id
    model_ids = {}        # (phone_brand_id, 型號名) → model_id
    products = {}         # (種類名, 廠牌名) → product_id

    added_variants = 0
    added_barcodes = 0
    skipped = 0

    for rec in records:
        category = rec["category"] or "未分類"
        if category not in cat_ids:
            cat_ids[category] = _get_or_create_category(conn, category)
        cid = cat_ids[category]

        # 廠牌(正規化)+ 廠牌×種類
        bid = None
        if rec["brand"]:
            if not rec["brand_resolvable"]:
                warnings.append(
                    f"廠牌無法解析為純廠牌名,保留原字串:「{rec['brand_raw']}」")
            if rec["brand"] not in brand_ids:
                brand_ids[rec["brand"]] = _get_or_create_brand(conn, rec["brand"])
            bid = brand_ids[rec["brand"]]
            _link_brand_category(conn, bid, cid)

        # 手機品牌
        if rec["phone_brand"] and rec["phone_brand"] not in pbrand_ids:
            pbrand_ids[rec["phone_brand"]] = _get_or_create_phone_brand(
                conn, rec["phone_brand"])

        # 型號拆解警告(即使該列跳過也回報,便於對帳)
        for w in rec["model_warnings"]:
            warnings.append(
                f"型號拆解失敗,保留原片段:品牌「{rec['phone_brand']}」值「{w}」")

        # 規格欄 → 待寫入的 (field_id, option_id) 清單。
        # 鋼化玻璃走 spec §3 對照表(規格 multi + 特性詞條 tags + 版型 select);
        # 其他種類維持既有 select 邏輯(本輪不匯,但保留)。
        va_inserts = []
        if category == GLASS_CATEGORY:
            if cid not in glass_fids:
                glass_fids[cid] = _ensure_glass_fields(conn, cid)
            spec_fid, tags_fid, layout_fid = glass_fids[cid]
            ga = glass_attrs(rec["select_attrs"].get(COL_SPEC),
                             rec["select_attrs"].get(COL_CAT1))
            for w in ga[GLASS_SPEC_FIELD]:
                va_inserts.append((spec_fid, _get_or_create_option(conn, spec_fid, w)))
            for t in ga[GLASS_TAGS_FIELD]:
                va_inserts.append((tags_fid, _get_or_create_option(conn, tags_fid, t)))
            if ga[GLASS_LAYOUT_FIELD]:
                va_inserts.append((layout_fid, _get_or_create_option(
                    conn, layout_fid, ga[GLASS_LAYOUT_FIELD])))
        else:
            for col, val in rec["select_attrs"].items():
                fkey = (cid, col)
                if fkey not in select_fids:
                    select_fids[fkey] = _get_or_create_field(conn, col, cid, "select")
                va_inserts.append((select_fids[fkey],
                                   _get_or_create_option(conn, select_fids[fkey], val)))

        # 商品描述(共用 text 欄)+ 啟用 CategoryField
        if rec["desc"] and desc_fid is not None:
            _link_category_field(conn, cid, desc_fid)

        # 條碼判重(可重跑)——已存在整列跳過
        if conn.execute("SELECT 1 FROM Barcode WHERE barcode=?",
                        (rec["barcode"],)).fetchone():
            skipped += 1
            continue

        # 款(Product):同 種類+廠牌 歸一款
        pkey = product_key(rec)
        if pkey not in products:
            pname = product_name(rec)
            existing = conn.execute(
                "SELECT product_id FROM Product WHERE name=? AND category_id IS ? "
                "AND brand_id IS ?", (pname, cid, bid)).fetchone()
            products[pkey] = existing["product_id"] if existing else conn.execute(
                "INSERT INTO Product(name,category_id,brand_id,note) VALUES(?,?,?,?)",
                (pname, cid, bid, rec["note"])).lastrowid
        pid = products[pkey]

        # 變體(價格 NULL)
        vid = conn.execute(
            "INSERT INTO Variant(product_id) VALUES(?)", (pid,)).lastrowid
        added_variants += 1

        # 條碼:TL 開頭=自取條碼(store),其餘=原廠碼(factory)
        src = "store" if rec["barcode"].startswith("TL") else "factory"
        conn.execute(
            "INSERT INTO Barcode(barcode,variant_id,source) VALUES(?,?,?)",
            (rec["barcode"], vid, src))
        added_barcodes += 1

        # 規格欄 VariantAttribute:存 option_id(multi/tags 多筆、select 單筆)
        for fid, oid in va_inserts:
            conn.execute(
                "INSERT OR IGNORE INTO VariantAttribute(variant_id,field_id,option_id) "
                "VALUES(?,?,?)", (vid, fid, oid))
        # 商品描述:text 存 text_value
        if rec["desc"] and desc_fid is not None:
            conn.execute(
                "INSERT INTO VariantAttribute(variant_id,field_id,text_value) "
                "VALUES(?,?,?)", (vid, desc_fid, rec["desc"]))

        # 型號掛變體
        for nm in rec["models"]:
            pbid = pbrand_ids.get(rec["phone_brand"])
            if pbid is None:
                continue
            mkey = (pbid, nm)
            if mkey not in model_ids:
                model_ids[mkey] = _get_or_create_model(conn, pbid, nm)
            conn.execute(
                "INSERT OR IGNORE INTO VariantModel(variant_id,model_id) VALUES(?,?)",
                (vid, model_ids[mkey]))

    # 自取碼計數器:匯入的 TL 碼寫進 Barcode 後,把 Setting.next_store_barcode
    # 更新為「匯入最大號+1」(只往前推不倒退,重跑安全);App 取號直接讀此值+1
    max_tl = conn.execute(
        "SELECT MAX(CAST(SUBSTR(barcode,3) AS INTEGER)) FROM Barcode "
        "WHERE barcode LIKE 'TL%' AND SUBSTR(barcode,3) GLOB '[0-9]*'").fetchone()[0]
    if max_tl is not None:
        row = conn.execute(
            "SELECT value FROM Setting WHERE key='next_store_barcode'").fetchone()
        cur = int(row[0]) if row else 0
        if max_tl + 1 > cur:
            conn.execute(
                "INSERT OR REPLACE INTO Setting(key,value) "
                "VALUES('next_store_barcode',?)", (str(max_tl + 1),))

    stats = {
        "categories": conn.execute("SELECT COUNT(*) FROM Category").fetchone()[0],
        "brands": conn.execute("SELECT COUNT(*) FROM Brand").fetchone()[0],
        "phone_brands": conn.execute(
            "SELECT COUNT(*) FROM PhoneBrand").fetchone()[0],
        "models": conn.execute("SELECT COUNT(*) FROM PhoneModel").fetchone()[0],
        "products": conn.execute("SELECT COUNT(*) FROM Product").fetchone()[0],
        "variants_total": conn.execute(
            "SELECT COUNT(*) FROM Variant").fetchone()[0],
        "barcodes_total": conn.execute(
            "SELECT COUNT(*) FROM Barcode").fetchone()[0],
        "options": conn.execute(
            "SELECT COUNT(*) FROM AttributeOption").fetchone()[0],
        "added_variants": added_variants,
        "added_barcodes": added_barcodes,
        "skipped": skipped,
    }
    return stats, warnings


def _print_report(stats, warnings):
    from collections import Counter
    print("=== 匯入對帳 ===")
    print(f"種類 {stats['categories']}、廠牌 {stats['brands']}、"
          f"手機品牌 {stats['phone_brands']}、型號 {stats['models']}")
    print(f"款(Product) {stats['products']}、變體 {stats['variants_total']}、"
          f"條碼 {stats['barcodes_total']}、選項 {stats['options']}")
    print(f"本次新增:變體 {stats['added_variants']}、條碼 {stats['added_barcodes']};"
          f"跳過(條碼已存在){stats['skipped']}")
    counts = Counter(warnings)
    print(f"=== 警告 {len(warnings)} 筆({len(counts)} 種)===")
    for w, c in counts.items():
        print(f"  - [{c}x] {w}" if c > 1 else "  - " + w)


def main():
    ap = argparse.ArgumentParser(
        description="匯入 產品清單.xlsm 商品資料庫 → pos.db(可重跑)")
    ap.add_argument("--category", default=None, help="只匯此種類(不帶=全匯)")
    ap.add_argument("--db", default=os.path.join("data", "pos.db"),
                    help="目標 DB 路徑(預設 data/pos.db)")
    ap.add_argument("--excel", default=os.path.join("docs", "New產品清單.xlsm"),
                    help="來源 Excel 路徑(預設 docs/New產品清單.xlsm)")
    args = ap.parse_args()

    init_db(args.db)
    conn = get_conn(args.db)
    try:
        records = load_rows(args.excel, category=args.category)
        scope = f"種類「{args.category}」" if args.category else "全部種類"
        print(f"讀入 {scope}:{len(records)} 筆")
        stats, warnings = run_import(conn, records)
        conn.commit()
        _print_report(stats, warnings)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
