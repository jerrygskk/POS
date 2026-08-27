// 規格欄候選載入／型號過濾共用工具(款式建檔與修改共用)。
window.CatalogFields = {
  // 依變體「適用型號」過濾 select/multi 選項:未綁型號者恆顯示;綁定含任一適用型號者顯示;
  // 未選型號=顯示全部。
  filterOptions(list, modelIds) {
    list = list || [];
    if (!modelIds || !modelIds.length) return list;
    return list.filter(o =>
      !o.model_ids.length || o.model_ids.some(id => modelIds.includes(id)));
  },
  // 逐欄撈「該種類使用次數排序」候選存入 into(field_id → usage 清單)。
  // 子產品建檔/修改的 select/multi 候選改採此模式(與詞條選取器一致)。
  // scope 可帶 { brand_id, product_id } 決定前排範圍(廠牌→產品→無),
  // 由服務層算出每列的 lead／lead_count;不帶則沿用種類次數。
  async loadFieldUsage(categoryId, fields, into, types, scope) {
    types = types || ["select", "multi"];
    for (const f of (fields || []))
      if (types.includes(f.field_type))
        into[f.field_id] = await API.fieldUsage(categoryId, f.field_id, scope);
  },
  // 由產品推出前排範圍:兩個都送,服務層依「廠牌→產品→無」退路決定。
  // 沒廠牌(如無品牌皮套)或該廠牌尚無紀錄時,前排就用這個產品自己用過的值。
  usageScope(product) {
    if (!product) return null;
    return { brand_id: product.brand_id != null ? product.brand_id : null,
             product_id: product.product_id };
  },
};
