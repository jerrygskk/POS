// 規格欄載入／過濾／自增選項共用工具(收銀無用;進貨、資料庫、設定共用)。
window.CatalogFields = {
  // 依變體「適用型號」過濾 select/multi 選項:未綁型號者恆顯示;綁定含任一適用型號者顯示;
  // 未選型號=顯示全部。
  filterOptions(list, modelIds) {
    list = list || [];
    if (!modelIds || !modelIds.length) return list;
    return list.filter(o =>
      !o.model_ids.length || o.model_ids.some(id => modelIds.includes(id)));
  },
  // 逐欄撈選項存入 into(field_id → 選項清單)。預設只撈 select/multi;
  // 設定頁維護需含 tags 與停用選項(all=1)。型號過濾於渲染時由 filterOptions 處理,
  // 此處撈全部選項不帶 model_ids。
  async loadFieldsWithOptions(fields, into, opts) {
    opts = opts || {};
    const types = opts.types || ["select", "multi"];
    for (const f of (fields || []))
      if (types.includes(f.field_type))
        into[f.field_id] = await API.listOptions(
          Object.assign({ field_id: f.field_id }, opts.all ? { all: 1 } : {}));
  },
  // 逐欄撈「該種類使用次數排序」候選存入 into(field_id → usage 清單)。
  // 子產品建檔/修改的 select/multi 候選改採此模式(與詞條選取器一致)。
  async loadFieldUsage(categoryId, fields, into, types) {
    types = types || ["select", "multi"];
    for (const f of (fields || []))
      if (types.includes(f.field_type))
        into[f.field_id] = await API.fieldUsage(categoryId, f.field_id);
  },
  // 手打自增:select/multi 欄輸入不存在的值,存檔前自動入庫(冪等,失敗忽略)。
  // multi 值為陣列、select 為字串;逐值嘗試建立。
  async ensureOptions(fields, attrs, optMap) {
    for (const f of (fields || [])) {
      if (f.field_type !== "select" && f.field_type !== "multi") continue;
      const raw = attrs[f.name];
      const vals = (Array.isArray(raw) ? raw : [raw])
        .map(x => String(x == null ? "" : x).trim()).filter(Boolean);
      if (!vals.length) continue;
      const list = optMap[f.field_id] || [];
      let changed = false;
      for (const v of vals) {
        if (list.some(o => o.value === v)) continue;
        try { await API.createOption({ field_id: f.field_id, value: v }); changed = true; }
        catch (e) { /* 冪等 */ }
      }
      if (changed) optMap[f.field_id] = await API.listOptions({ field_id: f.field_id });
    }
  },
};
