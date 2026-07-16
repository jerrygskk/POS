window.PosPages = window.PosPages || {};

// 廠牌／手機品牌／型號沿用通用清單維護;種類與大產品改走商品設定單頁。
const _MAINT = {
  brands: { id: "brand_id", label: "廠牌", list: "listBrands", create: "createBrand", update: "updateBrand", delete: "deleteBrand", sort: "sortBrands" },
  phoneBrands: { id: "phone_brand_id", label: "手機品牌", list: "listPhoneBrands", create: "createPhoneBrand", update: "updatePhoneBrand", delete: "deletePhoneBrand", sort: "sortPhoneBrands" },
  models: { id: "model_id", label: "型號", list: "listModels", create: "createModel", update: "updateModel", delete: "deleteModel", sort: "sortModels" },
};

const _TYPE_LABEL = { select: "下拉選單", text: "文字", multi: "複選", tags: "特性詞條" };
const _MODE_LABEL = { required: "使用適用型號", hidden: "不使用適用型號" };

window.PosPages["page-settings"] = {
  template: "#tpl-settings",
  inject: ["showError"],
  data() {
    return {
      categories: [], brands: [], phoneBrands: [], models: [],
      newItem: { brands: "", phoneBrands: "" },
      newModel: { phone_brand_id: null, name: "", series: "" },
      newSeq: { brands: "", phoneBrands: "", models: "" },
      // 商品設定單頁
      selCatId: null, newCatName: "",
      tplFields: [], tplOptions: {}, catHasVariant: false, bigProducts: [],
      // 規格模板 popup(單層)
      fieldPopup: null,
      // 大產品 popup(單層)
      prodPopup: null, brandMenuOpen: false,
      // 廠牌經營種類
      openBrand: null, openBrandName: "", brandCatChecked: {},
    };
  },
  computed: {
    modelGroups() {
      const g = {};
      for (const m of this.models) (g[m.brand_name] = g[m.brand_name] || []).push(m);
      return Object.keys(g).map(brand => ({ brand, items: g[brand] }));
    },
    selectedCat() {
      return this.categories.find(c => c.category_id === this.selCatId) || null;
    },
    templateRows() {
      return this.tplFields.slice().sort((a, b) =>
        (a.sort - b.sort) || (a.field_id - b.field_id));
    },
    filteredBrands() {
      const q = ((this.prodPopup && this.prodPopup.brandQuery) || "").trim().toLowerCase();
      if (!q) return this.brands;
      return this.brands.filter(b => (b.name || "").toLowerCase().includes(q));
    },
    brandExactMatch() {
      const q = ((this.prodPopup && this.prodPopup.brandQuery) || "").trim().toLowerCase();
      if (!q) return true;
      return this.brands.some(b => (b.name || "").trim().toLowerCase() === q);
    },
  },
  async mounted() { await this.reloadAll(); },
  methods: {
    async reloadAll() {
      await this.guard(async () => {
        this.categories = await API.listCategories({ all: 1 });
        this.brands = await API.listBrands({ all: 1 });
        this.phoneBrands = await API.listPhoneBrands({ all: 1 });
        this.models = await API.listModels({ all: 1 });
        this._takeSnap();
        if (!this.categories.some(c => c.category_id === this.selCatId))
          this.selCatId = this.categories.length ? this.categories[0].category_id : null;
        if (this.selCatId != null) await this.loadCategoryDetail();
      });
    },

    // ==== 商品設定:種類 ====
    async selectCategory(c) {
      if (this.selCatId === c.category_id) return;
      this.selCatId = c.category_id;
      this.fieldPopup = null; this.prodPopup = null;
      await this.guard(() => this.loadCategoryDetail());
    },
    async loadCategoryDetail() {
      const cid = this.selCatId;
      this.tplFields = await API.listFields({ category_id: cid });
      this.tplOptions = {};
      for (const f of this.tplFields)
        if (["select", "multi", "tags"].includes(f.field_type))
          this.tplOptions[f.field_id] = await API.listOptions({ field_id: f.field_id, all: 1 });
      this.bigProducts = await API.listCatalog({ category_id: cid, include_inactive: true });
      this.catHasVariant = this.bigProducts.some(p => (p.variants || []).length > 0);
    },
    async addCategory() {
      const name = (this.newCatName || "").trim();
      if (!name) { this.showError("請輸入商品種類名稱"); return; }
      await this.guard(async () => {
        const r = await API.createCategory({ name, model_mode: "hidden" });
        this.newCatName = "";
        this.selCatId = r.category_id;
        await this.reloadAll();
      });
    },
    async saveCategoryName(c) {
      const name = (c.name || "").trim();
      if (!name) { this.showError("商品種類名稱不可空白"); return; }
      await this.guard(() => API.updateCategory(c.category_id, { name }));
    },
    async toggleCategoryActive(c) {
      await this.guard(async () => {
        await API.updateCategory(c.category_id, { active: c.active ? 0 : 1 });
        c.active = c.active ? 0 : 1;
      });
    },
    async setModelMode(c, mode) {
      await this.guard(async () => {
        await API.updateCategory(c.category_id, { model_mode: mode });
        c.model_mode = mode;
      });
    },
    async deleteCategory(c) {
      if (!confirm(`確定刪除商品種類「${c.name}」?刪除後無法復原。`)) return;
      await this.guard(async () => {
        await API.deleteCategory(c.category_id);
        if (this.selCatId === c.category_id) this.selCatId = null;
        await this.reloadAll();
      });
    },
    modeLabel(m) { return _MODE_LABEL[m] || m; },

    // ==== 商品設定:規格模板 ====
    fieldTypeLabel(t) { return _TYPE_LABEL[t] || t; },
    isFeature(f) { return f.field_type === "tags"; },
    defaultValueName(f) {
      if (f.default_option_id == null) return "";
      const o = (this.tplOptions[f.field_id] || []).find(x => x.option_id === f.default_option_id);
      return o ? o.value : "";
    },
    activeOptions(fieldId) {
      return (this.tplOptions[fieldId] || []).filter(o => o.active);
    },
    openFieldPopup(f) {
      if (f && this.isFeature(f)) return;   // 特性詞條為固定欄,不進編輯
      if (f) {
        this.fieldPopup = {
          mode: "edit", field_id: f.field_id, name: f.name, field_type: f.field_type,
          sort: f.sort, required: !!f.required, active: !!f.cf_active,
          default_option_id: f.default_option_id, newOption: "",
        };
      } else {
        this.fieldPopup = {
          mode: "new", field_id: null, name: "", field_type: "select",
          sort: (this.tplFields.length ? Math.max(...this.tplFields.map(x => x.sort)) + 1 : 1),
          required: false, active: true, default_option_id: null, newOption: "",
        };
      }
    },
    async savePopupField() {
      const p = this.fieldPopup;
      const name = (p.name || "").trim();
      if (!name) { this.showError("請輸入規格欄名稱"); return; }
      await this.guard(async () => {
        let fid = p.field_id;
        if (p.mode === "new") {
          const r = await API.createField({ name, category_id: this.selCatId, field_type: p.field_type });
          fid = r.field_id;
        } else {
          if (name !== this.tplFields.find(f => f.field_id === fid).name)
            await API.updateField(fid, { name });
          const cur = this.tplFields.find(f => f.field_id === fid);
          if (p.field_type !== cur.field_type)
            await API.updateField(fid, { field_type: p.field_type });
        }
        const setFields = { sort: parseInt(p.sort, 10) || 0, active: p.active ? 1 : 0 };
        if (!this.catHasVariant) setFields.required = p.required ? 1 : 0;
        setFields.default_option_id = p.field_type === "select" ? (p.default_option_id ?? null) : null;
        await API.setCategoryField(this.selCatId, fid, setFields);
        this.fieldPopup = null;
        await this.loadCategoryDetail();
      });
    },
    async addPopupOption() {
      const p = this.fieldPopup;
      const v = (p.newOption || "").trim();
      if (!v || p.field_id == null) return;
      await this.guard(async () => {
        await API.createOption({ field_id: p.field_id, value: v, reactivate: true });
        p.newOption = "";
        this.tplOptions[p.field_id] = await API.listOptions({ field_id: p.field_id, all: 1 });
      });
    },
    async cleanupFieldOptions() {
      const p = this.fieldPopup;
      if (p.field_id == null) return;
      if (!confirm("將永久刪除此規格欄中未使用且非預設值的選項,無法復原。確定繼續?")) return;
      await this.guard(async () => {
        const r = await API.cleanupOptions(p.field_id);
        this.tplOptions[p.field_id] = await API.listOptions({ field_id: p.field_id, all: 1 });
        alert(`已清理 ${r.deleted} 個未使用選項。`);
      });
    },
    async deletePopupOption(o) {
      const p = this.fieldPopup;
      const message = o.usage_count > 0
        ? `此選項有 ${o.usage_count} 筆商品規格使用中,刪除後將從新增選單隱藏,既有商品不受影響。確定繼續?`
        : "此選項目前未使用,刪除後將永久移除且無法復原。確定繼續?";
      if (!confirm(message)) return;
      await this.guard(async () => {
        await API.deleteOption(o.option_id);
        if (p.default_option_id === o.option_id) p.default_option_id = null;
        this.tplOptions[p.field_id] = await API.listOptions({ field_id: p.field_id, all: 1 });
      });
    },

    // ==== 商品設定:大產品 ====
    openProductPopup(p) {
      this.brandMenuOpen = false;
      if (p) {
        this.prodPopup = {
          mode: "edit", product_id: p.product_id, name: p.name, nameDirty: true,
          brandQuery: p.brand_name || "", brand_id: p.brand_id, brand_name: null,
          note: p.note || "", active: !!p.active,
        };
      } else {
        this.prodPopup = {
          mode: "new", product_id: null, name: "", nameDirty: false,
          brandQuery: "", brand_id: null, brand_name: null, note: "", active: true,
        };
      }
    },
    onProdNameInput() { if (this.prodPopup) this.prodPopup.nameDirty = true; },
    refreshAutoName() {
      const p = this.prodPopup;
      if (!p || p.nameDirty) return;
      const brand = p.brandQuery.trim();
      const cat = this.selectedCat ? this.selectedCat.name : "";
      p.name = (brand && cat) ? (brand + cat) : (p.name || "");
    },
    pickBrand(b) {
      const p = this.prodPopup;
      p.brand_id = b.brand_id; p.brand_name = null; p.brandQuery = b.name;
      this.brandMenuOpen = false;
      this.refreshAutoName();
    },
    addInlineBrand() {
      const p = this.prodPopup;
      const name = p.brandQuery.trim();
      if (!name) return;
      p.brand_id = null; p.brand_name = name;
      this.brandMenuOpen = false;
      this.refreshAutoName();
    },
    onBrandQueryInput() {
      const p = this.prodPopup;
      p.brand_id = null; p.brand_name = null;
      this.brandMenuOpen = true;
      this.refreshAutoName();
    },
    onBrandBlur() { setTimeout(() => { this.brandMenuOpen = false; }, 120); },
    _brandPayload(p) {
      if (p.brand_id != null) return { brand_id: p.brand_id };
      const q = p.brandQuery.trim();
      if (q) {
        const hit = this.brands.find(b => (b.name || "").trim().toLowerCase() === q.toLowerCase());
        return hit ? { brand_id: hit.brand_id } : { brand_name: q };
      }
      return {};
    },
    async saveProduct() {
      const p = this.prodPopup;
      const name = (p.name || "").trim();
      if (!name) { this.showError("請輸入大產品名稱"); return; }
      const brand = this._brandPayload(p);
      await this.guard(async () => {
        if (p.mode === "new") {
          await API.createProduct(Object.assign(
            { name, category_id: this.selCatId, note: p.note.trim() || null }, brand));
        } else {
          await API.updateProduct(p.product_id, Object.assign(
            { name, note: p.note.trim() || null, active: p.active ? 1 : 0 }, brand));
        }
        this.prodPopup = null;
        await this.loadCategoryDetail();
      });
    },
    async toggleProductActive(p) {
      await this.guard(async () => {
        await API.updateProduct(p.product_id, { active: p.active ? 0 : 1 });
        await this.loadCategoryDetail();
      });
    },
    async deleteProduct(p) {
      if (!confirm(`確定刪除大產品「${p.name}」?刪除後無法復原。`)) return;
      await this.guard(async () => {
        await API.deleteProduct(p.product_id);
        await this.loadCategoryDetail();
      });
    },

    // ==== 通用清單維護(廠牌/手機品牌/型號)====
    _itemBody(kind, item) {
      const body = { name: (item.name || "").trim() };
      if (kind === "models") {
        body.alias = (item.alias || "").trim() || null;
        body.series = (item.series || "").trim() || null;
      }
      return body;
    },
    _takeSnap() {
      this._snap = {};
      for (const kind of Object.keys(_MAINT)) {
        const m = _MAINT[kind];
        for (const it of (this[kind] || []))
          this._snap[kind + ":" + it[m.id]] = JSON.stringify(this._itemBody(kind, it));
      }
    },
    async saveAll(kind) {
      const m = _MAINT[kind];
      await this.guard(async () => {
        for (const it of (this[kind] || [])) {
          const body = this._itemBody(kind, it);
          if (!body.name) { this.showError("名稱不可空白"); return; }
          if (this._snap[kind + ":" + it[m.id]] === JSON.stringify(body)) continue;
          await API[m.update](it[m.id], body);
        }
        await this.reloadAll();
      });
    },
    async toggleActive(kind, item) {
      const m = _MAINT[kind];
      await this.guard(async () => {
        await API[m.update](item[m.id], { active: item.active ? 0 : 1 });
        item.active = item.active ? 0 : 1;
      });
    },
    async deleteItem(kind, item) {
      const m = _MAINT[kind];
      if (!confirm(`確定刪除${m.label}「${item.name}」?刪除後無法復原。`)) return;
      await this.guard(async () => {
        await API[m.delete](item[m.id]);
        if (this.openBrand === item[m.id]) this.openBrand = null;
        await this.reloadAll();
      });
    },
    async saveSort(kind, ids) {
      const m = _MAINT[kind];
      await this.guardReload(() => API[m.sort](ids));
    },
    async _applyNewSeq(kind, list, newId) {
      const t = (this.newSeq[kind] || "").trim();
      this.newSeq[kind] = "";
      if (!/^[0-9]+$/.test(t) || !newId) return;
      const m = _MAINT[kind];
      const ids = list.map(x => x[m.id]).filter(x => x !== newId);
      const pos = Math.min(Math.max(parseInt(t, 10), 1), ids.length + 1);
      ids.splice(pos - 1, 0, newId);
      await API[m.sort](ids);
      await this.reloadAll();
    },
    async addItem(kind) {
      const m = _MAINT[kind];
      const name = (this.newItem[kind] || "").trim();
      if (!name) return;
      await this.guard(async () => {
        const r = await API[m.create]({ name });
        this.newItem[kind] = "";
        await this.reloadAll();
        await this._applyNewSeq(kind, this[kind], r[m.id]);
      });
    },
    async addModel() {
      const pbid = this.newModel.phone_brand_id, name = this.newModel.name.trim();
      if (!pbid || !name) { this.showError("請選擇手機品牌並輸入型號名稱"); return; }
      const series = (this.newModel.series || "").trim() || null;
      await this.guard(async () => {
        const r = await API.createModel({ phone_brand_id: pbid, name, series });
        this.newModel = { phone_brand_id: null, name: "", series: "" };
        await this.reloadAll();
        const grp = this.models.filter(m => m.phone_brand_id === pbid);
        await this._applyNewSeq("models", grp, r.model_id);
      });
    },

    // ==== 廠牌經營種類 ====
    async openBrandEditor(b) {
      if (this.openBrand === b.brand_id) { this.openBrand = null; return; }
      this.openBrand = b.brand_id; this.openBrandName = b.name;
      const checked = {};
      await this.guard(async () => {
        for (const c of this.categories) {
          const list = await API.listBrands({ category_id: c.category_id });
          if (list.some(x => x.brand_id === b.brand_id)) checked[c.category_id] = true;
        }
        this.brandCatChecked = checked;
      });
    },
    async toggleBrandCat(c) {
      const checked = Object.assign({}, this.brandCatChecked);
      checked[c.category_id] = !checked[c.category_id];
      const ids = this.categories.filter(x => checked[x.category_id]).map(x => x.category_id);
      await this.guard(async () => {
        await API.setBrandCategories(this.openBrand, ids);
        this.brandCatChecked = checked;
      });
    },
  },
};
