window.PosPages = window.PosPages || {};
window.PosComponents = window.PosComponents || {};

// 選項候選選取器(A＋C):該種類使用次數前 8 常用 chip ＋ 搜尋全部 ＋ 新增 ＋ 停用重啟提示。
// 通用於特性詞條(tags)、multi(多選)與 select(單選)。
// props:
//   modelValue  multiple=陣列(multi)或逗號字串(tags);single=字串(select)
//   usage       API.fieldUsage 回傳(依使用次數排序,含停用,帶 model_ids)
//   multiple    true=可多選(tags/multi);false=單選(select,再選即取代)
//   asList      multiple 時 modelValue 型別:true=陣列(multi)、false=逗號字串(tags)
//   modelIds    目前適用型號(依 OptionModel 過濾特別色候選)
//   placeholder 搜尋框提示字
window.PosComponents["opt-picker"] = {
  props: {
    modelValue: { default: "" },
    usage: { type: Array, default: () => [] },
    multiple: { type: Boolean, default: false },
    asList: { type: Boolean, default: false },
    modelIds: { type: Array, default: () => [] },
    placeholder: { type: String, default: "搜尋或輸入" },
  },
  emits: ["update:modelValue"],
  data() { return { query: "", showMore: false }; },
  computed: {
    selected() {
      if (this.multiple)
        return this.asList
          ? (Array.isArray(this.modelValue) ? this.modelValue.slice() : [])
          : window.parseTagList(this.modelValue);
      const s = (this.modelValue == null ? "" : String(this.modelValue)).trim();
      return s ? [s] : [];
    },
    selectedKeys() { return new Set(this.selected.map(s => s.toLowerCase())); },
    pool() {
      // 依適用型號過濾特別色候選(usage row 帶 model_ids)
      return window.CatalogFields.filterOptions(this.usage, this.modelIds);
    },
    available() {
      return this.pool.filter(o => o.active && !this.selectedKeys.has(o.value.toLowerCase()));
    },
    topChips() { return this.available.slice(0, 8); },
    moreChips() { return this.available.slice(8); },
    matches() {
      const q = this.query.trim().toLowerCase();
      if (!q) return [];
      return this.pool.filter(o => o.value.toLowerCase().includes(q)
        && !this.selectedKeys.has(o.value.toLowerCase())).slice(0, 12);
    },
    exactExists() {
      const q = this.query.trim().toLowerCase();
      if (!q) return true;
      return this.pool.some(o => o.value.toLowerCase() === q)
        || this.selectedKeys.has(q);
    },
  },
  methods: {
    isDisabledVal(val) {
      const o = this.pool.find(u => u.value.toLowerCase() === val.toLowerCase());
      return !!(o && !o.active);
    },
    emitList(list) {
      if (!this.multiple) { this.$emit("update:modelValue", list.length ? list[list.length - 1] : ""); return; }
      this.$emit("update:modelValue", this.asList ? list.slice() : list.join(", "));
    },
    add(val) {
      val = String(val).trim();
      if (!val) return;
      if (this.selectedKeys.has(val.toLowerCase())) return;
      this.emitList(this.multiple ? this.selected.concat([val]) : [val]);
    },
    remove(val) {
      this.emitList(this.selected.filter(s => s.toLowerCase() !== val.toLowerCase()));
    },
    addFromSearch() { this.add(this.query); this.query = ""; },
    pickMatch(o) { this.add(o.value); this.query = ""; },
  },
  template: `
  <div class="tag-selector">
    <div class="chip-wrap" v-if="selected.length">
      <span v-for="val in selected" :key="val" class="chip on tag-chip">
        {{ val }}
        <span v-if="isDisabledVal(val)" class="tag-reactivate">(將重新啟用)</span>
        <button type="button" class="tag-x" @click="remove(val)">✕</button>
      </span>
    </div>
    <div class="chip-wrap" v-if="topChips.length || moreChips.length">
      <button type="button" v-for="o in topChips" :key="o.option_id" class="chip"
              @click="add(o.value)">{{ o.value }}<span class="tag-count">{{ o.usage_count }}</span></button>
      <button type="button" v-if="moreChips.length && !showMore" class="chip tag-more"
              @click="showMore=true">更多…</button>
      <template v-if="showMore">
        <button type="button" v-for="o in moreChips" :key="o.option_id" class="chip"
                @click="add(o.value)">{{ o.value }}<span class="tag-count">{{ o.usage_count }}</span></button>
      </template>
    </div>
    <div class="tag-search">
      <input v-model="query" :placeholder="placeholder" @keyup.enter.stop="addFromSearch">
      <button type="button" class="btn-sm" v-if="query.trim() && !exactExists"
              @click="addFromSearch">新增「{{ query.trim() }}」</button>
    </div>
    <div class="chip-wrap" v-if="matches.length">
      <button type="button" v-for="o in matches" :key="o.option_id" class="chip"
              :class="{ inactive: !o.active }" @click="pickMatch(o)">
        {{ o.value }}<span v-if="!o.active" class="tag-reactivate">(停用,將重新啟用)</span>
      </button>
    </div>
  </div>`,
};

// 相容別名:特性詞條選取器(tags 逗號字串多選)。
window.PosComponents["tag-selector"] = {
  props: { modelValue: { type: String, default: "" }, usage: { type: Array, default: () => [] } },
  emits: ["update:modelValue"],
  template: `<opt-picker :model-value="modelValue" :usage="usage" :multiple="true" :as-list="false"
    placeholder="搜尋或輸入特性詞條" @update:model-value="$emit('update:modelValue', $event)"></opt-picker>`,
};

// 新增子產品內容頁:draft array 單一資料來源、連續建檔、預覽表、單層修改 popup。
window.PosPages["page-variant-batch"] = {
  template: "#tpl-variant-batch",
  inject: ["showError", "goPage"],
  data() {
    return {
      categories: [], products: [], models: [],
      catId: null, productId: null,
      fields: [], fieldOptions: {}, fieldUsage: {}, tagUsage: [],
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
    });
  },
  unmounted() { document.removeEventListener("keydown", this._escHandler); },
  methods: {
    blankInput() {
      return { attrs: {}, price: null, model_ids: [], barcode: "", store: false };
    },
    async onCategoryChange() {
      this.productId = null;
      this.fields = []; this.fieldOptions = {}; this.fieldUsage = {}; this.tagUsage = [];
      this.drafts = []; this.commitErrors = {};
    },
    async onProductChange() {
      this.drafts = []; this.commitErrors = {}; this.doneMsg = "";
      if (this.productId == null) return;
      await this.guard(async () => {
        this.fields = await API.categoryFields(this.catId);
        await window.CatalogFields.loadFieldsWithOptions(this.formalFields, this.fieldOptions);
        this.fieldUsage = {};
        await window.CatalogFields.loadFieldUsage(this.catId, this.formalFields, this.fieldUsage);
        if (this.featureField)
          this.tagUsage = await API.fieldUsage(this.catId, this.featureField.field_id);
        this.input = this.blankInput();
        this.input.attrs = window.initFormAttrs(this.fields, {});
      });
    },
    goCatalog() { this.goPage("catalog"); },

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
      if (this.productId == null) { this.showError("請先選擇大產品"); return; }
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
        this.doneMsg = "已建立 " + res.results.length + " 筆子產品。";
        this.drafts = [];            // 成功才清空
        this.input = this.blankInput();
        this.input.attrs = window.initFormAttrs(this.fields, {});
        this.fieldUsage = {};
        await window.CatalogFields.loadFieldUsage(this.catId, this.formalFields, this.fieldUsage);
        if (this.featureField)
          this.tagUsage = await API.fieldUsage(this.catId, this.featureField.field_id);
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
