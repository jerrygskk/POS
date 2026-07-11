window.PosPages = window.PosPages || {};
window.PosPages["page-catalog"] = {
  template: "#tpl-catalog",
  inject: ["showError"],
  data() {
    return {
      q: "", includeInactive: false,
      fCategory: null, fBrand: null, fModel: null,
      categories: [], brands: [], models: [],
      products: [], fieldsByCat: {}, fieldOptions: {}, modelSearch: "",
      expanded: {}, bcInput: {},
      editProduct: null, editVariant: null,
      addingFor: null, newVariant: { attrs: {}, price: null, barcode: "", model_ids: [] },
    };
  },
  computed: {
    filteredModelGroups() {
      const kw = this.modelSearch.trim().toLowerCase();
      const g = {};
      for (const m of this.models) {
        if (kw && !(`${m.brand_name} ${m.name}`.toLowerCase().includes(kw))) continue;
        (g[m.brand_name] = g[m.brand_name] || []).push(m);
      }
      return Object.keys(g).map(brand => ({ brand, items: g[brand] }));
    },
  },
  async mounted() {
    try {
      this.categories = await API.get("/api/categories");
      this.brands = await API.get("/api/brands");
      this.models = await API.get("/api/models");
    } catch (e) { this.showError(e.message); }
    await this.reload();
  },
  methods: {
    attrText(row) { return window.fmtAttr(row) || "（無規格）"; },
    async reload() {
      try {
        let url = "/api/catalog?q=" + encodeURIComponent(this.q);
        if (this.includeInactive) url += "&include_inactive=1";
        if (this.fCategory) url += "&category_id=" + this.fCategory;
        if (this.fBrand) url += "&brand_id=" + this.fBrand;
        if (this.fModel) url += "&model_id=" + this.fModel;
        this.products = await API.get(url);
        this.editProduct = null; this.editVariant = null; this.addingFor = null;
      } catch (e) { this.showError(e.message); }
    },
    toggleExpand(pid) { this.expanded[pid] = !this.expanded[pid]; },

    async ensureFields(cid) {
      if (cid == null || this.fieldsByCat[cid]) return;
      try {
        const fields = await API.get("/api/categories/" + cid + "/fields");
        this.fieldsByCat[cid] = fields;
        // select/multi 欄選項另帶 model_ids(限定型號),供勾選框/下拉依適用型號過濾
        for (const f of fields)
          if (f.field_type === "select" || f.field_type === "multi")
            this.fieldOptions[f.field_id] =
              await API.get("/api/options?field_id=" + f.field_id);
      } catch (e) { this.showError(e.message); }
    },
    optionsFor(f, modelIds) {
      // 依變體「適用型號」過濾:未綁型號的選項恆顯示;綁定含任一適用型號者顯示。
      // 未選型號=顯示全部。
      const list = this.fieldOptions[f.field_id] || [];
      if (!modelIds || !modelIds.length) return list;
      return list.filter(o =>
        !o.model_ids.length || o.model_ids.some(id => modelIds.includes(id)));
    },
    // 手打自增:select 欄位輸入不存在的值,存檔時自動入庫
    async ensureOptions(cid, attrs) {
      for (const f of (this.fieldsByCat[cid] || [])) {
        if (f.field_type !== "select") continue;
        const v = (attrs[f.name] || "").trim();
        const list = this.fieldOptions[f.field_id] || [];
        if (!v || list.some(o => o.value === v)) continue;
        try {
          await API.post("/api/options", { field_id: f.field_id, value: v });
          this.fieldOptions[f.field_id] =
            await API.get("/api/options?field_id=" + f.field_id);
        } catch (e) { /* 冪等 */ }
      }
    },
    modelIdsByNames(cat_names) {
      // 依名稱反查 model_id(型號名稱於真實資料唯一)
      const ids = [];
      for (const n of cat_names) {
        const m = this.models.find(x => x.name === n);
        if (m) ids.push(m.model_id);
      }
      return ids;
    },

    // 款編輯
    startEditProduct(p) {
      this.editVariant = null;
      this.editProduct = { product_id: p.product_id, name: p.name,
        category_id: p.category_id, brand_id: p.brand_id,
        default_price: p.default_price, note: p.note || "" };
    },
    async saveProduct() {
      const e = this.editProduct;
      if (!e.name.trim()) { this.showError("請輸入商品名稱"); return; }
      try {
        await API.put("/api/products/" + e.product_id, {
          name: e.name.trim(), category_id: e.category_id, brand_id: e.brand_id,
          default_price: e.default_price ?? null, note: e.note.trim() || null });
        await this.reload();
      } catch (err) { this.showError(err.message); }
    },
    async toggleProductActive(p) {
      try {
        await API.put("/api/products/" + p.product_id, { active: p.active ? 0 : 1 });
        await this.reload();
      } catch (e) { this.showError(e.message); }
    },
    async deleteProduct(p) {
      if (!confirm(`確定刪除商品「${p.name}」?刪除後無法復原。`)) return;
      try {
        await API.del("/api/products/" + p.product_id);
        await this.reload();
      } catch (e) { this.showError(e.message); }
    },

    // 變體編輯
    async startEditVariant(p, v) {
      this.editProduct = null; this.modelSearch = "";
      await this.ensureFields(p.category_id);
      this.editVariant = { variant_id: v.variant_id, price: v.price,
        attrs: window.initFormAttrs(this.fieldsByCat[p.category_id], v.attributes),
        model_ids: this.modelIdsByNames(v.models || []),
        _cat: p.category_id };
    },
    async saveVariant() {
      const e = this.editVariant;
      try {
        await this.ensureOptions(e._cat, e.attrs);
        await API.put("/api/variants/" + e.variant_id, {
          attributes: window.buildAttrPayload(this.fieldsByCat[e._cat], e.attrs),
          price: e.price ?? null });
        await API.put("/api/variants/" + e.variant_id + "/models",
          { model_ids: e.model_ids });
        await this.reload();
      } catch (err) { this.showError(err.message); }
    },
    async toggleVariantActive(p, v) {
      try {
        await API.put("/api/variants/" + v.variant_id, { active: v.active ? 0 : 1 });
        await this.reload();
      } catch (e) { this.showError(e.message); }
    },
    async deleteVariant(p, v) {
      if (!confirm("確定刪除此變體?刪除後無法復原。")) return;
      try {
        await API.del("/api/variants/" + v.variant_id);
        await this.reload();
      } catch (e) { this.showError(e.message); }
    },

    // 條碼(瀏覽只顯示一條:優先原廠碼,其次自取碼;管理進編輯)
    mainBarcode(v) {
      if (!v.barcodes || !v.barcodes.length) return null;
      return v.barcodes.find(b => b.source === "factory") || v.barcodes[0];
    },
    async removeBarcode(p, code) {
      if (!confirm(`確定移除條碼「${code}」?`)) return;
      try {
        await API.del("/api/barcodes/" + encodeURIComponent(code));
        await this.reload();
      } catch (e) { this.showError(e.message); }
    },
    async addFactoryBarcode(p, v) {
      const code = (this.bcInput[v.variant_id] || "").trim();
      if (!code) { this.showError("請輸入原廠條碼"); return; }
      try {
        await API.post("/api/variants/" + v.variant_id + "/barcodes",
                       { barcode: code, source: "factory" });
        this.bcInput[v.variant_id] = "";
        await this.reload();
      } catch (e) { this.showError(e.message); }
    },
    async addStoreBarcode(p, v) {
      try {
        await API.post("/api/variants/" + v.variant_id + "/barcodes",
                       { source: "store" });
        await this.reload();
      } catch (e) { this.showError(e.message); }
    },

    // 新增變體
    async startAddVariant(p) {
      await this.ensureFields(p.category_id);
      this.modelSearch = "";
      this.newVariant = {
        attrs: window.initFormAttrs(this.fieldsByCat[p.category_id], {}),
        price: null, barcode: "", model_ids: [], _cat: p.category_id };
      this.addingFor = p.product_id;
    },
    async saveNewVariant(p) {
      const n = this.newVariant;
      const barcodes = n.barcode.trim()
        ? [{ barcode: n.barcode.trim(), source: "factory" }] : [];
      try {
        await this.ensureOptions(p.category_id, n.attrs);
        await API.post("/api/products/" + p.product_id + "/variants", {
          attributes: window.buildAttrPayload(this.fieldsByCat[p.category_id], n.attrs),
          price: n.price ?? null, model_ids: n.model_ids, barcodes });
        await this.reload();
      } catch (e) { this.showError(e.message); }
    },
  },
};
