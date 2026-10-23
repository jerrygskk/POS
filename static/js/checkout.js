window.PosPages = window.PosPages || {};
// 切頁會卸載收銀元件;購物車放元件外才撐得過切頁。只活在本次開啟程式期間,
// 不落地——關閉程式後沒結帳的單本來就不該留著。
window.PosCheckoutDraft = Vue.reactive({ cart: [], orderDiscount: 0, submitting: false });
window.PosPages["page-checkout"] = {
  template: "#tpl-checkout",
  inject: ["showError"],
  data() {
    return { scanCode: "", searchQ: "", searchResults: [], draft: window.PosCheckoutDraft,
             payments: [], payment: "現金", paid: null, doneMsg: "",
             employee: window.PosEmployee };
  },
  computed: {
    total() {
      const t = this.draft.cart.reduce((s, i) => s + i.qty * i.unit_price - i.discount, 0)
        - this.draft.orderDiscount;
      return Math.max(0, t);
    },
    // 目前只記錄賣了什麼、不實際收款:實收留白視為剛好付清
    paidBlank() { return this.paid === null || this.paid === ""; },
    paidAmount() { return this.paidBlank ? this.total : this.paid; },
  },
  async mounted() {
    await this.guard(async () => {
      this.payments = await API.listPayments();
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
    async addItem(r) {
      let price = r.price;
      if (price === null) {
        const s = await PosConfirm.input({
          title: "未定價商品", message: `「${r.name}」尚未定價，請輸入成交單價：`,
          inputType: "number",
          validate: value => /^\d+$/.test(value) ? null : "價格必須是非負整數",
        });
        if (s === null) return;
        price = Number(s);
      }
      const dup = this.draft.cart.find(i => i.variant_id === r.variant_id);
      if (dup) dup.qty += 1;
      else this.draft.cart.push({ variant_id: r.variant_id, name: r.name,
        attributes: r.attributes, attr_display: r.attr_display,
        label_barcode: r.label_barcode,
        unit_price: price, qty: 1, discount: 0, stock: r.stock });
      this.searchResults = [];
    },
    async onScan() {

      await this.guard(async () => {  // 查無條碼:保留輸入
        const query = await window.PosScan(this.scanCode, { sellableOnly: true });
        if (!query) { this.scanCode = ""; return; }
        await this.addItem(query.data);
        this.scanCode = "";
      });
    },
    async onSearch() {
      if (!this.searchQ.trim()) return;
      this.searchResults = await API.listProducts({q: this.searchQ.trim()});
    },
    async checkout() {
      if (this.draft.submitting) return;
      if (!this.employee.id) { this.showError("請先選擇員工代號"); return; }
      this.draft.submitting = true;
      try {
        await this.guard(async () => {
          const r = await API.checkout({
            payment: this.payment, order_discount: this.draft.orderDiscount,
            paid: this.paidAmount, employee: this.employee.id,
            items: this.draft.cart.map(i => ({ variant_id: i.variant_id, qty: i.qty,
              unit_price: i.unit_price, discount: i.discount })) });
          this.doneMsg = this.paidBlank
            ? `結帳完成(交易編號 ${r.sale_id})`
            : `結帳完成，找零 ${r.change} 元(交易編號 ${r.sale_id})`;
          this.draft.cart = []; this.draft.orderDiscount = 0; this.paid = null;
          setTimeout(() => this.doneMsg = "", 5000);
        });
      } finally {
        this.draft.submitting = false;
      }
    },
  },
};
