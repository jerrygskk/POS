window.PosPages = window.PosPages || {};
window.PosPages["page-receive"] = {
  template: "#tpl-receive",
  inject: ["showError", "goPage"],
  data() {
    return {
      scanCode: "", hit: null, qty: 1, notFound: null,
    };
  },
  async mounted() {
    this.$refs.scan.focus();
  },
  methods: {
    async onScan() {
      const code = String(this.scanCode || "").trim();
      if (!code) return;
      try {
        const query = await API.barcodeQuery(code);
        if (!query) return;
        this.hit = query.data;
        this.notFound = null;
        this.scanCode = "";
      } catch (e) {
        // 進貨不建立大產品(規格 §6.1):查無條碼一律引導至商品設定
        if (e.status === 404) { this.hit = null; this.notFound = code; this.scanCode = ""; }
        else this.showError(e.message);
      }
    },
    goSettings() { this.goPage("settings"); },
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
