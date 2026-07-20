"""子產品待處理異常(VariantIssue)服務層(階段 6)。

處理既有子產品的重新驗證、啟用前完整驗證,以及待處理彙總查詢。
支援的問題種類(規格 §11.2 / 商品設定核心規格 §8.2):
  missing_required   - 缺少必填模板欄位值(field_id 指出欄位)
  duplicate_signature- 與同大產品的另一子產品規格簽章重複(related_variant_id 對照)
  duplicate_barcode  - 來源條碼與既有條碼衝突,暫存於 source_value

三種問題任一存在即視為未解決;全部排除後方可手動啟用。
容錯建立(供未來匯入)由 VariantBatchService.tolerant_create 寫入問題筆,
本服務負責之後的重驗與啟用。
"""

from lib.application_errors import NotFoundError, ValidationError
from lib.normalize import normalize_key
from lib.product_data import FEATURE_FIELD_KEY, variant_signature


class VariantIssueService:
    def __init__(self, connection):
        self.conn = connection

    # ---- 前置查詢 ----

    def _context(self, variant_id):
        row = self.conn.execute(
            "SELECT v.variant_id, v.product_id, v.active, p.category_id "
            "FROM Variant v JOIN Product p ON v.product_id=p.product_id "
            "WHERE v.variant_id=?", (variant_id,)).fetchone()
        if row is None:
            raise NotFoundError("找不到子產品")
        return row

    def _feature_id(self):
        for row in self.conn.execute(
                "SELECT field_id, name FROM AttributeField WHERE active=1 ORDER BY field_id"):
            if normalize_key(row["name"]) == FEATURE_FIELD_KEY:
                return row["field_id"]
        return None

    def _required_fields(self, category_id):
        """該種類啟用中的必填模板欄位:{field_id: name}。"""
        return {r["field_id"]: r["name"] for r in self.conn.execute(
            "SELECT f.field_id, f.name FROM CategoryField cf "
            "JOIN AttributeField f ON f.field_id=cf.field_id "
            "WHERE cf.category_id=? AND cf.active=1 AND f.active=1 AND cf.required=1",
            (category_id,))}

    def _provided_fields(self, variant_id):
        """該子產品已有值的 field_id 集合(option 或非空 text)。"""
        out = set()
        for r in self.conn.execute(
                "SELECT field_id, option_id, text_value FROM VariantAttribute "
                "WHERE variant_id=?", (variant_id,)):
            if r["option_id"] is not None or (r["text_value"] or "").strip():
                out.add(r["field_id"])
        return out

    def _signature(self, variant_id, feature_id):
        """持久化簽章:正式規格(含停用值,排除特性詞條)＋適用型號。"""
        return variant_signature(self.conn, variant_id, feature_id)

    def _persisted_dup_barcodes(self, variant_id):
        """既有 duplicate_barcode 問題的來源值清單。"""
        return [r["source_value"] for r in self.conn.execute(
            "SELECT source_value FROM VariantIssue "
            "WHERE variant_id=? AND issue_type='duplicate_barcode'", (variant_id,))
            if r["source_value"]]

    # ---- 計算目前應有的問題集合 ----

    def compute(self, variant_id):
        """依目前持久化狀態計算應有問題集合。回傳:
        {issues: [issue_dict,...], resolvable_barcodes: [code,...]}
        resolvable_barcodes 為原重複、現已無衝突可寫入正式欄位的條碼。"""
        ctx = self._context(variant_id)
        feature_id = self._feature_id()
        issues = []
        # 1. 缺必填
        provided = self._provided_fields(variant_id)
        for fid, name in sorted(self._required_fields(ctx["category_id"]).items()):
            if fid not in provided:
                issues.append({"issue_type": "missing_required", "field_id": fid,
                               "source_value": None, "related_variant_id": None})
        # 2. 規格簽章重複(對同大產品其他子產品)
        sig = self._signature(variant_id, feature_id)
        related = None
        for r in self.conn.execute(
                "SELECT variant_id FROM Variant WHERE product_id=? AND variant_id<>? "
                "ORDER BY variant_id", (ctx["product_id"], variant_id)):
            if self._signature(r["variant_id"], feature_id) == sig:
                related = r["variant_id"]
                break
        if related is not None:
            issues.append({"issue_type": "duplicate_signature", "field_id": None,
                           "source_value": None, "related_variant_id": related})
        # 3. 重複條碼:既有問題若來源值仍被他人佔用則保留,否則可寫入
        resolvable = []
        for code in self._persisted_dup_barcodes(variant_id):
            owner = self.conn.execute(
                "SELECT variant_id FROM Barcode WHERE barcode=?", (code,)).fetchone()
            if owner is not None and owner["variant_id"] != variant_id:
                issues.append({"issue_type": "duplicate_barcode", "field_id": None,
                               "source_value": code, "related_variant_id": owner["variant_id"]})
            else:
                resolvable.append(code)
        return {"issues": issues, "resolvable_barcodes": resolvable}

    # ---- 重新驗證(修改後) ----

    def revalidate(self, variant_id):
        """重算問題:已解決者刪除、仍存在者更新;可寫入的重複條碼補寫正式欄位。
        不自動啟用。回傳 {variant_id, issues, can_activate}。"""
        result = self.compute(variant_id)
        for code in result["resolvable_barcodes"]:
            # 條碼衝突已消失 → 寫入正式欄位(重複條碼恆為原廠來源)
            if self.conn.execute("SELECT 1 FROM Barcode WHERE barcode=?", (code,)).fetchone() is None:
                self.conn.execute(
                    "INSERT INTO Barcode(barcode,variant_id,source) VALUES(?,?,'factory')",
                    (code, variant_id))
        self.conn.execute("DELETE FROM VariantIssue WHERE variant_id=?", (variant_id,))
        for it in result["issues"]:
            self.conn.execute(
                "INSERT INTO VariantIssue(variant_id,issue_type,field_id,source_value,related_variant_id) "
                "VALUES(?,?,?,?,?)",
                (variant_id, it["issue_type"], it["field_id"], it["source_value"],
                 it["related_variant_id"]))
        return {"variant_id": variant_id, "issues": self._issue_rows(variant_id),
                "can_activate": not result["issues"]}

    def activate(self, variant_id):
        """手動啟用:先跑完整重驗,仍有問題則拒絕;否則清問題並啟用。"""
        state = self.revalidate(variant_id)
        if state["issues"]:
            raise ValidationError("子產品仍有待處理問題,無法啟用", details=state["issues"])
        self.conn.execute("UPDATE Variant SET active=1 WHERE variant_id=?", (variant_id,))
        return {"variant_id": variant_id, "active": 1}

    def _issue_rows(self, variant_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT issue_type, field_id, source_value, related_variant_id "
            "FROM VariantIssue WHERE variant_id=? ORDER BY issue_id", (variant_id,))]

    # ---- 待處理彙總查詢 ----

    def summary(self):
        """待處理彙總:總筆數(以子產品計)、依問題種類、依種類。"""
        by_type = {r["issue_type"]: r["c"] for r in self.conn.execute(
            "SELECT issue_type, COUNT(DISTINCT variant_id) c FROM VariantIssue GROUP BY issue_type")}
        by_category = {r["category_id"]: r["c"] for r in self.conn.execute(
            "SELECT p.category_id, COUNT(DISTINCT vi.variant_id) c FROM VariantIssue vi "
            "JOIN Variant v ON vi.variant_id=v.variant_id "
            "JOIN Product p ON v.product_id=p.product_id GROUP BY p.category_id")}
        total = self.conn.execute(
            "SELECT COUNT(DISTINCT variant_id) c FROM VariantIssue").fetchone()["c"]
        return {"pending_variant_count": total, "by_type": by_type,
                "by_category": by_category}
