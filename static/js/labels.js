window.PosPages = window.PosPages || {};
window.PosPages["page-labels"] = {
  template: "#tpl-labels",
  inject: ["showError"],
  data() { return { query: "", results: [], selected: null, copies: 1, doneMsg: "" }; },
  methods: {
    async search() {
      await this.guard(async () => {
        if (!this.query.trim()) { this.results = []; return; }
        // 用 catalog.list：products.list 不回傳 barcodes,店內條碼會全部顯示成「尚無」。
        const products = await API.listCatalog({q: this.query.trim()});
        this.results = products.flatMap(
          p => (p.variants || []).map(v => Object.assign({}, v, {name: p.name})));
      });
    },
    select(row) { this.selected = row; this.results = []; },
    storeBarcodes(row) {
      const barcode = (row.barcodes || []).find(item => item.source === "store");
      return barcode ? barcode.barcode : "";
    },
    priceText(row) { return row.price == null ? "" : `$${row.price}`; },
    rowText(row) {
      // 空欄位不串進去,否則未定價的商品會留下連續兩個分隔線。
      return [row.name, this.attrText(row), (row.models || []).join("、"),
        this.priceText(row), this.storeBarcodes(row) || "尚無店內條碼"]
        .filter(part => part !== "" && part != null).join("｜");
    },
    async print() {
      if (!this.selected) return;
      await this.guard(async () => {
        const copies = Math.max(1, Math.floor(this.copies) || 1);
        if (!confirm(`確定列印 ${copies} 張標籤？`)) return;
        await API.printBarcode(this.selected.variant_id, copies);
        this.doneMsg = "標籤列印指令已送出。";
      });
    },
  },
};
