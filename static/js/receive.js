window.PosPages = window.PosPages || {};
window.PosPages["page-receive"] = {
  template: "#tpl-receive",
  inject: ["showError"],
  data() {
    return {
      scanCode: "", hit: null, qty: 1, creating: false, newBarcode: "",
      createdVid: null,
      categories: [], brands: [], models: [], fields: [],
      fieldOptions: {},
      modelSearch: "",
      form: { name: "", price: null, category_id: null, brand_id: null,
              model_ids: [], attrs: {} },
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
      this.models = await API.get("/api/models");
    } catch (e) { this.showError(e.message); }
    this.$refs.scan.focus();
  },
  methods: {
    attrText(row) { return window.fmtAttr(row); },
    async onScan() {
      const code = this.scanCode.trim();
      if (!code) return;
      try {
        this.hit = await API.get("/api/barcode/" + encodeURIComponent(code));
        this.creating = false; this.scanCode = "";
      } catch (e) { this.startCreate(code); }  // 查無 → 建檔,條碼帶入
    },
    startCreate(code) {
      this.hit = null; this.creating = true; this.newBarcode = code;
      this.createdVid = null; this.modelSearch = "";
      this.brands = []; this.fields = []; this.fieldOptions = {};
      this.form = { name: "", price: null, category_id: null, brand_id: null,
                    model_ids: [], attrs: {} };
    },
    async onCategoryChange() {
      this.form.brand_id = null; this.form.attrs = {};
      this.brands = []; this.fields = []; this.fieldOptions = {};
      const cid = this.form.category_id;
      if (!cid) return;
      try {
        this.brands = await API.get("/api/brands?category_id=" + cid);
        this.fields = await API.get("/api/categories/" + cid + "/fields");
        // select/multi 欄選項另帶 model_ids(限定型號),供勾選框/下拉依已選型號過濾
        for (const f of this.fields)
          if (f.field_type === "select" || f.field_type === "multi")
            this.fieldOptions[f.field_id] =
              await API.get("/api/options?field_id=" + f.field_id);
        // 依欄型初始化屬性值(multi=陣列、tags=逗號字串、select 預設帶入)
        this.form.attrs = window.initFormAttrs(this.fields, {});
      } catch (e) { this.showError(e.message); }
    },
    optionsFor(f) {
      // 依「目前已選型號」過濾:未綁型號的選項恆顯示;綁定含任一已選型號者顯示。
      // 未選任何型號=顯示全部。
      const list = this.fieldOptions[f.field_id] || [];
      const sel = this.form.model_ids;
      if (!sel.length) return list;
      return list.filter(o =>
        !o.model_ids.length || o.model_ids.some(id => sel.includes(id)));
    },
    async ensureOptions() {
      // 手打自增:select 欄位輸入不存在的值,建檔時自動入庫
      for (const f of this.fields) {
        if (f.field_type !== "select") continue;
        const v = (this.form.attrs[f.name] || "").trim();
        if (!v) continue;
        const list = this.fieldOptions[f.field_id] || [];
        if (list.some(o => o.value === v)) continue;
        try {
          await API.post("/api/options", { field_id: f.field_id, value: v });
          this.fieldOptions[f.field_id] =
            await API.get("/api/options?field_id=" + f.field_id);
        } catch (e) { /* 冪等,忽略 */ }
      }
    },
    async createProduct() {
      if (!this.form.name.trim()) { this.showError("請輸入商品名稱"); return; }
      if (!this.form.category_id) { this.showError("請先選擇配件種類"); return; }
      try {
        await this.ensureOptions();
        const attrs = window.buildAttrPayload(this.fields, this.form.attrs);
        const barcodes = this.newBarcode
          ? [{ barcode: this.newBarcode, source: "factory" }] : [];
        const r = await API.post("/api/products", { name: this.form.name.trim(),
          category_id: this.form.category_id, brand_id: this.form.brand_id,
          default_price: this.form.price ?? null,
          variants: [{ attributes: attrs, model_ids: this.form.model_ids,
                       barcodes }] });
        this.createdVid = r.variant_ids[0];
        this.hit = this.newBarcode
          ? await API.get("/api/barcode/" + encodeURIComponent(this.newBarcode))
          : { name: this.form.name, attributes: attrs, stock: 0,
              variant_id: this.createdVid };
      } catch (e) { this.showError(e.message); }
    },
    async genBarcode() {
      const r = await API.post(`/api/variants/${this.createdVid}/barcodes`,
                               { source: "store" });
      this.newBarcode = r.barcode;
    },
    async printBarcode() {
      try {
        await API.post("/api/print/barcode",
          { barcode: this.newBarcode, name: this.form.name });
      } catch (e) { this.showError(e.message); }  // 現階段顯示 501 訊息
    },
    async doReceive() {
      try {
        const r = await API.post("/api/stock/receive",
          { variant_id: this.hit.variant_id, qty: this.qty });
        this.hit.stock = r.stock; this.qty = 1;
        this.$refs.scan.focus();
      } catch (e) { this.showError(e.message); }
    },
  },
};
