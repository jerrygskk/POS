"""子產品批次建立(階段 5)。

單一 transaction 內:重新驗證全部 draft → 必填／model_mode／條碼檢查 →
C 規則重複判定(批內互查＋對 DB)→ 建新選項／重新啟用停用選項 →
建 Variant/VariantAttribute/VariantModel/Barcode → 需要時配自取碼。
任一 draft 有誤即整批 raise;交易由呼叫端 rollback(含自取碼計數器與選項回復)。
"""

from lib.application_errors import NotFoundError, ValidationError
from lib.db import next_sort
from lib.normalize import normalize_display, normalize_key
from lib.product_data import FEATURE_FIELD_KEY
from lib.product_rules import next_store_barcode


class VariantBatchService:
    def __init__(self, connection):
        self.conn = connection

    # ---- 前置查詢 ----

    def _require_product(self, product_id):
        row = self.conn.execute(
            "SELECT p.category_id, p.active p_active, c.active c_active "
            "FROM Product p LEFT JOIN Category c ON p.category_id=c.category_id "
            "WHERE p.product_id=?", (product_id,)).fetchone()
        if row is None:
            raise NotFoundError("找不到商品")
        if not row["p_active"]:
            raise ValidationError("大產品已停用,不可新增子產品")
        if row["category_id"] is None or not row["c_active"]:
            raise ValidationError("商品種類不存在或已停用")
        return row["category_id"]

    def _writable_fields(self, category_id):
        """該種類可輸入的模板欄位(cf.active=1 且 f.active=1)。回 {正規化欄名: row}。"""
        out = {}
        for r in self.conn.execute(
                "SELECT f.field_id, f.name, f.field_type, cf.required "
                "FROM CategoryField cf JOIN AttributeField f ON f.field_id=cf.field_id "
                "WHERE cf.category_id=? AND cf.active=1 AND f.active=1",
                (category_id,)):
            out[normalize_key(r["name"])] = r
        return out

    def _feature_field_id(self):
        for row in self.conn.execute(
                "SELECT field_id, name FROM AttributeField WHERE active=1 ORDER BY field_id"):
            if normalize_key(row["name"]) == FEATURE_FIELD_KEY:
                return row["field_id"]
        return None

    def _options_of(self, field_id):
        """欄位選項快照(供 normalize_key 比對重用/新建/重新啟用)。"""
        return self.conn.execute(
            "SELECT option_id, value, active FROM AttributeOption WHERE field_id=?",
            (field_id,)).fetchall()

    # ---- 選項解析(新建 / 重新啟用停用同名) ----

    def _resolve_option(self, field_id, value, created, reactivated):
        key = normalize_key(value)
        for o in self._options_of(field_id):
            if normalize_key(o["value"]) == key:
                if not o["active"]:
                    self.conn.execute(
                        "UPDATE AttributeOption SET active=1 WHERE option_id=?",
                        (o["option_id"],))
                    reactivated.add(o["option_id"])
                return o["option_id"]
        sort = next_sort(self.conn, "AttributeOption", "field_id=?", (field_id,))
        cur = self.conn.execute(
            "INSERT INTO AttributeOption(field_id,value,sort) VALUES(?,?,?)",
            (field_id, normalize_display(value), sort))
        created.add(cur.lastrowid)
        return cur.lastrowid

    # ---- 單筆 draft 解析 ----

    def _resolve_draft(self, draft, category_id, writable, feature_id,
                       model_mode, created, reactivated):
        errors = []
        attrs_out = []          # [(field_id, "option"/"text", value)]
        sig = set()             # 正式規格簽章元素(不含特性詞條)
        provided = set()        # 已填欄位 field_id(必填檢查用)
        attributes = draft.get("attributes") or {}
        for name, raw in attributes.items():
            key = normalize_key(name)
            if feature_id is not None and key == FEATURE_FIELD_KEY:
                fid, ftype, is_feature = feature_id, "tags", True
            elif key in writable:
                f = writable[key]
                fid, ftype, is_feature = f["field_id"], f["field_type"], False
            else:
                errors.append(f"規格欄「{name}」不存在或未套用於此種類")
                continue
            if ftype == "text":
                text = normalize_display(str(raw)) if raw is not None else ""
                if not text:
                    continue
                attrs_out.append((fid, "text", text))
                provided.add(fid)
                if not is_feature:
                    sig.add((fid, "t", text))
                continue
            values = raw if isinstance(raw, (list, tuple)) else [raw]
            values = [normalize_display(str(v)) for v in values if str(v).strip()]
            values = list(dict.fromkeys(values))
            if not values:
                continue
            if ftype == "select" and len(values) != 1:
                errors.append(f"規格欄「{name}」僅能選一個值")
                continue
            provided.add(fid)
            for v in values:
                oid = self._resolve_option(fid, v, created, reactivated)
                attrs_out.append((fid, "option", oid))
                if not is_feature:
                    sig.add((fid, "o", oid))
        # 必填檢查(cf.required=1 的可輸入欄)
        for f in writable.values():
            if f["required"] and f["field_id"] not in provided:
                errors.append(f"必填規格「{f['name']}」未填")
        # 適用型號
        model_ids = list(dict.fromkeys(draft.get("model_ids") or []))
        for mid in model_ids:
            if self.conn.execute("SELECT 1 FROM PhoneModel WHERE model_id=?",
                                 (mid,)).fetchone() is None:
                errors.append(f"型號(id={mid})不存在")
        if model_mode == "required" and not model_ids:
            errors.append("此種類須指定適用型號")
        for mid in model_ids:
            sig.add(("m", mid))
        # 條碼
        barcodes = []
        for bc in draft.get("barcodes") or []:
            code = bc.get("barcode")
            code = code.strip() if isinstance(code, str) else code
            source = bc.get("source") or ("factory" if code else "store")
            if code and code.upper().startswith("TL"):
                errors.append("TL 開頭條碼僅供系統自動產生")
                continue
            barcodes.append({"barcode": code or None, "source": source})
        price = draft.get("price")
        active = 1 if draft.get("active", 1) else 0
        return {
            "draft_id": draft.get("draft_id"),
            "attrs": attrs_out, "model_ids": model_ids, "barcodes": barcodes,
            "price": price, "active": active,
            "signature": frozenset(sig), "errors": errors,
        }

    # ---- 對 DB 既有子產品簽章 ----

    def _existing_signatures(self, product_id, feature_id):
        """回傳 {簽章: variant_id}。含停用欄位值(正式規格皆納入,特性詞條除外)。"""
        vids = [r["variant_id"] for r in self.conn.execute(
            "SELECT variant_id FROM Variant WHERE product_id=?", (product_id,))]
        out = {}
        for vid in vids:
            sig = set()
            for r in self.conn.execute(
                    "SELECT field_id, option_id, text_value FROM VariantAttribute "
                    "WHERE variant_id=?", (vid,)):
                if feature_id is not None and r["field_id"] == feature_id:
                    continue
                if r["option_id"] is not None:
                    sig.add((r["field_id"], "o", r["option_id"]))
                else:
                    sig.add((r["field_id"], "t", r["text_value"]))
            for r in self.conn.execute(
                    "SELECT model_id FROM VariantModel WHERE variant_id=?", (vid,)):
                sig.add(("m", r["model_id"]))
            out[frozenset(sig)] = vid
        return out

    # ---- 主流程 ----

    def batch_create(self, payload):
        product_id = payload["product_id"]
        drafts = payload["drafts"]
        if not drafts:
            raise ValidationError("尚未加入任何子產品")
        category_id = self._require_product(product_id)
        writable = self._writable_fields(category_id)
        feature_id = self._feature_field_id()
        model_mode = self.conn.execute(
            "SELECT model_mode FROM Category WHERE category_id=?",
            (category_id,)).fetchone()["model_mode"]

        created, reactivated = set(), set()
        resolved = [self._resolve_draft(d, category_id, writable, feature_id,
                                        model_mode, created, reactivated)
                    for d in drafts]

        # C 規則重複判定:批內互查
        first_seen = {}
        for idx, r in enumerate(resolved):
            sig = r["signature"]
            if sig in first_seen:
                r["errors"].append(f"與第 {first_seen[sig] + 1} 筆子產品規格重複")
            else:
                first_seen[sig] = idx
        # C 規則:對 DB 既有子產品
        existing = self._existing_signatures(product_id, feature_id)
        for r in resolved:
            if r["signature"] in existing:
                r["errors"].append("與既有子產品規格重複")

        # 條碼重複:批內互查 + 對 DB
        seen_codes = {}
        for idx, r in enumerate(resolved):
            for bc in r["barcodes"]:
                code = bc["barcode"]
                if not code:
                    continue
                if code in seen_codes and seen_codes[code] != idx:
                    r["errors"].append(f"條碼「{code}」與第 {seen_codes[code] + 1} 筆重複")
                elif self.conn.execute(
                        "SELECT 1 FROM Barcode WHERE barcode=?", (code,)).fetchone():
                    r["errors"].append(f"條碼「{code}」已存在")
                seen_codes.setdefault(code, idx)

        errors = [{"index": i, "draft_id": r["draft_id"], "errors": r["errors"]}
                  for i, r in enumerate(resolved) if r["errors"]]
        if errors:
            raise ValidationError("部分子產品資料有誤,請修正後再送出", details=errors)

        # 全數通過:寫入
        results = []
        for r in resolved:
            cur = self.conn.execute(
                "INSERT INTO Variant(product_id,price,active) VALUES(?,?,?)",
                (product_id, r["price"], r["active"]))
            vid = cur.lastrowid
            for fid, kind, val in r["attrs"]:
                if kind == "option":
                    self.conn.execute(
                        "INSERT INTO VariantAttribute(variant_id,field_id,option_id) "
                        "VALUES(?,?,?)", (vid, fid, val))
                else:
                    self.conn.execute(
                        "INSERT INTO VariantAttribute(variant_id,field_id,text_value) "
                        "VALUES(?,?,?)", (vid, fid, val))
            for mid in r["model_ids"]:
                self.conn.execute(
                    "INSERT OR IGNORE INTO VariantModel(variant_id,model_id) VALUES(?,?)",
                    (vid, mid))
            codes = []
            for bc in r["barcodes"]:
                code = bc["barcode"] or next_store_barcode(self.conn)
                self.conn.execute(
                    "INSERT INTO Barcode(barcode,variant_id,source) VALUES(?,?,?)",
                    (code, vid, bc["source"]))
                codes.append({"barcode": code, "source": bc["source"]})
            results.append({"draft_id": r["draft_id"], "variant_id": vid,
                            "barcodes": codes})

        return {"product_id": product_id, "results": results,
                "created_option_ids": sorted(created),
                "reactivated_option_ids": sorted(reactivated)}
