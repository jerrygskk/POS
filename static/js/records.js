window.PosPages = window.PosPages || {};
window.PosPages["page-records"] = {
  template: "#tpl-records",
  inject: ["showError"],
  data() {
    const today = new Date().toISOString().slice(0, 10);
    return { kinds: [["sales", "收銀"], ["receive", "進貨"], ["stocktake", "盤點"]], kind: "sales",
             dateFrom: today, dateTo: today, payment: "", payments: [],
             sales: [], summary: null, receipts: [], stocktakes: [], expanded: null };
  },
  computed: { isAdmin() { return window.PosAuth.isAdmin; } },
  async mounted() {
    await this.guard(async () => {
      this.payments = await API.listPayments();
      await this.reload();
    });
  },
  methods: {
    async selectKind(kind) {
      if (this.kind === kind) return;
      this.kind = kind; this.expanded = null;
      await this.reload();
    },
    toggle(key) { this.expanded = this.expanded === key ? null : key; },
    async reload() {
      await this.guard(async () => {
        const range = {date_from: this.dateFrom, date_to: this.dateTo};
        if (this.kind === "sales") {
          const filters = {...range, payment: this.payment || ""};
          this.sales = await API.listSales(filters);
          this.summary = await API.salesSummary(filters);
        } else if (this.kind === "receive") {
          this.receipts = await API.listReceipts(range);
        } else {
          this.stocktakes = await API.stocktakeHistory(range);
        }
      });
    },
    async exportCsv() {
      if (!this.isAdmin) { this.showError("需要管理者權限"); return; }
      await this.guard(async () => {
        await API.exportSales({ date_from: this.dateFrom, date_to: this.dateTo,
          payment: this.payment || "" });
      });
    },
  },
};
