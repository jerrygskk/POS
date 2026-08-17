window.PosPages = window.PosPages || {};

// 依型號分組並附 rowspan 資訊的純函式。
// variants:變體陣列(v.models 已為別名字串、依型號 sort 序排好)。
// modelOrder:{ 型號別名 → 全域排序索引 },用於群組間排序;撈不到則以名稱排序。
// 回傳列陣列,每列 { v, label, models, showModel, rowspan }。
window.groupVariantsByModel = function (variants, modelOrder) {
  modelOrder = modelOrder || {};
  const groups = [];
  const idx = {};
  for (const v of variants) {
    const models = v.models || [];
    const key = models.join("");
    if (idx[key] === undefined) {
      idx[key] = groups.length;
      groups.push({ label: models.join("、"), models: models, rows: [] });
    }
    groups[idx[key]].rows.push(v);
  }
  // 群組排序鍵:有型號且找得到 → [0, 索引];有型號但找不到 → [1, 名稱];無型號 → [2]
  const sortKey = (g) => {
    if (!g.models.length) return [2, 0, ""];
    const first = g.models[0];
    const o = modelOrder[first];
    if (o === undefined) return [1, 0, first];
    return [0, o, ""];
  };
  groups
    .map((g, i) => ({ g, k: sortKey(g), i }))
    .sort((a, b) => {
      if (a.k[0] !== b.k[0]) return a.k[0] - b.k[0];
      if (a.k[1] !== b.k[1]) return a.k[1] - b.k[1];
      if (a.k[2] !== b.k[2]) return a.k[2] < b.k[2] ? -1 : 1;
      return a.i - b.i; // 穩定:維持原群組順序
    })
    .forEach((e, pos) => (e.g._pos = pos));
  groups.sort((a, b) => a._pos - b._pos);
  // 攤平成列，同型號群組共用一個 rowspan 型號格。
  const out = [];
  for (const g of groups) {
    for (let i = 0; i < g.rows.length; i++) {
      out.push({ v: g.rows[i], label: g.label, models: g.models,
                 showModel: i === 0, rowspan: i === 0 ? g.rows.length : 0 });
    }
  }
  return out;
};

window.PosPages["page-catalog"] = {
  template: "#tpl-catalog",
  inject: ["showError", "goPage"],
  data() {
    return {
      q: "", appliedQ: "", includeInactive: false, pending: false, pendingCount: 0,
      fCategory: null, fBrand: null, fModel: null,
      categories: [], brands: [], models: [],
      products: [], fieldsByCat: {}, fieldOptions: {}, fieldUsageByCat: {},
      inactiveMatchCount: null, inactiveLookupFailed: false, _refreshToken: 0,
    };
  },
  computed: {
    modelOrder() {
      // 型號別名 → 全域排序索引(this.models 已依 pb.sort, m.sort 排好)
      const o = {};
      this.models.forEach((m, i) => { o[m.alias || m.name] = i; });
      return o;
    },
    displayVariantCount() {
      return this.variantCount(this.products);
    },
    displayProducts() {
      const hasKeyword = Boolean((this.appliedQ || "").trim());
      return this.products.filter(p =>
        (p.variants || []).length > 0 || (!hasKeyword && p.active));
    },
  },
  async mounted() {
    this._childWindowClosed = (event) => this.onChildWindowClosed(event);
    window.addEventListener("pos-child-window-closed", this._childWindowClosed);
    await this.guard(async () => {
      this.categories = await API.listCategories({});
      this.brands = await API.listBrands({});
      this.models = await API.listModels({});
    });
    await this.reload();
  },
  unmounted() {
    window.removeEventListener("pos-child-window-closed", this._childWindowClosed);
  },
  methods: {
    // 只重撈資料,不動編輯狀態(條碼即時新增/刪除用,避免把使用者踢出編輯)
    async refresh() {
      const token = ++this._refreshToken;
      const params = this.catalogParams();
      await this.guard(async () => {
        const [products, summary] = await Promise.all([
          API.listCatalog(params), API.variantIssues(),
        ]);
        if (token !== this._refreshToken) return;
        this.products = products;
        this.appliedQ = params.q;
        this.pendingCount = summary.pending_variant_count || 0;
        this.inactiveMatchCount = null;
        this.inactiveLookupFailed = false;

        if (params.include_inactive || this.variantCount(products) !== 0) return;
        try {
          const inactiveProducts = await API.listCatalog({
            ...params, include_inactive: true,
          });
          if (token !== this._refreshToken) return;
          this.inactiveMatchCount = this.variantCount(inactiveProducts);
        } catch (error) {
          if (token !== this._refreshToken) return;
          this.inactiveLookupFailed = true;
          this.showError(error.message);
        }
      });
    },
    catalogParams() {
      return {q: this.q, include_inactive: this.includeInactive,
        category_id: this.fCategory, brand_id: this.fBrand,
        model_id: this.fModel, pending: this.pending};
    },
    variantCount(products) {
      return (products || []).reduce(
        (total, product) => total + (product.variants || []).length, 0);
    },
    async showInactiveResults() {
      this.includeInactive = true;
      await this.reload();
    },
    // 待處理問題轉正式中文說明(missing_required/duplicate_barcode/duplicate_signature)
    issueText(it) {
      if (it.issue_type === "missing_required")
        return `缺少必填「${it.field_name || it.source_value || "規格"}」`;
      if (it.issue_type === "duplicate_barcode")
        return `條碼「${it.source_value}」與其他款式重複` +
          (it.related_label ? `（${it.related_label}）` : "");
      if (it.issue_type === "duplicate_signature")
        return "規格與其他款式重複" +
          (it.related_label ? `（${it.related_label}）` : "");
      return "資料異常";
    },
    async reload() {
      await this.refresh();
    },
    editInSettings() { this.goPage("settings"); },
    groupedVariants(p) {
      return window.groupVariantsByModel(p.variants, this.modelOrder);
    },

    // 款式修改與新增款式共用同一個子視窗機制:開窗前先鎖主視窗,
    // 開窗失敗要自己解鎖(成功時由關窗事件解鎖)。
    async openChildWindow(page, context) {
      window.PosDesktopLock.lock();
      try {
        await API.invoke("desktop.child_window.open", {page, context});
      } catch (error) {
        window.PosDesktopLock.unlock();
        this.showError(error.message);
      }
    },
    async openVariantEditor(product, variant) {
      await this.openChildWindow("variant_editor", {product, variant});
    },
    // product 為 null=不指定大產品(由子視窗自己選)
    async openAddVariant(product) {
      await this.openChildWindow("variant_batch", product ? {
        category_id: product.category_id, product_id: product.product_id,
      } : {});
    },
    async onChildWindowClosed(event) {
      if (event && event.detail && event.detail.saved) await this.reload();
    },

    async ensureFields(cid) {
      if (cid == null || this.fieldsByCat[cid]) return;
      await this.guard(async () => {
        const fields = await API.categoryFields(cid);
        this.fieldsByCat[cid] = fields;
        // select/multi 欄選項(供自增比對與型號過濾)
        await window.CatalogFields.loadFieldsWithOptions(fields, this.fieldOptions);
        // 該種類使用次數排序候選(select/multi 改用候選選取器)
        const usage = {};
        await window.CatalogFields.loadFieldUsage(cid, fields, usage);
        this.fieldUsageByCat[cid] = usage;
      });
    },
    async reloadFieldUsage(cid) {
      if (cid == null || !this.fieldsByCat[cid]) return;
      const usage = {};
      await window.CatalogFields.loadFieldUsage(cid, this.fieldsByCat[cid], usage);
      this.fieldUsageByCat[cid] = usage;
    },
    async toggleProductActive(p) {
      await this.guardReload(() =>
        API.updateProduct(p.product_id, { active: p.active ? 0 : 1 }));
    },
    async deleteProduct(p) {
      if (!confirm(`確定刪除商品「${p.name}」?刪除後無法復原。`)) return;
      await this.guardReload(() => API.deleteProduct(p.product_id));
    },

    async toggleVariantActive(p, v) {
      await this.guardReload(() =>
        API.updateVariant(v.variant_id, { active: v.active ? 0 : 1 }));
    },
    async deleteVariant(p, v) {
      if (!confirm("確定刪除此款式?刪除後無法復原。")) return;
      await this.guardReload(() => API.deleteVariant(v.variant_id));
    },

    // 條碼(瀏覽只顯示一條:優先原廠碼,其次自取碼;管理進編輯)
    mainBarcode(v) {
      if (!v.barcodes || !v.barcodes.length) return null;
      return v.barcodes.find(b => b.source === "factory") || v.barcodes[0];
    },
  },
};
