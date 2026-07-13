window.PosPages = window.PosPages || {};
window.PosPages["page-records"] = {
  template: "#tpl-records",
  data() {
    const today = new Date().toISOString().slice(0, 10);
    return { dateFrom: today, dateTo: today, payment: "", payments: [],
             sales: [], summary: null, expanded: null };
  },
  async mounted() {
    this.payments = await API.get("/api/payments");
    await this.reload();
  },
  methods: {
    attrText(row) { return window.fmtAttr(row); },
    async reload() {
      const q = `date_from=${this.dateFrom}&date_to=${this.dateTo}` +
                (this.payment ? `&payment=${encodeURIComponent(this.payment)}` : "");
      this.sales = await API.get("/api/sales?" + q);
      this.summary = await API.get("/api/sales/summary?" + q);
    },
    exportCsv() {
      window.open(`/api/sales/export?date_from=${this.dateFrom}&date_to=${this.dateTo}`);
    },
  },
};
