window.PosPages = window.PosPages || {};

window.PosPages["page-variant-batch"] = {
  template: "#tpl-variant-batch",
  inject: ["showError", "goPage", "markSaved"],
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
      drafts: [], skipped: [], precheckErrors: {},
      seq: 0, precheckSeq: 0, _precheckTimer: null,
      inputCollapsed: false, selectionCollapsed: false,
      showDiffOnly: false, showSkipped: false,
      fixedEditor: null, lastDeleted: null,
      doneMsg: "", committing: false,
    };
  },
  computed: {
    product() { return this.products.find(p => p.product_id === this.productId) || null; },
    category() { return this.categories.find(c => c.category_id === this.catId) || null; },
    formalFields() { return this.fields.filter(f => f.field_type !== "tags"); },
    selectFields() { return this.formalFields.filter(f => f.field_type === "select"); },
    featureField() { return this.fields.find(f => f.field_type === "tags") || null; },
    catProducts() {
      return this.catId == null ? [] : this.products.filter(p => p.category_id === this.catId);
    },
    modelMode() { return this.category ? this.category.model_mode : "hidden"; },
    axesInfo() {
      return window.VariantBatchLogic.expandAxes(this.formalFields, this.input.attrs);
    },
    previewCount() { return this.axesInfo.count; },
    inputFormula() { return window.VariantBatchLogic.formulaText(this.axesInfo.axes); },
    diffFields() {
      return window.VariantBatchLogic.diffFieldNames(this.drafts, this.formalFields);
    },
    fixedDraft() {
      return this.fixedEditor
        ? this.drafts.find(d => d.draft_id === this.fixedEditor.draftId) || null : null;
    },
    fixedField() {
      if (!this.fixedEditor || this.fixedEditor.fieldName === "__models") return null;
      return this.fields.find(f => f.name === this.fixedEditor.fieldName) || null;
    },
  },
  async mounted() {
    this._escHandler = ev => {
      if (ev.key === "Escape" && this.fixedEditor) this.closeFixedEditor();
    };
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
  unmounted() {
    this.invalidatePrecheck();
    document.removeEventListener("keydown", this._escHandler);
  },
  methods: {
    blankInput() {
      return { attrs: {}, price: null, model_ids: [], barcode: "", store: false };
    },
    resetInput() {
      const input = this.blankInput();
      input.attrs = window.initFormAttrs(this.fields, {});
      for (const field of this.selectFields) input.attrs[field.name] = [];
      this.input = input;
    },
    clearWorksheet() {
      this.drafts = [];
      this.skipped = [];
      this.precheckErrors = {};
      this.fixedEditor = null;
      this.lastDeleted = null;
      this.showSkipped = false;
    },
    async onCategoryChange() {
      this.invalidatePrecheck();
      this.productId = null;
      this.fields = [];
      this.fieldUsage = {};
      this.tagUsage = [];
      this.input = this.blankInput();
      this.clearWorksheet();
      this.inputCollapsed = false;
      this.selectionCollapsed = false;
      this.doneMsg = "";
    },
    async onProductChange() {
      this.invalidatePrecheck();
      this.clearWorksheet();
      this.doneMsg = "";
      this.inputCollapsed = false;
      this.selectionCollapsed = this.productId != null;
      if (this.productId == null) {
        this.fields = [];
        this.fieldUsage = {};
        this.tagUsage = [];
        this.input = this.blankInput();
        return;
      }
      await this.guard(async () => {
        this.fields = await API.categoryFields(this.catId);
        this.fieldUsage = {};
        const scope = window.CatalogFields.usageScope(this.product);
        await window.CatalogFields.loadFieldUsage(
          this.catId, this.formalFields, this.fieldUsage, null, scope);
        this.tagUsage = this.featureField
          ? await API.fieldUsage(this.catId, this.featureField.field_id, scope) : [];
        this.resetInput();
      });
    },
    reopenProductSelection() {
      this.selectionCollapsed = false;
      this.inputCollapsed = false;
    },
    closeWindow() {
      this.invalidatePrecheck();
      this.goPage("catalog");
    },
    setInputAttr(name, value) { this.input.attrs[name] = value; },
    optionCount(name) {
      const value = this.input.attrs[name];
      return Array.isArray(value) ? value.length : 0;
    },

    async generatePreview() {
      if (this.productId == null) {
        this.showError("請先選擇產品");
        return;
      }
      const count = this.previewCount;
      if (count > 30 && !await PosConfirm.ask(`將產生 ${count} 筆款式，確定展開？`)) return;
      this.invalidatePrecheck();
      const rows = window.VariantBatchLogic.expandRows(
        this.formalFields, this.input, this.seq);
      this.seq += rows.length;
      this.drafts = this.drafts.concat(rows);
      this.doneMsg = "";
      await this.runPrecheck();
      this.inputCollapsed = true;
      this.resetInput();
    },
    duplicateRowAt(index) {
      const row = window.VariantBatchLogic.duplicateRow(this.drafts[index], ++this.seq);
      this.drafts.splice(index + 1, 0, row);
      this.doneMsg = "";
      this.schedulePrecheck();
    },
    removeDraft(index) {
      const draft = this.drafts[index];
      this.lastDeleted = { index, draft };
      this.drafts.splice(index, 1);
      if (this.fixedEditor && this.fixedEditor.draftId === draft.draft_id)
        this.fixedEditor = null;
      this.doneMsg = "";
      this.schedulePrecheck();
    },
    undoDelete() {
      if (!this.lastDeleted) return;
      this.drafts.splice(Math.min(this.lastDeleted.index, this.drafts.length), 0,
        this.lastDeleted.draft);
      this.lastDeleted = null;
      this.schedulePrecheck();
    },
    openFixedEditor(draftId, fieldName) {
      this.fixedEditor = { draftId, fieldName };
    },
    closeFixedEditor() {
      if (!this.fixedEditor) return;
      this.fixedEditor = null;
      this.schedulePrecheck();
    },

    invalidatePrecheck() {
      this.precheckSeq++;
      clearTimeout(this._precheckTimer);
      this._precheckTimer = null;
    },
    schedulePrecheck() {
      this.invalidatePrecheck();
      this._precheckTimer = setTimeout(() => this.runPrecheck(), 300);
    },
    async runPrecheck() {
      const seq = ++this.precheckSeq;
      const rows = this.drafts.slice();
      if (!rows.length) {
        if (seq === this.precheckSeq) this.precheckErrors = {};
        return;
      }
      let res;
      try {
        res = await API.batchPrecheckVariants(this.productId, this.buildPayload(rows));
      } catch (err) {
        if (seq === this.precheckSeq) this.showError(err.message);
        return;
      }
      if (seq !== this.precheckSeq) return;
      const part = window.VariantBatchLogic.partitionPrecheck(rows, res.results);
      this.drafts = part.kept;
      this.skipped = this.skipped.concat(part.skipped);
      this.precheckErrors = part.errorsByDraftId;
      if (this.fixedEditor && !this.fixedDraft) this.fixedEditor = null;
    },

    buildPayload(rows) {
      return (rows || this.drafts).map(d => {
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
    errorsFor(draft) { return this.precheckErrors[draft.draft_id] || []; },
    fieldErrors(draft, field) {
      return this.errorsFor(draft).filter(err => err.field_id === field.field_id);
    },
    modelErrors(draft) {
      return this.errorsFor(draft).filter(err => err.code === "missing_models");
    },
    barcodeErrors(draft) {
      return this.errorsFor(draft).filter(err => err.code === "duplicate_barcode");
    },
    duplicateErrors(draft) {
      return this.errorsFor(draft).filter(err => err.code === "duplicate_signature");
    },
    errorText(errors) {
      return (errors || []).map(err => err.message || "資料有誤").join("；");
    },
    rowStatus(draft) {
      const duplicates = this.duplicateErrors(draft);
      if (duplicates.length) {
        const error = duplicates[0];
        return error.related_draft_id
          ? window.VariantBatchLogic.dupRefText(error, this.drafts)
          : (error.message || "規格重複");
      }
      const count = this.errorsFor(draft).length;
      return count ? `請修正 ${count} 項問題` : "可建立";
    },
    fieldOptions(field, draft) {
      const values = (this.fieldUsage[field.field_id] || []).map(option => option.value);
      const current = draft.attrs[field.name];
      if (current != null && String(current).trim() && !values.includes(current)) values.push(current);
      return values;
    },
    draftSpecText(draft) {
      const parts = [];
      for (const field of this.formalFields) {
        const value = draft.attrs[field.name];
        const text = Array.isArray(value) ? value.join("＋") : String(value || "").trim();
        if (text) parts.push(`${field.name}：${text}`);
      }
      return parts.join("｜") || "（無規格）";
    },
    draftTags(draft) {
      if (!this.featureField) return "";
      return window.parseTagList(draft.attrs[this.featureField.name]).join("＋");
    },
    draftModels(draft) {
      return draft.model_ids.map(id => {
        const model = this.models.find(item => item.model_id === id);
        return model ? (model.alias || model.name) : `型號 ${id}`;
      }).join("、");
    },
    draftBarcode(draft) {
      if (draft.barcode) return draft.barcode;
      return draft.store ? "自取碼（建立後產生）" : "—";
    },
    draftNumber(draft) {
      return this.drafts.findIndex(row => row.draft_id === draft.draft_id) + 1;
    },
    existingVariant(item) {
      for (const product of this.products) {
        const variant = (product.variants || []).find(
          row => row.variant_id === item.related_variant_id);
        if (variant) return variant;
      }
      return null;
    },
    existingVariantText(item) {
      const variant = this.existingVariant(item);
      if (!variant) return `款式編號 ${item.related_variant_id}`;
      const attrs = variant.attributes || {};
      const parts = Array.isArray(attrs)
        ? attrs.map(value => value.value || value.option_value || "").filter(Boolean)
        : Object.entries(attrs).map(([name, value]) => `${name}：${value}`);
      return parts.join("｜") || `款式編號 ${item.related_variant_id}`;
    },
    mapDetails(details) {
      const mapped = {};
      for (const item of (details || [])) {
        const row = this.drafts[item.index];
        const draftId = item.draft_id || (row && row.draft_id);
        if (draftId) mapped[draftId] = (item.errors || []).slice();
      }
      return mapped;
    },
    async reloadUsage() {
      this.fieldUsage = {};
      const scope = window.CatalogFields.usageScope(this.product);
      await window.CatalogFields.loadFieldUsage(
        this.catId, this.formalFields, this.fieldUsage, null, scope);
      this.tagUsage = this.featureField
        ? await API.fieldUsage(this.catId, this.featureField.field_id, scope) : [];
    },
    async commitAll() {
      if (!this.drafts.length || this.committing) return;
      this.invalidatePrecheck();
      this.committing = true;
      try {
        const res = await API.batchCreateVariants(this.productId, this.buildPayload(this.drafts));
        this.doneMsg = `已建立 ${res.results.length} 筆款式。`;
        this.markSaved();
        this.clearWorksheet();
        this.resetInput();
        this.inputCollapsed = false;
        await this.reloadUsage();
      } catch (err) {
        this.precheckErrors = this.mapDetails(err.details);
        this.showError(err.message || "建立失敗");
      } finally {
        this.committing = false;
      }
    },
  },
};
