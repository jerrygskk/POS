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
      "auth.status", "auth.login", "auth.logout", "auth.touch", "auth.change_password",
      "categories.list", "categories.create", "categories.update", "categories.delete", "categories.sort", "categories.fields", "categories.set_common_fields", "categories.set_field", "categories.delete_field",
      "brands.list", "brands.create", "brands.update", "brands.delete", "brands.sort", "brands.set_categories",
      "phone_brands.list", "phone_brands.create", "phone_brands.update", "phone_brands.delete", "phone_brands.sort",
      "device_types.list", "device_types.create", "device_types.update", "device_types.delete", "device_types.sort",
      "models.list", "models.create", "models.update", "models.delete", "models.sort",
      "fields.list", "fields.create", "fields.update",
      "options.list", "options.create", "options.update", "options.delete", "options.models", "options.set_models", "options.cleanup",
      "products.create", "products.list", "products.update", "products.delete", "catalog.list",
      "variants.create", "variants.update", "variants.set_models", "variants.update_details", "variants.update_editor", "variants.delete",
      "variants.batch_create", "variants.batch_precheck", "variants.field_usage", "variants.model_usage", "variants.activate", "variants.issues", "variants.price_list", "variants.price_save",
      "barcodes.scan", "stock.receive", "stock.detail", "stock.receipts",
      "stocktake.create", "stocktake.list", "stocktake.history", "stocktake.detail", "stocktake.scan", "stocktake.set_counted", "stocktake.remove_item", "stocktake.close", "stocktake.withdraw", "stocktake.return", "stocktake.approve", "stocktake.pending_count", "stocktake.set_note", "stocktake.cancel",
      "returns.create", "returns.list", "returns.history", "returns.detail", "returns.scan", "returns.set_qty", "returns.set_sale", "returns.set_reason", "returns.remove_item", "returns.close", "returns.withdraw", "returns.set_disposition", "returns.approve", "returns.cancel", "returns.pending_count", "returns.repair_receive", "returns.close_item", "returns.reopen_item",
      "payments.list", "sales.checkout", "sales.list", "sales.recent", "sales.summary", "sales.export_save", "printing.barcode", "printing.preview", "printing.check",
      "members.lookup", "members.list", "members.create", "members.update", "members.phone_owners", "members.delete",
      "desktop.child_window.open", "desktop.child_window.context", "desktop.child_window.close",
      "desktop.update.check", "desktop.update.start", "desktop.update.status", "desktop.update.restart", "desktop.update.busy"
    ]);
    if (!allowed.has(action)) throw new Error("不支援的桌面操作");
    const api = await this._bridge();
    const result = await api.invoke(action, payload || {});
    if (result && result.ok) return result.data;
    const info = (result && result.error) || {};
    if (info.code === "forbidden" && window.PosAuthOps)
      window.PosAuthOps.refresh().catch(() => {});
    const err = new Error(info.message || "操作失敗");
    const statuses = { validation_error: 422, not_found: 404, conflict: 409, forbidden: 403,
      database_error: 500, internal_error: 500 };
    err.status = statuses[info.code] || 500;
    err.code = info.code; err.details = info.details;
    throw err;
  },
  authStatus: () => API.invoke("auth.status", {}),
  authLogin: password => API.invoke("auth.login", {password}),
  authLogout: () => API.invoke("auth.logout", {}),
  authTouch: () => API.invoke("auth.touch", {}),
  changePassword: (old_password, new_password) => API.invoke("auth.change_password", {old_password, new_password}),
  removeStocktakeItem: (session_id, variant_id) => API.invoke("stocktake.remove_item", {session_id, variant_id}),
  listReceipts: p => API.invoke("stock.receipts", p),
  stocktakeHistory: p => API.invoke("stocktake.history", p),
  withdrawStocktake: session_id => API.invoke("stocktake.withdraw", {session_id}),
  returnStocktakeItems: (session_id, variant_ids) => API.invoke("stocktake.return", {session_id, variant_ids}),
  approveStocktake: session_id => API.invoke("stocktake.approve", {session_id}),
  pendingStocktakeCount: () => API.invoke("stocktake.pending_count", {}),
  setStocktakeNote: (session_id, variant_id, note) => API.invoke("stocktake.set_note", {session_id, variant_id, note}),
  cancelStocktake: (session_id, employee) => API.invoke("stocktake.cancel", {session_id, employee}),
  createReturn: p => API.invoke("returns.create", p), listReturns: () => API.invoke("returns.list", {}),
  returnHistory: p => API.invoke("returns.history", p), returnDetail: return_id => API.invoke("returns.detail", {return_id}),
  returnScan: (return_id, variant_id, qty) => API.invoke("returns.scan", {return_id, variant_id, qty}),
  setReturnQty: (return_id, variant_id, qty) => API.invoke("returns.set_qty", {return_id, variant_id, qty}),
  setReturnSale: (return_id, variant_id, sale_id) => API.invoke("returns.set_sale", {return_id, variant_id, sale_id}),
  setReturnReason: (return_id, variant_id, reason) => API.invoke("returns.set_reason", {return_id, variant_id, reason}),
  removeReturnItem: (return_id, variant_id) => API.invoke("returns.remove_item", {return_id, variant_id}),
  closeReturn: return_id => API.invoke("returns.close", {return_id}), withdrawReturn: return_id => API.invoke("returns.withdraw", {return_id}),
  setReturnDisposition: (return_id, variant_id, disposition) => API.invoke("returns.set_disposition", {return_id, variant_id, disposition}),
  approveReturn: return_id => API.invoke("returns.approve", {return_id}),
  cancelReturn: (return_id, employee) => API.invoke("returns.cancel", {return_id, employee}),
  pendingReturnCount: () => API.invoke("returns.pending_count", {}),
  repairReceive: p => API.invoke("returns.repair_receive", p),
  closeReturnItem: (return_id, variant_id, reason) => API.invoke("returns.close_item", {return_id, variant_id, reason}),
  reopenReturnItem: (return_id, variant_id) => API.invoke("returns.reopen_item", {return_id, variant_id}),
  listCategories: p => API.invoke("categories.list", p), createCategory: p => API.invoke("categories.create", p),
  updateCategory: (id, fields) => API.invoke("categories.update", {id, fields}), deleteCategory: id => API.invoke("categories.delete", {id}),
  sortCategories: ids => API.invoke("categories.sort", {ids}), categoryFields: id => API.invoke("categories.fields", {id}),
  setCategoryCommonFields: (id, field_ids) => API.invoke("categories.set_common_fields", {id, field_ids}),
  setCategoryField: (category_id, field_id, fields) => API.invoke("categories.set_field", {category_id, field_id, fields}),
  deleteCategoryField: (category_id, field_id) => API.invoke("categories.delete_field", {category_id, field_id}),
  listBrands: p => API.invoke("brands.list", p), createBrand: p => API.invoke("brands.create", p), updateBrand: (id, fields) => API.invoke("brands.update", {id, fields}), deleteBrand: id => API.invoke("brands.delete", {id}), sortBrands: ids => API.invoke("brands.sort", {ids}), setBrandCategories: (id, category_ids) => API.invoke("brands.set_categories", {id, category_ids}),
  listPhoneBrands: p => API.invoke("phone_brands.list", p), createPhoneBrand: p => API.invoke("phone_brands.create", p), updatePhoneBrand: (id, fields) => API.invoke("phone_brands.update", {id, fields}), deletePhoneBrand: id => API.invoke("phone_brands.delete", {id}), sortPhoneBrands: ids => API.invoke("phone_brands.sort", {ids}),
  listDeviceTypes: p => API.invoke("device_types.list", p), createDeviceType: p => API.invoke("device_types.create", p), updateDeviceType: (id, fields) => API.invoke("device_types.update", {id, fields}), deleteDeviceType: id => API.invoke("device_types.delete", {id}), sortDeviceTypes: ids => API.invoke("device_types.sort", {ids}),
  listModels: p => API.invoke("models.list", p), createModel: p => API.invoke("models.create", p), updateModel: (id, fields) => API.invoke("models.update", {id, fields}), deleteModel: id => API.invoke("models.delete", {id}), sortModels: ids => API.invoke("models.sort", {ids}),
  listFields: p => API.invoke("fields.list", p), createField: p => API.invoke("fields.create", p), updateField: (id, fields) => API.invoke("fields.update", {id, fields}),
  listOptions: p => API.invoke("options.list", p), createOption: p => API.invoke("options.create", p), updateOption: (id, fields) => API.invoke("options.update", {id, fields}), deleteOption: id => API.invoke("options.delete", {id}), cleanupOptions: field_id => API.invoke("options.cleanup", field_id == null ? {} : {field_id}),
  createProduct: p => API.invoke("products.create", p), listProducts: p => API.invoke("products.list", p), listCatalog: p => API.invoke("catalog.list", p), updateProduct: (id, fields) => API.invoke("products.update", {id, fields}), deleteProduct: id => API.invoke("products.delete", {id}),
  createVariant: (product_id, fields) => API.invoke("variants.create", {product_id, fields}), updateVariant: (id, fields) => API.invoke("variants.update", {id, fields}), deleteVariant: id => API.invoke("variants.delete", {id}),
  batchCreateVariants: (product_id, drafts) => API.invoke("variants.batch_create", {product_id, drafts}), batchPrecheckVariants: (product_id, drafts) => API.invoke("variants.batch_precheck", {product_id, drafts}), fieldUsage: (category_id, field_id, scope) => API.invoke("variants.field_usage", Object.assign({category_id, field_id}, scope || {})), modelUsage: (category_id, scope) => API.invoke("variants.model_usage", Object.assign({category_id}, scope || {})),
  activateVariant: id => API.invoke("variants.activate", {id}), variantIssues: () => API.invoke("variants.issues", {}),
  scanBarcode: code => API.invoke("barcodes.scan", {code}),
  lookupMembers: query => API.invoke("members.lookup", {query}), listMembers: query => API.invoke("members.list", {query}),
  createMember: p => API.invoke("members.create", p), updateMember: p => API.invoke("members.update", p),
  memberPhoneOwners: p => API.invoke("members.phone_owners", p), deleteMember: member_id => API.invoke("members.delete", {member_id}),
  receiveStock: p => API.invoke("stock.receive", p), stockDetail: variant_id => API.invoke("stock.detail", {variant_id}),
  createStocktake: p => API.invoke("stocktake.create", p), listStocktakes: () => API.invoke("stocktake.list", {}), stocktakeDetail: session_id => API.invoke("stocktake.detail", {session_id}), stocktakeScan: p => API.invoke("stocktake.scan", p), setStocktakeCounted: p => API.invoke("stocktake.set_counted", p), closeStocktake: session_id => API.invoke("stocktake.close", {session_id}),
  listPayments: () => API.invoke("payments.list", {}), checkout: p => API.invoke("sales.checkout", p), listSales: p => API.invoke("sales.list", p), recentSales: () => API.invoke("sales.recent", {}), salesSummary: p => API.invoke("sales.summary", p),
  updateVariantDetails(id, fields, modelIds) {
    return this.invoke("variants.update_details", {
      id, fields, model_ids: modelIds
    });
  },
  updateVariantEditor(payload) {
    return this.invoke("variants.update_editor", payload);
  },
  exportSales(payload) { return this.invoke("sales.export_save", payload); },
  printBarcode(variant_id, barcode, copies) { return this.invoke("printing.barcode", {variant_id, barcode, copies}); },
  previewLabel(variant_id, barcode) { return this.invoke("printing.preview", {variant_id, barcode}); },
  checkLabels(items) { return this.invoke("printing.check", {items}); },
};

window.parseTagList = function (v) {
  return String(v || "").split(/[,、，]/).map(s => s.trim()).filter(Boolean);
};

// ---- 規格顯示與表單共用工具(multi/tags 皆為清單)----

// 規格顯示字串:優先用後端組好的 attr_display;
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
