window.PosPages = window.PosPages || {};
window.PosPages["page-records"] = {
  template: "#tpl-records",
  inject: ["showError"],
  data() {
    const today = new Date().toISOString().slice(0, 10);
    return { dateFrom: today, dateTo: today, payment: "", payments: [],
             sales: [], summary: null, expanded: null };
  },
  computed: { isAdmin() { return window.PosAuth.isAdmin; } },
  async mounted() {
    await this.guard(async () => {
      this.payments = await API.listPayments();
      await this.reload();
    });
  },
  methods: {
    async reload() {
      await this.guard(async () => {
        const filters = {date_from: this.dateFrom, date_to: this.dateTo,
                         payment: this.payment || ""};
        this.sales = await API.listSales(filters);
        this.summary = await API.salesSummary(filters);
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
