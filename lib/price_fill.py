"""補售價子視窗的資料來源:列出啟用中子產品、批次寫回售價。

原為 docs/ 下的 tkinter 小工具,併入主程式後改走 ProductFacade(管理者限定),
寫入沿用 ProductService.update_variant,待處理筆的重驗與一般改價一致。
"""
from lib.application_errors import ValidationError

PRICE_ROWS_SQL = """
SELECT v.variant_id, IFNULL(c.name, '') AS category, IFNULL(b.name, '') AS brand,
  p.name, v.price,
  IFNULL((SELECT group_concat(val, ' / ') FROM (
    SELECT IFNULL(o.value, va.text_value) AS val
    FROM VariantAttribute va
    LEFT JOIN AttributeOption o ON o.option_id = va.option_id
    LEFT JOIN CategoryField cf ON cf.category_id = p.category_id AND cf.field_id = va.field_id
    WHERE va.variant_id = v.variant_id
    ORDER BY IFNULL(cf.sort, 9999), va.field_id, IFNULL(o.sort, 0))), '') AS spec,
  IFNULL((SELECT group_concat(IFNULL(NULLIF(m.alias, ''), m.name), ', ')
    FROM VariantModel vm JOIN PhoneModel m ON m.model_id = vm.model_id
    WHERE vm.variant_id = v.variant_id), '') AS models,
  IFNULL((SELECT group_concat(barcode, ', ') FROM Barcode bc
    WHERE bc.variant_id = v.variant_id), '') AS barcode
FROM Variant v
JOIN Product p ON p.product_id = v.product_id
LEFT JOIN Category c ON c.category_id = p.category_id
LEFT JOIN Brand b ON b.brand_id = p.brand_id
WHERE v.active = 1 AND p.active = 1
ORDER BY c.sort, c.name, b.sort, b.name, p.name, v.variant_id
"""


def list_price_rows(connection):
    return [dict(row) for row in connection.execute(PRICE_ROWS_SQL)]


def validate_changes(changes):
    """changes = [{variant_id, price}];售價須為大於 0 的整數,同一子產品不得重複。"""
    if not isinstance(changes, list) or not changes:
        raise ValidationError("沒有需要儲存的售價")
    seen = set()
    for item in changes:
        if not isinstance(item, dict) or set(item) != {"variant_id", "price"}:
            raise ValidationError("售價資料格式不正確")
        vid, price = item["variant_id"], item["price"]
        if type(vid) is not int or type(price) is not int:
            raise ValidationError("售價資料格式不正確")
        if price <= 0:
            raise ValidationError("售價必須是大於 0 的整數")
        if vid in seen:
            raise ValidationError("同一子產品重複填價")
        seen.add(vid)


def save_prices(service, connection, changes):
    """整批在同一交易內寫入;任何一筆不合格整批不寫(交易由 BaseFacade 回滾)。"""
    vids = [item["variant_id"] for item in changes]
    qs = ",".join("?" * len(vids))
    active = {row[0] for row in connection.execute(
        f"SELECT v.variant_id FROM Variant v JOIN Product p ON p.product_id = v.product_id "
        f"WHERE v.active = 1 AND p.active = 1 AND v.variant_id IN ({qs})", vids)}
    if len(active) != len(vids):
        raise ValidationError("部分子產品已停用或不存在，請重新載入後再儲存")
    for item in changes:
        service.update_variant(item["variant_id"], {"price": item["price"]})
    return {"saved": len(changes)}
