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
      products: [],
      inactiveMatchCount: null, inactiveLookupFailed: false, _refreshToken: 0,
    };
  },
  computed: {
    isAdmin() { return window.PosAuth.isAdmin; },
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
  watch: {
    isAdmin(isAdmin) {
      if (!isAdmin && this.pending) { this.pending = false; this.reload(); }
    },
    pending(value) {
      if (value && !this.isAdmin) {
        this.pending = false;
        this.showError("需要管理者權限");
      }
    },
  },
  async mounted() {
    this._childWindowClosed = (event) => this.onChildWindowClosed(event);
    window.addEventListener("pos-child-window-closed", this._childWindowClosed);
    await this.guard(async () => {
      this.categories = await API.listCategories({});
      this.brands = await API.listBrands({ with_products: true });
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
          API.listCatalog(params), this.isAdmin ? API.variantIssues() : Promise.resolve({}),
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
    // 廠牌下拉只列「目前種類真的有商品」的廠牌:掛了種類卻沒商品的廠牌選了只會
    // 查無資料。切種類後原本選的廠牌若不在清單內,一併清掉避免查出空白。
    async reloadBrands() {
      const params = { with_products: true };
      if (this.fCategory != null) params.category_id = this.fCategory;
      this.brands = await API.listBrands(params);
      if (this.fBrand != null
          && !this.brands.some(b => b.brand_id === this.fBrand)) this.fBrand = null;
    },
    async onCategoryChange() {
      await this.guard(() => this.reloadBrands());
      await this.reload();
    },
    catalogParams() {
      if (this.pending && !this.isAdmin) {
        this.pending = false;
        this.showError("需要管理者權限");
      }
      return {q: this.q, include_inactive: this.includeInactive,
        category_id: this.fCategory, brand_id: this.fBrand,
        model_id: this.fModel, pending: this.pending};
    },
    // 型號顯示依種類設定:設為不使用適用型號的種類一律顯示「—」,
    // 既有 VariantModel 關聯保留不動,重新開啟就會再顯示。
    usesModel(p) {
      const c = this.categories.find(x => x.category_id === p.category_id);
      return !!c && c.model_mode === "required";
    },
    modelLines(p, v) {
      return this.usesModel(p) ? (v.models || []) : [];
    },
    variantCount(products) {
      return (products || []).reduce(
        (total, product) => total + (product.variants || []).length, 0);
    },
    async showInactiveResults() {
      this.includeInactive = true;
      await this.reload();
    },
    // 待處理問題轉正式中文說明(missing_required/duplicate_signature)
    issueText(it) {
      if (it.issue_type === "missing_required")
        return `缺少必填「${it.field_name || it.source_value || "規格"}」`;
      if (it.issue_type === "duplicate_signature")
        return "規格與其他款式重複" +
          (it.related_label ? `（${it.related_label}）` : "");
      return "資料異常";
    },
    async reload() {
      await this.refresh();
    },
    requireAdmin() {
      if (this.isAdmin) return true;
      this.showError("需要管理者權限"); return false;
    },
    editInSettings(p) {
      if (!this.requireAdmin()) return;
      this.goPage("settings", { category_id: p.category_id });
    },
    printLabel(p, v) { this.goPage("labels", { variant: Object.assign({}, v, { name: p.name }) }); },
    groupedVariants(p) {
      return window.groupVariantsByModel(p.variants, this.modelOrder);
    },

    async openVariantEditor(product, variant) {
      if (!this.requireAdmin()) return;
      await this.openChildWindow({
        page: "variant_editor", context: {product, variant},
      });
    },
    // product 為 null=不指定產品(由子視窗自己選)
    async openAddVariant(product) {
      if (!this.requireAdmin()) return;
      await this.openChildWindow({
        page: "variant_batch",
        context: product ? {
          category_id: product.category_id, product_id: product.product_id,
        } : {},
      });
    },
    async onChildWindowClosed(event) {
      if (event && event.detail && event.detail.saved) await this.reload();
    },

    async toggleProductActive(p) {
      if (!this.requireAdmin()) return;
      await this.guardReload(() =>
        API.updateProduct(p.product_id, { active: p.active ? 0 : 1 }));
    },
    async deleteProduct(p) {
      if (!this.requireAdmin()) return;
      if (!await PosConfirm.ask(`確定刪除商品「${p.name}」?刪除後無法復原。`,
                                { danger: true })) return;
      if (!this.requireAdmin()) return;
      await this.guardReload(() => API.deleteProduct(p.product_id));
    },

    async toggleVariantActive(p, v) {
      if (!this.requireAdmin()) return;
      await this.guardReload(() =>
        API.updateVariant(v.variant_id, { active: v.active ? 0 : 1 }));
    },
    async deleteVariant(p, v) {
      if (!this.requireAdmin()) return;
      if (!await PosConfirm.ask("確定刪除此款式?刪除後無法復原。", { danger: true })) return;
      if (!this.requireAdmin()) return;
      await this.guardReload(() => API.deleteVariant(v.variant_id));
    },

    // 條碼(瀏覽只顯示一條:優先原廠碼,其次自取碼;管理進編輯)
    mainBarcode(v) {
      if (!v.barcodes || !v.barcodes.length) return null;
      return v.barcodes.find(b => b.source === "factory") || v.barcodes[0];
    },
  },
};
