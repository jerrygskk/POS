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
  inject: ["showError", "goPage"],
  data() {
    return {
      q: "", includeInactive: false,
      fCategory: null, fBrand: null, fModel: null,
      categories: [], brands: [], models: [],
      products: [], fieldsByCat: {}, fieldOptions: {},
      expanded: {}, bcInput: {}, bcError: {},
      editVariant: null,
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
    // ESC 取消行內編輯(子產品),不存檔
    this._escHandler = (ev) => {
      if (ev.key !== "Escape") return;
      if (this.editVariant) this.editVariant = null;
    };
    document.addEventListener("keydown", this._escHandler);
    await this.guard(async () => {
      this.categories = await API.listCategories({});
      this.brands = await API.listBrands({});
      this.models = await API.listModels({});
    });
    await this.reload();
  },
  unmounted() {
    document.removeEventListener("keydown", this._escHandler);
  },
  methods: {
    // 只重撈資料,不動編輯狀態(條碼即時新增/刪除用,避免把使用者踢出編輯)
    async refresh() {
      await this.guard(async () => {
        this.products = await API.listCatalog({q: this.q,
          include_inactive: this.includeInactive, category_id: this.fCategory,
          brand_id: this.fBrand, model_id: this.fModel});
      });
    },
    async reload() {
      await this.refresh();
      this.editVariant = null; this.addingFor = null;
    },
    editInSettings() { this.goPage("settings"); },
    toggleExpand(pid) { this.expanded[pid] = !this.expanded[pid]; },
    groupedVariants(p) {
      const editId = this.editVariant ? this.editVariant.variant_id : null;
      return window.groupVariantsByModel(p.variants, this.modelOrder, editId);
    },

    async ensureFields(cid) {
      if (cid == null || this.fieldsByCat[cid]) return;
      await this.guard(async () => {
        const fields = await API.categoryFields(cid);
        this.fieldsByCat[cid] = fields;
        // select/multi 欄選項另帶 model_ids(限定型號),供勾選框/下拉依適用型號過濾
        await window.CatalogFields.loadFieldsWithOptions(fields, this.fieldOptions);
      });
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

    async toggleProductActive(p) {
      await this.guardReload(() =>
        API.updateProduct(p.product_id, { active: p.active ? 0 : 1 }));
    },
    async deleteProduct(p) {
      if (!confirm(`確定刪除商品「${p.name}」?刪除後無法復原。`)) return;
      await this.guardReload(() => API.deleteProduct(p.product_id));
    },

    // 變體編輯
    async startEditVariant(p, v) {
      await this.ensureFields(p.category_id);
      this.editVariant = { variant_id: v.variant_id, price: v.price,
        attrs: window.initFormAttrs(this.fieldsByCat[p.category_id], v.attributes),
        model_ids: this.modelIdsByNames(v.models || []),
        _cat: p.category_id };
    },
    async saveVariant() {
      const e = this.editVariant;
      try {
        await window.CatalogFields.ensureOptions(
          this.fieldsByCat[e._cat] || [], e.attrs, this.fieldOptions);
        await API.updateVariantDetails(e.variant_id, {
          attributes: window.buildAttrPayload(this.fieldsByCat[e._cat], e.attrs),
          price: e.price === "" ? null : (e.price ?? null)
        }, e.model_ids);
        await this.reload();
      } catch (err) {
        this.showError(err.message);
        // 雙寫可能部分成功,重新載入拉回後端真實狀態,避免畫面停在舊資料
        await this.reload();
      }
    },
    async toggleVariantActive(p, v) {
      await this.guardReload(() =>
        API.updateVariant(v.variant_id, { active: v.active ? 0 : 1 }));
    },
    async deleteVariant(p, v) {
      if (!confirm("確定刪除此子產品?刪除後無法復原。")) return;
      await this.guardReload(() => API.deleteVariant(v.variant_id));
    },

    // 條碼(瀏覽只顯示一條:優先原廠碼,其次自取碼;管理進編輯)
    mainBarcode(v) {
      if (!v.barcodes || !v.barcodes.length) return null;
      return v.barcodes.find(b => b.source === "factory") || v.barcodes[0];
    },
    async removeBarcode(p, code) {
      if (!confirm(`確定移除條碼「${code}」?`)) return;
      await this.guard(async () => {
        await API.deleteBarcode(code);
        await this.refresh();
      });
    },
    async addFactoryBarcode(p, v) {
      const code = (this.bcInput[v.variant_id] || "").trim();
      if (!code) { this.bcError[v.variant_id] = "請輸入原廠條碼"; return; }
      if (code.toUpperCase().startsWith("TL")) {
        this.bcError[v.variant_id] = "TL 開頭為系統保留，如有需求請按自取";
        return;
      }
      try {
        await API.addBarcode({variant_id: v.variant_id,
                              barcode: code, source: "factory"});
        this.bcInput[v.variant_id] = "";
        this.bcError[v.variant_id] = "";
        await this.refresh();
      } catch (e) { this.bcError[v.variant_id] = e.message; }
    },
    async addStoreBarcode(p, v) {
      await this.guard(async () => {
        await API.addBarcode({variant_id: v.variant_id, source: "store"});
        await this.refresh();
      });
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
      await this.guardReload(async () => {
        await window.CatalogFields.ensureOptions(
          this.fieldsByCat[p.category_id] || [], n.attrs, this.fieldOptions);
        await API.createVariant(p.product_id, {
          attributes: window.buildAttrPayload(this.fieldsByCat[p.category_id], n.attrs),
          price: n.price === "" ? null : (n.price ?? null),
          model_ids: n.model_ids, barcodes });
      });
    },
  },
};
