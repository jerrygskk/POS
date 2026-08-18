window.PosPages = window.PosPages || {};

// 廠牌／手機品牌／型號沿用通用清單維護;種類與大產品改走商品設定單頁。
const _MAINT = {
  brands: { id: "brand_id", label: "廠牌", list: "listBrands", create: "createBrand", update: "updateBrand", delete: "deleteBrand", sort: "sortBrands" },
  phoneBrands: { id: "phone_brand_id", label: "手機品牌", list: "listPhoneBrands", create: "createPhoneBrand", update: "updatePhoneBrand", delete: "deletePhoneBrand", sort: "sortPhoneBrands" },
  models: { id: "model_id", label: "型號", list: "listModels", create: "createModel", update: "updateModel", delete: "deleteModel", sort: "sortModels" },
};

const _TYPE_LABEL = { select: "下拉選單", text: "文字", multi: "複選", tags: "特性詞條",
                      model: "手機型號" };
// 規格模板固定列:每個種類都有一列「手機型號」,點列切換該種類是否使用適用型號
// (讀寫 Category.model_mode)。型號實際存於 VariantModel 關聯表,不是規格欄,
// 故以固定列呈現而非真的建 AttributeField。
const MODEL_ROW_ID = "__model__";
// 新種類預設帶入的規格欄(既有全域欄位,不新建);與 lib/db_seed.NEW_CATEGORY_FIELDS 對齊
const DEFAULT_CATEGORY_FIELDS = ["顏色", "款式"];

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
      section: "category",
      selCatId: null, newCatName: "",
      tplFields: [], tplOptions: {}, catHasVariant: false, bigProducts: [],
      // 規格模板 popup(單層)
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
      const rows = this.tplFields.slice().sort((a, b) =>
        (a.sort - b.sort) || (a.field_id - b.field_id));
      if (!this.selectedCat) return rows;
      // 固定列排最前:手機型號(使用與否由 model_mode 決定)
      const on = this.selectedCat.model_mode === "required";
      rows.unshift({ field_id: MODEL_ROW_ID, name: "手機型號", field_type: "model",
                     required: on, cf_active: on, default_option_id: null, sort: -1 });
      return rows;
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
  async mounted() {
    this._loadSeq = 0;
    // 規格設定子視窗存檔後關窗:重讀該種類模板(沒存檔就關掉不白跑一次查詢)
    this._childWindowClosed = (event) => this.onChildWindowClosed(event);
    window.addEventListener("pos-child-window-closed", this._childWindowClosed);
    await this.reloadAll();
  },
  unmounted() {
    window.removeEventListener("pos-child-window-closed", this._childWindowClosed);
  },
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
    selectSection(name) {
      this.section = name;
      this.prodPopup = null;
      this.brandMenuOpen = false;
    },
    async selectCategory(c) {
      const same = this.selCatId === c.category_id;
      this.section = "category";
      this.prodPopup = null;
      this.brandMenuOpen = false;
      if (same) return;
      this.selCatId = c.category_id;
      await this.guard(() => this.loadCategoryDetail());
    },
    async loadCategoryDetail() {
      const seq = ++this._loadSeq;
      const cid = this.selCatId;
      const fields = await API.listFields({ category_id: cid });
      const options = {};
      for (const f of fields)
        if (["select", "multi", "tags"].includes(f.field_type))
          options[f.field_id] = await API.listOptions({ field_id: f.field_id, all: 1 });
      const products = await API.listCatalog({ category_id: cid, include_inactive: true });
      if (seq !== this._loadSeq) return;
      this.tplFields = fields;
      this.tplOptions = options;
      this.bigProducts = products;
      this.catHasVariant = products.some(p => (p.variants || []).length > 0);
    },
    async addCategory() {
      const name = (this.newCatName || "").trim();
      if (!name) { this.showError("請輸入商品種類名稱"); return; }
      await this.guard(async () => {
        const r = await API.createCategory({ name, model_mode: "hidden" });
        await this.attachDefaultFields(r.category_id);
        this.newCatName = "";
        this.selCatId = r.category_id;
        await this.reloadAll();
      });
    },
    // 新種類預設帶「顏色」「款式」兩個規格欄(選填):空白模板讓店員無從下手。
    // 掛的是既有全域欄位,不新建欄位;不需要的用模板列紅色 ✕ 移除。
    async attachDefaultFields(categoryId) {
      const all = await API.listFields({});
      let sort = 1;
      for (const name of DEFAULT_CATEGORY_FIELDS) {
        const field = all.find(f => f.name === name);
        if (!field) continue;
        await API.setCategoryField(categoryId, field.field_id,
                                   { sort: sort++, required: 0, active: 1 });
      }
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

    // ==== 商品設定:規格模板 ====
    fieldTypeLabel(t) { return _TYPE_LABEL[t] || t; },
    isFeature(f) { return f.field_type === "tags"; },
    isModelRow(f) { return f.field_id === MODEL_ROW_ID; },
    // 型號固定列顯示使用狀態,其餘欄位維持必填／選填
    templateRowState(f) {
      if (this.isModelRow(f)) return f.cf_active ? "使用" : "不使用";
      return f.required && !this.isFeature(f) ? "必填" : "選填";
    },
    // 點列:型號列切換使用與否,其餘照原本開規格編輯
    toggleModelMode() {
      const cat = this.selectedCat;
      if (!cat) return;
      this.setModelMode(cat, cat.model_mode === "required" ? "hidden" : "required");
    },
    // 紅色 ✕:把規格欄從此種類移除,並清掉此種類商品填過的值(欄位本身若沒人再用才一起刪)
    async deleteTemplateField(f) {
      const used = f.cat_usage_count || 0;
      const impact = used
        ? `此種類有 ${used} 筆商品填過此規格,一併刪除後無法復原。`
        : "此種類尚無商品使用此規格。";
      if (!confirm(`刪除規格「${f.name}」?\n${impact}`)) return;
      await this.guard(async () => {
        await API.deleteCategoryField(this.selCatId, f.field_id);
        await this.loadCategoryDetail();
      });
    },
    defaultValueName(f) {
      if (f.default_option_id == null) return "";
      const o = (this.tplOptions[f.field_id] || []).find(x => x.option_id === f.default_option_id);
      return o ? o.value : "";
    },
    // 規格設定改開 pywebview 子視窗(可拖、可縮):f 為 null＝新增。
    // 開窗前鎖主視窗,開窗失敗自己解鎖(成功時由關窗事件解鎖)。
    async openFieldPopup(f) {
      if (f && this.isFeature(f)) return;   // 特性詞條為固定欄,不進編輯
      if (f && this.isModelRow(f)) return;  // 手機型號為固定列,點列切換即可
      if (this.selCatId == null) return;
      window.PosDesktopLock.lock();
      try {
        await API.invoke("desktop.child_window.open", {
          page: "field_editor",
          title: f ? "修改規格" : "新增規格",
          context: {
            category_id: this.selCatId,
            field_id: f ? f.field_id : null,
            cat_has_variant: !!this.catHasVariant,
          },
        });
      } catch (error) {
        window.PosDesktopLock.unlock();
        this.showError(error.message);
      }
    },
    async onChildWindowClosed(event) {
      const saved = !!(event && event.detail && event.detail.saved);
      if (!saved || this.selCatId == null) return;
      await this.guard(() => this.loadCategoryDetail());
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
