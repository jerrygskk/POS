window.PosPages = window.PosPages || {};

// 依型號分組並附 rowspan 資訊的純函式。
// variants:變體陣列(v.models 已為別名字串、依型號 sort 序排好)。
// modelOrder:{ 型號別名 → 全域排序索引 },用於群組間排序;撈不到則以名稱排序。
// editId:目前行內編輯中的 variant_id(可為 null),編輯列強制自成一段以保有型號欄位置。
// 回傳列陣列,每列 { v, label, models, showModel, rowspan }。
window.groupVariantsByModel = function (variants, modelOrder, editId) {
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
  // 攤平成列;編輯中的變體自成一段(rowspan=1),其餘連續列合併
  const out = [];
  for (const g of groups) {
    let i = 0;
    while (i < g.rows.length) {
      const cur = g.rows[i];
      if (editId != null && cur.variant_id === editId) {
        out.push({ v: cur, label: g.label, models: g.models, showModel: true, rowspan: 1 });
        i++;
        continue;
      }
      let j = i;
      while (j < g.rows.length &&
             !(editId != null && g.rows[j].variant_id === editId)) j++;
      const run = j - i;
      for (let k = i; k < j; k++) {
        out.push({ v: g.rows[k], label: g.label, models: g.models,
                   showModel: k === i, rowspan: k === i ? run : 0 });
      }
      i = j;
    }
  }
  return out;
};

window.PosPages["page-catalog"] = {
  template: "#tpl-catalog",
  inject: ["showError"],
  data() {
    return {
      q: "", includeInactive: false,
      fCategory: null, fBrand: null, fModel: null,
      categories: [], brands: [], models: [],
      products: [], fieldsByCat: {}, fieldOptions: {},
      expanded: {}, bcInput: {}, bcError: {},
      editProduct: null, editVariant: null,
      addingFor: null, newVariant: { attrs: {}, price: null, barcode: "", model_ids: [] },
    };
  },
  computed: {
    modelOrder() {
      // 型號別名 → 全域排序索引(this.models 已依 pb.sort, m.sort 排好)
      const o = {};
      this.models.forEach((m, i) => { o[m.alias || m.name] = i; });
      return o;
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
    // 只重撈資料,不動編輯狀態(條碼即時新增/刪除用,避免把使用者踢出編輯)
    async refresh() {
      try {
        let url = "/api/catalog?q=" + encodeURIComponent(this.q);
        if (this.includeInactive) url += "&include_inactive=1";
        if (this.fCategory) url += "&category_id=" + this.fCategory;
        if (this.fBrand) url += "&brand_id=" + this.fBrand;
        if (this.fModel) url += "&model_id=" + this.fModel;
        this.products = await API.get(url);
      } catch (e) { this.showError(e.message); }
    },
    async reload() {
      await this.refresh();
      this.editProduct = null; this.editVariant = null; this.addingFor = null;
    },
    toggleExpand(pid) { this.expanded[pid] = !this.expanded[pid]; },
    groupedVariants(p) {
      const editId = this.editVariant ? this.editVariant.variant_id : null;
      return window.groupVariantsByModel(p.variants, this.modelOrder, editId);
    },

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
      // 依顯示名稱反查 model_id:後端回傳的 v.models 是別名(無別名=全名),
      // 須先比對別名再比對全名,否則有別名的型號反查不到,存檔會清空綁定
      const ids = [];
      for (const n of cat_names) {
        const m = this.models.find(x => (x.alias || x.name) === n)
              || this.models.find(x => x.name === n);
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
          default_price: e.default_price === "" ? null : (e.default_price ?? null),
          note: e.note.trim() || null });
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

    // 詞條(tags)chip:attrs[f.name] 為逗號字串,切換僅增刪 token,進出格式不變
    tagList(str) {
      return String(str || "").split(/[,、，]/).map(s => s.trim()).filter(Boolean);
    },
    tagHas(str, val) { return this.tagList(str).includes(val); },
    toggleTag(obj, fname, val) {
      const arr = this.tagList(obj[fname]);
      const i = arr.indexOf(val);
      if (i >= 0) arr.splice(i, 1); else arr.push(val);
      obj[fname] = arr.join(", ");
    },

    // 變體編輯
    async startEditVariant(p, v) {
      this.editProduct = null;
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
          price: e.price === "" ? null : (e.price ?? null) });
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
        await this.refresh();
      } catch (e) { this.showError(e.message); }
    },
    async addFactoryBarcode(p, v) {
      const code = (this.bcInput[v.variant_id] || "").trim();
      if (!code) { this.bcError[v.variant_id] = "請輸入原廠條碼"; return; }
      if (code.toUpperCase().startsWith("TL")) {
        this.bcError[v.variant_id] = "TL 開頭為系統保留，如有需求請按自取條碼";
        return;
      }
      try {
        await API.post("/api/variants/" + v.variant_id + "/barcodes",
                       { barcode: code, source: "factory" });
        this.bcInput[v.variant_id] = "";
        this.bcError[v.variant_id] = "";
        await this.refresh();
      } catch (e) { this.bcError[v.variant_id] = e.message; }
    },
    async addStoreBarcode(p, v) {
      try {
        await API.post("/api/variants/" + v.variant_id + "/barcodes",
                       { source: "store" });
        await this.refresh();
      } catch (e) { this.showError(e.message); }
    },

    // 新增變體
    async startAddVariant(p) {
      await this.ensureFields(p.category_id);
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
          price: n.price === "" ? null : (n.price ?? null),
          model_ids: n.model_ids, barcodes });
        await this.reload();
      } catch (e) { this.showError(e.message); }
    },
  },
};
