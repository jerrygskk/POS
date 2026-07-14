window.PosPages = window.PosPages || {};
window.PosPages["page-checkout"] = {
  template: "#tpl-checkout",
  inject: ["showError"],
  data() {
    return { scanCode: "", searchQ: "", searchResults: [], cart: [],
             payments: [], payment: "現金", orderDiscount: 0, paid: 0, doneMsg: "" };
  },
  computed: {
    total() {
      const t = this.cart.reduce((s, i) => s + i.qty * i.unit_price - i.discount, 0)
        - this.orderDiscount;
      return Math.max(0, t);
    },
  },
  async mounted() {
    await this.guard(async () => {
      this.payments = await API.get("/api/payments");
      this.payment = this.payments[0];
    });
    this.$refs.scan?.focus();
    this._refocus = () => setTimeout(() => {
      if (document.activeElement.tagName !== "INPUT" &&
          document.activeElement.tagName !== "SELECT") this.$refs.scan?.focus();
    }, 300);
    document.addEventListener("click", this._refocus);
  },
  unmounted() { document.removeEventListener("click", this._refocus); },
  methods: {
    addItem(r) {
      let price = r.price;
      if (price === null) {
        const s = prompt(`「${r.name}」尚未定價,請輸入成交單價:`);
        if (s === null) return;
        price = parseInt(s, 10);
        if (isNaN(price) || price < 0) { this.showError("價格輸入不正確"); return; }
      }
      const dup = this.cart.find(i => i.variant_id === r.variant_id);
      if (dup) dup.qty += 1;
      else this.cart.push({ variant_id: r.variant_id, name: r.name,
        attributes: r.attributes, attr_display: r.attr_display,
        unit_price: price, qty: 1, discount: 0 });
      this.searchResults = [];
    },
    async onScan() {

      await this.guard(async () => {  // 查無條碼:保留輸入
        const query = await API.barcodeQuery(this.scanCode);
        if (!query) return;
        this.addItem(query.data);
        this.scanCode = "";
      });
    },
    async onSearch() {
      if (!this.searchQ.trim()) return;
      this.searchResults = await API.get("/api/products?q=" +
        encodeURIComponent(this.searchQ.trim()));
    },
    async checkout() {
      await this.guard(async () => {
        const r = await API.post("/api/sales", {
          payment: this.payment, order_discount: this.orderDiscount,
          paid: this.paid,
          items: this.cart.map(i => ({ variant_id: i.variant_id, qty: i.qty,
            unit_price: i.unit_price, discount: i.discount })) });
        this.doneMsg = `結帳完成,找零 ${r.change} 元(交易編號 ${r.sale_id})`;
        this.cart = []; this.orderDiscount = 0; this.paid = 0;
        setTimeout(() => this.doneMsg = "", 5000);
      });
    },
  },
};
