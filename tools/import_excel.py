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

# 各商品種類名(規格拆解規則依種類分派)
CASE_CATEGORY = "手機殼"
LENS_CATEGORY = "鏡頭貼"
SOCKET_CATEGORY = "插座"
EARPHONE_CATEGORY = "藍芽耳機"
POWERBANK_CATEGORY = "行動電源"
CABLE_CATEGORY = "充電線"
WATCH_CATEGORY = "AppleWatch玻璃"


# ================= 純函式(可單測、不碰 DB)=================

def clean(value):
    """儲存格值 → 去空白字串;空字串/None/'nan' 一律回 None。"""
    if value is None:
        return None
    # 商品編碼/條碼在 Excel 若存成數值,openpyxl 回 float(如 4711...0),
    # 直接 str() 會帶「.0」汙染條碼;整數值的 float 先轉 int
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    s = str(value).strip()
    if s == "" or s.lower() == "nan":
        return None
    return s


# 補齊未閉合括號(來源缺字修正):左括號多於右括號時,依序補上對應右括號。
# 全/半形皆處理,支援巢狀;多餘右括號不動(屬另類錯誤,不在此範圍)。
_PAREN_CLOSE = {"(": ")", "（": "）"}
_PAREN_OPEN = set(_PAREN_CLOSE)
_PAREN_CLOSERS = set(_PAREN_CLOSE.values())


def close_unbalanced_parens(s):
    """字串尾端補齊未閉合的左括號(如「磁吸(附掛環扣」→「磁吸(附掛環扣)」)。"""
    stack = []
    for ch in s:
        if ch in _PAREN_OPEN:
            stack.append(ch)
        elif ch in _PAREN_CLOSERS and stack:
            stack.pop()
    return s + "".join(_PAREN_CLOSE[c] for c in reversed(stack))


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
    # AppleWatch玻璃/充電線 尾綴無法以通則還原純廠牌,顯式對照:
    "ETON_Watch玻璃": "ETON",
    "ACEICE_Ai_Watch玻璃": "ACEICE",
    "犀牛盾充電線": "RS犀牛盾",     # 通則會得「犀牛盾」,須併入既有 RS犀牛盾
    # 註:NAVJack手機殼 由通則去尾綴即得 NAVJack,不必列。
    # 註:COZY五倍強化/COZY微晶盾/硬派6倍強化 改走 GLASS_BRAND_TAGS(廠牌+詞條)。
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
    if category == GLASS_CATEGORY and s in GLASS_BRAND_TAGS:
        return GLASS_BRAND_TAGS[s][0]        # 廠牌尾綴→詞條:回傳純廠牌
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
    t = re.sub(r"[(（].*?[)）]", "", token)      # 去尺寸括號(半形/全形)(6.1)（6.1）
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
      select_attrs({欄名:值}), desc, note,
      earphone_model(藍芽耳機型號 text or None), earphone_suspicious(bool)
    """
    barcode = clean(raw.get(COL_CODE))
    if barcode is None:
        return None
    category = clean(raw.get(COL_CATEGORY))
    brand_raw = clean(raw.get(COL_BRAND))
    # 藍芽耳機廠牌欄為「品牌+型號」髒值:拆廠牌/型號,不走通用正規化。
    earphone_model = None
    earphone_suspicious = False
    if category == EARPHONE_CATEGORY:
        brand, earphone_model, earphone_suspicious = split_earphone_brand(brand_raw)
    else:
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
            select_attrs[col] = close_unbalanced_parens(v)
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
        "earphone_model": earphone_model,
        "earphone_suspicious": earphone_suspicious,
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
GLASS_SPEC_FIELD = "材質"          # multi:亮面/霧面/藍光/防窺
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

# 廠牌尾綴→特性詞條(僅鋼化玻璃生效):原始廠牌字串 → (純廠牌, [詞條...])。
# 詞條寫進鋼化玻璃的「特性詞條」tags 欄。取代舊 BRAND_ALIASES 的 COZY 兩筆。
GLASS_BRAND_TAGS = {
    "硬派6倍強化": ("硬派", ["6倍強化"]),
    "COZY五倍強化": ("COZY", ["五倍強化"]),
    "COZY微晶盾": ("COZY", ["微晶盾"]),
}


def glass_brand_tags(brand_raw):
    """原始廠牌字串 → 該廠牌尾綴帶出的鋼化玻璃特性詞條 list(無則空)。"""
    if brand_raw is None:
        return []
    return list(GLASS_BRAND_TAGS.get(str(brand_raw).strip(), (None, []))[1])


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


# ================= 其餘七類規格模型 =================
#
# 每種類的專屬欄以 CATEGORY_FIELDS 宣告(欄名, 欄型),依序建立→決定欄顯示順位。
# 各類的規格/分類1/廠牌拆解寫成純函式(可單測),run_import 依種類分派後
# 用 category_attr_writes(rec) 取得該列要寫的選項/文字欄與警告。

# 欄名常數:宣告表(CATEGORY_SELECT_RENAME/CATEGORY_FIELDS)與寫入邏輯
# (category_attr_writes 各 writer)共用同一來源,避免欄名字串各處各拼而 drift。
F_STYLE = "款式"; F_COLOR = "顏色"; F_MATERIAL = "材質"; F_FRAME = "框色"
F_SPEC = "規格"; F_MODEL = "型號"; F_CONNECTOR = "接頭"; F_LENGTH = "長度"
F_TAGS = "特性詞條"; F_SIZE = "尺寸"

# 手機殼/鏡頭貼/插座:規格、分類1 僅換欄名的純 select。
CATEGORY_SELECT_RENAME = {
    CASE_CATEGORY:   [(COL_SPEC, F_STYLE), (COL_CAT1, F_COLOR)],
    LENS_CATEGORY:   [(COL_SPEC, F_MATERIAL), (COL_CAT1, F_FRAME)],
    SOCKET_CATEGORY: [(COL_SPEC, F_SPEC), (COL_CAT1, F_COLOR)],
}

# 種類 → [(欄名, 欄型)];依序建立(欄 sort 遞增),第一次遇到該種類時全建。
CATEGORY_FIELDS = {
    CASE_CATEGORY:     [(F_STYLE, "select"), (F_COLOR, "select")],
    LENS_CATEGORY:     [(F_MATERIAL, "select"), (F_FRAME, "select")],
    SOCKET_CATEGORY:   [(F_SPEC, "select"), (F_COLOR, "select")],
    EARPHONE_CATEGORY: [(F_MODEL, "text"), (F_COLOR, "select")],
    POWERBANK_CATEGORY: [(F_SPEC, "select"), (F_COLOR, "select")],
    CABLE_CATEGORY:    [(F_CONNECTOR, "select"), (F_LENGTH, "select"),
                        (F_COLOR, "select"), (F_TAGS, "tags")],
    WATCH_CATEGORY:    [(F_STYLE, "select"), (F_SIZE, "select")],
}


# ---- 藍芽耳機:廠牌欄「品牌+型號」髒值拆解 ----

def split_earphone_brand(brand_raw):
    """藍芽耳機廠牌欄 → (廠牌, 型號 or None, 廠牌可疑 bool)。

    取第一個空白前的 token 當廠牌(去尾端「-」),其餘字串進型號。連續空白吞掉。
    廠牌含數字視為可疑(如「T6」),另列警告供對帳,不改拆法。
    """
    if brand_raw is None:
        return None, None, False
    s = str(brand_raw).strip()
    if not s:
        return None, None, False
    parts = s.split(None, 1)                 # 依任意空白切一刀,吞連續空白
    brand = parts[0].rstrip("-")             # 「DA-」→「DA」
    model = parts[1].strip() if len(parts) > 1 else None
    if not model:
        model = None
    suspicious = bool(re.search(r"\d", brand))
    return brand, model, suspicious


# ---- 行動電源:規格含「-」時拆出顏色 ----

def split_powerbank_spec(spec_value):
    """行動電源規格 → (規格值, 顏色 or None)。

    含「-」時以最後一個「-」為界:前=規格、後=顏色(「4代 CC-柔霧白」→
    「4代 CC」+「柔霧白」);無「-」則整串為規格、顏色 None。
    """
    if spec_value is None:
        return None, None
    s = str(spec_value).strip()
    if not s:
        return None, None
    if "-" in s:
        i = s.rfind("-")
        return s[:i].strip() or None, s[i + 1:].strip() or None
    return s, None


# ---- 充電線:接頭/長度/特性詞條拆解 ----

CABLE_PREFIXES = ["5A", "騎士"]              # 規格前綴 → 特性詞條
# 接頭正規化:iPhone→Lightning、USB-Type-C→Type-C;其餘可接受值原樣。
CABLE_CONNECTOR_MAP = {"iPhone": "Lightning", "USB-Type-C": "Type-C",
                       "Lightning": "Lightning"}
CABLE_KNOWN_CONNECTORS = {"Lightning", "Type-C", "PD", "雙C"}


def is_cable_length(value):
    """字串是否為長度(/^\\d+公分$/)。"""
    if value is None:
        return False
    return bool(re.match(r"^\d+公分$", str(value).strip()))


def normalize_connector(value):
    """接頭值正規化 → (接頭, 可辨識 bool)。無法辨識回 (原值, False)。"""
    t = str(value).strip()
    if t in CABLE_CONNECTOR_MAP:
        return CABLE_CONNECTOR_MAP[t], True
    if t in CABLE_KNOWN_CONNECTORS:
        return t, True
    return t, False


def parse_cable(spec_value, desc_value):
    """充電線 規格 + 商品描述 → {接頭, 長度, 特性詞條:[...], warnings:[...]}。

    兩套寫法:規格=長度且描述=接頭;或規格=接頭(可帶前綴 5A/騎士)且描述=長度。
    判斷:符合 /^\\d+公分$/ 者=長度,其餘=接頭;前綴 5A/騎士 進特性詞條。
    ⚠️ 商品描述已在此消化為接頭/長度,呼叫端不得再寫進共用「商品描述」欄。
    """
    length = None
    connector_raw = None
    warnings = []
    for val in (spec_value, desc_value):
        v = clean(val)
        if v is None:
            continue
        if is_cable_length(v):
            length = v
        else:
            connector_raw = v
    tags = []
    connector = None
    if connector_raw is not None:
        parts = connector_raw.split(None, 1)
        if len(parts) == 2 and parts[0] in CABLE_PREFIXES:
            tags.append(parts[0])
            core = parts[1].strip()
        else:
            core = connector_raw
        connector, ok = normalize_connector(core)
        if not ok:
            # 怪值不污染「接頭」選項池,改當一個特性詞條保留(可搜尋、不流失)
            connector = None
            if core:
                tags.append(core)
            warnings.append(f"充電線接頭無法辨識,改入特性詞條:「{core}」")
    else:
        warnings.append(
            f"充電線無法辨識接頭(規格「{clean(spec_value)}」描述「{clean(desc_value)}」)")
    return {"接頭": connector, "長度": length, "特性詞條": tags,
            "warnings": warnings}


# ---- AppleWatch玻璃:款式 + 尺寸 拆解 ----

def split_watch_glass(spec_value):
    """AppleWatch玻璃規格 → (款式, 尺寸 or None, 需警告 bool)。

    以最後一個空白拆,尾段符合 /^\\d+mm$/ 才拆出尺寸;否則整串為款式並列警告。
    """
    if spec_value is None:
        return None, None, False
    s = str(spec_value).strip()
    if not s:
        return None, None, False
    if " " in s:
        head, tail = s.rsplit(" ", 1)
        if re.match(r"^\d+mm$", tail):
            return head.strip() or None, tail, False
    return s, None, True


# 各類 writer:(rec, spec, cat1) → (option_writes, text_writes, warnings);
# option_writes/text_writes 皆為 [(欄名, 值)]。經 CATEGORY_WRITERS 分派。

def _writes_rename(rec, spec, cat1):
    """手機殼/鏡頭貼/插座:規格、分類1 換欄名的純 select。"""
    cat = rec["category"]
    opts = []
    for col, fname in CATEGORY_SELECT_RENAME[cat]:
        v = rec["select_attrs"].get(col)
        if v is not None:
            opts.append((fname, v))
    # 手機殼款式/顏色兩欄皆空(空壓殼等透明殼)→ 款式填「透明」,避免無規格
    if cat == CASE_CATEGORY and not opts:
        opts.append((F_STYLE, "透明"))
    return opts, [], []


def _writes_earphone(rec, spec, cat1):
    opts, texts, warns = [], [], []
    if rec.get("earphone_model"):
        texts.append((F_MODEL, rec["earphone_model"]))
    if cat1 is not None:
        opts.append((F_COLOR, cat1))
    if rec.get("earphone_suspicious"):
        warns.append(
            f"藍芽耳機廠牌可疑(對帳用):原值「{rec['brand_raw']}」"
            f"→ 廠牌「{rec['brand']}」型號「{rec.get('earphone_model')}」")
    return opts, texts, warns


def _writes_powerbank(rec, spec, cat1):
    opts = []
    pspec, pcolor = split_powerbank_spec(spec)
    if pspec is not None:
        opts.append((F_SPEC, pspec))
    color = cat1 if cat1 is not None else pcolor   # 分類1 優先(實務不併存)
    if color is not None:
        opts.append((F_COLOR, color))
    return opts, [], []


def _writes_cable(rec, spec, cat1):
    opts = []
    info = parse_cable(spec, rec["desc"])
    if info[F_CONNECTOR] is not None:
        opts.append((F_CONNECTOR, info[F_CONNECTOR]))
    if info[F_LENGTH] is not None:
        opts.append((F_LENGTH, info[F_LENGTH]))
    if cat1 is not None:
        opts.append((F_COLOR, cat1))
    for t in info[F_TAGS]:
        opts.append((F_TAGS, t))
    return opts, [], list(info["warnings"])


def _writes_watch(rec, spec, cat1):
    opts, warns = [], []
    style, size, need_warn = split_watch_glass(spec)
    if style is not None:
        opts.append((F_STYLE, style))
    if size is not None:
        opts.append((F_SIZE, size))
    if need_warn:
        warns.append(f"AppleWatch玻璃規格無法拆出尺寸,整串入款式:「{spec}」")
    return opts, [], warns


CATEGORY_WRITERS = {
    EARPHONE_CATEGORY: _writes_earphone,
    POWERBANK_CATEGORY: _writes_powerbank,
    CABLE_CATEGORY: _writes_cable,
    WATCH_CATEGORY: _writes_watch,
}


def category_attr_writes(rec):
    """依種類拆 rec → (option_writes, text_writes, warnings)。

    先查 rename 表(純換欄名的三類),再查 CATEGORY_WRITERS(各自邏輯);
    欄型由 CATEGORY_FIELDS 決定。鋼化玻璃另走 glass_attrs,不在此。
    """
    cat = rec["category"]
    spec = rec["select_attrs"].get(COL_SPEC)
    cat1 = rec["select_attrs"].get(COL_CAT1)
    if cat in CATEGORY_SELECT_RENAME:
        return _writes_rename(rec, spec, cat1)
    writer = CATEGORY_WRITERS.get(cat)
    if writer:
        return writer(rec, spec, cat1)
    return [], [], []


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

def _get_or_create(conn, table, id_col, keys, extra_cols=None):
    """單鍵/多鍵 find-or-create 通則:以 keys(欄→值)查,無則插入。

    extra_cols:僅插入時附加的欄(不參與查詢),供有預設值等需求。
    回傳既有或新建列的 id_col。"""
    where = " AND ".join(f"{k}=?" for k in keys)
    row = conn.execute(f"SELECT {id_col} FROM {table} WHERE {where}",
                       tuple(keys.values())).fetchone()
    if row:
        return row[id_col]
    ins = dict(keys)
    if extra_cols:
        ins.update(extra_cols)
    cols = ",".join(ins)
    placeholders = ",".join("?" for _ in ins)
    return conn.execute(f"INSERT INTO {table}({cols}) VALUES({placeholders})",
                        tuple(ins.values())).lastrowid


def _get_or_create_category(conn, name):
    return _get_or_create(conn, "Category", "category_id", {"name": name})


def _get_or_create_brand(conn, name):
    return _get_or_create(conn, "Brand", "brand_id", {"name": name})


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


def _ensure_category_fields(conn, cid, category, field_fids):
    """第一次遇到該種類時,依 CATEGORY_FIELDS 順序建立專屬欄(決定欄顯示順位),
    快取 (cid, 欄名) → field_id。"""
    for name, ftype in CATEGORY_FIELDS.get(category, []):
        key = (cid, name)
        if key not in field_fids:
            field_fids[key] = _get_or_create_field(conn, name, cid, ftype)


def _resolve_product(conn, products, rec, cid, bid):
    """款(Product):同 種類+廠牌 歸一款;快取於 products。"""
    pkey = product_key(rec)
    if pkey not in products:
        pname = product_name(rec)
        existing = conn.execute(
            "SELECT product_id FROM Product WHERE name=? AND category_id IS ? "
            "AND brand_id IS ?", (pname, cid, bid)).fetchone()
        products[pkey] = existing["product_id"] if existing else conn.execute(
            "INSERT INTO Product(name,category_id,brand_id,note) VALUES(?,?,?,?)",
            (pname, cid, bid, rec["note"])).lastrowid
    return products[pkey]


def _create_variant(conn, pid, barcode, source, va_options, va_texts, model_id_list):
    """建變體 + 條碼 + 屬性(option/text)+ 適用型號,回傳 variant_id。"""
    vid = conn.execute("INSERT INTO Variant(product_id) VALUES(?)", (pid,)).lastrowid
    conn.execute("INSERT INTO Barcode(barcode,variant_id,source) VALUES(?,?,?)",
                 (barcode, vid, source))
    for fid, oid in va_options:
        conn.execute(
            "INSERT OR IGNORE INTO VariantAttribute(variant_id,field_id,option_id) "
            "VALUES(?,?,?)", (vid, fid, oid))
    for fid, tv in va_texts:
        conn.execute(
            "INSERT INTO VariantAttribute(variant_id,field_id,text_value) "
            "VALUES(?,?,?)", (vid, fid, tv))
    for mid in model_id_list:
        conn.execute(
            "INSERT OR IGNORE INTO VariantModel(variant_id,model_id) VALUES(?,?)",
            (vid, mid))
    return vid


# 判重簽章的屬性元組一律為 (field_id, option_id, text_value):
#   option 列 → text_value=None;text 列 → option_id=None。
# _target_signature 與 _variant_signature 必須產出「完全同形」的 frozenset,
# 判重(_find_matching_variant 的 ==)才成立;改任一端務必同步另一端。
def _target_signature(va_options, va_texts, model_id_list):
    """由待寫入屬性/型號組出判重簽章(與 _variant_signature 對齊,見上方契約)。"""
    attrs = frozenset([(fid, oid, None) for fid, oid in va_options]
                      + [(fid, None, tv) for fid, tv in va_texts])
    return attrs, frozenset(model_id_list)


def _variant_signature(conn, vid):
    """既有變體的屬性/型號簽章:{(field_id,option_id,text_value)} × {model_id}。"""
    attrs = frozenset(
        (r["field_id"], r["option_id"], r["text_value"])
        for r in conn.execute(
            "SELECT field_id, option_id, text_value FROM VariantAttribute "
            "WHERE variant_id=?", (vid,)))
    models = frozenset(
        r["model_id"] for r in conn.execute(
            "SELECT model_id FROM VariantModel WHERE variant_id=?", (vid,)))
    return attrs, models


def _find_matching_variant(conn, pid, target_sig):
    """該款下是否已有「完全相同屬性+型號組合」的變體;有回 variant_id,否則 None。"""
    for r in conn.execute("SELECT variant_id FROM Variant WHERE product_id=?", (pid,)):
        if _variant_signature(conn, r["variant_id"]) == target_sig:
            return r["variant_id"]
    return None


def _read_setting_int(conn, key, default):
    """讀 Setting[key] 轉 int;查無回 default。"""
    row = conn.execute("SELECT value FROM Setting WHERE key=?", (key,)).fetchone()
    return int(row["value"]) if row else default


def _write_setting(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO Setting(key,value) VALUES(?,?)",
                 (key, str(value)))


def _bump_setting_min(conn, key, target):
    """只前進不倒退:target 大於現值時才把 Setting[key] 推到 target(重跑安全)。"""
    if target > _read_setting_int(conn, key, 0):
        _write_setting(conn, key, target)


def _next_store_barcode(conn):
    """自取碼取號:讀 Setting.next_store_barcode → 用 TL{n} → 寫回 n+1
    (與 api.products.next_store_barcode 邏輯一致)。"""
    n = _read_setting_int(conn, "next_store_barcode", 100000001)
    _write_setting(conn, "next_store_barcode", n + 1)
    return f"TL{n}"


def _seed_store_counter(conn, records):
    """匯入前先把 next_store_barcode 推到「既有 TL 最大號+1」,確保檔內重覆條碼
    改發的 TL 碼不與 Excel 既有 TL 碼相撞。(全匯時 records 已含所有 Excel TL 碼;
    分種類匯入時輔以 DB 既有 TL 碼。)"""
    nums = []
    for rec in records:
        b = rec["barcode"]
        if b and b.startswith("TL") and b[2:].isdigit():
            nums.append(int(b[2:]))
    for row in conn.execute("SELECT barcode FROM Barcode WHERE barcode LIKE 'TL%'"):
        b = row["barcode"]
        if b[2:].isdigit():
            nums.append(int(b[2:]))
    if not nums:
        return
    _bump_setting_min(conn, "next_store_barcode", max(nums) + 1)


# ================= 匯入主流程 =================

def run_import(conn, records):
    """回傳 (stats dict, warnings list)。可重跑:條碼判重,已存在整列跳過。"""
    warnings = []
    desc_fid = _shared_field_id(conn, SHARED_DESC_FIELD)
    _seed_store_counter(conn, records)        # 改發 TL 前先讓計數器越過既有 TL 碼

    cat_ids = {}          # 種類名 → category_id
    brand_ids = {}        # 純廠牌名 → brand_id
    pbrand_ids = {}       # 手機品牌名 → phone_brand_id
    select_fids = {}      # (category_id, 欄名) → field_id(通用兜底 select 用)
    field_fids = {}       # (category_id, 欄名) → field_id(CATEGORY_FIELDS 專屬欄)
    glass_fids = {}       # category_id → (規格,特性詞條,版型) field_id
    model_ids = {}        # (phone_brand_id, 型號名) → model_id
    products = {}         # (種類名, 廠牌名) → product_id
    barcode_occurrence = {}   # 條碼 → 本次匯入已出現次數(判「檔內重覆」)

    added_variants = 0
    added_barcodes = 0
    skipped = 0
    reassigned = 0

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

        # 規格欄 → 待寫入清單:va_options [(fid,oid)]、va_texts [(fid,text)]。
        # 鋼化玻璃走 spec §3 對照表 + 廠牌尾綴詞條;其餘七類走 category_attr_writes;
        # 未涵蓋的種類仍以通用 select 兜底。
        va_options = []
        va_texts = []
        if category == GLASS_CATEGORY:
            if cid not in glass_fids:
                glass_fids[cid] = _ensure_glass_fields(conn, cid)
            spec_fid, tags_fid, layout_fid = glass_fids[cid]
            ga = glass_attrs(rec["select_attrs"].get(COL_SPEC),
                             rec["select_attrs"].get(COL_CAT1))
            gtags = list(ga[GLASS_TAGS_FIELD])
            for t in glass_brand_tags(rec["brand_raw"]):   # 廠牌尾綴帶出的特性詞條
                if t not in gtags:
                    gtags.append(t)
            for w in ga[GLASS_SPEC_FIELD]:
                va_options.append((spec_fid, _get_or_create_option(conn, spec_fid, w)))
            for t in gtags:
                va_options.append((tags_fid, _get_or_create_option(conn, tags_fid, t)))
            if ga[GLASS_LAYOUT_FIELD]:
                va_options.append((layout_fid, _get_or_create_option(
                    conn, layout_fid, ga[GLASS_LAYOUT_FIELD])))
        elif category in CATEGORY_FIELDS:
            _ensure_category_fields(conn, cid, category, field_fids)
            opt_writes, text_writes, cat_warns = category_attr_writes(rec)
            warnings.extend(cat_warns)
            for name, val in opt_writes:
                fid = field_fids[(cid, name)]
                va_options.append((fid, _get_or_create_option(conn, fid, val)))
            for name, val in text_writes:
                va_texts.append((field_fids[(cid, name)], val))
        else:
            for col, val in rec["select_attrs"].items():
                fkey = (cid, col)
                if fkey not in select_fids:
                    select_fids[fkey] = _get_or_create_field(conn, col, cid, "select")
                va_options.append((select_fids[fkey],
                                   _get_or_create_option(conn, select_fids[fkey], val)))

        # 商品描述(共用 text 欄)+ 啟用 CategoryField。
        # ⚠️ 充電線的商品描述已消化為接頭/長度,不再入共用欄。
        if rec["desc"] and desc_fid is not None and category != CABLE_CATEGORY:
            _link_category_field(conn, cid, desc_fid)
            va_texts.append((desc_fid, rec["desc"]))

        # 適用型號 id 清單(供變體掛載與判重簽章)
        model_id_list = []
        pbid = pbrand_ids.get(rec["phone_brand"])
        if pbid is not None:
            for nm in rec["models"]:
                mkey = (pbid, nm)
                if mkey not in model_ids:
                    model_ids[mkey] = _get_or_create_model(conn, pbid, nm)
                model_id_list.append(model_ids[mkey])

        # 條碼處理:同一次匯入中首次出現走原條碼;第二次(含)出現=檔內重覆,改發 TL。
        bc = rec["barcode"]
        occ = barcode_occurrence.get(bc, 0) + 1
        barcode_occurrence[bc] = occ
        if occ == 1:
            # 首次出現:條碼判重(可重跑)——DB 已存在整列跳過
            if conn.execute("SELECT 1 FROM Barcode WHERE barcode=?", (bc,)).fetchone():
                skipped += 1
                continue
            pid = _resolve_product(conn, products, rec, cid, bid)
            src = "store" if bc.startswith("TL") else "factory"
            _create_variant(conn, pid, bc, src, va_options, va_texts, model_id_list)
            added_variants += 1
            added_barcodes += 1
        else:
            # 檔內重覆條碼:改發系統自取碼(TL)。重跑安全——同 Product +
            # 完全相同屬性/型號組合已有變體則跳過,不重覆建變體。
            pid = _resolve_product(conn, products, rec, cid, bid)
            target_sig = _target_signature(va_options, va_texts, model_id_list)
            if _find_matching_variant(conn, pid, target_sig) is not None:
                skipped += 1
                continue
            tl = _next_store_barcode(conn)
            _create_variant(conn, pid, tl, "store", va_options, va_texts, model_id_list)
            added_variants += 1
            added_barcodes += 1
            reassigned += 1
            warnings.append(
                f"檔內重覆條碼:原條碼「{bc}」已用於前一列,本列改發自取碼「{tl}」")

    # 自取碼計數器:匯入的 TL 碼寫進 Barcode 後,把 Setting.next_store_barcode
    # 更新為「匯入最大號+1」(只往前推不倒退,重跑安全);App 取號直接讀此值+1
    max_tl = conn.execute(
        "SELECT MAX(CAST(SUBSTR(barcode,3) AS INTEGER)) FROM Barcode "
        "WHERE barcode LIKE 'TL%' AND SUBSTR(barcode,3) GLOB '[0-9]*'").fetchone()[0]
    if max_tl is not None:
        _bump_setting_min(conn, "next_store_barcode", max_tl + 1)

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
        "reassigned": reassigned,
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
          f"跳過(條碼已存在或重覆){stats['skipped']};"
          f"檔內重覆改發自取碼 {stats['reassigned']}")
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
