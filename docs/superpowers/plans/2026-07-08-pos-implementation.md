# 手機配件店 POS 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 單機 Web POS(收銀/進貨/盤點/銷售紀錄),打包成單一 exe。

**Architecture:** FastAPI 後端 + SQLite + Vue 無建置前端,啟動時起 localhost 並自動開瀏覽器。庫存採異動流水制;商品=款/變體/條碼三層;選單庫連動下拉。

**Tech Stack:** Python 3.11+、FastAPI、uvicorn、SQLite(stdlib sqlite3)、Vue 3(本地檔,無 npm)、PyInstaller。

## Global Constraints

- 依 spec:`docs/superpowers/specs/2026-07-08-pos-design.md`
- 遵守 `CLAUDE.md`(台灣用語、UI 文字正式、測試放 `tests/`、`python -m unittest discover -s tests`)
- **不引入 npm/node**;前端第三方只有本地 `static/js/vue.global.prod.js`
- 資料落地只進 `data/`(pos.db、backups/、config.json);`data/` 進 .gitignore
- 金額用 int(新台幣元,無小數);數量 int
- 所有 API 路徑前綴 `/api/`
- DB 存取統一走 `lib/db.py` 的 `get_conn(db_path)`(`sqlite3.Row` factory、`PRAGMA foreign_keys=ON`)

## 檔案結構(全貌)

```
main.py                     進入點:備份→起 uvicorn→開瀏覽器
lib/
  __init__.py
  version.py                VERSION 字串
  db.py                     get_conn / ensure_schema 呼叫
  db_schema.py              全部 DDL(唯一來源)
  db_seed.py                預設八屬性欄位、付款方式種子
  backup.py                 GFS 備份(日7/週4/月12)
api/
  __init__.py               create_app():FastAPI、掛 router、掛 static
  attributes.py             屬性欄位/選單庫(含連動)
  products.py               款/變體/條碼 CRUD、條碼查詢、店內條碼產生
  stock.py                  進貨、庫存查詢
  sales.py                  結帳、銷售紀錄、日結、CSV
  stocktake.py              盤點單
  printing.py               條碼列印服務介面(stub)
static/
  index.html                單頁骨架 + 各畫面 <template>
  css/pos.css
  js/vue.global.prod.js     (Task 8 下載放入)
  js/api.js                 fetch 包裝
  js/app.js                 Vue app、頁面切換
  js/checkout.js  js/receive.js  js/stocktake.js  js/records.js  js/settings.js
tools/
  import_excel.py           一次性:產品清單_org.xlsm → pos.db
tests/
  test_schema.py test_attributes.py test_products.py
  test_stock.py test_sales.py test_stocktake.py test_backup.py
```

---

### Task 1: 專案骨架 + DB schema

**Files:**
- Create: `lib/__init__.py`(空)、`lib/version.py`、`lib/db_schema.py`、`lib/db.py`、`lib/db_seed.py`、`.gitignore`、`tests/test_schema.py`

**Interfaces:**
- Produces: `db.get_conn(db_path) -> sqlite3.Connection`(Row factory、FK on)、`db.init_db(db_path)`(建 schema+種子,冪等)、`db_seed.DEFAULT_FIELDS`(八欄位名 list)

- [ ] **Step 1: 寫失敗測試** `tests/test_schema.py`

```python
import unittest, tempfile, os
from lib.db import get_conn, init_db

class TestSchema(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)

    def test_tables_exist(self):
        conn = get_conn(self.db)
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in ["Product","Variant","Barcode","AttributeField","AttributeOption",
                  "StockMovement","Sale","SaleItem","StocktakeSession","StocktakeItem","Setting"]:
            self.assertIn(t, names)

    def test_init_idempotent(self):
        init_db(self.db)  # 第二次不炸
        conn = get_conn(self.db)
        n = conn.execute("SELECT COUNT(*) c FROM AttributeField").fetchone()["c"]
        self.assertEqual(n, 8)  # 種子不重複

    def test_default_fields(self):
        conn = get_conn(self.db)
        rows = [r["name"] for r in conn.execute(
            "SELECT name FROM AttributeField ORDER BY sort")]
        self.assertEqual(rows, ["商品種類","廠牌","規格","商品描述",
                                "分類1","分類2","手機品牌","手機型號"])
```

- [ ] **Step 2: 跑測試確認失敗** — `python -m unittest tests.test_schema -v` → FAIL(ModuleNotFoundError)

- [ ] **Step 3: 實作**

`lib/version.py`
```python
VERSION = "0.1.0"
```

`lib/db_schema.py`
```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS Product(
  product_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  category TEXT,
  default_price INTEGER,          -- 可空:建檔可不填價
  note TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS Variant(
  variant_id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL REFERENCES Product(product_id),
  attributes TEXT NOT NULL DEFAULT '{}',   -- JSON:{欄位名:值}
  price INTEGER,                  -- 可空:覆蓋款預設價
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS Barcode(
  barcode TEXT PRIMARY KEY,
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  source TEXT NOT NULL CHECK(source IN ('factory','store')),
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS AttributeField(
  field_id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS AttributeOption(
  option_id INTEGER PRIMARY KEY AUTOINCREMENT,
  field_id INTEGER NOT NULL REFERENCES AttributeField(field_id),
  value TEXT NOT NULL,
  parent_field_id INTEGER REFERENCES AttributeField(field_id),  -- 連動:父欄位
  parent_value TEXT,                                            -- 連動:父欄位值
  sort INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  UNIQUE(field_id, value, parent_field_id, parent_value)
);
CREATE TABLE IF NOT EXISTS StockMovement(
  move_id INTEGER PRIMARY KEY AUTOINCREMENT,
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  qty INTEGER NOT NULL,           -- 進貨+/銷售-/盤點±
  kind TEXT NOT NULL CHECK(kind IN ('purchase','sale','adjust')),
  ref_id INTEGER,                 -- sale_id 或 session_id
  note TEXT,
  ts TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_move_variant ON StockMovement(variant_id);
CREATE TABLE IF NOT EXISTS Sale(
  sale_id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  payment TEXT NOT NULL,
  order_discount INTEGER NOT NULL DEFAULT 0,  -- 整單折抵(元)
  total INTEGER NOT NULL,         -- 應收
  paid INTEGER NOT NULL,          -- 實收
  change INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS SaleItem(
  item_id INTEGER PRIMARY KEY AUTOINCREMENT,
  sale_id INTEGER NOT NULL REFERENCES Sale(sale_id),
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  qty INTEGER NOT NULL,
  unit_price INTEGER NOT NULL,    -- 成交單價
  discount INTEGER NOT NULL DEFAULT 0  -- 單品折扣(元)
);
CREATE TABLE IF NOT EXISTS StocktakeSession(
  session_id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  ended_at TEXT,
  operator TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
  note TEXT
);
CREATE TABLE IF NOT EXISTS StocktakeItem(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL REFERENCES StocktakeSession(session_id),
  variant_id INTEGER NOT NULL REFERENCES Variant(variant_id),
  system_qty INTEGER NOT NULL,    -- 開盤當下快照
  counted_qty INTEGER NOT NULL DEFAULT 0,
  UNIQUE(session_id, variant_id)
);
CREATE TABLE IF NOT EXISTS Setting(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
"""
```

`lib/db_seed.py`
```python
import json

DEFAULT_FIELDS = ["商品種類","廠牌","規格","商品描述","分類1","分類2","手機品牌","手機型號"]
DEFAULT_PAYMENTS = ["現金","刷卡","行動支付"]

def seed(conn):
    for i, name in enumerate(DEFAULT_FIELDS):
        conn.execute(
            "INSERT OR IGNORE INTO AttributeField(name, sort) VALUES(?,?)", (name, i))
    conn.execute("INSERT OR IGNORE INTO Setting(key,value) VALUES('payments',?)",
                 (json.dumps(DEFAULT_PAYMENTS, ensure_ascii=False),))
```

`lib/db.py`
```python
import sqlite3
from lib.db_schema import SCHEMA
from lib import db_seed

def get_conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db(db_path):
    conn = get_conn(db_path)
    conn.executescript(SCHEMA)
    db_seed.seed(conn)
    conn.commit()
    conn.close()
```

- [ ] **Step 4: 跑測試通過** — `python -m unittest tests.test_schema -v` → OK(3 tests)

- [ ] **Step 5: 建 `.gitignore` 並 commit**

`.gitignore`
```
__pycache__/
data/
*.pyc
build/
dist/
*.spec
產品清單_org.xlsm
```

```bash
git add .gitignore lib/ tests/test_schema.py
git commit -m "feat: DB schema 與種子(款/變體/條碼/選單庫/異動流水/銷售/盤點)"
```

---

### Task 2: FastAPI app 骨架 + 屬性/選單庫 API

**Files:**
- Create: `api/__init__.py`、`api/attributes.py`、`tests/test_attributes.py`
- Create: `static/index.html`(最小占位,Task 8 再擴)

**Interfaces:**
- Consumes: `lib.db.get_conn / init_db`
- Produces: `api.create_app(db_path) -> FastAPI`;端點:
  - `GET /api/fields` → `[{field_id,name,sort,active}]`
  - `PUT /api/fields/{field_id}` body `{name?,sort?,active?}`(改標題用)
  - `POST /api/fields` body `{name}` → 新欄位
  - `GET /api/options?field_id=&parent_field_id=&parent_value=` → 連動過濾選項
  - `POST /api/options` body `{field_id,value,parent_field_id?,parent_value?}`(手打新值加入選單)

- [ ] **Step 1: 安裝依賴並記錄** — `pip install fastapi uvicorn httpx` 後建 `requirements.txt`:

```
fastapi
uvicorn
httpx
openpyxl
```

- [ ] **Step 2: 寫失敗測試** `tests/test_attributes.py`

```python
import unittest, tempfile, os
from fastapi.testclient import TestClient
from lib.db import init_db
from api import create_app

class TestAttributes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)
        self.c = TestClient(create_app(self.db))

    def test_list_fields(self):
        r = self.c.get("/api/fields")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([f["name"] for f in r.json()][:2], ["商品種類","廠牌"])

    def test_rename_field(self):
        fid = self.c.get("/api/fields").json()[4]["field_id"]  # 分類1
        r = self.c.put(f"/api/fields/{fid}", json={"name": "顏色"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("顏色", [f["name"] for f in self.c.get("/api/fields").json()])

    def test_cascading_options(self):
        fields = {f["name"]: f["field_id"] for f in self.c.get("/api/fields").json()}
        kind, brand = fields["商品種類"], fields["廠牌"]
        self.c.post("/api/options", json={"field_id": kind, "value": "鋼化玻璃"})
        self.c.post("/api/options", json={"field_id": brand, "value": "HODA",
                    "parent_field_id": kind, "parent_value": "鋼化玻璃"})
        self.c.post("/api/options", json={"field_id": brand, "value": "犀牛盾",
                    "parent_field_id": kind, "parent_value": "手機殼"})
        r = self.c.get(f"/api/options?field_id={brand}"
                       f"&parent_field_id={kind}&parent_value=鋼化玻璃")
        self.assertEqual([o["value"] for o in r.json()], ["HODA"])

    def test_duplicate_option_ignored(self):
        fid = self.c.get("/api/fields").json()[0]["field_id"]
        self.c.post("/api/options", json={"field_id": fid, "value": "插座"})
        r = self.c.post("/api/options", json={"field_id": fid, "value": "插座"})
        self.assertEqual(r.status_code, 200)  # 重複靜默成功,不炸
```

- [ ] **Step 3: 跑測試確認失敗** — `python -m unittest tests.test_attributes -v` → FAIL

- [ ] **Step 4: 實作**

`api/__init__.py`
```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import os

def create_app(db_path):
    app = FastAPI(title="POS")
    app.state.db_path = db_path
    from api import attributes
    app.include_router(attributes.router)
    static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
    if os.path.isdir(static_dir):
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app
```

`api/attributes.py`
```python
from fastapi import APIRouter, Request
from pydantic import BaseModel
from lib.db import get_conn

router = APIRouter(prefix="/api")

class FieldPatch(BaseModel):
    name: str | None = None
    sort: int | None = None
    active: int | None = None

class FieldNew(BaseModel):
    name: str

class OptionNew(BaseModel):
    field_id: int
    value: str
    parent_field_id: int | None = None
    parent_value: str | None = None

@router.get("/fields")
def list_fields(request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM AttributeField WHERE active=1 ORDER BY sort")]
    finally:
        conn.close()

@router.post("/fields")
def add_field(body: FieldNew, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        cur = conn.execute(
            "INSERT INTO AttributeField(name, sort) "
            "VALUES(?, (SELECT COALESCE(MAX(sort),0)+1 FROM AttributeField))",
            (body.name,))
        conn.commit()
        return {"field_id": cur.lastrowid}
    finally:
        conn.close()

@router.put("/fields/{field_id}")
def patch_field(field_id: int, body: FieldPatch, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        for col in ("name", "sort", "active"):
            v = getattr(body, col)
            if v is not None:
                conn.execute(f"UPDATE AttributeField SET {col}=? WHERE field_id=?",
                             (v, field_id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.get("/options")
def list_options(field_id: int, request: Request,
                 parent_field_id: int | None = None, parent_value: str | None = None):
    conn = get_conn(request.app.state.db_path)
    try:
        if parent_field_id is not None and parent_value is not None:
            # 連動:出「掛在該父值下」的+「無父設定(通用)」的
            rows = conn.execute(
                "SELECT * FROM AttributeOption WHERE field_id=? AND active=1 AND "
                "((parent_field_id=? AND parent_value=?) OR parent_field_id IS NULL) "
                "ORDER BY sort, option_id",
                (field_id, parent_field_id, parent_value))
        else:
            rows = conn.execute(
                "SELECT * FROM AttributeOption WHERE field_id=? AND active=1 "
                "ORDER BY sort, option_id", (field_id,))
        return [dict(r) for r in rows]
    finally:
        conn.close()

@router.post("/options")
def add_option(body: OptionNew, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        conn.execute(
            "INSERT OR IGNORE INTO AttributeOption"
            "(field_id, value, parent_field_id, parent_value) VALUES(?,?,?,?)",
            (body.field_id, body.value, body.parent_field_id, body.parent_value))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
```

`static/index.html`(占位)
```html
<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="utf-8"><title>POS</title></head>
<body><h1>POS 系統建置中</h1></body></html>
```

- [ ] **Step 5: 跑測試通過** — `python -m unittest tests.test_attributes -v` → OK(4 tests)

- [ ] **Step 6: Commit**

```bash
git add requirements.txt api/ static/index.html tests/test_attributes.py
git commit -m "feat: FastAPI 骨架與屬性欄位/連動選單庫 API"
```

---

### Task 3: 商品/變體/條碼 API

**Files:**
- Create: `api/products.py`、`tests/test_products.py`
- Modify: `api/__init__.py`(加 `app.include_router(products.router)`,置於 static mount 之前)

**Interfaces:**
- Produces:
  - `POST /api/products` body `{name,category?,default_price?,note?,variants:[{attributes:{},price?,barcodes:[{barcode,source}]}]}` → `{product_id,variant_ids}`
  - `GET /api/barcode/{code}` → `{variant_id,product_id,name,attributes,price,stock}` 或 404 `{"detail":"查無此條碼"}`
  - `POST /api/variants/{variant_id}/barcodes` body `{barcode?,source}`;barcode 省略時自動產店內條碼 → `{barcode}`
  - `GET /api/products?q=` 關鍵字搜尋(名稱/屬性值 LIKE)
  - 店內條碼規則:`SP` + 8 位流水(`SP00000001`),`Setting.next_store_barcode` 計數
  - 有效售價 = `Variant.price` 不為 NULL 用之,否則 `Product.default_price`,皆 NULL → 回 `price: null`

- [ ] **Step 1: 寫失敗測試** `tests/test_products.py`

```python
import unittest, tempfile, os
from fastapi.testclient import TestClient
from lib.db import init_db, get_conn
from api import create_app

def make_client(self):
    self.tmp = tempfile.mkdtemp()
    self.db = os.path.join(self.tmp, "pos.db")
    init_db(self.db)
    return TestClient(create_app(self.db))

class TestProducts(unittest.TestCase):
    def setUp(self):
        self.c = make_client(self)

    def _create(self):
        return self.c.post("/api/products", json={
            "name": "HODA 鋼化玻璃", "category": "鋼化玻璃", "default_price": 590,
            "variants": [
                {"attributes": {"規格": "亮面", "手機型號": "iPhone17pro"},
                 "barcodes": [{"barcode": "TL100000001", "source": "store"}]},
                {"attributes": {"規格": "霧面", "手機型號": "iPhone17pro"},
                 "price": 690, "barcodes": []},
            ]}).json()

    def test_create_and_scan(self):
        r = self._create()
        self.assertEqual(len(r["variant_ids"]), 2)
        hit = self.c.get("/api/barcode/TL100000001").json()
        self.assertEqual(hit["price"], 590)          # 用款預設價
        self.assertEqual(hit["attributes"]["規格"], "亮面")
        self.assertEqual(hit["stock"], 0)

    def test_variant_price_overrides(self):
        r = self._create()
        v2 = r["variant_ids"][1]
        b = self.c.post(f"/api/variants/{v2}/barcodes",
                        json={"source": "store"}).json()["barcode"]
        self.assertTrue(b.startswith("SP"))
        self.assertEqual(self.c.get(f"/api/barcode/{b}").json()["price"], 690)

    def test_unknown_barcode_404(self):
        self.assertEqual(self.c.get("/api/barcode/NOPE").status_code, 404)

    def test_null_price_allowed(self):
        r = self.c.post("/api/products", json={
            "name": "無價品", "variants": [{"attributes": {}, "barcodes":
                [{"barcode": "X1", "source": "factory"}]}]})
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(self.c.get("/api/barcode/X1").json()["price"])

    def test_store_barcode_sequence(self):
        r = self._create()
        v = r["variant_ids"][0]
        b1 = self.c.post(f"/api/variants/{v}/barcodes", json={"source":"store"}).json()["barcode"]
        b2 = self.c.post(f"/api/variants/{v}/barcodes", json={"source":"store"}).json()["barcode"]
        self.assertEqual(int(b2[2:]) - int(b1[2:]), 1)
```

- [ ] **Step 2: 跑測試確認失敗** — `python -m unittest tests.test_products -v` → FAIL

- [ ] **Step 3: 實作** `api/products.py`

```python
import json
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
    barcodes: list[BarcodeIn] = []

class ProductIn(BaseModel):
    name: str
    category: str | None = None
    default_price: int | None = None
    note: str | None = None
    variants: list[VariantIn] = []

def next_store_barcode(conn):
    row = conn.execute("SELECT value FROM Setting WHERE key='next_store_barcode'").fetchone()
    n = int(row["value"]) if row else 1
    conn.execute("INSERT OR REPLACE INTO Setting(key,value) VALUES('next_store_barcode',?)",
                 (str(n + 1),))
    return f"SP{n:08d}"

def stock_of(conn, variant_id):
    r = conn.execute("SELECT COALESCE(SUM(qty),0) s FROM StockMovement WHERE variant_id=?",
                     (variant_id,)).fetchone()
    return r["s"]

@router.post("/products")
def create_product(body: ProductIn, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        cur = conn.execute(
            "INSERT INTO Product(name,category,default_price,note) VALUES(?,?,?,?)",
            (body.name, body.category, body.default_price, body.note))
        pid = cur.lastrowid
        vids = []
        for v in body.variants:
            cur = conn.execute(
                "INSERT INTO Variant(product_id,attributes,price) VALUES(?,?,?)",
                (pid, json.dumps(v.attributes, ensure_ascii=False), v.price))
            vid = cur.lastrowid
            vids.append(vid)
            for b in v.barcodes:
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
            "SELECT v.variant_id, v.product_id, v.attributes, "
            "COALESCE(v.price, p.default_price) AS price, p.name "
            "FROM Barcode b JOIN Variant v ON b.variant_id=v.variant_id "
            "JOIN Product p ON v.product_id=p.product_id WHERE b.barcode=?",
            (code,)).fetchone()
        if not row:
            raise HTTPException(404, "查無此條碼")
        return {"variant_id": row["variant_id"], "product_id": row["product_id"],
                "name": row["name"], "attributes": json.loads(row["attributes"]),
                "price": row["price"], "stock": stock_of(conn, row["variant_id"])}
    finally:
        conn.close()

@router.post("/variants/{variant_id}/barcodes")
def add_barcode(variant_id: int, body: BarcodeIn, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        code = body.barcode or next_store_barcode(conn)
        conn.execute("INSERT INTO Barcode(barcode,variant_id,source) VALUES(?,?,?)",
                     (code, variant_id, body.source))
        conn.commit()
        return {"barcode": code}
    finally:
        conn.close()

@router.get("/products")
def search(request: Request, q: str = ""):
    conn = get_conn(request.app.state.db_path)
    try:
        like = f"%{q}%"
        rows = conn.execute(
            "SELECT v.variant_id, p.name, v.attributes, "
            "COALESCE(v.price,p.default_price) AS price "
            "FROM Variant v JOIN Product p ON v.product_id=p.product_id "
            "WHERE p.name LIKE ? OR v.attributes LIKE ? LIMIT 100", (like, like))
        return [{"variant_id": r["variant_id"], "name": r["name"],
                 "attributes": json.loads(r["attributes"]), "price": r["price"],
                 "stock": stock_of(conn, r["variant_id"])} for r in rows]
    finally:
        conn.close()
```

`api/__init__.py` 的 create_app 中加:
```python
    from api import attributes, products
    app.include_router(attributes.router)
    app.include_router(products.router)
```

- [ ] **Step 4: 跑測試通過** — `python -m unittest tests.test_products -v` → OK(5 tests)

- [ ] **Step 5: Commit** — `git add api/ tests/test_products.py && git commit -m "feat: 商品/變體/條碼 API(掃碼查詢、店內條碼流水)"`

---

### Task 4: 進貨與庫存 API

**Files:**
- Create: `api/stock.py`、`tests/test_stock.py`
- Modify: `api/__init__.py`(掛 router)

**Interfaces:**
- Produces:
  - `POST /api/stock/receive` body `{variant_id,qty,note?}` → 寫 `StockMovement(kind='purchase')`,回 `{stock}`(新庫存)
  - `GET /api/stock/{variant_id}` → `{stock, movements:[...最近50筆]}`
- Consumes: `products.stock_of`

- [ ] **Step 1: 寫失敗測試** `tests/test_stock.py`

```python
import unittest, tempfile, os
from fastapi.testclient import TestClient
from lib.db import init_db
from api import create_app

class TestStock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)
        self.c = TestClient(create_app(self.db))
        r = self.c.post("/api/products", json={"name": "測試品", "variants":
            [{"attributes": {}, "barcodes": [{"barcode": "B1", "source": "store"}]}]})
        self.vid = r.json()["variant_ids"][0]

    def test_receive_accumulates(self):
        self.assertEqual(self.c.post("/api/stock/receive",
            json={"variant_id": self.vid, "qty": 5}).json()["stock"], 5)
        self.assertEqual(self.c.post("/api/stock/receive",
            json={"variant_id": self.vid, "qty": 3}).json()["stock"], 8)

    def test_detail_lists_movements(self):
        self.c.post("/api/stock/receive", json={"variant_id": self.vid, "qty": 5})
        r = self.c.get(f"/api/stock/{self.vid}").json()
        self.assertEqual(r["stock"], 5)
        self.assertEqual(r["movements"][0]["kind"], "purchase")

    def test_reject_zero_qty(self):
        r = self.c.post("/api/stock/receive", json={"variant_id": self.vid, "qty": 0})
        self.assertEqual(r.status_code, 422)
```

- [ ] **Step 2: 確認失敗** — `python -m unittest tests.test_stock -v` → FAIL

- [ ] **Step 3: 實作** `api/stock.py`

```python
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel, Field
from lib.db import get_conn
from api.products import stock_of

router = APIRouter(prefix="/api")

class ReceiveIn(BaseModel):
    variant_id: int
    qty: int = Field(gt=0)   # 進貨必為正
    note: str | None = None

@router.post("/stock/receive")
def receive(body: ReceiveIn, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        conn.execute(
            "INSERT INTO StockMovement(variant_id,qty,kind,note) VALUES(?,?,'purchase',?)",
            (body.variant_id, body.qty, body.note))
        conn.commit()
        return {"stock": stock_of(conn, body.variant_id)}
    finally:
        conn.close()

@router.get("/stock/{variant_id}")
def detail(variant_id: int, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        moves = [dict(r) for r in conn.execute(
            "SELECT * FROM StockMovement WHERE variant_id=? ORDER BY move_id DESC LIMIT 50",
            (variant_id,))]
        return {"stock": stock_of(conn, variant_id), "movements": moves}
    finally:
        conn.close()
```

並在 `api/__init__.py` 掛上 `stock.router`。

- [ ] **Step 4: 通過** — `python -m unittest tests.test_stock -v` → OK(3 tests)
- [ ] **Step 5: Commit** — `git commit -m "feat: 進貨入庫與庫存查詢 API"`

---

### Task 5: 結帳與銷售紀錄 API

**Files:**
- Create: `api/sales.py`、`tests/test_sales.py`
- Modify: `api/__init__.py`(掛 router)

**Interfaces:**
- Produces:
  - `GET /api/payments` → `["現金","刷卡","行動支付"]`(讀 Setting)
  - `POST /api/sales` body `{payment, order_discount?, paid, items:[{variant_id,qty,unit_price,discount?}]}` → 同一 transaction 寫 Sale+SaleItem+每項 StockMovement(qty 為負,kind='sale',ref_id=sale_id);`total = Σ(qty*unit_price - discount) - order_discount`;`change = paid - total`;total<0 回 422
  - `GET /api/sales?date_from=&date_to=&payment=` → 交易列表(含明細)
  - `GET /api/sales/summary?date=YYYY-MM-DD` → `{total, by_payment:{現金:…}, count}`
  - `GET /api/sales/export?date_from=&date_to=` → CSV(utf-8-sig)

- [ ] **Step 1: 寫失敗測試** `tests/test_sales.py`

```python
import unittest, tempfile, os, datetime
from fastapi.testclient import TestClient
from lib.db import init_db
from api import create_app

class TestSales(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)
        self.c = TestClient(create_app(self.db))
        r = self.c.post("/api/products", json={"name": "膜", "default_price": 500,
            "variants": [{"attributes": {}, "barcodes": [{"barcode":"B1","source":"store"}]}]})
        self.vid = r.json()["variant_ids"][0]
        self.c.post("/api/stock/receive", json={"variant_id": self.vid, "qty": 10})

    def _sale(self, **kw):
        body = {"payment": "現金", "paid": 1000,
                "items": [{"variant_id": self.vid, "qty": 2, "unit_price": 500}]}
        body.update(kw)
        return self.c.post("/api/sales", json=body)

    def test_checkout_math_and_stock(self):
        r = self._sale(order_discount=100, paid=900).json()
        self.assertEqual(r["total"], 900)   # 2*500-100
        self.assertEqual(r["change"], 0)
        self.assertEqual(self.c.get(f"/api/stock/{self.vid}").json()["stock"], 8)

    def test_item_discount(self):
        r = self._sale(items=[{"variant_id": self.vid, "qty": 1,
                               "unit_price": 500, "discount": 50}]).json()
        self.assertEqual(r["total"], 450)

    def test_negative_total_rejected(self):
        r = self._sale(order_discount=99999)
        self.assertEqual(r.status_code, 422)
        # 交易失敗庫存不動
        self.assertEqual(self.c.get(f"/api/stock/{self.vid}").json()["stock"], 10)

    def test_summary(self):
        self._sale(); self._sale(payment="刷卡")
        today = datetime.date.today().isoformat()
        s = self.c.get(f"/api/sales/summary?date={today}").json()
        self.assertEqual(s["count"], 2)
        self.assertEqual(s["by_payment"]["現金"], 1000)

    def test_export_csv(self):
        self._sale()
        r = self.c.get("/api/sales/export")
        self.assertEqual(r.status_code, 200)
        self.assertIn("csv", r.headers["content-type"])
```

- [ ] **Step 2: 確認失敗** — `python -m unittest tests.test_sales -v` → FAIL

- [ ] **Step 3: 實作** `api/sales.py`

```python
import json, io, csv
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from lib.db import get_conn

router = APIRouter(prefix="/api")

class ItemIn(BaseModel):
    variant_id: int
    qty: int = Field(gt=0)
    unit_price: int = Field(ge=0)
    discount: int = Field(default=0, ge=0)

class SaleIn(BaseModel):
    payment: str
    order_discount: int = Field(default=0, ge=0)
    paid: int = Field(ge=0)
    items: list[ItemIn] = Field(min_length=1)

@router.get("/payments")
def payments(request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        row = conn.execute("SELECT value FROM Setting WHERE key='payments'").fetchone()
        return json.loads(row["value"])
    finally:
        conn.close()

@router.post("/sales")
def checkout(body: SaleIn, request: Request):
    total = sum(i.qty * i.unit_price - i.discount for i in body.items) - body.order_discount
    if total < 0:
        raise HTTPException(422, "折扣後金額不可為負")
    conn = get_conn(request.app.state.db_path)
    try:
        cur = conn.execute(
            "INSERT INTO Sale(payment,order_discount,total,paid,change) VALUES(?,?,?,?,?)",
            (body.payment, body.order_discount, total, body.paid, body.paid - total))
        sale_id = cur.lastrowid
        for i in body.items:
            conn.execute(
                "INSERT INTO SaleItem(sale_id,variant_id,qty,unit_price,discount) "
                "VALUES(?,?,?,?,?)",
                (sale_id, i.variant_id, i.qty, i.unit_price, i.discount))
            conn.execute(
                "INSERT INTO StockMovement(variant_id,qty,kind,ref_id) "
                "VALUES(?,?,'sale',?)", (i.variant_id, -i.qty, sale_id))
        conn.commit()   # 全部一次 commit=同一 transaction
        return {"sale_id": sale_id, "total": total, "change": body.paid - total}
    finally:
        conn.close()

def _query_sales(conn, date_from, date_to, payment):
    sql = ("SELECT s.*, i.variant_id, i.qty, i.unit_price, i.discount, p.name, v.attributes "
           "FROM Sale s JOIN SaleItem i ON s.sale_id=i.sale_id "
           "JOIN Variant v ON i.variant_id=v.variant_id "
           "JOIN Product p ON v.product_id=p.product_id WHERE 1=1")
    args = []
    if date_from: sql += " AND date(s.ts)>=?"; args.append(date_from)
    if date_to:   sql += " AND date(s.ts)<=?"; args.append(date_to)
    if payment:   sql += " AND s.payment=?";   args.append(payment)
    return conn.execute(sql + " ORDER BY s.sale_id DESC", args).fetchall()

@router.get("/sales")
def list_sales(request: Request, date_from: str = "", date_to: str = "", payment: str = ""):
    conn = get_conn(request.app.state.db_path)
    try:
        out = {}
        for r in _query_sales(conn, date_from, date_to, payment):
            s = out.setdefault(r["sale_id"], {
                "sale_id": r["sale_id"], "ts": r["ts"], "payment": r["payment"],
                "order_discount": r["order_discount"], "total": r["total"], "items": []})
            s["items"].append({"variant_id": r["variant_id"], "name": r["name"],
                "attributes": json.loads(r["attributes"]), "qty": r["qty"],
                "unit_price": r["unit_price"], "discount": r["discount"]})
        return list(out.values())
    finally:
        conn.close()

@router.get("/sales/summary")
def summary(request: Request, date: str = ""):
    conn = get_conn(request.app.state.db_path)
    try:
        sql = "SELECT payment, COUNT(*) c, SUM(total) t FROM Sale"
        args = []
        if date: sql += " WHERE date(ts)=?"; args.append(date)
        rows = conn.execute(sql + " GROUP BY payment", args).fetchall()
        return {"count": sum(r["c"] for r in rows),
                "total": sum(r["t"] or 0 for r in rows),
                "by_payment": {r["payment"]: r["t"] for r in rows}}
    finally:
        conn.close()

@router.get("/sales/export")
def export_csv(request: Request, date_from: str = "", date_to: str = ""):
    conn = get_conn(request.app.state.db_path)
    try:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["交易編號","時間","付款方式","商品","屬性","數量","成交單價","單品折扣","整單折抵","應收"])
        for r in _query_sales(conn, date_from, date_to, ""):
            w.writerow([r["sale_id"], r["ts"], r["payment"], r["name"],
                        r["attributes"], r["qty"], r["unit_price"], r["discount"],
                        r["order_discount"], r["total"]])
        data = "﻿" + buf.getvalue()   # utf-8-sig 給 Excel
        return StreamingResponse(iter([data]), media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=sales.csv"})
    finally:
        conn.close()
```

並在 `api/__init__.py` 掛上 `sales.router`。

- [ ] **Step 4: 通過** — `python -m unittest tests.test_sales -v` → OK(5 tests)
- [ ] **Step 5: Commit** — `git commit -m "feat: 結帳(transaction)與銷售紀錄/日結/CSV API"`

---

### Task 6: 盤點 API

**Files:**
- Create: `api/stocktake.py`、`tests/test_stocktake.py`
- Modify: `api/__init__.py`(掛 router)

**Interfaces:**
- Produces:
  - `POST /api/stocktake` body `{operator?,note?}` → `{session_id}`(status=open)
  - `POST /api/stocktake/{sid}/scan` body `{variant_id, qty?}`:首掃寫入 `system_qty` 快照、counted=qty(預設1);再掃累加 counted → `{counted_qty, system_qty}`
  - `PUT /api/stocktake/{sid}/items/{variant_id}` body `{counted_qty}`(手改)
  - `GET /api/stocktake/{sid}` → 明細+差異
  - `POST /api/stocktake/{sid}/close` → 只對差異≠0 的變體寫 `StockMovement(kind='adjust', ref_id=sid)`,status→closed;已 closed 回 409
  - `GET /api/stocktake` → 盤點單列表(續盤入口)

- [ ] **Step 1: 寫失敗測試** `tests/test_stocktake.py`

```python
import unittest, tempfile, os
from fastapi.testclient import TestClient
from lib.db import init_db
from api import create_app

class TestStocktake(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)
        self.c = TestClient(create_app(self.db))
        r = self.c.post("/api/products", json={"name": "品A", "variants":
            [{"attributes": {}, "barcodes": [{"barcode":"A1","source":"store"}]},
             {"attributes": {}, "barcodes": [{"barcode":"A2","source":"store"}]}]})
        self.v1, self.v2 = r.json()["variant_ids"]
        self.c.post("/api/stock/receive", json={"variant_id": self.v1, "qty": 5})
        self.c.post("/api/stock/receive", json={"variant_id": self.v2, "qty": 3})
        self.sid = self.c.post("/api/stocktake", json={"operator": "測試"}).json()["session_id"]

    def test_scan_snapshot_and_accumulate(self):
        r = self.c.post(f"/api/stocktake/{self.sid}/scan", json={"variant_id": self.v1}).json()
        self.assertEqual((r["system_qty"], r["counted_qty"]), (5, 1))
        r = self.c.post(f"/api/stocktake/{self.sid}/scan", json={"variant_id": self.v1}).json()
        self.assertEqual(r["counted_qty"], 2)

    def test_close_adjusts_only_diff(self):
        # v1 實盤 4(差 -1);v2 沒盤 → 不動
        self.c.post(f"/api/stocktake/{self.sid}/scan",
                    json={"variant_id": self.v1, "qty": 4})
        self.c.post(f"/api/stocktake/{self.sid}/close")
        self.assertEqual(self.c.get(f"/api/stock/{self.v1}").json()["stock"], 4)
        self.assertEqual(self.c.get(f"/api/stock/{self.v2}").json()["stock"], 3)

    def test_close_twice_409(self):
        self.c.post(f"/api/stocktake/{self.sid}/close")
        self.assertEqual(self.c.post(f"/api/stocktake/{self.sid}/close").status_code, 409)

    def test_manual_set(self):
        self.c.post(f"/api/stocktake/{self.sid}/scan", json={"variant_id": self.v1})
        self.c.put(f"/api/stocktake/{self.sid}/items/{self.v1}", json={"counted_qty": 7})
        d = self.c.get(f"/api/stocktake/{self.sid}").json()
        item = [i for i in d["items"] if i["variant_id"] == self.v1][0]
        self.assertEqual(item["counted_qty"], 7)
        self.assertEqual(item["diff"], 2)
```

- [ ] **Step 2: 確認失敗** — `python -m unittest tests.test_stocktake -v` → FAIL

- [ ] **Step 3: 實作** `api/stocktake.py`

```python
import json
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from lib.db import get_conn
from api.products import stock_of

router = APIRouter(prefix="/api")

class SessionIn(BaseModel):
    operator: str | None = None
    note: str | None = None

class ScanIn(BaseModel):
    variant_id: int
    qty: int = 1

class SetIn(BaseModel):
    counted_qty: int

@router.post("/stocktake")
def open_session(body: SessionIn, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        cur = conn.execute("INSERT INTO StocktakeSession(operator,note) VALUES(?,?)",
                           (body.operator, body.note))
        conn.commit()
        return {"session_id": cur.lastrowid}
    finally:
        conn.close()

@router.get("/stocktake")
def list_sessions(request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM StocktakeSession ORDER BY session_id DESC LIMIT 50")]
    finally:
        conn.close()

def _require_open(conn, sid):
    row = conn.execute("SELECT status FROM StocktakeSession WHERE session_id=?",
                       (sid,)).fetchone()
    if not row:
        raise HTTPException(404, "查無此盤點單")
    if row["status"] != "open":
        raise HTTPException(409, "盤點單已結案")

@router.post("/stocktake/{sid}/scan")
def scan(sid: int, body: ScanIn, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        _require_open(conn, sid)
        row = conn.execute(
            "SELECT * FROM StocktakeItem WHERE session_id=? AND variant_id=?",
            (sid, body.variant_id)).fetchone()
        if row:
            counted = row["counted_qty"] + body.qty
            conn.execute("UPDATE StocktakeItem SET counted_qty=? WHERE id=?",
                         (counted, row["id"]))
            system = row["system_qty"]
        else:
            system = stock_of(conn, body.variant_id)  # 開盤快照:首掃當下
            counted = body.qty
            conn.execute(
                "INSERT INTO StocktakeItem(session_id,variant_id,system_qty,counted_qty) "
                "VALUES(?,?,?,?)", (sid, body.variant_id, system, counted))
        conn.commit()
        return {"system_qty": system, "counted_qty": counted}
    finally:
        conn.close()

@router.put("/stocktake/{sid}/items/{variant_id}")
def set_counted(sid: int, variant_id: int, body: SetIn, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        _require_open(conn, sid)
        conn.execute(
            "UPDATE StocktakeItem SET counted_qty=? WHERE session_id=? AND variant_id=?",
            (body.counted_qty, sid, variant_id))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()

@router.get("/stocktake/{sid}")
def detail(sid: int, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        items = []
        for r in conn.execute(
            "SELECT si.*, p.name, v.attributes FROM StocktakeItem si "
            "JOIN Variant v ON si.variant_id=v.variant_id "
            "JOIN Product p ON v.product_id=p.product_id WHERE si.session_id=?", (sid,)):
            items.append({"variant_id": r["variant_id"], "name": r["name"],
                "attributes": json.loads(r["attributes"]),
                "system_qty": r["system_qty"], "counted_qty": r["counted_qty"],
                "diff": r["counted_qty"] - r["system_qty"]})
        sess = conn.execute("SELECT * FROM StocktakeSession WHERE session_id=?",
                            (sid,)).fetchone()
        if not sess:
            raise HTTPException(404, "查無此盤點單")
        return {**dict(sess), "items": items}
    finally:
        conn.close()

@router.post("/stocktake/{sid}/close")
def close(sid: int, request: Request):
    conn = get_conn(request.app.state.db_path)
    try:
        _require_open(conn, sid)
        for r in conn.execute(
                "SELECT variant_id, counted_qty - system_qty AS diff "
                "FROM StocktakeItem WHERE session_id=?", (sid,)):
            if r["diff"] != 0:
                conn.execute(
                    "INSERT INTO StockMovement(variant_id,qty,kind,ref_id,note) "
                    "VALUES(?,?,'adjust',?,'盤點調整')", (r["variant_id"], r["diff"], sid))
        conn.execute("UPDATE StocktakeSession SET status='closed', "
                     "ended_at=datetime('now','localtime') WHERE session_id=?", (sid,))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()
```

並在 `api/__init__.py` 掛上 `stocktake.router`。

- [ ] **Step 4: 通過** — `python -m unittest tests.test_stocktake -v` → OK(4 tests)
- [ ] **Step 5: 跑全部測試** — `python -m unittest discover -s tests -v` → 全 OK
- [ ] **Step 6: Commit** — `git commit -m "feat: 盤點單 API(快照/累掃/局部盤結案)"`

---

### Task 7: 備份 + 進入點 main.py + 條碼列印 stub

**Files:**
- Create: `lib/backup.py`、`api/printing.py`、`main.py`、`tests/test_backup.py`
- Modify: `api/__init__.py`(掛 printing.router)

**Interfaces:**
- Produces:
  - `backup.run_auto_backup(db_path)`:GFS 至 `<db 同層>/backups/`;每日第一次開啟建 `pos_day_YYYYMMDD.db` 留 7、每 ISO 週 `pos_week_YYYYMMDD.db` 留 4、每月 `pos_month_YYYYMMDD.db` 留 12;sqlite3 backup API 快照;全程 try/except 失敗不擋啟動
  - `POST /api/print/barcode` body `{barcode,name}` → 501 `{"detail":"條碼列印尚未接上實體印表機"}`(介面預留)
  - `main.py`:確保 `data/` → `init_db` → `run_auto_backup` → 起 uvicorn(127.0.0.1, port 8737)→ `webbrowser.open`

- [ ] **Step 1: 寫失敗測試** `tests/test_backup.py`

```python
import unittest, tempfile, os, glob
from lib.db import init_db
from lib.backup import run_auto_backup

class TestBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "pos.db")
        init_db(self.db)

    def test_creates_daily(self):
        run_auto_backup(self.db)
        self.assertEqual(len(glob.glob(os.path.join(self.tmp, "backups", "pos_day_*.db"))), 1)

    def test_same_day_no_duplicate(self):
        run_auto_backup(self.db); run_auto_backup(self.db)
        self.assertEqual(len(glob.glob(os.path.join(self.tmp, "backups", "pos_day_*.db"))), 1)

    def test_prune_keeps_7(self):
        bdir = os.path.join(self.tmp, "backups"); os.makedirs(bdir)
        for d in range(1, 10):
            open(os.path.join(bdir, f"pos_day_202601{d:02d}.db"), "w").close()
        run_auto_backup(self.db)
        self.assertLessEqual(
            len(glob.glob(os.path.join(bdir, "pos_day_*.db"))), 7)

    def test_failure_silent(self):
        run_auto_backup(os.path.join(self.tmp, "no_such.db"))  # 不拋例外
```

- [ ] **Step 2: 確認失敗** — `python -m unittest tests.test_backup -v` → FAIL

- [ ] **Step 3: 實作**

`lib/backup.py`
```python
import os, glob, sqlite3, datetime

KEEP = {"day": 7, "week": 4, "month": 12}

def _snapshot(db_path, dest):
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest + ".tmp")
        src.backup(dst)
        dst.close()
        os.replace(dest + ".tmp", dest)
    finally:
        src.close()

def run_auto_backup(db_path):
    try:
        if not os.path.exists(db_path):
            return
        bdir = os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")
        os.makedirs(bdir, exist_ok=True)
        today = datetime.date.today()
        tags = {"day": today.strftime("%Y%m%d"),
                "week": f"{today.isocalendar()[0]}W{today.isocalendar()[1]:02d}",
                "month": today.strftime("%Y%m")}
        for kind, tag in tags.items():
            pattern = os.path.join(bdir, f"pos_{kind}_*.db")
            if any(tag in os.path.basename(p) for p in glob.glob(pattern)):
                continue  # 本期已備
            _snapshot(db_path, os.path.join(bdir, f"pos_{kind}_{tag}.db"))
            files = sorted(glob.glob(pattern))
            for old in files[:-KEEP[kind]]:
                os.remove(old)
    except Exception:
        pass  # 備份失敗絕不擋啟動;無 log 機制前先靜默
```

> 注意:`test_prune_keeps_7` 的假檔名 `pos_day_20260101.db` 不含今日 tag,今日快照會新增後裁到 7。

`api/printing.py`
```python
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api")

class PrintIn(BaseModel):
    barcode: str
    name: str = ""

@router.post("/print/barcode")
def print_barcode(body: PrintIn):
    # 預留介面:之後接實體條碼機時只改這裡
    raise HTTPException(501, "條碼列印尚未接上實體印表機")
```

`main.py`
```python
import os, sys, threading, webbrowser
import uvicorn
from lib.db import init_db
from lib.backup import run_auto_backup
from api import create_app

PORT = 8737

def data_dir():
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) \
        else os.path.dirname(os.path.abspath(__file__))
    d = os.path.join(base, "data")
    os.makedirs(d, exist_ok=True)
    return d

def main():
    db_path = os.path.join(data_dir(), "pos.db")
    init_db(db_path)
    run_auto_backup(db_path)
    app = create_app(db_path)
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="warning")

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 通過** — `python -m unittest tests.test_backup -v` → OK(4 tests)
- [ ] **Step 5: 手動驗證** — `python main.py` → 瀏覽器自動開、顯示占位頁;Ctrl+C 關閉
- [ ] **Step 6: Commit** — `git commit -m "feat: GFS 自動備份、main 進入點、條碼列印介面預留"`

---

### Task 8: 前端骨架(導覽 + api.js + Vue 本地檔)

**Files:**
- Create: `static/js/vue.global.prod.js`(下載)、`static/js/api.js`、`static/js/app.js`、`static/css/pos.css`
- Modify: `static/index.html`(整個重寫)

**Interfaces:**
- Produces:
  - `api.js`:全域 `API = {get(url), post(url, body), put(url, body)}`,非 2xx 拋 `Error(detail)`
  - `app.js`:Vue app,`page` 狀態切換 `checkout/receive/stocktake/records/settings` 五頁;全域錯誤提示列
  - 各頁面元件掛在 `window.PosPages = {}`(checkout.js 等各自註冊),app.js 統一 `app.component()` 掛載

- [ ] **Step 1: 下載 Vue** — `curl -L -o static/js/vue.global.prod.js https://unpkg.com/vue@3/dist/vue.global.prod.js`(確認檔案 >100KB 且開頭含 `Vue`)

- [ ] **Step 2: 實作**

`static/js/api.js`
```javascript
const API = {
  async _do(method, url, body) {
    const opt = { method, headers: { "Content-Type": "application/json" } };
    if (body !== undefined) opt.body = JSON.stringify(body);
    const r = await fetch(url, opt);
    if (!r.ok) {
      let msg = "系統發生錯誤";
      try { msg = (await r.json()).detail || msg; } catch (e) {}
      throw new Error(msg);
    }
    return r.json();
  },
  get(url) { return this._do("GET", url); },
  post(url, body) { return this._do("POST", url, body); },
  put(url, body) { return this._do("PUT", url, body); },
};
```

`static/js/app.js`
```javascript
window.PosPages = window.PosPages || {};
document.addEventListener("DOMContentLoaded", () => {
  const app = Vue.createApp({
    data() {
      return { page: "checkout", error: "", pages: [
        ["checkout", "收銀"], ["receive", "進貨"], ["stocktake", "盤點"],
        ["records", "銷售紀錄"], ["settings", "設定"]] };
    },
    methods: {
      showError(msg) { this.error = msg; setTimeout(() => this.error = "", 5000); },
    },
    provide() { return { showError: (m) => this.showError(m) }; },
  });
  for (const [name, comp] of Object.entries(window.PosPages))
    app.component(name, comp);
  app.mount("#app");
});
```

`static/index.html`
```html
<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>POS</title>
<link rel="stylesheet" href="/css/pos.css">
</head>
<body>
<div id="app">
  <nav>
    <button v-for="[key, label] in pages" :key="key"
            :class="{active: page===key}" @click="page=key">{{ label }}</button>
  </nav>
  <div class="error-bar" v-if="error">{{ error }}</div>
  <component :is="'page-' + page"></component>
</div>
<!-- 各頁模板集中於此,由各 js 以 template: '#tpl-xxx' 引用 -->
<script src="/js/vue.global.prod.js"></script>
<script src="/js/api.js"></script>
<script src="/js/checkout.js"></script>
<script src="/js/receive.js"></script>
<script src="/js/stocktake.js"></script>
<script src="/js/records.js"></script>
<script src="/js/settings.js"></script>
<script src="/js/app.js"></script>
</body>
</html>
```

`static/css/pos.css`(基礎;細節隨頁面補)
```css
* { box-sizing: border-box; margin: 0; }
body { font-family: "Microsoft JhengHei", sans-serif; font-size: 16px; background: #f5f5f7; }
nav { display: flex; gap: 8px; padding: 10px; background: #1f2937; }
nav button { padding: 12px 24px; font-size: 18px; border: 0; border-radius: 8px;
  background: #374151; color: #e5e7eb; cursor: pointer; }
nav button.active { background: #2563eb; color: #fff; }
.error-bar { background: #dc2626; color: #fff; padding: 10px 16px; }
.page { padding: 16px; }
table { width: 100%; border-collapse: collapse; background: #fff; }
th, td { border: 1px solid #d1d5db; padding: 8px 10px; text-align: left; }
button.primary { background: #2563eb; color: #fff; border: 0; padding: 12px 20px;
  border-radius: 8px; font-size: 18px; cursor: pointer; }
input, select { padding: 8px 10px; font-size: 16px; border: 1px solid #d1d5db;
  border-radius: 6px; }
.neg { color: #dc2626; font-weight: bold; }
```

各頁 js 先建占位(讓導覽可切),例 `static/js/checkout.js`:
```javascript
window.PosPages = window.PosPages || {};
window.PosPages["page-checkout"] = {
  template: `<div class="page"><h2>收銀</h2></div>`,
};
```
(receive/stocktake/records/settings 同式各建一檔,標題各異)

- [ ] **Step 3: 手動驗證** — `python main.py` → 五個導覽鈕可切換、無 console 錯誤(F12)
- [ ] **Step 4: Commit** — `git add static/ && git commit -m "feat: 前端骨架(Vue 本地檔、導覽、API 包裝)"`

---

### Task 9: 收銀畫面

**Files:**
- Modify: `static/js/checkout.js`(整檔重寫)、`static/index.html`(加 `<template id="tpl-checkout">`)、`static/css/pos.css`(追加)

**Interfaces:**
- Consumes: `GET /api/barcode/{code}`、`GET /api/payments`、`POST /api/sales`、`GET /api/products?q=`
- 行為:掃描框常駐 focus(blur 後 300ms 拉回);Enter=查條碼入購物車(同變體累加);可改單價/單品折扣/數量/刪行;整單折抵、付款方式、實收、找零即時算;無價商品入車時強制先填價;結帳成功清空+顯示找零;查無條碼顯示訊息並保留輸入內容供改查

- [ ] **Step 1: 實作** — `static/index.html` 加模板:

```html
<template id="tpl-checkout">
<div class="page checkout">
  <div class="scan-row">
    <input ref="scan" v-model="scanCode" @keyup.enter="onScan"
           placeholder="掃描條碼或輸入後按 Enter" class="scan-input" autofocus>
    <input v-model="searchQ" @keyup.enter="onSearch" placeholder="關鍵字搜尋商品">
  </div>
  <div v-if="searchResults.length" class="search-pop">
    <div v-for="r in searchResults" :key="r.variant_id" class="search-item"
         @click="addItem(r)">
      {{ r.name }}|{{ attrText(r.attributes) }}|
      {{ r.price === null ? '未定價' : r.price + ' 元' }}|庫存 {{ r.stock }}
    </div>
  </div>
  <table>
    <thead><tr><th>商品</th><th>屬性</th><th>單價</th><th>數量</th>
      <th>單品折扣</th><th>小計</th><th></th></tr></thead>
    <tbody>
      <tr v-for="(it, i) in cart" :key="it.variant_id">
        <td>{{ it.name }}</td>
        <td>{{ attrText(it.attributes) }}</td>
        <td><input type="number" v-model.number="it.unit_price" min="0" class="w80"></td>
        <td><input type="number" v-model.number="it.qty" min="1" class="w60"></td>
        <td><input type="number" v-model.number="it.discount" min="0" class="w80"></td>
        <td>{{ it.qty * it.unit_price - it.discount }}</td>
        <td><button @click="cart.splice(i,1)">✕</button></td>
      </tr>
    </tbody>
  </table>
  <div class="pay-panel">
    <label>整單折抵 <input type="number" v-model.number="orderDiscount" min="0" class="w80"></label>
    <label>付款方式
      <select v-model="payment"><option v-for="p in payments" :key="p">{{ p }}</option></select>
    </label>
    <div class="total">應收 {{ total }} 元</div>
    <label>實收 <input type="number" v-model.number="paid" min="0" class="w100"></label>
    <div class="total">找零 {{ Math.max(0, paid - total) }} 元</div>
    <button class="primary" :disabled="!cart.length || paid < total"
            @click="checkout">結帳</button>
  </div>
  <div v-if="doneMsg" class="done-msg">{{ doneMsg }}</div>
</div>
</template>
```

`static/js/checkout.js`
```javascript
window.PosPages = window.PosPages || {};
window.PosPages["page-checkout"] = {
  template: "#tpl-checkout",
  inject: ["showError"],
  data() {
    return { scanCode: "", searchQ: "", searchResults: [], cart: [],
             payments: [], payment: "現金", orderDiscount: 0, paid: 0, doneMsg: "" };
  },
  computed: {
    total() {
      const t = this.cart.reduce((s, i) => s + i.qty * i.unit_price - i.discount, 0)
        - this.orderDiscount;
      return Math.max(0, t);
    },
  },
  async mounted() {
    this.payments = await API.get("/api/payments");
    this.payment = this.payments[0];
    this.$refs.scan.focus();
    this._refocus = () => setTimeout(() => {
      if (document.activeElement.tagName !== "INPUT" &&
          document.activeElement.tagName !== "SELECT") this.$refs.scan?.focus();
    }, 300);
    document.addEventListener("click", this._refocus);
  },
  unmounted() { document.removeEventListener("click", this._refocus); },
  methods: {
    attrText(a) { return Object.values(a).join(" / "); },
    addItem(r) {
      let price = r.price;
      if (price === null) {
        const s = prompt(`「${r.name}」尚未定價,請輸入成交單價:`);
        if (s === null) return;
        price = parseInt(s, 10);
        if (isNaN(price) || price < 0) { this.showError("價格輸入不正確"); return; }
      }
      const dup = this.cart.find(i => i.variant_id === r.variant_id);
      if (dup) dup.qty += 1;
      else this.cart.push({ variant_id: r.variant_id, name: r.name,
        attributes: r.attributes, unit_price: price, qty: 1, discount: 0 });
      this.searchResults = [];
    },
    async onScan() {
      const code = this.scanCode.trim();
      if (!code) return;
      try {
        this.addItem(await API.get("/api/barcode/" + encodeURIComponent(code)));
        this.scanCode = "";
      } catch (e) { this.showError(e.message); }  // 查無條碼:保留輸入
    },
    async onSearch() {
      if (!this.searchQ.trim()) return;
      this.searchResults = await API.get("/api/products?q=" +
        encodeURIComponent(this.searchQ.trim()));
    },
    async checkout() {
      try {
        const r = await API.post("/api/sales", {
          payment: this.payment, order_discount: this.orderDiscount,
          paid: this.paid,
          items: this.cart.map(i => ({ variant_id: i.variant_id, qty: i.qty,
            unit_price: i.unit_price, discount: i.discount })) });
        this.doneMsg = `結帳完成,找零 ${r.change} 元(交易編號 ${r.sale_id})`;
        this.cart = []; this.orderDiscount = 0; this.paid = 0;
        setTimeout(() => this.doneMsg = "", 5000);
      } catch (e) { this.showError(e.message); }
    },
  },
};
```

`pos.css` 追加:
```css
.scan-row { display: flex; gap: 10px; margin-bottom: 12px; }
.scan-input { flex: 1; font-size: 22px; padding: 12px; }
.w60 { width: 60px; } .w80 { width: 80px; } .w100 { width: 100px; }
.pay-panel { display: flex; gap: 16px; align-items: center; margin-top: 14px;
  flex-wrap: wrap; background: #fff; padding: 14px; border-radius: 8px; }
.total { font-size: 22px; font-weight: bold; }
.done-msg { margin-top: 10px; padding: 12px; background: #16a34a; color: #fff;
  border-radius: 8px; font-size: 18px; }
.search-pop { background: #fff; border: 1px solid #d1d5db; border-radius: 8px;
  margin-bottom: 10px; max-height: 300px; overflow-y: auto; }
.search-item { padding: 10px; cursor: pointer; border-bottom: 1px solid #eee; }
.search-item:hover { background: #eff6ff; }
```

- [ ] **Step 2: 手動驗證** — `python main.py`;用 Task 3 測試建的資料或先以 `curl`/swagger(`/docs`)建一筆商品,鍵盤模擬掃碼(輸入條碼+Enter)→ 入車 → 改價/折扣 → 結帳 → 找零正確、再掃庫存已減
- [ ] **Step 3: Commit** — `git commit -m "feat: 收銀畫面(掃碼入車/改價/折扣/結帳)"`

---

### Task 10: 進貨 + 快速建檔畫面(連動下拉)

**Files:**
- Modify: `static/js/receive.js`(整檔重寫)、`static/index.html`(加 `<template id="tpl-receive">`)

**Interfaces:**
- Consumes: `GET /api/barcode/{code}`、`POST /api/stock/receive`、`GET /api/fields`、`GET /api/options`、`POST /api/options`、`POST /api/products`、`POST /api/variants/{id}/barcodes`、`POST /api/print/barcode`
- 行為:掃碼 → 已存在:顯示品名+數量框,送出寫進貨;不存在:展開快速建檔表單(名稱、預設售價可空、八屬性欄=連動下拉+可手打 datalist、手打新值自動 POST /api/options 入選單);無條碼商品可按「產生店內條碼」;建檔成功即可接著輸入進貨量;「列印條碼」鈕呼叫 print API(現階段顯示 501 訊息)

- [ ] **Step 1: 實作** — `static/index.html` 加模板:

```html
<template id="tpl-receive">
<div class="page">
  <div class="scan-row">
    <input ref="scan" v-model="scanCode" @keyup.enter="onScan"
           placeholder="掃描條碼(查無自動進入建檔)" class="scan-input">
    <button class="primary" @click="startCreate('')">無條碼建檔</button>
  </div>
  <div v-if="hit" class="pay-panel">
    <div>{{ hit.name }}|{{ attrText(hit.attributes) }}|目前庫存 {{ hit.stock }}</div>
    <label>進貨數量 <input type="number" v-model.number="qty" min="1" class="w80"></label>
    <button class="primary" @click="doReceive">入庫</button>
  </div>
  <div v-if="creating" class="create-form">
    <h3>快速建檔{{ newBarcode ? '(條碼 ' + newBarcode + ')' : '' }}</h3>
    <label>商品名稱 <input v-model="form.name"></label>
    <label>預設售價(可留空) <input type="number" v-model.number="form.price" min="0"></label>
    <div v-for="f in fields" :key="f.field_id" class="attr-row">
      <label>{{ f.name }}
        <input :list="'dl-' + f.field_id" v-model="form.attrs[f.name]"
               @focus="loadOptions(f)" @change="maybeAddOption(f)">
        <datalist :id="'dl-' + f.field_id">
          <option v-for="o in optionsFor[f.field_id] || []" :key="o.option_id"
                  :value="o.value"></option>
        </datalist>
      </label>
    </div>
    <button class="primary" @click="createProduct">建檔</button>
    <button v-if="createdVid && !newBarcode" @click="genBarcode">產生店內條碼</button>
    <button v-if="newBarcode" @click="printBarcode">列印條碼</button>
  </div>
</div>
</template>
```

`static/js/receive.js`
```javascript
window.PosPages = window.PosPages || {};
window.PosPages["page-receive"] = {
  template: "#tpl-receive",
  inject: ["showError"],
  data() {
    return { scanCode: "", hit: null, qty: 1, creating: false, newBarcode: "",
             createdVid: null, fields: [], optionsFor: {},
             form: { name: "", price: null, attrs: {} } };
  },
  async mounted() {
    this.fields = await API.get("/api/fields");
    this.$refs.scan.focus();
  },
  methods: {
    attrText(a) { return Object.values(a).join(" / "); },
    async onScan() {
      const code = this.scanCode.trim();
      if (!code) return;
      try {
        this.hit = await API.get("/api/barcode/" + encodeURIComponent(code));
        this.creating = false; this.scanCode = "";
      } catch (e) { this.startCreate(code); }  // 查無 → 建檔,條碼帶入
    },
    startCreate(code) {
      this.hit = null; this.creating = true; this.newBarcode = code;
      this.createdVid = null;
      this.form = { name: "", price: null, attrs: {} };
    },
    parentOf(f) {
      // 連動規則:廠牌依商品種類、規格依廠牌、手機型號依手機品牌
      const map = { "廠牌": "商品種類", "規格": "廠牌", "手機型號": "手機品牌" };
      const pname = map[f.name];
      if (!pname) return null;
      const pf = this.fields.find(x => x.name === pname);
      const pv = this.form.attrs[pname];
      return (pf && pv) ? { field_id: pf.field_id, value: pv } : null;
    },
    async loadOptions(f) {
      const p = this.parentOf(f);
      let url = "/api/options?field_id=" + f.field_id;
      if (p) url += "&parent_field_id=" + p.field_id +
                    "&parent_value=" + encodeURIComponent(p.value);
      this.optionsFor[f.field_id] = await API.get(url);
    },
    async maybeAddOption(f) {
      const v = (this.form.attrs[f.name] || "").trim();
      if (!v) return;
      const known = (this.optionsFor[f.field_id] || []).some(o => o.value === v);
      if (known) return;
      if (!confirm(`「${v}」不在「${f.name}」選單中,要加入選單嗎?`)) return;
      const p = this.parentOf(f);
      await API.post("/api/options", { field_id: f.field_id, value: v,
        parent_field_id: p ? p.field_id : null, parent_value: p ? p.value : null });
    },
    async createProduct() {
      if (!this.form.name.trim()) { this.showError("請輸入商品名稱"); return; }
      try {
        const attrs = {};
        for (const [k, v] of Object.entries(this.form.attrs))
          if (v && v.trim()) attrs[k] = v.trim();
        const barcodes = this.newBarcode
          ? [{ barcode: this.newBarcode, source: "factory" }] : [];
        const r = await API.post("/api/products", { name: this.form.name.trim(),
          default_price: this.form.price ?? null,
          variants: [{ attributes: attrs, barcodes }] });
        this.createdVid = r.variant_ids[0];
        this.hit = this.newBarcode
          ? await API.get("/api/barcode/" + encodeURIComponent(this.newBarcode))
          : { name: this.form.name, attributes: attrs, stock: 0,
              variant_id: this.createdVid };
      } catch (e) { this.showError(e.message); }
    },
    async genBarcode() {
      const r = await API.post(`/api/variants/${this.createdVid}/barcodes`,
                               { source: "store" });
      this.newBarcode = r.barcode;
    },
    async printBarcode() {
      try {
        await API.post("/api/print/barcode",
          { barcode: this.newBarcode, name: this.form.name });
      } catch (e) { this.showError(e.message); }  // 現階段顯示 501 訊息
    },
    async doReceive() {
      try {
        const r = await API.post("/api/stock/receive",
          { variant_id: this.hit.variant_id, qty: this.qty });
        this.hit.stock = r.stock; this.qty = 1;
        this.$refs.scan.focus();
      } catch (e) { this.showError(e.message); }
    },
  },
};
```

`pos.css` 追加:
```css
.create-form { background: #fff; padding: 14px; border-radius: 8px; margin-top: 12px;
  display: flex; flex-direction: column; gap: 10px; max-width: 560px; }
.attr-row input { width: 260px; }
```

- [ ] **Step 2: 手動驗證** — 掃已知條碼入庫成功;掃未知條碼進建檔、下拉連動(選鋼化玻璃→廠牌只出對應)、手打新值問「加入選單」、建檔後入庫;無條碼建檔→產店內條碼→列印鈕顯示 501 訊息
- [ ] **Step 3: Commit** — `git commit -m "feat: 進貨與快速建檔畫面(連動下拉/選單自增/店內條碼)"`

---

### Task 11: 盤點畫面 + 銷售紀錄畫面 + 設定頁

**Files:**
- Modify: `static/js/stocktake.js`、`static/js/records.js`、`static/js/settings.js`(各整檔重寫)、`static/index.html`(加三個 template)

**Interfaces:**
- Consumes: Task 5/6 全部端點、`GET /api/fields`、`PUT /api/fields/{id}`
- 盤點頁:列近 50 張盤點單(open 可續盤);開新單;盤點中掃碼累加、手改數量;結案前顯示差異清單(差異列紅字)+ confirm;結案成功回列表
- 紀錄頁:日期起迄+付款方式篩選;交易展開明細;頂部當日小結(筆數/總額/各付款);「匯出 CSV」開 `/api/sales/export?...`
- 設定頁:欄位標題改名(對應「分類1→顏色」需求)、欄位排序、顯示版本號

- [ ] **Step 1: 實作三頁**(模板置 index.html,結構同前兩任務;以下為 js 核心)

`static/js/stocktake.js`
```javascript
window.PosPages = window.PosPages || {};
window.PosPages["page-stocktake"] = {
  template: "#tpl-stocktake",
  inject: ["showError"],
  data() {
    return { sessions: [], current: null, detail: null, scanCode: "", operator: "" };
  },
  async mounted() { await this.reload(); },
  methods: {
    attrText(a) { return Object.values(a).join(" / "); },
    async reload() { this.sessions = await API.get("/api/stocktake"); },
    async openNew() {
      const r = await API.post("/api/stocktake", { operator: this.operator || null });
      await this.enter(r.session_id);
    },
    async enter(sid) {
      this.current = sid;
      this.detail = await API.get("/api/stocktake/" + sid);
      this.$nextTick(() => this.$refs.scan?.focus());
    },
    async onScan() {
      const code = this.scanCode.trim();
      if (!code) return;
      try {
        const hit = await API.get("/api/barcode/" + encodeURIComponent(code));
        await API.post(`/api/stocktake/${this.current}/scan`,
                       { variant_id: hit.variant_id });
        this.detail = await API.get("/api/stocktake/" + this.current);
        this.scanCode = "";
      } catch (e) { this.showError(e.message); }
    },
    async setCounted(it) {
      await API.put(`/api/stocktake/${this.current}/items/${it.variant_id}`,
                    { counted_qty: it.counted_qty });
      this.detail = await API.get("/api/stocktake/" + this.current);
    },
    async close() {
      const diffs = this.detail.items.filter(i => i.diff !== 0);
      if (!confirm(`共 ${diffs.length} 項有差異,結案後將調整庫存。確定結案?`)) return;
      await API.post(`/api/stocktake/${this.current}/close`);
      this.current = null; this.detail = null;
      await this.reload();
    },
  },
};
```

`static/index.html` 盤點模板:
```html
<template id="tpl-stocktake">
<div class="page">
  <div v-if="!current">
    <div class="scan-row">
      <input v-model="operator" placeholder="盤點人(選填)">
      <button class="primary" @click="openNew">開新盤點單</button>
    </div>
    <table>
      <thead><tr><th>單號</th><th>開始時間</th><th>狀態</th><th>盤點人</th><th></th></tr></thead>
      <tbody><tr v-for="s in sessions" :key="s.session_id">
        <td>{{ s.session_id }}</td><td>{{ s.started_at }}</td>
        <td>{{ s.status === 'open' ? '進行中' : '已結案' }}</td><td>{{ s.operator }}</td>
        <td><button v-if="s.status==='open'" @click="enter(s.session_id)">續盤</button>
            <button v-else @click="enter(s.session_id)">檢視</button></td>
      </tr></tbody>
    </table>
  </div>
  <div v-else>
    <div class="scan-row">
      <input ref="scan" v-model="scanCode" @keyup.enter="onScan"
             class="scan-input" placeholder="掃描條碼(重複掃自動累加)"
             :disabled="detail.status!=='open'">
      <button @click="current=null; detail=null; reload()">返回列表</button>
      <button class="primary" v-if="detail.status==='open'" @click="close">結案</button>
    </div>
    <table>
      <thead><tr><th>商品</th><th>屬性</th><th>系統量</th><th>實盤量</th><th>差異</th></tr></thead>
      <tbody><tr v-for="it in detail.items" :key="it.variant_id">
        <td>{{ it.name }}</td><td>{{ attrText(it.attributes) }}</td>
        <td>{{ it.system_qty }}</td>
        <td><input type="number" v-model.number="it.counted_qty" min="0" class="w80"
                   :disabled="detail.status!=='open'" @change="setCounted(it)"></td>
        <td :class="{neg: it.diff !== 0}">{{ it.diff > 0 ? '+' + it.diff : it.diff }}</td>
      </tr></tbody>
    </table>
  </div>
</div>
</template>
```

`static/js/records.js`
```javascript
window.PosPages = window.PosPages || {};
window.PosPages["page-records"] = {
  template: "#tpl-records",
  data() {
    const today = new Date().toISOString().slice(0, 10);
    return { dateFrom: today, dateTo: today, payment: "", payments: [],
             sales: [], summary: null, expanded: null };
  },
  async mounted() {
    this.payments = await API.get("/api/payments");
    await this.reload();
  },
  methods: {
    attrText(a) { return Object.values(a).join(" / "); },
    async reload() {
      const q = `date_from=${this.dateFrom}&date_to=${this.dateTo}` +
                (this.payment ? `&payment=${encodeURIComponent(this.payment)}` : "");
      this.sales = await API.get("/api/sales?" + q);
      this.summary = await API.get("/api/sales/summary?date=" + this.dateFrom);
    },
    exportCsv() {
      window.open(`/api/sales/export?date_from=${this.dateFrom}&date_to=${this.dateTo}`);
    },
  },
};
```

`static/index.html` 紀錄模板:
```html
<template id="tpl-records">
<div class="page">
  <div class="scan-row">
    <label>起 <input type="date" v-model="dateFrom"></label>
    <label>迄 <input type="date" v-model="dateTo"></label>
    <select v-model="payment"><option value="">全部付款方式</option>
      <option v-for="p in payments" :key="p">{{ p }}</option></select>
    <button class="primary" @click="reload">查詢</button>
    <button @click="exportCsv">匯出 CSV</button>
  </div>
  <div v-if="summary" class="pay-panel">
    <div>{{ dateFrom }} 小結:{{ summary.count }} 筆,共 {{ summary.total }} 元</div>
    <div v-for="(amt, p) in summary.by_payment" :key="p">{{ p }}:{{ amt }} 元</div>
  </div>
  <table>
    <thead><tr><th>編號</th><th>時間</th><th>付款</th><th>整單折抵</th><th>應收</th><th></th></tr></thead>
    <tbody>
      <template v-for="s in sales" :key="s.sale_id">
        <tr>
          <td>{{ s.sale_id }}</td><td>{{ s.ts }}</td><td>{{ s.payment }}</td>
          <td>{{ s.order_discount }}</td><td>{{ s.total }}</td>
          <td><button @click="expanded = expanded===s.sale_id ? null : s.sale_id">
            {{ expanded===s.sale_id ? '收合' : '明細' }}</button></td>
        </tr>
        <tr v-if="expanded===s.sale_id"><td colspan="6">
          <div v-for="it in s.items">
            {{ it.name }}|{{ attrText(it.attributes) }}|{{ it.qty }} 件 ×
            {{ it.unit_price }} 元(折 {{ it.discount }})
          </div>
        </td></tr>
      </template>
    </tbody>
  </table>
</div>
</template>
```

`static/js/settings.js`
```javascript
window.PosPages = window.PosPages || {};
window.PosPages["page-settings"] = {
  template: "#tpl-settings",
  inject: ["showError"],
  data() { return { fields: [] }; },
  async mounted() { this.fields = await API.get("/api/fields"); },
  methods: {
    async save(f) {
      try {
        await API.put("/api/fields/" + f.field_id, { name: f.name, sort: f.sort });
      } catch (e) { this.showError(e.message); }
    },
  },
};
```

`static/index.html` 設定模板:
```html
<template id="tpl-settings">
<div class="page">
  <h3>屬性欄位(標題可自訂,如「分類1」改「顏色」)</h3>
  <table style="max-width:480px">
    <thead><tr><th>順序</th><th>欄位標題</th><th></th></tr></thead>
    <tbody><tr v-for="f in fields" :key="f.field_id">
      <td><input type="number" v-model.number="f.sort" class="w60"></td>
      <td><input v-model="f.name"></td>
      <td><button class="primary" @click="save(f)">儲存</button></td>
    </tr></tbody>
  </table>
</div>
</template>
```

- [ ] **Step 2: 手動驗證** — 開盤點單→掃碼累加→手改→結案差異紅字→庫存已調;紀錄頁篩選/明細/CSV 下載開啟;設定頁改「分類1」→「顏色」後,進貨建檔表單標題跟著變
- [ ] **Step 3: 跑全部測試** — `python -m unittest discover -s tests` → 全 OK
- [ ] **Step 4: Commit** — `git commit -m "feat: 盤點/銷售紀錄/設定畫面"`

---

### Task 12: Excel 初始資料匯入

**Files:**
- Create: `tools/import_excel.py`、`tests/test_import.py`

**Interfaces:**
- Produces: `python tools/import_excel.py <xlsm路徑> <db路徑>`;可重跑(以條碼判重,已存在跳過)
- 匯入規則:
  - 「商品資料庫」每列 → 以 `(商品種類, 廠牌)` 分組建 Product(name=`{廠牌} {商品種類}`,category=商品種類);每列一個 Variant(attributes=規格/商品描述/分類1/分類2/手機品牌/手機型號 非空欄);`商品編碼`(TL…)掛 Barcode(source='store')
  - 「選單庫」→ AttributeOption:第0欄起各命名欄依表頭對應——`商品種類`→商品種類選項;各廠牌欄(如 `HODA`)→ 規格選項、parent=(廠牌, 欄名);`鋼化玻璃` 等種類名欄→ 廠牌選項、parent=(商品種類, 欄名);`iphone型號`/`SAMSUNG型號`→ 手機型號、parent=(手機品牌, iPhone/SAMSUNG);`分類1`→分類1選項。對照表寫在腳本常數 `MENU_MAP`,匯入前列印摘要供人工確認
  - 核心解析函式 `parse_products(rows) -> list[dict]`、`parse_menus(sheet) -> list[dict]` 與 DB 寫入分離,可單測

- [ ] **Step 1: 寫失敗測試** `tests/test_import.py`

```python
import unittest
from tools.import_excel import parse_products, group_key

class TestImport(unittest.TestCase):
    ROW = {"商品編碼": "TL100000001", "商品種類": "鋼化玻璃", "廠牌": "HODA",
           "規格": "亮面滿版", "商品描述": None, "分類1": "滿版", "分類2": None,
           "手機品牌": "iPhone", "手機型號": "iPhone17pro", "庫存": 0}

    def test_group_key(self):
        self.assertEqual(group_key(self.ROW), ("鋼化玻璃", "HODA"))

    def test_parse_products_groups(self):
        r2 = dict(self.ROW, 商品編碼="TL100000002", 規格="霧面滿版")
        r3 = dict(self.ROW, 商品編碼="TL100000003", 廠牌="imos")
        out = parse_products([self.ROW, r2, r3])
        self.assertEqual(len(out), 2)                      # 兩組
        hoda = [p for p in out if p["name"] == "HODA 鋼化玻璃"][0]
        self.assertEqual(len(hoda["variants"]), 2)
        v = hoda["variants"][0]
        self.assertEqual(v["barcode"], "TL100000001")
        self.assertNotIn("商品描述", v["attributes"])       # 空欄不入 attributes
        self.assertEqual(v["attributes"]["規格"], "亮面滿版")
```

- [ ] **Step 2: 確認失敗** — `python -m unittest tests.test_import -v` → FAIL

- [ ] **Step 3: 實作** `tools/import_excel.py`

```python
"""一次性匯入:產品清單_org.xlsm → pos.db。可重跑(條碼判重)。不入常駐程式。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lib.db import get_conn, init_db

ATTR_COLS = ["規格", "商品描述", "分類1", "分類2", "手機品牌", "手機型號"]

def group_key(row):
    return (row.get("商品種類") or "未分類", row.get("廠牌") or "未知廠牌")

def parse_products(rows):
    groups = {}
    for r in rows:
        code = r.get("商品編碼")
        if not code:
            continue
        k = group_key(r)
        g = groups.setdefault(k, {"name": f"{k[1]} {k[0]}", "category": k[0],
                                  "variants": []})
        attrs = {c: str(r[c]).strip() for c in ATTR_COLS
                 if r.get(c) is not None and str(r[c]).strip() not in ("", "nan")}
        g["variants"].append({"attributes": attrs, "barcode": str(code).strip()})
    return list(groups.values())

def load_rows(xlsm_path):
    import openpyxl
    wb = openpyxl.load_workbook(xlsm_path, read_only=True, data_only=True)
    ws = wb["商品資料庫"]
    it = ws.iter_rows(values_only=True)
    headers = [str(h) if h is not None else "" for h in next(it)]
    return [dict(zip(headers, row)) for row in it if any(v is not None for v in row)]

def import_products(conn, products):
    added = skipped = 0
    for p in products:
        pid = None
        for v in p["variants"]:
            if conn.execute("SELECT 1 FROM Barcode WHERE barcode=?",
                            (v["barcode"],)).fetchone():
                skipped += 1
                continue
            if pid is None:
                pid = conn.execute(
                    "INSERT INTO Product(name,category) VALUES(?,?)",
                    (p["name"], p["category"])).lastrowid
            vid = conn.execute(
                "INSERT INTO Variant(product_id,attributes) VALUES(?,?)",
                (pid, json.dumps(v["attributes"], ensure_ascii=False))).lastrowid
            conn.execute(
                "INSERT INTO Barcode(barcode,variant_id,source) VALUES(?,?,'store')",
                (v["barcode"], vid))
            added += 1
    return added, skipped

def import_menus(conn, xlsm_path):
    """選單庫 → AttributeOption。欄名對照 MENU_MAP:(目標欄位, 父欄位, 父值);父值 None=無連動。"""
    import openpyxl
    MENU_MAP = {  # 選單庫欄標題 → (寫入哪個屬性欄位, 父欄位, 父值);未列欄位=跳過
        "商品種類": ("商品種類", None, None),
        "分類1": ("分類1", None, None),
        "手機型號": ("手機品牌", None, None),
        "iphone型號": ("手機型號", "手機品牌", "iPhone"),
        "SAMSUNG型號": ("手機型號", "手機品牌", "SAMSUNG"),
        "鋼化玻璃": ("廠牌", "商品種類", "鋼化玻璃"),
        "手機殼": ("廠牌", "商品種類", "手機殼"),
        "充電線": ("廠牌", "商品種類", "充電線"),
        "插座": ("廠牌", "商品種類", "插座"),
        "行動電源": ("廠牌", "商品種類", "行動電源"),
        "藍芽耳機": ("廠牌", "商品種類", "藍芽耳機"),
        "鏡頭貼": ("廠牌", "商品種類", "鏡頭貼"),
        "HODA": ("規格", "廠牌", "HODA"),
        "imos": ("規格", "廠牌", "imos"),
        "硬博士": ("規格", "廠牌", "Dr.TOUGH硬博士"),
        "微晶盾": ("規格", "廠牌", "COZY微晶盾"),
        "MoreSee墨舍": ("規格", "廠牌", "MoreSee墨舍"),
        "UNIQTOUGH": ("規格", "廠牌", "UNIQTOUGH"),
        "硬派6倍強化": ("規格", "廠牌", "硬派6倍強化"),
    }
    fields = {r["name"]: r["field_id"]
              for r in conn.execute("SELECT * FROM AttributeField")}
    wb = openpyxl.load_workbook(xlsm_path, read_only=True, data_only=True)
    ws = wb["選單庫"]
    rows = list(ws.iter_rows(values_only=True))
    headers = [str(h) if h is not None else "" for h in rows[0]]
    n = 0
    for col, header in enumerate(headers):
        if header not in MENU_MAP:
            continue
        target, pfield, pvalue = MENU_MAP[header]
        for r in rows[1:]:
            v = r[col]
            if v is None or not str(v).strip():
                continue
            conn.execute(
                "INSERT OR IGNORE INTO AttributeOption"
                "(field_id,value,parent_field_id,parent_value) VALUES(?,?,?,?)",
                (fields[target], str(v).strip(),
                 fields[pfield] if pfield else None, pvalue))
            n += 1
    return n

def main():
    if len(sys.argv) != 3:
        print("用法: python tools/import_excel.py <xlsm路徑> <db路徑>")
        sys.exit(1)
    xlsm, db = sys.argv[1], sys.argv[2]
    init_db(db)
    conn = get_conn(db)
    try:
        products = parse_products(load_rows(xlsm))
        added, skipped = import_products(conn, products)
        menus = import_menus(conn, xlsm)
        conn.commit()
        print(f"商品:新增 {added} 變體、跳過(已存在){skipped}")
        print(f"選單:處理 {menus} 選項")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
```

> `MENU_MAP` 依實際選單庫欄位增補——執行時若有未對應欄位,先列出讓維護者確認再補表(選單庫有 40+ 欄,首版先涵蓋主要欄,漏的欄位執行後由維護者指認)。

- [ ] **Step 4: 通過** — `python -m unittest tests.test_import -v` → OK(2 tests)
- [ ] **Step 5: 實際匯入驗證** — `python tools/import_excel.py 產品清單_org.xlsm data/pos.db` → 回報筆數(預期 575 變體);開 `python main.py` 掃 `TL100000001`(鍵盤輸入)→ 帶出商品;重跑一次 → 全部 skipped
- [ ] **Step 6: Commit** — `git add tools/ tests/test_import.py && git commit -m "feat: Excel 初始資料匯入(商品/條碼/連動選單)"`(xlsm 已在 .gitignore,不入庫)

---

### Task 13: PyInstaller 打包 + DEVELOPER.md

**Files:**
- Create: `DEVELOPER.md`、`tools/build.ps1`

**Interfaces:**
- Produces: `dist/POS.exe`(onefile,含 static);`data/` 於 exe 同層自動建立

- [ ] **Step 1: 建 build 腳本** `tools/build.ps1`

```powershell
# 全新 build:清舊產物後 onefile 打包(PowerShell 執行)
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
Remove-Item POS.spec -ErrorAction SilentlyContinue
pyinstaller --onefile --name POS --add-data "static;static" `
  --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto `
  --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.lifespan.on `
  main.py
```

並修改 `api/__init__.py` 的 static 路徑支援打包(PyInstaller 解壓至 `sys._MEIPASS`):

```python
import os, sys

def _static_dir():
    if getattr(sys, "frozen", False):
        return os.path.join(sys._MEIPASS, "static")
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
```
(`create_app` 內改用 `_static_dir()`)

- [ ] **Step 2: 打包並驗證** — `pip install pyinstaller`;PowerShell 跑 `tools/build.ps1` → `dist/POS.exe`;雙擊:瀏覽器自動開、掃碼結帳流程可跑、`data/` 建在 exe 同層
- [ ] **Step 3: 寫 `DEVELOPER.md`** — 章節:§1 架構(main→uvicorn→FastAPI→static;檔案結構表)、§2 慣例(異動流水制、金額 int、條碼混合、選單連動 parent 規則)、§3 測試(`python -m unittest discover -s tests`)、§4 打包(build.ps1、_MEIPASS 雷)、§5 版本記錄(0.1.0 首版一列)
- [ ] **Step 4: 跑全部測試** — `python -m unittest discover -s tests` → 全 OK
- [ ] **Step 5: Commit** — `git add tools/build.ps1 api/ DEVELOPER.md && git commit -m "feat: PyInstaller 打包與技術文件"`

---

## 驗收總表(全部完成後上機走一遍)

1. 雙擊 exe → 瀏覽器開收銀頁
2. 掃(鍵入)`TL100000001` → 入車 → 改價 → 現金結帳 → 找零正確
3. 進貨頁掃未知條碼 → 快速建檔(下拉連動)→ 入庫 5 件
4. 盤點頁開單 → 掃該品 3 次 → 差異 -2 紅字 → 結案 → 庫存變 3
5. 紀錄頁看到交易、日結、CSV 下載開啟
6. 設定頁「分類1」改「顏色」→ 進貨建檔表單標題同步變
7. 關程式重開 → `data/backups/` 有當日備份;資料都在
