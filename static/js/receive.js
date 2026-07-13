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
      form: { name: "", price: null, category_id: null, brand_id: null,
              model_ids: [], attrs: {} },
    };
  },
  async mounted() {
    await this.guard(async () => {
      this.categories = await API.get("/api/categories");
      this.models = await API.get("/api/models");
    });
    this.$refs.scan.focus();
  },
  methods: {
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
      this.createdVid = null;
      this.brands = []; this.fields = []; this.fieldOptions = {};
      this.form = { name: "", price: null, category_id: null, brand_id: null,
                    model_ids: [], attrs: {} };
    },
    async onCategoryChange() {
      this.form.brand_id = null; this.form.attrs = {};
      this.brands = []; this.fields = []; this.fieldOptions = {};
      const cid = this.form.category_id;
      if (!cid) return;
      await this.guard(async () => {
        this.brands = await API.get("/api/brands?category_id=" + cid);
        this.fields = await API.get("/api/categories/" + cid + "/fields");
        // 先依欄型初始化屬性值(multi=陣列、tags=逗號字串、select 預設帶入),
        // 再載入選項:避免規格欄先於 attrs 就緒而讀到 undefined
        this.form.attrs = window.initFormAttrs(this.fields, {});
        // select/multi 欄選項另帶 model_ids(限定型號),供勾選框/下拉依已選型號過濾
        await window.CatalogFields.loadFieldsWithOptions(this.fields, this.fieldOptions);
      });
    },
    async createProduct() {
      if (!this.form.name.trim()) { this.showError("請輸入商品名稱"); return; }
      if (!this.form.category_id) { this.showError("請先選擇配件種類"); return; }
      await this.guard(async () => {
        await window.CatalogFields.ensureOptions(this.fields, this.form.attrs, this.fieldOptions);
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
      });
    },
    async genBarcode() {
      const r = await API.post(`/api/variants/${this.createdVid}/barcodes`,
                               { source: "store" });
      this.newBarcode = r.barcode;
    },
    async printBarcode() {
      // 現階段顯示 501 訊息
      await this.guard(() => API.post("/api/print/barcode",
        { barcode: this.newBarcode, name: this.form.name }));
    },
    async doReceive() {
      await this.guard(async () => {
        const r = await API.post("/api/stock/receive",
          { variant_id: this.hit.variant_id, qty: this.qty });
        this.hit.stock = r.stock; this.qty = 1;
        this.$refs.scan.focus();
      });
    },
  },
};
