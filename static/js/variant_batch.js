window.PosPages = window.PosPages || {};

// 新增子產品內容頁:draft array 單一資料來源、連續建檔、預覽表、單層修改 popup。
window.PosPages["page-variant-batch"] = {
  template: "#tpl-variant-batch",
  inject: ["showError", "goPage", "markSaved"],
  // 由子視窗外殼帶入:從商品資料庫某商品進來時直接鎖定該種類與產品。
  props: {
    initCategoryId: { default: null },
    initProductId: { default: null },
  },
  data() {
    return {
      categories: [], products: [], models: [],
      catId: null, productId: null,
      fields: [], fieldUsage: {}, tagUsage: [],
      input: this.blankInput(),
      drafts: [], seq: 0,
      editing: null, lastDeleted: null,
      commitErrors: {}, doneMsg: "",
    };
  },
  computed: {
    product() { return this.products.find(p => p.product_id === this.productId) || null; },
    category() { return this.categories.find(c => c.category_id === this.catId) || null; },
    formalFields() { return this.fields.filter(f => f.field_type !== "tags"); },
    featureField() { return this.fields.find(f => f.field_type === "tags") || null; },
    catProducts() {
      return this.catId == null ? []
        : this.products.filter(p => p.category_id === this.catId);
    },
    modelMode() { return this.category ? this.category.model_mode : "hidden"; },
  },
  async mounted() {
    this._escHandler = (ev) => { if (ev.key === "Escape" && this.editing) this.editing = null; };
    document.addEventListener("keydown", this._escHandler);
    await this.guard(async () => {
      this.categories = await API.listCategories({});
      this.products = await API.listCatalog({});
      this.models = await API.listModels({});
      if (this.initCategoryId != null) {
        this.catId = this.initCategoryId;
        this.productId = this.initProductId;
        if (this.productId != null) await this.onProductChange();
      }
    });
  },
  unmounted() { document.removeEventListener("keydown", this._escHandler); },
  methods: {
    blankInput() {
      return { attrs: {}, price: null, model_ids: [], barcode: "", store: false };
    },
    async onCategoryChange() {
      this.productId = null;
      this.fields = []; this.fieldUsage = {}; this.tagUsage = [];
      this.drafts = []; this.commitErrors = {};
    },
    async onProductChange() {
      this.drafts = []; this.commitErrors = {}; this.doneMsg = "";
      if (this.productId == null) return;
      await this.guard(async () => {
        this.fields = await API.categoryFields(this.catId);
        this.fieldUsage = {};
        const scope = window.CatalogFields.usageScope(this.product);
        await window.CatalogFields.loadFieldUsage(
          this.catId, this.formalFields, this.fieldUsage, null, scope);
        if (this.featureField)
          this.tagUsage = await API.fieldUsage(
            this.catId, this.featureField.field_id, scope);
        this.input = this.blankInput();
        this.input.attrs = window.initFormAttrs(this.fields, {});
      });
    },
    closeWindow() { this.goPage("catalog"); },

    // ---- 加入 / 刪除 / 復原 ----
    missingRequired(attrs) {
      // 前端即時提示;最終以服務層為準
      const miss = [];
      for (const f of this.formalFields) {
        if (!f.required) continue;
        const v = attrs[f.name];
        const empty = f.field_type === "multi" ? !(Array.isArray(v) && v.length)
          : !(v != null && String(v).trim());
        if (empty) miss.push(f.name);
      }
      if (this.modelMode === "required") return miss; // 型號另在送出檢查
      return miss;
    },
    addDraft() {
      if (this.productId == null) { this.showError("請先選擇產品"); return; }
      const miss = this.missingRequired(this.input.attrs);
      if (miss.length) { this.showError("必填規格未填:" + miss.join("、")); return; }
      this.drafts.push(this.snapshot(this.input, "d" + (++this.seq)));
      this.doneMsg = "";
      // 連續建檔:保留輸入區內容供下一筆修改
    },
    snapshot(src, draft_id) {
      return {
        draft_id,
        attrs: JSON.parse(JSON.stringify(src.attrs)),
        price: src.price === "" ? null : src.price,
        model_ids: src.model_ids.slice(),
        barcode: (src.barcode || "").trim(),
        store: !!src.store,
      };
    },
    removeDraft(i) {
      this.lastDeleted = { index: i, draft: this.drafts[i] };
      this.drafts.splice(i, 1);
    },
    undoDelete() {
      if (!this.lastDeleted) return;
      this.drafts.splice(Math.min(this.lastDeleted.index, this.drafts.length), 0,
        this.lastDeleted.draft);
      this.lastDeleted = null;
    },

    // ---- 修改(單層 popup,深複本)----
    openEdit(i) {
      const d = this.drafts[i];
      this.editing = { index: i, draft: JSON.parse(JSON.stringify(d)) };
    },
    applyEdit() {
      const e = this.editing;
      this.drafts.splice(e.index, 1, this.snapshot(e.draft, e.draft.draft_id));
      this.editing = null;
    },
    cancelEdit() { this.editing = null; },

    // ---- 顯示 ----
    draftSpecText(d) {
      const parts = [];
      for (const f of this.formalFields) {
        const v = d.attrs[f.name];
        if (f.field_type === "multi") {
          if (Array.isArray(v) && v.length) parts.push(f.name + ":" + v.join("+"));
        } else if (v != null && String(v).trim()) parts.push(f.name + ":" + String(v).trim());
      }
      return parts.join("｜") || "(無規格)";
    },
    draftTags(d) {
      if (!this.featureField) return "";
      return window.parseTagList(d.attrs[this.featureField.name]).join(" + ");
    },
    draftModels(d) {
      const names = [];
      for (const id of d.model_ids) {
        const m = this.models.find(x => x.model_id === id);
        if (m) names.push(m.alias || m.name);
      }
      return names.join("、");
    },
    draftBarcode(d) {
      if (d.barcode) return d.barcode;
      if (d.store) return "自取碼（建立後產生）";
      return "—";
    },
    draftErrors(d) { return this.commitErrors[d.draft_id] || []; },

    // ---- 送出 ----
    buildPayload() {
      return this.drafts.map(d => {
        const barcodes = d.barcode ? [{ barcode: d.barcode, source: "factory" }]
          : (d.store ? [{ source: "store" }] : []);
        return {
          draft_id: d.draft_id,
          attributes: window.buildAttrPayload(this.fields, d.attrs),
          price: d.price === "" ? null : (d.price ?? null),
          model_ids: d.model_ids,
          barcodes,
        };
      });
    },
    async commitAll() {
      if (!this.drafts.length) { this.showError("尚未加入任何子產品"); return; }
      this.commitErrors = {};
      try {
        const res = await API.batchCreateVariants(this.productId, this.buildPayload());
        this.doneMsg = "已建立 " + res.results.length + " 筆款式。";
        this.markSaved();
        this.drafts = [];            // 成功才清空
        this.input = this.blankInput();
        this.input.attrs = window.initFormAttrs(this.fields, {});
        this.fieldUsage = {};
        const scope = window.CatalogFields.usageScope(this.product);
        await window.CatalogFields.loadFieldUsage(
          this.catId, this.formalFields, this.fieldUsage, null, scope);
        if (this.featureField)
          this.tagUsage = await API.fieldUsage(
            this.catId, this.featureField.field_id, scope);
      } catch (err) {
        // 失敗保留全部 draft,逐筆標示錯誤
        const map = {};
        for (const item of (err.details || [])) {
          const key = item.draft_id || (this.drafts[item.index] && this.drafts[item.index].draft_id);
          if (key) map[key] = item.errors || [];
        }
        this.commitErrors = map;
        this.showError(err.message || "建立失敗");
      }
    },
  },
};
