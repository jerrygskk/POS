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
      this.categories = await API.listCategories({});
      this.models = await API.listModels({});
    });
    this.$refs.scan.focus();
  },
  methods: {
    async onScan() {
      const code = String(this.scanCode || "").trim();
      try {
        const query = await API.barcodeQuery(code);
        if (!query) return;
        this.hit = query.data;
        this.creating = false; this.scanCode = "";
      } catch (e) {
        if (e.status === 404) this.startCreate(code);
        else this.showError(e.message);
      }
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
        this.brands = await API.listBrands({category_id: cid});
        this.fields = await API.categoryFields(cid);
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
        const r = await API.createProduct({ name: this.form.name.trim(),
          category_id: this.form.category_id, brand_id: this.form.brand_id,
          default_price: this.form.price ?? null,
          variants: [{ attributes: attrs, model_ids: this.form.model_ids,
                       barcodes }] });
        this.createdVid = r.variant_ids[0];
        const query = await API.barcodeQuery(this.newBarcode);
        this.hit = query
          ? query.data
          : { name: this.form.name, attributes: attrs, stock: 0,
              variant_id: this.createdVid };
      });
    },
    async genBarcode() {
      const r = await API.addBarcode({variant_id: this.createdVid, source: "store"});
      this.newBarcode = r.barcode;
    },
    async printBarcode() {
      await this.guard(() => API.printBarcode(this.createdVid));
    },
    async doReceive() {
      await this.guard(async () => {
        const r = await API.receiveStock(
          { variant_id: this.hit.variant_id, qty: this.qty });
        this.hit.stock = r.stock; this.qty = 1;
        this.$refs.scan.focus();
      });
    },
  },
};
