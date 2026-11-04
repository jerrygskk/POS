window.PosPages = window.PosPages || {};
window.PosPages["page-records"] = {
  template: "#tpl-records",
  inject: ["showError"],
  data() {
    // 用本機日期;toISOString 是 UTC,台灣早上 8 點前會變成昨天
    const now = new Date();
    const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
    return { kinds: [["sales", "收銀"], ["receive", "進貨"], ["stocktake", "盤點"], ["returns", "退貨"]], kind: "sales",
             dateFrom: today, dateTo: today, payment: "", payments: [],
             sales: [], summary: null, receipts: [], stocktakes: [], returns: [], expanded: new Set() };
  },
  computed: {
    isAdmin() { return window.PosAuth.isAdmin; },
    // 目前類別每一列的開合鍵,與範本的 isOpen／toggle 共用
    rowKeys() {
      if (this.kind === "sales") return this.sales.map(s => s.sale_id);
      if (this.kind === "receive") return this.receipts.map((_, i) => i);
      if (this.kind === "returns") return this.returns.map(t => t.return_id);
      return this.stocktakes.map(t => t.session_id);
    },
  },
  async mounted() {
    await this.guard(async () => {
      this.payments = await API.listPayments();
      await this.reload();
    });
  },
  methods: {
    async selectKind(kind) {
      if (this.kind === kind) return;
      this.kind = kind;
      await this.reload();
    },
    isOpen(key) { return this.expanded.has(key); },
    toggle(key) {
      const next = new Set(this.expanded);
      if (!next.delete(key)) next.add(key);
      this.expanded = next;
    },
    expandAll() { this.expanded = new Set(this.rowKeys); },
    collapseAll() { this.expanded = new Set(); },
    async reload() {
      // 查詢後資料可能換了;進貨舊紀錄只能用列序當鍵,保留展開會開到別筆
      this.collapseAll();
      await this.guard(async () => {
        const range = {date_from: this.dateFrom, date_to: this.dateTo};
        if (this.kind === "sales") {
          const filters = {...range, payment: this.payment || ""};
          this.sales = await API.listSales(filters);
          this.summary = await API.salesSummary(filters);
        } else if (this.kind === "receive") {
          this.receipts = await API.listReceipts(range);
        } else if (this.kind === "returns") {
          this.returns = await API.returnHistory(range);
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
