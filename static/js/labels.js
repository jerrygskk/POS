window.PosPages = window.PosPages || {};
window.PosPages["page-labels"] = {
  template: "#tpl-labels",
  inject: ["showError", "takePageContext"],
  data() { return { query: "", results: [], selected: null, copies: 1, doneMsg: "", searched: false }; },
  mounted() {
    const ctx = this.takePageContext?.();
    if (ctx?.variant) this.select(ctx.variant);
  },
  watch: {
    query() { this.searched = false; },
  },
  methods: {
    async search() {
      const query = this.query;
      await this.guard(async () => {
        if (!query.trim()) { this.results = []; return; }
        // 用 catalog.list：products.list 不回傳 barcodes,店內條碼會全部顯示成「尚無」。
        const products = await API.listCatalog({q: query.trim()});
        this.results = products.flatMap(
          p => (p.variants || []).map(v => Object.assign({}, v, {name: p.name})))
          .filter(row => this.storeBarcodes(row) !== "");
        if (this.query === query) this.searched = true;
      });
    },
    select(row) { this.selected = row; this.results = []; this.searched = false; },
    storeBarcodes(row) {
      const barcode = (row.barcodes || []).find(item => item.source === "store");
      return barcode ? barcode.barcode : "";
    },
    priceText(row) { return row.price == null ? "" : `$${row.price}`; },
    modelsTitle(row) { return (row.models || []).join("、") || null; },
    rowText(row) {
      // 空欄位不串進去,否則未定價的商品會留下連續兩個分隔線。
      return [row.name, this.attrText(row), (row.models || []).join("、"),
        this.priceText(row), this.storeBarcodes(row)]
        .filter(part => part !== "" && part != null).join("｜");
    },
    async print() {
      if (!this.selected) return;
      await this.guard(async () => {
        const copies = Math.max(1, Math.floor(this.copies) || 1);
        if (!await PosConfirm.ask(`確定列印 ${copies} 張標籤？`)) return;
        await API.printBarcode(this.selected.variant_id, copies);
        this.doneMsg = "標籤列印指令已送出。";
      });
    },
  },
};
