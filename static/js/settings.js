window.PosPages = window.PosPages || {};

const _MAINT = {
  categories: { url: "/api/categories", id: "category_id", label: "種類" },
  brands: { url: "/api/brands", id: "brand_id", label: "廠牌" },
  models: { url: "/api/models", id: "model_id", label: "型號" },
  phoneBrands: { url: "/api/phone-brands", id: "phone_brand_id", label: "手機品牌" },
};

window.PosPages["page-settings"] = {
  template: "#tpl-settings",
  inject: ["showError"],
  data() {
    return {
      categories: [], brands: [], models: [], phoneBrands: [],
      newItem: { categories: "", brands: "", phoneBrands: "" },
      newModel: { phone_brand_id: null, name: "", series: "" },
      newSeq: { categories: "", brands: "", phoneBrands: "", models: "" },
      // 種類規格欄設定
      openCat: null, openCatName: "",
      catFields: [], catOptions: {}, newField: { name: "", field_type: "select" },
      newOption: {}, sharedFields: [], enabledShared: {},
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
  },
  async mounted() { await this.reloadAll(); },
  methods: {
    async reloadAll() {
      await this.guard(async () => {
        this.categories = await API.get("/api/categories?all=1");
        this.brands = await API.get("/api/brands?all=1");
        this.phoneBrands = await API.get("/api/phone-brands?all=1");
        this.models = await API.get("/api/models?all=1");
        this._takeSnap();
      });
    },

    // ---- 通用清單維護(種類/廠牌/型號)----
    _itemBody(kind, item) {
      const body = { name: (item.name || "").trim() };
      if (kind === "models") {  // 型號同時儲存顯示別名(空=顯示全名)與系列
        body.alias = (item.alias || "").trim() || null;
        body.series = (item.series || "").trim() || null;
      }
      return body;
    },
    _takeSnap() {
      // 各清單載入時留快照,「儲存修改」只送有變動的列
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
          await API.patch(m.url + "/" + it[m.id], body);
        }
        await this.reloadAll();
      });
    },
    async toggleActive(kind, item) {
      const m = _MAINT[kind];
      await this.guard(async () => {
        await API.patch(m.url + "/" + item[m.id], { active: item.active ? 0 : 1 });
        item.active = item.active ? 0 : 1;
      });
    },
    async deleteItem(kind, item) {
      const m = _MAINT[kind];
      if (!confirm(`確定刪除${m.label}「${item.name}」?刪除後無法復原。`)) return;
      // 409:已有商品使用,無法刪除,可改為停用
      await this.guard(async () => {
        await API.del(m.url + "/" + item[m.id]);
        if (this.openCat === item[m.id]) this.openCat = null;
        if (this.openBrand === item[m.id]) this.openBrand = null;
        await this.reloadAll();
      });
    },
    async saveSort(kind, ids) {
      const m = _MAINT[kind];
      await this.guardReload(() => API.put(m.url + "/sort", { ids }));
    },
    // 新增時指定序號:先照舊排最後,再把新 id 搬到第 n 位重寫排序(超界夾到 1..N)
    async _applyNewSeq(kind, list, newId) {
      const t = (this.newSeq[kind] || "").trim();
      this.newSeq[kind] = "";
      if (!/^[0-9]+$/.test(t) || !newId) return;
      const m = _MAINT[kind];
      const ids = list.map(x => x[m.id]).filter(x => x !== newId);
      const pos = Math.min(Math.max(parseInt(t, 10), 1), ids.length + 1);
      ids.splice(pos - 1, 0, newId);
      await API.put(m.url + "/sort", { ids });
      await this.reloadAll();
    },

    // 種類/廠牌/手機品牌新增共用一支(型號 addModel 為特例另留)
    async addItem(kind) {
      const m = _MAINT[kind];
      const name = (this.newItem[kind] || "").trim();
      if (!name) return;
      await this.guard(async () => {
        const r = await API.post(m.url, { name });
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
        const r = await API.post("/api/models", { phone_brand_id: pbid, name, series });
        this.newModel = { phone_brand_id: null, name: "", series: "" };
        await this.reloadAll();
        const grp = this.models.filter(m => m.phone_brand_id === pbid);
        await this._applyNewSeq("models", grp, r.model_id);
      });
    },

    // ---- 種類規格欄設定 ----
    async openCategory(c) {
      if (this.openCat === c.category_id) { this.openCat = null; return; }
      this.openBrand = null;
      this.openCat = c.category_id; this.openCatName = c.name;
      this.newField = { name: "", field_type: "select" };
      await this.guard(async () => {
        this.catFields = await API.get("/api/fields?category_id=" + c.category_id);
        this._fieldSnap = {};
        for (const f of this.catFields) this._fieldSnap[f.field_id] = f.name;
        // 設定頁只顯示啟用選項；已刪除但仍被舊商品使用者保持隱藏
        this.catOptions = {};
        await window.CatalogFields.loadFieldsWithOptions(this.catFields, this.catOptions,
          { types: ["select", "multi", "tags"] });
        this.sharedFields = await API.get("/api/fields?common=1");
        const merged = await API.get("/api/categories/" + c.category_id + "/fields");
        const en = {};
        for (const f of merged) if (f.shared) en[f.field_id] = true;
        this.enabledShared = en;
      });
    },
    async loadFieldOptions(f) {
      this.catOptions[f.field_id] = await API.get("/api/options?field_id=" + f.field_id);
    },
    fieldTypeLabel(t) {
      return { select: "下拉選單", text: "文字", multi: "複選",
               tags: "特性詞條" }[t] || t;
    },
    async setDefaultOption(f, val) {
      const oid = val === "" ? null : parseInt(val, 10);
      await this.guard(async () => {
        await API.put("/api/fields/" + f.field_id, { default_option_id: oid });
        f.default_option_id = oid;
      });
    },
    async addField() {
      const name = this.newField.name.trim();
      if (!name) { this.showError("請輸入規格欄名稱"); return; }
      await this.guard(async () => {
        await API.post("/api/fields", { name, category_id: this.openCat,
          field_type: this.newField.field_type });
        this.newField = { name: "", field_type: "select" };
        this.catFields = await API.get("/api/fields?category_id=" + this.openCat);
        for (const f of this.catFields) this._fieldSnap[f.field_id] = f.name;
      });
    },
    async saveAllFields() {
      await this.guard(async () => {
        for (const f of this.catFields) {
          const name = (f.name || "").trim();
          if (!name) { this.showError("名稱不可空白"); return; }
          if (this._fieldSnap[f.field_id] === name) continue;
          await API.put("/api/fields/" + f.field_id, { name });
          this._fieldSnap[f.field_id] = name;
        }
      });
    },
    async deleteField(f) {
      if (!confirm(`確定刪除規格欄「${f.name}」?`)) return;
      await this.guard(async () => {
        await API.put("/api/fields/" + f.field_id, { active: 0 });
        this.catFields = await API.get("/api/fields?category_id=" + this.openCat);
      });
    },
    async addOption(f) {
      const v = (this.newOption[f.field_id] || "").trim();
      if (!v) return;
      await this.guard(async () => {
        await API.post("/api/options", {
          field_id: f.field_id, value: v, reactivate: true,
        });
        this.newOption[f.field_id] = "";
        await this.loadFieldOptions(f);
      });
    },
    async deleteOption(f, o) {
      const message = o.usage_count > 0
        ? `此選項有 ${o.usage_count} 個商品規格使用中。刪除後將從新增選單隱藏，既有商品不受影響。確定刪除？`
        : "此選項目前未使用，刪除後將永久移除且無法復原。確定刪除？";
      if (!confirm(message)) return;
      await this.guard(async () => {
        await API.del("/api/options/" + o.option_id);
        if (f.default_option_id === o.option_id) f.default_option_id = null;
        await this.loadFieldOptions(f);
      });
    },
    async toggleShared(sf) {
      const en = Object.assign({}, this.enabledShared);
      en[sf.field_id] = !en[sf.field_id];
      const ids = this.sharedFields.filter(x => en[x.field_id]).map(x => x.field_id);
      await this.guard(async () => {
        await API.put("/api/categories/" + this.openCat + "/fields-common",
          { field_ids: ids });
        this.enabledShared = en;
      });
    },

    // ---- 廠牌經營種類 ----
    async openBrandEditor(b) {
      if (this.openBrand === b.brand_id) { this.openBrand = null; return; }
      this.openCat = null;
      this.openBrand = b.brand_id; this.openBrandName = b.name;
      const checked = {};
      await this.guard(async () => {
        for (const c of this.categories) {
          const list = await API.get("/api/brands?category_id=" + c.category_id);
          if (list.some(x => x.brand_id === b.brand_id)) checked[c.category_id] = true;
        }
        this.brandCatChecked = checked;
      });
    },
    async toggleBrandCat(c) {
      const checked = Object.assign({}, this.brandCatChecked);
      checked[c.category_id] = !checked[c.category_id];
      const ids = this.categories.filter(x => checked[x.category_id])
        .map(x => x.category_id);
      await this.guard(async () => {
        await API.put("/api/brands/" + this.openBrand + "/categories",
          { category_ids: ids });
        this.brandCatChecked = checked;
      });
    },
  },
};
