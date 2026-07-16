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
  // 設定頁維護需含 tags 與停用選項(all=1)。
  async loadFieldsWithOptions(fields, into, opts) {
    opts = opts || {};
    const types = opts.types || ["select", "multi"];
    const q = opts.all ? "&all=1" : "";
    for (const f of (fields || []))
      if (types.includes(f.field_type))
        into[f.field_id] = await API.listOptions({field_id: f.field_id, model_ids: modelIds || []});
  },
  // 手打自增:select 欄輸入不存在的值,存檔時自動入庫(冪等,失敗忽略)。
  async ensureOptions(fields, attrs, optMap) {
    for (const f of (fields || [])) {
      if (f.field_type !== "select") continue;
      const v = (attrs[f.name] || "").trim();
      const list = optMap[f.field_id] || [];
      if (!v || list.some(o => o.value === v)) continue;
      try {
        await API.createOption({ field_id: f.field_id, value: v });
        optMap[f.field_id] = await API.listOptions({field_id: f.field_id});
      } catch (e) { /* 冪等 */ }
    }
  },
};
