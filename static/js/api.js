const API = {
  _ready: null,
  _bridge() {
    if (window.pywebview && window.pywebview.api) return Promise.resolve(window.pywebview.api);
    if (this._ready) return this._ready;
    this._ready = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("桌面服務啟動逾時，請重新開啟程式")), 10000);
      const ready = () => {
        clearTimeout(timer);
        if (window.pywebview && window.pywebview.api) resolve(window.pywebview.api);
        else reject(new Error("桌面服務無法使用"));
      };
      window.addEventListener("pywebviewready", ready, { once: true });
    });
    return this._ready;
  },
  async invoke(action, payload) {
    const allowed = new Set([
      "categories.list", "categories.create", "categories.update", "categories.delete", "categories.sort", "categories.fields", "categories.set_common_fields", "categories.set_field",
      "brands.list", "brands.create", "brands.update", "brands.delete", "brands.sort", "brands.set_categories",
      "phone_brands.list", "phone_brands.create", "phone_brands.update", "phone_brands.delete", "phone_brands.sort",
      "models.list", "models.create", "models.update", "models.delete", "models.sort",
      "fields.list", "fields.create", "fields.update", "fields.delete",
      "options.list", "options.create", "options.update", "options.delete", "options.models", "options.set_models",
      "products.create", "products.list", "products.update", "products.delete", "catalog.list",
      "variants.create", "variants.update", "variants.set_models", "variants.update_details", "variants.delete",
      "barcodes.scan", "barcodes.add", "barcodes.delete", "stock.receive", "stock.detail",
      "stocktake.create", "stocktake.list", "stocktake.detail", "stocktake.scan", "stocktake.set_counted", "stocktake.close",
      "payments.list", "sales.checkout", "sales.list", "sales.summary", "sales.export_save", "printing.barcode"
    ]);
    if (!allowed.has(action)) throw new Error("不支援的桌面操作");
    const api = await this._bridge();
    const result = await api.invoke(action, payload || {});
    if (result && result.ok) return result.data;
    const info = (result && result.error) || {};
    const err = new Error(info.message || "操作失敗");
    const statuses = { validation_error: 422, not_found: 404, conflict: 409,
      database_error: 500, internal_error: 500 };
    err.status = statuses[info.code] || 500;
    err.code = info.code; err.details = info.details;
    throw err;
  },
  listCategories: p => API.invoke("categories.list", p), createCategory: p => API.invoke("categories.create", p),
  updateCategory: (id, fields) => API.invoke("categories.update", {id, fields}), deleteCategory: id => API.invoke("categories.delete", {id}),
  sortCategories: ids => API.invoke("categories.sort", {ids}), categoryFields: id => API.invoke("categories.fields", {id}),
  setCategoryCommonFields: (id, field_ids) => API.invoke("categories.set_common_fields", {id, field_ids}),
  setCategoryField: (category_id, field_id, fields) => API.invoke("categories.set_field", {category_id, field_id, fields}),
  listBrands: p => API.invoke("brands.list", p), createBrand: p => API.invoke("brands.create", p), updateBrand: (id, fields) => API.invoke("brands.update", {id, fields}), deleteBrand: id => API.invoke("brands.delete", {id}), sortBrands: ids => API.invoke("brands.sort", {ids}), setBrandCategories: (id, category_ids) => API.invoke("brands.set_categories", {id, category_ids}),
  listPhoneBrands: p => API.invoke("phone_brands.list", p), createPhoneBrand: p => API.invoke("phone_brands.create", p), updatePhoneBrand: (id, fields) => API.invoke("phone_brands.update", {id, fields}), deletePhoneBrand: id => API.invoke("phone_brands.delete", {id}), sortPhoneBrands: ids => API.invoke("phone_brands.sort", {ids}),
  listModels: p => API.invoke("models.list", p), createModel: p => API.invoke("models.create", p), updateModel: (id, fields) => API.invoke("models.update", {id, fields}), deleteModel: id => API.invoke("models.delete", {id}), sortModels: ids => API.invoke("models.sort", {ids}),
  listFields: p => API.invoke("fields.list", p), createField: p => API.invoke("fields.create", p), updateField: (id, fields) => API.invoke("fields.update", {id, fields}), deleteField: id => API.invoke("fields.delete", {id}),
  listOptions: p => API.invoke("options.list", p), createOption: p => API.invoke("options.create", p), updateOption: (id, fields) => API.invoke("options.update", {id, fields}), deleteOption: id => API.invoke("options.delete", {id}),
  createProduct: p => API.invoke("products.create", p), listProducts: p => API.invoke("products.list", p), listCatalog: p => API.invoke("catalog.list", p), updateProduct: (id, fields) => API.invoke("products.update", {id, fields}), deleteProduct: id => API.invoke("products.delete", {id}),
  createVariant: (product_id, fields) => API.invoke("variants.create", {product_id, fields}), updateVariant: (id, fields) => API.invoke("variants.update", {id, fields}), deleteVariant: id => API.invoke("variants.delete", {id}),
  addBarcode: p => API.invoke("barcodes.add", p), deleteBarcode: code => API.invoke("barcodes.delete", {code}), scanBarcode: code => API.invoke("barcodes.scan", {code}),
  receiveStock: p => API.invoke("stock.receive", p), stockDetail: variant_id => API.invoke("stock.detail", {variant_id}),
  createStocktake: p => API.invoke("stocktake.create", p), listStocktakes: () => API.invoke("stocktake.list", {}), stocktakeDetail: session_id => API.invoke("stocktake.detail", {session_id}), stocktakeScan: p => API.invoke("stocktake.scan", p), setStocktakeCounted: p => API.invoke("stocktake.set_counted", p), closeStocktake: session_id => API.invoke("stocktake.close", {session_id}),
  listPayments: () => API.invoke("payments.list", {}), checkout: p => API.invoke("sales.checkout", p), listSales: p => API.invoke("sales.list", p), salesSummary: p => API.invoke("sales.summary", p),
  async barcodeQuery(value) {
    const code = String(value || "").trim();
    if (!code) return null;
    const data = await this.scanBarcode(code);
    return { code, data };
  },
  updateVariantDetails(id, fields, modelIds) {
    return this.invoke("variants.update_details", {
      id, fields, model_ids: modelIds
    });
  },
  exportSales(payload) { return this.invoke("sales.export_save", payload); },
  printBarcode(variant_id) { return this.invoke("printing.barcode", {variant_id}); },
};

window.parseTagList = function (v) {
  return String(v || "").split(/[,、，]/).map(s => s.trim()).filter(Boolean);
};

// ---- 規格顯示與表單共用工具(multi/tags 皆為清單)----

// 規格顯示字串:優先用後端組好的 attr_display(遵守 spec §2 順位);
// 退而求其次以本地屬性 dict 兜底(陣列以「+」連、各欄以「｜」分隔)。
window.fmtAttr = function (row) {
  if (row && typeof row.attr_display === "string" && row.attr_display)
    return row.attr_display;
  const a = (row && row.attributes) || row || {};
  const parts = [];
  for (const v of Object.values(a)) {
    if (Array.isArray(v)) { if (v.length) parts.push(v.join("+")); }
    else if (v != null && v !== "") parts.push(v);
  }
  return parts.join("｜");
};

// 依欄型初始化表單屬性值:multi=陣列、tags=逗號字串、select 有預設即帶入。
// existing 為既有變體屬性(編輯時傳入),既有值優先於預設。
window.initFormAttrs = function (fields, existing) {
  existing = existing || {};
  const a = {};
  for (const f of (fields || [])) {
    const cur = existing[f.name];
    if (f.field_type === "multi")
      a[f.name] = Array.isArray(cur) ? cur.slice() : [];
    else if (f.field_type === "tags")
      a[f.name] = Array.isArray(cur) ? cur.join(", ") : (cur || "");
    else if (cur != null && cur !== "")
      a[f.name] = cur;
    else if (f.field_type === "select" && f.default_value)
      a[f.name] = f.default_value;
    else
      a[f.name] = "";
  }
  return a;
};

// 表單屬性 → API 送出格式:multi/tags 送清單、select/text 送字串;空值略過。
window.buildAttrPayload = function (fields, attrs) {
  const out = {};
  for (const f of (fields || [])) {
    const v = attrs[f.name];
    if (f.field_type === "multi") {
      const arr = (Array.isArray(v) ? v : []).map(x => String(x).trim())
        .filter(Boolean);
      if (arr.length) out[f.name] = arr;
    } else if (f.field_type === "tags") {
      const arr = window.parseTagList(v);
      if (arr.length) out[f.name] = arr;
    } else {
      const s = (v == null ? "" : String(v)).trim();
      if (s) out[f.name] = s;
    }
  }
  return out;
};
